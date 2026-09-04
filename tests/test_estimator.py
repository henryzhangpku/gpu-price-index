"""Estimator behaviour under ordinary and adversarial inputs.

The manipulation tests are the point of this file. An index that settles a
futures contract has to survive somebody who wants it somewhere else.
"""

from __future__ import annotations

import pytest

from gpuidx.estimator import aggregate_by_provider, estimate
from gpuidx.models import Tier
from gpuidx.normalize import normalize
from gpuidx.spec import Gates

GATES = Gates()


def quotes_from(observations):
    return [normalize(o) for o in observations]


def market(make_obs, prices: dict[str, float], **kwargs):
    """Build a market where each provider lists a few SKUs around its level.

    Real venues never publish exactly one row, and a one-row-per-provider
    fixture would silently sit below the observation-count gate -- testing
    the gate instead of the behaviour under test.
    """
    observations = []
    for name, price in prices.items():
        for i, tilt in enumerate((0.98, 1.00, 1.02)):
            observations.append(
                make_obs(
                    source=name,
                    price_per_gpu=price * tilt,
                    sku=f"{name}-sku{i}",
                    **kwargs,
                )
            )
    return quotes_from(observations)


def test_value_is_the_weighted_mean_of_provider_medians(make_obs):
    quotes = market(make_obs, {"a": 2.0, "b": 3.0, "c": 4.0, "d": 5.0})
    result = estimate("GIX-H100", quotes, GATES)
    assert result.passed
    assert result.value == pytest.approx(3.5)


def test_one_provider_gets_one_vote_regardless_of_sku_count(make_obs):
    """A venue listing forty variants of one box must not outvote the market."""
    honest = [
        make_obs(source=name, price_per_gpu=price)
        for name, price in {"a": 3.0, "b": 3.0, "c": 3.0, "d": 3.0}.items()
    ]
    spammer = [
        make_obs(source="flooder", price_per_gpu=1.0, sku=f"flooder-{i}") for i in range(40)
    ]

    aggregates = aggregate_by_provider(quotes_from(honest + spammer))
    flooder = next(a for a in aggregates if a.provider == "flooder")
    assert flooder.quote_count == 40
    # Forty rows collapse to one median observation before weighting.
    assert len([a for a in aggregates if not a.screened_out]) == 5

    result = estimate("GIX-H100", quotes_from(honest + spammer), GATES)
    # Without collapsing, 40 rows at $1 would drag the value to roughly $1.18.
    assert result.value > 2.0


def test_single_absurd_offer_is_screened_by_mad(make_obs):
    quotes = market(make_obs, {"a": 3.0, "b": 3.1, "c": 2.9, "d": 3.05, "manipulator": 0.05})
    result = estimate("GIX-H100", quotes, GATES)

    screened = [p for p in result.providers if p.screened_out]
    assert [p.provider for p in screened] == ["manipulator"]
    assert result.value == pytest.approx(3.0125, abs=0.02)


def test_no_single_provider_exceeds_the_weight_cap(make_obs):
    """Tier-1 status must not let one venue dominate a thin index."""
    observations = [
        make_obs(source="executable", price_per_gpu=3.0, tier=Tier.EXECUTABLE),
        make_obs(source="b", price_per_gpu=3.0),
        make_obs(source="c", price_per_gpu=3.0),
        make_obs(source="d", price_per_gpu=3.0),
    ]
    result = estimate("GIX-H100", quotes_from(observations), GATES)

    total = sum(p.weight for p in result.contributing)
    for provider in result.contributing:
        assert provider.weight / total <= GATES.max_provider_weight_share + 1e-9


def test_thin_market_is_withheld_not_guessed(make_obs):
    result = estimate("GIX-H100", market(make_obs, {"a": 3.0, "b": 3.1}), GATES)
    assert not result.passed
    assert result.value is not None  # a candidate exists...
    assert "min_providers" in result.failed_gate_summary  # ...but is not publishable


def test_incoherent_market_is_withheld_on_dispersion(make_obs):
    """If providers disagree this violently it is not one market, and a
    central estimate would misrepresent all of them."""
    quotes = market(make_obs, {"a": 0.60, "b": 1.20, "c": 4.00, "d": 8.00, "e": 12.0})
    result = estimate("GIX-H100", quotes, GATES)
    dispersion_gate = next(g for g in result.gates if g.name == "dispersion")
    assert not dispersion_gate.passed
    assert not result.passed


def test_rate_card_only_index_raises_a_flag(make_obs):
    result = estimate("GIX-H100", market(make_obs, {"a": 3.0, "b": 3.1, "c": 3.0, "d": 2.9}), GATES)
    assert result.passed
    assert any(f.code == "no_executable_input" for f in result.flags)


def test_executable_inputs_outweigh_rate_cards(make_obs):
    """Same prices, but the tier-1 venue should pull the value toward itself."""
    base = {"b": 4.0, "c": 4.0, "d": 4.0}
    with_rate_card = estimate(
        "GIX-H100",
        quotes_from(
            [make_obs(source="a", price_per_gpu=2.0)]
            + [make_obs(source=n, price_per_gpu=p) for n, p in base.items()]
        ),
        GATES,
    )
    with_executable = estimate(
        "GIX-H100",
        quotes_from(
            [make_obs(source="a", price_per_gpu=2.0, tier=Tier.EXECUTABLE)]
            + [make_obs(source=n, price_per_gpu=p) for n, p in base.items()]
        ),
        GATES,
    )
    assert with_executable.value < with_rate_card.value


