"""BC-ALL slice 11: the H2 hub and ammonia presets (spec §6.3, §6.4; issue 11).

GK B: the lost-production drop is f × s; a cost with no source gets no default. GK A point 3: the billed
series has an identity test against the built network.
"""

from __future__ import annotations

import json

import pytest

from models.business_case import LineItem
from services.business_case import linked_outputs as L
from services.business_case import presets as P
from services.finance.business_case import build_business_case, pnl
from services.finance.case import TemplateLine
from services.finance.engine import run_case
from services.process_products import t_per_unit
from tests.test_business_case_engine import PACK, _ic
from tests.test_business_case_guards import OPS, _by_year
from tests.test_business_case_linked_outputs import (
    _built,
    _value,
)

#: template → (variant, product load, revenue line reading it, O2 links offered)
CASES = {
    "eh_h2_hub": ("h2_offtake", "h2_offtake", "served_h2.h2_offtake", 1),
    "eh_h2_hub_staged": ("h2_offtake", "h2_offtake", "served_h2.h2_offtake", 4),
    "eh_ammonia_staged": ("nh3_sales", "nh3_delivery", "served_product.nh3", 4),
}
PRICES = {"H2 offtake": 4.0, "Production premium": 0.4, "NH3 sales": 900.0}
LIFE = 10.0


def _fill(template: str) -> P.BusinessCaseInputs:
    """The preset with every empty cell typed: prices, the stack lump, staff, insurance, the ASU share."""
    variant = CASES[template][0]
    case = P.preset(template, variant, currency="USD")
    rows = []
    for ln in case.lines:
        d = ln.model_dump()
        if ln.kind == "revenue":
            d.update(price=PRICES[ln.name], indexation=0.0)
        elif ln.name == "Stack replacement":
            d.update(quantity=1.0, price=1000.0, life_years=LIFE)
        elif ln.name == "Staff":
            d.update(quantity=2.0, price=50.0)
        elif ln.name in ("Insurance", "ASU and synthesis-loop O&M"):
            d.update(quantity=0.01)
        rows.append(d)
    return case.model_copy(update={"lines": [LineItem.model_validate(d) for d in rows]})


def _offtake_mwh(n, load) -> dict:
    """The built load's MWh a year, per period: Σ p_set · weight ÷ the period's years (flat networks: key None)."""
    from services.period_utils import (
        period_years_map,
        snapshot_weights,
        years_for_period,
    )

    x = n.get_switchable_as_dense("Load", "p_set")[load] * snapshot_weights(
        n, "generators"
    )
    if not hasattr(x.index, "levels"):
        return {None: float(x.sum()) / years_for_period(period_years_map(n), None)}
    years = period_years_map(n)
    return {
        int(p): float(v) / years_for_period(years, p)
        for p, v in x.groupby(level=0).sum().items()
    }


def _ids(template: str) -> set[str]:
    n, solve = _built(template)
    return {s.id for s in L.catalogue(n, solve)}


@pytest.mark.parametrize("template", CASES)
def test_the_preset_rows_are_spec_6_3_and_6_4(template):
    variant, _load, source, _n_o2 = CASES[template]
    case = P.preset(template, variant, currency="EUR")
    by = {ln.name: ln for ln in case.lines}
    assert case.preset == f"{template}.{variant}@1"
    rev = [ln for ln in case.lines if ln.kind == "revenue"]
    assert all(ln.price is None and not ln.price_by_year for ln in rev)
    assert all(ln.quantity_source == f"linked:{source}@1" for ln in rev)
    first = {"eh_ammonia_staged": "NH3 sales"}.get(template, "H2 offtake")
    assert by[first].category == "product_sale"
    assert by["Stack replacement"].kind == "capex"
    assert by["Stack replacement"].price is None  # no default value (GK B)
    assert not by["Stack replacement"].assumed
    # The one default: the pack's sourced electrolyser fixed O&M share, assumed (GK B: no new pack).
    assert {ln.name: ln.assumed["quantity"].key for ln in case.lines if ln.assumed} == {
        "Non-energy O&M": "electrolysis.investment.fom_share"
    }
    assert by["Staff"].price is None and by["Insurance"].quantity is None
    # O2 is not a line (off by default): it is an offered row per electrolyser.
    assert not [ln for ln in case.lines if ln.category == "by_product"]


