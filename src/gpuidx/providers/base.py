"""Provider adapter contract.

Every venue gets one adapter whose only job is to turn that venue's response
into ``RawObservation`` records. Adapters do no normalisation and no
filtering beyond dropping records they cannot parse, so that the raw layer
stays a faithful record of what the venue said.

Adapters must never raise: a venue being down is a data-quality event, not a
crash. A failed collection returns an empty list and a recorded error, which
the publication gates then account for.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from datetime import UTC, datetime

import httpx

from ..models import FormFactor, Interconnect, RawObservation
from ..spec import US_REGION_TOKENS

USER_AGENT = "gpuidx/0.1 (benchmark reference implementation)"
TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class CollectionError(Exception):
    """Raised internally by adapters; converted to a quality flag upstream."""


class Provider(ABC):
    """One venue."""

    #: Stable identifier used for provider-level weighting and dropout checks.
    name: str
    #: Human-facing label for the source of truth, cited in the audit trail.
    source_url: str

    @abstractmethod
    def collect(self, client: httpx.Client) -> list[RawObservation]:
        """Return every price this venue currently publishes."""

    # -- helpers shared by adapters ----------------------------------------

    @staticmethod
    def now() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def infer_form_factor(*texts: str | None) -> FormFactor:
        blob = " ".join(t for t in texts if t).lower()
        if "sxm" in blob or "oam" in blob:
            return FormFactor.SXM
        if "pcie" in blob or "pci-e" in blob or "nvl" in blob:
            return FormFactor.PCIE
        return FormFactor.UNKNOWN

    @staticmethod
    def infer_interconnect(*texts: str | None) -> Interconnect:
        blob = " ".join(t for t in texts if t).lower()
        if "nvlink" in blob or "nvl" in blob or "sxm" in blob:
            return Interconnect.NVLINK
        if "infiniband" in blob or blob.strip() == "ib":
            return Interconnect.INFINIBAND
        if "ethernet" in blob or "roce" in blob:
            return Interconnect.ETHERNET
        if "pcie" in blob:
            return Interconnect.NONE
        return Interconnect.UNKNOWN

    @staticmethod
    def is_us_region(region: str | None) -> bool | None:
        """Return True/False, or None when the venue does not disclose region.

        The tri-state matters: an undisclosed region is screened differently
        from a disclosed non-US one.
        """
        if not region:
            return None
        blob = re.sub(r"[_\-/]", " ", region).lower()
        return any(tok in blob for tok in US_REGION_TOKENS)


def make_client() -> httpx.Client:
    return httpx.Client(
        timeout=TIMEOUT,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )
