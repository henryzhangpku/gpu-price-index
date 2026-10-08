"""Methodology 1.2.0: the two defects FINDINGS #13 found in 1.1.0, fixed forward.

1. The region screen matched US tokens as substrings, and ``"us"`` is a
   substring of ``"australia"`` and ``"russia"``. 1.2.0 matches whole
   components and tokens.
2. The marketplace book floor counted the venue's whole book before
   normalisation, so non-US machines and NVL cards carried a one-machine
   vote over it. 1.2.0 counts the rows that actually price the index.

Both defects stay in the 1.0.0 and 1.1.0 records on purpose: values were
published under them, and those values must keep reproducing. The tests pin
both sides of that.
"""

from __future__ import annotations

import csv
import shutil
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from gpuidx.archive import (
    SNAPSHOT_DIR,
    TAPE_COLUMNS,
    append_to_tape,
    read_snapshot,
    read_tape,
    tape_path,
)
from gpuidx.estimator import estimate
from gpuidx.models import RawObservation
from gpuidx.normalize import (
    Rejection,
    hold_out_thin_books,
    normalize,
    prepare,
    prepare_quotes,
    region_is_us,
)
from gpuidx.pipeline import run_daily
from gpuidx.providers.base import Provider
from gpuidx.reproduce import estimate_from_archive, verify, version_impact
from gpuidx.spec import CURRENT_METHODOLOGY, METHODOLOGIES
from gpuidx.store import Store

V110 = METHODOLOGIES["1.1.0"]
V120 = METHODOLOGIES["1.2.0"]
REPO = Path(__file__).resolve().parent.parent

#: The day FINDINGS #13 names: Vast.ai's whole H100 vote was one machine on one host.
OCT_6 = "2026-10-06T192708Z.jsonl.gz"


def test_new_fixings_publish_under_1_2_0():
    assert CURRENT_METHODOLOGY is V120
    assert V120.screens.exact_region
    assert V120.gates.book_floor_basis == "priced"
    assert (V120.gates.min_book_machines, V120.gates.min_book_hosts) == (4, 3)


def test_the_published_records_keep_their_defects():
    """Editing 1.1.0 would silently re-explain sixty-five published values."""
    for version in ("1.0.0", "1.1.0"):
        record = METHODOLOGIES[version]
        assert record.screens.exact_region is False
        assert record.gates.book_floor_basis == "book"


def test_nothing_but_the_two_fixes_differs_from_1_1_0():
    assert V120.estimator == V110.estimator
    assert V120.screens.model_copy(update={"exact_region": False}) == V110.screens
    assert V120.gates.model_copy(update={"book_floor_basis": "book"}) == V110.gates
    for field in (
        "form_factor_factors", "interconnect_factors", "commitment_factors",
        "node_size_curve", "max_total_adjustment", "tier_weights",
    ):
        assert getattr(V120, field) == getattr(V110, field), field


# -- defect 1: the region screen ---------------------------------------------


@pytest.mark.parametrize(
    "region",
    [
        "Australia", "Australia, AU", "AU", "AU, Sydney", "SYD2",
        "Russia", "Russia, RU", ", RU",
        "Cyprus", "Belarus", "Mauritius",
        "Georgia, GE", "Georgia",
        "Alberta, CA", "CA, Montreal", "Moldova, MD", "IN, Mumbai",
        "Germany, DE", "Columbus, OH",
    ],
)
def test_disclosed_regions_that_are_not_shown_to_be_us(region):
    assert region_is_us(region) is False


@pytest.mark.parametrize(
    "region",
    [
        "US", "us", "USA", "United States", "United States, US", ", US",
        "US-East", "us-west-2", "us-east-1", "us-central1", "us-southeast-1",
        "eastus", "westus2", "southcentralus",
        "Austin, US", "Texas, US", "Georgia, US", "District of Columbia, US",
        "US, Austin, TX", "US, San Jose, Ca", "US, Kansas City, MO",
        "Texas", "Ashburn", "New York",
    ],
)
def test_regions_that_are_us(region):
    assert region_is_us(region) is True


