"""DataCrunch adapter.

DataCrunch publishes an unauthenticated instance-type catalogue with both an
on-demand and a spot rate for the same hardware. Capturing both gives the
pipeline a rare directly-observed on-demand/spot pair on identical hardware,
which is the only place in this data set where the spot adjustment factor can
be sanity-checked against something real rather than asserted.
"""

from __future__ import annotations

import re

import httpx

from ..models import Commitment, RawObservation, Tier
from .base import Provider

ENDPOINT = "https://api.datacrunch.io/v1/instance-types"

#: e.g. "8x H100 SXM5 80GB" -> count 8, model "H100 SXM5 80GB"
_GPU_DESC = re.compile(r"^\s*(\d+)\s*x\s*(.+?)\s*$", re.IGNORECASE)


class DataCrunch(Provider):
    name = "datacrunch"
    source_url = ENDPOINT

    def collect(self, client: httpx.Client) -> list[RawObservation]:
        resp = client.get(ENDPOINT)
        resp.raise_for_status()
        items = resp.json()
        captured = self.now()

        out: list[RawObservation] = []
        for item in items:
            gpu = item.get("gpu") or {}
            count = gpu.get("number_of_gpus") or 0
            if not count:
                continue
            model = item.get("model") or ""
            desc = gpu.get("description") or ""
            match = _GPU_DESC.match(desc)
            if match:
                model = match.group(2)

            vram = (item.get("gpu_memory") or {}).get("size_in_gigabytes")
            p2p = item.get("p2p")

            for price_key, commitment, suffix in (
                ("price_per_hour", Commitment.ON_DEMAND, "ondemand"),
                ("spot_price", Commitment.SPOT, "spot"),
            ):
                raw_price = item.get(price_key)
                if raw_price in (None, "", "0", "0.000"):
                    continue
                try:
                    price = float(raw_price)
                except (TypeError, ValueError):
                    continue
                if price <= 0:
                    continue

                out.append(
                    RawObservation(
                        source="datacrunch",
                        source_sku=f"{item.get('instance_type')}:{suffix}",
                        gpu_model=str(model),
                        gpu_count=int(count),
                        usd_per_hour_total=price,
                        commitment=commitment,
                        form_factor=self.infer_form_factor(model, desc),
                        interconnect=self.infer_interconnect(model, desc, p2p),
                        vram_gb=int(vram) if vram else None,
                        # DataCrunch's public catalogue is Finland and Iceland
                        # led; region is not exposed per instance type, so it
                        # is left undisclosed rather than guessed.
                        region=None,
                        available=None,
                        tier=Tier.LIST_PRICE,
                        observed_at=captured,
                        payload={"instance_type": item.get("instance_type"), "p2p": p2p},
                    )
                )
        return out
