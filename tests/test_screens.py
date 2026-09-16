"""The 1.1.0 screens: is this observation evidence about the benchmark good at all?

Each one was motivated by something in the archive, and each test names it.
All of them are off under 1.0.0, which is how the historical series stays
reproducible; the tests pin that too.
"""

from __future__ import annotations

import pytest

from gpuidx.models import PriceKind, RawObservation
from gpuidx.normalize import (
    Rejection,
    hold_out_thin_books,
    match_contract,
    normalize,
    prepare_quotes,
    product_identity,
    vram_readings,
)
from gpuidx.spec import METHODOLOGIES, PRODUCT_IDENTITY_RULES, Gates, Methodology, Screens

V100 = METHODOLOGIES["1.0.0"]
V110 = METHODOLOGIES["1.1.0"]


def with_vram(obs: RawObservation, vram: int | None) -> RawObservation:
    return obs.model_copy(update={"vram_gb": vram})


# -- product identity -------------------------------------------------------


def test_the_a100_40gb_was_priced_into_the_80gb_index(make_obs):
    """The 2026-09-16 snapshot: 17 Shadeform 'A100' rows at 40 GB, all in GIX-A100."""
    forty = with_vram(make_obs(gpu_model="A100", price_per_gpu=1.2), 40)
    assert normalize(forty, V100).index_code == "GIX-A100"
    with pytest.raises(Rejection) as caught:
        normalize(forty, V110)
    assert caught.value.code == "product_identity"
    assert "40 GB" in caught.value.detail


def test_a_labelled_variant_is_rejected_with_the_reason_not_as_unmatched(make_obs):
    nvl = make_obs(gpu_model="H100_nvl", price_per_gpu=2.0)
    with pytest.raises(Rejection) as caught:
        normalize(nvl, V110)
    assert caught.value.code == "product_identity"
    assert "NVL" in caught.value.detail
    # Under 1.0.0 the same listing was accepted and adjusted as a PCIe card.
    assert normalize(nvl, V100).index_code == "GIX-H100"


def test_a_node_total_is_read_as_a_node_total(make_obs):
    """DataCrunch lists a 2x H100 as 160 GB. That is 80 per card, not a different product."""
    node = with_vram(make_obs(gpu_model="H100 SXM5 80GB", gpu_count=2), 160)
    assert vram_readings(node) == {160.0, 80.0}
    assert product_identity(node, match_contract(node)) is None
    assert normalize(node, V110).index_code == "GIX-H100"


def test_a_node_total_that_matches_no_reading_is_still_rejected(make_obs):
    """DataCrunch 'A100 SXM4 40GB' 8x at 320 GB: 320 is not 80 and 40 is not 80."""
    node = with_vram(make_obs(gpu_model="A100 SXM4 40GB", gpu_count=8), 320)
    assert product_identity(node, match_contract(node)) is not None


def test_vram_inside_tolerance_stands(make_obs):
    """B200 at 192 GB (raw HBM3e) against a 180 GB contract is the same card."""
    b200 = with_vram(make_obs(gpu_model="B200", price_per_gpu=6.0), 192)
    assert normalize(b200, V110).index_code == "GIX-B200"
    h200_nvl = with_vram(make_obs(gpu_model="NVIDIA H200 NVL", price_per_gpu=3.0), 143)
    assert normalize(h200_nvl, V110).index_code == "GIX-H200"


def test_undisclosed_vram_is_tolerated(make_obs):
    bare = with_vram(make_obs(gpu_model="A100"), None)
    assert normalize(bare, V110).index_code == "GIX-A100"


def test_every_identity_rule_cites_its_evidence():
    for code, rules in PRODUCT_IDENTITY_RULES.items():
        for rule in rules:
            assert rule.cited, (code, rule.token)
            assert rule.reason, (code, rule.token)
            assert "2026" in rule.cited, "a rule must name the snapshot that motivated it"


# -- from-floor -------------------------------------------------------------


def test_a_teaser_floor_never_prices(make_obs):
    teaser = make_obs(price_per_gpu=1.99).model_copy(update={"price_kind": PriceKind.FROM_FLOOR})
    with pytest.raises(Rejection) as caught:
        normalize(teaser, V110)
    assert caught.value.code == "from_floor"
    assert normalize(teaser, V100).normalized_usd_per_gpu_hour == pytest.approx(1.99)


def test_price_kind_defaults_to_quoted_for_archived_rows(make_obs):
    """Every snapshot written before the field existed must still load as a real quote."""
    assert make_obs().price_kind == PriceKind.QUOTED
    assert make_obs().currency == "USD"


