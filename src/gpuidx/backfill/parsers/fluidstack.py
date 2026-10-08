"""FluidStack: fluidstack.io/pricing.

One table, priced per GPU-hour: ``GPU Model | VRAM (GB) | Max vCPUs per GPU |
Max RAM (GB) per GPU | GPU On-Demand Price (/hr) | GPU 6 Month Price (/hr)``.
The term column is recorded as reserved. No node size is stated, so rows are
one GPU, as in the live RunPod adapter.

Until early 2024 the CPU and RAM columns carried their own prices --
``Max vCPUs per GPU ($0.01/hr)`` -- which makes the GPU column a component
price with the host billed on top, the same a-la-carte card CoreWeave
published. Those pages are read with ``basis="gpu_component"`` and discarded
at restatement; the later pages state no separate host price and are read as
instance prices.

From mid-2025 the page lists cluster prices (``H100 SXM | $2.10 | / GPU / H``)
under no commitment heading, which the table reader does not attribute.
"""

from __future__ import annotations

import re
from dataclasses import replace

from ..htmltext import cells
from ..records import RateCardRow
from . import register
from .tables import read_table

_HOST_PRICED = re.compile(r"(vcpu|ram).*\(\s*\$\s?\d", re.I)


@register("fluidstack")
def parse(body: bytes, captured: str, source: str) -> list[RateCardRow]:
    cs = cells(body)
    rows = read_table(cs, captured, source, sections=True)
    if any(_HOST_PRICED.search(c) for c in cs if len(c) < 80):
        rows = [replace(r, basis="gpu_component", note=r.note + "; host CPU/RAM billed separately") for r in rows]
    return rows
