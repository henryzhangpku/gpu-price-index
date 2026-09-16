"""Data-quality checks that run before anything is allowed to be published.

The checks here are chosen for one property: they catch failures that look
like valid data. A feed returning HTTP 500 is caught by the collector. A feed
returning yesterday's prices forever, or silently dropping its largest
provider, or shifting 40% overnight because a vendor changed units, all
arrive as well-formed JSON and would otherwise flow straight into a
settlement price.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from .estimator import ProviderAggregate
from .models import NormalizedQuote, QualityFlag, RawObservation
from .spec import CURRENT_METHODOLOGY, Gates, Methodology
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
    now = datetime.now(UTC)
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


#: A provider's own median moving this far day over day is worth a human look.
#:
#: Measured rather than chosen. Across 403 provider day-over-day observations
#: in the archive, the median move is 0.0%, the 90th percentile is 0.0%, the
#: 95th is 5.2% and the 99th is 21.2%. Rate cards are almost perfectly sticky;
#: essentially all of the movement comes from one venue. 25% therefore sits
#: just above the observed 99th percentile.
#:
#: The population is sharply bimodal, and the threshold is a compromise across
#: it: eighteen venues have a median absolute daily move of exactly zero, while
#: Vast.ai -- a marketplace of independent hosts -- has a median of 5.6% and a
#: maximum of 82%. So this will fire on Vast.ai's genuine moves. That is
#: accepted rather than tuned away: a marketplace median moving 82% in a day is
#: worth a human look even when it is entirely honest, and a per-provider
#: threshold fitted to each venue's own history needs far more than the twelve
#: days of archive that exist today.
PROVIDER_LEVEL_SHIFT = 0.25

#: A move this large in another contributor, in the same direction, counts as
#: corroboration that a jump is a repricing rather than a glitch.
CORROBORATING_MOVE = 0.10

#: How many other contributors must have moved that far, that way, before a
#: jump is called corroborated.
CORROBORATIONS_REQUIRED = 2

#: Below this many other contributors present on both days the screen cannot
#: tell corroborated from uncorroborated and says so instead of guessing.
CORROBORATION_MIN_PEERS = 3


def check_provider_level_shift(
    store: Store,
    index_code: str,
    index_date: date,
    aggregates: list[ProviderAggregate],
    methodology: Methodology = CURRENT_METHODOLOGY,
) -> list[QualityFlag]:
    """Flag a single contributor whose own price moved sharply since the last fixing.

    The index-level ``check_level_shift`` watches the published number. It
    cannot see the attack it most needs to: one provider moving its own median
    a long way while the index moves less than the index-level threshold.

    Concretely, on the 7 September H100 panel a tier-1 provider moving from
    $4.01 to $0.90 sits 1.85 robust sigma from the median -- inside the keep
    band, so the outlier screen keeps it -- passes the dispersion gate, and
    never approaches the 35% concentration cap at an 11.8% share. It moves the
    fixing 12%, which is under the 15% index-level review threshold. Every
    existing defence lets it through, because they are all designed around the
    *shape of the panel* rather than around a contributor changing its mind.

    This does not block publication, for the same reason the index-level check
    does not: a venue is entitled to reprice. It demands that someone looks.

    With ``screens.jump_corroboration`` on, it also says *what* to look for.
    One contributor moving 25% while two others moved 10% the same way is a
    market repricing and the flag says so; the same move with the rest of the
    panel flat is a glitch or an attack until shown otherwise, and the flag
    says that instead. With too few peers present on both days to judge, the
    screen stands down and says it cannot tell, which is a different
    statement from either.
    """
    previous = store.previous_published(index_code, index_date)
    if previous is None:
        return []

    prior_rows = store.contributions(
        index_code, date.fromisoformat(previous["index_date"]), previous["revision"]
    )
    prior = {
        row["provider"]: float(row["price"])
        for row in prior_rows
        if not row["screened_out"] and row["price"]
    }
    if not prior:
        return []

    # Every contributor's own move, for the corroboration count.
    moves: dict[str, float] = {}
    for agg in aggregates:
        if agg.screened_out:
            continue
        before = prior.get(agg.provider)
        if not before or before <= 0:
            continue
        moves[agg.provider] = (agg.price - before) / before

    corroborate = methodology.screens.jump_corroboration

    flags: list[QualityFlag] = []
    for agg in aggregates:
        if agg.screened_out or agg.provider not in moves:
            continue
        move = moves[agg.provider]
        if abs(move) <= PROVIDER_LEVEL_SHIFT:
            continue
        before = prior[agg.provider]
        base = (
            f"{agg.provider} moved {move:+.1%} from ${before:.3f} "
            f"({previous['index_date']}) to ${agg.price:.3f}; exceeds the "
            f"{PROVIDER_LEVEL_SHIFT:.0%} contributor review threshold"
        )
        if not corroborate:
            flags.append(
                QualityFlag(
                    severity="warn", index_code=index_code,
                    code="provider_level_shift", detail=base,
                )
            )
            continue

        peers = {p: m for p, m in moves.items() if p != agg.provider}
        if len(peers) < CORROBORATION_MIN_PEERS:
            flags.append(
                QualityFlag(
                    severity="warn", index_code=index_code,
                    code="provider_level_shift",
                    detail=(
                        f"{base}; only {len(peers)} other contributors present on both "
                        "days, too few to say whether the panel moved with it"
                    ),
                )
            )
            continue

        same_way = [
            p for p, m in peers.items()
            if abs(m) >= CORROBORATING_MOVE and (m > 0) == (move > 0)
        ]
        if len(same_way) >= CORROBORATIONS_REQUIRED:
            flags.append(
                QualityFlag(
                    severity="info", index_code=index_code,
                    code="provider_level_shift_corroborated",
                    detail=(
                        f"{base}; corroborated by {len(same_way)} of {len(peers)} others "
                        f"moving {CORROBORATING_MOVE:.0%}+ the same way "
                        f"({', '.join(sorted(same_way))}), so this reads as a repricing"
                    ),
                )
            )
        else:
            flags.append(
                QualityFlag(
                    severity="warn", index_code=index_code,
                    code="provider_level_shift_uncorroborated",
                    detail=(
                        f"{base}; {len(same_way)} of {len(peers)} others moved "
                        f"{CORROBORATING_MOVE:.0%}+ the same way, so this is one "
                        "contributor's move and not the market's -- a glitch or an "
                        "attack until shown otherwise"
                    ),
                )
            )
    return flags