def test_empty_input_withholds_without_crashing(make_obs):
    result = estimate("GIX-H100", [], GATES)
    assert result.value is None
    assert not result.passed


def test_extreme_quote_is_screened_even_when_the_market_agrees_exactly(make_obs):
    """Regression: MAD collapses to zero when providers quote identically.

    An earlier version returned from the screen without doing anything when
    MAD was zero, which is the worst possible moment to stop screening -- a
    tight consensus is exactly when one extreme quote drags the mean furthest.
    Four providers at $3.00 and one at $1,000,000 published $200,002.
    """
    honest = [
        make_obs(source=name, price_per_gpu=3.00, sku=f"{name}-{i}")
        for name in ("a", "b", "c", "d")
        for i in range(3)
    ]
    absurd = [make_obs(source="manipulator", price_per_gpu=1_000_000.0, sku=f"m-{i}") for i in range(3)]

    result = estimate("GIX-H100", quotes_from(honest + absurd), GATES)

    screened = [p for p in result.providers if p.screened_out]
    assert [p.provider for p in screened] == ["manipulator"]
    assert result.value == pytest.approx(3.00)
    assert any(f.code == "outlier_screened_degenerate" for f in result.flags)


def test_exact_consensus_alone_screens_nobody(make_obs):
    """The fallback must not start rejecting a market that simply agrees."""
    observations = [
        make_obs(source=name, price_per_gpu=3.00, sku=f"{name}-{i}")
        for name in ("a", "b", "c", "d", "e")
        for i in range(3)
    ]
    result = estimate("GIX-H100", quotes_from(observations), GATES)

    assert not any(p.screened_out for p in result.providers)
    assert result.value == pytest.approx(3.00)
    assert result.passed


def test_genuinely_cheap_capacity_survives_an_exact_consensus(make_obs):
    """Half the price of a managed cloud is market structure, not manipulation.

    A spare-capacity marketplace really does clear well below a managed
    provider. The degenerate screen is a backstop against the absurd, so it
    has to leave a real discount alone even when the rest agree exactly.
    """
    observations = [
        make_obs(source=name, price_per_gpu=4.00, sku=f"{name}-{i}")
        for name in ("a", "b", "c", "d")
        for i in range(3)
    ] + [make_obs(source="marketplace", price_per_gpu=2.00, sku=f"m-{i}") for i in range(3)]

    result = estimate("GIX-H100", quotes_from(observations), GATES)
    assert not any(p.screened_out for p in result.providers)


def test_degenerate_screen_is_symmetric(make_obs):
    """A near-zero quote must be caught as readily as an enormous one.

    A percentage-deviation test caps at 100% on the downside and could never
    screen one cent against a three dollar consensus. The band is a ratio for
    exactly this reason.
    """
    honest = [
        make_obs(source=name, price_per_gpu=3.00, sku=f"{name}-{i}")
        for name in ("a", "b", "c", "d")
        for i in range(3)
    ]
    floor_it = [make_obs(source="lowballer", price_per_gpu=0.01, sku=f"l-{i}") for i in range(3)]

    result = estimate("GIX-H100", quotes_from(honest + floor_it), GATES)
    screened = [p for p in result.providers if p.screened_out]
    assert [p.provider for p in screened] == ["lowballer"]
    assert result.value == pytest.approx(3.00)


def test_degenerate_screen_handles_a_zero_median(make_obs):
    """A zero median would divide by zero in the relative test."""
    # Prices must be positive to normalise at all, so drive the median as low
    # as the model permits and confirm nothing raises.
    observations = [
        make_obs(source=name, price_per_gpu=1e-9, sku=f"{name}-{i}")
        for name in ("a", "b", "c", "d", "e")
        for i in range(3)
    ]
    result = estimate("GIX-H100", quotes_from(observations), GATES)
    assert result.value is not None


def test_the_degenerate_band_is_scale_invariant_at_its_boundary(make_obs):
    """A provider exactly at the tolerance is kept, whatever the price level.

    Found by the scale-invariance property, not by hand. A market of
    [0.9, 2.7, 2.7, 2.7] puts the low provider at a ratio of exactly 3.0, and
    the methodology says "more than 3x", so it contributes. The same market
    scaled by 33 computes 3.0000000000000004 in binary floating point, and
    before the epsilon was added the screen dropped it -- moving the fixing by
    20% with nothing about the market changed.

    A benchmark whose screen depends on the price level has an absolute scale
    baked into it, and would stop behaving as the market repriced.
    """
    def fixing(factor: float) -> float:
        observations = [
            make_obs(source=name, price_per_gpu=price * factor, sku=f"{name}-{i}")
            for name, price in (("a", 0.9), ("b", 2.7), ("c", 2.7), ("d", 2.7))
            for i in range(3)
        ]
        result = estimate("GIX-H100", quotes_from(observations), GATES)
        assert result.value is not None
        return result.value

    base = fixing(1.0)
    assert base == pytest.approx(2.25), "the 3.0x provider must contribute"
    assert fixing(33.0) == pytest.approx(base * 33.0, rel=1e-6)


def test_a_provider_past_the_degenerate_band_is_still_screened(make_obs):
    """The epsilon must not blunt the screen it protects."""
    observations = [
        make_obs(source=name, price_per_gpu=price, sku=f"{name}-{i}")
        for name, price in (("a", 0.89), ("b", 2.7), ("c", 2.7), ("d", 2.7))
        for i in range(3)
    ]
    result = estimate("GIX-H100", quotes_from(observations), GATES)
    assert [p.provider for p in result.providers if p.screened_out] == ["a"]