def test_the_substring_defect_is_what_1_1_0_did(make_obs):
    """Pinned so the legacy behaviour is visibly a defect, not an accident of this test."""
    aussie = make_obs(region="Australia, AU")
    assert normalize(aussie, V110).index_code == "GIX-H100"
    with pytest.raises(Rejection) as rej:
        normalize(aussie, V120)
    assert rej.value.code == "region_mismatch"


def test_an_undisclosed_region_is_still_admitted(make_obs):
    for region in (None, "", "  "):
        assert normalize(make_obs(region=region), V120).index_code == "GIX-H100"


def test_every_archived_region_string_changes_reading_only_for_australia_and_russia():
    """Across the whole archive the matcher disagrees with 1.1.0 on exactly the defect."""
    from gpuidx.archive import list_snapshots
    from gpuidx.spec import US_REGION_TOKENS

    regions: set[str] = set()
    for path in list_snapshots(REPO):
        regions |= {o.region for o in read_snapshot(path).observations if o.region}

    def legacy(r: str) -> bool:
        blob = r.replace("_", " ").replace("-", " ").replace("/", " ").lower()
        return any(token in blob for token in US_REGION_TOKENS)

    changed = {r for r in regions if legacy(r) != region_is_us(r)}
    assert changed == {"Australia, AU", "Russia, RU"}


# -- defect 2: the book floor ------------------------------------------------


def vast(make_obs, machine: int, host: int, *, region="Texas, US", gpu_model="H100 SXM", price=3.0):
    return make_obs(
        source="vastai", price_per_gpu=price, gpu_model=gpu_model, region=region,
        sku=f"vast-{machine}-{gpu_model}-{region}",
    ).model_copy(update={"payload": {"machine_id": machine, "host_id": host}})


def one_machine_book(make_obs) -> list[RawObservation]:
    """The 6 October shape: a deep book of which one US machine can price H100.

    Ten German machines on ten hosts and an NVL card carry the book over the
    1.1.0 floor; the only rows that survive normalisation are one machine,
    listed three times.
    """
    us = [vast(make_obs, 57753, 260094, price=p) for p in (6.0, 6.2, 6.4)]
    abroad = [vast(make_obs, 100 + i, 200 + i, region="Germany, DE") for i in range(10)]
    nvl = [vast(make_obs, 999, 998, gpu_model="H100 NVL")]
    return us + abroad + nvl


def test_1_1_0_counts_the_whole_book_and_lets_one_machine_through(make_obs):
    rows = one_machine_book(make_obs)
    _, flags = hold_out_thin_books(rows, V110)
    assert flags == []
    quotes, _ = prepare_quotes(rows, V110)
    assert {q.source for q in quotes if q.index_code == "GIX-H100"} == {"vastai"}


def test_1_2_0_counts_only_the_rows_that_price_the_index(make_obs):
    rows = one_machine_book(make_obs)
    prepared = prepare(rows, V120)
    assert [q for q in prepared.quotes if q.source == "vastai"] == []
    assert prepared.held_quotes == 3
    [flag] = prepared.book_flags
    assert flag.code == "book_population_floor"
    assert flag.index_code == "GIX-H100"
    assert "1 distinct machines of 4 required across 3 priced rows" in flag.detail
    assert "after the region screen" in flag.detail


def test_the_region_screen_runs_before_the_floor_counts(make_obs):
    """Australian machines must not count toward the floor either: both fixes compose."""
    us = [vast(make_obs, m, m) for m in (1, 2)]
    aussie = [vast(make_obs, m, m, region="Australia, AU") for m in (3, 4, 5)]
    prepared = prepare(us + aussie, V120)
    assert [q for q in prepared.quotes if q.source == "vastai"] == []
    assert "2 distinct machines of 4 required across 2 priced rows" in prepared.book_flags[0].detail


