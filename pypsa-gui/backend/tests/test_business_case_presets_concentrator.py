# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""BC-ALL slice 13: the mining concentrator preset (spec §6.5).

Revenue reads shipped t, not mill output (LO-2); a shortfall lowers revenue once (G3)."""

from __future__ import annotations

import pytest

from models.business_case import LineItem
from services.business_case import linked_outputs as L
from services.business_case import presets as P
from services.finance.business_case import build_business_case
from services.finance.case import TemplateLine
from services.finance.engine import run_case
from tests.test_business_case_engine import PACK, _ic
from tests.test_business_case_guards import OPS, _by_year
from tests.test_business_case_linked_outputs import _built

T = "eh_concentrator_staged"
SHIPPED = "linked:shipped.shipments@1"


def _fill() -> P.BusinessCaseInputs:
    case = P.preset(T, "concentrate_sales", currency="USD")
    rows = []
    for ln in case.lines:
        d = ln.model_dump()
        if ln.kind == "revenue":
            d.update(price=1500.0, indexation=0.0)
        elif ln.kind == "capex":
            d.update(quantity=1.0, price=1000.0, life_years=10.0)
        elif ln.category in ("insurance", "maintenance"):
            d.update(quantity=0.01)
        elif ln.name == "Staff":
            d.update(quantity=2.0, price=50.0)
        elif ln.name == "Process plant cost":
            d.update(quantity=1000.0, price=8.0)
        else:
            d.update(price=20.0)
        rows.append(LineItem.model_validate(d))
    return case.model_copy(update={"lines": rows})


def test_the_rows_are_spec_6_5():
    case = P.preset(T, "concentrate_sales", currency="EUR")
    by = {ln.name: ln for ln in case.lines}
    assert case.preset == "eh_concentrator_staged.concentrate_sales@1"
    sale = by["Concentrate sales (net)"]
    assert (sale.kind, sale.category, sale.unit) == ("revenue", "product_sale", "t")
    assert sale.quantity_source == SHIPPED and sale.price is None
    assert "shipped" in sale.note and "TC/RC" in sale.note
    plant = by["Process plant cost"]
    assert (plant.kind, plant.category, plant.unit) == ("opex", "consumables", "t")
    assert plant.quantity_source == "typed" and "milled" in plant.note
    freight = by["Freight"]
    assert (freight.category, freight.quantity_source) == ("other", SHIPPED)
    assert {ln.category for ln in case.lines if ln.kind == "opex"} >= {
        "staff",
        "maintenance",
        "insurance",
    }
    assert all(ln.price is None for ln in case.lines if ln.kind == "revenue")


def test_every_linked_id_exists_on_the_built_template():
    n, solve = _built(T)
    ids = {s.id for s in L.catalogue(n, solve)}
    case = P.preset(T, "concentrate_sales", currency="EUR")
    assert "shipped.shipments" in ids
    assert {
        ln.quantity_source
        for ln in case.lines
        if ln.quantity_source.startswith("linked:")
    } == {SHIPPED}


def test_lo2_revenue_reads_shipped_tonnes_not_mill_output():
    inputs = _fill()
    sale = next(ln for ln in inputs.lines if ln.name == "Concentrate sales (net)")
    shipped = L.Series("shipped.shipments", "t", {None: 9000.0}, {})
    ic = _ic(lines=(TemplateLine("fom", "fom", -20.0, "opex"),), analysis_years=8)
    bc = build_business_case(ic, inputs, {sale.name: shipped, "Freight": shipped})
    for y in OPS:
        assert _by_year(bc)[y][sale.name].amount == pytest.approx(1500.0 * 9000.0)


def _run(served: float):
    inputs = _fill()
    lp = TemplateLine(
        "asset:opex:Generator:__lost_conc",
        "lost_production",
        -90.0,
        "fuel",
        source="asset",
        source_id="opex:Generator:__lost_conc",
    )
    ic = _ic(lines=(lp, TemplateLine("fom", "fom", -20.0, "opex")), analysis_years=8)
    lpm = L.LostProductionMap(
        slack_bus={"__lost_conc": "conc"},
        load_bus={"shipments": "conc"},
        share={"__lost_conc": {"shipments": L.Series("x", "share", {None: 1.0}, {})}},
        unread={},
    )
    series = {
        ln.name: L.Series("shipped.shipments", "t", {None: served}, {})
        for ln in inputs.lines
        if ln.quantity_source.startswith("linked:")
    }
    bc = build_business_case(ic, inputs, series, lpm)
    run_case(bc.case, PACK)
    return bc


def test_g3_a_shortfall_lowers_revenue_once():
    full, short = _run(1000.0), _run(700.0)
    name = "Concentrate sales (net)"
    for y in OPS:
        assert _by_year(short)[y][name].amount == pytest.approx(
            _by_year(full)[y][name].amount * 0.7
        )
        # The concentrate bus's penalty is dropped, not charged on top; other lines stay.
        assert not [k for k in _by_year(short)[y] if "__lost_" in k]
        assert "fom" in _by_year(short)[y]


def test_f3_shares_have_a_plant_capex_base_and_each_capex_is_in_one_maintenance_base():
    lines = _fill().lines
    shares = [ln for ln in lines if ln.source == "share_of"]
    assert shares
    for ln in shares:
        base = [b for b in lines if b.kind == "capex"]
        assert [b for b in base if b.refresh_years is None], ln.name
    maint = [ln for ln in shares if ln.category == "maintenance"]
    assert len(maint) <= 1
