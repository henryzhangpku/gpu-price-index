"""Lambda: lambdalabs.com/service/gpu-cloud (2023-2025), lambda.ai (2025-).

The on-demand table has kept one reading order since 2023: a cell naming the
instance as ``<n>x NVIDIA <GPU>``, the per-GPU VRAM, CPU/RAM/storage cells,
then a price -- per instance (``$20.72 / hr``) until 2024 and per GPU
(``$2.99 / GPU / hr``) after. Reserved and cluster tables follow the on-demand
one on the same page and are recognised by their section heading or by a
stated term (``3-years``), and recorded as reserved.

From December 2025 the instance count left the row: the page shows a tab
strip (``8x | 4x | 2x | 1x``) followed by one table per tab, in that order,
whose rows name only the GPU. The count of a row is read as the tab at the
position of its table. That is an inference from layout, checked against the
vCPU column (208/104/52/26 for 8/4/2/1 H100s on every capture read), and it
only affects the node-size adjustment, because the price column is per GPU.
The 1-Click Clusters block above the tables quotes cluster commitments of a
week or more and is not read.
"""

from __future__ import annotations

import re

from ...models import Commitment
from ..htmltext import cells
from ..records import RateCardRow
from . import register
from .common import classify, per_gpu_wording, price_in

_ROW = re.compile(r"^(?:(on[- ]demand|reserved)\s+)?(\d{1,2})\s?x\s+(?:NVIDIA\s+)?(.+)$", re.I)
_VRAM = re.compile(r"^(\d{2,3})\s?GB$", re.I)
_TERM = re.compile(r"\b\d+\s*-?\s*(years?|months?|yrs?|mos?|weeks?)\b", re.I)
_RESERVED_HEAD = re.compile(r"reserved|cluster|1-click|private cloud|sprint", re.I)
_ONDEMAND_HEAD = re.compile(r"on[- ]demand", re.I)
_PER_GPU_HEADER = re.compile(r"price\s*/\s*gpu", re.I)
_PRICE_HEADER = re.compile(r"^price(?![a-z])", re.I)
_BARE_PRICE = re.compile(r"^\$\s?\d+(\.\d+)?\W{0,3}$")


def _section(cell: str, current: str) -> str:
    if len(cell) > 80:
        return current
    if _RESERVED_HEAD.search(cell) and not _ONDEMAND_HEAD.search(cell):
        return "reserved"
    if _ONDEMAND_HEAD.search(cell):
        return "on_demand"
    return current


@register("lambda")
def parse(body: bytes, captured: str, source: str) -> list[RateCardRow]:
    cs = cells(body)
    out: list[RateCardRow] = []
    section = "on_demand"
    #: Set by a column header such as "PRICE/GPU/HR*": from mid-2025 the
    #: price cells are bare amounts and the unit lives only in the header.
    header_per_gpu = False
    i = 0
    while i < len(cs):
        cell = cs[i]
        m = _ROW.match(cell)
        if not m:
            section = _section(cell, section)
            if _PRICE_HEADER.search(cell):
                header_per_gpu = bool(_PER_GPU_HEADER.search(cell))
            i += 1
            continue
        prefix = (m.group(1) or "").lower()
        count = int(m.group(2))
        label = m.group(3)
        vram = None
        price = None
        price_cell = ""
        j = i + 1
        while j < len(cs) and j <= i + 9 and not _ROW.match(cs[j]):
            c = cs[j]
            v = _VRAM.match(c)
            if v and vram is None:
                vram = int(v.group(1))
            elif price_in(c) is not None and ("hr" in c.lower() or "hour" in c.lower()):
                price, price_cell = price_in(c), c
                break
            elif header_per_gpu and _BARE_PRICE.match(c):
                price, price_cell = price_in(c), f"{c} (column: per GPU-hour)"
                break
            elif price is None and classify(c) is not None and len(c) <= 12:
                label = f"{label} {c}"
            j += 1
        if price is None:
            i += 1
            continue
        after = " ".join(cs[j + 1 : j + 3])
        if prefix:
            reserved = prefix == "reserved"
        else:
            reserved = section == "reserved" or bool(_TERM.search(after))
        gpu = classify(label, vram)
        if gpu is not None and price > 0:
            per_gpu = per_gpu_wording(price_cell) or price_cell.endswith("(column: per GPU-hour)")
            total = price * count if per_gpu else price
            out.append(
                RateCardRow(
                    source=source,
                    captured=captured,
                    sku=f"{count}x {label}".strip(),
                    gpu_model=gpu.model,
                    gpu_count=count,
                    price_per_instance_hour=round(total, 6),
                    commitment=Commitment.RESERVED if reserved else Commitment.ON_DEMAND,
                    form_factor=gpu.form_factor,
                    interconnect=gpu.interconnect,
                    vram_gb=gpu.vram_gb,
                    note=(price_cell + (f" | {_TERM.search(after).group(0)}" if _TERM.search(after) else "")),
                )
            )
        i = j + 1
    if not out:
        out = _parse_tabbed(cs, captured, source)
    return out


_TAB = re.compile(r"^(\d{1,2})x$")


def _parse_tabbed(cs: list[str], captured: str, source: str) -> list[RateCardRow]:
    out: list[RateCardRow] = []
    tabs: list[int] = []
    table = -1
    for k, cell in enumerate(cs):
        m = _TAB.match(cell)
        if m and k + 1 < len(cs) and _TAB.match(cs[k + 1]) and not tabs:
            run = []
            q = k
            while q < len(cs) and _TAB.match(cs[q]):
                run.append(int(_TAB.match(cs[q]).group(1)))
                q += 1
            tabs = run
            continue
        if not tabs:
            continue
        if _PER_GPU_HEADER.search(cell):
            table += 1
            continue
        if table < 0 or table >= len(tabs) or not cell.upper().startswith("NVIDIA "):
            continue
        if len(cell) > 40:
            continue
        vram = None
        for c in cs[k + 1 : k + 7]:
            v = _VRAM.match(c)
            if v and vram is None:
                vram = int(v.group(1))
            if _BARE_PRICE.match(c):
                gpu = classify(cell, vram)
                price = price_in(c)
                if gpu is not None and price and price > 0:
                    count = tabs[table]
                    out.append(
                        RateCardRow(
                            source=source,
                            captured=captured,
                            sku=f"{count}x {cell} (tab)",
                            gpu_model=gpu.model,
                            gpu_count=count,
                            price_per_instance_hour=round(price * count, 6),
                            commitment=Commitment.ON_DEMAND,
                            form_factor=gpu.form_factor,
                            interconnect=gpu.interconnect,
                            vram_gb=gpu.vram_gb,
                            note=f"{c} (column: per GPU-hour; count from tab {count}x)",
                        )
                    )
                break
            if c.upper().startswith("NVIDIA "):
                break
    return out
