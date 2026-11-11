# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""Business-case 05: the engine, the P&L and the KPIs (spec §3, §7; GK B BC1–BC8,
G-C1..G-C8 and the 05 plan conditions C1–C9, issue 05 ## Comments).

The IC case is `eh_site`-shaped: a PV and a battery behind the site's grid connection,
the bill, the export and the assets' fixed O&M, with the site-today bill as the
counterfactual. The business-case lines are typed (and two linked) on top of it."""

from __future__ import annotations

import dataclasses
import json
import math
import subprocess
import sys
from datetime import date

import numpy as np
import pytest

from models.business_case import (
    CATEGORY_STREAM,
    BusinessCaseInputs,
    CapexMissing,
    CapexNotOwned,
    DoubleCount,
    FacilityCapexMissing,
    ItCapexMissing,
    LineItem,
    LinkedOutputMissing,
    NameDuplicate,
    OutsideHorizon,
    PresetRule,
    PriceNotEstablished,
    ShareCycle,
)
from models.finance import DebtTranche, FinanceInputs, Incentive
from services.business_case.linked_outputs import NotEvaluated, Series
from services.finance import business_case as bcm
from services.finance.business_case import (
    BREAK_EVEN_TOL,
    break_even,
    build_business_case,
    business_case_hash,
    cash_bridge,
    kpis,
    pnl,
)
from services.finance.case import (
    CONTRACT_CLASS,
    AssetFinance,
    FinanceCase,
    FinanceRefused,
    Template,
    TemplateLine,
)
from services.finance.cashflow import STREAM_CLASS
from services.finance.engine import _net, run_case
from services.finance.packs.base import load_pack
from services.finance.report import ENERGY_BILL_STREAMS, _fallback_hash

PACK = load_pack("us_federal", as_of=date(2030, 1, 1))
ESC = {"opex": 0.02, "tariff": 0.03, "export": 0.01, "capex": 0.02, "ppa": 0.0}


def _fin(**over) -> FinanceInputs:
    kw = dict(
        currency="USD",
        financial_close=date(2030, 1, 1),
        capex_phasing=[1.0],
        contingency_share=0.0,
        analysis_years=5,
        escalation=dict(ESC),
        degradation_by_asset={"pv": 0.0},
        tax_losses="offset_other_income",
        financing_fee_tax="not_deducted",
        wacc_nominal=0.07,
        cost_of_equity=0.09,
        inflation=0.02,
        state_rate=0.0,
        acquisition_date=date(2030, 6, 1),
        small_business_163j=True,
        depreciation_class_by_asset={"pv": "slm_20", "battery": "slm_10"},
    )
    kw.update(over)
    return FinanceInputs(**kw)


def _ic(*, lines=None, **fin_over) -> FinanceCase:
    lines = (
        lines
        if lines is not None
        else (
            TemplateLine("bill", "energy_import", -300.0, "tariff"),
            TemplateLine("export", "energy_export", 120.0, "export"),
            TemplateLine("fom", "fom", -20.0, "opex"),
        )
    )
    return FinanceCase(
        inputs=_fin(**fin_over),
        owner="o",
        base_year=2031,
        cod=date(2031, 1, 1),
        templates=(Template(2031, tuple(lines), energy_mwh={"pv": 10.0}),),
        counterfactual=(
            Template(2031, (TemplateLine("bill", "energy_import", -500.0, "tariff"),)),
        ),
        counterfactual_hash="cf0123456789abcd",
        assets=(
            AssetFinance("pv", "Generator", 1000.0, 25.0, carrier="solar"),
            AssetFinance("battery", "StorageUnit", 400.0, 15.0, carrier="battery"),
        ),
    )


def _line(**kw) -> LineItem:
    base = dict(
        name="product",
        kind="revenue",
        category="product_sale",
        unit="t",
        quantity=10.0,
        price=50.0,
        escalation="opex",
    )
    base.update(kw)
    return LineItem(**base)


def _capex(**kw) -> LineItem:
    base = dict(
        name="plant",
        kind="capex",
        category="equipment",
        unit="lump",
        quantity=1.0,
        price=600.0,
        escalation="capex",
        life_years=10.0,
    )
    base.update(kw)
    return LineItem(**base)


def _inputs(*lines, **kw) -> BusinessCaseInputs:
    return BusinessCaseInputs(lines=list(lines), **kw)


def _run(inputs, ic=None, outputs=None):
    bc = build_business_case(ic or _ic(), inputs, outputs or {})
    return bc, run_case(bc.case, PACK)


def _years(r):
    return [int(y) for y in r.tl.years]


def _at(r, year):
    return _years(r).index(year)


# ── STREAM_CLASS (G-C1, C8) ─────────────────────────────────────────────────

# The rows before slice 05, frozen (C8): slice 05 adds rows, never changes one.
_FROZEN_STREAM_CLASS = {
    "energy_import": "tariff",
    "network_capacity": "tariff",
    "network_energy": "tariff",
    "demand_charge": "tariff",
    "retail_fixed": "tariff",
    "certificates": "tariff",
    "tax": "tariff",
    "energy_export": "export",
    "ancillary": "export",
    "energy_wholesale": "export",
    "ppa_settlement": CONTRACT_CLASS,
    "cfd_settlement": CONTRACT_CLASS,
    "lease": CONTRACT_CLASS,
    "eaas_fee": CONTRACT_CLASS,
    "dr_availability": CONTRACT_CLASS,
    "dr_activation": CONTRACT_CLASS,
    "fom": "opex",
    "vom": "opex",
    "other": "opex",
    "fuel": "fuel",
    "lost_production": "fuel",
    "capex": "capex",
}
_FROZEN_BILL_STREAMS = (
    "certificates",
    "demand_charge",
    "energy_import",
    "network_capacity",
    "network_energy",
    "retail_fixed",
    "tax",
)


def test_every_category_has_its_stream_class_row():
    for (kind, category), (stream, cls) in CATEGORY_STREAM.items():
        if kind == "capex":
            assert stream is None, category
            continue
        assert STREAM_CLASS[stream] == cls, (kind, category)


def test_the_pre_existing_stream_rows_and_bill_streams_are_unchanged():
    assert {k: STREAM_CLASS[k] for k in _FROZEN_STREAM_CLASS} == _FROZEN_STREAM_CLASS
    new = set(STREAM_CLASS) - set(_FROZEN_STREAM_CLASS)
    assert new == {s for s, _ in CATEGORY_STREAM.values() if s is not None}
    assert ENERGY_BILL_STREAMS == _FROZEN_BILL_STREAMS
    assert not any(STREAM_CLASS[s] == "tariff" for s in new)


def test_an_empty_business_case_is_the_ic_on_its_own_line_for_line():
    """Per-year templates change nothing: no BC lines = the IC without counterfactual."""
    ic = _ic()
    _, r = _run(_inputs(), ic)
    ref = run_case(
        dataclasses.replace(ic, counterfactual=(), counterfactual_hash=None), PACK
    )
    assert set(r.op.lines) == set(ref.op.lines)
    assert {m.stream for m in r.op.line_meta.values()} == {
        m.stream for m in ref.op.line_meta.values()
    }
    for k in ref.op.lines:
        np.testing.assert_array_equal(r.op.lines[k], ref.op.lines[k])
    for k, v in ref.cash.items():
        np.testing.assert_array_equal(r.cash[k], v)
    assert r.metrics == ref.metrics


# ── BC1, C1, C9: the IC is untouched; the BC hash is its own domain ──────────


def test_bc1_the_build_leaves_the_ic_case_and_its_dicts_untouched():
    ic = _ic()
    dep, rep, cod = (
        ic.inputs.depreciation_class_by_asset,
        ic.inputs.replacement_capex,
        ic.asset_cod,
    )
    snap = (dict(dep), list(rep), dict(cod))
    before = run_case(ic, PACK)
    inputs = _inputs(
        _line(),
        _capex(refresh_years=2.0),
        _capex(name="later", start_year=2033),
    )
    bc = build_business_case(ic, inputs, {})
    assert ic.inputs.depreciation_class_by_asset is dep
    assert ic.inputs.replacement_capex is rep
    assert ic.asset_cod is cod
    assert (dict(dep), list(rep), dict(cod)) == snap
    assert bc.case.inputs.depreciation_class_by_asset is not dep
    assert bc.case.inputs.replacement_capex is not rep
    assert bc.case.asset_cod is not cod
    after = run_case(ic, PACK)
    assert after.metrics == before.metrics
    for k, v in before.cash.items():
        np.testing.assert_array_equal(after.cash[k], v)


def test_bc1_the_bc_hash_is_its_own_domain_and_covers_each_input():
    ic = _ic()
    linked = _line(
        name="nh3", quantity=None, quantity_source="linked:served_product.nh3@1"
    )
    s = Series("served_product.nh3", "t", {None: 100.0}, {})
    base = _inputs(_line(), linked, preset="eh_site@1", receivable_days=30.0)
    h = business_case_hash(ic, base, {"nh3": s})
    assert h != _fallback_hash(ic)
    assert h == business_case_hash(ic, base.model_copy(deep=True), {"nh3": s})
    assert h == build_business_case(ic, base, {"nh3": s}).hash
    # Nothing else: an output no line links to does not enter the hash.
    assert h == business_case_hash(ic, base, {"nh3": s, "unused": s})
    changed = [
        business_case_hash(
            ic, base.model_copy(update={"preset": "eh_site@2"}), {"nh3": s}
        ),
        business_case_hash(
            ic, base.model_copy(update={"receivable_days": 45.0}), {"nh3": s}
        ),
        business_case_hash(
            ic,
            _inputs(
                _line(price=51.0), linked, preset="eh_site@1", receivable_days=30.0
            ),
            {"nh3": s},
        ),
        business_case_hash(
            ic, base, {"nh3": Series("served_product.nh3", "t", {None: 99.0}, {})}
        ),
        business_case_hash(
            ic, base, {"nh3": NotEvaluated("served_product.nh3", "stale")}
        ),
    ]
    assert h not in changed and len(set(changed)) == len(changed)


_DIGESTS = """
import json, sys
from datetime import date
from models.finance import FinanceInputs
from services.finance.case import FinanceCase
from services.finance.investment_case_runner import json_digest
from services.finance.report import _fallback_hash
assert "services.finance.business_case" not in sys.modules
fin = FinanceInputs.model_validate_json(sys.stdin.read())
case = FinanceCase(inputs=fin, owner="o", base_year=2031, cod=date(2031, 1, 1),
                   templates=(), assets=())
