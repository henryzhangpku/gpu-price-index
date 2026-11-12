"""Runcrate adapter.

Runcrate publishes an unauthenticated JSON feed of its live on-demand prices,
one entry per GPU type. ``min_price_hr`` is the on-demand price per GPU-hour
and ``available_options`` is how many offers are live behind it.

Three things to know about the payload:

* The feed does not say SXM or PCIe, so only types whose product is not in
  doubt are read: B200 and H200 (the PCIe-bridge H200 is a separate feed type,
  ``H200_nvl``, and is not read). H100 and A100 are skipped for the same reason
  the SXM and PCIe parts are different goods here.
* A type with no available options keeps a stale price in the feed, so it is
  dropped instead of being reported as a rate.
* The price is per GPU, not per node, so each entry is recorded as one GPU.
  Region is recorded only when the feed lists a single one: a type offered in
  several regions has one minimum, and the feed does not say which region it
  belongs to.
"""

from __future__ import annotations

import re

import httpx

from ..models import Commitment, FormFactor, RawObservation, Tier
from .base import Provider

ENDPOINT = "https://www.runcrate.ai/api/gpu-pricing"

#: Feed ``gpu_type`` values read by this adapter. Both ship as SXM parts.
GPU_TYPES: frozenset[str] = frozenset({"B200", "H200"})


class Runcrate(Provider):
    name = "runcrate"
    source_url = ENDPOINT

    def collect(self, client: httpx.Client) -> list[RawObservation]:
        resp = client.get(ENDPOINT)
        resp.raise_for_status()
        payload = resp.json()
        captured = self.now()

        out: list[RawObservation] = []
        for item in payload.get("gpus") or []:
            gpu_type = item.get("gpu_type")
            if gpu_type not in GPU_TYPES:
                continue
            # Out of stock: the price is the last one seen, not a current rate.
            if (item.get("available_options") or 0) < 1:
                continue
            price = item.get("min_price_hr")
            if not price or float(price) <= 0:
                continue

            vram = re.match(r"\d+", str(item.get("vram") or ""))
            regions = item.get("regions") or []
            interconnect_raw = item.get("interconnect")

            out.append(
                RawObservation(
                    source="runcrate",
                    source_sku=f"{gpu_type}:ondemand",
                    gpu_model=str(gpu_type),
                    gpu_count=1,
                    usd_per_hour_total=float(price),
                    currency="USD",
                    commitment=Commitment.ON_DEMAND,
                    # Set outright: infer_form_factor reads "NVLink" as PCIe.
                    form_factor=FormFactor.SXM,
                    interconnect=self.infer_interconnect(interconnect_raw),
                    vram_gb=int(vram.group()) if vram else None,
                    region=regions[0] if len(regions) == 1 else None,
                    available=True,
                    # A published on-demand rate with a stock count, not a
                    # confirmed order book: rate-card weight.
                    tier=Tier.LIST_PRICE,
                    observed_at=captured,
                    payload={
                        "interconnect": interconnect_raw,
                        "available_options": item.get("available_options"),
                        "regions": regions,
                    },
                )
            )
        return out
