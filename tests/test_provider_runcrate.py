"""The Runcrate adapter: which feed entries become observations, and how.

Runs offline: the feed below is a trimmed copy of the live one from 2026-11-08, one
entry per case the adapter has to handle.
"""

from __future__ import annotations

import httpx

from gpuidx.models import Commitment, FormFactor, Interconnect, Tier
from gpuidx.normalize import normalize
from gpuidx.providers import default_providers
from gpuidx.providers.runcrate import ENDPOINT, Runcrate

FEED = {
    "gpus": [
        {"gpu_type": "B200", "name": "B200", "vram": "180GB", "interconnect": "NVLink 5",
         "min_price_hr": 7.93, "max_price_hr": 7.93, "avg_price_hr": 7.93, "available_options": 1,
         "regions": ["Ashburn, VA, United States"]},
        {"gpu_type": "H200", "name": "H200", "vram": "141GB", "interconnect": "SXM5",
         "min_price_hr": 5.13, "max_price_hr": 8.51, "avg_price_hr": 6.79, "available_options": 15,
         "regions": ["Helsinki, Finland", "Jackson, MS, United States"]},
        # Out of stock: the feed keeps the last price.
        {"gpu_type": "MI350X", "name": "MI350X", "vram": "288GB", "interconnect": "Infinity Fabric",
         "min_price_hr": 5.72, "max_price_hr": 5.72, "avg_price_hr": 5.72, "available_options": 0,
         "regions": ["Italy"]},
        # The feed does not say SXM or PCIe for these.
        {"gpu_type": "H100", "name": "H100", "vram": "80GB", "interconnect": None,
         "min_price_hr": 3.58, "max_price_hr": 7.79, "avg_price_hr": 4.57, "available_options": 15,
         "regions": ["Ashburn, VA, United States"]},
        {"gpu_type": "A100_80G", "name": "A100 80GB", "vram": "80GB", "interconnect": None,
         "min_price_hr": 1.73, "max_price_hr": 2.5, "avg_price_hr": 2.0, "available_options": 15,
         "regions": ["Ashburn, VA, United States"]},
        # A different product from the H200 contract's SXM part.
        {"gpu_type": "H200_nvl", "name": "H200 NVL", "vram": "141GB",
         "interconnect": "NVLink bridge (groups of 4)", "min_price_hr": 4.67, "max_price_hr": 5.0,
         "avg_price_hr": 4.8, "available_options": 4, "regions": ["Worcester, United Kingdom"]},
    ],
    "fetched_at": "2026-11-08T06:30:28.019Z",
}


def collect(feed=FEED):
    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=feed)))
    return Runcrate().collect(client)


def test_reads_only_in_stock_types_the_feed_names_unambiguously():
    assert {o.gpu_model for o in collect()} == {"B200", "H200"}


def test_records_the_per_gpu_on_demand_rate_as_one_gpu():
    b200 = next(o for o in collect() if o.gpu_model == "B200")
    assert b200.source == "runcrate"
    assert b200.gpu_count == 1
    assert b200.usd_per_gpu_hour == 7.93
    assert b200.currency == "USD"
    assert b200.commitment == Commitment.ON_DEMAND
    assert b200.tier == Tier.LIST_PRICE
    assert b200.vram_gb == 180


def test_form_factor_and_interconnect_are_not_read_as_pcie():
    """infer_form_factor reads "NVLink" as PCIe; the adapter sets SXM outright."""
    for obs in collect():
        assert obs.form_factor == FormFactor.SXM
        assert obs.interconnect == Interconnect.NVLINK


def test_region_is_recorded_only_when_the_feed_lists_one():
    by_gpu = {o.gpu_model: o for o in collect()}
    assert by_gpu["B200"].region == "Ashburn, VA, United States"
    assert by_gpu["H200"].region is None


def test_a_stale_price_for_a_type_with_no_stock_is_dropped():
    feed = {"gpus": [dict(FEED["gpus"][0], available_options=0)]}
    assert collect(feed) == []


def test_observations_restate_onto_the_matching_contracts():
    codes = {o.gpu_model: normalize(o).index_code for o in collect()}
    assert codes == {"B200": "GIX-B200", "H200": "GIX-H200"}


def test_runcrate_is_in_the_default_collection():
    assert "runcrate" in {p.name for p in default_providers()}
    assert Runcrate.source_url == ENDPOINT
