"""How the reconstructed back-series compares with the live index where both exist.

The live tape starts on 27 August 2026; the backfill reads archive captures
through September 2026, so the two overlap for about five weeks. This module
measures the gap and does nothing with it. A spliced series -- backfill
levels rescaled to meet the live index -- is deliberately not produced: the
two measure different things (a median of list prices restated per provider,
against a tier-weighted mean of executable offers and rate cards through
aggregators), and a scale factor fitted on five weeks would be presented as a
constant when the test below is whether it is one.

Two comparisons:

* **Level.** For each overlap period, the backfill value against the mean of
  the live index's published fixings in that period, as a ratio.
* **Like for like.** For each provider that appears in both -- Lambda on its
  own archived page and as ``shadeform:lambdalabs`` in the live feed -- its
  backfill vote against its live provider median over the same days. This is
  the check on the parsers: the same seller, read two ways, should agree,
  and where it does not the difference is a fact about one of the two reads.
"""

from __future__ import annotations

import csv
import io
import statistics
from collections import defaultdict
from datetime import date
from pathlib import Path

from ..archive import live_tape_values, read_tape
from ..reproduce import estimate_from_archive
from .aggregate import week_of
from .records import BACKFILL_INDICES

LIVE_START = date(2026, 8, 27)

#: Backfill source -> the provider name the live estimator gives the same seller.
LIVE_NAMES: dict[str, tuple[str, ...]] = {
    "lambda": ("shadeform:lambdalabs", "lambdalabs"),
    "runpod": ("runpod",),
    "datacrunch": ("datacrunch",),
    "hyperstack": ("shadeform:hyperstack",),
    "digitalocean": ("shadeform:digitalocean",),
    "voltagepark": ("shadeform:voltagepark",),
    "paperspace": ("shadeform:paperspace",),
    "crusoe": ("shadeform:crusoe",),
    "nebius": ("shadeform:nebius",),
    "coreweave": ("shadeform:coreweave",),
    "fluidstack": ("shadeform:fluidstack",),
}

LEVEL_COLUMNS = [
    "granularity",
    "period",
    "index_code",
    "backfill_value",
    "backfill_providers",
    "live_mean",
    "live_days",
    "ratio_live_to_backfill",
]
PROVIDER_COLUMNS = [
    "period",
    "index_code",
    "backfill_source",
    "live_provider",
    "backfill_vote",
    "live_median",
    "live_days",
    "ratio_live_to_backfill",
]


def _period(d: date, granularity: str) -> str:
    return f"{d.year:04d}-{d.month:02d}" if granularity == "month" else week_of(d.strftime("%Y%m%d"))


def live_fixings(root: Path) -> dict[tuple[str, str], float]:
    out = {}
    for (code, day), row in live_tape_values(root).items():
        if row["status"] == "published" and row["value"]:
            out[(code, day)] = float(row["value"])
    return out


def live_provider_medians(root: Path) -> dict[tuple[str, str, str], float]:
    """(index, day, provider) -> that provider's restated median on the day."""
    out: dict[tuple[str, str, str], float] = {}
    days = sorted({(r["index_code"], r["index_date"]) for r in read_tape(root)})
    for code, day in days:
        if code not in BACKFILL_INDICES:
            continue
        est, _ = estimate_from_archive(root, code, day)
        if est is None:
            continue
        for p in est.providers:
            out[(code, day, p.provider)] = p.price
    return out


def _read_csv(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text)))


def splice_tables(
    root: Path, monthly_csv: str, weekly_csv: str, observations_csv: str
) -> tuple[list[dict], list[dict]]:
    fixings = live_fixings(root)
    level: list[dict] = []
    for granularity, text in (("month", monthly_csv), ("week", weekly_csv)):
        live_by: dict[tuple[str, str], list[float]] = defaultdict(list)
        for (code, day), v in fixings.items():
            d = date.fromisoformat(day)
            if d >= LIVE_START:
                live_by[(_period(d, granularity), code)].append(v)
        for row in _read_csv(text):
            key = (row["period"], row["index_code"])
            if key not in live_by:
                continue
            live = live_by[key]
            bf = float(row["value"]) if row["value"] else None
            mean = statistics.fmean(live)
            level.append(
                {
                    "granularity": granularity,
                    "period": row["period"],
                    "index_code": row["index_code"],
                    "backfill_value": "" if bf is None else f"{bf:.4f}",
                    "backfill_providers": row["provider_count"],
                    "live_mean": f"{mean:.4f}",
                    "live_days": len(live),
                    "ratio_live_to_backfill": "" if bf is None else f"{mean / bf:.4f}",
                }
            )

    providers = live_provider_medians(root)
    votes: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for o in _read_csv(observations_csv):
        if o["outcome"] != "restated":
            continue
        d = date(int(o["captured"][:4]), int(o["captured"][4:6]), int(o["captured"][6:8]))
        if d < LIVE_START:
            continue
        votes[(o["month"], o["index_code"], o["source"])].append(float(o["restated_per_gpu_hour"]))

    by_provider: list[dict] = []
    for (month, code, source), vals in sorted(votes.items()):
        vote = statistics.median(vals)
        for live_name in LIVE_NAMES.get(source, ()):
            live = [
                v
                for (c, day, p), v in providers.items()
                if c == code and p == live_name and day[:7] == month and date.fromisoformat(day) >= LIVE_START
            ]
            if not live:
                continue
            med = statistics.median(live)
            by_provider.append(
                {
                    "period": month,
                    "index_code": code,
                    "backfill_source": source,
                    "live_provider": live_name,
                    "backfill_vote": f"{vote:.4f}",
                    "live_median": f"{med:.4f}",
                    "live_days": len(live),
                    "ratio_live_to_backfill": f"{med / vote:.4f}",
                }
            )
    return level, by_provider


def as_csv(columns: list[str], rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()
