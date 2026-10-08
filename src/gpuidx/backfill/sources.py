"""The archived pages the backfill reads, and which parser reads each.

A source is one provider's public on-demand price page, possibly at several
URLs over the years (providers rename domains and move pricing pages). Every
URL is listed explicitly: the backfill reads what is named here and nothing
it discovers on its own, so the coverage table in docs/BACKFILL.md is a
statement about this list, not about the web.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .parsers import PARSERS
from .records import RateCardRow
from .wayback import Capture


@dataclass(frozen=True)
class Source:
    name: str
    display: str
    urls: tuple[str, ...]
    parser: str
    #: "weekly" (one snapshot per ISO week) or "monthly".
    cadence: str = "weekly"
    #: Captures outside these YYYYMM bounds are ignored for this source, for
    #: pages that changed meaning (a pricing URL later redirected to a
    #: marketing page that happens to quote a stale price).
    first: str = "202301"
    last: str = "202609"
    #: Original-URL substrings to refuse (localised variants and the like).
    exclude: tuple[str, ...] = field(default_factory=tuple)
    notes: str = ""

    def accepts(self, capture: Capture) -> bool:
        if not (self.first <= capture.timestamp[:6] <= self.last):
            return False
        return not any(x in capture.original for x in self.exclude)

    def parse(self, body: bytes, captured: str) -> list[RateCardRow]:
        fn: Callable[[bytes, str, str], list[RateCardRow]] = PARSERS[self.parser]
        return fn(body, captured, self.name)


SOURCES: list[Source] = [
    Source(
        name="lambda",
        display="Lambda (Lambda Labs)",
        urls=(
            "lambdalabs.com/service/gpu-cloud",
            "lambdalabs.com/service/gpu-cloud/pricing",
            "lambda.ai/service/gpu-cloud",
            "lambda.ai/pricing",
        ),
        parser="lambda",
    ),
    Source(
        name="runpod",
        display="RunPod",
        urls=("runpod.io/gpu-instance/pricing", "runpod.io/pricing"),
        parser="runpod",
    ),
    Source(
        name="coreweave",
        display="CoreWeave",
        urls=("coreweave.com/gpu-cloud-pricing", "coreweave.com/pricing"),
        parser="coreweave",
        cadence="monthly",
    ),
    Source(
        name="paperspace",
        display="Paperspace (DigitalOcean)",
        urls=("paperspace.com/pricing",),
        parser="paperspace",
    ),
    Source(
        name="fluidstack",
        display="FluidStack",
        urls=("fluidstack.io/pricing",),
        parser="fluidstack",
    ),
    Source(
        name="datacrunch",
        display="DataCrunch",
        urls=("datacrunch.io/products",),
        parser="datacrunch",
    ),
    Source(
        name="hyperstack",
        display="Hyperstack",
        urls=("hyperstack.cloud/gpu-pricing",),
        parser="hyperstack",
    ),
    Source(
        name="nebius",
        display="Nebius",
        urls=("nebius.com/prices",),
        parser="nebius",
    ),
    Source(
        name="digitalocean",
        display="DigitalOcean GPU Droplets",
        urls=("digitalocean.com/pricing/gpu-droplets",),
        parser="digitalocean",
    ),
    Source(
        name="crusoe",
        display="Crusoe Cloud",
        urls=("crusoecloud.com/pricing", "crusoe.ai/cloud/pricing"),
        parser="crusoe",
    ),
    Source(
        name="voltagepark",
        display="Voltage Park",
        urls=("voltagepark.com/pricing",),
        parser="voltagepark",
    ),
]

SOURCE_BY_NAME = {s.name: s for s in SOURCES}
