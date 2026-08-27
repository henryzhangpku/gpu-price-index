"""Rebuild and verify the series from the archive.

Two operations, and the distinction between them is the point.

``rebuild``
    Reconstruct the working database from archived snapshots and the tape.
    This is how the Action gets its history back without committing a binary
    database: the state a value depended on -- prior runs, prior fixings -- is
    restored from durable files.

``verify``
    Recompute every published value from its archived raw inputs and compare
    against what the tape says was published. This is the property that makes
    the benchmark auditable rather than merely logged: given the archive and
    the methodology version, anyone can derive the same numbers, or find out
    exactly where they cannot.

A verify failure is not necessarily a bug. It is the correct alarm when the
methodology changed without a version bump, when a snapshot was altered, or
when a value was published from inputs that were never archived. All three
are things a benchmark administrator has to be able to detect.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import METHODOLOGY_VERSION
from .archive import list_snapshots, live_tape_values, read_snapshot, read_tape
from .estimator import estimate
from .normalize import prepare_quotes
from .spec import CONTRACTS, DEFAULT_GATES, Gates
from .store import Store

#: Values are compared to the cent. Tighter than this and floating-point
#: association order across a rebuild would produce spurious failures.
TOLERANCE = 0.005


@dataclass
class Mismatch:
    index_code: str
    index_date: str
    published: float | None
    recomputed: float | None
    detail: str


@dataclass
class VerifyReport:
    checked: int = 0
    matched: int = 0
    mismatches: list[Mismatch] = field(default_factory=list)
    unverifiable: list[Mismatch] = field(default_factory=list)
    methodology_drift: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.mismatches and not self.methodology_drift


def rebuild(
    store: Store, root: Path, gates: Gates | None = None, recent: int | None = None
) -> int:
    """Replay archived snapshots into a store, restoring run history.

    Index values are not recomputed here -- they are read from the tape, which
    is the authoritative publication record. Recomputing them would renumber
    revisions and silently discard the history of what was published when.

    ``recent`` bounds how many snapshots are replayed. The quality checks only
    look back a handful of runs, so a daily job does not need to re-ingest a
    year of archive to have the history it depends on. The full tape is always
    restored regardless -- it is small, and level-shift detection needs the
    entire published series, not a window of it. Pass ``None`` to replay
    everything, which is what an audit wants.
    """
    gates = gates or DEFAULT_GATES
    snapshots = list_snapshots(root)
    if recent is not None and recent > 0:
        snapshots = snapshots[-recent:]

    for path in snapshots:
        archived = read_snapshot(path)
        per_provider: dict[str, int] = {}
        for obs in archived.observations:
            per_provider[obs.source] = per_provider.get(obs.source, 0) + 1

        run_id = store.start_run(per_provider)
        store.record_observations(run_id, archived.observations)
        quotes, _ = prepare_quotes(archived.observations)
        store.record_quotes(run_id, quotes)

    _restore_tape(store, root)
    return len(snapshots)


def _restore_tape(store: Store, root: Path) -> None:
    """Load the publication record verbatim, revision numbering intact."""
    rows = read_tape(root)
    if not rows:
        return

    with store.tx() as conn:
        for row in rows:
            conn.execute(
                "INSERT OR REPLACE INTO index_values (index_code, index_date, revision,"
                " status, value, provider_count, observation_count, dispersion,"
                " withheld_reason, methodology_version, published_at, superseded_at,"
                " revision_reason, run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
                (
                    row["index_code"],
                    row["index_date"],
                    int(row["revision"]),
                    row["status"],
                    float(row["value"]) if row["value"] else None,
                    int(row["provider_count"]),
                    int(row["observation_count"]),
                    float(row["dispersion"]) if row["dispersion"] else None,
                    row["withheld_reason"] or None,
                    row["methodology_version"],
                    row["published_at"],
                    row["superseded_at"] or None,
                    row["revision_reason"] or None,
                ),
            )


def verify(root: Path, gates: Gates | None = None) -> VerifyReport:
    """Recompute published values from archived inputs and compare to the tape."""
    gates = gates or DEFAULT_GATES
    report = VerifyReport()

    # Index snapshots by filename. A value is checked against the exact run
    # that produced it, never against a pooled day: two runs on one date
    # produce different inputs, and pooling them would compare a published
    # value against a set of observations that never existed together.
    available = {path.name: path for path in list_snapshots(root)}
    cache: dict[str, list] = {}

    for (index_code, index_date), row in sorted(live_tape_values(root).items()):
        report.checked += 1

        if row["methodology_version"] != METHODOLOGY_VERSION:
            report.methodology_drift.append(
                f"{index_code} {index_date} published under methodology "
                f"{row['methodology_version']}, current is {METHODOLOGY_VERSION}"
            )
            continue

        name = (row.get("snapshot") or "").strip()
        if not name:
            report.unverifiable.append(
                Mismatch(
                    index_code,
                    index_date,
                    _as_float(row["value"]),
                    None,
                    "tape row predates snapshot provenance and names no inputs",
                )
            )
            continue
        if name not in available:
            report.unverifiable.append(
                Mismatch(
                    index_code,
                    index_date,
                    _as_float(row["value"]),
                    None,
                    f"named snapshot {name} is missing from the archive",
                )
            )
            continue

        if name not in cache:
            cache[name] = read_snapshot(available[name]).observations
        observations = cache[name]

        quotes, _ = prepare_quotes(observations)
        relevant = [q for q in quotes if q.index_code == index_code]
        recomputed = estimate(index_code, relevant, gates)

        published_value = _as_float(row["value"])
        recomputed_value = recomputed.value if recomputed.passed else None

        if published_value is None and recomputed_value is None:
            report.matched += 1
            continue

        if published_value is None or recomputed_value is None:
            report.mismatches.append(
                Mismatch(
                    index_code,
                    index_date,
                    published_value,
                    recomputed_value,
                    "published and recomputed disagree on whether to publish at all",
                )
            )
            continue

        if abs(published_value - recomputed_value) <= TOLERANCE:
            report.matched += 1
        else:
            report.mismatches.append(
                Mismatch(
                    index_code,
                    index_date,
                    published_value,
                    recomputed_value,
                    f"differs by ${abs(published_value - recomputed_value):.4f}",
                )
            )

    return report


def _as_float(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def coverage(root: Path) -> dict[str, int]:
    """How much archived history exists, for the operator's benefit."""
    snapshots = list_snapshots(root)
    tape = read_tape(root)
    dates = {row["index_date"] for row in tape}
    return {
        "snapshots": len(snapshots),
        "tape_rows": len(tape),
        "index_dates": len(dates),
        "indices": len(CONTRACTS),
    }
