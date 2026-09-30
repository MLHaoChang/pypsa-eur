"""
The finance axis and operating cashflows (IC P4 plan WP4.1): SAM parity on
energy, revenue, O&M and EBITDA (S1–S3f), and the hand oracles F4
(escalation, indexation, tenor, base year before COD) and F5 (terminal value,
replacement), plus the refusals and the not-established paths (plan C12).
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from models.finance import FinanceInputs, TerminalValueRule
from services.finance.case import (
    CONTRACT_CLASS, AssetFinance, FinanceCase, FinanceRefused, Template, TemplateLine,
)
from services.finance.cashflow import (
    STREAM_CLASS, build_operating, degradation_factor, esc_class_for,
)
from services.finance.metrics import irr, npv
from services.finance.timeline import build_timeline
from tests.fixtures.investment_case.sam import sam_case as S


def _tol(expected):
    return np.maximum(1.0, 1e-6 * np.abs(np.asarray(expected, dtype=float)))


def _close(ours, sam):
    ours, sam = np.asarray(ours, dtype=float), np.asarray(sam, dtype=float)
    assert ours.shape == sam.shape
    bad = np.abs(ours - sam) > _tol(sam)
    assert not bad.any(), f"years {np.flatnonzero(bad)}: ours {ours[bad]} sam {sam[bad]}"


@pytest.mark.parametrize("name", S.CASES)
def test_sam_parity_energy_revenue_om_and_ebitda(name):
    case = S.to_finance_case(name)
    tl = build_timeline(case)
    op = build_operating(case, tl)
    e = S.sam_expected(name)["arrays"]
    assert op.status == {"operating": "ok", "capex": "ok", "terminal": "ok"}, op.reasons
    _close(op.energy_mwh["pv"], e["cf_energy_net"])
    _close(op.revenue + op.terminal, e["cf_total_revenue"])   # SAM counts salvage as revenue
    _close(op.costs, e["cf_operating_expenses"])
    _close(op.ebitda, e["cf_ebitda"])               # salvage inside EBITDA (S3, S3f)
    assert op.capex[0] == pytest.approx(S.sam_params(name).installed_cost)


def _case(lines, *, fin_over=None, base_year=2031, cod=date(2033, 1, 1), templates=None,
          assets=None, energy=None):
    kw = dict(financial_close=date(2031, 6, 1), cod_by_asset={"pv": cod},
              capex_phasing=[0.6, 0.4], contingency_share=0.1, analysis_years=10,
              escalation={"opex": 0.02, "tariff": 0.03, "export": 0.01, "ppa": 0.015,
                          "capex": 0.02},
              degradation_by_asset={"pv": 0.01})
    kw.update(fin_over or {})
    fin = FinanceInputs(**kw)
    return FinanceCase(inputs=fin, owner="owner", base_year=base_year, cod=cod,
                       templates=templates or (Template(first_year=cod.year, lines=tuple(lines),
                                                        energy_mwh=energy or {"pv": 1000.0}),),
                       assets=assets or (AssetFinance("pv", "Generator", 1_000_000.0, 30.0),))


def test_f4_escalation_indexation_tenor_and_a_base_year_before_cod():
    """F4 by hand: base year 2031, COD 2033 (two years later), a PPA with its
    own 2.5 % indexation ending after 8 operating years, a tariff line at 3 %,
    an export line at 1 % degrading with pv at 1 %/yr."""
    ppa = TemplateLine("ppa", "ppa_settlement", 50_000.0, CONTRACT_CLASS, indexation=0.025,
                       tenor_years=8, contract_id="p1")
    bill = TemplateLine("bill", "energy_import", -20_000.0, esc_class_for("energy_import"))
    exp = TemplateLine("exp", "energy_export", 10_000.0, esc_class_for("energy_export"),
                       degrades_with="pv")
    case = _case([ppa, bill, exp])
    tl = build_timeline(case)
    op = build_operating(case, tl)
    assert tl.y0 == 2031 and tl.cod_year == 2033 and tl.construction_years == [2031, 2032]
    for i, y in enumerate(tl.years):
        k = y - 2033 + 1
        if y < 2033:
            assert op.lines["ppa"][i] == op.lines["bill"][i] == 0.0
            continue
        want_ppa = 50_000.0 * 1.025 ** (y - 2031) if k <= 8 else 0.0
        assert op.lines["ppa"][i] == pytest.approx(want_ppa, rel=1e-12)
        assert op.lines["bill"][i] == pytest.approx(-20_000.0 * 1.03 ** (y - 2031), rel=1e-12)
        assert op.lines["exp"][i] == pytest.approx(10_000.0 * 1.01 ** (y - 2031) * 0.99 ** (k - 1),
                                                   rel=1e-12)
    assert "contract_ends:p1:2040" in op.flags
    # Capex: 1 M × 1.1 contingency, 60/40 over the two construction years.
    assert op.capex[0] == pytest.approx(660_000.0) and op.capex[1] == pytest.approx(440_000.0)
    assert op.revenue[2] == pytest.approx(op.lines["ppa"][2] + op.lines["exp"][2])
    assert op.costs[2] == pytest.approx(-op.lines["bill"][2])


def test_f5_terminal_values_and_replacement():
    om = TemplateLine("om", "fom", -10_000.0, "opex")
    rev = TemplateLine("rev", "energy_export", 100_000.0, "export")
    fixed = _case([om, rev], fin_over={"terminal_value": TerminalValueRule(method="fixed",
                                                                           value=50_000.0),
                                       "replacement_capex": [(2037, "pv", 200_000.0)]})
    tl = build_timeline(fixed)
    op = build_operating(fixed, tl)
    assert op.terminal[-1] == 50_000.0 and op.terminal[:-1].sum() == 0.0
    assert op.ebitda[-1] == pytest.approx(op.revenue[-1] - op.costs[-1] + 50_000.0)
    assert op.replacement[tl.index(2037)] == pytest.approx(200_000.0 * 1.02 ** (2037 - 2031))
    mult = _case([om, rev], fin_over={"terminal_value": TerminalValueRule(
        method="multiple_of_ebitda", value=3.0)})
    op2 = build_operating(mult, build_timeline(mult))
    assert op2.terminal[-1] == pytest.approx(3.0 * (op2.revenue[-1] - op2.costs[-1]))
    book = _case([om, rev], fin_over={"terminal_value": TerminalValueRule(method="book_value")})
    op3 = build_operating(book, build_timeline(book))
    assert op3.status["terminal"] == "not_established" and op3.ebitda is None


def test_unknown_inputs_are_not_established_never_zero():
    rev = TemplateLine("rev", "energy_export", 100_000.0, "export", degrades_with="bess")
    none_line = TemplateLine("bill", "energy_import", None, "tariff")
    op = build_operating(c := _case([rev, none_line], fin_over={"contingency_share": None,
                                                                "escalation": {"opex": 0.02}}),
                         build_timeline(c))
    assert op.status["operating"] == "not_established" and op.revenue is None
    assert {"line_not_established:bill", "escalation_missing:export:rev"} <= set(op.reasons["operating"])
    assert op.status["capex"] == "not_established" and op.capex is None
    assert "contingency_share_missing" in op.reasons["capex"]
    # A degradation entry missing for the line's asset.
    op2 = build_operating(c2 := _case([rev]), build_timeline(c2))
    assert "degradation_missing:bess" in op2.reasons["operating"]
    op3 = build_operating(c3 := _case([rev], assets=(AssetFinance("pv", "Generator", None, 30.0),)),
                          build_timeline(c3))
    assert "overnight_cost_missing:pv" in op3.reasons["capex"]


def test_the_axis_refusals():
    rev = TemplateLine("rev", "energy_export", 1.0, "export")
    with pytest.raises(FinanceRefused) as e:
        build_timeline(_case([rev], fin_over={"analysis_years": None}))
    assert e.value.code == "analysis_years_missing"
    with pytest.raises(FinanceRefused) as e:
        build_timeline(_case([rev], fin_over={"cod_by_asset": {"pv": date(2033, 1, 1),
                                                               "bess": date(2034, 1, 1)}}))
    assert e.value.code == "cod_mismatch"
    with pytest.raises(FinanceRefused) as e:
        build_timeline(_case([rev], fin_over={"financial_close": date(2034, 1, 1)}))
    assert e.value.code == "financial_close_after_cod"
    with pytest.raises(FinanceRefused) as e:
        build_timeline(_case([rev], fin_over={"capex_phasing": [1.0]}))
    assert e.value.code == "capex_phasing_mismatch"
    with pytest.raises(FinanceRefused) as e:
        build_timeline(_case([rev], assets=(AssetFinance("pv", "Generator", 1.0, 5.0),)))
    assert e.value.code == "asset_lifetime_short"
    build_timeline(_case([rev], assets=(AssetFinance("pv", "Generator", 1.0, 5.0),),
                         fin_over={"replacement_capex": [(2037, "pv", 1.0)]}))


def test_a_multi_period_template_applies_from_its_first_year():
    a = Template(2033, (TemplateLine("rev", "energy_export", 100.0, "export"),))
    b = Template(2037, (TemplateLine("rev", "energy_export", 200.0, "export"),))
    case = _case([], templates=(a, b), fin_over={"escalation": {"export": 0.0, "capex": 0.0}})
    op = build_operating(case, build_timeline(case))
    tl = op.tl
    assert op.lines["rev"][tl.index(2036)] == 100.0 and op.lines["rev"][tl.index(2037)] == 200.0
    assert op.lines["rev"][tl.index(2042)] == 200.0


def test_degradation_from_a_list():
    f = degradation_factor([0.02, 0.01], np.array([0, 1, 2, 3, 4]))
    assert list(np.round(f, 12)) == [0.0, 1.0, 0.98, round(0.98 * 0.99, 12), round(0.98 * 0.99 ** 2, 12)]


def test_irr_and_npv_core():
    c = [-100.0, 60.0, 60.0]
    r, flags = irr(c)
    assert r == pytest.approx(0.13066238629180748, abs=1e-12) and flags == []
    assert npv(r, c) == pytest.approx(0.0, abs=1e-9)
    assert irr([100.0, 10.0])[0] is None and irr([100.0, 10.0])[1] == ["irr_not_established:no_sign_change"]
    r2, f2 = irr([-100.0, 230.0, -132.0])            # roots 10 % and 20 %
    assert r2 == pytest.approx(0.1) and "irr_multiple_sign_changes" in f2
    assert npv(None, c) is None and npv(0.1, [1.0, float("nan")]) is None


# ── WP4.1 review round 1 ─────────────────────────────────────────────────────


def test_irr_is_never_zero_on_zero_cash_and_names_an_out_of_range_root():
    assert irr([0.0] * 10) == (None, ["irr_not_established:no_sign_change"])     # #1
    assert irr([-1.0, 20.0]) == (None, ["irr_not_established:no_root_in_scan"])   # #7: IRR 1900 %
    assert npv(-1.0, [1.0, 1.0]) is None


def test_energy_without_a_degradation_entry_is_not_established():
    rev = TemplateLine("rev", "energy_export", 100.0, "export")
    case = _case([rev], energy={"pv": 1000.0, "wind": 500.0})
    op = build_operating(case, build_timeline(case))
    assert op.energy_mwh["wind"] is None and "degradation_missing:wind" in op.reasons["operating"]
    assert op.status["operating"] == "not_established"


def test_a_later_template_in_its_own_money_year_is_not_escalated_twice():
    """#4: a period-2037 template stated in 2037 money escalates from 2037."""
    a = Template(2033, (TemplateLine("rev", "energy_export", 100.0, "export"),
                        TemplateLine("ppa", "ppa_settlement", 50.0, CONTRACT_CLASS, indexation=0.03)))
    b = Template(2037, (TemplateLine("rev", "energy_export", 200.0, "export"),
                        TemplateLine("ppa", "ppa_settlement", 80.0, CONTRACT_CLASS, indexation=0.03)),
                 money_year=2037)
    case = _case([], templates=(a, b), fin_over={"escalation": {"export": 0.05, "capex": 0.0}})
    op = build_operating(case, build_timeline(case))
    tl = op.tl
    assert op.lines["rev"][tl.index(2036)] == pytest.approx(100.0 * 1.05 ** (2036 - 2031))
    assert op.lines["rev"][tl.index(2037)] == pytest.approx(200.0)
    assert op.lines["rev"][tl.index(2039)] == pytest.approx(200.0 * 1.05 ** 2)
    assert op.lines["ppa"][tl.index(2038)] == pytest.approx(80.0 * 1.03)


