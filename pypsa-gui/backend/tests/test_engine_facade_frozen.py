"""
The engine facade frozen for the guided study (IC plan
`2026-10-05-one-investment-engine-two-faces.md` §6, the U1 landing plan
`2026-10-05-ic-u1-engine-landing.md` §3; GS Q4, IC U1 follow-up; the S0b
additions of `2026-10-06-ic-s0b-replacements-terminal.md` S7: the asset parts,
`effective_parts`, `scale_capex`, the replacement schedule,
`FinanceInputs.replacement_rule` and the `remaining_life_annuity` method; and
the G2 additions of `2026-10-07-ic-g1-g2-campus-equipment.md` G-11:
`ExtraOwnerAsset`, `FinanceCase.extra_assets` and the `extra_assets` keyword of
`build_finance_case`).

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
        "(n, cfg, fin, *, result_df, lost_load=None, owner: 'str | None' = None, "
        "extra_assets: 'Sequence[ExtraOwnerAsset]' = ()) -> 'FinanceCase'",
    # IC G2 (plan G-11): campus equipment as owner capex.
    "services.finance.case.ExtraOwnerAsset":
        "(name: 'str', kind: 'str', basis: 'str', quantity: 'float', "
        "parts: 'tuple[ExtraAssetPart, ...]', build_year: 'int', source: 'str', "
        "source_hash: 'str') -> None",
    # Gate r1 note 2: the finance-side part the extra asset normalises its parts to.
    "services.finance.case.ExtraAssetPart":
        "(name: 'str', upfront_per_unit: 'float', lifetime: 'float', fom_share: 'float', "
        "derived_from_capital_cost: 'bool' = False) -> None",
    "services.finance.case.ExtraOwnerAsset.asset_finance": "(self) -> 'AssetFinance'",
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
    # IC S0b (plan S7): one source of capex truth and the one replacement schedule.
    "services.finance.case.effective_parts": "(a: 'AssetFinance') -> 'tuple[AssetPart, ...]'",
    "services.finance.case.scale_capex": "(case: 'FinanceCase', f: 'float') -> 'FinanceCase'",
    "services.finance.case.AssetPart":
        "(name: 'str', overnight_cost: 'float | None', lifetime_years: 'float | None', "
        "fom_share: 'float | None' = None) -> None",
    "services.finance.case.AssetFinance":
        "(name: 'str', component: 'str', overnight_cost: 'float | None', "
        "lifetime_years: 'float | None' = None, carrier: 'str | None' = None, "
        "parts: 'tuple[AssetPart, ...]' = ()) -> None",
    "services.finance.replacements.schedule":
        "(case: 'FinanceCase', tl: 'Timeline') -> 'tuple[Replacement, ...]'",
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
    # The context-level commercial binding (C, U1 follow-up b; GS Q15).
    "services.commercial.binding.bind_commercial_on_context":
        "(ctx, commercial, *, user=None, in_flight: 'Callable[[object], bool] | None' = None)"
        " -> 'dict | None'",
    # The generic defaults pack (A, U1 follow-up a) and the flat export series (e).
    "services.library.defaults_pack.loader.load_defaults_pack":
        "(version: 'str | None' = None) -> 'DefaultsPack'",
    "services.library.defaults_pack.loader.DefaultsPack.load_profile_series":
        "(self, profile_id: 'str', index, *, annual_mwh: 'float | None' = None, weights=None)"
        " -> 'pd.Series'",
    "services.library.defaults_pack.loader.DefaultsPack.pack_tariff":
        "(self, tariff_id: 'str') -> 'Tariff'",
    "services.library.defaults_pack.loader.DefaultsPack.tariff_is_unchanged":
        "(self, tariff: 'Tariff | Mapping[str, Any]') -> 'bool'",
    "services.library.export_series.put_flat_export_series":
        "(db: 'DBSession', org_id: 'UUID', name: 'str', eur_per_mwh: 'float', *, snapshots, "
        "source: 'str' = 'flat export price', vintage_year: 'int | None' = None, "
        "description: 'str | None' = None, created_by: 'UUID | None' = None, "
        "root: 'Path | None' = None) -> 'PriceSeriesRef'",
}

# Result types the guided study reads by attribute.
# The `FinanceResult` fields the guided study reads (`cash`, `metrics`, `lcos`)
# must stay; other fields may be added.
RESULT_FIELDS_READ = ("cash", "metrics", "lcos")

DATACLASS_FIELDS: dict[str, tuple[str, ...]] = {
    "services.commercial.tariff_engine.RatingResult": (
        "lines", "fixed_lines", "monthly", "annual", "per_item", "total", "total_supported",
        "flags", "notes", "unsupported_items", "per_item_sampled", "demand_lines"),
    # IC S0b (plan S2, S4): the parts on the case and a scheduled replacement.
    "services.finance.case.AssetFinance": (
        "name", "component", "overnight_cost", "lifetime_years", "carrier", "parts"),
    "services.finance.case.AssetPart": ("name", "overnight_cost", "lifetime_years", "fom_share"),
    "services.finance.replacements.Replacement": ("year", "asset", "part", "amount", "source"),
    # IC G2 (plan G-4, G-5): the extra asset and the case field that carries it.
    "services.finance.case.ExtraOwnerAsset": (
        "name", "kind", "basis", "quantity", "parts", "build_year", "source", "source_hash"),
    "services.finance.case.ExtraAssetPart": (
        "name", "upfront_per_unit", "lifetime", "fom_share", "derived_from_capital_cost"),
    "services.finance.case.FinanceCase": (
        "inputs", "owner", "base_year", "cod", "templates", "assets", "flags", "counterfactual",
        "lp_basis", "counterfactual_hash", "conservation_ok", "extra_assets"),
}

# The literal choices the guided study compiles to (IC S0b plan S7: C2 compiles
# to `remaining_life_annuity`, D9 to `part_lifetimes`).
LITERALS: dict[tuple[str, str], tuple[str, ...]] = {
    ("models.finance.TerminalValueRule", "method"): (
        "none", "book_value", "multiple_of_ebitda", "fixed", "remaining_life_annuity"),
    ("models.finance.FinanceInputs", "replacement_rule"): ("fixed", "part_lifetimes"),
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
        "replacement_capex", "replacement_rule", "reserves_rate", "small_business_163j",
        "solve_ppa", "state_rate",
        "tax_equity", "tax_losses", "tax_pack_id", "terminal_value", "wacc_nominal"),
}


def _get(path: str):
    """A module attribute, or a class attribute one level down
    (`pkg.module.Class.method`)."""
    module, _, name = path.rpartition(".")
    try:
        return getattr(importlib.import_module(module), name)
    except ModuleNotFoundError:
        module, _, cls = module.rpartition(".")
        return getattr(getattr(importlib.import_module(module), cls), name)


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


@pytest.mark.parametrize("path,field", sorted(LITERALS))
def test_the_compiled_literal_choices_are_frozen(path, field):
    import typing

    ann = _get(path).model_fields[field].annotation
    assert typing.get_args(ann) == LITERALS[(path, field)]


def test_the_finance_result_keeps_the_fields_the_guided_study_reads():
    from services.finance.engine import FinanceResult

    names = {f.name for f in dataclasses.fields(FinanceResult)}
    assert set(RESULT_FIELDS_READ) <= names, sorted(set(RESULT_FIELDS_READ) - names)


def test_finance_refused_keeps_its_code_and_detail():
    from services.finance.case import FinanceRefused

    e = FinanceRefused("cod_missing", "x")
    assert isinstance(e, ValueError) and (e.code, e.detail) == ("cod_missing", "x")
