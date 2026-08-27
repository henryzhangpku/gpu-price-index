"""Data model for the benchmark pipeline.

Three record types matter:

``RawObservation``
    Exactly what a venue said, untouched. Preserved so that any published
    value can be rebuilt from source.

``NormalizedQuote``
    A raw observation restated as the benchmark-equivalent contract, carrying
    the audit trail of every adjustment applied to get there.

``IndexValue``
    A published (or withheld) benchmark value for one index on one date, at
    one revision.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Tier(int, Enum):
    """Waterfall tier of an input, in descending order of evidential quality.

    The tier drives both the weight an observation receives and the
    publication gates: a value built only from TIER_3 inputs is not
    representative of a transactable market and is withheld.
    """

    #: Executable offer on a venue where the capacity is observably rentable.
    EXECUTABLE = 1
    #: Published on-demand rate card, capacity not confirmed at capture time.
    LIST_PRICE = 2
    #: Curated or carried-forward price standing in for an unobservable market.
    JUDGEMENT = 3


class Commitment(str, Enum):
    ON_DEMAND = "on_demand"
    SPOT = "spot"
    COMMUNITY = "community"
    RESERVED = "reserved"


class FormFactor(str, Enum):
    SXM = "sxm"
    PCIE = "pcie"
    UNKNOWN = "unknown"


class Interconnect(str, Enum):
    NVLINK = "nvlink"
    INFINIBAND = "infiniband"
    ETHERNET = "ethernet"
    NONE = "none"
    UNKNOWN = "unknown"


class RawObservation(BaseModel):
    """A price as the venue stated it, before any interpretation."""

    source: str
    source_sku: str
    gpu_model: str
    gpu_count: int
    usd_per_hour_total: float
    commitment: Commitment
    form_factor: FormFactor = FormFactor.UNKNOWN
    interconnect: Interconnect = Interconnect.UNKNOWN
    vram_gb: int | None = None
    region: str | None = None
    available: bool | None = None
    reliability: float | None = None
    tier: Tier
    #: When the venue's price was true. For polled APIs this is capture time.
    observed_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)

    @property
    def usd_per_gpu_hour(self) -> float:
        return self.usd_per_hour_total / self.gpu_count

    def fingerprint(self) -> str:
        """Stable identity for a quote, used to detect a stalled feed.

        Deliberately excludes ``observed_at`` -- the same rate card seen on
        two consecutive days must produce the same fingerprint.
        """
        material = json.dumps(
            {
                "source": self.source,
                "sku": self.source_sku,
                "gpus": self.gpu_count,
                "price": round(self.usd_per_hour_total, 6),
                "commitment": self.commitment.value,
                "region": self.region,
            },
            sort_keys=True,
        )
        return hashlib.sha256(material.encode()).hexdigest()[:16]


class Adjustment(BaseModel):
    """One multiplicative step from a raw price toward the benchmark contract."""

    name: str
    factor: float
    rationale: str


class NormalizedQuote(BaseModel):
    """A raw observation restated as the benchmark-equivalent contract."""

    index_code: str
    source: str
    source_sku: str
    raw_usd_per_gpu_hour: float
    normalized_usd_per_gpu_hour: float
    adjustments: list[Adjustment]
    tier: Tier
    region: str | None
    observed_at: datetime
    fingerprint: str

    @property
    def total_adjustment(self) -> float:
        return self.normalized_usd_per_gpu_hour / self.raw_usd_per_gpu_hour


class IndexStatus(str, Enum):
    PUBLISHED = "published"
    #: Gates failed. A gap is an honest answer; a guess is not.
    WITHHELD = "withheld"


class IndexValue(BaseModel):
    """A benchmark value for one index on one date, at one revision."""

    index_code: str
    index_date: date
    revision: int
    status: IndexStatus
    value: float | None
    #: Contributing providers after screening, not raw quote count.
    provider_count: int
    observation_count: int
    dispersion: float | None
    withheld_reason: str | None = None
    methodology_version: str
    published_at: datetime
    revision_reason: str | None = None


class GateResult(BaseModel):
    name: str
    passed: bool
    detail: str


class QualityFlag(BaseModel):
    severity: Literal["info", "warn", "error"]
    code: str
    detail: str
    #: Set for flags raised while building one index; None for run-wide flags.
    index_code: str | None = None
