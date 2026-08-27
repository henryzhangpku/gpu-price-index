"""Properties the estimator must hold for *any* market, not just plausible ones.

The hand-written tests in `test_estimator.py` encode specific scenarios, and
every one of them was built to look like a real market: prices a few percent
apart, no exact ties, no absurd values. That is exactly the blind spot that
let the MAD-zero defect through. The screen only failed when providers agreed
*exactly*, and no realistic-looking fixture ever produced that.

These tests generate the inputs instead of choosing them, and deliberately
generate the shapes a human would not think to write down: exact ties across
every provider, prices spanning six orders of magnitude, single-provider
markets, and adversaries quoting arbitrary values.

The generators round prices to a coarse grid on purpose. Sampling continuous
floats would make exact ties vanishingly unlikely, and reproducing the
original bug depends on them.
"""

from __future__ import annotations

import statistics
from datetime import UTC, datetime

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from gpuidx.estimator import estimate
from gpuidx.models import Commitment, FormFactor, Interconnect, RawObservation, Tier
from gpuidx.normalize import normalize
from gpuidx.spec import Gates

GATES = Gates()
CAPTURED = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)

#: A coarse grid so that exact ties occur often. Continuous floats would make
#: the degenerate MAD case effectively unreachable.
prices = st.integers(min_value=25, max_value=1200).map(lambda cents: cents / 100.0)

#: Enough providers that the outlier screen is active (it is suppressed below
#: four) and the provider-count gate can pass.
provider_counts = st.integers(min_value=4, max_value=9)

#: Enough quotes per provider to clear the observation-count gate.
quote_counts = st.integers(min_value=2, max_value=4)


def observation(source: str, price: float, sku: str) -> RawObservation:
    return RawObservation(
        source=source,
        source_sku=sku,
        gpu_model="H100 SXM",
        gpu_count=8,
        usd_per_hour_total=price * 8,
        commitment=Commitment.ON_DEMAND,
        form_factor=FormFactor.SXM,
        interconnect=Interconnect.NVLINK,
        region="us-east-1",
        tier=Tier.LIST_PRICE,
        observed_at=CAPTURED,
    )


def market(price_list: list[float], per_provider: int = 3) -> list:
    """One provider per price, each quoting the same price `per_provider` times."""
    observations = [
        observation(f"p{index}", price, f"p{index}-{n}")
        for index, price in enumerate(price_list)
        for n in range(per_provider)
    ]
    return [normalize(o) for o in observations]


def value_of(price_list: list[float], per_provider: int = 3) -> float | None:
    return estimate("GIX-H100", market(price_list, per_provider), GATES).value


