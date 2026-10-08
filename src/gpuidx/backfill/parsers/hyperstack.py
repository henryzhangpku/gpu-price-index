"""Hyperstack: hyperstack.cloud/gpu-pricing.

``GPU | VRAM (GB) | Max vCPUs per GPU | Max RAM (GB) per GPU | Price per
hour`` under an "On-Demand Cloud GPU Pricing" heading, each price stated
``per GPU``. The column header carries no commitment, so it is taken from the
heading above it; later layouts add reservation tables under their own
headings, which the same rule records as reserved.
"""

from __future__ import annotations

from ..htmltext import cells
from ..records import RateCardRow
from . import register
from .tables import read_table


@register("hyperstack")
def parse(body: bytes, captured: str, source: str) -> list[RateCardRow]:
    return read_table(cells(body), captured, source, sections=True)
