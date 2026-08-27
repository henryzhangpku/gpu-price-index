"""Turn a set of normalised quotes into one number, or refuse to.

The estimator is built around one adversarial question: what would a
participant who wanted to move the settlement price do, and does this
procedure stop them?

The answers built in here:

* **Flood the venue with SKUs.** Collapse to a per-provider median first, so
  a provider listing forty variants of the same box gets one vote.
* **Post one absurd offer.** Screen on median absolute deviation, which has a
  50% breakdown point, rather than on standard deviation, which does not.
* **Dominate a thin index.** Cap any single provider's share of total weight
  and refuse to publish below a provider-count floor.
* **Wait for a quiet day.** Publication gates are evaluated every day, so a
  thin day withholds rather than printing a manipulable number.

Withholding is a first-class outcome. A gap in the series is a fact about the
market; an interpolated value is a fiction about it.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from .models import GateResult, NormalizedQuote, QualityFlag, Tier
from .spec import TIER_WEIGHTS, Gates

#: Scale factor making MAD a consistent estimator of sigma for normal data.
MAD_TO_SIGMA = 1.4826

#: Quotes further than this many robust sigmas from the median are screened.
OUTLIER_SIGMAS = 3.0

#: Used only when MAD is exactly zero and the sigma test is undefined. A
#: provider more than this factor above *or below* an exact consensus is
#: screened.
#:
#: Expressed as a ratio rather than a percentage deviation on purpose: a
#: relative test is asymmetric, capped at 100% on the downside, so it can
#: never catch a quote of one cent against a consensus of three dollars. A
#: symmetric band in ratio space treats a 300x overstatement and a 300x
#: understatement alike.
#:
#: Deliberately loose. Genuinely cheap capacity exists -- a spare-capacity
#: marketplace runs at a third of a managed cloud without either being wrong
#: -- so this is a backstop against the absurd, not a second opinion on where
#: the market is.
DEGENERATE_RATIO_TOLERANCE = 3.0


@dataclass
class ProviderAggregate:
    """One provider's single contribution to the estimate."""

    provider: str
    price: float
    quote_count: int
    best_tier: Tier
    weight: float = 0.0
    screened_out: bool = False
    screen_reason: str | None = None


@dataclass
class Estimate:
    index_code: str
    value: float | None
    dispersion: float | None
    providers: list[ProviderAggregate] = field(default_factory=list)
    gates: list[GateResult] = field(default_factory=list)
    flags: list[QualityFlag] = field(default_factory=list)

    @property
    def contributing(self) -> list[ProviderAggregate]:
        return [p for p in self.providers if not p.screened_out]

    @property
    def passed(self) -> bool:
        return all(g.passed for g in self.gates) and self.value is not None

    @property
    def failed_gate_summary(self) -> str | None:
        failed = [g for g in self.gates if not g.passed]
        if not failed:
            return None
        return "; ".join(f"{g.name}: {g.detail}" for g in failed)


def _provider_of(quote: NormalizedQuote) -> str:
    """Attribute a quote to its underlying cloud, not to an aggregator.

    Shadeform resells roughly twenty clouds. Counting it as one provider
    would understate breadth; counting its rows as twenty independent
    providers when they all come from one feed would overstate independence.
    The compromise is to attribute to the underlying cloud, which is what a
    buyer actually transacts with, and to let the per-provider weight cap
    limit any single one.
    """
    return quote.source


def aggregate_by_provider(quotes: list[NormalizedQuote]) -> list[ProviderAggregate]:
    """Collapse each provider's quotes to a single median observation."""
    buckets: dict[str, list[NormalizedQuote]] = {}
    for quote in quotes:
        buckets.setdefault(_provider_of(quote), []).append(quote)

    aggregates: list[ProviderAggregate] = []
    for provider, group in sorted(buckets.items()):
        prices = [q.normalized_usd_per_gpu_hour for q in group]
        aggregates.append(
            ProviderAggregate(
                provider=provider,
                price=statistics.median(prices),
                quote_count=len(group),
                best_tier=min(q.tier for q in group),
            )
        )
    return aggregates


def screen_outliers(aggregates: list[ProviderAggregate]) -> list[QualityFlag]:
    """Flag providers implausibly far from the cross-provider median.

    Uses MAD rather than standard deviation so that the screen cannot be
    defeated by the very outlier it is meant to catch.
    """
    live = [a for a in aggregates if not a.screened_out]
    if len(live) < 4:
        # Below four points MAD is not meaningful and screening would be
        # more likely to remove signal than noise.
        return []

    prices = [a.price for a in live]
    median = statistics.median(prices)
    mad = statistics.median([abs(p - median) for p in prices])

    flags: list[QualityFlag] = []

    if mad == 0:
        # Half or more of the providers quote exactly the same price, so MAD
        # collapses and there is no scale left to measure deviation against.
        #
        # This is not a rare corner. It is the single most dangerous
        # configuration the screen faces: a tight consensus is precisely when
        # one extreme quote moves the mean furthest. An earlier version
        # returned here without screening anything, and four providers at
        # $3.00 alongside one at $1,000,000 published a value of $200,002.
        #
        # With no scale available, fall back to a relative test against the
        # consensus. Anything outside the band is, by construction, far from
        # where the market agrees it is.
        if median <= 0:
            return flags
        for agg in live:
            if agg.price <= 0:
                continue
            ratio = max(agg.price / median, median / agg.price)
            if ratio > DEGENERATE_RATIO_TOLERANCE:
                agg.screened_out = True
                agg.screen_reason = f"{ratio:.1f}x from an exact consensus"
                flags.append(
                    QualityFlag(
                        severity="warn",
                        code="outlier_screened_degenerate",
                        detail=(
                            f"{agg.provider} at ${agg.price:.2f} screened: "
                            f"{ratio:.1f}x from a consensus of ${median:.2f}, "
                            f"where dispersion is zero and MAD gives no scale"
                        ),
                    )
                )
        return flags

    sigma = mad * MAD_TO_SIGMA
    for agg in live:
        deviation = abs(agg.price - median) / sigma
        if deviation > OUTLIER_SIGMAS:
            agg.screened_out = True
            agg.screen_reason = f"{deviation:.1f} robust sigma from median"
            flags.append(
                QualityFlag(
                    severity="info",
                    code="outlier_screened",
                    detail=(
                        f"{agg.provider} at ${agg.price:.2f} screened "
                        f"({deviation:.1f} sigma from ${median:.2f})"
                    ),
                )
            )
    return flags