def test_hosts_are_counted_after_normalisation_too(make_obs):
    rows = [vast(make_obs, m, 1 if m < 4 else 2) for m in range(6)]
    rows += [vast(make_obs, 50 + i, 50 + i, region="Japan, JP") for i in range(5)]
    prepared = prepare(rows, V120)
    assert "2 distinct hosts of 3 required across 6 priced rows" in prepared.book_flags[0].detail


def test_a_populated_us_book_still_prices(make_obs):
    rows = [vast(make_obs, m, m) for m in range(4)]
    prepared = prepare(rows, V120)
    assert prepared.book_flags == []
    assert len([q for q in prepared.quotes if q.source == "vastai"]) == 4


def test_the_priced_floor_still_fails_closed(make_obs):
    rows = [make_obs(source="vastai", sku=f"v{i}") for i in range(6)]
    prepared = prepare(rows, V120)
    assert prepared.quotes == []
    assert "population is unproven" in prepared.book_flags[0].detail


def test_the_priced_floor_is_per_index(make_obs):
    h100 = [vast(make_obs, 1, 1)]
    h200 = [vast(make_obs, m, m, gpu_model="H200") for m in range(10, 15)]
    prepared = prepare(h100 + h200, V120)
    assert {q.index_code for q in prepared.quotes} == {"GIX-H200"}
    assert [f.index_code for f in prepared.book_flags] == ["GIX-H100"]


def test_the_dropped_vote_leaves_the_rest_of_the_panel_to_the_gates(make_obs):
    """Only the venue's vote goes; whether the index publishes is the ordinary gates' call."""
    cards = [
        make_obs(source=n, price_per_gpu=p, sku=f"{n}-{i}")
        for n, p in {"a": 3.0, "b": 3.1, "c": 2.9, "d": 3.2}.items()
        for i in range(2)
    ]
    quotes, _ = prepare_quotes(cards + one_machine_book(make_obs), V120)
    est = estimate("GIX-H100", [q for q in quotes if q.index_code == "GIX-H100"], V120)
    assert est.passed
    assert {p.provider for p in est.providers} == {"a", "b", "c", "d"}


# -- the reason reaches the tape ---------------------------------------------


class Replay(Provider):
    """A venue that returns a fixed set of rows, so the real pipeline can run offline."""

    source_url = "test://replay"

    def __init__(self, name: str, rows: list[RawObservation]) -> None:
        self.name = name
        self.rows = rows

    def collect(self, _client: httpx.Client) -> list[RawObservation]:
        return self.rows


def replay_providers(observations: list[RawObservation]) -> list[Provider]:
    by_source: dict[str, list[RawObservation]] = {}
    for obs in observations:
        by_source.setdefault(obs.source.split(":", 1)[0], []).append(obs)
    return [Replay(name, rows) for name, rows in sorted(by_source.items())]


def test_the_daily_run_writes_1_2_0_and_the_hold_out_to_the_tape(tmp_path, make_obs):
    cards = [
        make_obs(source=n, price_per_gpu=p, sku=f"{n}-{i}")
        for n, p in {"a": 3.0, "b": 3.1, "c": 2.9, "d": 3.2}.items()
        for i in range(2)
    ]
    store = Store(tmp_path / "db.sqlite")
    try:
        run_daily(
            store,
            index_date=date(2026, 10, 9),
            providers=replay_providers(cards + one_machine_book(make_obs)),
            archive_root=tmp_path,
        )
    finally:
        store.close()

    rows = {r["index_code"]: r for r in read_tape(tmp_path)}
    assert {r["methodology_version"] for r in rows.values()} == {"1.2.0"}
    h100 = rows["GIX-H100"]
    assert h100["status"] == "published"
    assert h100["venue_holdouts"].startswith("vastai held out of GIX-H100: 1 distinct machines")
    assert rows["GIX-H200"]["venue_holdouts"] == ""

    report = verify(tmp_path)
    assert report.ok, report


