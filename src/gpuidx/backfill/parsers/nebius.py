"""Nebius: nebius.com/prices.

Four layouts between November 2024 and September 2026, all read by the same
rule: find the GPU table's header strip, take its price columns in order, and
match each row's price slots to them.

* Nov 2024 - Jan 2025: ``GPU | vRAM, GB | RAM, GB | vCPUS | On demand |
  Reserve``, rows ``1 x H100 SXM | 80 | 200 | 16 | $2.95 | $2.00`` and
  ``8 x H100 SXM | ... | $23.60 | $16.00`` (instance prices).
* Mar - Jun 2025: an "On-demand GPU pricing" table, ``VRAM, GB | vCPUs | RAM,
  GB | Price per hour``, one GPU per row.
* Jul 2025 - Mar 2026: "NVIDIA GPU Instances", ``Item | vCPUs | RAM, GB |
  Price per GPU-hour``. **This header names no commitment.** It is read as
  on-demand because it is the page's only self-serve per-hour table, the
  reservation offers sit in separate cards that say "commitment of hundreds
  of units", and the same table is headed ``On-demand, GPU-hour`` from April
  2026 with the same figures ($2.95 H100, $3.50 H200, $5.50 B200). That is a
  reading of a label, disclosed in docs/BACKFILL.md, not an observation.
* Apr 2026 on: ``... | Preemptible, GPU-hour | On-demand, GPU-hour``; the
  preemptible column is recorded as spot.

The "Reserve your best price" cards above the table ("$2.00 / hour ...
commitment of hundreds of units for at least 3 months") are not read.
"""

from __future__ import annotations

import re

from ...models import Commitment
from ..htmltext import cells
from ..records import RateCardRow
from . import register
from .common import classify, price_in
from .tables import PLACEHOLDER

_COUNT = re.compile(r"^(\d)\s*[\u00d7x]\s*(.+)$")
_SPEC = re.compile(r"^\d{1,4}(?:\s*-\s*\d{1,4})?$")
_DOLLAR = re.compile(r"^\$\s?\d+(?:\.\d+)?$")
_DASHES = re.compile(r"^[-\u2013\u2014]{1,3}$")


def _column(header: str) -> Commitment | None:
    h = header.lower()
    if "preemptible" in h or "spot" in h:
        return Commitment.SPOT
    if "reserve" in h:
        return Commitment.RESERVED
    if "on demand" in h or "on-demand" in h:
        return Commitment.ON_DEMAND
    if h in ("price per gpu-hour", "price per hour"):
        return Commitment.ON_DEMAND  # see the module docstring
    return None


_HEADER_START = {"item", "gpu"}
_SPEC_HEADERS = {"vram, gb", "vcpus", "ram, gb"}


@register("nebius")
def parse(body: bytes, captured: str, source: str) -> list[RateCardRow]:
    cs = cells(body)
    out: list[RateCardRow] = []
    columns: list[tuple[str, Commitment | None]] = []
    i = 0
    while i < len(cs):
        low = cs[i].lower()
        # A header strip: spec headers then price headers, ending at a row.
        if low in _HEADER_START or low in _SPEC_HEADERS:
            j = i
            heads: list[str] = []
            while (
                j < len(cs)
                and len(cs[j]) <= 30
                and not cs[j].startswith("$")
                and not cs[j].upper().startswith("NVIDIA")
                and not _COUNT.match(cs[j])
                and classify(cs[j]) is None
            ):
                heads.append(cs[j])
                j += 1
            priced = [(h, _column(h)) for h in heads if h.lower() not in _HEADER_START | _SPEC_HEADERS]
            if any(c is not None for _, c in priced) and j < len(cs):
                columns = priced
                i = j
                continue
        if not columns or len(cs[i]) > 40:
            i += 1
            continue
        m = _COUNT.match(cs[i])
        label = m.group(2) if m else cs[i]
        count = int(m.group(1)) if m else 1
        gpu = classify(label)
        if gpu is None:
            i += 1
            continue
        j = i + 1
        while j < len(cs) and _SPEC.match(cs[j]):
            j += 1
        slots: list[str] = []
        while j < len(cs) and len(slots) < len(columns) and (
            _DOLLAR.match(cs[j]) or PLACEHOLDER.match(cs[j]) or _DASHES.match(cs[j])
        ):
            slots.append(cs[j])
            j += 1
        for (header, commitment), slot in zip(columns, slots, strict=False):
            if commitment is None or not _DOLLAR.match(slot):
                continue
            price = price_in(slot)
            if not price or price <= 0:
                continue
            per_gpu = "gpu-hour" in header.lower() or count == 1
            out.append(
                RateCardRow(
                    source=source,
                    captured=captured,
                    sku=f"{cs[i]} [{header}]",
                    gpu_model=gpu.model,
                    gpu_count=count,
                    price_per_instance_hour=round(price * count if per_gpu else price, 6),
                    commitment=commitment,
                    form_factor=gpu.form_factor,
                    interconnect=gpu.interconnect,
                    vram_gb=gpu.vram_gb,
                    note=f"{slot} under '{header}'",
                )
            )
        i = max(j, i + 1)
    return out
