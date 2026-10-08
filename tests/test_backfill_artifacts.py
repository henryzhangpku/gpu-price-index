"""The committed back-series rebuilds from the committed archive cache.

``gpuidx backfill`` without ``--collect`` reads only backfill/cdx/ and
backfill/archive/, which are committed, so every value in
series/backfill_ratecards*.csv can be reproduced offline. These tests rebuild
everything in memory and compare it with what is on disk, byte for byte.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest

from gpuidx.backfill.aggregate import LABEL, MIN_PROVIDERS, SERIES_COLUMNS
from gpuidx.backfill.build import MONTHLY_NAME, WEEKLY_NAME, build_outputs
from gpuidx.backfill.parsers import PARSERS
from gpuidx.backfill.sources import SOURCE_BY_NAME, SOURCES

ROOT = Path(__file__).resolve().parents[1]


def _lf(text: str) -> str:
    return text.replace("\r\n", "\n")


@pytest.fixture(scope="module")
def rebuilt() -> dict[str, str]:
    return build_outputs(ROOT)


def test_every_source_names_a_registered_parser():
    assert {s.parser for s in SOURCES} <= set(PARSERS)
    assert len({s.name for s in SOURCES}) == len(SOURCES)


def test_rebuilding_from_the_committed_cache_reproduces_the_committed_csvs(rebuilt):
    for rel, text in rebuilt.items():
        committed = _lf((ROOT / rel).read_text(encoding="utf-8"))
        assert committed == text, f"{rel} does not rebuild from the archive cache"


def test_the_backfill_never_writes_the_live_tape(rebuilt):
    assert not any(rel.endswith("index_values.csv") for rel in rebuilt)
    assert f"series/{MONTHLY_NAME}" in rebuilt and f"series/{WEEKLY_NAME}" in rebuilt


def test_committed_series_have_the_documented_shape(rebuilt):
    for name in (MONTHLY_NAME, WEEKLY_NAME):
        rows = list(csv.DictReader(io.StringIO(rebuilt[f"series/{name}"])))
        assert list(rows[0]) == SERIES_COLUMNS
        assert all(r["label"] == LABEL for r in rows)
        for r in rows:
            if r["status"] == "published":
                assert int(r["provider_count"]) >= MIN_PROVIDERS and r["value"]
            else:
                assert r["value"] == "" and r["withheld_reason"]


def test_every_observation_traces_to_a_cached_snapshot(rebuilt):
    manifest = list(csv.DictReader(io.StringIO(rebuilt["backfill/manifest.csv"])))
    cached = {(m["source"], m["captured"]) for m in manifest}
    for m in manifest:
        assert (ROOT / m["cache_path"]).exists()
        assert m["snapshot_url"].startswith(f"https://web.archive.org/web/{m['captured']}id_/")
    obs = list(csv.DictReader(io.StringIO(rebuilt["backfill/observations.csv"])))
    assert obs
    assert all((o["source"], o["captured"]) in cached for o in obs)
    assert {o["source"] for o in obs} <= set(SOURCE_BY_NAME)


def test_every_discard_carries_a_reason(rebuilt):
    obs = list(csv.DictReader(io.StringIO(rebuilt["backfill/observations.csv"])))
    for o in obs:
        if o["outcome"] == "discarded":
            assert o["discard_code"] and o["discard_detail"]
        else:
            assert o["outcome"] == "restated" and float(o["restated_per_gpu_hour"]) > 0
