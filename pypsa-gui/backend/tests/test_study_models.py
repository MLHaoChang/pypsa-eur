"""
Decision-study contracts (guided investment study MVP-1, phase S1).

Plan: docs/superpowers/plans/2026-09-28-guided-investment-study-mvp1-v2.md (S1)
Spec: docs/superpowers/specs/2026-09-28-guided-investment-study-design.md §4

Every model round-trips through JSON, the way the store writes it and the
routes serve it, and a figure the engine could not resolve survives that trip
as `null` with its flag beside it (ADR-0001) — never as `0.0`. A `null`
without a flag, or a flag beside a value, is refused at construction: the
flag is what tells a reader "unavailable" apart from "we forgot".
"""
from __future__ import annotations

import json
from datetime import datetime, UTC

import pytest
from pydantic import ValidationError

from models.energy_hub import SectionState
from models.study import (
    AssumptionsLedger,
    Basis,
    DecisionQuestion,
    DecisionReport,
    DecisionStudy,
    DemandCharge,
    Fidelity,
    Findings,
    InvestmentCase,
    LedgerRow,
    OptionResult,
    OptionSpec,
    Perspective,
    StudyMaturity,
    Tariff,
    VerdictClass,
)

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
SID = "0123456789abcdef0123456789abcdef"


def _roundtrip(model):
    """Serialise the way the store does, parse back, and compare."""
    text = json.dumps(model.model_dump(mode="json", by_alias=True))
    back = type(model).model_validate(json.loads(text))
    assert back == model
    return json.loads(text), back


def _ledger_row(**over):
    row = dict(
        key="battery_inverter_eur_per_kw", label="Battery inverter cost",
        technical_name="overnight_cost", value=213.9, unit="EUR/kW",
        basis="real", currency_year=2020, source="technology-data v0.14.0",
        source_year=2024, source_url=None,
        range={"low": 149.7, "high": 278.1, "source": "assumed"},
        provenance="library", status="default", sensitivity_flag=True,
    )
    row.update(over)
    return LedgerRow.model_validate(row)


def _tariff():
    return Tariff.model_validate(dict(
        tariff_id="t1", name="Seed C&I tariff", source="user", source_year=2026,
        currency="EUR", currency_year=2026, billing_period="month",
        energy_bands=[
            {"label": "peak", "price_per_mwh": 180.0,
             "applies": {"months": [], "weekdays": [0, 1, 2, 3, 4],
                         "hours": list(range(8, 20))}},
            {"label": "off-peak", "price_per_mwh": 95.0, "applies": {}},
        ],
        demand_charge={"price_per_mw_per_period": 9500.0,
                       "basis": "billing_period_peak"},
        capacity_charge=None, fixed_charge_per_period=120.0,
        network_charges=[{"label": "levy", "price": 4.0, "basis": "per_mwh"}],
        export={"price_per_mwh": 40.0, "series_ref": None, "cap_mw": None},
        connection_limit_mw=None, honesty_notes=["seed tariff, not a real offer"],
    ))


def _option_spec():
    return OptionSpec(
        option_id="bess_2h", label="Best with a 2-hour battery",
        one_line="Size a battery with two hours of storage",
        free_assets=["bess"], fixed_assets=[], omitted_assets=[],
        discrete_choices=[{"key": "max_hours", "values": [2]}],
    )


def _option_result():
    return OptionResult.model_validate(dict(
        option_id="bess_2h", label="Best with a 2-hour battery",
        project_ref=None, solve_status="infeasible",
        sizes=[{"asset": "bess", "p_nom_opt": None, "e_nom_opt": None,
                "unavailable": {"p_nom_opt": "solve_infeasible",
                                "e_nom_opt": "solve_infeasible"}}],
        system_cost=None, bill=None, case_ref=None,
        delta_vs_baseline={"npv": None, "payback": None, "capex": None,
                           "co2": None,
                           "unavailable": {"npv": "solve_infeasible",
                                           "payback": "solve_infeasible",
                                           "capex": "solve_infeasible",
                                           "co2": "solve_infeasible"}},
        fidelity="full_study", basis={"terms": "real", "tax": "pre",
                                      "subsidy": "excl"},
        currency_year=2026,
        unavailable={"system_cost": "solve_infeasible",
                     "bill": "solve_infeasible"},
    ))


# ── enums ─────────────────────────────────────────────────────────────────

def test_enum_values_are_the_spec_names():
    assert {p.value for p in Perspective} == {
        "site_owner", "developer", "investor", "multi_party"}
    assert {b.value for b in Basis} == {"real", "nominal"}
    assert {v.value for v in VerdictClass} == {
        "recommended", "marginal", "not_recommended"}
    assert {f.value for f in Fidelity} == {"quick_screen", "full_study"}


