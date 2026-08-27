"""Data-quality checks that run before anything is allowed to be published.

The checks here are chosen for one property: they catch failures that look
like valid data. A feed returning HTTP 500 is caught by the collector. A feed
returning yesterday's prices forever, or silently dropping its largest
provider, or shifting 40% overnight because a vendor changed units, all
arrive as well-formed JSON and would otherwise flow straight into a
settlement price.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from .models import NormalizedQuote, QualityFlag, RawObservation
from .spec import Gates
from .store import Store

#: A feed whose entire content is byte-identical for this many consecutive
#: runs is assumed stalled rather than genuinely unchanged.
STALE_RUN_THRESHOLD = 3

#: Providers losing more than this share of their rows against their recent
#: norm are flagged: partial truncation is more dangerous than total failure.
DROPOUT_RATIO = 0.5


def check_feed_staleness(store: Store, run_id: int) -> list[QualityFlag]:
    """Detect a feed repeating an identical fingerprint set across runs.

    Rate cards legitimately go unchanged for days, so an unchanged feed is
    only suspicious once it persists; the threshold trades a little
    sensitivity for far fewer false alarms.
    """
    runs = [r["run_id"] for r in store.recent_runs(limit=STALE_RUN_THRESHOLD + 1)]
    if len(runs) <= STALE_RUN_THRESHOLD:
        return []

    sources = [
        r["source"]
        for r in store.conn.execute(
            "SELECT DISTINCT source FROM observations WHERE run_id = ?", (run_id,)
        ).fetchall()
    ]

    flags: list[QualityFlag] = []
    for source in sources:
        signatures = {frozenset(store.fingerprints_for(source, r)) for r in runs}
        signatures.discard(frozenset())
        if len(signatures) == 1 and len(runs) > STALE_RUN_THRESHOLD:
            flags.append(
                QualityFlag(
                    severity="warn",
                    code="feed_possibly_stalled",
                    detail=(
                        f"{source} returned an identical quote set across "
                        f"{len(runs)} consecutive runs"
                    ),
                )
            )
    return flags


def check_provider_dropout(store: Store, run_id: int) -> list[QualityFlag]:
    """Flag providers whose row count collapsed against their recent norm."""
    prior_runs = [r["run_id"] for r in store.recent_runs(limit=6) if r["run_id"] != run_id]
    if len(prior_runs) < 2:
        return []

    placeholders = ",".join("?" * len(prior_runs))
    baseline = {
        r["source"]: r["avg_rows"]
        for r in store.conn.execute(
            "SELECT source, AVG(n) AS avg_rows FROM ("
            f"  SELECT source, run_id, COUNT(*) AS n FROM observations"
            f"  WHERE run_id IN ({placeholders}) GROUP BY source, run_id"
            ") GROUP BY source",
            prior_runs,
        ).fetchall()
    }
    current = {
        r["source"]: r["n"]
        for r in store.conn.execute(
            "SELECT source, COUNT(*) AS n FROM observations WHERE run_id = ? GROUP BY source",
            (run_id,),
        ).fetchall()
    }

    flags: list[QualityFlag] = []
    for source, avg_rows in baseline.items():
        now = current.get(source, 0)
        if avg_rows >= 4 and now < avg_rows * DROPOUT_RATIO:
            flags.append(
                QualityFlag(
                    severity="error" if now == 0 else "warn",
                    code="provider_dropout",
                    detail=(
                        f"{source} returned {now} rows against a recent average "
                        f"of {avg_rows:.0f}"
                    ),
                )
            )
    return flags


def check_capture_freshness(
    observations: list[RawObservation], max_age: timedelta = timedelta(hours=6)
) -> list[QualityFlag]:
    """Flag observations whose event time is far behind the run.

    Curated entries legitimately carry an older event time; this catches a
    live feed serving a cached response.
    """
    now = datetime.now(timezone.utc)
    stale_sources: dict[str, int] = {}
    for obs in observations:
        if obs.source.startswith("curated:"):
            continue
        if now - obs.observed_at > max_age:
            stale_sources[obs.source] = stale_sources.get(obs.source, 0) + 1

    return [
        QualityFlag(
            severity="warn",
            code="stale_capture",
            detail=f"{source}: {count} observations older than {max_age}",
        )
        for source, count in sorted(stale_sources.items())
    ]


def check_level_shift(
    store: Store, index_code: str, index_date: date, value: float | None, gates: Gates
) -> list[QualityFlag]:
    """Flag a day-over-day move large enough to warrant a human look.

    This does not block publication. A real market can gap, and a benchmark
    that suppresses genuine moves is worse than one that flags them. It does
    demand that somebody signs off before the number is relied upon.
    """
    if value is None:
        return []
    previous = store.previous_published(index_code, index_date)
    if previous is None or not previous["value"]:
        return []

    prior = float(previous["value"])
    move = (value - prior) / prior
    if abs(move) <= gates.review_move_threshold:
        return []

    return [
        QualityFlag(
            severity="warn",
            code="level_shift",
            detail=(
                f"{index_code} moved {move:+.1%} from ${prior:.3f} ({previous['index_date']}) "
                f"to ${value:.3f}; exceeds {gates.review_move_threshold:.0%} review threshold"
            ),
        )
    ]


def check_adjustment_load(quotes: list[NormalizedQuote]) -> list[QualityFlag]:
    """Flag an index resting mostly on heavily adjusted inputs.

    If the median input needed a large markup to become comparable, the value
    is being driven by the adjustment schedule rather than by observed
    prices. That is worth saying out loud on the tape.
    """
    if not quotes:
        return []
    heavy = [q for q in quotes if q.total_adjustment >= 1.25]
    share = len(heavy) / len(quotes)
    if share < 0.5:
        return []
    return [
        QualityFlag(
            severity="warn",
            code="adjustment_dominated",
            detail=(
                f"{share:.0%} of inputs required 25%+ cumulative adjustment; "
                "value reflects the adjustment schedule as much as observed prices"
            ),
        )
    ]
