"""What the market expects, read off a ladder of prediction-market brackets.

`forward.py` argues that a GPU-hour is not storable, so no forward can be
bootstrapped from spot, and ends with the observation that a forward curve
"has to be observed, and there is currently nothing liquid to observe."

There is now something to observe. It is not liquid, but it is real, and this
module reads it.

HOW IT DIFFERS FROM forward.py
------------------------------
`forward.py` inverts a committed-use discount for an implied expected decline,
and cannot separate expectation from the price of lock-in. One observation,
several unknowns.

A prediction market on the settlement value sells **mutually exclusive
brackets**. The quotes across a ladder are therefore a probability mass
function over the outcome directly: no bootstrap, no decomposition, nothing
assumed about who the marginal hedger is. For a non-storable good, where
F(T) = E[S(T)] + risk premium, reading the distribution is the only
construction that identifies the expectation without an assumption.

WHY IT IS STILL NOT A PRICE
---------------------------
Three things degrade it, and all three are measurable, so this module measures
them rather than burying them:

1. **The book does not sum to one.** Mutually exclusive, exhaustive brackets
   should. The gap is spread plus stale quotes, and it is a direct read on how
   seriously to take the level.
2. **The tails are open.** "<$2.00" and "$3.25+" have no midpoint until one is
   assumed, and if the tails carry real mass the assumption drives the answer.
   `expectation_sensitivity` shows how much.
3. **The volume is small.** Tens of thousands of dollars, not millions.

So the honest output is an expectation, a dispersion, and a list of reasons to
distrust both. Where the reasons are severe enough, `assess` withholds the
level entirely — the same rule the index itself follows when a fixing is not
supportable.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date

import httpx

GAMMA = "https://gamma-api.polymarket.com/events"

#: The H100 ladders worth reading, nearest first. Slugs are stable once an
#: event is created; a missing one means the month has settled and rolled, and
#: the CLI reports that rather than failing.
DEFAULT_LADDERS: tuple[tuple[str, str], ...] = (
    ("end Sep 2026", "gpu-rental-prices-h100-end-of-september-1785423806291"),
    ("end Oct 2026", "gpu-rental-prices-h100-end-of-october"),
    ("end 2026", "gpu-rental-prices-h100-end-of-2026-20260709164334623"),
)

#: Brackets are quoted in quarter-dollar steps; an open tail is assumed to
#: extend by this much beyond its stated edge. Deliberately a parameter: see
#: expectation_sensitivity for what the choice is worth.
DEFAULT_OPEN_SPAN = 0.25

#: A ladder whose quotes miss unity by more than this is quoting spread, not a
#: distribution.
MAX_BOOK_ERROR = 0.10

#: Below this, the midpoint assumed for the open tails is doing too much of the
#: work for the expectation to mean anything.
MAX_TAIL_MASS = 0.25

#: Dollars of traded volume across the ladder, below which the level is noise.
MIN_VOLUME = 5_000.0


@dataclass(frozen=True)
class Bracket:
    """One mutually exclusive outcome range and its quoted probability."""

    label: str
    low: float | None  # None: open below
    high: float | None  # None: open above
    quoted: float
    volume: float = 0.0
    liquidity: float = 0.0

    @property
    def is_tail(self) -> bool:
        return self.low is None or self.high is None

    def midpoint(self, open_span: float = DEFAULT_OPEN_SPAN) -> float:
        if self.low is None and self.high is None:
            raise ValueError(f"bracket {self.label!r} is unbounded on both sides")
        if self.low is None:
            return self.high - open_span / 2.0
        if self.high is None:
            return self.low + open_span / 2.0
        return (self.low + self.high) / 2.0


def parse_bracket(label: str) -> tuple[float | None, float | None]:
    """Turn a quoted bracket label into its bounds.

    '<$2.00' -> (None, 2.00);  '$2.00-$2.25' -> (2.00, 2.25);  '$3.25+' -> (3.25, None)
    """
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", label)]
    if not nums:
        raise ValueError(f"no bound in bracket label {label!r}")
    text = label.strip()
    if text.startswith("<") or text.lower().startswith("under"):
        return None, nums[0]
    if text.endswith("+") or text.startswith(">") or text.lower().startswith("over"):
        return nums[0], None
    if len(nums) >= 2:
        return nums[0], nums[1]
    # A single number with no comparator is ambiguous; treat it as an upper edge
    # rather than guessing a width.
    return None, nums[0]


@dataclass
class ImpliedDistribution:
    """A ladder of brackets for one settlement date, and what it implies."""

    tenor: str
    settles: date | None
    source_index: str
    brackets: list[Bracket] = field(default_factory=list)

    # ---- quality, measured before anything is reported ------------------
    @property
    def book_sum(self) -> float:
        return sum(b.quoted for b in self.brackets)

    @property
    def book_error(self) -> float:
        return abs(self.book_sum - 1.0)

    @property
    def volume(self) -> float:
        return sum(b.volume for b in self.brackets)

    @property
    def tail_mass(self) -> float:
        """Share of normalised probability sitting in the open-ended brackets."""
        total = self.book_sum
        if total <= 0:
            return 1.0
        return sum(b.quoted for b in self.brackets if b.is_tail) / total

    def refusals(self) -> list[str]:
        """Why this ladder should not be quoted as a level. Empty means usable."""
        out: list[str] = []
        if not self.brackets:
            out.append("no open brackets")
            return out
        if self.book_error > MAX_BOOK_ERROR:
            out.append(
                f"book sums to {self.book_sum:.3f}; a mutually exclusive, exhaustive "
                f"ladder should sum to 1.00, so {self.book_error:.0%} of this is spread"
            )
        if self.tail_mass > MAX_TAIL_MASS:
            out.append(
                f"{self.tail_mass:.0%} of the mass is in open-ended tails, so the "
                f"assumed tail midpoint drives the expectation"
            )
        if self.volume < MIN_VOLUME:
            out.append(f"only ${self.volume:,.0f} of volume across the ladder")
        return out

    # ---- the numbers ----------------------------------------------------
    def probabilities(self) -> list[float]:
        """Quotes renormalised to sum to one."""
        total = self.book_sum
        if total <= 0:
            return [0.0] * len(self.brackets)
        return [b.quoted / total for b in self.brackets]

    def expectation(self, open_span: float = DEFAULT_OPEN_SPAN) -> float:
        return sum(
            b.midpoint(open_span) * p for b, p in zip(self.brackets, self.probabilities())
        )

    def dispersion(self, open_span: float = DEFAULT_OPEN_SPAN) -> float:
        """Implied standard deviation. Usually the more informative number."""
        mean = self.expectation(open_span)
        var = sum(
            (b.midpoint(open_span) - mean) ** 2 * p
            for b, p in zip(self.brackets, self.probabilities())
        )
        return math.sqrt(max(0.0, var))

    def assess(self, open_span: float = DEFAULT_OPEN_SPAN) -> dict:
        """Everything, including whether to believe the level.

        Mirrors the index's own rule: when the evidence does not support a
        number, say so and withhold it rather than publishing something
        defensible only by omission.
        """
        refusals = self.refusals()
        usable = not refusals
        return {
            "tenor": self.tenor,
            "settles": self.settles,
            "source_index": self.source_index,
            "expected": self.expectation(open_span) if self.brackets else None,
            "dispersion": self.dispersion(open_span) if self.brackets else None,
            "book_sum": self.book_sum,
            "tail_mass": self.tail_mass if self.brackets else None,
            "volume": self.volume,
            "withheld": not usable,
            "refusals": refusals,
        }


def expectation_sensitivity(
    dist: ImpliedDistribution, spans: tuple[float, ...] = (0.25, 0.50, 1.00)
) -> list[dict]:
    """How much the answer depends on the assumed width of the open tails.

    The same discipline as forward.py's premium sensitivity: where an
    unobservable assumption is required, publish its consequence rather than
    one number chosen from a range.
    """
    return [
        {"open_span": s, "expected": dist.expectation(s), "dispersion": dist.dispersion(s)}
        for s in spans
    ]


# ---------------------------------------------------------------- fetching
def distribution_from_event(event: dict, tenor: str) -> ImpliedDistribution:
    """Build a distribution from one Polymarket event payload."""
    import json as _json

    source = "unknown"
    brackets: list[Bracket] = []
    for market in event.get("markets", []):
        if market.get("closed"):
            continue
        label = (market.get("groupItemTitle") or "").strip()
        if not label:
            continue
        prices = market.get("outcomePrices")
        if isinstance(prices, str):
            prices = _json.loads(prices)
        if not prices:
            continue
        question = market.get("question") or ""
        match = re.search(r"\b(Ornn|Silicon Data)[\w\s]*Index", question)
        if match:
            source = match.group(0)
        try:
            low, high = parse_bracket(label)
        except ValueError:
            continue
        brackets.append(
            Bracket(
                label=label,
                low=low,
                high=high,
                quoted=float(prices[0]),
                volume=float(market.get("volumeNum") or 0.0),
                liquidity=float(market.get("liquidityNum") or 0.0),
            )
        )
    brackets.sort(key=lambda b: (b.low if b.low is not None else -math.inf))

    settles = None
    raw_end = event.get("endDate")
    if isinstance(raw_end, str) and len(raw_end) >= 10:
        try:
            settles = date.fromisoformat(raw_end[:10])
        except ValueError:
            settles = None

    return ImpliedDistribution(
        tenor=tenor, settles=settles, source_index=source, brackets=brackets
    )


def fetch_distribution(slug: str, tenor: str, client: httpx.Client | None = None) -> ImpliedDistribution:
    """Fetch one event's ladder. Network; everything above this line is pure."""
    own = client is None
    client = client or httpx.Client(
        timeout=httpx.Timeout(30.0, connect=10.0),
        headers={"User-Agent": "gpuidx/implied (+https://github.com/henryzhangpku/gpu-price-index)"},
        follow_redirects=True,
    )
    try:
        response = client.get(GAMMA, params={"slug": slug})
        response.raise_for_status()
        payload = response.json()
        if not payload:
            return ImpliedDistribution(tenor=tenor, settles=None, source_index="unknown")
        return distribution_from_event(payload[0], tenor)
    finally:
        if own:
            client.close()
