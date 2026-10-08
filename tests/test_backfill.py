"""The reconstructed back-series: reading, restating, aggregating, reproducing.

The backfill is not the index (docs/BACKFILL.md). These tests pin the
properties that make it honest rather than merely plausible: it only reads
what a page states, it restates through the live index's own normaliser, it
withholds rather than guesses on a thin panel, it never files a page under a
date the archive did not serve, and it rebuilds byte for byte from the
committed cache.
"""

from __future__ import annotations

import gzip
from datetime import date
from pathlib import Path

import httpx
import pytest

from gpuidx.backfill.aggregate import (
    LABEL,
    MIN_PROVIDERS,
    build_series,
    month_of,
    month_range,
    robust_dispersion,
    week_of,
    week_range,
)
from gpuidx.backfill.build import select
from gpuidx.backfill.parsers.common import classify
from gpuidx.backfill.records import RateCardRow
from gpuidx.backfill.restate import restate
from gpuidx.backfill.sources import Source
from gpuidx.backfill.wayback import (
    Capture,
    Fetcher,
    fetch_snapshot,
    parse_cdx,
    read_cached,
    snapshot_path,
    usable,
)
from gpuidx.models import Commitment, FormFactor, Interconnect, PriceKind

ROOT = Path(__file__).resolve().parents[1]


def row(
    source="p1",
    captured="20250115120000",
    model="H100 SXM",
    count=8,
    per_gpu=3.0,
    commitment=Commitment.ON_DEMAND,
    ff=FormFactor.SXM,
    ic=Interconnect.NVLINK,
    **kw,
) -> RateCardRow:
    return RateCardRow(
        source=source,
        captured=captured,
        sku=f"{count}x {model}",
        gpu_model=model,
        gpu_count=count,
        price_per_instance_hour=per_gpu * count,
        commitment=commitment,
        form_factor=ff,
        interconnect=ic,
        **kw,
    )


# -- reading the page's words ---------------------------------------------


@pytest.mark.parametrize(
    ("text", "model", "ff"),
    [
        ("NVIDIA H100 SXM", "H100 SXM", FormFactor.SXM),
        ("H100 80GB SXM5", "H100 SXM", FormFactor.SXM),
        ("NVIDIA HGX H100", "H100 SXM", FormFactor.SXM),
        ("1x NVIDIA H100 PCIe", "H100 PCIe", FormFactor.PCIE),
        ("NVIDIA H100", "H100", FormFactor.UNKNOWN),
        ("NVIDIA B200 SXM6", "B200 SXM", FormFactor.SXM),
        ("H200 SXM", "H200 SXM", FormFactor.SXM),
        ("A100 SXM4 80GB", "A100 SXM", FormFactor.SXM),
        ("A100 PCIe 40GB", "A100 PCIe 40GB", FormFactor.PCIE),
    ],
)
def test_classify_reads_only_what_the_label_states(text, model, ff):
    gpu = classify(text)
    assert gpu is not None
    assert gpu.model == model
    assert gpu.form_factor == ff


@pytest.mark.parametrize("text", ["NVIDIA GH200", "GB200 NVL72", "H800", "RTX A6000", "B300"])
def test_classify_refuses_neighbouring_products(text):
    assert classify(text) is None


def test_nvl_is_passed_through_labelled_so_the_live_screen_can_reject_it():
    gpu = classify("H100 NVL")
    assert gpu is not None and gpu.model == "H100 NVL"
    kept, dropped = restate([row(model=gpu.model, count=1, ff=gpu.form_factor, ic=gpu.interconnect)])
    assert not kept
    assert dropped[0].code == "product_identity"


def test_a_stated_nvlink_fabric_is_recorded_without_inferring_sxm():
    gpu = classify("A100 80GB NVLINK")
    assert gpu.form_factor == FormFactor.UNKNOWN
    assert gpu.interconnect == Interconnect.NVLINK


# -- restating through the live normaliser --------------------------------


def test_a_conforming_8x_sxm_on_demand_row_is_restated_at_its_own_price():
    kept, dropped = restate([row(per_gpu=2.99)])
    assert not dropped
    assert kept[0].index_code == "GIX-H100"
    assert kept[0].quote.normalized_usd_per_gpu_hour == pytest.approx(2.99)


def test_a_single_pcie_card_carries_the_live_schedule_factors():
    kept, _ = restate([row(model="H100 PCIe", count=1, per_gpu=2.00, ff=FormFactor.PCIE, ic=Interconnect.NONE)])
    # form factor 1.18, no fabric 1.15, 1-GPU node 0.92 -- METHODOLOGY section 4.
    assert kept[0].quote.normalized_usd_per_gpu_hour == pytest.approx(2.00 * 1.18 * 1.15 * 0.92)


