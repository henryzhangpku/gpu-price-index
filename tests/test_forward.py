"""Term structure for a non-storable good.

The model is deliberately thin: a constant decline rate inverted out of an
observed committed-use discount. What these tests pin down is that the
inversion is mathematically right and that its central weakness -- the
unidentified split between expectation and lock-in -- is visible in the output
rather than hidden by it.
"""

from __future__ import annotations

import math

import pytest

from gpuidx.forward import (
    TermPoint,
    annual_decline_pct,
    average_price_factor,
    consistency_check,
    implied_decline,
    implied_forward_level,
    premium_sensitivity,
)


def test_flat_market_averages_to_one():
    assert average_price_factor(0.0, 1.0) == pytest.approx(1.0)
    assert average_price_factor(0.0, 3.0) == pytest.approx(1.0)


def test_average_factor_is_continuous_through_zero():
    """The closed form has a removable singularity at d = 0."""
    just_above = average_price_factor(1e-10, 1.0)
    at_zero = average_price_factor(0.0, 1.0)
    assert just_above == pytest.approx(at_zero, abs=1e-9)


def test_average_factor_falls_as_decline_steepens():
    factors = [average_price_factor(d, 1.0) for d in (0.0, 0.1, 0.5, 1.0, 2.0)]
    assert factors == sorted(factors, reverse=True)
    assert all(0.0 < f <= 1.0 for f in factors)


def test_no_discount_implies_no_expected_decline():
    assert implied_decline(1.0, 1.0) == pytest.approx(0.0, abs=1e-6)


def test_inversion_round_trips():
    """Recovering the rate that generated a discount."""
    for rate, tenor in ((0.2, 1.0), (0.5, 1.0), (0.35, 3.0), (0.8, 2.0)):
        ratio = average_price_factor(rate, tenor)
        assert implied_decline(ratio, tenor) == pytest.approx(rate, abs=1e-6)


def test_deeper_discount_implies_steeper_decline():
    shallow = implied_decline(0.85, 1.0)
    steep = implied_decline(0.45, 1.0)
    assert steep > shallow


def test_risk_premium_absorbs_part_of_the_discount():
    """The identification problem, stated as a test.

    The same observed discount implies a smaller expected decline once part of
    it is attributed to lock-in. A point estimate without a stated premium is
    not an estimate.
    """
    rates = [implied_decline(0.60, 1.0, premium) for premium in (0.0, 0.10, 0.20, 0.30)]
    assert rates == sorted(rates, reverse=True)
    # The spread across plausible premiums is large enough to change any
    # conclusion drawn from it.
    assert rates[0] - rates[-1] > 0.3


def test_premium_covering_the_whole_discount_implies_nothing():
    # A 40% discount fully explained by a 40% lock-in premium leaves no
    # expectation component at all.
    assert implied_decline(0.60, 1.0, 0.40) == pytest.approx(0.0, abs=1e-9)


def test_rejects_impossible_inputs():
    with pytest.raises(ValueError):
        implied_decline(0.0, 1.0)
    with pytest.raises(ValueError):
        implied_decline(1.5, 1.0)
    with pytest.raises(ValueError):
        implied_decline(0.6, 1.0, risk_premium=1.0)
    with pytest.raises(ValueError):
        average_price_factor(0.2, 0.0)


def test_annual_decline_conversion():
    assert annual_decline_pct(0.0) == pytest.approx(0.0)
    assert annual_decline_pct(math.log(2)) == pytest.approx(0.5)
    assert annual_decline_pct(math.inf) == pytest.approx(1.0)


def test_projection_is_monotone_and_decaying():
    levels = [implied_forward_level(3.05, 0.5, h) for h in (0, 1, 2, 3)]
    assert levels[0] == pytest.approx(3.05)
    assert levels == sorted(levels, reverse=True)


def test_sensitivity_reports_every_assumption():
    point = TermPoint(vendor="v", tenor_years=1.0, price_ratio=0.60)
    rows = premium_sensitivity(point, [0.0, 0.10, 0.20])
    assert [r["risk_premium"] for r in rows] == [0.0, 0.10, 0.20]
    assert all(0.0 <= r["annual_decline"] <= 1.0 for r in rows)


def test_headline_cloud_discounts_imply_implausible_declines():
    """The result that matters, asserted so it cannot quietly change.

    General-purpose committed-use discounts, read naively as GPU price
    expectations, imply spot falling by roughly half or more every year. That
    is not a forecast; it is evidence that the discount is dominated by
    commercial terms rather than by expectations, and that these instruments
    are the wrong proxy for GPU term structure.
    """
    aws_1y = implied_decline(0.60, 1.0, 0.10)
    assert annual_decline_pct(aws_1y) > 0.5


def test_one_and_three_year_discounts_disagree():
    """A constant decline rate cannot fit both tenors at once.

    Lock-in cost grows with term, so a three-year commitment carries more
    non-expectation discount than a one-year. The constant-rate model has no
    way to express that, and the disagreement is the model telling you so.
    """
    points = [
        TermPoint(vendor="aws", tenor_years=1, price_ratio=0.60),
        TermPoint(vendor="aws", tenor_years=3, price_ratio=0.40),
    ]
    rows = consistency_check(points, risk_premium=0.10)
    assert len(rows) == 1
    assert not rows[0]["consistent"]
    assert rows[0]["spread"] > 0.10


def test_single_tenor_vendor_is_skipped():
    points = [TermPoint(vendor="solo", tenor_years=1, price_ratio=0.60)]
    assert consistency_check(points) == []
