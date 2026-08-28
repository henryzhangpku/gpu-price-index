"""Term structure for a good that cannot be stored.

A GPU-hour is not storable. You cannot buy one today, hold it, and deliver it
in six months; an unused hour is gone. That single fact removes the machinery
most commodity forward pricing rests on.

For a storable good, the forward is pinned by arbitrage:

    F(T) = S * exp((r + u - y) * T)

Buy spot, finance it, store it, deliver later. Any deviation is a trade. For a
GPU-hour there is no such trade, so no-arbitrage says nothing, and the forward
is whatever the market expects plus whatever it charges for bearing the risk:

    F(T) = E[S(T)] + risk premium

This puts compute in the family of electricity, weather, and shipping rather
than the family of gold and crude. Two practical consequences:

* A forward curve here cannot be bootstrapped from spot. It has to be
  observed, and there is currently nothing liquid to observe.
* Term structure is driven by supply-side physics -- fab and HBM capacity,
  datacentre power, and above all the next-generation release cadence -- not
  by carry. A B200 ramp mechanically depresses the H100 forward curve in a way
  no storage-cost model would predict.

What *is* observable is the committed-use discount: a venue will sell a
one-year or three-year commitment below its on-demand rate. That discount is a
real price, but it bundles at least four things:

1. expected decline in spot over the term,
2. the buyer's premium for price certainty,
3. the vendor's value from guaranteed utilisation,
4. the buyer's cost of losing optionality (lock-in).

**These are not separately identified from one observation.** A single discount
is one equation in several unknowns. This module does not pretend otherwise:
it inverts the discount for expected decline *conditional on an assumed risk
premium*, and its main output is the sensitivity of that answer to the
assumption. The width of that range is the honest headline.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path

COMMITTED_USE_PATH = Path(__file__).resolve().parents[2] / "data" / "committed_use.json"

#: Newton iterations are plenty for a smooth monotone function in one unknown.
_MAX_ITER = 200
_TOL = 1e-10


@dataclass
class TermPoint:
    """One observed commitment: a tenor and its price relative to on-demand."""

    vendor: str
    tenor_years: float
    #: Committed rate divided by the same vendor's on-demand rate. 0.60 means
    #: the commitment costs 60% of on-demand, i.e. a 40% discount.
    price_ratio: float
    source_url: str | None = None
    as_of: date | None = None
    note: str | None = None

    @property
    def discount(self) -> float:
        return 1.0 - self.price_ratio


def average_price_factor(decline_rate: float, tenor_years: float) -> float:
    """Time-average of spot over [0, T] when spot decays at a constant rate.

    With S(t) = S0 * exp(-d*t), the average paid by someone buying on demand
    throughout the term is S0 * (1 - exp(-d*T)) / (d*T). At d = 0 this is 1.0;
    the limit is taken explicitly to avoid a singularity at the origin.
    """
    if tenor_years <= 0:
        raise ValueError("tenor must be positive")
    x = decline_rate * tenor_years
    if abs(x) < 1e-9:
        # Series expansion; also the correct value at exactly zero decline.
        return 1.0 - x / 2.0
    return (1.0 - math.exp(-x)) / x


def implied_decline(price_ratio: float, tenor_years: float, risk_premium: float = 0.0) -> float:
    """Annualised expected spot decline implied by a committed-use discount.

    ``risk_premium`` is the share of the observed discount attributed to
    something other than expectations -- lock-in, illiquidity, the vendor's
    utilisation guarantee. Setting it to zero attributes the entire discount to
    expected price decline, which is the most aggressive reading available and
    should be treated as an upper bound rather than an estimate.

    Returns the continuously-compounded annual rate. A return of 0.30 means
    spot is expected to fall about 26% per year (1 - exp(-0.30)).
    """
    if not 0.0 < price_ratio <= 1.0:
        raise ValueError("price_ratio must lie in (0, 1]")
    if not 0.0 <= risk_premium < 1.0:
        raise ValueError("risk_premium must lie in [0, 1)")

    # The portion of the discount attributable to expectations alone.
    target = price_ratio / (1.0 - risk_premium)
    if target >= 1.0:
        # The premium explains the whole discount; no decline is implied.
        return 0.0

    # average_price_factor is strictly decreasing in the decline rate, so
    # bisection is both safe and sufficient.
    lo, hi = 0.0, 5.0
    if average_price_factor(hi, tenor_years) > target:
        # Even a 5/yr decay cannot produce a discount this deep.
        return math.inf

    for _ in range(_MAX_ITER):
        mid = (lo + hi) / 2.0
        if average_price_factor(mid, tenor_years) > target:
            lo = mid
        else:
            hi = mid
        if hi - lo < _TOL:
            break
    return (lo + hi) / 2.0


def annual_decline_pct(rate: float) -> float:
    """Convert a continuous rate to the plain-language annual percentage fall."""
    if math.isinf(rate):
        return 1.0
    return 1.0 - math.exp(-rate)


def premium_sensitivity(
    point: TermPoint, risk_premiums: list[float] | None = None
) -> list[dict]:
    """How much the implied decline moves with the unidentified assumption."""
    risk_premiums = risk_premiums if risk_premiums is not None else [0.0, 0.05, 0.10, 0.15, 0.20]
    rows = []
    for premium in risk_premiums:
        rate = implied_decline(point.price_ratio, point.tenor_years, premium)
        rows.append(
            {
                "risk_premium": premium,
                "decline_rate": rate,
                "annual_decline": annual_decline_pct(rate),
            }
        )
    return rows


def implied_forward_level(spot: float, decline_rate: float, horizon_years: float) -> float:
    """Project a spot level forward under a constant decline.

    This is an *expectation*, not a tradeable forward. Naming it a forward
    price would imply a no-arbitrage relation that does not exist for a
    non-storable good.
    """
    return spot * math.exp(-decline_rate * horizon_years)


def load_committed_use(path: Path | None = None) -> list[TermPoint]:
    path = path or COMMITTED_USE_PATH
    if not path.exists():
        return []
    entries = json.loads(path.read_text(encoding="utf-8"))
    return [
        TermPoint(
            vendor=entry["vendor"],
            tenor_years=float(entry["tenor_years"]),
            price_ratio=float(entry["price_ratio"]),
            source_url=entry.get("source_url"),
            as_of=date.fromisoformat(entry["as_of"]) if entry.get("as_of") else None,
            note=entry.get("note"),
        )
        for entry in entries
    ]


def consistency_check(points: list[TermPoint], risk_premium: float = 0.10) -> list[dict]:
    """Are a vendor's one-year and three-year discounts telling the same story?

    Under a constant decline rate, one tenor determines the other. Where a
    vendor publishes both, the two implied rates should agree; a large gap
    means the constant-decline assumption is wrong, the discounts encode
    something tenor-dependent (lock-in grows with term, which it plainly
    does), or the headline numbers are marketing rather than prices.
    """
    by_vendor: dict[str, list[TermPoint]] = {}
    for point in points:
        by_vendor.setdefault(point.vendor, []).append(point)

    rows = []
    for vendor, group in sorted(by_vendor.items()):
        if len(group) < 2:
            continue
        implied = [
            (p.tenor_years, implied_decline(p.price_ratio, p.tenor_years, risk_premium))
            for p in sorted(group, key=lambda p: p.tenor_years)
        ]
        rates = [r for _, r in implied if not math.isinf(r)]
        spread = (max(rates) - min(rates)) if len(rates) > 1 else 0.0
        rows.append(
            {
                "vendor": vendor,
                "tenors": [t for t, _ in implied],
                "implied": [r for _, r in implied],
                "spread": spread,
                "consistent": spread < 0.10,
            }
        )
    return rows
