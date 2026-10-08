"""Shared reading rules: what a page's GPU wording means.

Every parser maps the page's own words onto a model string the live contract
aliases recognise, plus a form factor and fabric *only where the words state
one*. Nothing is inferred from price, and nothing from what the provider
"probably" ran: an unstated form factor stays UNKNOWN and the live schedule
prices that uncertainty (METHODOLOGY section 4), rather than a parser
resolving it silently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ...models import FormFactor, Interconnect


@dataclass(frozen=True)
class Gpu:
    model: str  # alias-compatible model string, e.g. "H100 SXM"
    form_factor: FormFactor
    interconnect: Interconnect
    vram_gb: int | None


_FAMILIES = ("H100", "H200", "B200", "A100")


def classify(text: str, vram_gb: int | None = None) -> Gpu | None:
    """Read a covered GPU out of a page's label, or None.

    ``GH200``, ``GB200``, ``H100 NVL`` and ``H800`` are deliberately not read
    as their neighbours: GH200 and GB200 are superchips with a CPU attached,
    H800 is the export variant, and H100 NVL is a different card that the live
    product-identity screen rejects with its reason, so it is passed through
    labelled rather than dropped here.
    """
    t = " " + re.sub(r"[\-_/]", " ", text.upper()) + " "
    t = re.sub(r"\s+", " ", t)
    if re.search(r"\bG[HB]\s?200\b|\bH800\b|\bA800\b|\bB100\b|\bB300\b|\bGB300\b", t):
        return None
    family = None
    for fam in _FAMILIES:
        if re.search(rf"(?<![A-Z0-9]){fam}(?![0-9])", t):
            family = fam
            break
    if family is None:
        return None

    if vram_gb is None:
        m = re.search(r"(\d{2,3})\s?GB", t)
        if m:
            vram_gb = int(m.group(1))

    nvl = bool(re.search(rf"{family}\s?NVL\b", t))
    if nvl:
        return Gpu(f"{family} NVL", FormFactor.PCIE, Interconnect.NVLINK, vram_gb)

    if re.search(r"\bSXM\d?\b|\bHGX\b", t):
        ff, ic = FormFactor.SXM, Interconnect.NVLINK
    elif re.search(r"\bPCIE\b|\bPCI E\b", t):
        ff, ic = FormFactor.PCIE, Interconnect.NONE
    else:
        ff, ic = FormFactor.UNKNOWN, Interconnect.UNKNOWN
    # A stated NVLink fabric is recorded; it does not by itself say SXM, since
    # PCIe cards are bridged with NVLink too.
    if ff != FormFactor.SXM and re.search(r"\bNVLINK\b", t):
        ic = Interconnect.NVLINK

    if family == "A100":
        label = "A100"
        if ff == FormFactor.SXM:
            label = "A100 SXM"
        elif ff == FormFactor.PCIE:
            label = "A100 PCIe"
        if vram_gb == 40:
            label = f"{label} 40GB"
        return Gpu(label, ff, ic, vram_gb)

    if ff == FormFactor.SXM:
        label = f"{family} SXM"
    elif ff == FormFactor.PCIE:
        label = f"{family} PCIe" if family == "H100" else family
    else:
        label = family
    return Gpu(label, ff, ic, vram_gb)


_COUNT = re.compile(r"^\s*(\d{1,2})\s?x\b", re.I)


def leading_count(text: str) -> int | None:
    m = _COUNT.match(text)
    return int(m.group(1)) if m else None


_PRICE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)")


def price_in(text: str) -> float | None:
    m = _PRICE.search(text)
    return float(m.group(1).replace(",", "")) if m else None


def per_gpu_wording(text: str) -> bool:
    """Does a price cell say it is per GPU rather than per instance?"""
    t = text.lower().replace(" ", "")
    return any(k in t for k in ("/gpu", "pergpu", "/h100", "/h200", "/b200", "/a100", "gpu/hr", "gpu/hour", "gpu-hr", "gpuhr"))
