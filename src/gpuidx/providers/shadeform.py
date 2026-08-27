"""Shadeform aggregator adapter.

Shadeform brokers capacity across roughly twenty independent clouds and
exposes a single unauthenticated rate card covering all of them, including
node size, interconnect, NVLink presence, and per-region availability. That
makes it the widest single view of the neocloud tail available without
commercial agreements.

Two things to know about the payload:

* ``hourly_price`` is in **cents for the whole instance**, not dollars and not
  per GPU. Lambda's 8x H100 SXM shows as 3192, which is $31.92/hr, or
  $3.99 per GPU-hour -- matching Lambda's published rate.
* Each entry is attributed to its underlying cloud via ``cloud``. The
  benchmark treats those underlying clouds as the providers, not Shadeform,
  so that a single aggregator cannot dominate provider-count gates.
"""

from __future__ import annotations

import httpx

from ..models import Commitment, RawObservation, Tier
from .base import Provider

ENDPOINT = "https://api.shadeform.ai/v1/instances/types"


class Shadeform(Provider):
    name = "shadeform"
    source_url = ENDPOINT

    def collect(self, client: httpx.Client) -> list[RawObservation]:
        resp = client.get(ENDPOINT)
        resp.raise_for_status()
        payload = resp.json()
        captured = self.now()

        out: list[RawObservation] = []
        for item in payload.get("instance_types", []):
            cfg = item.get("configuration") or {}
            gpu_type = item.get("gpu_type") or cfg.get("gpu_type")
            num_gpus = item.get("num_gpus") or cfg.get("num_gpus") or 0
            cents = item.get("hourly_price")
            if not gpu_type or not num_gpus or not cents:
                continue

            availability = item.get("availability") or []
            live_regions = [a for a in availability if a.get("available")]
            # An entry with confirmed live capacity is an executable offer;
            # one without is a rate card. The distinction drives tier weight.
            tier = Tier.EXECUTABLE if live_regions else Tier.LIST_PRICE
            region_pool = live_regions or availability
            region = None
            if region_pool:
                region = region_pool[0].get("display_name") or region_pool[0].get("region")

            interconnect_raw = item.get("interconnect") or cfg.get("interconnect")
            nvlink = item.get("nvlink") or cfg.get("nvlink")

            out.append(
                RawObservation(
                    source=f"shadeform:{item.get('cloud', 'unknown')}",
                    source_sku=str(item.get("shade_instance_type") or item.get("cloud_instance_type")),
                    gpu_model=str(gpu_type),
                    gpu_count=int(num_gpus),
                    usd_per_hour_total=float(cents) / 100.0,
                    commitment=Commitment.ON_DEMAND,
                    form_factor=self.infer_form_factor(interconnect_raw, str(gpu_type)),
                    interconnect=self.infer_interconnect(
                        "nvlink" if nvlink else "", interconnect_raw
                    ),
                    vram_gb=cfg.get("vram_per_gpu_in_gb"),
                    region=region,
                    available=bool(live_regions),
                    tier=tier,
                    observed_at=captured,
                    payload={
                        "cloud": item.get("cloud"),
                        "interconnect": interconnect_raw,
                        "nvlink": nvlink,
                        "regions_live": len(live_regions),
                        "regions_total": len(availability),
                    },
                )
            )
        return out
