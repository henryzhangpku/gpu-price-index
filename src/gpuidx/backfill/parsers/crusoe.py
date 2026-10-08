"""Crusoe Cloud: crusoecloud.com/pricing (2023), crusoe.ai/cloud/pricing (2024-).

A column strip -- ``On-Demand | Current Spot | 6-month reserved | 1-year
reserved | 3-year reserved`` -- then one row per GPU: ``NVIDIA H100 | 80GB |
SXM`` followed by one slot per column, each slot either a price split over
three cells (``$ | 3.90 | /hr``) or a placeholder (``Contact Sales``). Slots
are matched to columns in order; a placeholder keeps its column's place, so a
missing spot price cannot shift the reserved prices one column left.

Prices are per GPU-hour and no node size is stated, so rows are one GPU.
"""

from __future__ import annotations

import re

from ..htmltext import cells
from ..records import RateCardRow
from . import register
from .common import classify
from .tables import PLACEHOLDER, commitment_of

_NUM = re.compile(r"^\d+(?:\.\d+)?$")
_ONE_CELL = re.compile(r"^\$\s?(\d+(?:\.\d+)?)\s?/\s?(?:gpu-?hr|hr|hour)$", re.I)
_VRAM = re.compile(r"^(\d{2,3})\s?GB$", re.I)


def _columns_at(cs: list[str], i: int) -> list[str] | None:
    run = []
    j = i
    while j < len(cs) and len(cs[j]) <= 30 and commitment_of(cs[j]) is not None:
        run.append(cs[j])
        j += 1
    return run if len(run) >= 2 else None


@register("crusoe")
def parse(body: bytes, captured: str, source: str) -> list[RateCardRow]:
    cs = cells(body)
    out: list[RateCardRow] = []
    columns: list[str] = []
    i = 0
    while i < len(cs):
        run = _columns_at(cs, i)
        if run:
            columns = run
            i += len(run)
            continue
        cell = cs[i]
        gpu = classify(cell) if len(cell) <= 30 else None
        if gpu is None or not columns:
            i += 1
            continue
        j = i + 1
        vram, form = None, ""
        while j < len(cs) and j <= i + 3:
            if _VRAM.match(cs[j]):
                vram = int(_VRAM.match(cs[j]).group(1))
            elif cs[j].upper() in ("SXM", "PCIE", "OAM", "NVL", "HGX", "NVL72"):
                form = cs[j]
            else:
                break
            j += 1
        gpu = classify(f"{cell} {form}", vram) or gpu
        slots: list[float | None] = []
        while j < len(cs) and len(slots) < len(columns):
            if cs[j] == "$" and j + 1 < len(cs) and _NUM.match(cs[j + 1]):
                slots.append(float(cs[j + 1]))
                j += 3 if j + 2 < len(cs) and cs[j + 2].startswith("/") else 2
            elif _ONE_CELL.match(cs[j]):
                slots.append(float(_ONE_CELL.match(cs[j]).group(1)))
                j += 1
            elif PLACEHOLDER.match(cs[j]):
                slots.append(None)
                j += 1
            else:
                break
        for header, price in zip(columns, slots, strict=False):
            if price is None or price <= 0:
                continue
            out.append(
                RateCardRow(
                    source=source,
                    captured=captured,
                    sku=f"{cell} {form} [{header}]".replace("  ", " "),
                    gpu_model=gpu.model,
                    gpu_count=1,
                    price_per_instance_hour=price,
                    commitment=commitment_of(header),
                    form_factor=gpu.form_factor,
                    interconnect=gpu.interconnect,
                    vram_gb=gpu.vram_gb,
                    note=f"${price}/hr per GPU under '{header}'",
                )
            )
        i = max(j, i + 1)
    return out
