"""
Edge Investment Case — contracts (Phase 0, WP0.1).

Spec: docs/superpowers/specs/2026-09-26-edge-investment-case-design.md §4
Plan: docs/superpowers/plans/2026-09-26-edge-investment-case-p0-p1.md WP0.1

Models + fixtures only — no solver / billing / finance behaviour.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pydantic
import pytest

from models import commercial as C
from models import finance as F
from models import flex_archetypes as X

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "investment_case"


# ---------------------------------------------------------------- constants


def test_pipeline_and_budget_constants_match_spec():
    assert F.DEFAULT_IC_BUDGET_SOLVES == 30
    assert F.MAX_IC_BUDGET_SOLVES == 120
    assert F.IC_PIPELINE_STAGES == (
        "design_solve",
        "valuation_pf",
        "valuation_realistic",
        "billing",
        "participants",
        "finance",
        "uncertainty",
        "assemble",
    )
    assert F.IC_REPORT_SECTIONS == (
        "design",
        "commercial",
        "dispatch_modes",
        "participants",
        "project",
        "debt",
        "tax",
        "tax_equity",
        "uncertainty",
        "gates",
    )


def test_export_keys_are_a_stable_tuple_and_skeleton_exports_all_as_none():
    assert isinstance(F.IC_EXPORT_KEYS, tuple)
    assert len(F.IC_EXPORT_KEYS) == len(set(F.IC_EXPORT_KEYS))
    pinned = json.loads((FIXTURES / "ic_export_keys.json").read_text())
    assert list(F.IC_EXPORT_KEYS) == pinned
    r = F.InvestmentCaseReport(
        case_id="c1", assumptions_hash="a" * 16,
        completeness=F.empty_ic_section_map())
    exported = F.export_investment_case(r)
    assert set(exported) == set(F.IC_EXPORT_KEYS)
    # Skeleton: every headline figure is None, never 0 (ADR-0001).
    for k in ("project_irr_post_tax", "npv_at_wacc", "min_dscr",
              "cost_at_target_eur", "haircut_pct"):
        assert exported[k] is None


# ---------------------------------------------------------------- (b) status


def test_section_status_enum_enforced():
    with pytest.raises(pydantic.ValidationError):
        F.IcSectionState(status="missing")
    for ok in ("ok", "not_established", "skipped"):
        assert F.IcSectionState(status=ok).status == ok


def test_empty_section_map_covers_every_section_as_not_established():
    m = F.empty_ic_section_map()
    assert set(m) == set(F.IC_REPORT_SECTIONS)
    assert all(v.status == "not_established" for v in m.values())


# ---------------------------------------------------------------- (c) report


def test_report_completeness_keys_must_equal_sections():
    good = F.InvestmentCaseReport(
        case_id="c1", assumptions_hash="a" * 16,
        completeness=F.empty_ic_section_map())
    assert set(good.completeness) == set(F.IC_REPORT_SECTIONS)
    with pytest.raises(pydantic.ValidationError):
        F.InvestmentCaseReport(
            case_id="c1", assumptions_hash="a" * 16,
            completeness={"design": F.IcSectionState(status="ok")})
    extra = dict(F.empty_ic_section_map())
    extra["bogus"] = F.IcSectionState(status="ok")
    with pytest.raises(pydantic.ValidationError):
        F.InvestmentCaseReport(
            case_id="c1", assumptions_hash="a" * 16, completeness=extra)


def test_report_skeleton_fixture_round_trips():
    r = F.InvestmentCaseReport.model_validate_json(
        (FIXTURES / "ic_report_skeleton.json").read_text())
    assert r.reference_design_id is None
    assert r.pipeline.budget_solves == F.DEFAULT_IC_BUDGET_SOLVES
    assert [s.stage for s in r.pipeline.stages] == list(F.IC_PIPELINE_STAGES)
    assert all(v.status == "not_established" for v in r.completeness.values())
    assert F.InvestmentCaseReport.model_validate_json(r.model_dump_json()) == r


def test_pipeline_budget_clamped():
    assert F.IcStudyPipeline().budget_solves == 30
    with pytest.raises(pydantic.ValidationError):
        F.IcStudyPipeline(budget_solves=0)
    with pytest.raises(pydantic.ValidationError):
        F.IcStudyPipeline(budget_solves=121)


# ---------------------------------------------------------------- (d) tariffs


def _energy_item(**kw) -> C.TariffItem:
    base = dict(
        id="energy", kind="energy", unit="per_kwh",
        periods=[C.TariffPeriod(name="all", rate=0.12)],
        settlement="15min", measured_on="import", direction="cost")
    base.update(kw)
    return C.TariffItem(**base)


def test_tariff_item_rejects_non_monotone_tiers():
    with pytest.raises(pydantic.ValidationError):
        _energy_item(tiers=[C.Tier(threshold=100.0, rate=0.1),
                            C.Tier(threshold=50.0, rate=0.09)])
    ok = _energy_item(tiers=[C.Tier(threshold=50.0, rate=0.09),
                             C.Tier(threshold=100.0, rate=0.1)])
    assert len(ok.tiers) == 2


def test_tariff_item_ratchet_share_in_unit_interval():
    with pytest.raises(pydantic.ValidationError):
        _energy_item(kind="demand", unit="per_kw_month",
                     ratchet=C.Ratchet(lookback_months=11, share=0.0))
    with pytest.raises(pydantic.ValidationError):
        _energy_item(kind="demand", unit="per_kw_month",
                     ratchet=C.Ratchet(lookback_months=11, share=1.5))
    ok = _energy_item(kind="demand", unit="per_kw_month",
                      ratchet=C.Ratchet(lookback_months=11, share=1.0))
    assert ok.ratchet.share == 1.0


def test_tariff_item_enums_exact():
    assert set(C.TariffItemKind.__args__) == {
        "energy", "demand", "capacity", "fixed", "certificate", "tax_levy"}
    assert set(C.TariffUnit.__args__) == {
        "per_kwh", "per_kw_month", "per_kw_year", "per_month", "per_kva_year"}
    assert set(C.Settlement.__args__) == {"15min", "30min", "h"}
    assert set(C.MeasuredOn.__args__) == {"import", "export", "net", "peak_import"}


@pytest.mark.parametrize("name", [
    "de_tariff_capacity_tou.json",
    "us_tariff_demand_charge.json",
])
def test_tariff_fixtures_round_trip(name):
    t = C.Tariff.model_validate_json((FIXTURES / name).read_text())
    assert t.items, "fixture must carry at least one item"
    assert C.Tariff.model_validate_json(t.model_dump_json()) == t


def test_tariff_fixture_shapes():
    de = C.Tariff.model_validate_json(
        (FIXTURES / "de_tariff_capacity_tou.json").read_text())
    assert de.jurisdiction == "DE"
    kinds = {i.kind for i in de.items}
    assert {"energy", "capacity"} <= kinds
    us = C.Tariff.model_validate_json(
        (FIXTURES / "us_tariff_demand_charge.json").read_text())
    assert us.jurisdiction == "US"
    demand = [i for i in us.items if i.kind == "demand"]
    assert demand and demand[0].ratchet is not None


# ---------------------------------------------------------------- (e) connection


def test_fca_requires_envelope_or_curtailment_hours():
    with pytest.raises(pydantic.ValidationError):
        C.ConnectionAgreement(kind="fca", import_cap_mw=50.0,
                              available_from=date(2027, 1, 1))
    ok = C.ConnectionAgreement(
        kind="fca", import_cap_mw=50.0, available_from=date(2027, 1, 1),
        curtailment_hours_per_year=200.0)
    assert ok.curtailment_hours_per_year == 200.0
    ok2 = C.ConnectionAgreement.model_validate_json(
        (FIXTURES / "fca_connection.json").read_text())
    assert ok2.kind == "fca" and ok2.envelope is not None


def test_non_firm_dynamic_requires_envelope():
    with pytest.raises(pydantic.ValidationError):
        C.ConnectionAgreement(kind="non_firm_dynamic", import_cap_mw=50.0,
                              available_from=date(2027, 1, 1))


def test_connection_kinds_exact():
    assert set(C.ConnectionKind.__args__) == {
        "firm", "non_firm_static", "non_firm_dynamic", "fca"}


# ---------------------------------------------------------------- (f) participants


def test_participant_and_value_stream_enums_exact():
    assert set(C.ParticipantRole.__args__) == {
        "site_owner", "developer", "investor", "lender", "tax_equity", "dso",
        "tso", "retailer", "tenant", "landlord", "hub_member", "offtaker", "other"}
    assert set(C.ValueStreamKind.__args__) == {
        "energy_import", "energy_export", "network_capacity", "network_energy",
        "demand_charge", "retail_fixed", "ancillary", "dr_availability",
        "dr_activation", "ppa_settlement", "cfd_settlement", "certificates",
        "lease", "eaas_fee", "fuel", "fom", "vom", "capex", "incentive", "tax",
        "debt_service", "other"}


def test_single_owner_participants_fixture_round_trips():
    raw = json.loads((FIXTURES / "single_owner_participants.json").read_text())
    ps = [C.Participant.model_validate(x) for x in raw]
    assert len(ps) == 1 and ps[0].role == "site_owner"
    assert C.Participant.model_validate_json(ps[0].model_dump_json()) == ps[0]


# ---------------------------------------------------------------- (g) cashflow


def test_cashflow_line_provenance_mode_enum():
    with pytest.raises(pydantic.ValidationError):
        F.CashflowLine(year=2030, participant="owner", counterparty="grid",
                       value_stream="energy_import", amount=-1.0,
                       provenance=F.Provenance(source="billing", mode="magic"))
    for mode in ("pf", "realistic"):
        line = F.CashflowLine(
            year=2030, participant="owner", counterparty="grid",
            value_stream="energy_import", amount=-1.0,
            provenance=F.Provenance(source="billing", mode=mode))
        assert line.provenance.mode == mode


# ---------------------------------------------------------------- (i) commercial config


def test_commercial_config_validates_and_dumps_to_plain_dict():
    cfg = C.CommercialConfig.model_validate_json(
        (FIXTURES / "commercial_config_minimal.json").read_text())
    assert cfg.poc_link == "import"
    d = cfg.model_dump(mode="json")
    assert isinstance(d, dict)
    # Plain JSON-able dict: asdict()/json.dumps persistence must work.
    json.dumps(d)
    assert C.CommercialConfig.model_validate(d) == cfg
    with pytest.raises(pydantic.ValidationError):
        C.CommercialConfig(poc_link="")


# ---------------------------------------------------------------- (a) round trips


@pytest.mark.parametrize("model,kwargs", [
    (C.Participant, dict(id="p1", name="Owner", role="site_owner", currency="EUR")),
    (C.ValueStream, dict(id="v1", kind="energy_import")),
    (C.TariffPeriod, dict(name="peak", rate=0.3, months=[1, 2, 12],
                          weekdays=[0, 1, 2, 3, 4], start_hour=17, end_hour=21)),
    (C.PriceSeriesRef, dict(id="s1", version=1, hash="h" * 16, source="user",
                            vintage_year=2026, provider="test")),
    (C.MarketPack, dict(id="m1", region="DE", valid_year=2026,
                        pack_hash="p" * 16)),
    (C.PpaContract, dict(id="ppa1", kind="pay_as_produced", price=65.0,
                         tenor_years=15, seller="dev", buyer="owner",
                         asset_ids=["pv"])),
    (C.CfdContract, dict(id="cfd1", strike=70.0, tenor_years=15,
                         asset_ids=["pv"])),
    (C.DrContract, dict(id="dr1", availability_eur_per_mw_year=20000.0,
                        activation_eur_per_mwh=300.0, max_events=20,
                        max_duration_h=4.0, notice_h=2.0, load_ids=["dc"])),
    (F.DebtTranche, dict(kind="term_loan", gearing=0.6, rate=0.055,
                         tenor_years=15, sculpting="dscr_target",
                         dscr_target=1.3)),
    (F.DepreciationSchedule, dict(method="straight_line", years=20)),
    (F.Incentive, dict(kind="itc", rate=0.3)),
    (F.TaxEquityStructure, dict(kind="partnership_flip", te_share_pre_flip=0.99,
                                te_share_post_flip=0.05, target_flip_irr=0.07,
                                flip_year_cap=10, cash_share_pre_flip=0.3,
                                cash_share_post_flip=0.05, dro_cap=0.5,
                                itc_share_te=0.99)),
    (X.DataCentreLoadSpec, dict(phases=[X.DcPhase(cod=date(2028, 1, 1), it_mw=40.0)],
                                pue_at_design=1.2, redundancy="N+1",
                                critical_share=0.7, curtailable_share=0.3)),
    (X.BessSpec, dict(power_mw=20.0, energy_mwh=80.0, round_trip_efficiency=0.88)),
    (X.EvFleetSpec, dict(fleet_size=50, charger_mw=5.0, daily_energy_mwh=8.0,
                         arrival_hour=18, departure_hour=6)),
    (X.ThermalFlexSpec, dict(heat_demand_mw_peak=10.0, heat_pump_cop_design=3.2,
                             thermal_store_mwh=20.0)),
])
def test_models_round_trip_json(model, kwargs):
    m = model(**kwargs)
    assert model.model_validate_json(m.model_dump_json()) == m


def test_debt_tranche_needs_amount_or_gearing():
    with pytest.raises(pydantic.ValidationError):
        F.DebtTranche(kind="term_loan", rate=0.05, tenor_years=10,
                      sculpting="annuity")
    with pytest.raises(pydantic.ValidationError):
        F.DebtTranche(kind="term_loan", gearing=0.6, rate=0.05, tenor_years=10,
                      sculpting="dscr_target")  # dscr_target missing


def test_flex_spec_shares_bounded():
    with pytest.raises(pydantic.ValidationError):
        X.DataCentreLoadSpec(
            phases=[X.DcPhase(cod=date(2028, 1, 1), it_mw=40.0)],
            pue_at_design=0.9, redundancy="N+1",
            critical_share=0.7, curtailable_share=0.3)  # PUE < 1
    with pytest.raises(pydantic.ValidationError):
        X.DataCentreLoadSpec(
            phases=[X.DcPhase(cod=date(2028, 1, 1), it_mw=40.0)],
            pue_at_design=1.2, redundancy="N+1",
            critical_share=0.8, curtailable_share=0.3)  # shares > 1


def test_models_do_not_import_services():
    import inspect
    for mod in (C, F, X):
        src = inspect.getsource(mod)
        assert "from services" not in src and "import services" not in src