def test_more_axis_refusals():
    rev = TemplateLine("rev", "energy_export", 1.0, "export")
    for over, code in (({"replacement_capex": [(2037, "ghost", 1.0)]}, "replacement_unknown_asset"),
                       ({"cod_by_asset": {"pv": date(2040, 1, 1)}}, "cod_mismatch"),
                       ({"capex_phasing": [1.5, -0.5]}, "capex_phasing_negative")):
        with pytest.raises(FinanceRefused) as e:
            build_timeline(_case([rev], fin_over=over))
        assert e.value.code == code


def test_every_ledger_kind_has_an_escalation_class():
    """#6: the class of each `ValueStreamKind` (plan WP4.1 table); the two
    finance-side kinds have none."""
    import typing

    from models.commercial import ValueStreamKind
    kinds = set(typing.get_args(ValueStreamKind))
    assert set(STREAM_CLASS) == kinds - {"incentive", "debt_service"}
    want = {"energy_import": "tariff", "network_capacity": "tariff", "network_energy": "tariff",
            "demand_charge": "tariff", "retail_fixed": "tariff", "certificates": "tariff",
            "tax": "tariff", "energy_export": "export", "ancillary": "export",
            "ppa_settlement": CONTRACT_CLASS, "cfd_settlement": CONTRACT_CLASS,
            "lease": CONTRACT_CLASS, "eaas_fee": CONTRACT_CLASS, "dr_availability": CONTRACT_CLASS,
            "dr_activation": CONTRACT_CLASS, "fom": "opex", "vom": "opex", "other": "opex",
            "fuel": "fuel", "capex": "capex"}
    assert {kk: esc_class_for(kk) for kk in want} == want
    for kk in ("incentive", "debt_service"):
        with pytest.raises(ValueError):
            esc_class_for(kk)


