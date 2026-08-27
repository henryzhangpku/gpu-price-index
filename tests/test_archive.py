"""The durable record and the reproducibility check.

These tests never touch the network. They build observations directly, write
them through the same archive code the pipeline uses, and then ask whether the
published series can be derived back out of the archive.
"""

from __future__ import annotations

import gzip
from datetime import date, datetime, timezone

import pytest

from gpuidx import METHODOLOGY_VERSION
from gpuidx.archive import (
    append_to_tape,
    list_snapshots,
    live_tape_values,
    read_snapshot,
    read_tape,
    stamp_superseded,
    write_snapshot,
)
from gpuidx.estimator import estimate
from gpuidx.models import Commitment
from gpuidx.normalize import prepare_quotes
from gpuidx.reproduce import rebuild, verify
from gpuidx.spec import Gates
from gpuidx.store import Store

GATES = Gates()
DAY = "2026-08-27"


def build(make_obs, prices):
    return [
        make_obs(source=n, price_per_gpu=p * tilt, sku=f"{n}-{i}")
        for n, p in prices.items()
        for i, tilt in enumerate((0.98, 1.00, 1.02))
    ]


def publish_into_archive(root, observations, captured_at, revision=0, reason=""):
    """Mirror what the pipeline does, without collecting from the network."""
    path = write_snapshot(root, observations, captured_at=captured_at)
    # Deliberately the same entry point the pipeline uses. When publication
    # and verification went through different functions, adding a screen to
    # one silently made every value irreproducible from its own archive.
    quotes, _ = prepare_quotes(observations)
    est = estimate("GIX-H100", [q for q in quotes if q.index_code == "GIX-H100"], GATES)
    append_to_tape(
        root,
        [
            {
                "index_code": "GIX-H100",
                "index_date": DAY,
                "revision": revision,
                "status": "published" if est.passed else "withheld",
                "value": est.value if est.passed else "",
                "provider_count": len(est.contributing),
                "observation_count": sum(p.quote_count for p in est.contributing),
                "dispersion": est.dispersion,
                "withheld_reason": "" if est.passed else est.failed_gate_summary,
                "methodology_version": METHODOLOGY_VERSION,
                "published_at": captured_at.isoformat(),
                "superseded_at": "",
                "revision_reason": reason,
                "snapshot": path.name,
            }
        ],
    )
    stamp_superseded(root)
    return path, est


def test_snapshot_round_trips_every_observation(tmp_path, make_obs):
    observations = build(make_obs, {"a": 3.0, "b": 3.1, "c": 2.9, "d": 3.0})
    path = write_snapshot(tmp_path, observations)

    restored = read_snapshot(path).observations
    assert len(restored) == len(observations)
    assert [o.usd_per_gpu_hour for o in restored] == pytest.approx(
        [o.usd_per_gpu_hour for o in observations]
    )
    assert {o.source for o in restored} == {"a", "b", "c", "d"}


def test_snapshot_is_gzipped_jsonl(tmp_path, make_obs):
    path = write_snapshot(tmp_path, build(make_obs, {"a": 3.0, "b": 3.0}))
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        lines = [line for line in handle if line.strip()]
    assert len(lines) == 6
    assert all(line.startswith("{") for line in lines)


def test_published_series_reproduces_from_its_archive(tmp_path, make_obs):
    captured = datetime(2026, 8, 27, 14, 5, tzinfo=timezone.utc)
    publish_into_archive(tmp_path, build(make_obs, {"a": 3.0, "b": 3.1, "c": 2.9, "d": 3.0}), captured)

    report = verify(tmp_path)
    assert report.ok
    assert report.checked == 1
    assert report.matched == 1


def test_tampering_with_a_snapshot_is_detected(tmp_path, make_obs):
    captured = datetime(2026, 8, 27, 14, 5, tzinfo=timezone.utc)
    path, _ = publish_into_archive(
        tmp_path, build(make_obs, {"a": 3.0, "b": 3.1, "c": 2.9, "d": 3.0}), captured
    )

    # Rewrite the archived inputs so they no longer produce the published value.
    tampered = build(make_obs, {"a": 9.0, "b": 9.1, "c": 8.9, "d": 9.0})
    with gzip.open(path, "wt", encoding="utf-8", newline="\n") as handle:
        for obs in tampered:
            handle.write(obs.model_dump_json() + "\n")

    report = verify(tmp_path)
    assert not report.ok
    assert len(report.mismatches) == 1
    assert report.mismatches[0].index_code == "GIX-H100"


