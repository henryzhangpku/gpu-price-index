"""DataCrunch: datacrunch.io/products.

One table per GPU family: ``Instance name | GPU model | GPU | CPU | RAM | VRAM
| On demand price | 6-month price | 2-year price``, priced per instance-hour
(``$25.36/h``). A row is read from its instance name (``8H100.80S.176V``),
whose leading number is the GPU count; the GPU column after the model must
agree with it, and a row where the two disagree is not read.

The live DataCrunch adapter excludes this venue's spot prices as administered
(exactly half of on-demand on every SKU). The archived page quotes term
prices rather than spot; those are recorded as reserved and never restated.
"""

from __future__ import annotations

import re

from ..htmltext import cells
from ..records import RateCardRow
from . import register
from .common import classify, price_in
from .tables import DOLLAR, PLACEHOLDER, Column, commitment_of, is_price_header

_NAME = re.compile(r"^(\d{1,2})[A-Z]\d{2,3}[A-Z]?\.\S+$")


@register("datacrunch")
def parse(body: bytes, captured: str, source: str) -> list[RateCardRow]:
    cs = cells(body)
    out: list[RateCardRow] = []
    columns: list[Column] = []
    pending: list[str] = []
    for i, cell in enumerate(cs):
        if is_price_header(cell):
            pending.append(cell)
            continue
        if pending:
            cols = [Column(h, commitment_of(h)) for h in pending]
            if any(c.commitment is not None for c in cols):
                columns = cols
            pending = []
        m = _NAME.match(cell)
        if not m or not columns or i + 2 >= len(cs):
            continue
        label, count_cell = cs[i + 1], cs[i + 2]
        n = int(m.group(1))
        if not count_cell.isdigit() or int(count_cell) != n:
            continue
        gpu = classify(label)
        if gpu is None:
            continue
        dollars = [c for c in cs[i + 3 : i + 11] if DOLLAR.match(c) or PLACEHOLDER.match(c)][: len(columns)]
        for col, d in zip(columns, dollars, strict=False):
            if col.commitment is None or not DOLLAR.match(d):
                continue
            price = price_in(d)
            if not price or price <= 0:
                continue
            out.append(
                RateCardRow(
                    source=source,
                    captured=captured,
                    sku=f"{cell} {label} [{col.header}]",
                    gpu_model=gpu.model,
                    gpu_count=n,
                    price_per_instance_hour=price,
                    commitment=col.commitment,
                    form_factor=gpu.form_factor,
                    interconnect=gpu.interconnect,
                    vram_gb=gpu.vram_gb,
                    note=f"{d} per instance-hour under '{col.header}'",
                )
            )
    return out