def test_reason_names_the_ppa_rate_and_an_ended_contract_is_not_read():
    """#10: a contract with no indexation and no `ppa` rate names `ppa`; a
    `None` amount after the tenor ended is never read."""
    no_idx = TemplateLine("dr", "dr_availability", 10.0, CONTRACT_CLASS)
    op = build_operating(c := _case([no_idx], fin_over={"escalation": {"capex": 0.0}}),
                         build_timeline(c))
    assert "escalation_missing:ppa:dr" in op.reasons["operating"]
    a = Template(2033, (TemplateLine("ppa", "ppa_settlement", 10.0, CONTRACT_CLASS,
                                     indexation=0.0, tenor_years=2, contract_id="p"),))
    b = Template(2036, (TemplateLine("ppa", "ppa_settlement", None, CONTRACT_CLASS,
                                     indexation=0.0, tenor_years=2, contract_id="p"),))
    op2 = build_operating(c2 := _case([], templates=(a, b)), build_timeline(c2))
    assert op2.status["operating"] == "ok" and op2.flags == ["contract_ends:p:2034"]


def test_the_sam_mapping_refuses_what_it_cannot_express(monkeypatch):
    """#3: per-MWh O&M and schedule (list) inputs are refused, never dropped."""
    real = S.load_case

    def with_(group, key, value):
        def patched(name):
            d = real(name)
            d["sam_inputs"][group][key] = value
            return d
        return patched

    monkeypatch.setattr(S, "load_case", with_("SystemCosts", "om_production", [3.0]))
    with pytest.raises(S.SamMappingError, match="om_production"):
        S.sam_params("s1")
    monkeypatch.setattr(S, "load_case", with_("SystemOutput", "degradation", [0.5, 0.6, 0.7]))
    with pytest.raises(S.SamMappingError, match="schedule"):
        S.sam_params("s1")