def test_two_runs_on_one_date_verify_against_their_own_inputs(tmp_path, make_obs):
    """The case that pooling by date gets wrong.

    Two collections on the same index date produce different observation sets.
    Each published value must be checked against the run that produced it, not
    against the union, which never existed as a market state.
    """
    morning = datetime(2026, 8, 27, 14, 5, tzinfo=timezone.utc)
    evening = datetime(2026, 8, 27, 20, 5, tzinfo=timezone.utc)

    publish_into_archive(tmp_path, build(make_obs, {"a": 3.0, "b": 3.1, "c": 2.9, "d": 3.0}), morning)
    publish_into_archive(
        tmp_path,
        build(make_obs, {"a": 4.0, "b": 4.1, "c": 3.9, "d": 4.0}),
        evening,
        revision=1,
        reason="afternoon repricing",
    )

    assert len(list_snapshots(tmp_path)) == 2
    report = verify(tmp_path)
    assert report.ok, [m.detail for m in report.mismatches]
    # verify checks the live revision, which is the evening one.
    assert report.checked == 1


def test_missing_snapshot_is_unverifiable_not_a_pass(tmp_path, make_obs):
    captured = datetime(2026, 8, 27, 14, 5, tzinfo=timezone.utc)
    path, _ = publish_into_archive(
        tmp_path, build(make_obs, {"a": 3.0, "b": 3.1, "c": 2.9, "d": 3.0}), captured
    )
    path.unlink()

    report = verify(tmp_path)
    assert report.checked == 1
    assert report.matched == 0
    assert len(report.unverifiable) == 1
    assert "missing" in report.unverifiable[0].detail

    # A value whose inputs are gone must never be reported as reproduced, and
    # it fails the run. The promise is that every published value can be
    # rebuilt from the archive; a missing snapshot breaks that promise whether
    # or not anything was tampered with.
    assert not report.ok
    assert len(report.dangling) == 1


def test_superseded_is_stamped_without_disturbing_prior_rows(tmp_path, make_obs):
    morning = datetime(2026, 8, 27, 14, 5, tzinfo=timezone.utc)
    evening = datetime(2026, 8, 27, 20, 5, tzinfo=timezone.utc)
    publish_into_archive(tmp_path, build(make_obs, {"a": 3.0, "b": 3.1, "c": 2.9, "d": 3.0}), morning)
    before = read_tape(tmp_path)[0]["value"]

    publish_into_archive(
        tmp_path, build(make_obs, {"a": 4.0, "b": 4.0, "c": 4.0, "d": 4.0}), evening, revision=1
    )

    rows = read_tape(tmp_path)
    assert len(rows) == 2
    assert rows[0]["value"] == before  # the original print is untouched
    assert rows[0]["superseded_at"] == evening.isoformat()
    assert rows[1]["superseded_at"] == ""


def test_rebuild_restores_the_publication_record(tmp_path, make_obs):
    captured = datetime(2026, 8, 27, 14, 5, tzinfo=timezone.utc)
    publish_into_archive(tmp_path, build(make_obs, {"a": 3.0, "b": 3.1, "c": 2.9, "d": 3.0}), captured)

    store = Store(tmp_path / "rebuilt.db")
    replayed = rebuild(store, tmp_path)

    assert replayed == 1
    history = store.history("GIX-H100")
    assert len(history) == 1
    assert history[0]["index_date"] == DAY
    # Observations came back too, so the quality checks have their history.
    assert store.conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 12
    store.close()


