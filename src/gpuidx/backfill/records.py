"""What a parser reads off an archived rate card.

A ``RateCardRow`` is the backfill's raw layer: exactly what the archived page
said, before any restatement. It is deliberately close to the live index's
``RawObservation`` so that restatement can reuse the live normaliser rather
than a second copy of the adjustment schedule.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from ..models import Commitment, FormFactor, Interconnect, PriceKind

#: The backfill's coverage: the contracts a 2023-2026 rate card can speak to.
#: MI300X is omitted: almost no archived public page priced it.
BACKFILL_INDICES = ("GIX-H100", "GIX-H200", "GIX-B200", "GIX-A100")


@dataclass(frozen=True)
class RateCardRow:
    """One priced configuration on one archived page."""

    source: str
    #: Wayback capture timestamp, YYYYMMDDhhmmss UTC. The price is what the
    #: page said at that moment, not when it was set.
    captured: str
    #: The configuration as the page named it, verbatim enough to grep for.
    sku: str
    #: A model string the live contract aliases recognise ("H100 SXM",
    #: "H100 PCIe", "A100 SXM4 80GB", ...). Parsers map page wording onto these.
    gpu_model: str
    gpu_count: int
    price_per_instance_hour: float
    commitment: Commitment
    form_factor: FormFactor = FormFactor.UNKNOWN
    interconnect: Interconnect = Interconnect.UNKNOWN
    vram_gb: int | None = None
    currency: str = "USD"
    #: QUOTED for a rate for this configuration; FROM_FLOOR for a "from $X" /
    #: "starting at" teaser, which the live screen rejects with that reason.
    price_kind: PriceKind = PriceKind.QUOTED
    #: "instance" when the price buys the configuration with its host CPU and
    #: RAM; "gpu_component" when the page prices the GPU alone and bills CPU
    #: and RAM on top (CoreWeave's a-la-carte card). The contract includes the
    #: host, so a component price cannot be restated and is discarded.
    basis: str = "instance"
    #: Anything a reader of the row should know: "from" pricing, a stated
    #: term, the table the row came from.
    note: str = ""

    @property
    def price_per_gpu_hour(self) -> float:
        return self.price_per_instance_hour / self.gpu_count

    @property
    def month(self) -> str:
        return f"{self.captured[:4]}-{self.captured[4:6]}"

    @property
    def day(self) -> str:
        return f"{self.captured[:4]}-{self.captured[4:6]}-{self.captured[6:8]}"

    def as_dict(self) -> dict:
        out = asdict(self)
        for key in ("commitment", "form_factor", "interconnect", "price_kind"):
            out[key] = out[key].value
        out["price_per_gpu_hour"] = round(self.price_per_gpu_hour, 6)
        return out