def test_reserved_spot_and_community_never_reach_the_headline():
    rows = [
        row(commitment=Commitment.RESERVED, per_gpu=1.89),
        row(commitment=Commitment.SPOT, per_gpu=1.50),
        row(commitment=Commitment.COMMUNITY, per_gpu=2.20),
    ]
    kept, dropped = restate(rows)
    assert not kept
    assert {d.code for d in dropped} == {"not_on_demand"}


def test_a_teaser_floor_is_rejected_by_the_live_screen_with_its_reason():
    kept, dropped = restate([row(price_kind=PriceKind.FROM_FLOOR)])
    assert not kept and dropped[0].code == "from_floor"


def test_a_gpu_component_price_is_discarded_rather_than_completed():
    kept, dropped = restate([row(basis="gpu_component")])
    assert not kept and dropped[0].code == "component_price"


def test_a_40gb_a100_is_not_priced_into_the_80gb_contract():
    gpu = classify("A100 SXM4 40GB")
    kept, dropped = restate([row(model=gpu.model, vram_gb=40, per_gpu=1.29)])
    assert not kept and dropped[0].code == "product_identity"


# -- aggregation and the gate ---------------------------------------------


def _series(rows, periods=("2025-01",)):
    kept, dropped = restate(rows)
    out = build_series(kept, dropped, list(periods), month_of, "1.1.0")
    return {(r["period"], r["index_code"]): r for r in out}


def test_one_vote_per_provider_and_the_value_is_the_median_of_votes():
    rows = [
        # p1 lists the same box at three sizes; it still gets one vote (3.0).
        row(source="p1", per_gpu=3.0),
        row(source="p1", per_gpu=3.0, count=8),
        row(source="p1", per_gpu=9.0, count=8),
        row(source="p2", per_gpu=2.0),
        row(source="p3", per_gpu=4.0),
    ]
    s = _series(rows)[("2025-01", "GIX-H100")]
    assert s["status"] == "published"
    assert s["provider_count"] == 3
    assert s["observation_count"] == 5
    assert float(s["value"]) == pytest.approx(3.0)
    assert s["providers"] == "p1=3.000;p2=2.000;p3=4.000"


def test_fewer_than_three_providers_withholds_and_still_writes_the_row():
    s = _series([row(source="p1"), row(source="p2")])[("2025-01", "GIX-H100")]
    assert MIN_PROVIDERS == 3
    assert s["status"] == "withheld"
    assert s["value"] == ""
    assert s["withheld_reason"] == "min_providers: 2 of 3 required"
    assert s["provider_count"] == 2


def test_an_empty_period_is_a_written_gap_not_a_missing_row():
    out = _series([row(source="p1")], periods=("2025-01", "2025-02"))
    gap = out[("2025-02", "GIX-H100")]
    assert gap["status"] == "withheld" and gap["provider_count"] == 0
    assert set(out) == {(p, c) for p in ("2025-01", "2025-02") for c in ("GIX-H100", "GIX-H200", "GIX-B200", "GIX-A100")}


def test_dispersion_is_flagged_above_the_live_ceiling_but_never_gates():
    rows = [row(source="p1", per_gpu=1.0), row(source="p2", per_gpu=3.0), row(source="p3", per_gpu=9.0)]
    s = _series(rows)[("2025-01", "GIX-H100")]
    assert s["status"] == "published"
    assert float(s["dispersion"]) > 0.45
    assert "above the live ceiling" in s["flags"]


def test_side_columns_carry_non_on_demand_prices_unrestated():
    rows = [
        row(source="p1"),
        row(source="p1", commitment=Commitment.RESERVED, per_gpu=1.80),
        row(source="p2", commitment=Commitment.RESERVED, per_gpu=2.20),
        row(source="p3", commitment=Commitment.SPOT, per_gpu=1.40),
    ]
    s = _series(rows)[("2025-01", "GIX-H100")]
    assert float(s["reserved_raw_median"]) == pytest.approx(2.00)
    assert s["reserved_providers"] == 2
    assert float(s["spot_raw_median"]) == pytest.approx(1.40)
    assert s["raw_on_demand_median"] == "3.0000"


def test_every_row_carries_the_label_and_the_methodology_version():
    out = _series([row()])
    assert all(r["label"] == LABEL and r["methodology_version"] == "1.1.0" for r in out.values())
    assert "not the index" in LABEL and "list prices, not transactions" in LABEL


def test_robust_dispersion_needs_three_votes():
    assert robust_dispersion([1.0, 2.0]) is None
    assert robust_dispersion([2.0, 2.0, 2.0]) == 0.0


def test_weeks_are_iso_weeks_named_by_their_monday():
    assert week_of("20260101120000") == "2025-12-29"
    assert week_of("20260105000000") == "2026-01-05"
    weeks = week_range(date(2026, 1, 1), date(2026, 1, 20))
    assert weeks == ["2025-12-29", "2026-01-05", "2026-01-12", "2026-01-19"]


def test_month_range_is_inclusive():
    assert month_range("2025-11", "2026-02") == ["2025-11", "2025-12", "2026-01", "2026-02"]