# -- currency ---------------------------------------------------------------


def test_a_foreign_currency_is_held_out_not_assumed(make_obs):
    euro = make_obs(price_per_gpu=3.0).model_copy(update={"currency": "EUR"})
    with pytest.raises(Rejection) as caught:
        normalize(euro, V110)
    assert caught.value.code == "currency_unsupported"
    assert "EUR" in caught.value.detail
    # 1.0.0 predates the field and reads the number as dollars, which is
    # exactly the assumption 1.1.0 stops making.
    assert normalize(euro, V100).raw_usd_per_gpu_hour == pytest.approx(3.0)


# -- population floors for books --------------------------------------------


def book_rows(make_obs, machines: list[tuple[int, int]], price: float = 3.0):
    """Vast.ai-shaped rows: (machine_id, host_id) per offer."""
    return [
        make_obs(source="vastai", price_per_gpu=price, sku=f"v{i}").model_copy(
            update={"payload": {"machine_id": m, "host_id": h}}
        )
        for i, (m, h) in enumerate(machines)
    ]


def test_one_host_with_many_boxes_is_one_seller(make_obs):
    """Six rows, six machines, one host. A median over that is that host's rate card."""
    rows = book_rows(make_obs, [(m, 1) for m in range(6)])
    kept, flags = hold_out_thin_books(rows, V110)
    assert kept == []
    assert [f.code for f in flags] == ["book_population_floor"]
    assert "1 distinct hosts of 3 required" in flags[0].detail
    assert flags[0].index_code == "GIX-H100"


def test_too_few_machines_is_held_out(make_obs):
    rows = book_rows(make_obs, [(1, 1), (2, 2), (3, 3)])
    kept, flags = hold_out_thin_books(rows, V110)
    assert kept == []
    assert "3 distinct machines of 4 required" in flags[0].detail


def test_a_populated_book_prices(make_obs):
    rows = book_rows(make_obs, [(1, 1), (2, 2), (3, 3), (4, 4)])
    kept, flags = hold_out_thin_books(rows, V110)
    assert len(kept) == 4
    assert flags == []


def test_a_book_that_cannot_prove_its_population_is_held_out(make_obs):
    """Fail closed: rows with no ids at all are not a rate card, they are an unproven book."""
    rows = [make_obs(source="vastai", sku=f"v{i}") for i in range(10)]
    kept, flags = hold_out_thin_books(rows, V110)
    assert kept == []
    assert "population is unproven" in flags[0].detail


def test_a_rate_card_is_not_a_book(make_obs):
    """A price list with one row per SKU is one seller by construction; no floor applies."""
    rows = [make_obs(source="runpod", sku=f"r{i}") for i in range(2)]
    kept, flags = hold_out_thin_books(rows, V110)
    assert len(kept) == 2
    assert flags == []


def test_floors_are_per_index(make_obs):
    """A thin H100 book does not hold the same venue out of an index where it is deep."""
    h100 = book_rows(make_obs, [(1, 1)])
    h200 = [
        r.model_copy(update={"gpu_model": "H200"})
        for r in book_rows(make_obs, [(m, m) for m in range(10, 15)])
    ]
    kept, flags = hold_out_thin_books(h100 + h200, V110)
    assert {r.gpu_model for r in kept} == {"H200"}
    assert [f.index_code for f in flags] == ["GIX-H100"]


def test_floors_are_off_under_the_launch_methodology(make_obs):
    rows = [make_obs(source="vastai", sku=f"v{i}") for i in range(3)]
    kept, flags = hold_out_thin_books(rows, V100)
    assert len(kept) == 3
    assert flags == []


def test_the_hold_out_reaches_the_estimate_through_prepare_quotes(make_obs):
    rows = book_rows(make_obs, [(m, 1) for m in range(6)])
    quotes, flags = prepare_quotes(rows, V110)
    assert quotes == []
    assert any(f.code == "book_population_floor" for f in flags)


# -- the screens are a property of the version ------------------------------


def test_the_launch_methodology_has_every_screen_off():
    assert V100.screens == Screens()
    assert V100.gates.min_book_machines == 0
    assert V100.gates.min_book_hosts == 0


def test_a_custom_methodology_can_turn_one_screen_on(make_obs):
    only_identity = Methodology(
        version="t", screens=Screens(product_identity=True), gates=Gates()
    )
    forty = with_vram(make_obs(gpu_model="A100"), 40)
    with pytest.raises(Rejection):
        normalize(forty, only_identity)
    euro = make_obs().model_copy(update={"currency": "EUR"})
    assert normalize(euro, only_identity).index_code == "GIX-H100"
