"""Voltage Park: voltagepark.com/pricing.

Offer cards rather than a table: ``On-Demand, Ethernet | $1.99/hr | Pay only
for what you need. | 1-1016 HGX H100 GPUs ...`` and ``On-demand, 3200 Gbps
Infiniband | $2.49/hr | ... | 8-1016 HGX H100 GPUs``. The card heading states
the commitment and the fabric, and the GPU line states the model and the
smallest rentable count, which is recorded as the configuration. "Long-Term
Reserve" cards say "Contact for pricing" and carry no number.
"""

from __future__ import annotations

import re

from ...models import Commitment, Interconnect
from ..htmltext import cells
from ..records import RateCardRow
from . import register
from .common import classify, price_in

_HEAD = re.compile(r"^(on[- ]demand|reserved?|long[- ]term)\b[ ,]*(.*)$", re.I)
_UNITS = re.compile(r"(\d+)\s*-\s*[\d,]+\+?\s+((?:HGX\s+)?[HB]\d00\w*)", re.I)
_RATE = re.compile(r"^\$\s?\d+(?:\.\d+)?\s?/\s?(?:hr|hour)", re.I)


def _fabric(text: str) -> Interconnect:
    t = text.lower()
    if "infiniband" in t:
        return Interconnect.INFINIBAND
    if "ethernet" in t or "roce" in t:
        return Interconnect.ETHERNET
    return Interconnect.UNKNOWN


@register("voltagepark")
def parse(body: bytes, captured: str, source: str) -> list[RateCardRow]:
    cs = cells(body)
    out: list[RateCardRow] = []
    for i, cell in enumerate(cs):
        m = _HEAD.match(cell)
        if not m or len(cell) > 60 or i + 1 >= len(cs) or not _RATE.match(cs[i + 1]):
            continue
        kind = m.group(1).lower()
        commitment = Commitment.ON_DEMAND if kind.startswith("on") else Commitment.RESERVED
        units = None
        for c in cs[i + 2 : i + 6]:
            units = _UNITS.search(c)
            if units:
                break
        if not units:
            continue
        gpu = classify(units.group(2))
        if gpu is None:
            continue
        count = int(units.group(1))
        fabric = _fabric(m.group(2))
        price = price_in(cs[i + 1])
        out.append(
            RateCardRow(
                source=source,
                captured=captured,
                sku=f"{cell} ({units.group(0)})",
                gpu_model=gpu.model,
                gpu_count=count,
                price_per_instance_hour=round(price * count, 6),
                commitment=commitment,
                form_factor=gpu.form_factor,
                interconnect=fabric if fabric != Interconnect.UNKNOWN else gpu.interconnect,
                vram_gb=gpu.vram_gb,
                note=f"{cs[i + 1]} per GPU; smallest configuration {count} GPU",
            )
        )
    return out
