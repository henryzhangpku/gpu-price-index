"""The daily run, wired end to end.

    collect -> persist raw -> normalise -> screen -> estimate -> gate ->
    quality checks -> publish or withhold

Kept deliberately linear and free of branching cleverness: this is the code
path a regulator or a disputing counterparty would read, and it should be
possible to follow it without a debugger.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from . import METHODOLOGY_VERSION
from .archive import append_to_tape, stamp_superseded, write_snapshot
from .estimator import Estimate, estimate
from .models import IndexValue, NormalizedQuote, QualityFlag
from .normalize import prepare_quotes
from .providers import Provider, collect_all
from .quality import (
    check_adjustment_load,
    check_capture_freshness,
    check_feed_staleness,
    check_level_shift,
    check_provider_dropout,
)
from .spec import CONTRACTS, DEFAULT_GATES, Gates
from .store import Store


@dataclass
class RunReport:
    run_id: int
    index_date: date
    raw_count: int
    quote_count: int
    per_provider: dict[str, int]
    estimates: dict[str, Estimate] = field(default_factory=dict)
    values: dict[str, IndexValue] = field(default_factory=dict)
    flags: list[QualityFlag] = field(default_factory=list)

    @property
    def published(self) -> list[str]:
        return [c for c, v in self.values.items() if v.status.value == "published"]

    @property
    def withheld(self) -> list[str]:
        return [c for c, v in self.values.items() if v.status.value == "withheld"]


def run_daily(
    store: Store,
    index_date: date | None = None,
    providers: list[Provider] | None = None,
    gates: Gates | None = None,
    revision_reason: str | None = None,
    archive_root: Path | None = None,
) -> RunReport:
    """Execute one publication cycle and persist everything it touched.

    When ``archive_root`` is given, the run's raw observations are written to
    an immutable snapshot and its published values are appended to the tape.
    Those two artefacts, not the database, are the durable record.
    """
    gates = gates or DEFAULT_GATES
    index_date = index_date or datetime.now(timezone.utc).date()

    collection = collect_all(providers)
    run_id = store.start_run(collection.per_provider)
    store.record_observations(run_id, collection.observations)

    snapshot_path = None
    if archive_root is not None:
        snapshot_path = write_snapshot(archive_root, collection.observations)

    quotes, preparation_flags = prepare_quotes(collection.observations)
    store.record_quotes(run_id, quotes)

    flags: list[QualityFlag] = list(collection.flags)
    flags += preparation_flags
    flags += check_capture_freshness(collection.observations)
    flags += check_provider_dropout(store, run_id)
    flags += check_feed_staleness(store, run_id)
    store.record_flags(run_id, flags)

    report = RunReport(
        run_id=run_id,
        index_date=index_date,
        raw_count=len(collection.observations),
        quote_count=len(quotes),
        per_provider=collection.per_provider,
        flags=flags,
    )

    by_index: dict[str, list[NormalizedQuote]] = {code: [] for code in CONTRACTS}
    for quote in quotes:
        by_index[quote.index_code].append(quote)

    for code, index_quotes in by_index.items():
        est = estimate(code, index_quotes, gates)

        index_flags = list(est.flags)
        index_flags += check_adjustment_load(index_quotes)
        index_flags += check_level_shift(store, code, index_date, est.value, gates)
        for flag in index_flags:
            flag.index_code = code
        store.record_flags(run_id, index_flags, index_code=code, index_date=index_date)

        value = store.publish(code, index_date, est, run_id, revision_reason)

        report.estimates[code] = est
        report.values[code] = value
        report.flags.extend(index_flags)

    if archive_root is not None:
        append_to_tape(
            archive_root,
            [
                {
                    **v.model_dump(mode="json"),
                    # Filled in by stamp_superseded once a later revision exists.
                    "superseded_at": "",
                    # Provenance: the exact inputs this value was computed from.
                    "snapshot": snapshot_path.name if snapshot_path else "",
                }
                for v in report.values.values()
            ],
        )
        stamp_superseded(archive_root)

    return report


def methodology_fingerprint() -> str:
    """Identify the methodology a value was produced under.

    Stamped onto every published value so that a series can be split at a
    methodology change rather than silently spliced across one.
    """
    return METHODOLOGY_VERSION
