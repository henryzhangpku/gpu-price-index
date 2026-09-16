"""A methodology version is a behaviour, not a label.

Before the registry existed, ``verify`` compared every tape row's version to
one constant and reported any difference as drift. The first real methodology
change would therefore have made the entire historical series unverifiable at
once, and the daily workflow refuses to commit a fixing that fails verify. The
tests here pin the property that replaced that: a row is recomputed under the
version it names, so changing today's methodology leaves yesterday's values
exactly as reproducible as they were.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

import gpuidx
from gpuidx.archive import append_to_tape, stamp_superseded, write_snapshot
from gpuidx.estimator import estimate
from gpuidx.models import Tier
from gpuidx.normalize import prepare_quotes
from gpuidx.reproduce import estimate_from_archive, verify
from gpuidx.spec import (
    CURRENT_METHODOLOGY,
    METHODOLOGIES,
    Gates,
    Methodology,
    methodology_for,
)

DAY = "2026-08-27"


def build(make_obs, prices, **kwargs):
    return [
        make_obs(source=n, price_per_gpu=p * tilt, sku=f"{n}-{i}", **kwargs)
        for n, p in prices.items()
        for i, tilt in enumerate((0.98, 1.00, 1.02))
    ]


def publish(root, observations, captured_at, methodology: Methodology, index_date=DAY):
    """What the pipeline does, under an explicit methodology."""
    path = write_snapshot(root, observations, captured_at=captured_at)
    quotes, _ = prepare_quotes(observations, methodology)
    est = estimate("GIX-H100", [q for q in quotes if q.index_code == "GIX-H100"], methodology)
    append_to_tape(
        root,
        [
            {
                "index_code": "GIX-H100",
                "index_date": index_date,
                "revision": 0,
                "status": "published" if est.passed else "withheld",
                "value": est.value if est.passed else "",
                "provider_count": len(est.contributing),
                "observation_count": sum(p.quote_count for p in est.contributing),
                "dispersion": est.dispersion,
                "withheld_reason": "" if est.passed else est.failed_gate_summary,
                "methodology_version": methodology.version,
                "published_at": captured_at.isoformat(),
                "superseded_at": "",
                "revision_reason": "",
                "snapshot": path.name,
            }
        ],
    )
    stamp_superseded(root)
    return est


@pytest.fixture
def future_methodology(monkeypatch):
    """A registered version whose behaviour differs from 1.0.0 in a way a test can see.

    Tier-3 inputs count for nothing under it, so a panel mixing tiers prices
    differently than under 1.0.0 -- and the difference is the point.
    """
    record = Methodology(
        version="9.9.9-test",
        tier_weights={1: 1.0, 2: 0.6, 3: 0.0},
    )
    monkeypatch.setitem(METHODOLOGIES, record.version, record)
    return record


def test_the_package_version_is_the_current_record():
    assert CURRENT_METHODOLOGY.version == gpuidx.METHODOLOGY_VERSION
    assert methodology_for(gpuidx.METHODOLOGY_VERSION) is CURRENT_METHODOLOGY


def test_registered_records_cannot_be_edited_in_place():
    """A registered version is history; mutating it would rewrite the tape's meaning."""
    with pytest.raises(Exception, match="frozen"):
        CURRENT_METHODOLOGY.version = "tampered"  # type: ignore[misc]
    with pytest.raises(Exception, match="frozen"):
        CURRENT_METHODOLOGY.gates.min_providers = 1  # type: ignore[misc]


def test_a_value_published_under_an_old_version_still_verifies_after_the_methodology_moves(
    tmp_path, make_obs, future_methodology
):
    """The whole point of the registry.

    Day one is published under 1.0.0. Day two is published under a later
    version that prices the same panel differently. Both rows must reproduce,
    each under its own rules, and the two rules must actually disagree -- or
    the test would pass on a registry that ignored the version.
    """
    # The judged provider sits inside the outlier band on purpose: screened
    # out, it would carry no weight under either version and the two would
    # agree by accident.
    panel = build(make_obs, {"a": 3.0, "b": 3.1, "c": 2.9, "d": 3.0}) + build(
        make_obs, {"judged": 3.3}, tier=Tier.JUDGEMENT
    )
    old = publish(
        tmp_path, panel, datetime(2026, 8, 27, 14, 5, tzinfo=UTC), METHODOLOGIES["1.0.0"]
    )
    new = publish(
        tmp_path,
        panel,
        datetime(2026, 8, 28, 14, 5, tzinfo=UTC),
        future_methodology,
        index_date="2026-08-28",
    )

    assert old.passed and new.passed
    assert old.value != pytest.approx(new.value), "the two versions must price differently"

    report = verify(tmp_path)
    assert report.ok, report
    assert report.checked == 2
    assert report.matched == 2
    assert report.methodology_drift == []


def test_a_row_naming_a_version_this_build_does_not_carry_is_drift(tmp_path, make_obs):
    """Drift now means exactly one thing: nobody can say what these rules were."""
    captured = datetime(2026, 8, 27, 14, 5, tzinfo=UTC)
    orphan = Methodology(version="0.0.1-lost")
    publish(tmp_path, build(make_obs, {"a": 3.0, "b": 3.1, "c": 2.9, "d": 3.0}), captured, orphan)

    report = verify(tmp_path)
    assert not report.ok
    assert len(report.methodology_drift) == 1
    assert "0.0.1-lost" in report.methodology_drift[0]
    assert "does not carry" in report.methodology_drift[0]

    est, reason = estimate_from_archive(tmp_path, "GIX-H100", DAY)
    assert est is None
    assert "does not carry" in reason


def test_audit_recomputes_under_the_row_own_version(tmp_path, make_obs, future_methodology):
    # The judged provider sits inside the outlier band on purpose: screened
    # out, it would carry no weight under either version and the two would
    # agree by accident.
    panel = build(make_obs, {"a": 3.0, "b": 3.1, "c": 2.9, "d": 3.0}) + build(
        make_obs, {"judged": 3.3}, tier=Tier.JUDGEMENT
    )
    published = publish(
        tmp_path, panel, datetime(2026, 8, 27, 14, 5, tzinfo=UTC), future_methodology
    )

    est, _ = estimate_from_archive(tmp_path, "GIX-H100", DAY)
    assert est is not None
    assert est.value == pytest.approx(published.value)
    judged = next(p for p in est.providers if p.provider == "judged")
    assert judged.weight == 0.0


def test_bare_gates_run_under_the_current_methodology(make_obs):
    """Most callers only ever pass gates; they get today's estimator with those gates."""
    quotes, _ = prepare_quotes(build(make_obs, {"a": 3.0, "b": 3.1, "c": 2.9}))
    strict = estimate("GIX-H100", quotes, Gates(min_providers=4))
    lenient = estimate("GIX-H100", quotes, Gates(min_providers=3))
    assert not strict.passed
    assert lenient.passed
    assert strict.value == pytest.approx(lenient.value)


def test_default_gates_are_the_current_methodology_gates():
    from gpuidx.spec import DEFAULT_GATES

    assert CURRENT_METHODOLOGY.gates == DEFAULT_GATES
