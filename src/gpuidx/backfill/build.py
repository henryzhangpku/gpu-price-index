"""Collect archived rate cards, and rebuild the backfill series from the cache.

Two entry points, kept apart on purpose:

``collect``
    The only network step. Lists each source's captures, selects one per week
    (or month), and fetches what is not already cached. Safe to re-run: the
    cached listing and cached bodies are reused, so a second run makes no
    requests at all.

``build``
    Pure function of the cache. Parses every selected snapshot, restates it,
    and writes the series. ``gpuidx backfill`` without ``--collect`` runs only
    this, which is what the reproducibility test pins: rebuilding from the
    committed cache must give the committed CSVs byte for byte.

Output, all labelled "reconstructed from archived public rate cards; list
prices, not transactions; not the index":

``series/backfill_ratecards.csv``         monthly
``series/backfill_ratecards_weekly.csv``  weekly, ISO weeks named by Monday
``backfill/observations.csv``             every parsed row and what became of it
``backfill/manifest.csv``                 every snapshot: URL, hash, rows parsed

None of it is ever written into ``series/index_values.csv``.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .aggregate import (
    LABEL,
    SERIES_COLUMNS,
    build_series,
    month_of,
    month_range,
    week_of,
    week_range,
)
from .records import RateCardRow
from .restate import BACKFILL_METHODOLOGY_VERSION, restate
from .sources import SOURCES, Source
from .wayback import (
    BACKFILL_DIR,
    Capture,
    Fetcher,
    cdx_path,
    fetch_snapshot,
    list_captures,
    parse_cdx,
    read_cached,
    snapshot_path,
    usable,
)

START_MONTH = "2023-01"
END_MONTH = "2026-09"

MONTHLY_NAME = "backfill_ratecards.csv"
WEEKLY_NAME = "backfill_ratecards_weekly.csv"
OBSERVATIONS_NAME = "observations.csv"
MANIFEST_NAME = "manifest.csv"

MANIFEST_COLUMNS = [
    "source",
    "captured",
    "original",
    "snapshot_url",
    "cdx_digest",
    "sha256",
    "bytes",
    "cache_path",
    "rows_parsed",
    "rows_in_scope",
]

OBSERVATION_COLUMNS = [
    "source",
    "captured",
    "month",
    "week",
    "sku",
    "gpu_model",
    "gpu_count",
    "form_factor",
    "interconnect",
    "vram_gb",
    "commitment",
    "price_per_instance_hour",
    "price_per_gpu_hour",
    "currency",
    "price_kind",
    "basis",
    "note",
    "index_code",
    "outcome",
    "restated_per_gpu_hour",
    "adjustments",
    "discard_code",
    "discard_detail",
]


@dataclass
class BuildReport:
    snapshots: int = 0
    rows: int = 0
    restated: int = 0
    discarded: int = 0
    missing: list[str] = field(default_factory=list)


# -- selection -------------------------------------------------------------


def in_window(c: Capture) -> bool:
    return START_MONTH.replace("-", "") <= c.timestamp[:6] <= END_MONTH.replace("-", "")


def load_listing(root: Path, source: Source) -> list[Capture] | None:
    path = cdx_path(root, source.name)
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    captures: list[Capture] = []
    for rows in payload["listings"].values():
        captures.extend(usable(parse_cdx(rows)))
    return sorted(captures, key=lambda c: c.timestamp)


def select(captures: list[Capture], source: Source) -> list[list[Capture]]:
    """Candidate captures per period, best first.

    One snapshot per ISO week, falling back to later captures in the same
    week if the first cannot be fetched. ``source.cadence == "monthly"`` thins
    to one per month for pages archived so often that weekly would be
    hundreds of near-identical files.
    """
    key = month_of if source.cadence == "monthly" else week_of
    groups: dict[str, list[Capture]] = {}
    for c in captures:
        if in_window(c) and source.accepts(c):
            groups.setdefault(key(c.timestamp), []).append(c)
    return [groups[k][:3] for k in sorted(groups)]


# -- network ---------------------------------------------------------------


def collect(root: Path, sources: list[Source] | None = None, log=print) -> None:
    fetcher = Fetcher()
    for source in sources or SOURCES:
        path = cdx_path(root, source.name)
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
        else:
            payload = {"source": source.name, "listings": {}}
        for url in source.urls:
            if url in payload["listings"]:
                continue
            rows = list_captures(fetcher, url)
            if rows is None:
                log(f"{source.name}: listing failed for {url}; will retry next run")
                continue
            payload["listings"][url] = rows
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, indent=0, sort_keys=True), encoding="utf-8")
        captures = load_listing(root, source) or []
        fetched = 0
        for candidates in select(captures, source):
            if any(snapshot_path(root, source.name, c).exists() for c in candidates):
                continue
            for c in candidates:
                if fetch_snapshot(fetcher, root, source.name, c) is not None:
                    fetched += 1
                    break
        log(f"{source.name}: {len(captures)} usable captures, fetched {fetched} new snapshots")


# -- offline build ---------------------------------------------------------


def _chosen(root: Path, source: Source) -> list[Capture]:
    """The one cached snapshot per period that the build reads."""
    captures = load_listing(root, source)
    if not captures:
        return []
    out = []
    for candidates in select(captures, source):
        for c in candidates:
            if snapshot_path(root, source.name, c).exists():
                out.append(c)
                break
    return out


def parse_all(root: Path, sources: list[Source] | None = None):
    rows: list[RateCardRow] = []
    manifest: list[dict] = []
    import hashlib

    for source in sources or SOURCES:
        for c in _chosen(root, source):
            path = snapshot_path(root, source.name, c)
            body = read_cached(path)
            # A page that repeats a table (desktop and mobile copies) states
            # each price once; identical rows within a snapshot collapse.
            parsed = list(dict.fromkeys(source.parse(body, c.timestamp)))
            in_scope = [r for r in parsed if r.gpu_model]
            rows.extend(parsed)
            manifest.append(
                {
                    "source": source.name,
                    "captured": c.timestamp,
                    "original": c.original,
                    "snapshot_url": c.snapshot_url,
                    "cdx_digest": c.digest,
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "bytes": len(body),
                    "cache_path": path.relative_to(root).as_posix(),
                    "rows_parsed": len(parsed),
                    "rows_in_scope": len(in_scope),
                }
            )
    return rows, manifest


def _csv_text(columns: list[str], rows: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def build_outputs(root: Path, sources: list[Source] | None = None) -> dict[str, str]:
    """Every backfill output as text, keyed by repo-relative path."""
    rows, manifest = parse_all(root, sources)
    kept, dropped = restate(rows)

    end = date(int(END_MONTH[:4]), int(END_MONTH[5:7]), 28)
    months = month_range(START_MONTH, END_MONTH)
    weeks = week_range(date(int(START_MONTH[:4]), int(START_MONTH[5:7]), 1), end)
    monthly = build_series(kept, dropped, months, month_of, BACKFILL_METHODOLOGY_VERSION)
    weekly = build_series(kept, dropped, weeks, week_of, BACKFILL_METHODOLOGY_VERSION)

    obs_rows: list[dict] = []
    for r in kept:
        d = r.row.as_dict()
        d.update(
            month=r.row.month,
            week=week_of(r.row.captured),
            index_code=r.index_code,
            outcome="restated",
            restated_per_gpu_hour=f"{r.quote.normalized_usd_per_gpu_hour:.6f}",
            adjustments=";".join(f"{a.name}x{a.factor:g}" for a in r.quote.adjustments),
        )
        obs_rows.append(d)
    for x in dropped:
        d = x.row.as_dict()
        d.update(
            month=x.row.month,
            week=week_of(x.row.captured),
            index_code=x.index_code or "",
            outcome="discarded",
            discard_code=x.code,
            discard_detail=x.detail,
        )
        obs_rows.append(d)
    obs_rows.sort(key=lambda d: (d["source"], d["captured"], d["sku"], d["commitment"], d["price_per_instance_hour"]))

    return {
        f"series/{MONTHLY_NAME}": _csv_text(SERIES_COLUMNS, monthly),
        f"series/{WEEKLY_NAME}": _csv_text(SERIES_COLUMNS, weekly),
        f"{BACKFILL_DIR}/{OBSERVATIONS_NAME}": _csv_text(OBSERVATION_COLUMNS, obs_rows),
        f"{BACKFILL_DIR}/{MANIFEST_NAME}": _csv_text(MANIFEST_COLUMNS, manifest),
    }


def build(root: Path, sources: list[Source] | None = None) -> BuildReport:
    outputs = build_outputs(root, sources)
    for rel, text in outputs.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    obs = list(csv.DictReader(io.StringIO(outputs[f"{BACKFILL_DIR}/{OBSERVATIONS_NAME}"])))
    manifest = list(csv.DictReader(io.StringIO(outputs[f"{BACKFILL_DIR}/{MANIFEST_NAME}"])))
    return BuildReport(
        snapshots=len(manifest),
        rows=len(obs),
        restated=sum(1 for o in obs if o["outcome"] == "restated"),
        discarded=sum(1 for o in obs if o["outcome"] == "discarded"),
    )


__all__ = ["LABEL", "build", "build_outputs", "collect"]