def test_section_types_are_the_energy_hub_ones_not_copies():
    import models.energy_hub as eh
    import models.study as st

    assert st.SectionState is eh.SectionState
    assert st.SectionStatus is eh.SectionStatus


# ── round-trips, one per model ────────────────────────────────────────────

def test_decision_study_roundtrip():
    s = DecisionStudy(
        study_id=SID, name="Site A battery", question_id="bess_site",
        base_project="2b7c1f6a-0000-4000-8000-000000000001",
        created_by="u1", created_at=NOW, updated_at=NOW,
        intake={"site": {"connection_mw": 5.0}, "load": None},
    )
    data, back = _roundtrip(s)
    # Defaults are the MVP-1 honest scope, stated, not implied.
    assert data["perspective"] == "site_owner"
    assert data["basis"] == {"terms": "real", "tax": "pre", "subsidy": "excl"}
    assert data["option_projects"] == []
    assert data["budget"] == {"solves_max": 12, "solves_used": 0}
    # An intake answer the user left empty stays null, not dropped.
    assert data["intake"]["load"] is None
    # The maturity badge is not established until S2 computes it.
    assert data["maturity"]["status"] == "not_established"
    assert data["maturity"]["class"] is None
    assert data["maturity"]["unavailable"] == {"accuracy_band": "not_computed"}


def test_study_id_must_be_uuid4_hex():
    with pytest.raises(ValidationError):
        DecisionStudy(study_id="../../etc", name="x", question_id="bess_site",
                      base_project="p", created_at=NOW, updated_at=NOW)


def test_study_maturity_roundtrip_with_class_alias():
    m = StudyMaturity.model_validate({
        "status": "ok", "class": "screening",
        "accuracy_band": {"low_pct": -50.0, "high_pct": 100.0},
        "reasons": ["load is synthetic"],
    })
    data, _ = _roundtrip(m)
    assert data["class"] == "screening"


def test_decision_question_roundtrip():
    q = DecisionQuestion(
        question_id="bess_site", title="Do I need a battery at my site?",
        one_line="Battery against grid-only supply",
        archetype="strong_grid", mandatory_inputs=["load", "tariff"],
        defaults=[_ledger_row()], network_pack="bess_site_pack",
        baseline_definition={"text": "Grid supply with existing assets",
                             "fixed_assets": []},
        options=[_option_spec()],
        headline_metrics=["npv", "irr", "payback_simple"],
        value_streams=["demand_charge_savings", "energy_arbitrage"],
        key_drivers=["battery_inverter_eur_per_kw"],
        report_template_id=None,
    )
    _roundtrip(q)


def test_option_spec_roundtrip():
    _roundtrip(_option_spec())


def test_ledger_row_roundtrip_carries_currency_year():
    data, _ = _roundtrip(_ledger_row())
    assert data["currency_year"] == 2020
    assert data["range"]["source"] == "assumed"


def test_ledger_row_null_value_keeps_its_flag():
    row = _ledger_row(value=None, status="needs_attention",
                      unavailable={"value": "no_library_default"})
    data, _ = _roundtrip(row)
    assert data["value"] is None
    assert data["unavailable"] == {"value": "no_library_default"}


def test_assumptions_ledger_roundtrip_and_unique_keys():
    led = AssumptionsLedger(ledger_version="technology-data v0.14.0",
                            rows=[_ledger_row()], honesty_notes=("seed",))
    _roundtrip(led)
    with pytest.raises(ValidationError, match="duplicate"):
        AssumptionsLedger(ledger_version="v", rows=[_ledger_row(), _ledger_row()])


def test_demand_charge_roundtrip_and_ratchet_needs_its_terms():
    _roundtrip(DemandCharge(price_per_mw_per_period=9500.0,
                            basis="billing_period_peak"))
    _roundtrip(DemandCharge(price_per_mw_per_period=9500.0, basis="ratchet",
                            ratchet={"months": 11, "share": 0.8}))
    with pytest.raises(ValidationError, match="ratchet"):
        DemandCharge(price_per_mw_per_period=1.0, basis="ratchet")


def test_tariff_roundtrip():
    data, _ = _roundtrip(_tariff())
    assert data["capacity_charge"] is None
    assert data["demand_charge"]["basis"] == "billing_period_peak"


def test_tariff_time_rule_rejects_an_hour_out_of_range():
    bad = _tariff().model_dump(mode="json")
    bad["energy_bands"][0]["applies"]["hours"] = [24]
    with pytest.raises(ValidationError):
        Tariff.model_validate(bad)