def assign_weights(aggregates: list[ProviderAggregate], gates: Gates) -> list[QualityFlag]:
    """Weight by waterfall tier, then cap any single provider's influence."""
    live = [a for a in aggregates if not a.screened_out]
    if not live:
        return []

    for agg in live:
        agg.weight = TIER_WEIGHTS[int(agg.best_tier)]

    flags: list[QualityFlag] = []

    # The cap is only satisfiable once there are at least 1/cap providers:
    # with two providers and a 35% cap there is no allocation that respects
    # it. Attempting one drives every weight to zero. Such an index fails the
    # provider-count gate anyway, so leave the weights alone and let the gate
    # do the refusing.
    if len(live) * gates.max_provider_weight_share < 1.0:
        return flags

    # Iterate: capping one provider raises everyone else's share, which can
    # push a second provider over the cap.
    for _ in range(len(live)):
        total = sum(a.weight for a in live)
        if total <= 0:
            break
        over = [a for a in live if a.weight / total > gates.max_provider_weight_share]
        if not over:
            break
        for agg in over:
            others = total - agg.weight
            # Solve w / (w + others) == cap for w.
            capped = gates.max_provider_weight_share * others / (
                1 - gates.max_provider_weight_share
            )
            if capped < agg.weight:
                flags.append(
                    QualityFlag(
                        severity="warn",
                        code="provider_weight_capped",
                        detail=(
                            f"{agg.provider} weight {agg.weight:.2f} -> {capped:.2f} "
                            f"to respect {gates.max_provider_weight_share:.0%} cap"
                        ),
                    )
                )
                agg.weight = capped
    return flags


def _weighted_mean(aggregates: list[ProviderAggregate]) -> float | None:
    total = sum(a.weight for a in aggregates)
    if total <= 0:
        return None
    return sum(a.price * a.weight for a in aggregates) / total


def robust_dispersion(aggregates: list[ProviderAggregate]) -> float | None:
    """Robust coefficient of variation across contributing providers."""
    prices = [a.price for a in aggregates]
    if len(prices) < 3:
        return None
    median = statistics.median(prices)
    if median <= 0:
        return None
    mad = statistics.median([abs(p - median) for p in prices])
    return (mad * MAD_TO_SIGMA) / median


def estimate(
    index_code: str,
    quotes: list[NormalizedQuote],
    gates: Gates,
) -> Estimate:
    """Produce a candidate value and evaluate every publication gate."""
    aggregates = aggregate_by_provider(quotes)
    flags = screen_outliers(aggregates)
    flags += assign_weights(aggregates, gates)

    contributing = [a for a in aggregates if not a.screened_out]
    dispersion = robust_dispersion(contributing)
    value = _weighted_mean(contributing) if contributing else None

    tier1_present = any(a.best_tier == Tier.EXECUTABLE for a in contributing)
    observation_count = sum(a.quote_count for a in contributing)

    gate_results = [
        GateResult(
            name="min_providers",
            passed=len(contributing) >= gates.min_providers,
            detail=f"{len(contributing)} of {gates.min_providers} required",
        ),
        GateResult(
            name="min_observations",
            passed=observation_count >= gates.min_observations,
            detail=f"{observation_count} of {gates.min_observations} required",
        ),
        GateResult(
            name="dispersion",
            passed=dispersion is not None and dispersion <= gates.max_dispersion,
            detail=(
                f"{dispersion:.3f} against ceiling {gates.max_dispersion}"
                if dispersion is not None
                else "not computable with fewer than 3 providers"
            ),
        ),
    ]
    if gates.require_tier1:
        gate_results.append(
            GateResult(
                name="executable_input",
                passed=tier1_present,
                detail="executable offer present" if tier1_present else "rate cards only",
            )
        )

    if not tier1_present and contributing:
        flags.append(
            QualityFlag(
                severity="warn",
                code="no_executable_input",
                detail="value rests entirely on rate cards, not transactable offers",
            )
        )

    return Estimate(
        index_code=index_code,
        value=value,
        dispersion=dispersion,
        providers=aggregates,
        gates=gate_results,
        flags=flags,
    )