def test_verify_catches_a_hold_out_the_archive_does_not_support(tmp_path, make_obs):
    cards = [
        make_obs(source=n, price_per_gpu=p, sku=f"{n}-{i}")
        for n, p in {"a": 3.0, "b": 3.1, "c": 2.9, "d": 3.2}.items()
        for i in range(2)
    ]
    store = Store(tmp_path / "db.sqlite")
    try:
        run_daily(
            store,
            index_date=date(2026, 10, 9),
            providers=replay_providers(cards + one_machine_book(make_obs)),
            archive_root=tmp_path,
        )
    finally:
        store.close()

    path = tape_path(tmp_path)
    rows = read_tape(tmp_path)
    for row in rows:
        if row["index_code"] == "GIX-H100":
            row["venue_holdouts"] = ""
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=TAPE_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    report = verify(tmp_path)
    assert not report.ok
    [mismatch] = report.mismatches
    assert mismatch.index_code == "GIX-H100"
    assert "venue hold-outs differ" in mismatch.detail


def test_a_tape_written_before_the_column_is_widened_not_corrupted(tmp_path):
    """The first 1.2.0 append must not land its hold-out under no column name."""
    old_columns = TAPE_COLUMNS[:-1]
    path = tape_path(tmp_path)
    path.parent.mkdir(parents=True)
    old_row = dict.fromkeys(old_columns, "") | {
        "index_code": "GIX-H100", "index_date": "2026-10-08", "revision": "0",
        "status": "published", "value": "3.9", "provider_count": "12",
        "observation_count": "34", "methodology_version": "1.1.0",
    }
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=old_columns)
        writer.writeheader()
        writer.writerow(old_row)

    new_row = old_row | {
        "index_date": "2026-10-09", "methodology_version": "1.2.0",
        "venue_holdouts": "vastai held out of GIX-H100: reasons",
    }
    append_to_tape(tmp_path, [new_row])

    with path.open(encoding="utf-8", newline="") as handle:
        assert next(csv.reader(handle)) == TAPE_COLUMNS
    old, new = read_tape(tmp_path)
    assert old["value"] == "3.9" and old["venue_holdouts"] == ""
    assert new["venue_holdouts"] == "vastai held out of GIX-H100: reasons"
    assert None not in new


def test_a_tape_this_build_does_not_understand_is_refused(tmp_path):
    path = tape_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("index_code,something_else\nGIX-H100,x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not a prefix"):
        append_to_tape(tmp_path, [{"index_code": "GIX-H100"}])


# -- reproducibility from a real archived day --------------------------------


def test_the_6_october_snapshot_under_1_2_0_drops_the_one_machine_vote():
    """The committed snapshot FINDINGS #13 dissects, priced under 1.2.0.

    1.1.0 printed $4.059 from it, with Vast.ai's vote resting on machine
    57753 alone. 1.2.0 holds that vote out and prices the continuing rate
    cards. The number is pinned: a change to the 1.2.0 record moves it.
    """
    observations = read_snapshot(REPO / SNAPSHOT_DIR / OCT_6).observations
    quotes, flags = prepare_quotes(observations, V120)
    est = estimate("GIX-H100", [q for q in quotes if q.index_code == "GIX-H100"], V120)
    assert est.passed
    assert est.value == pytest.approx(3.351, abs=0.0005)
    assert "vastai" not in {p.provider for p in est.providers}
    held = [f for f in flags if f.code == "book_population_floor" and f.index_code == "GIX-H100"]
    assert len(held) == 1
    assert "1 distinct machines of 4 required across 3 priced rows" in held[0].detail

    legacy_quotes, _ = prepare_quotes(observations, V110)
    legacy = estimate(
        "GIX-H100", [q for q in legacy_quotes if q.index_code == "GIX-H100"], V110
    )
    assert legacy.value == pytest.approx(4.059, abs=0.0005)