# -- selection and the archive --------------------------------------------


def _cap(ts, status="200", mime="text/html", digest="D"):
    return Capture(timestamp=ts, original="https://example.com/pricing", statuscode=status, digest=digest, mimetype=mime)


def test_parse_cdx_reads_the_header_row():
    rows = [
        ["timestamp", "original", "mimetype", "statuscode", "digest"],
        ["20250101000000", "https://x/pricing", "text/html", "200", "AAA"],
    ]
    (c,) = parse_cdx(rows)
    assert c.timestamp == "20250101000000" and c.digest == "AAA" and c.month == "2025-01"


def test_only_archived_200_html_captures_are_usable():
    caps = [
        _cap("20250101000000"),
        _cap("20250102000000", status="-", mime="warc/revisit"),
        _cap("20250103000000", status="301"),
        _cap("20250104000000", mime="application/json"),
    ]
    assert [c.timestamp for c in usable(caps)] == ["20250101000000"]


def test_selection_is_one_per_week_with_later_captures_as_fallbacks():
    src = Source(name="x", display="x", urls=("x",), parser="lambda")
    caps = [_cap(ts) for ts in ("20250106010000", "20250107010000", "20250108010000", "20250109010000", "20250114010000")]
    groups = select(caps, src)
    assert [[c.timestamp for c in g] for g in groups] == [
        ["20250106010000", "20250107010000", "20250108010000"],
        ["20250114010000"],
    ]


def test_selection_respects_the_window():
    src = Source(name="x", display="x", urls=("x",), parser="lambda", first="202502", last="202502")
    caps = [_cap("20250115000000"), _cap("20250215000000"), _cap("20250315000000")]
    assert [g[0].timestamp for g in select(caps, src)] == ["20250215000000"]


def _fetcher(handler) -> Fetcher:
    return Fetcher(client=httpx.Client(transport=httpx.MockTransport(handler)), min_interval=0)


def test_a_redirect_to_another_capture_is_refused(tmp_path):
    asked = "20230126220029"

    def handler(request):
        return httpx.Response(302, headers={"location": "https://web.archive.org/web/20230131002022id_/https://example.com/pricing"})

    cap = _cap(asked)
    assert fetch_snapshot(_fetcher(handler), tmp_path, "x", cap) is None
    assert not snapshot_path(tmp_path, "x", cap).exists()


def test_a_redirect_that_keeps_the_timestamp_is_followed_and_cached(tmp_path):
    ts = "20250101000000"

    def handler(request):
        if "http://" in str(request.url).split("id_/")[1]:
            return httpx.Response(200, content=b"<html>$2.99</html>")
        return httpx.Response(302, headers={"location": f"https://web.archive.org/web/{ts}id_/http://example.com/pricing"})

    path = fetch_snapshot(_fetcher(handler), tmp_path, "x", _cap(ts))
    assert path is not None and read_cached(path) == b"<html>$2.99</html>"
    # Cached bytes are reused without a request.
    assert fetch_snapshot(_fetcher(lambda r: pytest.fail("network")), tmp_path, "x", _cap(ts)) == path


def test_cached_gzip_is_a_pure_function_of_the_content(tmp_path):
    def handler(request):
        return httpx.Response(200, content=b"same bytes")

    a = fetch_snapshot(_fetcher(handler), tmp_path / "a", "x", _cap("20250101000000"))
    b = fetch_snapshot(_fetcher(handler), tmp_path / "b", "x", _cap("20250101000000"))
    assert a.read_bytes() == b.read_bytes()
    assert gzip.decompress(a.read_bytes()) == b"same bytes"


def test_matched_change_ignores_entry_and_exit_and_needs_three_matches():
    jan = [row(source=p, captured="20250115120000", per_gpu=v) for p, v in (("p1", 2.0), ("p2", 3.0), ("p3", 4.0))]
    # p4 enters at a high price and p3 leaves: the level moves, the matched
    # change is the median of p1 and p2's own moves... which is two, not three.
    feb = [row(source=p, captured="20250215120000", per_gpu=v) for p, v in (("p1", 2.2), ("p2", 3.3), ("p4", 9.0))]
    s = _series(jan + feb, periods=("2025-01", "2025-02"))
    assert s[("2025-02", "GIX-H100")]["matched_providers"] == 2
    assert s[("2025-02", "GIX-H100")]["matched_log_change"] == ""
    mar = [row(source=p, captured="20250315120000", per_gpu=v) for p, v in (("p1", 2.2), ("p2", 3.3), ("p4", 9.9))]
    s = _series(jan + feb + mar, periods=("2025-01", "2025-02", "2025-03"))
    m = s[("2025-03", "GIX-H100")]
    assert m["matched_providers"] == 3
    # p1 and p2 unchanged, p4 up 10%: the median log change is zero.
    assert float(m["matched_log_change"]) == pytest.approx(0.0)
