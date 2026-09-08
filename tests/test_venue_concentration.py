"""The provider count overstates how independent a fixing's inputs are.

``min_providers`` counts companies. It cannot see that most of them arrived
through one aggregator, so a fixing drawing eleven of twelve providers from a
single feed reads as broad right up until that feed breaks -- at which point
coverage collapses for a reason no gate was watching for.

These tests pin the measurement and, just as importantly, pin that it stays a
*flag*. Today one venue supplies two thirds of the contributing providers on
the live indices, so any gate strict enough to be meaningful would withhold
everything, and any gate loose enough to pass would have been chosen by looking
at the sample it is meant to judge. The number gets disclosed; the refusing
waits for evidence.
"""

from __future__ import annotations

import pytest

from gpuidx.estimator import (
    VENUE_CONCENTRATION_FLAG,
    ProviderAggregate,
    estimate,
    venue_breakdown,
    venue_concentration_flags,
    venue_of,
)
from gpuidx.models import Tier
from gpuidx.spec import Gates

GATES = Gates()


def _agg(provider: str, screened: bool = False) -> ProviderAggregate:
    return ProviderAggregate(
        provider=provider,
        price=3.00,
        quote_count=3,
        best_tier=Tier.EXECUTABLE,
        screened_out=screened,
    )


@pytest.mark.parametrize(
    ("source", "venue"),
    [
        ("shadeform:lambdalabs", "shadeform"),
        ("shadeform:massedcompute", "shadeform"),
        ("curated:aws", "curated"),
        ("runpod", "runpod"),
        ("vastai", "vastai"),
    ],
)
def test_venue_is_the_route_not_the_seller(source: str, venue: str) -> None:
    """A bare source is its own venue; a prefixed one names the feed it came by."""
    assert venue_of(source) == venue


def test_curated_inputs_share_one_venue() -> None:
    """`curated` is a collection method rather than a marketplace, but it is the
    right answer here: every curated input shares one hand-maintained file, and
    therefore one failure mode."""
    aggs = [_agg("curated:aws"), _agg("curated:gcp"), _agg("curated:azure")]
    assert venue_breakdown(aggs) == {"curated": 3}


def test_screened_providers_do_not_count_toward_independence() -> None:
    """A screened provider contributes no weight, so it cannot contribute
    independence either. Counting it would let an outlier make a concentrated
    sample look diverse."""
    aggs = [_agg("shadeform:a"), _agg("shadeform:b"), _agg("runpod", screened=True)]
    assert venue_breakdown(aggs) == {"shadeform": 2}


def test_breakdown_is_ordered_by_size() -> None:
    aggs = [_agg("runpod"), _agg("shadeform:a"), _agg("shadeform:b"), _agg("vastai")]
    assert list(venue_breakdown(aggs)) == ["shadeform", "runpod", "vastai"]


def test_flag_fires_when_one_venue_dominates() -> None:
    aggs = [_agg(f"shadeform:{i}") for i in range(8)] + [_agg("runpod"), _agg("vastai")]
    flags = venue_concentration_flags(aggs)
    assert [f.code for f in flags] == ["venue_concentration"]
    assert "shadeform" in flags[0].detail
    assert flags[0].severity == "warn"


def test_flag_is_silent_at_exactly_the_threshold() -> None:
    """Half is the boundary, and the boundary is not a breach. A rule that
    fired on the threshold itself would flag every two-venue fixing."""
    aggs = [_agg("shadeform:a"), _agg("shadeform:b"), _agg("runpod"), _agg("vastai")]
    share = 2 / len(aggs)
    assert share == VENUE_CONCENTRATION_FLAG
    assert venue_concentration_flags(aggs) == []


def test_flag_is_silent_on_a_diverse_sample() -> None:
    aggs = [_agg("runpod"), _agg("vastai"), _agg("datacrunch"), _agg("shadeform:a")]
    assert venue_concentration_flags(aggs) == []


def test_no_contributors_raises_nothing() -> None:
    assert venue_concentration_flags([_agg("runpod", screened=True)]) == []


def test_concentration_never_becomes_a_gate(make_obs) -> None:
    """The whole point: it is disclosed, not enforced.

    A fixing whose providers all arrive by one venue must still publish if the
    real gates hold. Turning this into a gate on today's evidence would mean
    choosing a ceiling by looking at the sample it judges.
    """
    from gpuidx.normalize import normalize

    quotes = [
        normalize(make_obs(source=f"shadeform:v{i}", price_per_gpu=3.00 + i * 0.05, sku=f"s{i}-{j}"))
        for i in range(6)
        for j in range(3)
    ]
    est = estimate("GIX-H100", quotes, GATES)

    assert est.venue_count == 1
    assert any(f.code == "venue_concentration" for f in est.flags)
    assert "venue_concentration" not in {g.name for g in est.gates}
    assert est.passed, est.failed_gate_summary
    assert est.value is not None


def test_venue_count_is_never_above_the_provider_count(make_obs) -> None:
    quotes = [
        normalize_q
        for i, source in enumerate(("runpod", "vastai", "shadeform:a", "shadeform:b"))
        for normalize_q in _three(make_obs, source, 3.00 + i * 0.05)
    ]
    est = estimate("GIX-H100", quotes, GATES)
    assert est.venue_count == 3
    assert est.venue_count <= len(est.contributing)


def _three(make_obs, source: str, price: float):
    from gpuidx.normalize import normalize

    return [
        normalize(make_obs(source=source, price_per_gpu=price * tilt, sku=f"{source}-{i}"))
        for i, tilt in enumerate((0.98, 1.00, 1.02))
    ]
