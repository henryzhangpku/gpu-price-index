"""A contributor repricing itself is the attack every other defence misses.

The estimator's defences are all shaped around the *panel*: collapse each
venue to one median so SKU count buys nothing, screen outliers against the
panel's spread, cap any one provider's weight, and refuse to publish a thin
one. None of them watch a provider that simply changes its own mind.

On the 7 September H100 panel, a tier-1 contributor moving its median from
$4.01 to $0.90 sits **1.85 robust sigma** from the panel median -- inside the
keep band, so it is not screened -- clears the dispersion gate at 0.386, and
never approaches the 35% cap at an 11.8% share. It moves the fixing 12%, which
is under the 15% index-level review threshold, so even the level-shift flag
stays silent.

That is a short seller's move, and every gate lets it through. This check is
the one that speaks up, and these tests pin both halves: that it fires on the
attack, and that the outlier screen genuinely does not.
"""

from __future__ import annotations

import statistics
from datetime import date

import pytest

from gpuidx.estimator import MAD_TO_SIGMA, OUTLIER_SIGMAS, ProviderAggregate
from gpuidx.models import Tier
from gpuidx.quality import PROVIDER_LEVEL_SHIFT, check_provider_level_shift
from gpuidx.store import Store

CODE = "GIX-H100"
YESTERDAY = date(2026, 9, 6)
TODAY = date(2026, 9, 7)

#: The live 7 September panel, rounded to the prices the CLI prints.
PANEL = {
    "shadeform:lambdalabs": 4.010,
    "shadeform:latitude": 2.300,
    "shadeform:massedcompute": 3.585,
    "vastai": 1.667,
    "datacrunch": 3.087,
    "runpod": 3.223,
    "shadeform:denvr": 2.350,
    "shadeform:digitalocean": 4.057,
    "shadeform:hyperstack": 3.200,
    "shadeform:imwt": 3.248,
    "shadeform:voltagepark": 1.950,
    "curated:aws": 6.880,
}
ATTACKER = "shadeform:lambdalabs"
ATTACK_PRICE = 0.90


def _agg(provider: str, price: float, screened: bool = False) -> ProviderAggregate:
    return ProviderAggregate(
        provider=provider,
        price=price,
        quote_count=4,
        best_tier=Tier.EXECUTABLE,
        weight=1.0,
        screened_out=screened,
    )


@pytest.fixture
def store(tmp_path):
    """A store holding yesterday's published fixing and its contributions."""
    db = Store(tmp_path / "t.db")
    with db.conn as conn:
        conn.execute(
            "INSERT INTO index_values (index_code, index_date, revision, status, value,"
            " provider_count, observation_count, dispersion, withheld_reason,"
            " methodology_version, published_at, superseded_at, revision_reason, run_id)"
            " VALUES (?,?,0,'published',3.0711,12,47,0.379,NULL,'1.0.0',?,NULL,NULL,NULL)",
            (CODE, YESTERDAY.isoformat(), "2026-09-06T17:00:00Z"),
        )
        conn.executemany(
            "INSERT INTO contributions (index_code, index_date, revision, provider, price,"
            " weight, quote_count, tier, screened_out, screen_reason)"
            " VALUES (?,?,0,?,?,1.0,4,1,0,NULL)",
            [(CODE, YESTERDAY.isoformat(), p, v) for p, v in PANEL.items()],
        )
    yield db
    db.close()


def test_the_attack_is_flagged(store) -> None:
    aggregates = [
        _agg(p, ATTACK_PRICE if p == ATTACKER else v) for p, v in PANEL.items()
    ]
    flags = check_provider_level_shift(store, CODE, TODAY, aggregates)

    assert [f.code for f in flags] == ["provider_level_shift"]
    assert ATTACKER in flags[0].detail
    assert flags[0].severity == "warn"
    assert flags[0].index_code == CODE


def test_the_outlier_screen_does_not_catch_it(store) -> None:
    """The reason this check has to exist.

    If the 3-sigma screen already removed a provider that halved its own price,
    a contributor-level check would be redundant. It does not, because the band
    is symmetric in dollars on a positive, right-skewed quantity -- its lower
    edge sits below zero, so nothing can be screened for being too cheap.
    """
    prices = sorted([ATTACK_PRICE] + [v for p, v in PANEL.items() if p != ATTACKER])
    median = statistics.median(prices)
    sigma = statistics.median([abs(x - median) for x in prices]) * MAD_TO_SIGMA

    assert median - OUTLIER_SIGMAS * sigma < 0, "lower edge must be below zero"
    assert abs(ATTACK_PRICE - median) / sigma < OUTLIER_SIGMAS, "screen keeps it"


def test_an_ordinary_reprice_is_not_flagged(store) -> None:
    """Rate cards move. The threshold sits above the observed 99th percentile
    of provider day-over-day moves, so routine repricing stays quiet."""
    aggregates = [_agg(p, v * 1.10) for p, v in PANEL.items()]
    assert check_provider_level_shift(store, CODE, TODAY, aggregates) == []


@pytest.mark.parametrize("direction", [1, -1])
def test_the_threshold_is_symmetric(store, direction: int) -> None:
    """A provider doubling its price is as interesting as one halving it, even
    though only one of those is profitable for a short."""
    factor = 1 + direction * (PROVIDER_LEVEL_SHIFT + 0.05)
    aggregates = [
        _agg(p, v * factor if p == ATTACKER else v) for p, v in PANEL.items()
    ]
    flags = check_provider_level_shift(store, CODE, TODAY, aggregates)
    assert len(flags) == 1 and ATTACKER in flags[0].detail


def test_screened_providers_raise_nothing(store) -> None:
    """A screened provider carries no weight, so its move cannot reach the
    fixing and does not need a human."""
    aggregates = [
        _agg(p, ATTACK_PRICE if p == ATTACKER else v, screened=(p == ATTACKER))
        for p, v in PANEL.items()
    ]
    assert check_provider_level_shift(store, CODE, TODAY, aggregates) == []


def test_a_new_provider_raises_nothing(store) -> None:
    """No prior price is not a move. Treating an arrival as a shift would flag
    every genuine expansion of coverage."""
    aggregates = [_agg("brand:new", 0.10)]
    assert check_provider_level_shift(store, CODE, TODAY, aggregates) == []


def test_no_previous_fixing_raises_nothing(tmp_path) -> None:
    db = Store(tmp_path / "empty.db")
    try:
        aggregates = [_agg(p, v) for p, v in PANEL.items()]
        assert check_provider_level_shift(db, CODE, TODAY, aggregates) == []
    finally:
        db.close()


def test_it_flags_rather_than_gates(store) -> None:
    """Severity is a warning, never an error: a venue is entitled to reprice,
    and a check that withheld on one contributor's decision would hand every
    provider a veto over the fixing."""
    aggregates = [
        _agg(p, ATTACK_PRICE if p == ATTACKER else v) for p, v in PANEL.items()
    ]
    flags = check_provider_level_shift(store, CODE, TODAY, aggregates)
    assert all(f.severity == "warn" for f in flags)