def test_option_result_roundtrip_with_every_figure_null():
    data, _ = _roundtrip(_option_result())
    assert data["system_cost"] is None and data["bill"] is None
    assert data["sizes"][0]["p_nom_opt"] is None
    assert data["delta_vs_baseline"]["npv"] is None
    assert data["unavailable"]["system_cost"] == "solve_infeasible"


def test_investment_case_roundtrip_irr_null_with_flag():
    case = InvestmentCase.model_validate(dict(
        case_id="c1", study_id=SID, option_id="bess_2h", status="ok",
        perspective="site_owner",
        basis={"terms": "real", "tax": "pre", "subsidy": "excl"},
        currency_year=2026, fidelity="full_study", engine="cash_flow_expander",
        horizon_years=1, discount_rate=0.05, wacc=None, inflation=None,
        years=[{
            "year": 0, "capex": 1.0e6, "replacements": 0.0, "opex_fixed": 0.0,
            "opex_variable": 0.0, "fuel": 0.0, "co2_cost": 0.0,
            "bill_baseline": None, "bill_option": None, "savings": 0.0,
            "contract_revenue": 0.0, "market_revenue_at_duals": 0.0,
            "resilience_value": None, "tax": None, "depreciation": None,
            "debt_service": None, "net_cash_flow": -1.0e6,
            "discounted_cash_flow": -1.0e6, "cumulative_discounted": -1.0e6,
            "unavailable": {"bill_baseline": "build_year",
                            "bill_option": "build_year",
                            "resilience_value": "not_in_mvp1",
                            "tax": "pre_tax_basis",
                            "depreciation": "pre_tax_basis",
                            "debt_service": "no_financing_in_mvp1"},
        }],
        kpis={"npv": -1.0e6, "irr": None, "payback_simple": None,
              "payback_discounted": None, "lcoe": None, "lcos": None,
              "lcoh": None, "dscr_min": None, "capex_total": 1.0e6,
              "unavailable": {"irr": "no_sign_change",
                              "payback_simple": "never_pays_back",
                              "payback_discounted": "never_pays_back",
                              "lcoe": "not_applicable", "lcos": "no_discharge",
                              "lcoh": "not_applicable",
                              "dscr_min": "no_financing_in_mvp1"}},
        value_streams=[{"label": "Demand-charge savings", "annual_value": None,
                        "share": None, "engine": "bill_calculator",
                        "basis": {"terms": "real", "tax": "pre",
                                  "subsidy": "excl"},
                        "unavailable": {"annual_value": "no_demand_charge",
                                        "share": "no_demand_charge"}}],
        sources={"cost_breakdown_ref": None, "asset_economics_ref": None,
                 "bill_refs": [], "mc_ref": None},
        completeness={"cash_flow": "ok", "resilience": "skipped"},
        honesty_notes=("pre-tax, real, no subsidy",),
    ))
    data, _ = _roundtrip(case)
    assert data["kpis"]["irr"] is None
    assert data["kpis"]["unavailable"]["irr"] == "no_sign_change"
    assert data["years"][0]["bill_baseline"] is None
    assert data["value_streams"][0]["annual_value"] is None


def test_findings_roundtrip_nulls_and_sections():
    f = Findings.model_validate(dict(
        options=[_option_result().model_dump(mode="json")],
        baseline={"project_ref": None, "solve_status": "not_run", "bill": None,
                  "case_ref": None, "unavailable": {"bill": "not_run"}},
        verdict={"status": "not_established", "class": None, "sentence": None,
                 "headline_kpis": [{
                     "key": "npv", "label": "Net present value", "value": None,
                     "unit": "EUR", "basis": {"terms": "real", "tax": "pre",
                                              "subsidy": "excl"},
                     "currency_year": 2026, "engine": "cash_flow_expander",
                     "fidelity": "full_study", "unavailable": "not_run"}],
                 "drivers": [], "main_caveat": None},
        robustness={"status": "skipped", "method": "redispatch_fixed_sizes",
                    "tornado": [{"key": "k", "label": "K", "low_value": 1.0,
                                 "high_value": 2.0, "npv_low": None,
                                 "npv_high": None, "swing": None,
                                 "unavailable": {"npv_low": "not_run",
                                                 "npv_high": "not_run",
                                                 "swing": "not_run"}}],
                    "breakevens": [], "option_map": None, "dotplot": None},
        explain=[], completeness={"verdict": "not_established",
                                  "robustness": "skipped"},
        honesty_notes=("npv_nonnegative_at_optimum_by_construction",),
    ))
    data, _ = _roundtrip(f)
    assert data["verdict"]["class"] is None
    assert data["verdict"]["headline_kpis"][0]["value"] is None
    assert data["verdict"]["headline_kpis"][0]["unavailable"] == "not_run"


