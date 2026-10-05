"""
The engine facade frozen for the guided study (IC plan
`2026-10-05-one-investment-engine-two-faces.md` §6; GS Q4, IC U1 follow-up).

Every facade name is imported and its call signature pinned with
`inspect.signature` against the expectation below, so any change — a renamed
or reordered parameter, a new required one, a changed default or annotation —
fails here loudly. A deliberate change updates this table AND the §6 note in
the plan in the same commit, so the guided-study session sees it. The
`CommercialConfig` / `FinanceInputs` field names the ledger compiles to are
pinned too (additions included: GS compiles to exactly these).
"""
from __future__ import annotations

import dataclasses
import importlib
import inspect

import pytest

SIGNATURES: dict[str, str] = {
    "services.commercial.billing.bill_site":
        "(n, commercial, *, meter_history: 'dict | None' = None) -> 'SiteBill'",
    "services.commercial.billing.rate_meter":
        "(n, commercial, import_mw, export_mw, *, meter_history: 'dict | None' = None)"
        " -> 'SiteBill'",
    "services.commercial.tariff_engine.rate":
        "(dispatch: 'pd.DataFrame', tariff: 'Tariff', *, step_hours, timezone: 'str | None', "
        "billing_period: 'tuple | None' = None, represents_hours=None, "
        "meter_history: 'pd.DataFrame | None' = None, capacity_kw: 'float | None' = None, "
        "power_factor: 'float | None' = None) -> 'RatingResult'",
    "services.results.billing.compute_billing":
        "(n, cfg, *, state: 'dict | None' = None, result_df: 'Callable[..., Any]')"
        " -> 'dict | None'",
    "services.results.billing.compute_billing_preview": "(n, cfg, tariff) -> 'dict | None'",
    "services.commercial.lp_bindings.materialise_poc_prices":
        "(n, commercial: 'dict | CommercialConfig | None', *, log=None, "
        "solve_strategy: 'str' = 'full', multi_period: 'bool' = False) -> 'Applied'",
    "services.commercial.value_flow_templates.build":
        "(name: 'str', n, commercial: 'CommercialConfig', *, dsr_buses=()) -> 'TemplateResult'",
    "services.results.finance_case.build_finance_case":
        "(n, cfg, fin, *, result_df, lost_load=None, owner: 'str | None' = None)"
        " -> 'FinanceCase'",
    "services.finance.case.FinanceRefused": "(code: 'str', detail: 'str' = '')",
    "services.finance.engine.run_case":
        "(case: 'FinanceCase', pack: 'JurisdictionPack | None' = None, *, "
        "layers: 'tuple[TaxLayer, ...] | None' = None, _solve: 'bool' = True)"
        " -> 'FinanceResult'",
    "services.finance.engine.solve_ppa":
        "(case: 'FinanceCase', pack: 'JurisdictionPack | None' = None, *, layers=None)"
        " -> 'dict[str, float | str | None]'",
    "services.finance.engine.payback": "(cash: 'np.ndarray | None') -> 'float | None'",
    "services.finance.metrics.irr": "(cash) -> 'tuple[float | None, list[str]]'",
    "services.finance.metrics.npv": "(rate: 'float | None', cash) -> 'float | None'",
    "services.finance.report.assemble_finance_sections":
        "(result, case, *, case_id: 'str' = 'investment_case', "
        "assumptions_hash: 'str | None' = None, packs: 'dict[str, str] | None' = None, "
        "provenance: 'dict[str, Any] | None' = None) -> 'InvestmentCaseReport'",
    "services.finance.export_xlsx.build_workbook":
        "(report: 'InvestmentCaseReport', *, project: 'str | None' = None) -> 'bytes'",
    "services.finance.packs.base.load_pack":
        "(jurisdiction: 'str', *, as_of: 'date') -> 'JurisdictionPack'",
    "services.library.items.resolve":
        "(db: 'DBSession', org_id: 'UUID', ref: 'M.LibraryItemRef', *, "
        "root: 'Path | None' = None) -> 'dict'",
    "services.library.items.put_item":
        "(db: 'DBSession', org_id: 'UUID', kind: 'str', name: 'str', payload: 'dict', "
        "meta: 'dict | None' = None, *, created_by: 'UUID | None' = None, "
        "root: 'Path | None' = None) -> 'M.LibraryItemRef'",
    "services.library.series_store.put_series":
        "(db: 'DBSession', org_id: 'UUID', name: 'str', series: 'pd.Series', meta: 'dict', *, "
        "created_by: 'UUID | None' = None, root: 'Path | None' = None) -> 'PriceSeriesRef'",
    "services.commercial.cost_rows.commercial_cost_terms":
        "(n, commercial: 'dict | None', *, years=None) -> 'dict'",
    "services.results.value_flows.export_revenue":
        "(n, commercial) -> 'dict[str, float | None]'",
    # TODO(orchestrator, after C lands): the context-level binding helper —
    # "services.commercial.binding.bind_commercial_on_context": "(ctx, commercial, *, ...)",
    # TODO(orchestrator, after A lands): the generic defaults-pack loader and
    # the flat export-series helper (U1 a, e) —
    # "services.library.<defaults pack module>.<loader>": "(...)",
    # "services.library.<export helper module>.<helper>": "(...)",
}

