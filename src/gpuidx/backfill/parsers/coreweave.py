"""CoreWeave: coreweave.com/gpu-cloud-pricing (2023-24), coreweave.com/pricing (2025-).

Two cards, and only one of them prices the good:

* **A-la-carte (2023-24, and a legacy copy on later pages).** ``GPU Model |
  VRAM (GB) | Max vCPUs per GPU ($0.01/hr) | Max RAM (GB) per GPU ($0.005/hr)
  | GPU Component Cost Per Hour``. The dollar figure is the GPU alone; the host
  is billed on top per vCPU and per GB. Rows are read with
  ``basis="gpu_component"`` and discarded at restatement, because completing
  them would mean choosing a host configuration the page never quoted.
* **Instances (2025-).** Under "On-demand GPU instances": ``NVIDIA HGX H100 |
  8 | 80 | 128 | 2,048 | 61.44 | $49.24`` -- GPU count, VRAM, vCPUs, RAM,
  local storage, then the instance price per hour. These are read as quoted
  on-demand instance prices.
"""

from __future__ import annotations

import re

from ...models import Commitment
from ..htmltext import cells
from ..records import RateCardRow
from . import register
from .common import classify, price_in

_INT = re.compile(r"^\d{1,4}(?:,\d{3})?$")
_DOLLAR = re.compile(r"^\$\s?\d{1,3}(?:,\d{3})*(?:\.\d+)?$")


def _instances(cs: list[str], captured: str, source: str) -> list[RateCardRow]:
    out: list[RateCardRow] = []
    in_od = False
    for i, cell in enumerate(cs):
        low = cell.lower()
        if low.startswith("on-demand gpu instances"):
            in_od = True
            continue
        if not in_od or len(cell) > 30:
            continue
        gpu = classify(cell)
        if gpu is None:
            continue
        j = i + 1
        if j < len(cs) and cs[j] == cell:
            j += 1  # the label is repeated for the mobile layout
        if j + 2 >= len(cs) or not cs[j].isdigit() or not cs[j + 1].isdigit():
            continue
        count, vram = int(cs[j]), int(cs[j + 1])
        if count not in (1, 2, 4, 8) or vram not in (80, 94, 141, 180, 192):
            continue
        price = None
        for c in cs[j + 2 : j + 8]:
            if _DOLLAR.match(c):
                price = price_in(c)
                break
        if not price:
            continue
        g = classify(cell, vram) or gpu
        out.append(
            RateCardRow(
                source=source,
                captured=captured,
                sku=f"{cell} x{count}",
                gpu_model=g.model,
                gpu_count=count,
                price_per_instance_hour=price,
                commitment=Commitment.ON_DEMAND,
                form_factor=g.form_factor,
                interconnect=g.interconnect,
                vram_gb=g.vram_gb,
                note=f"${price}/hour per instance, on-demand GPU instances table",
            )
        )
    return out


def _a_la_carte(cs: list[str], captured: str, source: str) -> list[RateCardRow]:
    if not any("gpu component" in c.lower() for c in cs if len(c) < 60):
        return []
    out: list[RateCardRow] = []
    for i, cell in enumerate(cs):
        if len(cell) > 30 or i + 4 >= len(cs):
            continue
        gpu = classify(cell)
        if gpu is None:
            continue
        row = cs[i + 1 : i + 5]
        if not all(_INT.match(c) for c in row[:3]) or not _DOLLAR.match(row[3]):
            continue
        g = classify(cell, int(row[0])) or gpu
        out.append(
            RateCardRow(
                source=source,
                captured=captured,
                sku=f"{cell} (GPU component)",
                gpu_model=g.model,
                gpu_count=1,
                price_per_instance_hour=price_in(row[3]),
                commitment=Commitment.ON_DEMAND,
                form_factor=g.form_factor,
                interconnect=g.interconnect,
                vram_gb=g.vram_gb,
                basis="gpu_component",
                note=f"{row[3]}/hour GPU component; vCPU and RAM billed separately",
            )
        )
    return out


@register("coreweave")
def parse(body: bytes, captured: str, source: str) -> list[RateCardRow]:
    cs = cells(body)
    return _instances(cs, captured, source) + _a_la_carte(cs, captured, source)
