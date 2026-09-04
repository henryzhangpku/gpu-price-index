"""The exported bundle must agree with the tape it was derived from.

The site is a reader, so the risk it carries is not a wrong price but a
*disagreeing* one: a page that recomputes something subtly different from what
was published would misrepresent the record while looking authoritative. These
tests pin the agreement.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from gpuidx.archive import append_to_tape, write_snapshot
from gpuidx.models import Commitment, FormFactor, Interconnect, RawObservation, Tier
from gpuidx.spec import DEFAULT_GATES
from gpuidx.web import BUNDLE_FILES, build_bundle, build_latest, build_series, write_bundle


def _obs(source: str, price: float, gpus: int = 8, model: str = "H100 SXM") -> RawObservation:
    return RawObservation(
        source=source,
        source_sku=f"{source}-{model}",
        gpu_model=model,
        gpu_count=gpus,
        usd_per_hour_total=price * gpus,
        commitment=Commitment.ON_DEMAND,
        form_factor=FormFactor.SXM,
        interconnect=Interconnect.NVLINK,
        region="us-east",
        tier=Tier.EXECUTABLE,
        observed_at=datetime.now(UTC),
    )


@pytest.fixture
def archive(tmp_path):
    """A minimal archive: one snapshot, one tape row per index."""
    observations = [_obs(f"venue{i}", 3.00 + i * 0.05) for i in range(6)]
    snapshot = write_snapshot(tmp_path, observations)
    append_to_tape(
        tmp_path,
        [
            {
                "index_code": "GIX-H100",
                "index_date": "2026-09-03",
                "revision": 0,
                "status": "published",
                "value": 3.125,
                "provider_count": 6,
                "observation_count": 6,
                "dispersion": 0.05,
                "withheld_reason": "",
                "methodology_version": "1.0.0",
                "published_at": "2026-09-03T14:05:00Z",
                "superseded_at": "",
                "revision_reason": "",
                "snapshot": snapshot.name,
            },
            {
                "index_code": "GIX-MI300X",
                "index_date": "2026-09-03",
                "revision": 0,
                "status": "withheld",
                "value": "",
                "provider_count": 0,
                "observation_count": 0,
                "dispersion": "",
                "withheld_reason": "min_providers: 0 of 4 required",
                "methodology_version": "1.0.0",
                "published_at": "2026-09-03T14:05:00Z",
                "superseded_at": "",
                "revision_reason": "",
                "snapshot": snapshot.name,
            },
        ],
    )
    return tmp_path


def test_bundle_has_every_file(archive):
    bundle = build_bundle(archive)
    assert set(bundle) == set(BUNDLE_FILES)


def test_written_bundle_is_valid_json(archive, tmp_path):
    out = tmp_path / "out"
    written = write_bundle(archive, out)
    assert {p.name for p in written} == set(BUNDLE_FILES)
    for path in written:
        json.loads(path.read_text(encoding="utf-8"))


def test_latest_recomputes_the_published_value(archive):
    """The headline property: the page's number is the tape's number."""
    latest = build_latest(archive, DEFAULT_GATES)
    h100 = latest["indices"]["GIX-H100"]
    assert h100["status"] == "published"
    assert h100["estimate"]["value"] == pytest.approx(h100["published_value"], abs=0.005)


def test_withheld_index_keeps_its_unpublished_candidate(archive):
    """A withheld index still reports what the estimator produced.

    Dropping it would make withholding look like missing data. Showing it, next
    to the gate that stopped it, is the entire argument the site makes.
    """
    latest = build_latest(archive, DEFAULT_GATES)
    mi300x = latest["indices"]["GIX-MI300X"]
    assert mi300x["status"] == "withheld"
    assert mi300x["published_value"] is None
    assert not mi300x["estimate"]["passed"]
    assert any(not g["passed"] for g in mi300x["estimate"]["gates"])


def test_every_contract_appears_even_with_no_quotes(archive):
    latest = build_latest(archive, DEFAULT_GATES)
    assert set(latest["indices"]) == {
        "GIX-H100", "GIX-H200", "GIX-A100", "GIX-B200", "GIX-MI300X"
    }


def test_series_preserves_superseded_revisions(tmp_path):
    """A corrected day must still show what was originally published."""
    rows = [
        {
            "index_code": "GIX-H100", "index_date": "2026-09-01", "revision": 0,
            "status": "published", "value": 3.00, "provider_count": 5,
            "observation_count": 20, "dispersion": 0.1, "withheld_reason": "",
            "methodology_version": "1.0.0", "published_at": "2026-09-01T14:00:00Z",
            "superseded_at": "2026-09-01T18:00:00Z", "revision_reason": "",
            "snapshot": "a.jsonl.gz",
        },
        {
            "index_code": "GIX-H100", "index_date": "2026-09-01", "revision": 1,
            "status": "published", "value": 3.20, "provider_count": 5,
            "observation_count": 20, "dispersion": 0.1, "withheld_reason": "",
            "methodology_version": "1.0.0", "published_at": "2026-09-01T18:00:00Z",
            "superseded_at": "", "revision_reason": "corrected AWS rate card",
            "snapshot": "b.jsonl.gz",
        },
    ]
    append_to_tape(tmp_path, rows)

    series = build_series(tmp_path)
    h100 = series["indices"]["GIX-H100"]

    assert len(h100["revisions"]) == 2
    assert len(h100["live"]) == 1
    assert h100["live"][0]["revision"] == 1
    assert h100["live"][0]["value"] == pytest.approx(3.20)
    assert h100["revisions"][0]["live"] is False


def test_series_counts_withheld_days(archive):
    series = build_series(archive)
    assert series["indices"]["GIX-MI300X"]["withheld_count"] == 1
    assert series["indices"]["GIX-MI300X"]["published_count"] == 0
    assert series["indices"]["GIX-H100"]["published_count"] == 1


def test_empty_archive_does_not_explode(tmp_path):
    """A fresh checkout with no fixings yet should still export."""
    bundle = build_bundle(tmp_path)
    assert bundle["latest.json"]["index_date"] is None
    assert bundle["series.json"]["dates"] == []
    assert bundle["meta.json"]["coverage"]["snapshots"] == 0


def test_meta_publishes_the_constants_the_site_explains(archive):
    meta = build_bundle(archive)["meta.json"]
    assert meta["gates"]["min_providers"] == DEFAULT_GATES.min_providers
    assert meta["adjustments"]["max_total"] == 1.75
    assert [t["tier"] for t in meta["tiers"]] == [1, 2, 3]
    assert meta["tiers"][0]["weight"] == 1.00
    assert len(meta["contracts"]) == 5