print(json.dumps([_fallback_hash(case), json_digest(fin.model_dump(mode="json"))]))
assert "services.finance.business_case" not in sys.modules
"""


def test_c9_the_ic_digests_are_unchanged_by_the_bc_import_and_a_build():
    from services.finance.investment_case_runner import json_digest

    ic = _ic()

    def digests():
        return [_fallback_hash(ic), json_digest(ic.inputs.model_dump(mode="json"))]

    raw = ic.inputs.model_dump_json()
    before = digests()
    build_business_case(
        ic,
        _inputs(
            _line(), _capex(refresh_years=2.0), _capex(name="later", start_year=2033)
        ),
        {},
    )
    # The build changes no IC digest (taken before and after it, #1404 item 5) ...
    assert digests() == before
    here = before
    # ... and the BC import changes none: a process that never imports it agrees.
    out = subprocess.run(
        [sys.executable, "-c", _DIGESTS],
        input=raw,
        capture_output=True,
        text=True,
        check=True,
        cwd=str(__import__("pathlib").Path(__file__).resolve().parents[1]),
    )
    assert json.loads(out.stdout) == here


def test_b2_the_bc_has_no_counterfactual_and_the_ic_no_bc_line():
    ic = _ic()
    bc = build_business_case(ic, _inputs(_line()), {})
    assert bc.case.counterfactual == () and bc.case.counterfactual_hash is None
    assert not any(ln.source == "business_case" for t in ic.templates for ln in t.lines)
    bc_lines = {
        ln.key
        for t in bc.case.templates
        for ln in t.lines
        if ln.source == "business_case"
    }
    assert bc_lines == {"product"}


# ── G1 compile ──────────────────────────────────────────────────────────────


def test_a_revenue_line_is_its_category_stream_signed_and_escalated():
    _, r = _run(
        _inputs(
            _line(),
            _line(
                name="staff",
                kind="opex",
                category="staff",
                unit="unit",
                quantity=2.0,
                price=30.0,
            ),
        )
    )
    assert r.op.line_meta["product"].stream == "product_revenue"
    assert r.op.line_meta["staff"].stream == "bc_staff"
    for y in range(2031, 2036):
        i = _at(r, y)
        assert r.op.lines["product"][i] == pytest.approx(500.0 * 1.02 ** (y - 2031))
        assert r.op.lines["staff"][i] == pytest.approx(-60.0 * 1.02 ** (y - 2031))
    assert r.op.lines["product"][_at(r, 2030)] == 0.0


def test_a_line_with_a_non_base_money_year_escalates_from_it():
    _, r = _run(_inputs(_line(money_year=2028)))
    for y in range(2031, 2036):
        assert r.op.lines["product"][_at(r, y)] == pytest.approx(
            500.0 * 1.02 ** (y - 2028)
        )


def test_a_contract_line_uses_its_indexation_and_a_grid_cell_does_not_escalate():
    ln = _line(
        escalation="contract",
        indexation=0.05,
        price_by_year={2033: 70.0},
        quantity_by_year={2034: 4.0},
        start_year=2032,
        end_year=2034,
    )
    _, r = _run(_inputs(ln))
    v = r.op.lines["product"]
    assert v[_at(r, 2031)] == 0.0 and v[_at(r, 2035)] == 0.0
    assert v[_at(r, 2032)] == pytest.approx(500.0 * 1.05)
    assert v[_at(r, 2033)] == pytest.approx(700.0)  # the cell, unescalated
    assert v[_at(r, 2034)] == pytest.approx(4.0 * 50.0 * 1.05**3)


def test_kw_month_and_kw_year_are_real_units():
    a = _line(
        name="a", category="lease", unit="kW_month", quantity=100.0 * 12, price=2.0
    )
    b = _line(name="b", category="lease", unit="kW_year", quantity=100.0, price=24.0)
    _, r = _run(_inputs(a, b))
    np.testing.assert_allclose(r.op.lines["a"], r.op.lines["b"])


def test_bc6_a_linked_line_reads_served_only_and_unserved_earns_nothing():
    ln = _line(
        name="nh3",
        quantity=None,
        quantity_source="linked:served_product.nh3@1",
        price=400.0,
    )
    full = Series("served_product.nh3", "t", {None: 100.0}, {})
    short = Series("served_product.nh3", "t", {None: 90.0}, {})
    _, r_full = _run(_inputs(ln), outputs={"nh3": full})
    bc, r_short = _run(_inputs(ln), outputs={"nh3": short})
    for y in range(2031, 2036):
        i = _at(r_full, y)
        assert r_full.op.lines["nh3"][i] - r_short.op.lines["nh3"][i] == pytest.approx(
            10.0 * 400.0 * 1.02 ** (y - 2031)
        )
    blob = json.dumps(
        [dataclasses.asdict(t) for t in bc.case.templates]
        + [
            bc.case.inputs.model_dump(mode="json"),
            r_short.flags,
            list(pnl(r_short).rows),
        ],
        default=str,
    ).lower()
    assert "voll" not in blob and "lost_load" not in blob


def test_a_not_evaluated_or_uncovered_linked_quantity_is_never_zero():
    ln = _line(name="nh3", quantity=None, quantity_source="linked:served_product.nh3@1")
    _, r = _run(
        _inputs(ln), outputs={"nh3": NotEvaluated("served_product.nh3", "stale")}
    )
    assert r.op.revenue is None
    assert "line_not_established:nh3" in r.op.reasons["operating"]
    # A multi-period series that covers only 2031–2032: later years are not established.
    part = Series("served_product.nh3", "t", {2031: 100.0}, {2031: 2.0})
    _, r = _run(_inputs(ln), outputs={"nh3": part})
    assert "line_not_established:nh3" in r.op.reasons["operating"]


# ── capex (G1, Q1–Q3) ────────────────────────────────────────────────────────


def test_a_capex_line_is_an_owner_asset_on_its_own_depreciation_class():
    bc, r = _run(
        _inputs(
            _capex(),
            _capex(
                name="kit", depreciation="declining_balance", rate=0.25, life_years=8.0
            ),
        )
    )
    a = {x.name: x for x in bc.case.assets}
    assert (
        a["plant"].overnight_cost == 600.0 and a["plant"].component == "business_case"
    )
    assert math.isinf(a["plant"].lifetime_years)
    cls = bc.case.inputs.depreciation_class_by_asset
    assert cls["plant"] == "slm_10" and cls["kit"] == "db_0.25_8"
    assert cls["pv"] == "slm_20"
    assert r.sections["tax"] == "ok"
    assert float(r.op.capex.sum()) == pytest.approx(1000 + 400 + 600 + 600)


@pytest.mark.parametrize("money_year", [2028, 2034])
def test_q1_capex_in_another_money_year_converts_at_the_capex_rate(money_year):
    bc = build_business_case(_ic(), _inputs(_capex(money_year=money_year)), {})
    plant = next(a for a in bc.case.assets if a.name == "plant")
    assert plant.overnight_cost == pytest.approx(600.0 * 1.02 ** (2031 - money_year))


def test_q1_a_missing_capex_rate_is_not_established_never_zero_percent():
    esc = {k: v for k, v in ESC.items() if k != "capex"}
    bc, r = _run(_inputs(_capex(money_year=2028)), _ic(escalation=esc))
    assert next(a for a in bc.case.assets if a.name == "plant").overnight_cost is None
    assert "overnight_cost_missing:plant" in r.op.reasons["capex"]


def test_q2_capex_after_cod_is_a_stage_and_past_the_horizon_is_refused():
    bc, r = _run(_inputs(_capex(start_year=2033)))
    assert bc.case.asset_cod["plant"].year == 2033
    assert [s[0] for s in r.op.stage_items] == ["plant"]
    with pytest.raises(OutsideHorizon) as e:
        build_business_case(_ic(), _inputs(_capex(start_year=2040)), {})
    assert e.value.field == "lines[0].start_year"


def test_q2_the_staged_incentive_refusal_fires_through_the_bc_path():
    grant = Incentive(kind="grant", amount=100.0, grant_tax_treatment="taxable")
    bc = build_business_case(
        _ic(incentives=[grant]), _inputs(_capex(start_year=2033)), {}
    )
    with pytest.raises(FinanceRefused) as e:
        run_case(bc.case, PACK)
    assert e.value.code == "staged_incentive_not_supported:plant"


def test_refresh_capex_lands_in_start_plus_k_refresh_up_to_end_inclusive():
    bc, r = _run(_inputs(_capex(refresh_years=2.0)))
    assert sorted(bc.case.inputs.replacement_capex) == [
        (2033, "plant", 600.0),
        (2035, "plant", 600.0),
    ]
    rep = r.op.replacement
    hit = [y for y in _years(r) if rep[_at(r, y)] != 0.0]
    assert hit == [2033, 2035]
    assert rep[_at(r, 2033)] == pytest.approx(600.0 * 1.02**2)
    _, r0 = _run(_inputs(_capex()))
    dep, dep0 = r.tax.depreciation["federal"], r0.tax.depreciation["federal"]
    assert dep[_at(r, 2033)] > dep0[_at(r, 2033)]  # the vintage depreciates
    bc, _ = _run(_inputs(_capex(refresh_years=2.0, end_year=2034)))
    assert [y for y, *_ in bc.case.inputs.replacement_capex] == [2033]


def test_q3_a_non_integral_life_leaves_tax_and_every_post_tax_kpi_not_established():
    ic, inputs = _ic(), _inputs(_line(price=200.0), _capex(life_years=7.5))
    bc, r = _run(inputs, ic)
    assert r.tax_unlevered is None
    assert any(
        x.startswith("tax_input_missing:depreciation_class:plant")
        for x in r.reasons["tax"]
    )
    k = kpis(r, pnl(r))
    for name in (
        "project_npv",
        "project_irr",
        "equity_npv",
        "equity_irr",
        "payback_years",
    ):
        assert k[name].value is None and k[name].status == "not_established", name
        assert "tax" in k[name].reason, name
    be = break_even(ic, inputs, {}, "product", PACK)
    assert be["status"] == "break_even_not_established" and be["price"] is None


# ── share_of (G6, C3) ───────────────────────────────────────────────────────


def test_a_share_of_revenue_reads_the_revenue_lines_nominal_value():
    share = _line(
        name="royalty",
        kind="opex",
        category="other",
        unit="share",
        quantity=0.1,
        price=None,
        quantity_source="share_of:revenue",
        escalation="opex",
    )
    other = _line(name="o2", category="by_product", price=10.0, escalation="export")
    _, r = _run(_inputs(_line(), other, share))
    rev = r.op.lines["product"] + r.op.lines["o2"]
    np.testing.assert_allclose(r.op.lines["royalty"], -0.1 * rev, rtol=1e-12)


def test_a_share_of_capex_is_base_money_escalated_by_the_lines_class():
    share = _line(
        name="upkeep",
        kind="opex",
        category="maintenance",
        unit="share",
        quantity=0.02,
        price=None,
        quantity_source="share_of:capex",
        escalation="tariff",
    )
    _, r = _run(_inputs(_capex(), _capex(name="kit", price=400.0), share))
    for y in range(2031, 2036):
        assert r.op.lines["upkeep"][_at(r, y)] == pytest.approx(
            -0.02 * 1000.0 * 1.03 ** (y - 2031)
        )


def test_c3_a_share_whose_base_holds_a_share_is_refused_at_build():
    a = _line(
        name="s1",
        kind="opex",
        category="other",
        unit="share",
        quantity=0.1,
        price=None,
        quantity_source="share_of:opex",
    )
    with pytest.raises(ShareCycle) as e:
        build_business_case(
            _ic(),
            _inputs(_line(name="staff", kind="opex", category="staff", unit="unit"), a),
            {},
        )
    assert e.value.field == "lines[1].quantity_source"


# ── refusals at build (BC7, C2, C4) ─────────────────────────────────────────


def test_bc7_c2_c4_build_refuses_with_the_line_and_column():
    req = PresetRule(
        requires=["it_hardware", "facility", "equipment"], excludes=["other"]
    )
    cases = [
        (_inputs(_line(price=None)), PriceNotEstablished, "lines[0].price"),
        (
            _inputs(_line(preset_rule=req), _capex(category="other")),
            ItCapexMissing,
            "lines[0].preset_rule.requires[it_hardware]",
        ),
        (
            _inputs(_line(preset_rule=PresetRule(requires=["facility"]))),
            FacilityCapexMissing,
            "lines[0].preset_rule.requires[facility]",
        ),
        (
            _inputs(_line(preset_rule=PresetRule(requires=["equipment"]))),
            CapexMissing,
            "lines[0].preset_rule.requires[equipment]",
        ),
        (
            _inputs(_line(preset_rule=PresetRule(excludes=["equipment"])), _capex()),
            CapexNotOwned,
            "lines[1].category",
        ),
        (_inputs(_line(name="bill")), NameDuplicate, "lines[0].name"),
        (_inputs(_line(), _capex(name="pv")), NameDuplicate, "lines[1].name"),
        (
            _inputs(
                _line(
                    name="nh3",
                    quantity=None,
                    quantity_source="linked:served_product.nh3@1",
                )
            ),
            LinkedOutputMissing,
            "served_product.nh3",
        ),
    ]
    for inputs, exc, field in cases:
        with pytest.raises(exc) as e:
            build_business_case(_ic(), inputs, {})
        assert e.value.field == field, (exc, e.value.field)


def test_c4_a_linked_quantity_the_ic_ledger_prices_is_refused_double_count():
    ln = _line(
        name="exp",
        category="other",
        unit="MWh",
        quantity=None,
        quantity_source="linked:export.poc_grid@1",
    )
    s = Series("export.poc_grid", "MWh", {None: 10.0}, {})
    with pytest.raises(DoubleCount) as e:
        build_business_case(_ic(), _inputs(ln), {"exp": s})
    assert (
        e.value.field == "lines[0].quantity_source"
        and e.value.stream == "energy_export"
    )


# ── the P&L (BC3, BC4, BC5, C6) ─────────────────────────────────────────────


def test_bc4_the_pnl_is_op_by_stream_and_ebitda_ties_to_net_total():
    _, r = _run(
        _inputs(
            _line(price=200.0),
            _line(
                name="staff",
                kind="opex",
                category="staff",
                unit="unit",
                quantity=1.0,
                price=100.0,
            ),
        )
    )
    p = pnl(r)
    net = _net(r.op)
    np.testing.assert_array_equal(p.rows["ebitda"], net)
    np.testing.assert_array_equal(p.rows["revenue"], r.op.revenue)
    assert set(p.streams) == {m.stream for m in r.op.line_meta.values()}
    np.testing.assert_allclose(np.sum(list(p.streams.values()), axis=0), net, atol=1e-6)
    i = _at(r, 2032)
    # A year with net > 0 where gross ≠ net: op_incremental's clipped revenue is not it.
    assert net[i] > 0 and p.rows["revenue"][i] != pytest.approx(net[i])
    assert p.rows["revenue"][i] != pytest.approx(
        np.clip(r.op_incremental["net"], 0, None)[i]
    )


def test_c6_the_ebitda_tie_is_exact_on_order_sensitive_values():
    ic = _ic(lines=(TemplateLine("fom", "fom", -1.0, "opex"),))
    _, r = _run(
        _inputs(
            _line(name="big", unit="unit", quantity=1.0, price=1e16),
            _line(name="one", unit="unit", quantity=1.0, price=1.0),
            _line(
                name="c1",
                kind="opex",
                category="staff",
                unit="unit",
                quantity=1.0,
                price=1.0,
            ),
        ),
        ic,
    )
    np.testing.assert_array_equal(pnl(r).rows["ebitda"], _net(r.op))


def _tol(p, i):
    return 1e-6 * max(abs(float(p.rows["revenue"][i])), 1.0)


def test_bc5_the_tax_is_the_engines_and_the_cash_ties_each_year():
    loan = DebtTranche(
        kind="term_loan",
        amount=800.0,
        rate=0.05,
        tenor_years=4,
        upfront_fee=0.0,
        dsra_months=0,
        grace_years=0,
        commitment_fee=0.0,
    )
    _, r = _run(
        _inputs(_line(price=200.0), _capex(refresh_years=2.0)), _ic(debt=[loan])
    )
    p = pnl(r)
    np.testing.assert_array_equal(p.rows["tax"], r.tax_unlevered.total_liability)
    np.testing.assert_array_equal(p.rows["tax_equity"], r.tax.total_liability)
    for key in ("project_post_tax", "equity_post_tax"):
        for i in range(r.tl.n):
            assert abs(p.rows[key][i] - r.cash[key][i]) <= _tol(p, i), (key, i)
    # The mutant: a second tax computation (flat 21 % of EBIT) breaks the tie.
    rows = dict(p.rows)
    rows["tax"] = np.clip(0.21 * rows["ebit"], 0, None)
    bad = cash_bridge(rows)
    assert any(
        abs(bad["project_post_tax"][i] - r.cash["project_post_tax"][i]) > _tol(p, i)
        for i in range(r.tl.n)
    )


def test_bc3_working_capital_is_in_the_cash_bridge_only():
    inputs = _inputs(_line(price=200.0), _capex())
    _, r = _run(inputs)
    p0 = pnl(r)
    p30 = pnl(r, receivable_days=30.0)
    for row in (
        "revenue",
        "ebitda",
        "depreciation",
        "ebit",
        "tax",
        "net_income",
        "project_post_tax",
        "equity_post_tax",
    ):
        np.testing.assert_array_equal(p0.rows[row], p30.rows[row])
    assert p0.rows["working_capital_change"] is None
    wc = p30.rows["working_capital_change"]
    assert abs(wc.sum()) < 1e-9 and wc[_at(r, 2031)] < 0 and wc[-1] > 0
    for k in ("project", "equity"):
        np.testing.assert_allclose(
            p30.rows[f"{k}_after_working_capital"], p30.rows[f"{k}_post_tax"] + wc
        )


def test_a_hand_checked_three_year_case():
    """Revenue 100, staff 20, plant 200 straight-line over 2 years refreshed in 2033,
    no escalation, 21 % federal tax, no debt."""
    ic = _ic(
        lines=(TemplateLine("fom", "fom", 0.0, "opex"),),
        analysis_years=3,
        escalation={k: 0.0 for k in ESC},
    )
    ic = dataclasses.replace(
        ic, assets=(AssetFinance("pv", "Generator", 0.0, 25.0, carrier="solar"),)
    )
    inputs = _inputs(
        _line(unit="unit", quantity=1.0, price=100.0),
        _line(
            name="staff",
            kind="opex",
            category="staff",
            unit="unit",
            quantity=1.0,
            price=20.0,
        ),
        _capex(price=200.0, life_years=2.0, refresh_years=2.0),
    )
    _, r = _run(inputs, ic)
    p = pnl(r)
    ops = [_at(r, y) for y in (2031, 2032, 2033)]
    assert [p.rows["revenue"][i] for i in ops] == pytest.approx([100.0] * 3)
    assert [p.rows["ebitda"][i] for i in ops] == pytest.approx([80.0] * 3)
    # 200 over two years; the 2033 refresh (200) is written off in the last year.
    assert [p.rows["depreciation"][i] for i in ops] == pytest.approx(
        [100.0, 100.0, 200.0]
    )
    assert [p.rows["ebit"][i] for i in ops] == pytest.approx([-20.0, -20.0, -120.0])
    # Losses offset other income: tax is 21 % of EBIT, a credit when negative.
    assert [p.rows["tax"][i] for i in ops] == pytest.approx([-4.2, -4.2, -25.2])
    assert [p.rows["net_income"][i] for i in ops] == pytest.approx(
        [-15.8, -15.8, -94.8]
    )
    assert p.rows["capex"][_at(r, 2030)] == pytest.approx(200.0)
    assert p.rows["replacement"][_at(r, 2033)] == pytest.approx(200.0)


# ── KPIs (BC2, BC8, C5) ─────────────────────────────────────────────────────


def test_bc2_a_margin_zero_pass_through_nets_out():
    pt = _line(
        name="pt",
        category="cost_recovery",
        unit="unit",
        quantity=1.0,
        price=300.0,
        escalation="tariff",
    )
    cap = {"pv": 2.0, "battery": 1.0}
    _, ra = _run(_inputs(_line()))
    _, rb = _run(_inputs(_line(), pt))
    ka, kb = kpis(ra, pnl(ra), capacity=cap), kpis(rb, pnl(rb), capacity=cap)
    pb = pnl(rb)
    np.testing.assert_allclose(
        pb.rows["ebitda_net_of_pass_through"], pnl(ra).rows["ebitda"]
    )
    assert kb["ebitda_margin_net"].value == pytest.approx(
        ka["ebitda_margin_gross"].value
    )
    assert kb["revenue_per_capacity_net"].value == pytest.approx(
        ka["revenue_per_capacity_gross"].value
    )
    assert kb["ebitda_margin_gross"].value != pytest.approx(
        ka["ebitda_margin_gross"].value
    )
    assert kb["revenue_per_capacity_gross"].unit == "USD/MW-year"
    assert kb["revenue_per_capacity_gross"].basis == {
        "assets": ["battery", "pv"],
        "capacity": 3.0,
        "unit": "MW",
    }
    t = kpis(rb, pb, capacity={"nh3_loop": 2.0}, capacity_unit="t_per_year")
    assert t["revenue_per_capacity_gross"].unit == "USD/t_per_year-year"
    assert t["revenue_per_capacity_gross"].value == pytest.approx(
        kb["revenue_per_capacity_gross"].value * 3.0 / 2.0
    )


def test_c5_no_kpi_is_ever_non_finite():
    ic = _ic(lines=(TemplateLine("fom", "fom", -20.0, "opex"),))
    # No capex either, so every year's post-tax cash is ≤ 0.
    ic = dataclasses.replace(
        ic, assets=(AssetFinance("pv", "Generator", 0.0, 25.0, carrier="solar"),)
    )
    _, r = _run(_inputs(), ic)
    p = pnl(r)
    k = kpis(r, p, capacity={"pv": 0.0})
    assert (
        k["ebitda_margin_gross"].status == "n/a"
        and k["ebitda_margin_gross"].value is None
    )
    assert all(v is None for v in p.rows["ebitda_margin"])
    assert k["revenue_per_capacity_gross"].status == "not_established"
    assert kpis(r, p)["revenue_per_capacity_gross"].status == "not_established"
    assert (
        kpis(r, p, capacity={})["revenue_per_capacity_gross"].status
        == "not_established"
    )
    # All cash negative: no sign change, so the IRR is n/a.
    assert k["project_irr"].status == "n/a" and k["project_irr"].value is None
    for v in k.values():
        assert v.value is None or math.isfinite(v.value)


def test_bc8_dscr_is_na_without_debt_and_a_number_with_it():
    _, r = _run(_inputs(_line(price=200.0)))
    k = kpis(r, pnl(r))
    assert k["min_dscr"].status == "n/a" and k["min_dscr"].value is None
    loan = DebtTranche(
        kind="term_loan",
        amount=800.0,
        rate=0.05,
        tenor_years=4,
        upfront_fee=0.0,
        dsra_months=0,
        grace_years=0,
        commitment_fee=0.0,
    )
    _, r = _run(_inputs(_line(price=200.0)), _ic(debt=[loan]))
    k = kpis(r, pnl(r))
    assert k["min_dscr"].status == "ok" and math.isfinite(k["min_dscr"].value)


def test_bc8_c7_break_even_brackets_its_root_and_reruns_to_zero(monkeypatch):
    ic, inputs = (
        _ic(),
        _inputs(
            _line(price=50.0),
            _line(name="o2", category="by_product"),
            _line(
                name="fee",
                kind="opex",
                category="other",
                unit="share",
                quantity=0.05,
                price=None,
                quantity_source="share_of:revenue/by_product",
            ),
        ),
    )
    seen = []
    real = bcm.run_case

    def spy(case, *a, **kw):
        seen.append(case)
        return real(case, *a, **kw)

    monkeypatch.setattr(bcm, "run_case", spy)
    be = break_even(ic, inputs, {}, "product", PACK)
    assert be["status"] == "ok" and be["money_year"] == 2031

    # Only the named line moves: every other line is byte-equal across two trials.
    def others(case):
        return [
            (t.first_year, ln)
            for t in case.templates
            for ln in t.lines
            if ln.key != "product"
        ]

    assert len(seen) >= 2 and others(seen[0]) == others(seen[1])
    assert seen[0].assets == seen[1].assets
    monkeypatch.setattr(bcm, "run_case", real)
    solved = _inputs(_line(price=be["price"]), *inputs.lines[1:])
    _, r = _run(solved, ic)
    from services.finance.metrics import npv

    v = npv(ic.inputs.wacc_nominal, r.cash["project_post_tax"])
    assert abs(v) <= BREAK_EVEN_TOL * float(np.sum(r.op.lines["product"]))


def test_bc8_no_sign_change_in_the_bracket_is_refused_never_extrapolated():
    rich = _line(name="subsidy", category="other", unit="unit", quantity=1.0, price=1e5)
    be = break_even(_ic(), _inputs(_line(), rich), {}, "product", PACK)
    assert be["status"] == "break_even_not_bracketed" and be["price"] is None
    be = break_even(
        _ic(),
        _inputs(
            _line(), _line(name="staff", kind="opex", category="staff", unit="unit")
        ),
        {},
        "staff",
        PACK,
    )
    assert be["status"] == "break_even_line_not_revenue"
    be = break_even(_ic(), _inputs(_line()), {}, "nope", PACK)
    assert be["status"] == "break_even_line_not_revenue"


def test_bc8_npv_falling_with_price_is_not_bracketed(monkeypatch):
    """A kink where NPV falls with price over the whole bracket: no root is invented."""
    monkeypatch.setattr(bcm, "_npv_at", lambda *a, **k: -1.0 - a[-1])
    be = break_even(_ic(), _inputs(_line()), {}, "product", PACK)
    assert be["status"] == "break_even_not_bracketed"


# ── review fixes and the mutant survivors ───────────────────────────────────


def test_a_capex_share_of_another_capex_category_is_base_money():
    cont = _capex(
        name="contingency",
        category="other",
        unit="share",
        quantity=0.1,
        price=None,
        quantity_source="share_of:capex/equipment",
    )
    bc = build_business_case(
        _ic(), _inputs(_capex(), _capex(name="kit", price=400.0), cont), {}
    )
    a = {x.name: x.overnight_cost for x in bc.case.assets}
    assert a["contingency"] == pytest.approx(0.1 * 1000.0)


def test_a_capex_share_of_revenue_is_its_first_years_nominal_in_base_money():
    cap = _capex(
        name="dev",
        category="other",
        unit="share",
        quantity=0.5,
        price=None,
        quantity_source="share_of:revenue",
        start_year=2032,
    )
    bc = build_business_case(_ic(), _inputs(_line(), cap), {})
    dev = next(x for x in bc.case.assets if x.name == "dev")
    # 2032 revenue 500 × 1.02 (opex class), back to 2031 money at the capex rate 2 %.
    assert dev.overnight_cost == pytest.approx(0.5 * 500.0 * 1.02 / 1.02)


def test_capex_in_base_money_needs_no_capex_rate():
    esc = {k: v for k, v in ESC.items() if k != "capex"}
    bc = build_business_case(_ic(escalation=esc), _inputs(_capex()), {})
    assert next(a for a in bc.case.assets if a.name == "plant").overnight_cost == 600.0


def test_a_not_evaluated_linked_capex_stays_not_established_in_any_money_year():
    ln = _capex(
        unit="t",
        quantity=None,
        quantity_source="linked:served_product.nh3@1",
        money_year=2028,
    )
    bc = build_business_case(
        _ic(), _inputs(ln), {"plant": NotEvaluated("served_product.nh3", "stale")}
    )
    assert next(a for a in bc.case.assets if a.name == "plant").overnight_cost is None


def test_outputs_default_to_none_everywhere():
    ic, inputs = _ic(), _inputs(_line())
    assert (
        build_business_case(ic, inputs).hash
        == business_case_hash(ic, inputs)
        == build_business_case(ic, inputs, {}).hash
    )
    assert break_even(ic, inputs, None, "product", PACK)["status"] == "ok"


def test_the_hash_tells_not_evaluated_reasons_apart():
    ic = _ic()
    ln = _line(name="nh3", quantity=None, quantity_source="linked:served_product.nh3@1")
    a = business_case_hash(
        ic, _inputs(ln), {"nh3": NotEvaluated("served_product.nh3", "x")}
    )
    b = business_case_hash(
        ic, _inputs(ln), {"nh3": NotEvaluated("served_product.nh3", "y")}
    )
    assert a != b


def test_the_cash_bridge_is_not_established_without_its_rows():
    _, r = _run(_inputs(_line(price=200.0)))
    rows = dict(pnl(r).rows)
    rows["capex"] = None
    assert all(v is None for v in cash_bridge(rows).values())


def test_a_non_finite_kpi_reads_na():
    assert bcm._ok(float("inf")) == bcm.Kpi(None, "n/a", "not_finite")
    assert bcm._ok(float("nan")).value is None


def test_the_irr_reason_is_the_engines():
    ic = _ic(lines=(TemplateLine("fom", "fom", -20.0, "opex"),))
    ic = dataclasses.replace(
        ic, assets=(AssetFinance("pv", "Generator", 0.0, 25.0, carrier="solar"),)
    )
    _, r = _run(_inputs(), ic)
    assert kpis(r, pnl(r))["project_irr"].reason == "irr_not_established:no_sign_change"


def test_c4_a_difference_line_netting_to_zero_still_prices_its_volume():
    cfd = TemplateLine(
        "cfd",
        "cfd_settlement",
        0.0,
        CONTRACT_CLASS,
        indexation=0.0,
        price=50.0,
        price_qty_mwh=10.0,
    )
    ln = _line(
        name="exp",
        category="other",
        unit="MWh",
        quantity=None,
        quantity_source="linked:export.poc_grid@1",
    )
    s = Series("export.poc_grid", "MWh", {None: 10.0}, {})
    with pytest.raises(DoubleCount) as e:
        build_business_case(_ic(lines=(cfd,)), _inputs(ln), {"exp": s})
    assert e.value.stream == "cfd_settlement"


# ── GK B (49) #1404 NOT OK items ────────────────────────────────────────────


def _strict(k):
    json.dumps(dataclasses.asdict(k), allow_nan=False)


@pytest.mark.parametrize(
    "capacity",
    [{"pv": math.inf}, {"pv": math.nan}, {"pv": 100.0, "battery": -99.0}, {"pv": 0.0}],
)
def test_item1_a_non_finite_or_non_positive_capacity_is_not_established(capacity):
    _, r = _run(_inputs(_line()))
    k = kpis(r, pnl(r), capacity=capacity)
    for name in ("revenue_per_capacity_gross", "revenue_per_capacity_net"):
        assert k[name].status == "not_established" and k[name].value is None
        assert k[name].basis["capacity"] is None
    for v in k.values():
        _strict(v)


def test_item2_a_tiny_revenue_gives_no_infinite_margin():
    tiny = _line(unit="unit", quantity=1.0, price=1e-310)
    ic = _ic(lines=(TemplateLine("fom", "fom", -20.0, "opex"),))
    _, r = _run(_inputs(tiny), ic)
    p = pnl(r)
    assert any(v != 0.0 for v in p.rows["revenue"])
    assert all(v is None or math.isfinite(v) for v in p.rows["ebitda_margin"])
    k = kpis(r, p)
    for v in k.values():
        _strict(v)


def test_item3_an_overflowing_line_is_not_established_never_inf():
    huge = _line(quantity=1e200, price=1e200)
    bc, r = _run(_inputs(huge))
    amounts = [
        ln.amount for t in bc.case.templates for ln in t.lines if ln.key == "product"
    ]
    assert amounts and all(a is None for a in amounts)
    assert "line_not_established:product" in r.op.reasons["operating"]
    for row in pnl(r).rows.values():
        if isinstance(row, np.ndarray):
            assert np.isfinite(row).all()
    big = _capex(quantity=1e200, price=1e200)
    bc = build_business_case(_ic(), _inputs(big), {})
    assert next(a for a in bc.case.assets if a.name == "plant").overnight_cost is None


def _brentq_at(x, converged=True):
    from types import SimpleNamespace

    return lambda f, lo, hi, **kw: (x, SimpleNamespace(converged=converged))


def test_item6_a_rerun_outside_the_tolerance_is_not_converged(monkeypatch):
    monkeypatch.setattr(bcm, "_npv_at", lambda *a: a[-1] - 1.2)
    monkeypatch.setattr(bcm, "brentq", _brentq_at(1.3))
    be = break_even(_ic(), _inputs(_line()), {}, "product", PACK)
    assert be["status"] == "break_even_not_converged" and be["price"] is None


def test_item6_a_none_inside_the_bracket_is_not_established_never_a_500(monkeypatch):
    def f(*a):
        s = a[-1]
        return s - 1.2 if s in (0.0, 1.0, 1.5) else None

    monkeypatch.setattr(bcm, "_npv_at", f)
    be = break_even(_ic(), _inputs(_line()), {}, "product", PACK)
    assert be["status"] == "break_even_not_established"


def test_item6_brentq_not_converging_is_not_converged_never_a_500(monkeypatch):
    monkeypatch.setattr(bcm, "_npv_at", lambda *a: a[-1] - 1.2)
    monkeypatch.setattr(bcm, "brentq", _brentq_at(1.2, converged=False))
    be = break_even(_ic(), _inputs(_line()), {}, "product", PACK)
    assert be["status"] == "break_even_not_converged"


def test_item6_real_brentq_non_convergence_is_a_flag_not_an_exception(monkeypatch):
    """`disp=False`: SciPy's own maxiter stop is read from the flag."""
    from scipy.optimize import brentq as real

    def short(f, lo, hi, **kw):
        return real(f, lo, hi, **{**kw, "maxiter": 1})

    monkeypatch.setattr(bcm, "_npv_at", lambda *a: a[-1] ** 3 - 1.7)
    monkeypatch.setattr(bcm, "brentq", short)
    be = break_even(_ic(), _inputs(_line()), {}, "product", PACK)
    assert be["status"] == "break_even_not_converged"