def test_a_1_2_0_day_rebuilds_from_its_archived_snapshot(tmp_path):
    """Publish a real archived day through the live pipeline, then rebuild it from disk alone.

    The 6 October observations are replayed through ``run_daily`` into an
    empty archive, exactly as the scheduled job would publish them under
    1.2.0. Everything is then thrown away except the snapshot and the tape,
    and the fixing -- value, provider count and hold-out -- is derived again.
    """
    observations = read_snapshot(REPO / SNAPSHOT_DIR / OCT_6).observations
    store = Store(tmp_path / "db.sqlite")
    try:
        report = run_daily(
            store,
            index_date=date(2026, 10, 6),
            providers=replay_providers(observations),
            archive_root=tmp_path,
        )
    finally:
        store.close()
    (tmp_path / "db.sqlite").unlink()

    published = report.values["GIX-H100"]
    assert published.methodology_version == "1.2.0"
    assert published.value == pytest.approx(3.351, abs=0.0005)

    verified = verify(tmp_path)
    assert verified.ok, verified
    assert verified.checked == 5 and verified.matched == 5

    rebuilt, snapshot = estimate_from_archive(tmp_path, "GIX-H100", "2026-10-06")
    assert rebuilt is not None
    assert rebuilt.value == pytest.approx(published.value, abs=1e-12)
    assert len(rebuilt.contributing) == published.provider_count
    [row] = [r for r in read_tape(tmp_path) if r["index_code"] == "GIX-H100"]
    assert row["snapshot"] == snapshot
    assert "vastai held out of GIX-H100" in row["venue_holdouts"]


def test_the_committed_archive_still_verifies_under_each_row_own_version():
    """1.2.0 is registered beside 1.0.0 and 1.1.0, not over them."""
    report = verify(REPO)
    assert report.ok, report
    assert report.methodology_drift == []


def test_the_back_test_writes_nothing(tmp_path):
    """``impact`` is a document, never a revision."""
    shutil.copytree(REPO / SNAPSHOT_DIR, tmp_path / SNAPSHOT_DIR)
    (tmp_path / "series").mkdir()
    shutil.copy(tape_path(REPO), tape_path(tmp_path))
    before = tape_path(tmp_path).read_bytes()

    rows = version_impact(tmp_path, V120, V110)
    assert tape_path(tmp_path).read_bytes() == before

    oct6 = next(r for r in rows if r.index_code == "GIX-H100" and r.index_date == "2026-10-06")
    assert oct6.differs
    assert oct6.change == pytest.approx(3.351 / 4.059 - 1, abs=0.001)
    untouched = [r for r in rows if r.published_version == "1.0.0"]
    assert untouched and not any(r.differs for r in untouched), (
        "before 1.1.0 no Vast.ai row carried machine ids, so 1.1.0 and 1.2.0 both hold it out"
    )


def test_captured_at_is_carried_into_the_replayed_snapshot(tmp_path):
    """Sanity for the replay helper: the rows republished are the rows archived."""
    observations = read_snapshot(REPO / SNAPSHOT_DIR / OCT_6).observations
    store = Store(tmp_path / "db.sqlite")
    try:
        run_daily(
            store,
            index_date=date(2026, 10, 6),
            providers=replay_providers(observations),
            archive_root=tmp_path,
        )
    finally:
        store.close()
    [written] = list((tmp_path / SNAPSHOT_DIR).glob("*.jsonl.gz"))
    replayed = read_snapshot(written).observations
    assert len(replayed) == len(observations)
    assert min(o.observed_at for o in replayed) == min(o.observed_at for o in observations)
    assert min(o.observed_at for o in replayed) < datetime(2026, 10, 7, tzinfo=UTC)