def test_findings_completeness_must_agree_with_sections():
    with pytest.raises(ValidationError, match="disagrees"):
        Findings.model_validate(dict(
            baseline={"project_ref": None, "solve_status": "not_run",
                      "bill": None, "case_ref": None,
                      "unavailable": {"bill": "not_run"}},
            verdict={"status": "not_established"},
            robustness={"status": "skipped"},
            completeness={"verdict": "ok"},
        ))


def test_decision_report_roundtrip():
    r = DecisionReport.model_validate(dict(
        study_id=SID, question_id="bess_site", generated_at=NOW.isoformat(),
        basis={"terms": "real", "tax": "pre", "subsidy": "excl"},
        currency_year=2026, fidelity="full_study",
        maturity={"status": "not_established", "class": None,
                  "accuracy_band": None,
                  "unavailable": {"accuracy_band": "not_computed"}},
        sections={
            "verdict": {"status": "ok", "facts": {
                "npv": {"key": "npv", "label": "NPV", "value": None,
                        "unit": "EUR", "basis": {"terms": "real", "tax": "pre",
                                                 "subsidy": "excl"},
                        "currency_year": 2026, "engine": "cash_flow_expander",
                        "fidelity": "full_study", "unavailable": "not_run"}},
                "figures": [], "prose": [{"text": "{{npv}}", "ai": False,
                                          "reviewed": True}]},
            "resilience": {"status": "skipped", "note": "not in MVP-1"},
        },
        honesty_notes=("pre-tax",),
    ))
    data, back = _roundtrip(r)
    assert isinstance(back.sections["verdict"], SectionState)
    assert data["sections"]["verdict"]["facts"]["npv"]["value"] is None
    assert data["completeness"] == {"verdict": "ok", "resilience": "skipped"}


# ── ADR-0001 enforcement ──────────────────────────────────────────────────

@pytest.mark.parametrize("build", [
    # null without a flag
    lambda: _ledger_row(value=None),
    # a flag beside a value
    lambda: _ledger_row(unavailable={"value": "x"}),
    # a flag naming a field that is not a figure
    lambda: _ledger_row(unavailable={"label": "x"}),
])
def test_null_without_flag_and_flag_beside_value_are_refused(build):
    with pytest.raises(ValidationError, match="ADR-0001"):
        build()


def test_headline_figure_null_needs_its_reason():
    from models.study import Figure

    with pytest.raises(ValidationError, match="ADR-0001"):
        Figure(key="npv", label="NPV", value=None, unit="EUR",
               engine="cash_flow_expander")
    with pytest.raises(ValidationError, match="ADR-0001"):
        Figure(key="npv", label="NPV", value=1.0, unit="EUR",
               engine="cash_flow_expander", unavailable="not_run")


def test_a_real_zero_stays_zero():
    """0.0 is a legitimate result and needs no flag (ADR-0001)."""
    data, _ = _roundtrip(_ledger_row(value=0.0))
    assert data["value"] == 0.0
    assert data["unavailable"] == {}


# ── Gate S1 SB-1: the fields the later phases name exist as typed ──────────

def test_later_phase_fields_exist_as_typed():
    from models.study import (
        CashFlowYear, CaseKpis, DecisionReport, Findings, FindingsHashes,
        InvestmentCase, Robustness,
    )

    assert "salvage" in CashFlowYear.model_fields
    assert "salvage_eur" in CaseKpis.model_fields
    assert "salvage_basis" in InvestmentCase.model_fields
    assert {"pending", "note"} <= set(Robustness.model_fields)
    assert {"options_status", "pending_options", "hashes"} <= set(Findings.model_fields)
    assert {"stale", "stale_reasons", "hashes_at_findings"} <= set(DecisionReport.model_fields)
    h = FindingsHashes(ledger_hash="abc", option_network_hashes={"u1": "h1"})
    assert h.model_dump()["option_network_hashes"] == {"u1": "h1"}


def test_findings_completeness_names_the_options_section():
    from models.study import BaselineResult, Findings

    f = Findings(baseline=BaselineResult(bill=None, unavailable={"bill": "not_run"}),
                 options_status="not_established", pending_options=["bess_2h"])
    assert f.completeness["options"] == "not_established"
    assert f.pending_options == ["bess_2h"]