def test_b_an_engine_runtime_error_inside_the_bracket_propagates(monkeypatch):
    """Only brentq's non-convergence is not_converged (GK B #1404 B)."""

    class EngineBug(RuntimeError):
        pass

    def f(*a):
        if a[-1] not in (0.0, 1.0, 1.5):
            raise EngineBug("boom")
        return a[-1] - 1.2

    monkeypatch.setattr(bcm, "_npv_at", f)
    with pytest.raises(EngineBug):
        break_even(_ic(), _inputs(_line()), {}, "product", PACK)


def test_a_a_refused_rerun_is_not_established(monkeypatch):
    monkeypatch.setattr(bcm, "_npv_at", lambda *a: a[-1] - 1.2)

    def refused(*a):
        raise FinanceRefused("staged_incentive_not_supported:plant", "x")

    monkeypatch.setattr(bcm, "_result_at", refused)
    be = break_even(_ic(), _inputs(_line()), {}, "product", PACK)
    assert be["status"] == "break_even_not_established"


@pytest.mark.parametrize(
    "lines",
    [
        (
            dict(name="a", unit="unit", quantity=1.0, price=1.5e308),
            dict(name="b", unit="unit", quantity=1.0, price=1.5e308),
        ),
        (dict(name="a", unit="unit", quantity=1.0, price=1.7e308),),
    ],
)
def test_c_an_engine_overflow_never_reaches_a_pnl_row(lines):
    """Two lines summing past float range, or one escalated past it (GK B #1404 C)."""
    _, r = _run(_inputs(*(_line(**kw) for kw in lines)))
    p = pnl(r, receivable_days=30.0)
    assert "revenue" in p.not_finite
    for row in list(p.rows.values()) + list(p.streams.values()):
        if isinstance(row, np.ndarray):
            assert np.isfinite(row).all()
        elif isinstance(row, list):
            assert all(v is None or math.isfinite(v) for v in row)
    k = kpis(r, p, capacity={"pv": 1.0})
    assert k["ebitda_margin_gross"].reason == "not_finite"
    for v in k.values():
        _strict(v)


