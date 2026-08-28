"""How much of each fixing rests on judgement rather than on observation.

METHODOLOGY section 4 concedes that the adjustment factors are calibrated
judgement, bounds them, and caps their cumulative effect. That is a defence of
the containment, not of the numbers, and it leaves the obvious question
unanswered: *how much do they actually move the fixing?*

Without an answer, "the factors are bounded" is something a reader has to take
on trust. With one, the weakest part of the methodology becomes a published
figure that anyone can argue with.

Two measures, and the second is the one that matters:

*exposure*
    The share of contributing weight resting on inputs that needed any
    adjustment at all. A high number is not automatically bad -- it means the
    market is heterogeneous, which it is.

*counterfactual shift*
    The fixing recomputed using only inputs that conformed to the benchmark
    contract as observed, with every adjusted input discarded. The gap between
    that and the published value is the part of the number that exists because
    of section 4 rather than because of the market.

The counterfactual is not a better estimate. Discarding non-conforming inputs
throws away most of the sample and biases toward whichever venues happen to
sell the benchmark configuration. It is a measurement of dependence, not a
proposal.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .estimator import estimate
from .models import NormalizedQuote
from .spec import CONTRACTS, Gates

#: Floating point noise: a quote whose factors multiply to within this of 1.0
#: was not meaningfully adjusted.
CONFORMING_TOLERANCE = 1e-9


@dataclass
class AdjustmentExposure:
    index_code: str
    published: float | None
    #: Recomputed from conforming inputs alone.
    conforming_only: float | None
    #: Relative gap between the two, or None when either is unavailable.
    shift: float | None
    total_quotes: int
    conforming_quotes: int
    conforming_providers: int
    #: Share of contributing weight carried by providers whose median rests on
    #: at least one adjusted quote.
    weight_share_adjusted: float
    #: Share of quotes touched, per adjustment name.
    by_factor: dict[str, float] = field(default_factory=dict)
    #: Whether the index would still clear its gates on conforming inputs.
    publishable_without_adjustment: bool = False

    @property
    def conforming_share(self) -> float:
        return self.conforming_quotes / self.total_quotes if self.total_quotes else 0.0


def _is_conforming(quote: NormalizedQuote) -> bool:
    return abs(quote.total_adjustment - 1.0) <= CONFORMING_TOLERANCE


def exposure(index_code: str, quotes: list[NormalizedQuote], gates: Gates) -> AdjustmentExposure:
    """Measure how much of one fixing depends on the adjustment schedule."""
    published_estimate = estimate(index_code, quotes, gates)
    conforming = [q for q in quotes if _is_conforming(q)]
    conforming_estimate = estimate(index_code, conforming, gates)

    published = published_estimate.value if published_estimate.passed else None
    counterfactual = conforming_estimate.value if conforming_estimate.passed else None
    shift = None
    if published and counterfactual:
        shift = (published - counterfactual) / counterfactual

    # Attribute weight to providers whose contribution rests on adjusted quotes.
    adjusted_sources = {q.source for q in quotes if not _is_conforming(q)}
    contributing = published_estimate.contributing
    total_weight = sum(p.weight for p in contributing)
    adjusted_weight = sum(p.weight for p in contributing if p.provider in adjusted_sources)
    weight_share = adjusted_weight / total_weight if total_weight else 0.0

    by_factor: dict[str, int] = {}
    for quote in quotes:
        for adjustment in quote.adjustments:
            by_factor[adjustment.name] = by_factor.get(adjustment.name, 0) + 1

    return AdjustmentExposure(
        index_code=index_code,
        published=published,
        conforming_only=counterfactual,
        shift=shift,
        total_quotes=len(quotes),
        conforming_quotes=len(conforming),
        conforming_providers=len(conforming_estimate.contributing),
        weight_share_adjusted=weight_share,
        by_factor={
            name: count / len(quotes) for name, count in sorted(by_factor.items())
        }
        if quotes
        else {},
        publishable_without_adjustment=conforming_estimate.passed,
    )


def exposure_all(
    quotes: list[NormalizedQuote], gates: Gates
) -> list[AdjustmentExposure]:
    by_index: dict[str, list[NormalizedQuote]] = {code: [] for code in CONTRACTS}
    for quote in quotes:
        by_index[quote.index_code].append(quote)
    return [exposure(code, group, gates) for code, group in by_index.items()]
