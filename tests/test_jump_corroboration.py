"""A big move by one contributor means one thing if the panel moved with it and another if it did not.

The contributor level-shift check already fires on either. With
``jump_corroboration`` on it says which: a repricing the rest of the panel
corroborates, a lone move that is a glitch or an attack until shown
otherwise, or -- with too few peers on both days -- that it cannot tell.
"""

from __future__ import annotations

from datetime import date

import pytest

from gpuidx.estimator import ProviderAggregate
from gpuidx.models import Tier
from gpuidx.quality import (
    CORROBORATING_MOVE,
    CORROBORATION_MIN_PEERS,
    CORROBORATIONS_REQUIRED,
    check_provider_level_shift,
)
from gpuidx.spec import METHODOLOGIES
from gpuidx.store import Store

CODE = "GIX-H100"
YESTERDAY = date(2026, 9, 15)
TODAY = date(2026, 9, 16)
V100 = METHODOLOGIES["1.0.0"]
V110 = METHODOLOGIES["1.1.0"]

PANEL = {"a": 3.0, "b": 3.1, "c": 2.9, "d": 3.2, "e": 3.0, "mover": 3.0}


def _agg(provider: str, price: float) -> ProviderAggregate:
    return ProviderAggregate(
        provider=provider, price=price, quote_count=4, best_tier=Tier.LIST_PRICE, weight=1.0
    )


@pytest.fixture
def store(tmp_path):
    db = Store(tmp_path / "t.db")
    with db.conn as conn:
        conn.execute(
            "INSERT INTO index_values (index_code, index_date, revision, status, value,"
            " provider_count, observation_count, dispersion, withheld_reason,"
            " methodology_version, published_at, superseded_at, revision_reason, run_id)"
            " VALUES (?,?,0,'published',3.03,6,24,0.05,NULL,'1.1.0',?,NULL,NULL,NULL)",
            (CODE, YESTERDAY.isoformat(), "2026-09-15T17:00:00Z"),
        )
        conn.executemany(
            "INSERT INTO contributions (index_code, index_date, revision, provider, price,"
            " weight, quote_count, tier, screened_out, screen_reason)"
            " VALUES (?,?,0,?,?,1.0,4,2,0,NULL)",
            [(CODE, YESTERDAY.isoformat(), p, v) for p, v in PANEL.items()],
        )
    yield db
    db.close()


def today(moves: dict[str, float]) -> list[ProviderAggregate]:
    """Today's panel: yesterday's prices, each scaled by 1 + its move."""
    return [_agg(p, v * (1 + moves.get(p, 0.0))) for p, v in PANEL.items()]


def test_a_lone_jump_is_called_uncorroborated(store):
    flags = check_provider_level_shift(store, CODE, TODAY, today({"mover": -0.40}), V110)
    assert [f.code for f in flags] == ["provider_level_shift_uncorroborated"]
    assert flags[0].severity == "warn"
    assert "0 of 5 others" in flags[0].detail
    assert "attack" in flags[0].detail


def test_a_jump_the_panel_moved_with_is_called_a_repricing(store):
    moves = {"mover": -0.40, "a": -0.12, "b": -0.15, "c": -0.02}
    flags = check_provider_level_shift(store, CODE, TODAY, today(moves), V110)
    assert [f.code for f in flags] == ["provider_level_shift_corroborated"]
    assert flags[0].severity == "info"
    assert f"{CORROBORATIONS_REQUIRED} of 5 others" in flags[0].detail
    assert "a, b" in flags[0].detail


def test_corroboration_must_be_in_the_same_direction(store):
    """Two peers moving 12% the other way are not corroboration; they are the opposite."""
    moves = {"mover": -0.40, "a": +0.12, "b": +0.15}
    flags = check_provider_level_shift(store, CODE, TODAY, today(moves), V110)
    assert [f.code for f in flags] == ["provider_level_shift_uncorroborated"]


def test_one_corroborating_peer_is_not_enough(store):
    moves = {"mover": -0.40, "a": -0.12}
    flags = check_provider_level_shift(store, CODE, TODAY, today(moves), V110)
    assert [f.code for f in flags] == ["provider_level_shift_uncorroborated"]
    assert "1 of 5 others" in flags[0].detail


def test_a_peer_move_just_under_the_bar_does_not_count(store):
    just_under = -(CORROBORATING_MOVE - 0.005)
    moves = {"mover": -0.40, "a": just_under, "b": just_under}
    flags = check_provider_level_shift(store, CODE, TODAY, today(moves), V110)
    assert [f.code for f in flags] == ["provider_level_shift_uncorroborated"]


def test_with_too_few_peers_the_screen_stands_down(store):
    """Three providers today, two of them peers: it says it cannot tell, not that it is a glitch."""
    thin = [_agg("mover", 3.0 * 0.6), _agg("a", 3.0), _agg("b", 3.1)]
    assert len(thin) - 1 < CORROBORATION_MIN_PEERS
    flags = check_provider_level_shift(store, CODE, TODAY, thin, V110)
    assert [f.code for f in flags] == ["provider_level_shift"]
    assert "too few to say" in flags[0].detail


def test_the_launch_methodology_keeps_the_plain_flag(store):
    flags = check_provider_level_shift(store, CODE, TODAY, today({"mover": -0.40}), V100)
    assert [f.code for f in flags] == ["provider_level_shift"]
    assert "corroborat" not in flags[0].detail


def test_each_jumper_is_judged_against_the_others(store):
    """Two contributors both jumping: each sees the other as one corroboration, not enough alone."""
    moves = {"mover": -0.40, "a": -0.35}
    flags = check_provider_level_shift(store, CODE, TODAY, today(moves), V110)
    assert sorted(f.code for f in flags) == ["provider_level_shift_uncorroborated"] * 2
    assert all("1 of 5 others" in f.detail for f in flags)


def test_it_never_gates(store):
    """Every outcome is a flag; a venue is entitled to reprice and the fixing still prints."""
    for moves in ({"mover": -0.40}, {"mover": -0.40, "a": -0.12, "b": -0.15}):
        for f in check_provider_level_shift(store, CODE, TODAY, today(moves), V110):
            assert f.severity in {"info", "warn"}
