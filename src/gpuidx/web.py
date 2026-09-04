"""Export the archive as a JSON bundle for the static demo site.

The site is a reader, never a source. Everything it displays is derived here
from the two durable artefacts -- the snapshots and the tape -- through the
same ``prepare_quotes`` -> ``estimate`` path that ``verify`` uses. A page can
therefore be wrong about presentation but never about the numbers: if the
site and the tape disagreed, ``verify`` would already have failed.

Three files, matching the three questions a reader has:

``meta.json``
    What are the rules? Contract definitions, the adjustment schedule, the
    tier weights, and the gates -- the contents of ``spec.py``, published so
    a reader can argue with the numbers rather than the prose.

``series.json``
    What has been published? The whole tape, including superseded revisions,
    so the site can show a correction as a correction rather than silently
    displaying the latest value.

``latest.json``
    How was the most recent fixing built? Every provider behind it, screened
    ones included, with weights, gate results, and the adjustment applied to
    each contributing quote.

Nothing here reads the database. The bundle is reproducible from a fresh
checkout with no network and no prior state, which is the same property the
benchmark itself claims.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import METHODOLOGY_VERSION
from .archive import SNAPSHOT_DIR, list_snapshots, read_snapshot, read_tape
from .estimator import DEGENERATE_RATIO_TOLERANCE, OUTLIER_SIGMAS, Estimate, estimate
from .models import NormalizedQuote
from .normalize import prepare_quotes
from .sensitivity import exposure
from .spec import (
    COMMITMENT_FACTORS,
    CONTRACTS,
    DEFAULT_GATES,
    FORM_FACTOR_FACTORS,
    INTERCONNECT_FACTORS,
    MAX_TOTAL_ADJUSTMENT,
    TIER_WEIGHTS,
    Gates,
    node_size_factor,
)

BUNDLE_FILES = ("meta.json", "series.json", "latest.json")

TIER_LABELS = {
    1: "executable",
    2: "rate card",
    3: "judgement",
}

TIER_DEFINITIONS = {
    1: "An offer a buyer can transact against right now, with observable capacity.",
    2: "A published on-demand price, capacity not confirmed at capture.",
    3: "A curated or carried-forward price standing in for an unobservable market.",
}


def _as_float(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _as_int(value: str | None) -> int:
    if value in (None, ""):
        return 0
    return int(value)


# -- meta ------------------------------------------------------------------


def build_meta(root: Path, gates: Gates) -> dict[str, Any]:
    """Publish the methodology constants the site explains."""
    snapshots = list_snapshots(root)
    tape = read_tape(root)
    dates = sorted({row["index_date"] for row in tape})

    return {
        "methodology_version": METHODOLOGY_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "disclaimer": (
            "A demonstration of what a settlement-grade compute benchmark requires. "
            "Do not settle anything against these values."
        ),
        "contracts": [
            {
                "index_code": contract.index_code,
                "display_name": contract.display_name,
                "gpu_model": contract.gpu_model,
                "description": contract.describe(),
                "form_factor": contract.form_factor.value,
                "vram_gb": contract.vram_gb,
                "node_size": contract.node_size,
                "interconnect": contract.interconnect.value,
                "commitment": contract.commitment.value,
                "region": contract.region,
                "unit": contract.unit,
            }
            for contract in CONTRACTS.values()
        ],
        "gates": {
            "min_providers": gates.min_providers,
            "min_observations": gates.min_observations,
            "max_dispersion": gates.max_dispersion,
            "max_provider_weight_share": gates.max_provider_weight_share,
            "review_move_threshold": gates.review_move_threshold,
            "require_tier1": gates.require_tier1,
        },
        "tiers": [
            {
                "tier": tier,
                "label": TIER_LABELS[tier],
                "weight": weight,
                "definition": TIER_DEFINITIONS[tier],
            }
            for tier, weight in sorted(TIER_WEIGHTS.items())
        ],
        "adjustments": {
            "max_total": MAX_TOTAL_ADJUSTMENT,
            "form_factor": {k.value: v for k, v in FORM_FACTOR_FACTORS.items()},
            "interconnect": {k.value: v for k, v in INTERCONNECT_FACTORS.items()},
            "commitment": {k.value: v for k, v in COMMITMENT_FACTORS.items()},
            "node_size": {
                str(n): node_size_factor(n, 8) for n in (1, 2, 4, 8)
            },
        },
        "screen": {
            "outlier_sigmas": OUTLIER_SIGMAS,
            "degenerate_ratio_tolerance": DEGENERATE_RATIO_TOLERANCE,
        },
        "coverage": {
            "snapshots": len(snapshots),
            "tape_rows": len(tape),
            "first_date": dates[0] if dates else None,
            "last_date": dates[-1] if dates else None,
        },
    }


# -- series ----------------------------------------------------------------


def build_series(root: Path) -> dict[str, Any]:
    """The whole tape, grouped by index, newest first.

    Superseded revisions are included rather than filtered. A site that showed
    only live values would misrepresent a corrected day as one that was always
    right, which is exactly the history a settlement dispute needs to see.
    """
    rows = read_tape(root)
    by_index: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in rows:
        by_index[row["index_code"]].append(
            {
                "index_date": row["index_date"],
                "revision": _as_int(row.get("revision")),
                "status": row["status"],
                "value": _as_float(row.get("value")),
                "provider_count": _as_int(row.get("provider_count")),
                "observation_count": _as_int(row.get("observation_count")),
                "dispersion": _as_float(row.get("dispersion")),
                "withheld_reason": row.get("withheld_reason") or None,
                "methodology_version": row.get("methodology_version"),
                "published_at": row.get("published_at"),
                "superseded_at": row.get("superseded_at") or None,
                "revision_reason": row.get("revision_reason") or None,
                "snapshot": row.get("snapshot") or None,
                "live": not row.get("superseded_at"),
            }
        )

    indices = {}
    for code in CONTRACTS:
        entries = sorted(
            by_index.get(code, []),
            key=lambda r: (r["index_date"], r["revision"]),
        )
        live = [r for r in entries if r["live"]]
        published = [r for r in live if r["status"] == "published"]
        indices[code] = {
            "index_code": code,
            "display_name": CONTRACTS[code].display_name,
            "revisions": entries,
            "live": live,
            "published_count": len(published),
            "withheld_count": len(live) - len(published),
            "latest_value": published[-1]["value"] if published else None,
            "latest_date": live[-1]["index_date"] if live else None,
        }

    dates = sorted({row["index_date"] for row in rows})
    return {"dates": dates, "indices": indices}


# -- latest fixing ---------------------------------------------------------


def _quote_row(quote: NormalizedQuote) -> dict[str, Any]:
    return {
        "source": quote.source,
        "source_sku": quote.source_sku,
        "raw": round(quote.raw_usd_per_gpu_hour, 6),
        "normalized": round(quote.normalized_usd_per_gpu_hour, 6),
        "total_adjustment": round(quote.total_adjustment, 6),
        "tier": int(quote.tier),
        "region": quote.region,
        "adjustments": [
            {"name": a.name, "factor": a.factor, "rationale": a.rationale}
            for a in quote.adjustments
        ],
    }


def _estimate_rows(est: Estimate) -> dict[str, Any]:
    contributing = est.contributing
    total_weight = sum(a.weight for a in contributing)

    return {
        "value": est.value,
        "dispersion": est.dispersion,
        "passed": est.passed,
        "failed_gates": est.failed_gate_summary,
        "gates": [
            {"name": g.name, "passed": g.passed, "detail": g.detail} for g in est.gates
        ],
        "providers": [
            {
                "provider": a.provider,
                "price": round(a.price, 6),
                "quote_count": a.quote_count,
                "tier": int(a.best_tier),
                "weight": round(a.weight, 6),
                "share": (
                    round(a.weight / total_weight, 6)
                    if total_weight > 0 and not a.screened_out
                    else None
                ),
                "screened_out": a.screened_out,
                "screen_reason": a.screen_reason,
            }
            for a in est.providers
        ],
        "flags": [
            {"severity": f.severity, "code": f.code, "detail": f.detail} for f in est.flags
        ],
    }


def _latest_snapshot_for(root: Path, tape_rows: list[dict[str, str]]) -> Path | None:
    """The snapshot the newest fixing named, falling back to the newest file.

    Preferring the tape's own provenance link matters on a day carrying two
    runs: the newest file on disk is not necessarily the one the live value
    was computed from.
    """
    named = [r.get("snapshot") for r in tape_rows if r.get("snapshot")]
    if named:
        candidate = root / SNAPSHOT_DIR / named[-1]
        if candidate.exists():
            return candidate
    snapshots = list_snapshots(root)
    return snapshots[-1] if snapshots else None


def build_latest(root: Path, gates: Gates) -> dict[str, Any]:
    """Recompute the most recent fixing from its own archived inputs."""
    tape = read_tape(root)
    if not tape:
        return {"index_date": None, "indices": {}, "run": None}

    last_date = max(row["index_date"] for row in tape)
    rows_for_date = [r for r in tape if r["index_date"] == last_date]
    snapshot_path = _latest_snapshot_for(root, rows_for_date)
    if snapshot_path is None:
        return {"index_date": last_date, "indices": {}, "run": None}

    archived = read_snapshot(snapshot_path)
    quotes, flags = prepare_quotes(archived.observations)

    by_index: dict[str, list[NormalizedQuote]] = {code: [] for code in CONTRACTS}
    for quote in quotes:
        by_index[quote.index_code].append(quote)

    published_by_code = {
        row["index_code"]: row
        for row in sorted(rows_for_date, key=lambda r: _as_int(r.get("revision")))
    }

    indices: dict[str, Any] = {}
    for code, index_quotes in by_index.items():
        est = estimate(code, index_quotes, gates)
        exp = exposure(code, index_quotes, gates)
        tape_row = published_by_code.get(code, {})

        indices[code] = {
            "index_code": code,
            "display_name": CONTRACTS[code].display_name,
            "contract": CONTRACTS[code].describe(),
            "published_value": _as_float(tape_row.get("value")),
            "status": tape_row.get("status", "withheld"),
            "withheld_reason": tape_row.get("withheld_reason") or None,
            "estimate": _estimate_rows(est),
            "quotes": [_quote_row(q) for q in sorted(index_quotes, key=lambda q: q.source)],
            "exposure": {
                "conforming_only": exp.conforming_only,
                "shift": exp.shift,
                "total_quotes": exp.total_quotes,
                "conforming_quotes": exp.conforming_quotes,
                "conforming_providers": exp.conforming_providers,
                "weight_share_adjusted": exp.weight_share_adjusted,
                "publishable_without_adjustment": exp.publishable_without_adjustment,
                "by_factor": exp.by_factor,
            },
        }

    per_source: dict[str, int] = defaultdict(int)
    for obs in archived.observations:
        per_source[obs.source] += 1

    return {
        "index_date": last_date,
        "snapshot": snapshot_path.name,
        "captured_at": archived.captured_at.isoformat(),
        "indices": indices,
        "run": {
            "raw_observations": len(archived.observations),
            "normalized_quotes": len(quotes),
            "sources": dict(sorted(per_source.items())),
            "flags": [
                {"severity": f.severity, "code": f.code, "detail": f.detail} for f in flags
            ],
        },
    }


# -- bundle ----------------------------------------------------------------


def build_bundle(root: Path, gates: Gates | None = None) -> dict[str, Any]:
    gates = gates or DEFAULT_GATES
    return {
        "meta.json": build_meta(root, gates),
        "series.json": build_series(root),
        "latest.json": build_latest(root, gates),
    }


def write_bundle(root: Path, out_dir: Path, gates: Gates | None = None) -> list[Path]:
    """Write the bundle, creating ``out_dir`` if needed."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, payload in build_bundle(root, gates).items():
        path = out_dir / name
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8"
        )
        written.append(path)
    return written
