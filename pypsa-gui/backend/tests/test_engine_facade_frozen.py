"""
The engine facade frozen for the guided study (IC plan
`2026-10-05-one-investment-engine-two-faces.md` §6, the U1 landing plan
`2026-10-05-ic-u1-engine-landing.md` §3; GS Q4, IC U1 follow-up; the S0b
additions of `2026-10-06-ic-s0b-replacements-terminal.md` S7: the asset parts,
`effective_parts`, `scale_capex`, the replacement schedule,
`FinanceInputs.replacement_rule` and the `remaining_life_annuity` method; and
the G2 additions of `2026-10-07-ic-g1-g2-campus-equipment.md` G-11:
`ExtraOwnerAsset`, `FinanceCase.extra_assets` and the `extra_assets` keyword of
`build_finance_case`; and the 2026-10-13 engine ask: `series_store.delete_series`,
`pinned_by`, `list_series` and their result types; and P5b's staged builds of
`2026-10-14-e2e-guided-demo.md` §7.1: `FinanceCase.asset_cod` and
`FinanceInputs.stage_capex_lead_years`).

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
    # IC engine ask 5 (2026-10-13): a written dispatch recorded as an engine solve.
    "services.commercial.lp_bindings.record_dispatch_as_solve":
        "(n, commercial: 'dict | CommercialConfig') -> 'pd.DataFrame'",
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
    # IC engine ask 2 (2026-10-13): one asset, or one part of one asset.
    "services.finance.case.scale_capex":
        "(case: 'FinanceCase', f: 'float', *, asset: 'str | None' = None, "
        "part: 'str | None' = None) -> 'FinanceCase'",
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
    # The engine ask "delete_series / pinned_by" (owner 2026-10-13): the study
    # deletes the export series it minted, never while a project pins it, and
    # its orphan sweep lists the org's series.
    "services.library.series_store.list_series":
        "(db: 'DBSession', org_id: 'UUID') -> 'list[PriceSeriesRef]'",
    "services.library.series_store.pinned_by":
        "(name: 'str', *, project_dirs: 'Iterable[Path]') -> 'list[SeriesPin]'",
    "services.library.series_store.delete_series":
        "(db: 'DBSession', org_id: 'UUID', name: 'str', *, project_dirs: 'Iterable[Path]', "
        "root: 'Path | None' = None) -> 'SeriesDeleted'",
    # IC engine ask 3 (2026-10-13): the names the study used outside the facade
    # (its WP7 freeze, N15 and Q15 asks), frozen.
    "services.finance.engine.wacc_gate": "(case: 'FinanceCase') -> 'dict'",
    "services.commercial.binding.bind_commercial":
        "(n, commercial, *, project_dir: 'pathlib.Path | None', "
        "resolve_ref: 'Callable[[object], pd.Series]', lock=None, "
        "resolve_item: 'Callable[[object], dict] | None' = None, solver_cfg=None)"
        " -> 'dict | None'",
    "services.library.series_store.resolve":
        "(db: 'DBSession', org_id: 'UUID', ref: 'PriceSeriesRef', *, root: 'Path | None' = None)"
        " -> 'pd.Series'",
    "services.asset_schema.derive.apply_parts":
        "(n, component_class: 'str', name: 'str', parts: 'Mapping[str, Any]', *, "
        "discount_rate: 'float') -> 'None'",
    "services.asset_schema.derive.has_parts":
        "(component_class: 'str', values: 'Mapping[str, Any]') -> 'bool'",
    "services.commercial.cost_rows.commercial_cost_terms":
        "(n, commercial: 'dict | None', *, years=None) -> 'dict'",
    "services.results.value_flows.export_revenue":
        "(n, commercial) -> 'dict[str, float | None]'",
    # The context-level commercial binding (C, U1 follow-up b; GS Q15).
    "services.commercial.binding.bind_commercial_on_context":
        "(ctx, commercial, *, user=None, in_flight: 'Callable[[object], bool] | None' = None, "
        "solver_cfg=None) -> 'dict | None'",
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

# IC engine ask 3 (2026-10-13): every other attribute the study reads off an
# engine type (a dataclass or a pydantic model), as a SUBSET: these must
# stay, others may be added. `FinanceResult` must stay a dataclass (the study
# replaces its `gate`).
FIELDS_READ: dict[str, tuple[str, ...]] = {
    "services.finance.engine.FinanceResult": (
        "cash", "metrics", "lcos", "op", "op_incremental", "terminal", "reasons", "flags",
        "incentives", "gate"),
    "services.finance.cashflow.Operating": ("lines", "line_meta", "capex", "replacement",
                                            "reasons"),
    "services.finance.incentives.Incentives": ("grant",),
    "services.finance.case.Template": ("lines",),
    "services.finance.case.TemplateLine": ("key", "stream", "amount", "source"),
    "services.finance.case.LpBasis": ("discount_rate", "asset_discount_rates"),
    "services.commercial.billing.SiteBill": ("per_period", "flags"),
    "services.commercial.value_flow_templates.TemplateResult": ("config",),
    "models.commercial.ConnectionAgreement": ("kind", "import_cap_mw", "export_cap_mw",
                                              "available_from"),
    "models.commercial.PriceSeriesRef": ("id", "version", "hash", "source"),
    "models.commercial.Tariff": ("id", "name", "jurisdiction", "valid_from", "valid_to", "items"),
    "models.commercial.TariffItem": ("id", "kind", "unit", "periods", "settlement", "measured_on",
                                     "direction"),
    "models.commercial.TariffPeriod": ("name", "rate", "months", "weekdays", "start_hour",
                                       "end_hour"),
    "models.commercial.ValueFlowConfig": ("asset_owners",),
    "models.commercial.AssetOwnership": ("asset_id",),
    "models.finance.TerminalValueRule": ("method",),
    # The generic defaults pack the study seeds its ledger from (`study/library.py`).
    "services.library.defaults_pack.loader.DefaultsPack": (
        "version", "hash", "cost_values", "finance", "tariffs", "stamp", "default_tariff_id"),
    "services.library.defaults_pack.loader.FinanceDefaults": (
        "seed_library", "currency", "currency_year", "currency_year_source", "basis",
        "basis_source", "perspective", "perspective_source", "discount_rate", "horizon_rule",
        "replacement_rules", "degradation", "sizing_limit"),
    "services.library.defaults_pack.loader.PackValue": (
        "key", "value", "unit", "original_value", "original_unit", "conversion_factor", "range",
        "currency_year", "price_basis", "source", "source_year", "source_url", "projection_year",
        "domain", "illustrative", "derived", "note"),
    "services.library.defaults_pack.loader.PackRange": ("low", "high", "source"),
    "services.library.defaults_pack.loader.HorizonRule": ("horizon_years", "source"),
    "services.library.defaults_pack.loader.ReplacementRule": ("key", "every_years", "within",
                                                              "cost", "source"),
    "services.library.defaults_pack.loader.PackTariff": ("tariff", "meta"),
    "services.library.defaults_pack.loader.PackTariffMeta": (
        "currency", "currency_year", "source", "source_year", "illustrative", "billing_period",
        "export", "honesty", "items"),
    "services.library.defaults_pack.loader.TariffExport": ("price_per_mwh", "series", "cap_mw"),
    "services.library.defaults_pack.loader.HonestyNote": ("code", "help"),
    "services.library.defaults_pack.loader.TariffSourceRow": ("component", "label",
                                                              "original_price", "basis",
                                                              "periods"),
}

# The plain-dict keys the study reads off `run_case`'s result (probed below).
RESULT_KEYS_READ: dict[str, tuple[str, ...]] = {
    "cash": ("project_pre_tax",),
    "metrics": ("project_pre_tax_irr", "project_pre_tax_npv", "lcos_real_per_mwh"),
    "op_incremental": ("net",),
    "lcos": ("reasons",),
}

# Constants and exceptions the study reads.
CONSTANTS: dict[str, object] = {
    "services.commercial.lp_bindings.META_LINKS": "ic_poc_links",
    "models.finance.ESCALATION_CLASSES": ("opex", "fuel", "tariff", "ppa", "export", "capex"),
}
EXCEPTIONS: dict[str, tuple[type, str]] = {
    # path: (base class, constructor signature); the study reads `.code` where it has one.
    "services.commercial.binding.BindingRefusal":
        (Exception, "(status: 'int', code: 'str', message: 'str')"),
    "services.library.series_store.LibraryRefNotFound": (LookupError, ""),
    "services.library.series_store.LibraryRefStale": (ValueError, ""),
}

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
    "services.library.series_store.SeriesPin": ("project_dir", "via"),
    "services.library.series_store.SeriesDeleted": ("deleted_versions", "kept", "pinned_by"),
    "services.finance.case.FinanceCase": (
        "inputs", "owner", "base_year", "cod", "templates", "assets", "flags", "counterfactual",
        "lp_basis", "counterfactual_hash", "conservation_ok", "extra_assets", "asset_cod"),
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
        "site_party", "timezone", "value_flows",
        # H2e (2026-10-15): the counterfactual's reference purchases and CO2 price.
        "co2_price", "reference_purchases"),
    "models.finance.FinanceInputs": (
        "acquisition_date", "analysis_years", "annualise", "capex_phasing", "cod_by_asset",
        "construction_months_by_asset", "construction_start", "contingency_share",
        "cost_of_equity", "currency", "currency_year", "debt", "degradation_by_asset",
        "depreciation_class_by_asset", "escalation", "financial_close", "financing_fee_tax",
        "hebesatz_pct", "incentives", "inflation", "participants", "price_basis", "pwa_met",
        "replacement_capex", "replacement_rule", "reserves_rate", "small_business_163j",
        "solve_ppa", "stage_capex_lead_years", "state_rate",
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


# ── IC engine ask 3 (2026-10-13): every field the study reads ───────────────


@pytest.mark.parametrize("path", sorted(FIELDS_READ))
def test_a_type_keeps_the_fields_the_guided_study_reads(path):
    obj = _get(path)
    if dataclasses.is_dataclass(obj):
        names = {f.name for f in dataclasses.fields(obj)}
    else:
        names = set(obj.model_fields)
    names |= {k for k, v in vars(obj).items() if isinstance(v, property)}
    missing = sorted(set(FIELDS_READ[path]) - names)
    assert missing == [], f"{path} lost {missing}: the guided study reads them"


def test_the_finance_result_stays_a_replaceable_dataclass():
    from services.finance.engine import FinanceResult

    assert dataclasses.is_dataclass(FinanceResult)          # `dataclasses.replace(r, gate=...)`
    assert "gate" in {f.name for f in dataclasses.fields(FinanceResult)}


def test_run_case_keeps_the_result_keys_the_guided_study_reads():
    from services.finance.engine import run_case
    from tests.test_finance_engine import _case

    r = run_case(_case())
    for attr, keys in RESULT_KEYS_READ.items():
        got = getattr(r, attr)
        assert isinstance(got, dict) and set(keys) <= set(got), (attr, sorted(got))
    assert isinstance(r.reasons, dict) and isinstance(r.gate, dict)
    assert all(hasattr(r.op.line_meta[k], "stream") for k in r.op.lines)


@pytest.mark.parametrize("path", sorted(CONSTANTS))
def test_a_constant_the_guided_study_reads_is_frozen(path):
    assert _get(path) == CONSTANTS[path]


@pytest.mark.parametrize("path", sorted(EXCEPTIONS))
def test_an_exception_the_guided_study_catches_is_frozen(path):
    base, sig = EXCEPTIONS[path]
    exc = _get(path)
    assert issubclass(exc, base)
    if sig:
        assert str(inspect.signature(exc)) == sig


@pytest.mark.live_solve
def test_the_commercial_terms_keep_the_keys_the_guided_study_reads():
    """`engine_adapter` reads `commercial_cost_terms(...)["block"]` (`energy_export`,
    `by_item[label][item_id]`) and `export_revenue(...)` on the flat axis `"_"`."""
    from services.commercial.cost_rows import commercial_cost_terms
    from services.results.value_flows import export_revenue
    from tests.golden import site_fixture as sf

    n, _cfg, compiled, _ledger = sf.site_case_option("bess_2h")
    block = commercial_cost_terms(n, compiled.commercial())["block"]
    assert {"energy_export", "by_item"} <= set(block)
    per_item = block["by_item"]["demand_charge"]                # {item id: {period: amount}}
    assert per_item and all(isinstance(v, dict) and v for v in per_item.values())
    assert set(export_revenue(n, compiled.config)) == {"_"}
