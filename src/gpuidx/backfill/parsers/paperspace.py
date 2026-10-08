"""Paperspace (DigitalOcean): paperspace.com/pricing.

Card layout on the "Hourly" tab: ``A100 | $ | 3.09 | / hour | NVIDIA A100 GPU
| 90GB RAM | 12 vCPU``. The page says multi-GPU instances are priced by
multiplying the single-GPU card, so rows are one GPU.

Three things on this page are traps, and each is handled by a stated rule:

* **Template remnants.** From 2023 the H100 card carried a second price block
  copied from the A100 card -- ``H100 | New | $ | 3.09 | / hour | NVIDIA A100
  GPU`` -- which a reader of the HTML cannot tell from a real price. A price is
  read only when the spec line under it names the same GPU as the card.
* **Footnoted prices.** Asterisked prices are explained in footnotes, e.g.
  ``*$2.24/hour pricing is for a 3-year commitment``. An asterisked price is
  recorded only when a footnote states its commitment, and then as reserved.
* **On-demand in the small print.** The same footnote says ``On-demand
  pricing for H100 is $5.95/hour``; that sentence is read as an on-demand
  quote for the named GPU.

An unasterisked hourly card price is the pay-as-you-go rate (the page's
alternative tab is a monthly plan) and is recorded as on-demand. The form
factor is taken from the card's spec line where it names one ("NVIDIA HGX
H100 GPU").
"""

from __future__ import annotations

import re

from ...models import Commitment
from ..htmltext import cells, flat
from ..records import RateCardRow
from . import register
from .common import classify

_NUM = re.compile(r"^(\d+(?:\.\d+)?)(\**)$")
_FOOT_COMMIT = re.compile(r"(\*+)\s*\$(\d+(?:\.\d+)?)\s*/\s*hour pricing is for an? ([\w -]+?commitment)", re.I)
_FOOT_OD = re.compile(r"on-demand pricing for (H100|H200|B200|A100[-\w]*) is \$(\d+(?:\.\d+)?)\s*/\s*hour", re.I)


def _family(gpu_model: str) -> str:
    return gpu_model.split()[0]


@register("paperspace")
def parse(body: bytes, captured: str, source: str) -> list[RateCardRow]:
    cs = cells(body)
    text = flat(body)
    commitments = {(m.group(1), float(m.group(2))): m.group(3) for m in _FOOT_COMMIT.finditer(text)}
    out: list[RateCardRow] = []
    spec_for: dict[str, str] = {}

    for i, cell in enumerate(cs):
        if len(cell) > 12:
            continue
        # "A100-80G" names its memory without the B.
        label = re.sub(r"(\d{2,3})G$", r"\1GB", cell.replace("-", " "))
        gpu = classify(label)
        if gpu is None:
            continue
        j = i + 1
        while j < len(cs) and j <= i + 14:
            if classify(cs[j]) is not None and len(cs[j]) <= 12:
                break  # next card
            if cs[j] == "$" and j + 3 < len(cs) and _NUM.match(cs[j + 1]) and cs[j + 2].lower().startswith("/ hour"):
                price = float(_NUM.match(cs[j + 1]).group(1))
                stars = _NUM.match(cs[j + 1]).group(2)
                spec = cs[j + 3]
                spec_gpu = classify(spec)
                j += 4
                if spec_gpu is None or _family(spec_gpu.model) != _family(gpu.model):
                    continue  # a price block describing some other card
                spec_for[_family(gpu.model)] = spec
                if stars:
                    term = commitments.get((stars, price))
                    if term is None:
                        continue
                    commitment, note = Commitment.RESERVED, f"${price}{stars}/hour: {term}"
                else:
                    commitment, note = Commitment.ON_DEMAND, f"${price}/hour hourly card"
                g = classify(f"{label} {spec}") or gpu
                out.append(
                    RateCardRow(
                        source=source,
                        captured=captured,
                        sku=f"{cell} ({spec})",
                        gpu_model=g.model,
                        gpu_count=1,
                        price_per_instance_hour=price,
                        commitment=commitment,
                        form_factor=g.form_factor,
                        interconnect=g.interconnect,
                        vram_gb=g.vram_gb,
                        note=note,
                    )
                )
                continue
            j += 1

    for m in _FOOT_OD.finditer(text):
        label = m.group(1)
        spec = spec_for.get(label.split("-")[0].upper(), "")
        g = classify(f"{label} {spec}")
        if g is None:
            continue
        out.append(
            RateCardRow(
                source=source,
                captured=captured,
                sku=f"{label} (footnote)",
                gpu_model=g.model,
                gpu_count=1,
                price_per_instance_hour=float(m.group(2)),
                commitment=Commitment.ON_DEMAND,
                form_factor=g.form_factor,
                interconnect=g.interconnect,
                vram_gb=g.vram_gb,
                note=f"footnote: '{m.group(0)}'",
            )
        )
    return out
