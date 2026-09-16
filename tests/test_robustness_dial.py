"""The estimator's position between mean and median is a parameter, not an argument.

The standard objection to a mean is that a provider at the edge of the panel
drags it in proportion to its weight. The standard objection to a median is
the mirror image: it depends only on the vote straddling the midpoint, so
everyone else can move and the index will not. Both are true, and the
interquantile mean makes the trade-off a published number rather than a side
to defend.
"""

from __future__ import annotations

import pytest

from gpuidx.estimator import (
    MAD_TO_SIGMA,
    ProviderAggregate,
    aggregate_by_provider,
    estimate,
    interquantile_mean,
    vote_spread,
)
from gpuidx.models import Tier
from gpuidx.normalize import normalize
from gpuidx.spec import EstimatorParams, Methodology


def agg(name: str, price: float, weight: float = 1.0, spread: float = 0.0) -> ProviderAggregate:
    return ProviderAggregate(
        provider=name, price=price, quote_count=1, best_tier=Tier.LIST_PRICE,
        weight=weight, spread=spread,
    )


def panel(*prices: float) -> list[ProviderAggregate]:
    return [agg(f"p{i}", p) for i, p in enumerate(prices)]


MEAN = EstimatorParams(robustness_band=1.0)
NARROW = EstimatorParams(robustness_band=0.02)
MIDDLE = EstimatorParams(robustness_band=0.5)


def test_a_full_band_is_the_weighted_mean_exactly():
    """Not approximately: the short-circuit returns the same float the old estimator did."""
    votes = [agg("a", 2.0, 1.0), agg("b", 3.0, 0.6), agg("c", 4.0, 0.25), agg("d", 5.0, 1.0)]
    expected = sum(a.price * a.weight for a in votes) / sum(a.weight for a in votes)
    assert interquantile_mean(votes, MEAN) == expected


def test_a_vanishing_band_is_the_weighted_median():
    assert interquantile_mean(panel(1.0, 2.0, 3.0, 4.0, 100.0), NARROW) == pytest.approx(3.0, abs=0.05)
    assert interquantile_mean(panel(1.0, 2.0, 3.0, 4.0, 100.0), MEAN) == pytest.approx(22.0)


def test_the_value_is_continuous_in_the_band():
    """A small turn of the dial is a small move in the value, never a jump."""
    votes = panel(2.0, 2.5, 3.0, 3.5, 4.0, 9.0)
    previous = interquantile_mean(votes, EstimatorParams(robustness_band=0.05))
    for step in range(6, 100):
        current = interquantile_mean(votes, EstimatorParams(robustness_band=step / 100))
        assert abs(current - previous) < 0.15, (step, previous, current)
        previous = current


def test_an_edge_provider_moves_a_mean_and_not_a_narrow_band():
    """The concrete difference the dial buys.

    The mean moves with the edge provider by its weight share. The narrow band
    does not move at all until that provider crosses the middle of the panel.
    """
    quiet = panel(3.0, 3.1, 2.9, 3.0, 3.05)
    edge_at_5 = [*quiet, agg("edge", 5.0)]
    edge_at_9 = [*quiet, agg("edge", 9.0)]

    mean_5 = interquantile_mean(edge_at_5, MEAN)
    mean_9 = interquantile_mean(edge_at_9, MEAN)
    assert mean_9 - mean_5 == pytest.approx(4.0 / 6)

    narrow_5 = interquantile_mean(edge_at_5, NARROW)
    narrow_9 = interquantile_mean(edge_at_9, NARROW)
    assert narrow_5 == pytest.approx(narrow_9)


def test_the_middle_is_between_the_ends():
    """On a panel skewed inside as well as at the edge, the dial moves monotonically."""
    votes = panel(1.0, 3.0, 3.1, 3.2, 8.0, 9.0, 50.0)
    lo = interquantile_mean(votes, NARROW)
    mid = interquantile_mean(votes, MIDDLE)
    hi = interquantile_mean(votes, MEAN)
    assert lo == pytest.approx(3.2, abs=0.1)
    assert hi == pytest.approx(11.04, abs=0.01)
    assert lo < mid < hi


def test_scale_invariance():
    votes = panel(1.0, 2.0, 3.0, 4.0, 10.0)
    scaled = [agg(a.provider, a.price * 37.0) for a in votes]
    for params in (NARROW, MIDDLE, MEAN):
        assert interquantile_mean(scaled, params) == pytest.approx(
            37.0 * interquantile_mean(votes, params)
        )


def test_a_frozen_price_cannot_claim_certainty():
    """The sigma floor: a single quote votes with the floor spread, not with zero."""
    params = EstimatorParams(sigma_floor=0.03, sigma_ceiling=0.5)
    assert vote_spread(agg("frozen", 3.0, spread=0.0), params) == 0.03
    assert vote_spread(agg("noisy", 3.0, spread=0.12), params) == 0.12
    assert vote_spread(agg("chaos", 3.0, spread=2.0), params) == 0.5


def test_a_provider_own_disagreement_dilutes_it(make_obs):
    """A venue whose listings for one good disagree spreads its votes; one that agrees concentrates them."""
    tight = [make_obs(source="tight", price_per_gpu=3.0 * t, sku=f"t{i}") for i, t in enumerate((0.99, 1.0, 1.01))]
    loose = [make_obs(source="loose", price_per_gpu=3.0 * t, sku=f"l{i}") for i, t in enumerate((0.7, 1.0, 1.3))]
    aggregates = aggregate_by_provider([normalize(o) for o in tight + loose])
    by_name = {a.provider: a for a in aggregates}
    assert by_name["tight"].spread == pytest.approx(0.01 * MAD_TO_SIGMA)
    assert by_name["loose"].spread == pytest.approx(0.30 * MAD_TO_SIGMA)
    assert by_name["loose"].spread > by_name["tight"].spread


def test_a_zero_band_is_refused():
    with pytest.raises(ValueError):
        interquantile_mean(panel(1.0, 2.0), EstimatorParams(robustness_band=0.0))


def test_the_band_is_published_beside_the_level(make_obs):
    quotes = [
        make_obs(source=n, price_per_gpu=p * t, sku=f"{n}{i}")
        for n, p in {"a": 3.0, "b": 3.2, "c": 2.8, "d": 3.1}.items()
        for i, t in enumerate((0.98, 1.0, 1.02))
    ]
    est = estimate("GIX-H100", [normalize(o) for o in quotes], Methodology(version="t"))
    assert est.passed
    assert est.band == pytest.approx(est.dispersion * est.value)
    assert est.band > 0


def test_the_dial_is_carried_by_the_methodology(make_obs):
    """Two registered versions differing only in the band price the same panel differently."""
    obs = [
        make_obs(source=n, price_per_gpu=p * t, sku=f"{n}{i}")
        # Inside the outlier band on purpose; a screened edge carries no weight
        # under either version and the two would agree by accident.
        for n, p in {"a": 3.0, "b": 3.1, "c": 2.9, "d": 3.0, "edge": 3.4}.items()
        for i, t in enumerate((0.98, 1.0, 1.02))
    ]
    quotes = [normalize(o) for o in obs]
    as_mean = estimate("GIX-H100", quotes, Methodology(version="mean", estimator=MEAN))
    as_narrow = estimate("GIX-H100", quotes, Methodology(version="narrow", estimator=NARROW))
    assert as_mean.value > as_narrow.value
