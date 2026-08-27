"""Normalisation is where a wrong answer looks most like a right one."""

from __future__ import annotations

import pytest

from gpuidx.models import Commitment, FormFactor, Interconnect
from gpuidx.normalize import Rejection, match_contract, normalize
from gpuidx.spec import COMMITMENT_FACTORS, FORM_FACTOR_FACTORS


def test_conforming_quote_passes_through_unadjusted(make_obs):
    quote = normalize(make_obs(price_per_gpu=3.00))
    assert quote.index_code == "GIX-H100"
    assert quote.adjustments == []
    assert quote.normalized_usd_per_gpu_hour == pytest.approx(3.00)


def test_alias_matching_is_exact_or_prefixed_not_substring(make_obs):
    """A substring match would silently fold one GPU into another's index."""
    assert match_contract(make_obs(gpu_model="H100 SXM")).index_code == "GIX-H100"
    assert match_contract(make_obs(gpu_model="H100 SXM 80GB TDP350")).index_code == "GIX-H100"
    assert match_contract(make_obs(gpu_model="H200")).index_code == "GIX-H200"
    # The dangerous cases: neither may resolve to the H100 contract.
    assert match_contract(make_obs(gpu_model="H1000")) is None
    assert match_contract(make_obs(gpu_model="RTX 4090")) is None


def test_h200_is_not_absorbed_into_the_h100_index(make_obs):
    """One substring apart, and two indices wide if it goes wrong."""
    assert normalize(make_obs(gpu_model="H200")).index_code == "GIX-H200"
    assert normalize(make_obs(gpu_model="H100 SXM")).index_code == "GIX-H100"


def test_pcie_is_marked_up_toward_sxm(make_obs):
    quote = normalize(make_obs(price_per_gpu=2.00, form_factor=FormFactor.PCIE))
    expected = 2.00 * FORM_FACTOR_FACTORS[FormFactor.PCIE]
    assert quote.normalized_usd_per_gpu_hour == pytest.approx(expected)
    assert [a.name for a in quote.adjustments] == ["form_factor"]


def test_adjustments_compose_multiplicatively(make_obs):
    quote = normalize(
        make_obs(
            price_per_gpu=1.00,
            form_factor=FormFactor.PCIE,
            interconnect=Interconnect.ETHERNET,
            commitment=Commitment.COMMUNITY,
            gpu_count=1,
        )
    )
    names = {a.name for a in quote.adjustments}
    assert names == {"form_factor", "interconnect", "commitment", "node_size"}

    expected = 1.00
    for adjustment in quote.adjustments:
        expected *= adjustment.factor
    assert quote.normalized_usd_per_gpu_hour == pytest.approx(expected)
    assert quote.total_adjustment == pytest.approx(expected)


def test_infiniband_is_benchmark_conforming(make_obs):
    """NVLink and InfiniBand are both acceptable fabrics; neither is adjusted."""
    quote = normalize(make_obs(interconnect=Interconnect.INFINIBAND))
    assert all(a.name != "interconnect" for a in quote.adjustments)


def test_over_adjusted_quote_is_rejected(make_obs):
    """Past the cap the number describes the schedule, not the market."""
    with pytest.raises(Rejection) as excinfo:
        normalize(
            make_obs(
                form_factor=FormFactor.PCIE,
                interconnect=Interconnect.NONE,
                commitment=Commitment.SPOT,
            )
        )
    assert excinfo.value.code == "over_adjusted"


def test_disclosed_foreign_region_is_screened_out(make_obs):
    with pytest.raises(Rejection) as excinfo:
        normalize(make_obs(region="Czechia, CZ"))
    assert excinfo.value.code == "region_mismatch"


def test_undisclosed_region_is_tolerated(make_obs):
    """Global rate cards do not attribute a region; dropping them would gut
    the rate-card tier entirely."""
    assert normalize(make_obs(region=None)).index_code == "GIX-H100"


def test_us_regions_are_recognised_across_naming_styles(make_obs):
    for region in ("us-east-1", "US, Des Moines, IA", "eastus", "us_west_2"):
        assert normalize(make_obs(region=region)).index_code == "GIX-H100"


def test_spot_carries_a_larger_markup_than_community(make_obs):
    """Preemption risk should be priced above merely unvetted hosting."""
    assert COMMITMENT_FACTORS[Commitment.SPOT] > COMMITMENT_FACTORS[Commitment.COMMUNITY]


def test_fingerprint_is_stable_across_capture_times(make_obs, now):
    from datetime import timedelta

    first = make_obs()
    second = make_obs()
    second.observed_at = now + timedelta(days=1)
    # Same rate card seen twice must fingerprint identically, or the stalled
    # feed detector can never fire.
    assert first.fingerprint() == second.fingerprint()


def test_fingerprint_changes_when_price_changes(make_obs):
    assert make_obs(price_per_gpu=3.00).fingerprint() != make_obs(price_per_gpu=3.01).fingerprint()
