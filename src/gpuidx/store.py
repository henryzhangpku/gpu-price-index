"""Bitemporal storage for observations and published index values.

Two clocks are tracked separately throughout:

*event time* (``observed_at``, ``index_date``)
    When the fact was true in the market.
*knowledge time* (``ingested_at``, ``published_at``)
    When this system first knew it.

Keeping them distinct is what makes the series auditable. "What was GIX-H100
for 25 August?" and "what did we say GIX-H100 was for 25 August, as of the
morning of 26 August?" are different questions, and only the second one is
answerable by a settlement agent resolving a dispute months later. A store
that overwrites in place can answer the first and never the second.

Revisions are therefore append-only. A corrected value supersedes its
predecessor; it never erases it.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator

from . import METHODOLOGY_VERSION
from .estimator import Estimate
from .models import IndexStatus, IndexValue, NormalizedQuote, RawObservation

DEFAULT_DB = Path(__file__).resolve().parents[2] / "data" / "gpuidx.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS collection_runs (
    run_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ingested_at         TEXT NOT NULL,
    methodology_version TEXT NOT NULL,
    provider_summary    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS observations (
    obs_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              INTEGER NOT NULL REFERENCES collection_runs(run_id),
    source              TEXT NOT NULL,
    source_sku          TEXT NOT NULL,
    gpu_model           TEXT NOT NULL,
    gpu_count           INTEGER NOT NULL,
    usd_per_hour_total  REAL NOT NULL,
    usd_per_gpu_hour    REAL NOT NULL,
    commitment          TEXT NOT NULL,
    form_factor         TEXT NOT NULL,
    interconnect        TEXT NOT NULL,
    region              TEXT,
    available           INTEGER,
    reliability         REAL,
    tier                INTEGER NOT NULL,
    observed_at         TEXT NOT NULL,
    ingested_at         TEXT NOT NULL,
    fingerprint         TEXT NOT NULL,
    payload             TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_obs_run       ON observations(run_id);
CREATE INDEX IF NOT EXISTS idx_obs_source    ON observations(source, observed_at);
CREATE INDEX IF NOT EXISTS idx_obs_finger    ON observations(fingerprint);

CREATE TABLE IF NOT EXISTS normalized_quotes (
    quote_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              INTEGER NOT NULL REFERENCES collection_runs(run_id),
    index_code          TEXT NOT NULL,
    source              TEXT NOT NULL,
    source_sku          TEXT NOT NULL,
    raw_price           REAL NOT NULL,
    normalized_price    REAL NOT NULL,
    total_adjustment    REAL NOT NULL,
    adjustments         TEXT NOT NULL,
    tier                INTEGER NOT NULL,
    observed_at         TEXT NOT NULL,
    fingerprint         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quote_run ON normalized_quotes(run_id, index_code);

-- Append-only. A correction inserts a new revision and stamps the old one
-- with superseded_at; nothing is ever updated in place except that stamp.
CREATE TABLE IF NOT EXISTS index_values (
    index_code          TEXT NOT NULL,
    index_date          TEXT NOT NULL,
    revision            INTEGER NOT NULL,
    status              TEXT NOT NULL,
    value               REAL,
    provider_count      INTEGER NOT NULL,
    observation_count   INTEGER NOT NULL,
    dispersion          REAL,
    withheld_reason     TEXT,
    methodology_version TEXT NOT NULL,
    published_at        TEXT NOT NULL,
    superseded_at       TEXT,
    revision_reason     TEXT,
    run_id              INTEGER REFERENCES collection_runs(run_id),
    PRIMARY KEY (index_code, index_date, revision)
);
CREATE INDEX IF NOT EXISTS idx_values_asof ON index_values(index_code, index_date, published_at);

-- The per-provider audit trail behind one published value.
CREATE TABLE IF NOT EXISTS contributions (
    index_code          TEXT NOT NULL,
    index_date          TEXT NOT NULL,
    revision            INTEGER NOT NULL,
    provider            TEXT NOT NULL,
    price               REAL NOT NULL,
    weight              REAL NOT NULL,
    quote_count         INTEGER NOT NULL,
    tier                INTEGER NOT NULL,
    screened_out        INTEGER NOT NULL,
    screen_reason       TEXT,
    PRIMARY KEY (index_code, index_date, revision, provider)
);

CREATE TABLE IF NOT EXISTS quality_flags (
    flag_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              INTEGER REFERENCES collection_runs(run_id),
    index_code          TEXT,
    index_date          TEXT,
    severity            TEXT NOT NULL,
    code                TEXT NOT NULL,
    detail              TEXT NOT NULL,
    raised_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_flags_run ON quality_flags(run_id);
"""


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else DEFAULT_DB
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # -- writes ------------------------------------------------------------

    def start_run(self, provider_summary: dict[str, int]) -> int:
        with self.tx() as conn:
            cur = conn.execute(
                "INSERT INTO collection_runs (ingested_at, methodology_version, provider_summary)"
                " VALUES (?, ?, ?)",
                (_utc(), METHODOLOGY_VERSION, json.dumps(provider_summary, sort_keys=True)),
            )
            return int(cur.lastrowid)

    def record_observations(self, run_id: int, observations: list[RawObservation]) -> None:
        ingested = _utc()
        rows = [
            (
                run_id,
                o.source,
                o.source_sku,
                o.gpu_model,
                o.gpu_count,
                o.usd_per_hour_total,
                o.usd_per_gpu_hour,
                o.commitment.value,
                o.form_factor.value,
                o.interconnect.value,
                o.region,
                None if o.available is None else int(o.available),
                o.reliability,
                int(o.tier),
                o.observed_at.isoformat(),
                ingested,
                o.fingerprint(),
                json.dumps(o.payload, sort_keys=True, default=str),
            )
            for o in observations
        ]
        with self.tx() as conn:
            conn.executemany(
                "INSERT INTO observations (run_id, source, source_sku, gpu_model, gpu_count,"
                " usd_per_hour_total, usd_per_gpu_hour, commitment, form_factor, interconnect,"
                " region, available, reliability, tier, observed_at, ingested_at, fingerprint,"
                " payload) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )

    def record_quotes(self, run_id: int, quotes: list[NormalizedQuote]) -> None:
        rows = [
            (
                run_id,
                q.index_code,
                q.source,
                q.source_sku,
                q.raw_usd_per_gpu_hour,
                q.normalized_usd_per_gpu_hour,
                q.total_adjustment,
                json.dumps([a.model_dump() for a in q.adjustments]),
                int(q.tier),
                q.observed_at.isoformat(),
                q.fingerprint,
            )
            for q in quotes
        ]
        with self.tx() as conn:
            conn.executemany(
                "INSERT INTO normalized_quotes (run_id, index_code, source, source_sku,"
                " raw_price, normalized_price, total_adjustment, adjustments, tier,"
                " observed_at, fingerprint) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )

    def record_flags(self, run_id: int | None, flags, index_code=None, index_date=None) -> None:
        if not flags:
            return
        raised = _utc()
        with self.tx() as conn:
            conn.executemany(
                "INSERT INTO quality_flags (run_id, index_code, index_date, severity, code,"
                " detail, raised_at) VALUES (?,?,?,?,?,?,?)",
                [
                    (
                        run_id,
                        index_code,
                        index_date.isoformat() if index_date else None,
                        f.severity,
                        f.code,
                        f.detail,
                        raised,
                    )
                    for f in flags
                ],
            )

    def next_revision(self, index_code: str, index_date: date) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(MAX(revision), -1) AS r FROM index_values"
            " WHERE index_code = ? AND index_date = ?",
            (index_code, index_date.isoformat()),
        ).fetchone()
        return int(row["r"]) + 1

    def publish(
        self,
        index_code: str,
        index_date: date,
        estimate: Estimate,
        run_id: int | None,
        revision_reason: str | None = None,
    ) -> IndexValue:
        """Append a value (or a withholding) as a new revision."""
        revision = self.next_revision(index_code, index_date)
        now = _utc()
        contributing = estimate.contributing
        published = estimate.passed

        record = IndexValue(
            index_code=index_code,
            index_date=index_date,
            revision=revision,
            status=IndexStatus.PUBLISHED if published else IndexStatus.WITHHELD,
            value=estimate.value if published else None,
            provider_count=len(contributing),
            observation_count=sum(p.quote_count for p in contributing),
            dispersion=estimate.dispersion,
            withheld_reason=None if published else estimate.failed_gate_summary,
            methodology_version=METHODOLOGY_VERSION,
            published_at=datetime.now(timezone.utc),
            revision_reason=revision_reason,
        )

        with self.tx() as conn:
            if revision > 0:
                conn.execute(
                    "UPDATE index_values SET superseded_at = ?"
                    " WHERE index_code = ? AND index_date = ? AND revision = ?",
                    (now, index_code, index_date.isoformat(), revision - 1),
                )
            conn.execute(
                "INSERT INTO index_values (index_code, index_date, revision, status, value,"
                " provider_count, observation_count, dispersion, withheld_reason,"
                " methodology_version, published_at, superseded_at, revision_reason, run_id)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL,?,?)",
                (
                    index_code,
                    index_date.isoformat(),
                    revision,
                    record.status.value,
                    record.value,
                    record.provider_count,
                    record.observation_count,
                    record.dispersion,
                    record.withheld_reason,
                    record.methodology_version,
                    now,
                    revision_reason,
                    run_id,
                ),
            )
            conn.executemany(
                "INSERT INTO contributions (index_code, index_date, revision, provider, price,"
                " weight, quote_count, tier, screened_out, screen_reason)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        index_code,
                        index_date.isoformat(),
                        revision,
                        p.provider,
                        p.price,
                        p.weight,
                        p.quote_count,
                        int(p.best_tier),
                        int(p.screened_out),
                        p.screen_reason,
                    )
                    for p in estimate.providers
                ],
            )
        return record

    # -- reads -------------------------------------------------------------

    def latest(self, index_code: str, index_date: date) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM index_values WHERE index_code = ? AND index_date = ?"
            " ORDER BY revision DESC LIMIT 1",
            (index_code, index_date.isoformat()),
        ).fetchone()

    def as_of(
        self, index_code: str, index_date: date, knowledge_time: datetime
    ) -> sqlite3.Row | None:
        """What this system said for ``index_date``, as known at a point in time.

        This is the query a settlement dispute actually needs, and the reason
        the schema is append-only.
        """
        return self.conn.execute(
            "SELECT * FROM index_values WHERE index_code = ? AND index_date = ?"
            " AND published_at <= ? ORDER BY revision DESC LIMIT 1",
            (index_code, index_date.isoformat(), knowledge_time.isoformat()),
        ).fetchone()

    def history(self, index_code: str, limit: int = 30) -> list[sqlite3.Row]:
        """Current view of the series: the live revision for each date."""
        return self.conn.execute(
            "SELECT * FROM index_values v WHERE index_code = ? AND revision ="
            " (SELECT MAX(revision) FROM index_values w WHERE w.index_code = v.index_code"
            "  AND w.index_date = v.index_date)"
            " ORDER BY index_date DESC LIMIT ?",
            (index_code, limit),
        ).fetchall()

    def revisions(self, index_code: str, index_date: date) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM index_values WHERE index_code = ? AND index_date = ?"
            " ORDER BY revision",
            (index_code, index_date.isoformat()),
        ).fetchall()

    def contributions(
        self, index_code: str, index_date: date, revision: int
    ) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM contributions WHERE index_code = ? AND index_date = ?"
            " AND revision = ? ORDER BY screened_out, weight DESC",
            (index_code, index_date.isoformat(), revision),
        ).fetchall()

    def previous_published(
        self, index_code: str, before: date
    ) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM index_values v WHERE index_code = ? AND index_date < ?"
            " AND status = 'published' AND revision ="
            " (SELECT MAX(revision) FROM index_values w WHERE w.index_code = v.index_code"
            "  AND w.index_date = v.index_date)"
            " ORDER BY index_date DESC LIMIT 1",
            (index_code, before.isoformat()),
        ).fetchone()

    def fingerprints_for(self, source: str, run_id: int) -> set[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT fingerprint FROM observations WHERE source = ? AND run_id = ?",
            (source, run_id),
        ).fetchall()
        return {r["fingerprint"] for r in rows}

    def recent_runs(self, limit: int = 10) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM collection_runs ORDER BY run_id DESC LIMIT ?", (limit,)
        ).fetchall()

    def flags_for_run(self, run_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM quality_flags WHERE run_id = ? ORDER BY flag_id", (run_id,)
        ).fetchall()
