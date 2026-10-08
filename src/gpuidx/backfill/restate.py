"""Restate archived rate-card rows onto the index's standard good.

This module adds no adjustment rules of its own. Each row is turned into the
live index's ``RawObservation`` and passed through the live ``normalize`` under
a registered methodology, so the backfill applies exactly the schedule, the
product-identity screen and the 1.75x cap described in METHODOLOGY section 4,
and a change to that schedule is a methodology version here as well.

Three backfill-specific rules sit in front of it, all of them exclusions:

* **On-demand only for the headline.** Reserved, spot and community rows are
  kept, unrestated, for side columns. Restating a 3-year reservation by the
  1.25 commitment factor would put the schedule's judgement at the centre of
  a series whose only claim is that it reads published prices.
* **A GPU-only component price is not the good.** Where a page prices the
  GPU and bills CPU and RAM separately, the row is discarded rather than
  completed with a host configuration the page never quoted.
* **No region screen beyond the live one.** Archived rate cards almost never
  attribute a region, and the live rule admits an undisclosed region.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from ..models import Commitment, NormalizedQuote, RawObservation, Tier
from ..normalize import Rejection, match_contract, normalize
from ..spec import Methodology, methodology_for
from .records import BACKFILL_INDICES, RateCardRow

#: The methodology the backfill restates under. Pinned, not "current": the
#: committed CSV must rebuild identically after the live index moves on.
BACKFILL_METHODOLOGY_VERSION = "1.1.0"


def backfill_methodology() -> Methodology:
    m = methodology_for(BACKFILL_METHODOLOGY_VERSION)
    if m is None:  # pragma: no cover - the registry is append-only
        raise RuntimeError(f"methodology {BACKFILL_METHODOLOGY_VERSION} is not registered")
    return m


@dataclass(frozen=True)
class Restated:
    row: RateCardRow
    index_code: str
    quote: NormalizedQuote


@dataclass(frozen=True)
class Discarded:
    row: RateCardRow
    index_code: str | None
    code: str
    detail: str


def to_observation(row: RateCardRow) -> RawObservation:
    captured = datetime.strptime(row.captured, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    return RawObservation(
        source=row.source,
        source_sku=row.sku,
        gpu_model=row.gpu_model,
        gpu_count=row.gpu_count,
        usd_per_hour_total=row.price_per_instance_hour,
        currency=row.currency,
        commitment=row.commitment,
        form_factor=row.form_factor,
        interconnect=row.interconnect,
        vram_gb=row.vram_gb,
        region=None,
        price_kind=row.price_kind,
        tier=Tier.LIST_PRICE,
        observed_at=captured,
        payload={"note": row.note} if row.note else {},
    )


def restate(
    rows: list[RateCardRow], methodology: Methodology | None = None
) -> tuple[list[Restated], list[Discarded]]:
    """Split rows into restated on-demand quotes and logged discards."""
    methodology = methodology or backfill_methodology()
    kept: list[Restated] = []
    dropped: list[Discarded] = []
    for row in rows:
        obs = to_observation(row)
        contract = match_contract(obs)
        code = contract.index_code if contract else None
        if code is not None and code not in BACKFILL_INDICES:
            dropped.append(Discarded(row, code, "out_of_scope", f"{code} is not backfilled"))
            continue
        if row.basis != "instance":
            dropped.append(
                Discarded(
                    row,
                    code,
                    "component_price",
                    f"priced as {row.basis}; the contract includes host CPU and RAM, "
                    "and adding them would mean choosing a configuration the page did not",
                )
            )
            continue
        if row.commitment != Commitment.ON_DEMAND:
            dropped.append(
                Discarded(
                    row,
                    code,
                    "not_on_demand",
                    f"{row.commitment.value} price kept unrestated in a side column",
                )
            )
            continue
        try:
            quote = normalize(obs, methodology)
        except Rejection as rej:
            dropped.append(Discarded(row, code, rej.code, rej.detail))
            continue
        kept.append(Restated(row, quote.index_code, quote))
    return kept, dropped