PROPERTY_SETTINGS = settings(
    max_examples=300,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


@given(st.lists(prices, min_size=4, max_size=9), quote_counts)
@PROPERTY_SETTINGS
def test_value_never_escapes_the_contributing_prices(price_list, per_provider):
    """A weighted mean of provider medians cannot leave their range.

    If it ever does, the weighting has a sign error or a provider is being
    counted that was supposed to be screened.
    """
    result = estimate("GIX-H100", market(price_list, per_provider), GATES)
    assume(result.value is not None)

    contributing = [p.price for p in result.contributing]
    assume(contributing)
    assert min(contributing) - 1e-9 <= result.value <= max(contributing) + 1e-9


def attack(honest_price: float, honest_n: int, adversary_price: float):
    """A market where every honest provider agrees exactly, plus one adversary."""
    observations = [
        observation(f"p{i}", honest_price, f"p{i}-{n}")
        for i in range(honest_n)
        for n in range(3)
    ] + [observation("adversary", adversary_price, f"adv-{n}") for n in range(3)]
    return estimate("GIX-H100", [normalize(o) for o in observations], GATES)


@given(
    st.integers(min_value=4, max_value=9),
    prices,
    st.floats(min_value=100.0, max_value=1e7),
)
@PROPERTY_SETTINGS
def test_an_absurdly_high_quote_is_always_screened(honest_n, consensus, multiple):
    """The property that would have caught the MAD-zero defect.

    Every honest provider quotes the same price, which is the case that broke
    the original screen: MAD collapses to zero, the sigma test is undefined,
    and the implementation returned without screening anything. Four providers
    at $3.00 and one at $1,000,000 published $200,002.

    Stated as "the adversary is screened" rather than as a bound on the value,
    because a bound has to be chosen and the right bound depends on how many
    honest providers there are. Screening is the thing that must actually
    happen.
    """
    result = attack(consensus, honest_n, consensus * multiple)

    screened = {p.provider for p in result.providers if p.screened_out}
    assert "adversary" in screened, (
        f"adversary at {multiple:.0f}x survived; fixing = {result.value}"
    )
    assert result.value == pytest.approx(consensus, rel=1e-9)


@given(
    st.integers(min_value=4, max_value=9),
    prices,
    st.floats(min_value=1e-6, max_value=0.01),
)
@PROPERTY_SETTINGS
def test_an_absurdly_low_quote_is_always_screened(honest_n, consensus, fraction):
    """The same property downward, and the reason the band is a ratio.

    A percentage-deviation screen caps at 100% on the downside, so it could
    never catch one cent against a three dollar consensus however the
    threshold were tuned. Only a symmetric measure works, and this test fails
    against any relative-deviation implementation.
    """
    result = attack(consensus, honest_n, max(consensus * fraction, 1e-9))

    screened = {p.provider for p in result.providers if p.screened_out}
    assert "adversary" in screened, (
        f"adversary at {fraction:.6f}x survived; fixing = {result.value}"
    )
    assert result.value == pytest.approx(consensus, rel=1e-9)


@given(st.lists(prices, min_size=4, max_size=9), st.integers(min_value=1, max_value=40))
@PROPERTY_SETTINGS
def test_flooding_a_venue_with_duplicate_quotes_changes_nothing(price_list, extra):
    """One provider gets one vote however many SKUs it lists.

    Provider-median collapse should make quote count irrelevant. If this ever
    fails, the collapse has been bypassed somewhere.
    """
    baseline = value_of(price_list, per_provider=3)
    assume(baseline is not None)

    flooded = [
        observation("p0", price_list[0], f"p0-flood-{n}") for n in range(extra)
    ]
    quotes = market(price_list, per_provider=3) + [normalize(o) for o in flooded]
    result = estimate("GIX-H100", quotes, GATES)
    assume(result.value is not None)

    assert result.value == pytest.approx(baseline, rel=1e-9)


@given(st.lists(prices, min_size=4, max_size=9), st.floats(min_value=0.01, max_value=100.0))
@PROPERTY_SETTINGS
def test_scaling_every_price_scales_the_value(price_list, factor):
    """The estimator must have no absolute price level baked into it.

    Any hard-coded dollar threshold in the screen or the gates would break
    this, and would also mean the methodology stops working as the market
    reprices.
    """
    baseline = value_of(price_list)
    assume(baseline is not None)

    scaled = value_of([p * factor for p in price_list])
    assume(scaled is not None)

    assert scaled == pytest.approx(baseline * factor, rel=1e-6)


@given(st.lists(prices, min_size=4, max_size=9), st.randoms())
@PROPERTY_SETTINGS
def test_order_of_quotes_does_not_matter(price_list, rng):
    """A fixing must not depend on the order venues happened to respond in."""
    quotes = market(price_list)
    shuffled = list(quotes)
    rng.shuffle(shuffled)

    first = estimate("GIX-H100", quotes, GATES)
    second = estimate("GIX-H100", shuffled, GATES)

    assert (first.value is None) == (second.value is None)
    if first.value is not None:
        assert first.value == pytest.approx(second.value, rel=1e-12)


@given(st.lists(prices, min_size=4, max_size=9))
@PROPERTY_SETTINGS
def test_no_contributing_provider_exceeds_the_weight_cap(price_list):
    result = estimate("GIX-H100", market(price_list), GATES)
    contributing = result.contributing
    assume(len(contributing) >= 3)

    total = sum(p.weight for p in contributing)
    assume(total > 0)
    for provider in contributing:
        assert provider.weight / total <= GATES.max_provider_weight_share + 1e-9


@given(st.lists(prices, min_size=1, max_size=9), quote_counts)
@PROPERTY_SETTINGS
def test_a_failed_gate_always_blocks_publication(price_list, per_provider):
    """`passed` and the gates must never disagree.

    Includes markets far too thin to publish, which is where an off-by-one in
    a gate would hide.
    """
    result = estimate("GIX-H100", market(price_list, per_provider), GATES)
    if any(not gate.passed for gate in result.gates):
        assert not result.passed
    if result.passed:
        assert result.value is not None
        assert len(result.contributing) >= GATES.min_providers


@given(st.lists(prices, min_size=4, max_size=9))
@PROPERTY_SETTINGS
def test_dispersion_is_never_negative(price_list):
    result = estimate("GIX-H100", market(price_list), GATES)
    if result.dispersion is not None:
        assert result.dispersion >= 0.0


@given(st.lists(prices, min_size=4, max_size=9))
@PROPERTY_SETTINGS
def test_screening_is_monotone_in_the_criterion_it_actually_uses(price_list):
    """A screened provider must be at least as extreme as every kept one.

    "Extreme" has to be measured the way the screen measures it, and the screen
    has two branches. The sigma branch ranks providers by absolute distance
    from the median; the degenerate branch ranks them by ratio, because ratio
    is the symmetric measure and absolute distance is not.

    Stating this as absolute distance for both branches is wrong, and
    Hypothesis found the case that proves it: [0.25, 0.76, 0.76, 0.76, 1.28]
    has zero MAD, so 0.25 is screened at 3.04x while 1.28 is kept at 1.68x --
    even though 1.28 sits a hair further from the median in absolute terms.
    The screen is right and the naive invariant is wrong.
    """
    result = estimate("GIX-H100", market(price_list), GATES)
    screened = [p for p in result.providers if p.screened_out]
    kept = [p for p in result.providers if not p.screened_out]
    assume(screened and kept)

    prices_seen = [p.price for p in result.providers]
    median = statistics.median(prices_seen)
    assume(median > 0)
    mad = statistics.median([abs(p - median) for p in prices_seen])

    if mad == 0:
        def extremity(price: float) -> float:
            return max(price / median, median / price)
    else:
        def extremity(price: float) -> float:
            return abs(price - median)

    worst_kept = max(extremity(p.price) for p in kept)
    for provider in screened:
        assert extremity(provider.price) >= worst_kept - 1e-9
