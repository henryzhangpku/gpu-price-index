"""Measuring how much of a fixing rests on judgement rather than observation."""

from __future__ import annotations

import pytest

from gpuidx.models import Commitment, FormFactor
from gpuidx.normalize import normalize
from gpuidx.sensitivity import exposure
from gpuidx.spec import Gates

GATES = Gates()


def quotes_from(observations):
    return [normalize(o) for o in observations]


def test_a_fully_conforming_market_has_no_exposure(make_obs):
    observations = [
        make_obs(source=name, price_per_gpu=3.00 + i * 0.01, sku=f"{name}-{i}")
        for name in ("a", "b", "c", "d")
        for i in range(3)
    ]
    result = exposure("GIX-H100", quotes_from(observations), GATES)

    assert result.conforming_quotes == result.total_quotes
    assert result.weight_share_adjusted == pytest.approx(0.0)
    assert result.shift == pytest.approx(0.0, abs=1e-9)
    assert result.publishable_without_adjustment


def test_exposure_counts_adjusted_inputs(make_obs):
    conforming = [
        make_obs(source=name, price_per_gpu=3.00, sku=f"{name}-{i}")
        for name in ("a", "b")
        for i in range(3)
    ]
    adjusted = [
        make_obs(
            source=name,
            price_per_gpu=2.50,
            form_factor=FormFactor.PCIE,
            sku=f"{name}-{i}",
        )
        for name in ("c", "d")
        for i in range(3)
    ]
    result = exposure("GIX-H100", quotes_from(conforming + adjusted), GATES)

    assert result.conforming_quotes == 6
    assert result.total_quotes == 12
    assert result.weight_share_adjusted == pytest.approx(0.5, abs=0.01)
    assert "form_factor" in result.by_factor
    assert result.by_factor["form_factor"] == pytest.approx(0.5)


def test_an_index_that_cannot_stand_without_adjustment_is_reported_as_such(make_obs):
    """The case that matters: the schedule is load-bearing, not decorative.

    Only two providers quote the benchmark configuration natively, so the
    conforming-only recomputation fails the provider gate and there is no
    counterfactual to compare against. That is a fact about the index worth
    stating, not an error.
    """
    conforming = [
        make_obs(source=name, price_per_gpu=3.00, sku=f"{name}-{i}")
        for name in ("a", "b")
        for i in range(3)
    ]
    adjusted = [
        make_obs(
            source=name,
            price_per_gpu=2.20,
            commitment=Commitment.COMMUNITY,
            sku=f"{name}-{i}",
        )
        for name in ("c", "d", "e", "f")
        for i in range(3)
    ]
    result = exposure("GIX-H100", quotes_from(conforming + adjusted), GATES)

    assert result.published is not None
    assert result.conforming_only is None
    assert result.shift is None
    assert not result.publishable_without_adjustment


def test_shift_is_signed_against_the_counterfactual(make_obs):
    """Adjustments mark cheap non-conforming supply up, so the published value
    should sit above one computed from conforming inputs alone."""
    conforming = [
        make_obs(source=name, price_per_gpu=3.00, sku=f"{name}-{i}")
        for name in ("a", "b", "c", "d")
        for i in range(3)
    ]
    adjusted = [
        make_obs(
            source=name,
            price_per_gpu=3.00,
            form_factor=FormFactor.PCIE,
            sku=f"{name}-{i}",
        )
        for name in ("e", "f")
        for i in range(3)
    ]
    result = exposure("GIX-H100", quotes_from(conforming + adjusted), GATES)

    assert result.conforming_only is not None
    assert result.shift is not None
    assert result.shift > 0


def test_empty_index_does_not_raise(make_obs):
    result = exposure("GIX-MI300X", [], GATES)
    assert result.published is None
    assert result.total_quotes == 0
    assert result.conforming_share == 0.0
