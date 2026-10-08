"""RunPod: runpod.io/gpu-instance/pricing (2023-24), runpod.io/pricing (2024-).

Two layouts, tried in order:

* **Embedded GPU-type records (2023-24).** The page ships its price list as
  GraphQL ``GpuType`` objects -- ``displayName``, ``memoryInGb``,
  ``securePrice``, ``communityPrice``, ``secureSpotPrice``,
  ``communitySpotPrice``. These are the same fields the live RunPod adapter
  reads from the API, so they are read the same way: Secure Cloud is
  on-demand, Community Cloud is the ``community`` commitment, and the spot
  fields are spot. Every price is per GPU-hour.
* **Card text (2024-).** A card per GPU: ``H100 SXM | 80GB VRAM | ... |
  $2.99/hr | Secure Cloud | $2.69/hr | Community Cloud``. The "Starting from"
  figure above each card is the cheaper of the two and is not read.

RunPod rents pods of 1 to 8 GPUs at one per-GPU price, so every row is
recorded as a 1-GPU configuration and the node-size factor applies.
"""

from __future__ import annotations

import json
import re

from ...models import Commitment
from ..htmltext import cells
from ..records import RateCardRow
from . import register
from .common import classify, price_in


def _num(window: str, key: str) -> float | None:
    m = re.search(rf'"{key}":\s*(null|-?\d+(?:\.\d+)?)', window)
    if not m or m.group(1) == "null":
        return None
    return float(m.group(1))


def _str(window: str, key: str) -> str | None:
    m = re.search(rf'"{key}":\s*"([^"]*)"', window)
    return m.group(1) if m else None


def _from_records(text: str, captured: str, source: str) -> list[RateCardRow]:
    text = text.replace('\\"', '"')
    starts = [m.start() for m in re.finditer(r'"__typename":\s*"GpuType"', text)]
    out: list[RateCardRow] = []
    seen: set[tuple] = set()
    for k, start in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else start + 2000
        w = text[start:end]
        # The record's own fields follow its nested lowestPrice object.
        name = _str(w, "displayName")
        if not name:
            continue
        ident = _str(w, "id") or ""
        vram = _num(w, "memoryInGb")
        gpu = classify(f"{name} {ident}", int(vram) if vram else None)
        if gpu is None:
            continue
        for key, commitment in (
            ("securePrice", Commitment.ON_DEMAND),
            ("communityPrice", Commitment.COMMUNITY),
            ("secureSpotPrice", Commitment.SPOT),
            ("communitySpotPrice", Commitment.SPOT),
        ):
            price = _num(w, key)
            if price is None or price <= 0:
                continue
            sig = (name, key, price)
            if sig in seen:
                continue
            seen.add(sig)
            out.append(
                RateCardRow(
                    source=source,
                    captured=captured,
                    sku=f"{name} ({key})",
                    gpu_model=gpu.model,
                    gpu_count=1,
                    price_per_instance_hour=price,
                    commitment=commitment,
                    form_factor=gpu.form_factor,
                    interconnect=gpu.interconnect,
                    vram_gb=gpu.vram_gb,
                    note=f"embedded GpuType record, {key}",
                )
            )
    return out


_VRAM = re.compile(r"^(\d{2,3})\s?GB VRAM$", re.I)
_HR = re.compile(r"^\$\s?\d+(\.\d+)?\s?/\s?hr$", re.I)


def _from_cards(body: bytes, captured: str, source: str) -> list[RateCardRow]:
    cs = cells(body)
    out: list[RateCardRow] = []
    for i, cell in enumerate(cs):
        if len(cell) > 24 or i + 1 >= len(cs):
            continue
        v = _VRAM.match(cs[i + 1])
        if not v:
            continue
        gpu = classify(cell, int(v.group(1)))
        if gpu is None:
            continue
        j = i + 2
        while j < len(cs) and j < i + 12:
            c = cs[j]
            if _VRAM.match(c) or c.lower().startswith("starting from"):
                break
            if _HR.match(c) and j + 1 < len(cs):
                label = cs[j + 1].lower()
                if "secure" in label:
                    commitment = Commitment.ON_DEMAND
                elif "community" in label:
                    commitment = Commitment.COMMUNITY
                else:
                    j += 1
                    continue
                out.append(
                    RateCardRow(
                        source=source,
                        captured=captured,
                        sku=f"{cell} ({cs[j + 1]})",
                        gpu_model=gpu.model,
                        gpu_count=1,
                        price_per_instance_hour=price_in(c),
                        commitment=commitment,
                        form_factor=gpu.form_factor,
                        interconnect=gpu.interconnect,
                        vram_gb=gpu.vram_gb,
                        note=f"card: {c} {cs[j + 1]}",
                    )
                )
            j += 1
    return out


