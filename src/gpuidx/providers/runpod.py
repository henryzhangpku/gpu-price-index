"""RunPod adapter.

RunPod publishes an unauthenticated GraphQL rate card exposing two distinct
markets on the same hardware:

``securePrice``
    Vetted datacentre capacity with an uptime expectation. Comparable to a
    normal on-demand rate card.
``communityPrice``
    Peer-supplied hosts with no uptime guarantee. Systematically cheaper and
    a genuinely different good, so it is tagged ``COMMUNITY`` and carries the
    corresponding adjustment rather than being averaged in naively.

Both are captured. Discarding the community tier would bias the index toward
the institutional end of the market; averaging it in unadjusted would bias it
the other way.
"""

from __future__ import annotations

import httpx

from ..models import Commitment, RawObservation, Tier
from .base import Provider

ENDPOINT = "https://api.runpod.io/graphql"
QUERY = """
query {
  gpuTypes {
    id
    displayName
    memoryInGb
    securePrice
    communityPrice
  }
}
"""


class RunPod(Provider):
    name = "runpod"
    source_url = ENDPOINT

    def collect(self, client: httpx.Client) -> list[RawObservation]:
        resp = client.post(ENDPOINT, json={"query": QUERY})
        resp.raise_for_status()
        payload = resp.json()
        captured = self.now()

        out: list[RawObservation] = []
        for item in (payload.get("data") or {}).get("gpuTypes") or []:
            gpu_id = item.get("id") or ""
            display = item.get("displayName") or gpu_id
            vram = item.get("memoryInGb")

            for price_key, commitment, suffix in (
                ("securePrice", Commitment.ON_DEMAND, "secure"),
                ("communityPrice", Commitment.COMMUNITY, "community"),
            ):
                price = item.get(price_key)
                # RunPod reports 0 for "not currently offered", not "free".
                if not price or float(price) <= 0:
                    continue
                out.append(
                    RawObservation(
                        source="runpod",
                        source_sku=f"{gpu_id}:{suffix}",
                        gpu_model=str(gpu_id),
                        gpu_count=1,
                        usd_per_hour_total=float(price),
                        commitment=commitment,
                        form_factor=self.infer_form_factor(gpu_id, display),
                        interconnect=self.infer_interconnect(gpu_id, display),
                        vram_gb=int(vram) if vram else None,
                        # RunPod's rate card is global; region is not disclosed
                        # per price, so it stays None and is screened as such.
                        region=None,
                        available=None,
                        # A published rate card without confirmed capacity.
                        tier=Tier.LIST_PRICE,
                        observed_at=captured,
                        payload={"display_name": display, "market": suffix},
                    )
                )
        return out
