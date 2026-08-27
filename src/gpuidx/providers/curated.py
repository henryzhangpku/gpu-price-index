"""Curated rate cards for venues without an open pricing API.

The hyperscalers and several large neoclouds gate their pricing behind an
authenticated API or a rendered page, but they are too economically
significant to omit -- excluding them would bias the index toward the cheap
tail of the market.

They are therefore carried as an explicitly curated file rather than smuggled
in as if they were observed. Three properties keep that honest:

1. Every entry names its ``source_url`` and the date it was captured.
2. Everything from this provider enters at ``Tier.JUDGEMENT``, the lowest
   weight in the waterfall.
3. Entries older than ``MAX_STALENESS_DAYS`` are dropped outright, so a
   forgotten catalogue degrades into missing data rather than into a
   confidently wrong number.

The shipped values are indicative and must be re-verified against each
vendor's public pricing page before this is used for anything real. The
staleness gate is what makes that safe to say out loud.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx

from ..models import Commitment, FormFactor, Interconnect, RawObservation, Tier
from .base import Provider

CATALOG_PATH = Path(__file__).resolve().parents[3] / "data" / "curated_rate_cards.json"

#: A curated price older than this is no longer evidence about today.
MAX_STALENESS_DAYS = 45


class Curated(Provider):
    name = "curated"
    source_url = "file://data/curated_rate_cards.json"

    def __init__(self, path: Path | None = None, today: date | None = None) -> None:
        self.path = path or CATALOG_PATH
        self.today = today or datetime.now(UTC).date()
        self.dropped_stale: list[str] = []

    def collect(self, client: httpx.Client) -> list[RawObservation]:  # noqa: ARG002
        if not self.path.exists():
            return []
        entries = json.loads(self.path.read_text(encoding="utf-8"))

        out: list[RawObservation] = []
        for entry in entries:
            as_of = date.fromisoformat(entry["as_of"])
            age = (self.today - as_of).days
            if age > MAX_STALENESS_DAYS:
                self.dropped_stale.append(f"{entry['vendor']}:{entry['sku']} ({age}d old)")
                continue

            out.append(
                RawObservation(
                    source=f"curated:{entry['vendor']}",
                    source_sku=entry["sku"],
                    gpu_model=entry["gpu_model"],
                    gpu_count=int(entry["gpu_count"]),
                    usd_per_hour_total=float(entry["usd_per_hour_total"]),
                    commitment=Commitment(entry.get("commitment", "on_demand")),
                    form_factor=FormFactor(entry.get("form_factor", "unknown")),
                    interconnect=Interconnect(entry.get("interconnect", "unknown")),
                    vram_gb=entry.get("vram_gb"),
                    region=entry.get("region"),
                    available=None,
                    tier=Tier.JUDGEMENT,
                    # Event time is when the rate card was true, not now. The
                    # bitemporal store keeps that distinct from ingest time.
                    observed_at=datetime.combine(
                        as_of, datetime.min.time(), tzinfo=UTC
                    ),
                    payload={
                        "source_url": entry.get("source_url"),
                        "as_of": entry["as_of"],
                        "age_days": age,
                        "verified": entry.get("verified", False),
                    },
                )
            )
        return out