_ROW_START = re.compile(r'class="gpu-pricing-row[ "]|class="gpu_table-row"')
_MODEL = re.compile(r'data-line-clamp=""[^>]*>([^<]{2,40})</div>|class="gpu_name[^"]*">([^<]{2,40})<')
_ROW_VRAM = re.compile(r'>(\d{2,3})</div><div[^>]*>GB VRAM<|>(\d{2,3}) GB VRAM<')
_SECURE = re.compile(r'data-secure(?:-cloud-price)?="(\d+(?:\.\d+)?)"')
_COMMUNITY = re.compile(r'data-community(?:-cloud-price)?="(\d+(?:\.\d+)?)"')


def _from_data_attributes(text: str, captured: str, source: str) -> list[RateCardRow]:
    """Mid-2025 on: the visible price is a script-filled ``0`` (or a copy of
    one of two figures); the rate card sits in ``data-secure-cloud-price`` /
    ``data-community-cloud-price`` (later ``data-secure`` / ``data-community``)
    attributes on each row, which is what the script reads."""
    starts = [m.start() for m in _ROW_START.finditer(text)]
    out: list[RateCardRow] = []
    for k, start in enumerate(starts):
        w = text[start : starts[k + 1] if k + 1 < len(starts) else start + 4000]
        m = _MODEL.search(w)
        if not m:
            continue
        name = (m.group(1) or m.group(2)).strip()
        v = _ROW_VRAM.search(w)
        vram = int(v.group(1) or v.group(2)) if v else None
        gpu = classify(name, vram)
        if gpu is None:
            continue
        for pattern, commitment, label in (
            (_SECURE, Commitment.ON_DEMAND, "Secure Cloud"),
            (_COMMUNITY, Commitment.COMMUNITY, "Community Cloud"),
        ):
            p = pattern.search(w)
            if not p or float(p.group(1)) <= 0:
                continue
            out.append(
                RateCardRow(
                    source=source,
                    captured=captured,
                    sku=f"{name} ({label})",
                    gpu_model=gpu.model,
                    gpu_count=1,
                    price_per_instance_hour=float(p.group(1)),
                    commitment=commitment,
                    form_factor=gpu.form_factor,
                    interconnect=gpu.interconnect,
                    vram_gb=gpu.vram_gb,
                    note=f"row attribute {p.group(0)}",
                )
            )
    return out


_LD = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S | re.I)


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _from_structured_data(text: str, captured: str, source: str) -> list[RateCardRow]:
    """Mid-2026 on: schema.org ``Product`` records, one per GPU, each with a
    "Secure Cloud" and a "Community Cloud" ``Offer``."""
    out: list[RateCardRow] = []
    for block in _LD.findall(text):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for node in _walk(data):
            if node.get("@type") != "Product":
                continue
            name = str(node.get("name", "")).replace(" GPU on Runpod", "")
            gpu = classify(name)
            if gpu is None:
                continue
            offers = node.get("offers", {})
            offers = offers.get("offers", []) if isinstance(offers, dict) else offers
            for offer in offers if isinstance(offers, list) else []:
                label = str(offer.get("name", ""))
                if offer.get("priceCurrency") != "USD":
                    continue
                if label == "Secure Cloud":
                    commitment = Commitment.ON_DEMAND
                elif label == "Community Cloud":
                    commitment = Commitment.COMMUNITY
                else:
                    continue
                try:
                    price = float(offer.get("price"))
                except (TypeError, ValueError):
                    continue
                if price <= 0:
                    continue
                out.append(
                    RateCardRow(
                        source=source,
                        captured=captured,
                        sku=f"{name} ({label})",
                        gpu_model=gpu.model,
                        gpu_count=1,
                        price_per_instance_hour=price,
                        commitment=commitment,
                        form_factor=gpu.form_factor,
                        interconnect=gpu.interconnect,
                        vram_gb=gpu.vram_gb,
                        note=f"schema.org Offer '{label}' {price} USD per hour",
                    )
                )
    return out


@register("runpod")
def parse(body: bytes, captured: str, source: str) -> list[RateCardRow]:
    text = body.decode("utf-8", errors="replace")
    return (
        _from_records(text, captured, source)
        or _from_data_attributes(text, captured, source)
        or _from_structured_data(text, captured, source)
        or _from_cards(body, captured, source)
    )
