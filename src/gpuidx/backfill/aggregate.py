"""Monthly and weekly series from restated archived rate cards.

The estimator is deliberately simpler than the live index's. Every input here
is the same kind of evidence -- a published list price, read from an archive
-- so there is no tier waterfall to weight by, and the panels are thin enough
(three to eight providers) that a weighted mean would be one provider's
opinion on the day another drops out. Per period and per index:

1. **One vote per provider.** A provider's vote is the median of its restated
   on-demand quotes across every archived snapshot of its page in the period.
   A page listing 1x, 2x, 4x and 8x of the same card gets one vote, as in the
   live index.
2. **The value is the median of the votes.** Unweighted. No outlier screen:
   the median is its own screen at these counts, and a MAD screen over three
   points removes signal (the live index suppresses it below four for the
   same reason).
3. **Withheld below three providers.** A median of two rate cards is their
   midpoint, and of one is that card. The row is still written, with its
   counts and the reason, because a gap in the series is a fact about the
   archive and must not read as missing data.

Dispersion is the live index's robust coefficient of variation across the
provider votes, reported and flagged above the live ceiling, never gated: on
three to five points it is too coarse an order statistic to refuse on.

Because the panel changes from period to period with what the archive
happened to capture, the level moves when a provider enters or leaves even if
no price moved. ``matched_log_change`` is the composition-robust alternative:
the median, over providers voting in both this period and the one before, of
the log change in each one's own vote, published only when at least three
providers match. It is a different estimator of the same thing, not a fill:
a period with no matched panel gets no change.

Non-on-demand prices (reserved, spot, community) never enter the value. Their
raw per-GPU medians are carried in side columns so a reader can see them, and
so nobody is tempted to restate them into the headline.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from ..estimator import MAD_TO_SIGMA
from ..models import Commitment
from .records import BACKFILL_INDICES
from .restate import Discarded, Restated

MIN_PROVIDERS = 3
#: The live dispersion ceiling, used here as a flag only.
DISPERSION_FLAG = 0.45

LABEL = (
    "reconstructed from archived public rate cards; list prices, not transactions; "
    "not the index"
)

SERIES_COLUMNS = [
    "period",
    "index_code",
    "status",
    "value",
    "provider_count",
    "observation_count",
    "snapshot_count",
    "dispersion",
    "min_provider_value",
    "max_provider_value",
    "providers",
    "withheld_reason",
    "flags",
    "raw_on_demand_median",
    "reserved_raw_median",
    "reserved_providers",
    "spot_raw_median",
    "spot_providers",
    "matched_providers",
    "matched_log_change",
    "methodology_version",
    "label",
]


def month_of(captured: str) -> str:
    return f"{captured[:4]}-{captured[4:6]}"


def week_of(captured: str) -> str:
    """ISO week, named by its Monday, so the label sorts and reads as a date."""
    d = date(int(captured[:4]), int(captured[4:6]), int(captured[6:8]))
    iso = d.isocalendar()
    monday = date.fromisocalendar(iso.year, iso.week, 1)
    return monday.isoformat()


def robust_dispersion(values: list[float]) -> float | None:
    if len(values) < 3:
        return None
    med = statistics.median(values)
    if med <= 0:
        return None
    mad = statistics.median([abs(v - med) for v in values])
    return mad * MAD_TO_SIGMA / med


@dataclass
class _Cell:
    od: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    od_raw: list[float] = field(default_factory=list)
    reserved: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    spot: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    snapshots: set[tuple[str, str]] = field(default_factory=set)


def _fmt(x: float | None, digits: int = 4) -> str:
    return "" if x is None else f"{x:.{digits}f}"


def _median_of_provider_medians(book: dict[str, list[float]]) -> tuple[float | None, int]:
    if not book:
        return None, 0
    votes = [statistics.median(v) for v in book.values()]
    return statistics.median(votes), len(votes)


def build_series(
    restated: list[Restated],
    discarded: list[Discarded],
    periods: list[str],
    period_of=month_of,
    methodology_version: str = "",
) -> list[dict]:
    """One row per (period, index), every period in ``periods`` present.

    A period with no observations at all is written as withheld with zero
    counts, so the series has the same shape whatever the archive happened to
    capture, and a reader joining on period sees the gap.
    """
    cells: dict[tuple[str, str], _Cell] = defaultdict(_Cell)
    for r in restated:
        cell = cells[(period_of(r.row.captured), r.index_code)]
        cell.od[r.row.source].append(r.quote.normalized_usd_per_gpu_hour)
        cell.od_raw.append(r.row.price_per_gpu_hour)
        cell.snapshots.add((r.row.source, r.row.captured))
    for d in discarded:
        if d.code != "not_on_demand" or d.index_code not in BACKFILL_INDICES:
            continue
        cell = cells[(period_of(d.row.captured), d.index_code)]
        side = cell.spot if d.row.commitment in (Commitment.SPOT, Commitment.COMMUNITY) else cell.reserved
        side[d.row.source].append(d.row.price_per_gpu_hour)

    rows: list[dict] = []
    previous: dict[str, dict[str, float]] = {}
    for period in periods:
        for code in BACKFILL_INDICES:
            cell = cells.get((period, code), _Cell())
            votes = {p: statistics.median(v) for p, v in sorted(cell.od.items())}
            before = previous.get(code, {})
            common = sorted(set(votes) & set(before))
            matched = (
                statistics.median(math.log(votes[p] / before[p]) for p in common)
                if len(common) >= MIN_PROVIDERS
                else None
            )
            previous[code] = votes
            n = len(votes)
            value = statistics.median(votes.values()) if votes else None
            disp = robust_dispersion(list(votes.values()))
            flags = []
            if disp is not None and disp > DISPERSION_FLAG:
                flags.append(f"dispersion {disp:.3f} above the live ceiling {DISPERSION_FLAG}")
            status = "published" if n >= MIN_PROVIDERS else "withheld"
            reason = "" if n >= MIN_PROVIDERS else f"min_providers: {n} of {MIN_PROVIDERS} required"
            res_med, res_n = _median_of_provider_medians(cell.reserved)
            spot_med, spot_n = _median_of_provider_medians(cell.spot)
            rows.append(
                {
                    "period": period,
                    "index_code": code,
                    "status": status,
                    "value": _fmt(value) if status == "published" else "",
                    "provider_count": n,
                    "observation_count": sum(len(v) for v in cell.od.values()),
                    "snapshot_count": len(cell.snapshots),
                    "dispersion": _fmt(disp),
                    "min_provider_value": _fmt(min(votes.values())) if votes else "",
                    "max_provider_value": _fmt(max(votes.values())) if votes else "",
                    "providers": ";".join(f"{p}={v:.3f}" for p, v in votes.items()),
                    "withheld_reason": reason,
                    "flags": "; ".join(flags),
                    "raw_on_demand_median": _fmt(statistics.median(cell.od_raw)) if cell.od_raw else "",
                    "reserved_raw_median": _fmt(res_med),
                    "reserved_providers": res_n,
                    "spot_raw_median": _fmt(spot_med),
                    "spot_providers": spot_n,
                    "matched_providers": len(common),
                    "matched_log_change": _fmt(matched, 6),
                    "methodology_version": methodology_version,
                    "label": LABEL,
                }
            )
    return rows


def month_range(start: str, end: str) -> list[str]:
    """Inclusive list of YYYY-MM between two YYYY-MM strings."""
    y, m = int(start[:4]), int(start[5:7])
    ey, em = int(end[:4]), int(end[5:7])
    out = []
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def week_range(start: date, end: date) -> list[str]:
    """Every ISO-week Monday from the week containing ``start`` to ``end``."""
    from datetime import timedelta

    iso = start.isocalendar()
    d = date.fromisocalendar(iso.year, iso.week, 1)
    out = []
    while d <= end:
        out.append(d.isoformat())
        d += timedelta(days=7)
    return out