def test_item7_a_zero_export_line_that_is_no_difference_line_prices_nothing():
    ic = _ic(
        lines=(
            TemplateLine("export", "energy_export", 0.0, "export"),
            TemplateLine("fom", "fom", -20.0, "opex"),
        )
    )
    ln = _line(
        name="exp",
        category="other",
        unit="MWh",
        quantity=None,
        quantity_source="linked:export.poc_grid@1",
    )
    s = Series("export.poc_grid", "MWh", {None: 10.0}, {})
    bc = build_business_case(ic, _inputs(ln), {"exp": s})
    assert any(ln.key == "exp" for t in bc.case.templates for ln in t.lines)


def test_item7_a_served_product_line_beside_lost_production_builds():
    """G3's drop verdict is slice 06's, never a refusal here."""
    ic = _ic(
        lines=(
            TemplateLine("lp", "lost_production", -50.0, "fuel"),
            TemplateLine("fom", "fom", -20.0, "opex"),
        )
    )
    ln = _line(name="nh3", quantity=None, quantity_source="linked:served_product.nh3@1")
    s = Series("served_product.nh3", "t", {None: 100.0}, {})
    bc = build_business_case(ic, _inputs(ln), {"nh3": s})
    assert any(ln.key == "nh3" for t in bc.case.templates for ln in t.lines)


def test_item8_a_revenue_line_named_like_an_ic_asset_builds():
    bc = build_business_case(_ic(), _inputs(_line(name="pv")), {})
    assert any(ln.key == "pv" for t in bc.case.templates for ln in t.lines)


def test_a_linked_line_without_outputs_is_refused_and_hashes_with_none():
    ic = _ic()
    inputs = _inputs(
        _line(name="nh3", quantity=None, quantity_source="linked:served_product.nh3@1")
    )
    with pytest.raises(LinkedOutputMissing):
        build_business_case(ic, inputs)
    assert business_case_hash(ic, inputs) == business_case_hash(ic, inputs, {})
    assert break_even(ic, _inputs(_line()), None, "product", PACK)["status"] == "ok"
