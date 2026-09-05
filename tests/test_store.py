"""Bitemporal guarantees.

The property under test throughout: a published value can always be
reconstructed as it stood at any past moment, no matter what was corrected
afterwards. That is the difference between a series and a settlement record.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from gpuidx.estimator import estimate
from gpuidx.normalize import normalize
from gpuidx.spec import Gates
from gpuidx.store import Store

GATES = Gates()
DAY = date(2026, 8, 27)


@pytest.fixture
def store(tmp_path) -> Store:
    s = Store(tmp_path / "test.db")
    yield s
    s.close()


def build(make_obs, prices):
    """Three SKUs per provider, so fixtures clear the observation-count gate."""
    quotes = [
        normalize(make_obs(source=n, price_per_gpu=p * tilt, sku=f"{n}-{i}"))
        for n, p in prices.items()
        for i, tilt in enumerate((0.98, 1.00, 1.02))
    ]
    return estimate("GIX-H100", quotes, GATES)


def test_publish_then_revise_appends_rather_than_overwrites(store, make_obs):
    first = build(make_obs, {"a": 3.0, "b": 3.1, "c": 2.9, "d": 3.0})
    store.publish("GIX-H100", DAY, first, run_id=None)

    second = build(make_obs, {"a": 4.0, "b": 4.1, "c": 3.9, "d": 4.0})
    store.publish("GIX-H100", DAY, second, run_id=None, revision_reason="vendor unit error")

    revisions = store.revisions("GIX-H100", DAY)
    assert [r["revision"] for r in revisions] == [0, 1]
    assert revisions[0]["value"] == pytest.approx(3.0)
    assert revisions[1]["value"] == pytest.approx(4.0)
    # The original is retained, stamped with when it stopped being live.
    assert revisions[0]["superseded_at"] is not None
    assert revisions[1]["superseded_at"] is None
    assert revisions[1]["revision_reason"] == "vendor unit error"


def test_as_of_returns_what_was_known_at_the_time(store, make_obs):
    first = build(make_obs, {"a": 3.0, "b": 3.1, "c": 2.9, "d": 3.0})
    store.publish("GIX-H100", DAY, first, run_id=None)

    # Read the knowledge time back from the store rather than sampling the
    # clock: two publications inside the same wall-clock tick would otherwise
    # make the query ambiguous, and the test would be asserting on timing
    # rather than on bitemporal semantics.
    rev0_published = datetime.fromisoformat(
        store.revisions("GIX-H100", DAY)[0]["published_at"]
    )

    second = build(make_obs, {"a": 4.0, "b": 4.1, "c": 3.9, "d": 4.0})
    store.publish("GIX-H100", DAY, second, run_id=None, revision_reason="correction")
    between = rev0_published

    # A settlement agent asking what the tape said before the correction must
    # get the original number, not the corrected one.
    assert store.as_of("GIX-H100", DAY, between)["value"] == pytest.approx(3.0)
    later = datetime.now(UTC) + timedelta(days=1)
    assert store.as_of("GIX-H100", DAY, later)["value"] == pytest.approx(4.0)


def test_as_of_before_first_publication_returns_nothing(store, make_obs):
    est = build(make_obs, {"a": 3.0, "b": 3.1, "c": 2.9, "d": 3.0})
    store.publish("GIX-H100", DAY, est, run_id=None)
    before = datetime.now(UTC) - timedelta(days=1)
    assert store.as_of("GIX-H100", DAY, before) is None


def test_history_shows_only_the_live_revision_per_date(store, make_obs):
    for day, price in ((date(2026, 8, 25), 3.0), (date(2026, 8, 26), 3.2)):
        store.publish("GIX-H100", day, build(make_obs, {"a": price, "b": price, "c": price, "d": price}), None)
    store.publish(
        "GIX-H100",
        date(2026, 8, 25),
        build(make_obs, {"a": 9.0, "b": 9.0, "c": 9.0, "d": 9.0}),
        None,
        revision_reason="correction",
    )

    history = store.history("GIX-H100")
    assert len(history) == 2
    by_date = {r["index_date"]: r["value"] for r in history}
    assert by_date["2026-08-25"] == pytest.approx(9.0)


def test_withheld_value_is_recorded_with_its_reason(store, make_obs):
    thin = build(make_obs, {"a": 3.0, "b": 3.1})
    record = store.publish("GIX-H100", DAY, thin, run_id=None)
    assert record.status.value == "withheld"
    assert record.value is None
    assert "min_providers" in record.withheld_reason


def test_contributions_retain_screened_providers(store, make_obs):
    est = build(make_obs, {"a": 3.0, "b": 3.1, "c": 2.9, "d": 3.05, "odd": 0.05})
    store.publish("GIX-H100", DAY, est, run_id=None)

    rows = store.contributions("GIX-H100", DAY, 0)
    screened = [r for r in rows if r["screened_out"]]
    # The audit trail must show what was excluded, not just what survived.
    assert [r["provider"] for r in screened] == ["odd"]
    assert screened[0]["screen_reason"]


def test_as_of_at_the_exact_publication_instant_finds_the_value():
    """The boundary case, which is the one a dispute would actually ask about.

    Timestamps in this column are compared as strings. Two spellings were in
    use -- a live publish wrote `+00:00`, `rebuild` inserted the tape's `Z` --
    and since 'Z' sorts after '+', an as-of query at exactly a publication
    instant compared the two spellings and reported the value did not exist yet.
    """
    from datetime import UTC, date, datetime

    from gpuidx.store import _iso_z

    store = Store(":memory:")
    published = datetime(2026, 8, 27, 20, 53, 50, 445409, tzinfo=UTC)

    with store.tx() as conn:
        conn.execute(
            "INSERT INTO index_values (index_code, index_date, revision, status, value,"
            " provider_count, observation_count, dispersion, withheld_reason,"
            " methodology_version, published_at, superseded_at, revision_reason, run_id)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,NULL)",
            ("GIX-H100", "2026-08-27", 0, "published", 3.089, 6, 20, 0.1, None,
             "1.0.0", _iso_z(published)),
        )

    at_instant = store.as_of("GIX-H100", date(2026, 8, 27), published)
    assert at_instant is not None, "a value must exist at its own publication instant"
    assert at_instant["revision"] == 0

    from datetime import timedelta

    assert store.as_of("GIX-H100", date(2026, 8, 27), published - timedelta(microseconds=1)) is None
    assert store.as_of("GIX-H100", date(2026, 8, 27), published + timedelta(microseconds=1)) is not None
    store.close()


def test_timestamps_have_one_spelling():
    from datetime import UTC, datetime

    from gpuidx.store import _iso_z

    instant = datetime(2026, 8, 27, 20, 53, 50, 445409, tzinfo=UTC)
    canonical = "2026-08-27T20:53:50.445409Z"
    assert _iso_z(instant) == canonical
    assert _iso_z(canonical) == canonical
    assert _iso_z("2026-08-27T20:53:50.445409+00:00") == canonical
