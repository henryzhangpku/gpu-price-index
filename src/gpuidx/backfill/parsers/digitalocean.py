"""DigitalOcean GPU Droplets: digitalocean.com/pricing/gpu-droplets.

One card per Droplet: ``NVIDIA H100x8 | $2.99 | /GPU/hour | On-Demand Price |
GPUs per Droplet | 8 | ...``, or with ``Reserved Price`` for the 12-month
contract cards. Prices are per GPU-hour; the GPU count is read from "GPUs per
Droplet", falling back to the ``x8`` suffix on the name.

When a card shows two prices before its label (``$6.74 | /GPU/hour | $3.39 |
/GPU/hour* | On-Demand Price``) the first is the struck-through list price and
the second, asterisked, is the promotional rate actually charged on demand.
The charged rate is recorded and the list price kept in the note: the page
says the promotion is the price, and the backfill reads what the page says.
"""

from __future__ import annotations

import re

from ...models import Commitment
from ..htmltext import cells
from ..records import RateCardRow
from . import register
from .common import classify, price_in

_LABEL = re.compile(r"^NVIDIA\s+(.+?)(?:\s?x\s?(\d))?$", re.I)
_PRICE = re.compile(r"^\$\s?\d+(?:\.\d+)?$")


@register("digitalocean")
def parse(body: bytes, captured: str, source: str) -> list[RateCardRow]:
    cs = cells(body)
    out: list[RateCardRow] = []
    for i, cell in enumerate(cs):
        m = _LABEL.match(cell)
        if not m or len(cell) > 30:
            continue
        gpu = classify(m.group(1))
        if gpu is None:
            continue
        prices: list[str] = []
        kind = None
        j = i + 1
        while j < len(cs) and j <= i + 8:
            c = cs[j]
            if _PRICE.match(c):
                prices.append(c)
            elif c.lower().endswith("price"):
                kind = c
                break
            elif not c.lower().startswith("/gpu"):
                break
            j += 1
        if not prices or kind is None:
            continue
        count = int(m.group(2)) if m.group(2) else 1
        for k in range(j + 1, min(j + 4, len(cs) - 1)):
            if cs[k].lower() == "gpus per droplet" and cs[k + 1].isdigit():
                count = int(cs[k + 1])
                break
        k = kind.lower()
        if "reserved" in k:
            commitment = Commitment.RESERVED
        elif "on-demand" in k or "on demand" in k:
            commitment = Commitment.ON_DEMAND
        else:
            continue
        price = price_in(prices[-1])
        if not price or price <= 0:
            continue
        note = f"{prices[-1]}/GPU/hour, '{kind}'"
        if len(prices) > 1:
            note += f"; promotional, struck-through list price {prices[0]}"
        out.append(
            RateCardRow(
                source=source,
                captured=captured,
                sku=f"{cell} [{kind}]",
                gpu_model=gpu.model,
                gpu_count=count,
                price_per_instance_hour=round(price * count, 6),
                commitment=commitment,
                form_factor=gpu.form_factor,
                interconnect=gpu.interconnect,
                vram_gb=gpu.vram_gb,
                note=note,
            )
        )
    return out
