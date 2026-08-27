"""Vast.ai marketplace adapter.

Vast.ai is the only source here that exposes genuinely executable offers:
each row is a specific machine a buyer can rent right now at the quoted
price, with a host reliability score attached. That makes it the highest-tier
input available without a commercial data agreement.

Caveats that shape how it is used:

* Much of the supply is consumer hardware in non-datacentre locations, and a
  large share is fractional (``gpu_frac`` below 1.0). Fractional and
  single-GPU offers are captured but attract the node-size adjustment.
* Host reliability varies widely. Offers below a reliability floor are
  captured for the audit trail but flagged, because an offer from a host that
  disappears mid-rental is not really executable.
"""

from __future__ import annotations

import json

import httpx

from ..models import Commitment, RawObservation, Tier
from .base import Provider

ENDPOINT = "https://console.vast.ai/api/v0/bundles/"

#: Below this host reliability the offer is treated as a rate card rather
#: than an executable one -- you cannot rely on being able to transact.
RELIABILITY_FLOOR = 0.95

QUERIES = {
    "H100 SXM": {"gpu_name": {"eq": "H100 SXM"}},
    "H100 NVL": {"gpu_name": {"eq": "H100 NVL"}},
    "H200": {"gpu_name": {"eq": "H200"}},
    "A100 SXM4": {"gpu_name": {"eq": "A100 SXM4"}},
    "A100 PCIE": {"gpu_name": {"eq": "A100 PCIE"}},
    "B200": {"gpu_name": {"eq": "B200"}},
    "MI300X": {"gpu_name": {"eq": "MI300X"}},
}


class VastAI(Provider):
    name = "vastai"
    source_url = ENDPOINT

    def collect(self, client: httpx.Client) -> list[RawObservation]:
        captured = self.now()
        out: list[RawObservation] = []

        for label, predicate in QUERIES.items():
            query = dict(predicate)
            query["rentable"] = {"eq": True}
            query["limit"] = 64
            resp = client.get(ENDPOINT, params={"q": json.dumps(query)})
            if resp.status_code != 200:
                # One unavailable slice should not void the whole collection.
                continue
            offers = resp.json().get("offers") or []

            for offer in offers:
                gpus = offer.get("num_gpus") or 0
                dph = offer.get("dph_total")
                if not gpus or not dph or float(dph) <= 0:
                    continue
                reliability = offer.get("reliability2")
                executable = (
                    bool(offer.get("rentable"))
                    and reliability is not None
                    and float(reliability) >= RELIABILITY_FLOOR
                )
                gpu_name = offer.get("gpu_name") or label

                out.append(
                    RawObservation(
                        source="vastai",
                        source_sku=f"{gpu_name}:{offer.get('id')}",
                        gpu_model=str(gpu_name),
                        gpu_count=int(gpus),
                        usd_per_hour_total=float(dph),
                        commitment=Commitment.ON_DEMAND,
                        form_factor=self.infer_form_factor(gpu_name),
                        interconnect=self.infer_interconnect(gpu_name),
                        region=offer.get("geolocation"),
                        available=bool(offer.get("rentable")),
                        reliability=float(reliability) if reliability is not None else None,
                        tier=Tier.EXECUTABLE if executable else Tier.LIST_PRICE,
                        observed_at=captured,
                        payload={
                            "gpu_frac": offer.get("gpu_frac"),
                            "datacenter": offer.get("datacenter"),
                            "verified": offer.get("verified"),
                            "dph_base": offer.get("dph_base"),
                        },
                    )
                )
        return out
