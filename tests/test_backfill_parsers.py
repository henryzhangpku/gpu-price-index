"""Parser tests on saved fixtures.

Every file under tests/fixtures/backfill/ is a verbatim excerpt of a real
Internet Archive capture -- the comment at its head names the capture URL,
its timestamp and CDX digest, and the two text markers it was cut between.
Nothing inside a cut was edited. The figures asserted here were read off the
archived pages by eye when the fixtures were cut, so a test failing means the
parser no longer reads what the page says.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gpuidx.backfill.parsers import PARSERS
from gpuidx.backfill.records import RateCardRow
from gpuidx.backfill.restate import restate
from gpuidx.models import Commitment, FormFactor, Interconnect, PriceKind

FIXTURES = Path(__file__).parent / "fixtures" / "backfill"


def parse(name: str) -> list[RateCardRow]:
    path = FIXTURES / f"{name}.html"
    source, captured = path.stem.split("_")
    return list(dict.fromkeys(PARSERS[source](path.read_bytes(), captured, source)))


def pick(rows, model, commitment=Commitment.ON_DEMAND, count=None):
    out = [r for r in rows if r.gpu_model == model and r.commitment == commitment]
    if count is not None:
        out = [r for r in out if r.gpu_count == count]
    return out


def gpu_hour(rows) -> list[float]:
    return sorted(round(r.price_per_gpu_hour, 4) for r in rows)


def test_every_fixture_is_labelled_as_a_real_archived_excerpt():
    files = sorted(FIXTURES.glob("*.html"))
    assert len(files) >= 15
    for f in files:
        head = f.read_text(encoding="utf-8").splitlines()[0]
        assert head.startswith("<!-- FIXTURE. A verbatim excerpt of real archived HTML, not synthetic")


# -- Lambda -----------------------------------------------------------------


def test_lambda_2023_instance_prices_and_the_reserved_table():
    rows = parse("lambda_20230909194011")
    (pcie,) = pick(rows, "H100 PCIe")
    assert (pcie.gpu_count, pcie.price_per_instance_hour) == (1, 1.99)
    (sxm,) = pick(rows, "H100 SXM")
    assert (sxm.gpu_count, sxm.price_per_instance_hour) == (8, 20.72)
    # "$1.89/H100/hour | 3-years" and "$4.85/H100/hour | 3-months" are reserved.
    assert gpu_hour(pick(rows, "H100 SXM", Commitment.RESERVED)) == [1.89, 4.85]


def test_lambda_2024_per_gpu_prices_with_the_on_demand_prefix():
    rows = parse("lambda_20241103180910")
    assert {r.gpu_count: r.price_per_gpu_hour for r in pick(rows, "H100 SXM")} == {
        8: 2.99, 4: 3.09, 2: 3.19, 1: 3.29,
    }
    # A 40 GB A100 is labelled as such so the identity screen can refuse it.
    assert {r.vram_gb for r in rows if r.gpu_model.startswith("A100")} == {40, 80}


def test_lambda_2026_counts_come_from_the_tab_strip():
    rows = parse("lambda_20260109103934")
    (b200,) = pick(rows, "B200 SXM")
    assert (b200.gpu_count, b200.price_per_gpu_hour) == (8, 4.99)
    (h100,) = pick(rows, "H100 SXM")
    assert (h100.gpu_count, h100.price_per_instance_hour) == (8, 23.92)


# -- CoreWeave and FluidStack: the a-la-carte GPU component ----------------


def test_coreweave_a_la_carte_is_a_component_price_and_is_discarded():
    rows = parse("coreweave_20230921194906")
    (hgx,) = pick(rows, "H100 SXM")
    assert hgx.price_per_instance_hour == 4.76 and hgx.basis == "gpu_component"
    kept, dropped = restate(rows)
    assert not [k for k in kept if k.index_code == "GIX-H100"]
    assert {d.code for d in dropped if d.index_code == "GIX-H100"} == {"component_price"}


def test_fluidstack_2023_bills_the_host_separately():
    rows = parse("fluidstack_20230930024119")
    assert {r.basis for r in rows} == {"gpu_component"}
    assert gpu_hour(pick(rows, "H100 SXM")) == [4.76]
    assert gpu_hour(pick(rows, "H100 SXM", Commitment.RESERVED)) == [2.89]


def test_fluidstack_2025_a_placeholder_keeps_its_column():
    rows = parse("fluidstack_20250109013724")
    # "Nvidia H200 | 141 | 48 | 384 | n/a | $2.45": the $2.45 is the 1-year
    # price, not the on-demand one.
    assert not pick(rows, "H200")
    assert gpu_hour(pick(rows, "H200", Commitment.RESERVED)) == [2.45]
    assert gpu_hour(pick(rows, "H100 PCIe")) == [2.89]
    assert {r.basis for r in rows} == {"instance"}


# -- DataCrunch ---------------------------------------------------------------


def test_datacrunch_an_unlabelled_column_keeps_the_spot_column_in_place():
    rows = parse("datacrunch_20250808081801")
    on = {r.gpu_count: r.price_per_instance_hour for r in pick(rows, "B200 SXM")}
    assert on == {1: 3.64, 2: 7.27, 4: 14.55, 8: 29.10}
    # "Dynamic price" ($2.68 for 1x) is skipped; spot is the third column.
    spot = {r.gpu_count: r.price_per_instance_hour for r in pick(rows, "B200 SXM", Commitment.SPOT)}
    assert spot[1] == 0.80


# -- Hyperstack ---------------------------------------------------------------


def test_hyperstack_two_column_table_and_its_reservation_floor():
    rows = parse("hyperstack_20240822101707")
    (sxm,) = pick(rows, "H100 SXM")
    assert sxm.price_per_gpu_hour == 3.75
    (floor,) = pick(rows, "H100 SXM", Commitment.RESERVED)
    assert floor.price_per_gpu_hour == 2.25 and floor.price_kind == PriceKind.FROM_FLOOR


def test_hyperstack_2025_sections_and_an_unstated_form_factor():
    rows = parse("hyperstack_20250724001416")
    assert gpu_hour(pick(rows, "H100 SXM")) == [2.40]
    assert gpu_hour(pick(rows, "H200 SXM")) == [3.50]
    assert gpu_hour(pick(rows, "H100 SXM", Commitment.RESERVED)) == [2.04]
    (bare,) = [r for r in pick(rows, "H100") if r.interconnect == Interconnect.UNKNOWN]
    assert bare.form_factor == FormFactor.UNKNOWN and bare.price_per_gpu_hour == 1.90


# -- Nebius -------------------------------------------------------------------


def test_nebius_2024_instance_rows_carry_their_count():
    rows = parse("nebius_20241115043809")
    on = {r.gpu_count: r.price_per_instance_hour for r in pick(rows, "H100 SXM")}
    assert on == {1: 2.95, 8: 23.60}
    assert gpu_hour(pick(rows, "H100 SXM", Commitment.RESERVED)) == [2.0, 2.0]


def test_nebius_2026_preemptible_is_spot_and_on_demand_is_on_demand():
    rows = parse("nebius_20260712235400")
    assert gpu_hour(pick(rows, "H100 SXM")) == [3.85]
    assert gpu_hour(pick(rows, "H100 SXM", Commitment.SPOT)) == [2.15]
    assert gpu_hour(pick(rows, "B200 SXM")) == [7.15]


# -- DigitalOcean, Crusoe, Voltage Park --------------------------------------


def test_digitalocean_cards_reserved_and_on_demand():
    rows = parse("digitalocean_20250707231929")
    assert {r.gpu_count: r.price_per_gpu_hour for r in pick(rows, "H100")} == {8: 2.99, 1: 3.39}
    assert gpu_hour(pick(rows, "H100", Commitment.RESERVED)) == [1.99]


def test_crusoe_split_price_cells_map_to_the_column_strip():
    rows = parse("crusoe_20250521220413")
    assert gpu_hour(pick(rows, "H100 SXM")) == [3.90]
    assert gpu_hour(pick(rows, "H100 SXM", Commitment.RESERVED)) == [2.54, 2.93, 3.12]
    # "Contact Sales" holds the spot column's place for H100.
    assert not pick(rows, "H100 SXM", Commitment.SPOT)


def test_crusoe_one_cell_prices_and_hgx_form():
    rows = parse("crusoe_20251215002446")
    (h100,) = pick(rows, "H100 SXM")
    assert h100.price_per_gpu_hour == 3.90 and h100.form_factor == FormFactor.SXM
    assert gpu_hour(pick(rows, "H100 SXM", Commitment.SPOT)) == [1.60]


def test_voltagepark_cards_state_their_fabric_and_smallest_count():
    rows = parse("voltagepark_20250712040631")
    eth, ib = sorted(pick(rows, "H100 SXM"), key=lambda r: r.gpu_count)
    assert (eth.gpu_count, eth.price_per_gpu_hour, eth.interconnect) == (1, 1.99, Interconnect.ETHERNET)
    assert (ib.gpu_count, ib.price_per_gpu_hour, ib.interconnect) == (8, 2.49, Interconnect.INFINIBAND)


@pytest.mark.parametrize("name", [p.stem for p in sorted(FIXTURES.glob("*.html"))])
def test_every_fixture_restates_without_error_and_reads_something(name):
    rows = parse(name)
    assert rows
    kept, dropped = restate(rows)
    assert len(kept) + len(dropped) == len(rows)


# -- RunPod and Paperspace ----------------------------------------------------


def test_runpod_2023_embedded_records_split_secure_community_and_spot():
    rows = parse("runpod_20230929070808")
    assert gpu_hour(pick(rows, "H100 SXM")) == [4.49]
    assert gpu_hour(pick(rows, "H100 SXM", Commitment.COMMUNITY)) == [4.09]
    assert gpu_hour(pick(rows, "H100 SXM", Commitment.SPOT)) == [2.49]
    assert gpu_hour(pick(rows, "H100 PCIe")) == [4.29]
    assert {r.gpu_count for r in rows} == {1}


def test_runpod_2025_reads_the_row_attributes_not_the_script_filled_zero():
    rows = parse("runpod_20250909131230")
    assert gpu_hour(pick(rows, "H100 SXM")) == [2.69]
    assert gpu_hour(pick(rows, "H100 PCIe")) == [2.39]
    (nvl,) = pick(rows, "H100 NVL")
    kept, dropped = restate([nvl])
    assert not kept and dropped[0].code == "product_identity"


def test_paperspace_skips_the_template_remnant_and_reads_the_footnote():
    rows = parse("paperspace_20240123092147")
    # The H100 card's "$3.09 / hour | NVIDIA A100 GPU" block is not read.
    assert 3.09 not in {r.price_per_gpu_hour for r in rows}
    (od,) = pick(rows, "H100 SXM")
    assert od.price_per_gpu_hour == 6.00 and od.sku == "H100 (footnote)"
    assert gpu_hour(pick(rows, "H100 SXM", Commitment.RESERVED)) == [2.24]