def test_rebuild_recent_bounds_replay_but_keeps_the_whole_tape(tmp_path, make_obs):
    """A daily job should not re-ingest a year of archive to publish one value."""
    for hour in range(4):
        publish_into_archive(
            tmp_path,
            build(make_obs, {"a": 3.0, "b": 3.1, "c": 2.9, "d": 3.0}),
            datetime(2026, 8, 27, 10 + hour, tzinfo=timezone.utc),
            revision=hour,
        )

    store = Store(tmp_path / "bounded.db")
    replayed = rebuild(store, tmp_path, recent=2)

    assert replayed == 2
    # Only two snapshots replayed...
    assert store.conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 24
    # ...but every revision is present, because level-shift detection needs the
    # full published series rather than a window of it.
    assert len(store.revisions("GIX-H100", date.fromisoformat(DAY))) == 4
    store.close()


def test_live_tape_values_returns_the_highest_revision(tmp_path, make_obs):
    for revision in range(3):
        publish_into_archive(
            tmp_path,
            build(make_obs, {"a": 3.0 + revision, "b": 3.0 + revision, "c": 3.0 + revision, "d": 3.0 + revision}),
            datetime(2026, 8, 27, 10 + revision, tzinfo=timezone.utc),
            revision=revision,
        )

    live = live_tape_values(tmp_path)
    assert len(live) == 1
    assert int(live[("GIX-H100", DAY)]["revision"]) == 2


def test_publication_and_verification_share_one_preparation_path(tmp_path, make_obs):
    """Regression: a screen added to publication must reach verification too.

    The archive contains an administered venue whose quotes the pipeline
    excludes. If verification did not apply the same exclusion, it would
    recompute from a larger input set and the value would not reproduce --
    which is exactly what happened when these were two code paths.
    """
    captured = datetime(2026, 8, 27, 14, 5, tzinfo=timezone.utc)

    honest = build(make_obs, {"a": 3.0, "b": 3.1, "c": 2.9, "d": 3.0})
    administered = []
    for i, model in enumerate(["H100 SXM", "H200", "A100 SXM4", "B200", "RTX 4090", "L40S", "A40"]):
        administered.append(
            make_obs(source="policyvendor", gpu_model=model, price_per_gpu=4.0, sku=f"pv-{i}-od")
        )
        administered.append(
            make_obs(
                source="policyvendor",
                gpu_model=model,
                price_per_gpu=2.0,
                commitment=Commitment.SPOT,
                sku=f"pv-{i}-spot",
            )
        )

    _, est = publish_into_archive(tmp_path, honest + administered, captured)

    # The exclusion actually bit: the administered venue's spot quotes are gone.
    assert "policyvendor" in {p.provider for p in est.providers}
    quotes, flags = prepare_quotes(honest + administered)
    assert any(f.code == "administered_pricing_excluded" for f in flags)

    report = verify(tmp_path)
    assert report.ok, [m.detail for m in report.mismatches]
    assert report.matched == 1


def test_a_superseded_revision_losing_its_inputs_is_caught(tmp_path, make_obs):
    """Integrity across every revision, not just the live one.

    verify only recomputes the current value, so without a separate sweep a
    superseded revision's snapshot could be deleted and nothing would notice.
    A superseded value is still a published value; someone may have settled
    against it, and its inputs have to stay on file.
    """
    morning = datetime(2026, 8, 27, 14, 5, tzinfo=timezone.utc)
    evening = datetime(2026, 8, 27, 20, 5, tzinfo=timezone.utc)

    first, _ = publish_into_archive(
        tmp_path, build(make_obs, {"a": 3.0, "b": 3.1, "c": 2.9, "d": 3.0}), morning
    )
    publish_into_archive(
        tmp_path,
        build(make_obs, {"a": 4.0, "b": 4.1, "c": 3.9, "d": 4.0}),
        evening,
        revision=1,
        reason="correction",
    )

    # The live revision still reproduces, so the recompute path stays clean.
    assert verify(tmp_path).ok

    first.unlink()
    report = verify(tmp_path)

    assert not report.ok
    assert len(report.dangling) == 1
    assert "rev 0" in report.dangling[0]
    # The live value is untouched and still reproduces; only the archive is
    # incomplete, and the distinction is visible in the report.
    assert report.matched == 1
    assert not report.mismatches