# Result types the guided study reads by attribute.
DATACLASS_FIELDS: dict[str, tuple[str, ...]] = {
    "services.commercial.tariff_engine.RatingResult": (
        "lines", "fixed_lines", "monthly", "annual", "per_item", "total", "total_supported",
        "flags", "notes", "unsupported_items", "per_item_sampled", "demand_lines"),
}

MODEL_FIELDS: dict[str, tuple[str, ...]] = {
    "models.commercial.CommercialConfig": (
        "connection", "contracts", "demand_items", "export_link", "export_price_ref",
        "grid_cfe_share_ref", "group_cap_mw", "group_contract", "group_members",
        "import_tariff", "import_tariff_id", "import_tariff_ref", "initial_peak_lower_bound",
        "meter_history_energy_kwh", "meter_history_peaks_kw", "poc_link", "power_factor",
        "site_party", "timezone", "value_flows"),
    "models.finance.FinanceInputs": (
        "acquisition_date", "analysis_years", "annualise", "capex_phasing", "cod_by_asset",
        "construction_months_by_asset", "construction_start", "contingency_share",
        "cost_of_equity", "currency", "currency_year", "debt", "degradation_by_asset",
        "depreciation_class_by_asset", "escalation", "financial_close", "financing_fee_tax",
        "hebesatz_pct", "incentives", "inflation", "participants", "price_basis", "pwa_met",
        "replacement_capex", "reserves_rate", "small_business_163j", "solve_ppa", "state_rate",
        "tax_equity", "tax_losses", "tax_pack_id", "terminal_value", "wacc_nominal"),
}


def _get(path: str):
    module, _, name = path.rpartition(".")
    return getattr(importlib.import_module(module), name)


@pytest.mark.parametrize("path", sorted(SIGNATURES))
def test_a_facade_signature_is_frozen(path):
    got = str(inspect.signature(_get(path)))
    assert got == SIGNATURES[path], (
        f"{path} changed: {got!r}. The guided study builds on it — update the IC plan §6 "
        "and this pin together.")


@pytest.mark.parametrize("path", sorted(DATACLASS_FIELDS))
def test_a_facade_result_type_keeps_its_fields(path):
    obj = _get(path)
    assert dataclasses.is_dataclass(obj)
    assert tuple(f.name for f in dataclasses.fields(obj)) == DATACLASS_FIELDS[path]


@pytest.mark.parametrize("path", sorted(MODEL_FIELDS))
def test_the_compiled_model_field_names_are_frozen(path):
    assert sorted(_get(path).model_fields) == sorted(MODEL_FIELDS[path])


def test_finance_refused_keeps_its_code_and_detail():
    from services.finance.case import FinanceRefused

    e = FinanceRefused("cod_missing", "x")
    assert isinstance(e, ValueError) and (e.code, e.detail) == ("cod_missing", "x")
