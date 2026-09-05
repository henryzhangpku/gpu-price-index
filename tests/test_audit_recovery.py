"""A rebuilt store must still be able to explain a published value.

The gap this covers: contributions are written by a live ``publish``, and
``rebuild`` restores the tape rather than replaying one. So a fresh clone --
the state every reader of this repository is in -- could restore all 50
published values and explain none of them. `gpuidx audit`, which the README
calls the command worth looking at, printed a header and an empty table.

Nothing caught it because nothing tested the rebuild-then-audit path, which is
the only path a reader takes.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from gpuidx.archive import append_to_tape, write_snapshot
from gpuidx.models import Commitment, FormFactor, Interconnect, RawObservation, Tier
from gpuidx.reproduce import estimate_from_archive, rebuild
from gpuidx.store import Store


def _obs(source: str, price: float) -> RawObservation:
    return RawObservation(
        source=source,
        source_sku=f"{source}-h100",
        gpu_model="H100 SXM",
        gpu_count=8,
        usd_per_hour_total=price * 8,
        commitment=Commitment.ON_DEMAND,
        form_factor=FormFactor.SXM,
        interconnect=Interconnect.NVLINK,
        region="us-east",
        tier=Tier.EXECUTABLE,
        observed_at=datetime.now(UTC),
    )


@pytest.fixture
def archive(tmp_path):
    """One snapshot and one tape row naming it -- a minimal published day."""
    snapshot = write_snapshot(tmp_path, [_obs(f"venue{i}", 3.00 + i * 0.05) for i in range(6)])
    append_to_tape(
        tmp_path,
        [
            {
                "index_code": "GIX-H100", "index_date": "2026-09-04", "revision": 0,
                "status": "published", "value": 3.125, "provider_count": 6,
                "observation_count": 6, "dispersion": 0.05, "withheld_reason": "",
                "methodology_version": "1.0.0",
                "published_at": "2026-09-04T14:05:00Z", "superseded_at": "",
                "revision_reason": "", "snapshot": snapshot.name,
            }
        ],
    )
    return tmp_path


def test_a_rebuilt_store_has_no_contributions(archive, tmp_path):
    """Documents the gap rather than asserting it is fine.

    If this ever starts failing because rebuild learned to restore
    contributions, the fallback below becomes redundant and can go.
    """
    store = Store(tmp_path / "t.db")
    rebuild(store, archive)
    from datetime import date

    assert store.contributions("GIX-H100", date(2026, 9, 4), 0) == []
    store.close()


def test_the_breakdown_is_recoverable_from_the_archive(archive):
    """The fallback: derive the contributions from the inputs the tape names."""
    est, detail = estimate_from_archive(archive, "GIX-H100", "2026-09-04")
    assert est is not None, detail
    assert detail.endswith(".jsonl.gz")
    assert len(est.providers) == 6
    assert est.value == pytest.approx(3.125, abs=0.005)
    assert sum(p.weight for p in est.contributing) > 0


def test_a_missing_snapshot_reports_rather_than_crashes(archive):
    for path in (archive / "snapshots").glob("*.jsonl.gz"):
        path.unlink()
    est, detail = estimate_from_archive(archive, "GIX-H100", "2026-09-04")
    assert est is None
    assert "missing from the archive" in detail


def test_an_unknown_date_reports_rather_than_crashes(archive):
    est, detail = estimate_from_archive(archive, "GIX-H100", "2020-01-01")
    assert est is None
    assert "no tape row" in detail


def test_a_methodology_change_refuses_to_explain_the_old_value(archive):
    """Recomputing under different rules would explain it under rules it was
    not produced by, which is worse than declining."""
    tape = archive / "series" / "index_values.csv"
    tape.write_text(tape.read_text(encoding="utf8").replace("1.0.0", "0.9.0"), encoding="utf8")
    est, detail = estimate_from_archive(archive, "GIX-H100", "2026-09-04")
    assert est is None
    assert "methodology" in detail
