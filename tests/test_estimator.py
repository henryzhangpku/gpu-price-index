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
