"""Hyperstack: hyperstack.cloud/gpu-pricing.

Three layouts over the period, read by one rule: a row is a GPU label
followed by its specs and a per-hour price, and the price's commitment comes
from the section heading it sits under.

* 2023-24: ``GPU | VRAM (GB) | ... | Price per hour`` under an "On-Demand
  Cloud GPU Pricing" heading, prices stated ``$ 4.30 per GPU``.
* mid-2024 to mid-2025: one table with two price columns, ``Pricing Per Hour``
  and ``Reservation Pricing``; each row reads ``$ 3.44 per Hour | Starts from |
  $2.06/hour``. The first is the on-demand rate -- it is the column set beside
  the reservation column, and the page's own FAQ calls the platform "on-demand
  prepaid ... billed for each minute" -- and the second is a reservation
  floor, recorded as reserved and as a "from" price.
* mid-2025 on: separate "On-Demand GPU Pricing", "Reservation Pricing" and
  "Spot VM Pricing" tables, each under its own heading.

Prices are per GPU-hour and no node size is stated, so rows are one GPU.
"""

from __future__ import annotations

import re

from ...models import Commitment, PriceKind
from ..htmltext import cells
from ..records import RateCardRow
from . import register
from .common import classify, price_in

_PRICE = re.compile(r"^\$\s?(\d+(?:\.\d+)?)\s*(?:per\s+(?:gpu|hour)|/\s?hour|/\s?hr)?$", re.I)
_SPLIT_UNIT = re.compile(r"^(\d+(?:\.\d+)?)\s*per\s+hour$", re.I)
_FROM = re.compile(r"^(starts|starting)\s+from$", re.I)


def _heading(cell: str) -> Commitment | None:
    c = cell.lower().strip()
    if len(c) > 40:
        return None
    if c.startswith("on-demand") or c.startswith("on demand"):
        return Commitment.ON_DEMAND
    if c.startswith("reservation") or c.startswith("reserved"):
        return Commitment.RESERVED
    if c.startswith("spot"):
        return Commitment.SPOT
    return None


@register("hyperstack")
def parse(body: bytes, captured: str, source: str) -> list[RateCardRow]:
    cs = cells(body)
    out: list[RateCardRow] = []
    section: Commitment | None = None
    two_column = False
    for i, cell in enumerate(cs):
        if cell.lower() == "reservation" and i > 1 and "per" in cs[i - 1].lower() + cs[i - 2].lower():
            # "Pricing Per | Hour | Reservation | Pricing": the two-column table.
            two_column = True
            section = Commitment.ON_DEMAND
            continue
        h = _heading(cell)
        if h is not None and classify(cell) is None:
            section = h
            two_column = False
            continue
        if len(cell) > 40 or section is None:
            continue
        gpu = classify(cell)
        if gpu is None:
            continue
        j = i + 1
        found: list[tuple[float, bool]] = []  # (price, is_from)
        is_from = False
        while j < len(cs) and j <= i + 9:
            c = cs[j]
            if classify(c) is not None and len(c) <= 40 and not c.startswith("$"):
                break
            if _FROM.match(c):
                is_from = True
            elif c == "$" and j + 1 < len(cs) and _SPLIT_UNIT.match(cs[j + 1]):
                found.append((float(_SPLIT_UNIT.match(cs[j + 1]).group(1)), is_from))
                j += 1
            elif _PRICE.match(c):
                found.append((price_in(c), is_from))
            j += 1
        if not found:
            continue
        readings: list[tuple[float, Commitment, PriceKind]] = []
        if two_column:
            for price, floor in found[:2]:
                if floor:
                    readings.append((price, Commitment.RESERVED, PriceKind.FROM_FLOOR))
                else:
                    readings.append((price, Commitment.ON_DEMAND, PriceKind.QUOTED))
        else:
            price, floor = found[0]
            readings.append((price, section, PriceKind.FROM_FLOOR if floor else PriceKind.QUOTED))
        for price, commitment, kind in readings:
            if not price or price <= 0:
                continue
            out.append(
                RateCardRow(
                    source=source,
                    captured=captured,
                    sku=f"{cell} [{commitment.value}]",
                    gpu_model=gpu.model,
                    gpu_count=1,
                    price_per_instance_hour=price,
                    commitment=commitment,
                    form_factor=gpu.form_factor,
                    interconnect=gpu.interconnect,
                    vram_gb=gpu.vram_gb,
                    price_kind=kind,
                    note=f"${price} per GPU-hour, {'two-column table' if two_column else 'section ' + commitment.value}",
                )
            )
    return out
