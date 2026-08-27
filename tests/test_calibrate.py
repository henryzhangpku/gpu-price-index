"""Telling a market spread apart from a pricing policy.

The distinction matters because a policy is not evidence. A venue that prices
spot at a fixed fraction of on-demand produces a feed that is live, well
formed, and completely uninformative about what preemption risk costs.
"""

from __future__ import annotations

import pytest

from gpuidx.calibrate import (
    ADMINISTERED_CV,
    administered_pairs,
    compare_to_schedule,
    drop_administered,
    observed_ratios,
)
from gpuidx.models import Commitment

MODELS = ["H100 SXM", "H200", "A100 SXM4", "B200", "RTX 4090", "L40S", "A40"]


def paired(make_obs, source, commitment, ratios, base=4.0):
    """A venue listing on-demand and one other tier on the same hardware."""
    observations = []
    for model, ratio in zip(MODELS, ratios, strict=True):
        observations.append(
            make_obs(source=source, gpu_model=model, price_per_gpu=base, sku=f"{model}-od")
        )
        observations.append(
            make_obs(
                source=source,
                gpu_model=model,
                price_per_gpu=base / ratio,
                commitment=commitment,
                sku=f"{model}-alt",
            )
        )
    return observations


def test_constant_ratio_is_flagged_as_administered(make_obs):
    """DataCrunch's real behaviour: spot at exactly half of on-demand."""
    observations = paired(make_obs, "policyvendor", Commitment.SPOT, [2.0] * 7)

    evidence = observed_ratios(observations)
    item = next(e for e in evidence if e.commitment is Commitment.SPOT)

    assert item.pairs == 7
    assert item.median_ratio == pytest.approx(2.0)
    assert item.cv < ADMINISTERED_CV
    assert item.administered


def test_dispersed_ratio_is_treated_as_a_market_spread(make_obs):
    """RunPod's real behaviour: community pricing varies widely by model."""
    observations = paired(
        make_obs, "marketvendor", Commitment.COMMUNITY, [1.2, 1.8, 0.9, 4.8, 2.2, 1.3, 1.1]
    )

    evidence = observed_ratios(observations)
    item = next(e for e in evidence if e.commitment is Commitment.COMMUNITY)

    assert item.pairs == 7
    assert item.cv > ADMINISTERED_CV
    assert not item.administered


def test_administered_quotes_are_dropped_and_flagged(make_obs):
    observations = paired(make_obs, "policyvendor", Commitment.SPOT, [2.0] * 7)
    kept, flags = drop_administered(observations)

    assert len(observations) == 14
    assert len(kept) == 7
    assert all(o.commitment is Commitment.ON_DEMAND for o in kept)
    assert len(flags) == 1
    assert flags[0].code == "administered_pricing_excluded"
    # The exclusion has to be legible on the record, not silent.
    assert "policyvendor" in flags[0].detail
    assert "50%" in flags[0].detail


def test_market_priced_quotes_survive(make_obs):
    observations = paired(
        make_obs, "marketvendor", Commitment.COMMUNITY, [1.2, 1.8, 0.9, 4.8, 2.2, 1.3, 1.1]
    )
    kept, flags = drop_administered(observations)

    assert len(kept) == len(observations)
    assert flags == []


def test_only_the_administered_venue_is_dropped(make_obs):
    """One venue's policy must not remove another venue's genuine quotes."""
    observations = (
        paired(make_obs, "policyvendor", Commitment.SPOT, [2.0] * 7)
        + paired(make_obs, "marketvendor", Commitment.COMMUNITY, [1.2, 1.8, 0.9, 4.8, 2.2, 1.3, 1.1])
    )
    kept, flags = drop_administered(observations)

    assert {(o.source, o.commitment) for o in kept} == {
        ("policyvendor", Commitment.ON_DEMAND),
        ("marketvendor", Commitment.ON_DEMAND),
        ("marketvendor", Commitment.COMMUNITY),
    }
    assert len(flags) == 1


def test_too_few_pairs_makes_no_call(make_obs):
    """Below the minimum, a constant ratio is coincidence, not evidence."""
    observations = []
    for model in MODELS[:3]:
        observations.append(make_obs(source="tiny", gpu_model=model, price_per_gpu=4.0, sku=f"{model}-od"))
        observations.append(
            make_obs(
                source="tiny",
                gpu_model=model,
                price_per_gpu=2.0,
                commitment=Commitment.SPOT,
                sku=f"{model}-alt",
            )
        )

    evidence = observed_ratios(observations)
    assert not administered_pairs(evidence)
    kept, flags = drop_administered(observations)
    assert len(kept) == len(observations)
    assert flags == []


def test_comparison_reports_error_against_the_asserted_factor(make_obs):
    observations = paired(
        make_obs, "marketvendor", Commitment.COMMUNITY, [1.3, 1.3, 1.3, 1.4, 1.2, 1.35, 1.25]
    )
    rows = compare_to_schedule(observed_ratios(observations))
    row = next(r for r in rows if r["commitment"] == "community")

    assert row["asserted"] == pytest.approx(1.30)
    assert row["observed"] == pytest.approx(1.30)
    assert row["error"] == pytest.approx(0.0, abs=1e-9)


def test_unpaired_venue_yields_no_evidence(make_obs):
    """A venue selling only on-demand tells us nothing about the factors."""
    observations = [
        make_obs(source="ondemandonly", gpu_model=model, price_per_gpu=4.0, sku=model)
        for model in MODELS
    ]
    assert observed_ratios(observations) == []
