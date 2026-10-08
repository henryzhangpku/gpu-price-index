"""A shared reader for the commonest rate-card shape: a priced table.

Many provider pages are one table: a header row naming the price columns
(``On-Demand Price (/hr)``, ``6 Month Price (/hr)``), then one row per
configuration starting with the GPU's name. Flattened to cells that reads as
a label, some spec cells, then one dollar cell per price column, and this
module pairs the dollar cells with the most recent header's price columns
in order.

A price column's commitment is read from its own header text and nothing
else; a column whose header does not say is not read.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from ...models import Commitment
from ..records import RateCardRow
from .common import Gpu, classify, price_in

DOLLAR = re.compile(
    r"^\$\s?\d{1,3}(?:,\d{3})*(?:\.\d+)?\s?"
    r"(?:/\s?(?:hr|h|hour)|/\s?gpu\s?/\s?h(?:ou)?r|/\s?gpu|per\s+gpu(?:\s*/\s*hour)?|per\s+hour|/\s?gpu-hr)?"
    r"\s?\*{0,2}$",
    re.I,
)


#: A cell standing where a price would be, saying there is none.
PLACEHOLDER = re.compile(
    r"^(n/?a|-|\u2013|on request|contact (?:sales|us)|coming soon|sold out|unavailable)$", re.I
)

_COMMITMENT_WORDS = re.compile(r"commit|reserv|contract|prepaid", re.I)


def commitment_of(header: str) -> Commitment | None:
    h = header.lower()
    if "spot" in h or "interruptible" in h or "preemptible" in h:
        return Commitment.SPOT
    if re.search(r"\b\d+\s*-?\s*(month|year|yr|mo)\b|reserved|commit|term", h):
        return Commitment.RESERVED
    if "on-demand" in h or "on demand" in h or "ondemand" in h or "pay as you go" in h:
        return Commitment.ON_DEMAND
    return None


@dataclass
class Column:
    header: str
    #: None for a price column whose header names no commitment ("Dynamic
    #: price"). It is kept so the columns after it keep their positions, and
    #: never read.
    commitment: Commitment | None


def is_price_header(cell: str) -> bool:
    c = cell.lower()
    return len(cell) <= 60 and ("price" in c or "/hr" in c or "per hour" in c) and "$" not in cell


def read_table(
    cs: list[str],
    captured: str,
    source: str,
    *,
    gpu_count: Callable[[str, list[str]], int] = lambda _label, _cells: 1,
    max_label: int = 40,
    span: int = 9,
    per_gpu: bool = True,
    sections: bool = False,
    note: str = "",
) -> list[RateCardRow]:
    """Rows of every priced table on the page.

    ``gpu_count`` decides the configuration size from the label and the
    row's cells; the default is one GPU, for pages that price per GPU and do
    not state a node size (the live RunPod adapter's convention).
    ``per_gpu`` says whether the page's dollar cells are per GPU-hour or per
    instance-hour; it is a property of the page, read off its headers by the
    caller. ``sections`` lets a price column whose header names no
    commitment ("Price per hour") take it from the nearest preceding short
    heading that does ("On-Demand Cloud GPU Pricing").
    """
    out: list[RateCardRow] = []
    columns: list[Column] = []
    pending: list[str] = []
    section: Commitment | None = None
    for i, cell in enumerate(cs):
        if sections and len(cell) <= 80 and not DOLLAR.match(cell) and not is_price_header(cell):
            heading = commitment_of(cell)
            if heading is not None and classify(cell) is None:
                section = heading
        if is_price_header(cell):
            pending.append(cell)
            continue
        if pending:
            cols = [Column(h, commitment_of(h) or (section if sections else None)) for h in pending]
            if any(c.commitment is not None for c in cols):
                columns = cols
            pending = []
        if len(cell) > max_label or not columns:
            continue
        gpu: Gpu | None = classify(cell)
        if gpu is None:
            continue
        row = cs[i + 1 : i + 1 + span]
        dollars: list[str] = []
        own: list[str] = []
        for c in row:
            if classify(c) is not None and len(c) <= max_label and not DOLLAR.match(c):
                break
            own.append(c)
            # A placeholder keeps its column's place, so "n/a | $2.45" puts
            # the price under the second column rather than the first.
            if DOLLAR.match(c) or PLACEHOLDER.match(c):
                dollars.append(c)
        if not any(DOLLAR.match(d) for d in dollars):
            continue
        # A card that talks about a commitment is a reservation offer, whatever
        # table header happens to precede it on the page.
        talks_commitment = any(_COMMITMENT_WORDS.search(c) for c in own)
        vram = gpu.vram_gb
        if vram is None:
            for c in row:
                if re.fullmatch(r"\d{2,3}", c) and int(c) in (40, 80, 94, 96, 141, 180, 192):
                    vram = int(c)
                    break
        if vram != gpu.vram_gb:
            gpu = classify(cell, vram) or gpu
        n = gpu_count(cell, row)
        for col, d in zip(columns, dollars, strict=False):
            if col.commitment is None or not DOLLAR.match(d):
                continue
            price = price_in(d)
            if not price or price <= 0:
                continue
            if talks_commitment and col.commitment == Commitment.ON_DEMAND:
                continue
            out.append(
                RateCardRow(
                    source=source,
                    captured=captured,
                    sku=f"{cell} [{col.header}]",
                    gpu_model=gpu.model,
                    gpu_count=n,
                    price_per_instance_hour=round(price * n if per_gpu else price, 6),
                    commitment=col.commitment,
                    form_factor=gpu.form_factor,
                    interconnect=gpu.interconnect,
                    vram_gb=gpu.vram_gb,
                    note=(
                        f"{d} per {'GPU' if per_gpu else 'instance'}-hour under "
                        f"'{col.header}'{'; ' + note if note else ''}"
                    ),
                )
            )
    return out
