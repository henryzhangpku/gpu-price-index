"""Nebius: nebius.com/prices.

An "On-demand GPU pricing" table -- ``VRAM, GB | vCPUs | RAM, GB | Price per
hour`` -- with one row per GPU, priced per GPU-hour with the host share
stated (``NVIDIA H100 GPU | 80 | 16 | 200 | $2.95``). The header names no
commitment, so it is taken from the heading. Reservation cards elsewhere on
the page ("commitment of hundreds of units for at least 3 months") are
recognised by their own wording and never read as on-demand.
"""

from __future__ import annotations

from ..htmltext import cells
from ..records import RateCardRow
from . import register
from .tables import read_table


@register("nebius")
def parse(body: bytes, captured: str, source: str) -> list[RateCardRow]:
    return read_table(cells(body), captured, source, sections=True)