def test_the_h2_premium_hint_is_the_hydrogen_bank_range_and_ammonia_the_h2global_lot():
    h2 = {
        ln.name: ln for ln in P.preset("eh_h2_hub", "h2_offtake", currency="EUR").lines
    }
    assert h2["H2 offtake"].hint is None  # no public offtake benchmark (research §1)
    p = h2["Production premium"].hint
    assert (p.low, p.high, p.unit, p.year) == (0.37, 0.48, "EUR/kg", 2024)
    n = P.preset("eh_ammonia_staged", "nh3_sales", currency="EUR").lines[0].hint
    assert (n.low, n.high, n.unit, n.year) == (811.0, 1000.0, "EUR/t", 2024)
    # Only [S]-tagged figures: no grey-ammonia [U] benchmark rides along.
    assert "Tampa" not in json.dumps(n.model_dump())


@pytest.mark.parametrize("template", CASES)
def test_every_linked_id_exists_on_the_built_template(template):
    _v, _l, _s, n_o2 = CASES[template]
    ids = _ids(template)
    rows = P.preset(template, CASES[template][0], currency="EUR").lines + [
        o.line for o in P.offered_rows(template)
    ]
    linked = [
        ln.quantity_source[7:-2]
        for ln in rows
        if ln.quantity_source.startswith("linked:")
    ]
    assert linked and set(linked) <= ids
    o2 = [o for o in P.offered_rows(template) if o.line.category == "by_product"]
    assert len(o2) == n_o2 and all(o.line.unit == "t" for o in o2)


@pytest.mark.parametrize("template", CASES)
def test_identity_the_billed_series_is_the_built_networks_offtake(template):
    """GK A point 3: the quantity the line bills is the built load, zero slack."""
    _v, load, source, _n = CASES[template]
    n, solve = _built(template)
    unit = "kg" if source.startswith("served_h2") else "t"
    got = _value(L.read(n, solve, source, unit))
    mwh = _offtake_mwh(n, load)
    assert got.keys() == mwh.keys()
    for p, v in mwh.items():
        factor = 1000.0 / L._lhv(None) if unit == "kg" else t_per_unit(n, "nh3")
        assert got[p] == pytest.approx(v * factor)


def _run(template, served, lost_share):
    """The business case on the template's product, with `served` t or kg a year and a lost-production line."""
    _v, load, source, _n = CASES[template]
    n, _solve_ = _built(template)
    bus = str(n.loads.at[load, "bus"])
    lp = TemplateLine(
        f"asset:opex:Generator:__lost_{bus}",
        "lost_production",
        -50.0,
        "fuel",
        source="asset",
        source_id=f"opex:Generator:__lost_{bus}",
    )
    ic = _ic(lines=(lp, TemplateLine("fom", "fom", -20.0, "opex")), analysis_years=8)
    lpm = L.LostProductionMap(
        slack_bus={f"__lost_{bus}": bus},
        load_bus={load: bus},
        share={f"__lost_{bus}": {load: L.Series("x", "share", {None: lost_share}, {})}},
        unread={},
    )
    inputs = _fill(template)
    series = {
        ln.name: L.Series(source, ln.unit, {None: served}, {})
        for ln in inputs.lines
        if ln.quantity_source.startswith("linked:")
    }
    bc = build_business_case(ic, inputs, series, lpm)
    return bc, run_case(bc.case, PACK)


@pytest.mark.parametrize("template", CASES)
def test_g3_a_preset_runs_and_the_lost_production_line_is_dropped(template):
    bc, r = _run(template, 1000.0, 1.0)
    for y in OPS:
        keys = _by_year(bc)[y]
        assert not [k for k in keys if "__lost_" in k]
        assert "fom" in keys  # only the product bus's penalty goes
    assert pnl(r).rows


@pytest.mark.parametrize("template", CASES)
def test_g3_a_shortfall_lowers_revenue_once(template):
    """Served 700 of 1000: the revenue falls by price × 300 and the bus's penalty is not charged on top."""
    full, _ = _run(template, 1000.0, 1.0)
    short, _ = _run(template, 700.0, 1.0)
    name = "NH3 sales" if template == "eh_ammonia_staged" else "H2 offtake"
    for y in OPS:
        assert _by_year(short)[y][name].amount == pytest.approx(
            _by_year(full)[y][name].amount * 0.7
        )
        assert not [k for k in _by_year(short)[y] if "__lost_" in k]


@pytest.mark.parametrize("template", CASES)
def test_g4_the_reference_purchase_is_absent(template):
    bc, _r = _run(template, 1000.0, 1.0)
    assert bc.case.counterfactual == ()
