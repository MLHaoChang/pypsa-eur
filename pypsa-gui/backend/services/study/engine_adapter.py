"""
The guided study's ONE seam onto the Investment Case engine (U2).

Plan: docs/superpowers/plans/2026-10-05-guided-study-u2-engine-rewire.md §2
(C4, C5), §3.2 (`RatingResult` + the export line → `Bill`), WP5.

Every bill the guided face shows is IC's: the tariff engine through the
frozen facade's `billing.bill_site` (the solved PoC meter) or
`billing.rate_meter` (a meter series without a solve: the intake preview on
the unsolved baseline pack, the baseline at a perturbed tariff), plus the
value-flow export line (`cost_rows.commercial_cost_terms`'s
`block["energy_export"]`, cross-checked with `value_flows.export_revenue`).
The adapter only MAPS the engine's figures onto GS's `Bill` view shape; it
prices nothing itself, and a figure the engine cannot give is null with a
flag (ADR-0001).

Stage 1 (WP5): `bill` / `bill_meter`. WP6: `demand_charge_eur`, the demand
charge the solve COMMITTED (IC's `add_demand_terms`), for the run record. WP7:
`option_case` — the option's ONE `FinanceCase` (`build_finance_case` on the
compiled finance inputs, `run_case`) and its guided `InvestmentCase` view —
and `bound_case`, the tornado's CAPEX and RATE bounds derived from it (C5).
WP8: the runner, the findings and the routes bill and value every option
through here (gate C6: `bill` on a fork the engine solved with the same
compiled config; `bill_meter` only where export ≡ 0 — the intake preview and
the case's counterfactual baseline). GS's `BillCalculator` and the pro forma
remain only as the findings' fallback for a network the engine did not solve,
which no stored study reaches (its readers refuse such forks first,
`engine_inputs_changed_since_run`); WP10 deletes them. The workbook (WP9)
follows.
"""
from __future__ import annotations

import calendar
import contextlib
import dataclasses
import math
import re
from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

from models.study import (
    AssumptionsLedger,
    Bill,
    BillComponents,
    CaseKpis,
    CaseProvenance,
    CaseSources,
    CashFlowYear,
    Fidelity,
    InvestmentCase,
    MarketRevenueAtDuals,
    UpfrontGap,
    ValueStream,
)
from services.study import compile as C

__all__ = [
    "BILL_COMPONENTS", "BILL_COMPONENT_KEYS", "BY_CONSTRUCTION", "CAPEX_ROW_PART", "CaseBundle",
    "EPSILON_MW", "EngineRefused", "KIND_COMPONENT", "bill", "bill_meter", "bound_case",
    "component_of", "cycling_flags", "demand_charge_eur", "engine_ready", "irr",
    "is_zero_size", "option_case", "payback",
]

#: The guided bill's seven components and their labels (moved from
#: `proforma.BILL_COMPONENTS`; owner decision 7 adds "Taxes & levies").
BILL_COMPONENTS: tuple[tuple[str, str], ...] = (
    ("energy", "Energy charges"),
    ("demand", "Demand charge on peak import"),
    ("capacity", "Capacity charge"),
    ("fixed", "Fixed charges"),
    ("network", "Network charges"),
    ("export_credit", "Export credit"),
    ("taxes_levies", "Taxes & levies"),
)
BILL_COMPONENT_KEYS: tuple[str, ...] = tuple(k for k, _ in BILL_COMPONENTS)
#: An item the compiler did not name (an Expert's) is mapped by its kind
#: (§3.2 "no clean mapping" 2; decision 7 for `tax_levy` / `certificate`).
KIND_COMPONENT: Mapping[str, str] = {
    "energy": "energy", "demand": "demand", "capacity": "capacity", "fixed": "fixed",
    "tax_levy": "taxes_levies", "certificate": "taxes_levies"}

# The adapter's own guards (§3.2 "Why findings.STREAMS ... stay correct").
if BILL_COMPONENT_KEYS != C.BILL_COMPONENT_KEYS:
    raise ImportError("engine_adapter.BILL_COMPONENTS must be compile's seven keys")
if tuple(BillComponents._figure_fields) != BILL_COMPONENT_KEYS:
    raise ImportError("models.study.BillComponents must carry the seven bill components")
if not set(KIND_COMPONENT.values()) <= set(BILL_COMPONENT_KEYS):
    raise ImportError("KIND_COMPONENT maps outside the seven bill components")

_ONE_YEAR_HOURS = (8760.0, 8784.0)
_SUM_TOL = 1e-9
_CODE_RE = re.compile(r"[a-z]+(_[a-z]+)*")
# IC note → the guided bill's digit-free code (§3.2 `honesty_notes`).
_NOTE_MAP = {"capacity_prorated_by_represented_hours": "capacity_charge_prorated_by_hours"}


def component_of(item, item_component: Mapping[str, str]) -> str:
    """
    The bill component a tariff item is: by the compiled id first, a
    `network:*` id next, an export revenue item to `export_credit`, else by
    kind.
    """
    if item.id in item_component:
        return item_component[item.id]
    if item.id.startswith(C.NETWORK_PREFIX):
        return "network"
    if item.direction == "revenue" or item.measured_on == "export":
        return "export_credit"
    return KIND_COMPONENT[item.kind]


def _needs_import(item) -> bool:
    """
    Whether the item's quantity is the import meter (its component is not
    established without one); a fixed item or a contracted capacity is not.
    """
    if item.kind == "fixed":
        return False
    if item.kind == "capacity":
        return item.measured_on == "peak_import"
    return item.measured_on in ("import", "net", "peak_import")


def _weights(n) -> pd.Series:
    return n.snapshot_weightings.objective.astype(float)


def _partial_months(n) -> list[str]:
    """Calendar months the snapshots cover for fewer hours than they last."""
    w = _weights(n)
    idx = pd.DatetimeIndex(n.snapshots)
    covered = w.groupby(idx.strftime("%Y-%m")).sum()
    out = []
    for label, hours in covered.items():
        y, m = int(label[:4]), int(label[5:])
        if float(hours) < calendar.monthrange(y, m)[1] * 24.0 - 1e-6:
            out.append(str(label))
    return out


def _notes(res, partial: list[str], has_fixed: bool) -> tuple[str, ...]:
    out: list[str] = []
    for codes in (res.notes or {}).values():
        for code in codes:
            if code.startswith("resolution:"):
                code = "bill_resolution_differs_from_settlement"
            code = _NOTE_MAP.get(code, code)
            if not _CODE_RE.fullmatch(code):
                head = code.split(":", 1)[0]
                if not _CODE_RE.fullmatch(head):
                    continue
                code = head
            out.append(code)
    if partial and has_fixed:
        # IC pro-rates a partial month's fixed items by its covered hours.
        out.append("fixed_charge_prorated_on_partial_period")
    return tuple(sorted(dict.fromkeys(out)))


def _itemised(compiled: C.CompiledCommercial, items) -> list[str]:
    """
    The components the compiled tariff carries an item for, plus the export
    credit when the config prices export (the value-flow line).
    """
    found = {component_of(i, compiled.item_component) for i in items}
    if compiled.config.export_price_ref is not None or "export_series_not_minted" in compiled.notes:
        found.add("export_credit")
    return [k for k in BILL_COMPONENT_KEYS if k in found]


def _bill_notes(compiled: C.CompiledCommercial, res, partial: list[str],
                has_fixed: bool) -> tuple[str, ...]:
    """
    The engine's notes as codes, plus the compile notes a bill must carry
    (gate U2-S1 C2: a capacity charge billed on the assumed connection size).
    """
    out = list(_notes(res, partial, has_fixed)) if res is not None else []
    if C.CAPACITY_ASSUMED_CONNECTION in compiled.notes:
        out.append(C.CAPACITY_ASSUMED_CONNECTION)
    return tuple(sorted(dict.fromkeys(out)))


def _flag_codes(flags) -> str:
    """`SiteBill.flags` as one `unavailable["total"]` value (digit-free heads)."""
    heads = []
    for f in flags:
        head = str(f).split(":", 1)[0]
        if head not in heads:
            heads.append(head)
    return "+".join(heads)


def _bill(n, compiled: C.CompiledCommercial, site_bill, *, export_credit: float | None,
          export_flag: str | None, import_missing: bool = False,
          fidelity: Fidelity | str | None = None) -> Bill:
    """`SiteBill` (one flat period) + the export line → GS's `Bill` (§3.2)."""
    cfg = compiled.config
    items = list(cfg.import_tariff.items) if cfg.import_tariff is not None else []
    comp: dict[str, float | None] = {k: 0.0 for k in BILL_COMPONENT_KEYS}
    flags: dict[str, str] = {}
    res = site_bill.per_period.get(None) if site_bill.per_period else None
    for comp_key in set(compiled.item_component.values()):
        if comp_key not in BILL_COMPONENT_KEYS:
            raise ValueError(f"compiled item component {comp_key!r} is not a bill component")

    def null(key: str, code: str) -> None:
        comp[key] = None
        flags.setdefault(key, code)

    for item in items:
        key = component_of(item, compiled.item_component)
        if import_missing and _needs_import(item):
            null(key, "no_import_series")
            continue
        if res is None:
            null(key, "period_not_billed")
            continue
        if item.id in (res.unsupported_items or []):
            null(key, "item_unsupported")
            continue
        v = res.per_item.get(item.id)
        if v is None or not math.isfinite(float(v)):
            null(key, "item_not_rated")
            continue
        if comp[key] is not None:
            comp[key] = float(comp[key]) + float(v)

    # The value-flow export line (not a tariff item): summed with any export
    # revenue item the tariff carries.
    if export_credit is None:
        null("export_credit", export_flag or "export_line_not_established")
    elif comp["export_credit"] is not None:
        comp["export_credit"] = float(comp["export_credit"]) + float(export_credit)
    for k, v in comp.items():
        if v is not None:
            comp[k] = float(v) + 0.0          # no `-0.0` in JSON

    components = BillComponents(**comp, unavailable=flags)
    bill_flags: dict[str, str] = {}
    total: float | None = None
    if flags:
        bill_flags["total"] = "component_unavailable"
    elif site_bill.flags:
        bill_flags["total"] = _flag_codes(site_bill.flags)
    else:
        total = float(sum(comp.values()))
        engine_total = None if res is None or res.total is None else \
            float(res.total) + float(export_credit or 0.0)
        if engine_total is None or abs(total - engine_total) > _SUM_TOL * max(1.0, abs(total)):
            total = None
            bill_flags["total"] = "bill_components_do_not_sum"
    hours = float(_weights(n).sum())
    if total is None:
        annual = None
        bill_flags["annual_bill"] = bill_flags["total"]
    elif any(abs(hours - h) < 1e-6 for h in _ONE_YEAR_HOURS):
        annual = total
    else:
        annual = None
        bill_flags["annual_bill"] = "horizon_not_one_year"

    peaks: dict[str, float] = {}
    if res is not None and not import_missing and not res.demand_lines.empty:
        g = res.demand_lines.groupby("month")["peak_kw"].max()
        peaks = {str(m): float(v) / 1000.0 for m, v in g.items() if math.isfinite(float(v))}
    months = [str(m) for m in (res.monthly.index if res is not None else [])]
    if not months:
        months = sorted(set(pd.DatetimeIndex(n.snapshots).strftime("%Y-%m")))
    meta = compiled.tariff_meta
    periods = (sorted({m[:4] for m in months}) if meta.get("billing_period") == "year"
               else months)
    partial = _partial_months(n)
    has_fixed = any(i.kind == "fixed" for i in items)
    return Bill(
        total=total, annual_bill=annual, by_component=components,
        peak_mw_by_billing_period=peaks, billing_periods=periods, horizon_hours=hours,
        currency=meta.get("currency") or "EUR", currency_year=meta.get("currency_year"),
        engine="tariff_engine", unavailable=bill_flags,
        fidelity=None if fidelity is None else Fidelity(fidelity),
        honesty_notes=_bill_notes(compiled, res, partial, has_fixed),
        partial_billing_periods=partial, itemised_components=_itemised(compiled, items))


def _export_line(n, compiled: C.CompiledCommercial) -> tuple[float | None, str | None]:
    """
    The export credit of a dispatched network (negative: a credit): the
    committed `commercial_cost_terms` block's `energy_export` when the solve
    recorded one, else the value-flow ledger's line (`export_revenue`); the
    two must agree to 1e-9 when both exist.
    """
    cfg = compiled.config
    p0 = getattr(n.links_t, "p0", None)
    link = cfg.export_link
    if not link or cfg.export_price_ref is None:
        flow = 0.0 if p0 is None or link not in getattr(p0, "columns", []) else \
            float(np.nansum(np.abs(p0[link].to_numpy(dtype=float))))
        return (0.0, None) if flow == 0.0 else (None, "no_export_price")
    from services.commercial.cost_rows import commercial_cost_terms
    from services.results.value_flows import export_revenue

    rev = export_revenue(n, cfg)
    line = rev.get("_") if rev else None
    if rev and set(rev) != {"_"}:
        return None, "export_line_not_flat"
    if line is None:
        return None, "export_line_not_established"
    line = -float(line)
    try:
        block = commercial_cost_terms(n, compiled.commercial()).get("block") or {}
    except Exception:  # noqa: BLE001 — a network the solve did not record
        block = {}
    committed = block.get("energy_export")
    if committed is None:
        return line, None
    if abs(float(committed) - line) > _SUM_TOL * max(1.0, abs(line)):
        return None, "export_line_cross_check_failed"
    return float(committed), None


def bill(n, compiled: C.CompiledCommercial, *, fidelity: Fidelity | str | None = None) -> Bill:
    """
    The solved PoC meter's bill (C4): `billing.bill_site` on the network's
    dispatch with the compiled config, plus the export line. The network must
    carry the compiled config bound (`compile.bind_on_network`) for its export
    price. A dispatch the engine did not solve with this config (no solve
    record, a config changed since) is billed component by component, but its
    total is not established (`unavailable["total"]` names the engine's flags).
    """
    from services.commercial.billing import bill_site

    sb = bill_site(n, compiled.commercial())
    credit, flag = _export_line(n, compiled)
    return _bill(n, compiled, sb, export_credit=credit, export_flag=flag, fidelity=fidelity)


def _series(x, n) -> np.ndarray:
    if isinstance(x, pd.Series):
        x = x.reindex(n.snapshots)
    arr = np.asarray(x, dtype=float)
    if arr.shape != (len(n.snapshots),):
        raise ValueError(f"a meter series needs one value per snapshot ({len(n.snapshots)})")
    return arr


@contextlib.contextmanager
def _poc_sized_as_built(n, poc: str):
    """
    WORKAROUND (adapter-side; engine ask): `billing.rate_meter` rates capacity
    items on the PoC's `p_nom_opt` when it is finite, and an UNSOLVED network
    carries PyPSA's default `p_nom_opt = 0` — so a contracted capacity of the
    intake preview (the unsolved baseline pack) would bill 0. For a
    non-extendable PoC Link the built size IS `p_nom`; it is lent to
    `p_nom_opt` for the call and restored after. A solved network
    (`p_nom_opt == p_nom`) is untouched.
    """
    links = n.links
    if poc not in links.index or "p_nom_opt" not in links.columns or \
            bool(links.at[poc, "p_nom_extendable"]) or \
            float(links.at[poc, "p_nom_opt"]) == float(links.at[poc, "p_nom"]):
        yield
        return
    saved = links.at[poc, "p_nom_opt"]
    links.at[poc, "p_nom_opt"] = float(links.at[poc, "p_nom"])
    try:
        yield
    finally:
        links.at[poc, "p_nom_opt"] = saved


def bill_meter(n, compiled: C.CompiledCommercial, import_mw, export_mw, *,
               fidelity: Fidelity | str | None = None) -> Bill:
    """
    A meter series' bill WITHOUT a solve (C4): `billing.rate_meter` on the
    network's axis — the intake preview on the unsolved baseline pack
    (import = load, export = 0) and the baseline at a perturbed tariff. An
    absent series nulls the components it feeds (`no_import_series`,
    `no_export_series`). Export is the value-flow line, which needs the
    dispatch on the network: nothing exported is a real 0.0, a non-zero
    export series is not established here (`export_line_needs_the_dispatch`).
    """
    from services.commercial.billing import rate_meter

    zeros = np.zeros(len(n.snapshots))
    imp = zeros if import_mw is None else _series(import_mw, n)
    exp = zeros if export_mw is None else _series(export_mw, n)
    with _poc_sized_as_built(n, compiled.config.poc_link):
        sb = rate_meter(n, compiled.commercial(), imp, exp)
    if export_mw is None:
        credit, flag = None, "no_export_series"
    elif float(np.nansum(np.abs(exp))) == 0.0:
        credit, flag = 0.0, None
    else:
        credit, flag = None, "export_line_needs_the_dispatch"
    return _bill(n, compiled, sb, export_credit=credit, export_flag=flag,
                 import_missing=import_mw is None, fidelity=fidelity)


def demand_charge_eur(n, compiled: C.CompiledCommercial) -> float | None:
    """
    The demand charge the engine's solve COMMITTED on `n` (plan §2 C3, §5.2;
    replaces the F1-B4 `demand_charge_eur` bridge term): the sum, over the
    compiled items the bill maps to `demand`, of `commercial_cost_terms`'s
    per-item amounts (`block["by_item"]`, GS Q12a) — a monthly peak's
    `demand_charge`, a year-billed peak's `tariff_capacity` (IC's annual
    measured peak). Read from the solve's records (`ic_demand_peaks`,
    `ic_tariff_capacity`), so a config changed after the solve still reads
    what the LP carried (the bill flags the drift). 0.0 when the tariff has
    no demand item; None when the solve did not record one the config names
    (`demand_charge_not_established`, or a not-established capacity term).
    """
    from services.commercial.cost_rows import commercial_cost_terms

    cfg = compiled.config
    items = list(cfg.import_tariff.items) if cfg.import_tariff is not None else []
    ids = [i.id for i in items if component_of(i, compiled.item_component) == "demand"]
    if not ids:
        return 0.0
    terms = commercial_cost_terms(n, compiled.commercial())
    by_item = terms["block"].get("by_item") or {}
    total, found = 0.0, set()
    for label in ("demand_charge", "tariff_capacity"):
        per = by_item.get(label)
        if per is None:
            if label in by_item:
                return None           # a partly unknown term is unknown
            continue
        for item_id in ids:
            amounts = per.get(item_id)
            if amounts is None:
                continue
            found.add(item_id)
            total += float(sum(float(v) for v in amounts.values()))
    if set(ids) - found:
        return None
    return float(total) + 0.0


#: The engine preflight's cycling warnings (IC U1 f, the port of GS's F1-B6
#: check, owner decision 10) → the study's disclosure codes (gate F1
#: BC-F1-1: the report, the findings and the HELP mirror name these).
CYCLING_CODES: Mapping[str, str] = {
    "commercial.arbitrage_loop": "tariff_export_exceeds_import",
    "commercial.arbitrage_loop_via_storage": "tariff_export_exceeds_import_via_storage",
    # GS's own check (`validation_service._check_export_cycling`), read until
    # WP10 removes it: since WP6 the Links carry no price, so it finds none.
    "tariff_export_exceeds_import": "tariff_export_exceeds_import",
    "tariff_export_exceeds_import_via_storage": "tariff_export_exceeds_import_via_storage",
}
_CYCLING_ORDER = ("tariff_export_exceeds_import", "tariff_export_exceeds_import_via_storage")


def cycling_flags(issues) -> list[str]:
    """
    The export-cycling disclosure codes among preflight `issues` (U2 WP6):
    the engine's `commercial.arbitrage_loop*` warnings read as the study's
    codes, once each, in a stable order.
    """
    found = {CYCLING_CODES[i.code] for i in issues if i.code in CYCLING_CODES}
    return [c for c in _CYCLING_ORDER if c in found]


# ── WP7: the investment case (C1, C2, C4, C5) ─────────────────────────────

#: The case's engines, named on its provenance (§5.4: the view's figures keep
#: their `Engine` literal until WP8's rename).
CASE_ENGINES = ("tariff_engine", "finance_engine", "lp", "lp_duals", "ledger")
#: The battery's ledger cost rows → the upfront part each one prices (C1,
#: `packs.battery_parts`): a CAPEX bound moves that part only.
CAPEX_ROW_PART: Mapping[str, tuple[str, str]] = {
    "battery_inverter_eur_per_kw": ("battery", "power"),
    "battery_storage_eur_per_kwh": ("battery", "energy"),
}
RATE_ROW = "discount_rate"
#: Gate §4.2: the codes that hold at the centre only, on the §4.6 identity
#: (`test_u2_wp7_case.py` proves it on the site golden fixture).
BY_CONSTRUCTION = ("npv_nonnegative_at_optimum_by_construction",
                   "irr_and_discounted_payback_bounded_at_optimum_by_construction")
_OPERATING_FLAGS = {"resilience_value": "not_in_mvp1", "tax": "pre_tax_basis",
                    "depreciation": "pre_tax_basis", "debt_service": "no_financing_in_mvp1"}
_RECONCILE_TOL = 1e-6
#: A size at or below this is "no investment" (`proforma.EPSILON_MW`, C7).
EPSILON_MW = 1e-3


class EngineRefused(ValueError):
    """
    The case will not be built. `code` is stable and machine-read: GS's
    pro forma codes where they still apply (`baseline_has_no_case`,
    `currency_year_mixed`, `lifetime_not_whole_years`,
    `discount_rate_differs_from_lp`, ...) and `engine_refused` for a
    `FinanceRefused` of the engine, whose own code is `engine_code`.
    """

    def __init__(self, code: str, message: str, *, engine_code: str | None = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.engine_code = engine_code


@dataclasses.dataclass(frozen=True)
class CaseBundle:
    """
    One option's case (plan §2 C6): the guided view (`InvestmentCase`), the
    engine's `FinanceCase` and its `FinanceResult` (None when the view is not
    established before the engine runs), the bills the view's savings and
    streams read, and what a bound needs to rebuild the view without
    re-reading the network (C5).
    """

    view: InvestmentCase
    case: Any = None                # services.finance.case.FinanceCase
    result: Any = None              # services.finance.engine.FinanceResult
    bills: Mapping[str, Bill | None] = dataclasses.field(default_factory=dict)
    compiled: C.CompiledCommercial | None = None
    finance: C.CompiledFinance | None = None
    context: Mapping[str, Any] = dataclasses.field(default_factory=dict)


def irr(cash_flows) -> tuple[float | None, list[str]]:
    """The engine's IRR (`finance.metrics.irr`, C7): (rate, flags)."""
    from services.finance.metrics import irr as _irr

    return _irr(np.asarray(cash_flows, dtype=float))


def payback(cash_flows) -> float | None:
    """The engine's payback (`finance.engine.payback`, C7) on a cash series."""
    from services.finance.engine import payback as _payback

    return _payback(np.asarray(cash_flows, dtype=float))


def is_zero_size(p_mw: float | None) -> bool:
    """The ONE size rule: at or below `EPSILON_MW` is no investment (C7)."""
    return p_mw is not None and p_mw <= EPSILON_MW


def engine_ready(n) -> bool:
    """
    Whether a solved option network can be valued by the engine (WP7): the
    engine's commercial chain solved it (its committed PoC record,
    `lp_bindings.META_LINKS`) and its battery, if any, carries the two upfront
    parts (C1). A fork written before WP7 (a capital-cost battery) or solved
    outside the engine is not; its callers keep the pro forma until WP8 marks
    such studies stale.
    """
    from services.asset_schema.derive import has_parts
    from services.commercial.lp_bindings import META_LINKS

    if not (getattr(n, "meta", None) or {}).get(META_LINKS):
        return False
    su = n.storage_units
    return all(has_parts("StorageUnit", su.loc[name]) for name in su.index)


_CODE_SEGMENT = re.compile(r"[a-z]+(_[a-z]+)*")


def _code(flag: str) -> str | None:
    """A digit-free code from an engine flag: its leading clean segments."""
    out = []
    for seg in str(flag).split(":"):
        if not _CODE_SEGMENT.fullmatch(seg):
            break
        out.append(seg)
    return ":".join(out) or None


def _notes_codes(notes) -> list[str]:
    """Other engines' notes, reduced to digit-free codes (the pro forma's rule)."""
    out = []
    for note in notes or ():
        code = str(note).split(":", 1)[0]
        if code.startswith("technology_costs_are_"):
            code = "technology_costs_are_projections"
        if code and _CODE_SEGMENT.fullmatch(code):
            out.append(code)
    return out


def _pack_notes(n) -> list[str]:
    from services.study import packs

    meta = (getattr(n, "meta", None) or {}).get(packs.PACK_META_KEY) or {}
    return _notes_codes(meta.get("honesty_notes") or [])


def _as_bill(value) -> Bill | None:
    if value is None or isinstance(value, Bill):
        return value
    return Bill.model_validate(value)


def _served_load(n) -> np.ndarray:
    """The site's served load (MW per snapshot): the counterfactual's import."""
    p_set = n.loads_t.p_set
    cols = [c for c in n.loads.index if c in p_set.columns]
    return p_set[cols].sum(axis=1).to_numpy(dtype=float) if cols else \
        np.zeros(len(n.snapshots))


def _econ_rows(n, cfg, asset_economics) -> tuple[Mapping | None, str | None]:
    if asset_economics is not None:
        return asset_economics, "run_record"
    try:
        from services.adequacy.eh_report import _live_result_df
        from services.results.asset_economics import compute_asset_economics

        return compute_asset_economics(n, cfg, result_df=_live_result_df), "live_frames"
    except Exception:  # noqa: BLE001 — reported as not established, never a zero
        return None, None


def _econ_row(econ: Mapping | None, cls: str, name: str) -> Mapping | None:
    for row in (econ or {}).get(cls) or []:
        if row.get("name") == name:
            return row
    return None


def _market(n, econ) -> MarketRevenueAtDuals:
    """The option's assets' revenue at the LP's bus prices (BC-7), reported."""
    from services.study import packs

    by: dict[str, float] = {}
    missing = False
    if packs.BATTERY_NAME in n.storage_units.index:
        r = _econ_row(econ, "storage_units", packs.BATTERY_NAME)
        if r is None:
            missing = True
        else:
            by[packs.BATTERY_NAME] = (float(r.get("discharge_revenue_eur") or 0.0)
                                      - float(r.get("charge_cost_eur") or 0.0))
    if packs.PV_NAME in n.generators.index:
        r = _econ_row(econ, "generators", packs.PV_NAME)
        if r is None:
            missing = True
        else:
            by[packs.PV_NAME] = float(r.get("revenue_eur") or 0.0)
    if missing:
        return MarketRevenueAtDuals(annual_value=None, by_asset=by,
                                    unavailable={"annual_value": "asset_economics_unavailable"})
    return MarketRevenueAtDuals(annual_value=float(sum(by.values())), by_asset=by)


def _upfront_gaps(n, case) -> list[UpfrontGap]:
    """
    Gate S4: the battery's booked upfront (the engine's, from its parts) and
    the figure `upfront_cost_series` shows elsewhere (the parts' sum since C1).
    """
    from services.solver.periodized_costs import upfront_cost_series
    from services.study import packs

    out = []
    for a in case.assets:
        if a.name != packs.BATTERY_NAME or a.overnight_cost is None:
            continue
        try:
            per = float(upfront_cost_series(n, "StorageUnit")[a.name])
            p = float(n.storage_units.at[a.name, "p_nom_opt"])
            back = per * p if math.isfinite(per) and math.isfinite(p) else None
        except (KeyError, TypeError, ValueError):
            back = None
        out.append(UpfrontGap(asset=a.name, ledger_upfront_eur=float(a.overnight_cost),
                              back_calculated_upfront_eur=back,
                              gap_eur=None if back is None else back - float(a.overnight_cost)))
    return out


def _streams(base: Bill, opt: Bill) -> list[ValueStream]:
    """The bill's seven components as value streams, summing to the savings."""
    savings = float(base.annual_bill) - float(opt.annual_bill)
    out = []
    for key, label in BILL_COMPONENTS:
        delta = (float(getattr(base.by_component, key))
                 - float(getattr(opt.by_component, key)))
        share = delta / savings if savings != 0.0 else None
        out.append(ValueStream(
            key=key, label=label, annual_value=delta, share=share, engine=opt.engine,
            unavailable={} if share is not None else {"share": "zero_savings"}))
    return out


def _line_sum(op, t: int, keep) -> float:
    total = 0.0
    for key, arr in op.lines.items():
        meta = op.line_meta.get(key)
        if arr is None or meta is None or not keep(meta):
            continue
        total += float(arr[t])
    return total


def _view(ctx: Mapping[str, Any], case, result, *, centre: bool,
          extra_notes: tuple[str, ...] = ()) -> InvestmentCase:
    """
    The guided `InvestmentCase` view of the engine's case (§2 C6, `_view`):
    every money figure is the engine's — capex, replacements, the fixed and
    variable O&M lines, the terminal value, the project pre-tax cash and its
    KPIs — beside the bills' savings and streams, which the engine's
    incremental operating cash must reconcile with in every year
    (`case_streams_reconcile_with_engine`).
    """
    from services.study import packs

    fin = case.inputs if case is not None else ctx["finance"].inputs
    rate = float(fin.wacc_nominal)
    horizon = int(fin.analysis_years)
    base, opt = ctx["bills"].get("baseline"), ctx["bills"].get("option")
    notes = list(ctx["notes"]) + list(extra_notes)
    common = dict(case_id=ctx["case_id"], study_id=ctx["study_id"], option_id=ctx["option_id"],
                  currency_year=fin.currency_year, fidelity=ctx["fidelity"],
                  horizon_years=horizon, discount_rate=rate, sources=ctx["sources"],
                  provenance=ctx["provenance"], engine="finance_engine")
    not_established = {"cash_flow": "not_established", "kpis": "not_established",
                       "value_streams": "not_established"}
    missing = [w for w, b in (("baseline", base), ("option", opt))
               if b is None or b.annual_bill is None]
    if missing:
        return InvestmentCase(**common, status="not_established", completeness=not_established,
                              honesty_notes=tuple(dict.fromkeys(
                                  notes + [f"bill_unavailable_{w}" for w in missing])))
    cash = result.cash.get("project_pre_tax")
    if cash is None or result.op.capex is None or result.terminal is None:
        reasons = [r for rs in (list(result.op.reasons.values()) + [
            v for k, v in result.reasons.items() if k != "tax"]) for r in rs]
        codes = [f"engine_reason:{c}" for c in (_code(r) for r in reasons) if c]
        if result.terminal is None:
            codes += ["salvage_not_computed"]
        return InvestmentCase(**common, status="not_established", completeness=not_established,
                              honesty_notes=tuple(dict.fromkeys(
                                  notes + ["case_not_established"] + codes)))

    savings = float(base.annual_bill) - float(opt.annual_bill)
    op = result.op
    inc = result.op_incremental.get("net")
    years, net_flows = [], []
    cum = 0.0
    capex_total = float(np.sum(op.capex))
    for t in range(horizon + 1):
        fom = -_line_sum(op, t, lambda m: m.source == "asset" and m.stream == "fom")
        vom = -_line_sum(op, t, lambda m: m.source == "asset" and m.stream != "fom")
        row = dict(capex=float(op.capex[t]), replacements=float(op.replacement[t]),
                   opex_fixed=fom + 0.0, opex_variable=vom + 0.0,
                   salvage=float(result.terminal[t]) + 0.0)
        if t == 0:
            row.update(bill_baseline=None, bill_option=None, savings=0.0,
                       market_revenue_at_duals=0.0)
            flags = {"bill_baseline": "build_year", "bill_option": "build_year"}
        else:
            row.update(bill_baseline=float(base.annual_bill), bill_option=float(opt.annual_bill),
                       savings=savings,
                       market_revenue_at_duals=float(ctx["market"].annual_value or 0.0))
            flags = {}
            want = savings - fom - vom
            if inc is None or abs(float(inc[t]) - want) > _RECONCILE_TOL * max(1.0, abs(want)):
                return InvestmentCase(
                    **common, status="not_established", completeness=not_established,
                    honesty_notes=tuple(dict.fromkeys(
                        notes + ["case_not_established",
                                 "case_streams_do_not_reconcile_with_engine"])))
        net = float(cash[t])
        disc = net / (1.0 + rate) ** t
        cum += disc
        net_flows.append(net)
        years.append(CashFlowYear(
            year=t, **row, fuel=0.0, co2_cost=0.0, contract_revenue=0.0,
            resilience_value=None, tax=None, depreciation=None, debt_service=None,
            net_cash_flow=net, discounted_cash_flow=disc, cumulative_discounted=cum,
            unavailable={**flags, **_OPERATING_FLAGS}))

    m = result.metrics
    kpi_flags: dict[str, str] = {"lcoe": "not_in_scope_mvp1", "lcoh": "not_applicable",
                                 "dscr_min": "no_financing_in_mvp1"}
    invested = capex_total > 0
    irr_value = m.get("project_pre_tax_irr") if invested else None
    if irr_value is None:
        kpi_flags["irr"] = "irr_undefined"
    if "project_pre_tax:irr_multiple_sign_changes" in result.flags:
        notes.append("irr_cash_changes_sign_more_than_once")
    disc_flows = [y.discounted_cash_flow for y in years]
    pb = payback(net_flows) if invested else None
    pbd = payback(disc_flows) if invested else None
    for key, v in (("payback_simple", pb), ("payback_discounted", pbd)):
        if v is None:
            kpi_flags[key] = "never_pays_back" if invested else "no_investment"
    lcos = None
    if not any(a.component == "StorageUnit" for a in case.assets):
        kpi_flags["lcos"] = "not_applicable"
    else:
        lcos = m.get("lcos_real_per_mwh")
        if lcos is None:
            reasons = [_code(r) for r in (result.lcos.get("reasons") or [])]
            kpi_flags["lcos"] = next((r for r in reasons if r), "lcos_not_established")
        else:
            notes.append("lcos_includes_charging_energy_cost")
    salvage = float(result.terminal[-1])
    kpis = CaseKpis(npv=float(m["project_pre_tax_npv"]), irr=irr_value, payback_simple=pb,
                    payback_discounted=pbd, lcoe=None, lcos=lcos, lcoh=None, dscr_min=None,
                    capex_total=capex_total, salvage_eur=salvage, unavailable=kpi_flags)

    p_sizes = []
    if packs.BATTERY_NAME in ctx["sizes"]:
        p_sizes.append(ctx["sizes"][packs.BATTERY_NAME])
    if packs.PV_NAME in ctx["sizes"]:
        p_sizes.append(ctx["sizes"][packs.PV_NAME])
    if centre:
        notes.append(BY_CONSTRUCTION[0])
    if p_sizes and all(is_zero_size(q) for q in p_sizes):
        notes.append("size_zero_no_investment")
    elif invested and centre:
        notes.append(BY_CONSTRUCTION[1])
    notes += ["market_revenue_at_duals_excluded_from_cash_flow",
              "salvage_annuity_pv_remaining_life"]
    notes += ctx["tail_notes"]
    return InvestmentCase(
        **common, status="ok", salvage_basis="annuity_pv", years=years, kpis=kpis,
        value_streams=_streams(base, opt), market_revenue_at_duals=ctx["market"],
        upfront_gaps=ctx["upfront_gaps"],
        completeness={"cash_flow": "ok", "kpis": "ok", "value_streams": "ok",
                      "resilience": "skipped", "tax": "skipped"},
        honesty_notes=tuple(dict.fromkeys(notes)))


def option_case(n, cfg, ledger: AssumptionsLedger, *, compiled: C.CompiledCommercial,
                option_id: str, study_id: str, fidelity: Fidelity | str | None = None,
                asset_economics: Mapping | None = None, question=None,
                project_ref: str | None = None, model_hash: str | None = None,
                bills: Mapping[str, Any] | None = None,
                study_currency_year: int | None = None) -> CaseBundle:
    """
    One solved option's investment case on the Investment Case engine (plan
    §2 C6, WP7): `compile.finance_from_ledger` (C1 parts read by the engine,
    C2 the remaining-life terminal value, C4 the real pre-tax basis) →
    `build_finance_case(n, cfg, fin)` on the option's `single_owner` value
    flows → `run_case(case, pack=None)` → the guided view. ONE `FinanceCase`
    per option: the CAPEX and RATE bounds derive from it (`bound_case`).

    The baseline is the engine's counterfactual (the served load rated at
    the case's tariff, export 0): its bill is `bill_meter` on that meter, the
    option's `bill` on the solved dispatch, unless `bills` gives either
    (`{"baseline": Bill | None, "option": Bill | None}`; a None bill makes the
    view `not_established`, never a zero saving).

    Refused (`EngineRefused`): the baseline option, an unknown option, a
    multi-period network, a discount rate other than the LP's (at the centre;
    a rate bound is `bound_case`'s), a second currency year, a lifetime that
    is not whole years, and a `FinanceRefused` of the engine (`engine_refused`
    with its `engine_code`).
    """
    import dataclasses as dc

    from services.adequacy.eh_report import _live_result_df
    from services.finance.case import FinanceRefused
    from services.finance.engine import run_case
    from services.results.finance_case import build_finance_case
    from services.study import packs
    from services.study import questions as Q

    question = question or Q.BESS_AT_SITE
    try:
        spec = Q.option(question, option_id)
    except KeyError as exc:
        raise EngineRefused("option_unknown", str(exc)) from None
    if not spec.free_assets:
        raise EngineRefused("baseline_has_no_case",
                            f"{option_id!r} is the baseline every case is measured against")
    if not isinstance(n.snapshots, pd.DatetimeIndex):
        raise EngineRefused("network_not_one_flat_year",
                            "the case values ONE flat year; this network is multi-period")
    fidelity = Fidelity(fidelity) if fidelity is not None else None
    if compiled.config.value_flows is None:
        compiled = C.with_value_flows(compiled, n)
    owned = [o["asset_id"] for o in compiled.commercial()["value_flows"]["asset_owners"]]
    try:
        finance = C.finance_from_ledger(ledger, model_year=int(n.snapshots[0].year),
                                        owned_assets=owned, tariff_meta=compiled.tariff_meta,
                                        study_currency_year=study_currency_year)
    except C.CompileError as exc:
        raise EngineRefused(exc.code, exc.message) from None
    lp_rate = getattr(cfg, "discount_rate", None)
    if lp_rate is not None and abs(float(lp_rate) - float(finance.inputs.wacc_nominal)) > 1e-12:
        raise EngineRefused(
            "discount_rate_differs_from_lp",
            f"the ledger's rate {finance.inputs.wacc_nominal!r} is not the rate the LP used "
            f"({lp_rate!r}); the case and the LP must share one basis — re-run the study")
    cfg_case = dc.replace(cfg, commercial=compiled.commercial())
    try:
        case = build_finance_case(n, cfg_case, finance.inputs, result_df=_live_result_df)
        result = run_case(case, pack=None)
    except FinanceRefused as exc:
        raise EngineRefused("engine_refused", f"the finance engine refused the case: {exc}",
                            engine_code=exc.code) from None

    given = dict(bills or {})
    got: dict[str, Bill | None] = {}
    got["option"] = _as_bill(given["option"]) if "option" in given else \
        bill(n, compiled, fidelity=fidelity)
    got["baseline"] = _as_bill(given["baseline"]) if "baseline" in given else \
        bill_meter(n, compiled, _served_load(n), np.zeros(len(n.snapshots)), fidelity=fidelity)

    econ, econ_ref = _econ_rows(n, cfg, asset_economics)
    sizes = {}
    for comp, name in (("storage_units", packs.BATTERY_NAME), ("generators", packs.PV_NAME)):
        df = getattr(n, comp)
        if name in df.index:
            try:
                sizes[name] = max(float(df.at[name, "p_nom_opt"]), 0.0)
            except (TypeError, ValueError):
                sizes[name] = None
    meta = compiled.tariff_meta
    gaps = _upfront_gaps(n, case)
    has_demand = any(v == "demand" for v in compiled.item_component.values())
    notes = ["basis_real_pre_tax_no_subsidy", "currency_year_stated",
             "single_year_extrapolated", "perfect_foresight_dispatch"]
    if has_demand:
        notes += ["demand_charge_perfect_foresight", "duals_include_demand_charge",
                  "demand_peak_hourly_resolution"]
    notes += ["no_degradation", "wacc_field_holds_the_real_rate"]
    if packs.BATTERY_NAME in sizes:
        notes.append("inverter_replaced_at_its_lifetime")
        same = bool(gaps) and all(g.gap_eur is not None and abs(g.gap_eur) <= 1e-9 * max(
            1.0, g.ledger_upfront_eur) for g in gaps)
        notes.append("battery_upfront_from_two_parts" if same
                     else "battery_upfront_from_ledger_not_back_calculated")
    if any(f.startswith("meter_link_not_investment:") for f in case.flags):
        notes.append("meter_links_are_not_investments")
    notes += list(finance.notes)
    tail = list(meta.get("honesty_notes") or [])
    tail += _pack_notes(n)
    for b in got.values():
        if b is not None:
            tail += _notes_codes(b.honesty_notes)
    tail += _notes_codes(ledger.honesty_notes)
    sources = CaseSources(bill_refs=["tariff_engine:counterfactual", f"tariff_engine:{option_id}"],
                          asset_economics_ref=econ_ref)
    provenance = CaseProvenance(
        ledger_hash=packs.ledger_hash(ledger), library_version=ledger.ledger_version,
        tariff_id=meta.get("tariff_id"), project_ref=project_ref, model_hash=model_hash,
        engines=list(CASE_ENGINES))
    ctx = {"case_id": f"{study_id}.{option_id}", "study_id": study_id, "option_id": option_id,
           "fidelity": fidelity, "sources": sources, "provenance": provenance, "bills": got,
           "finance": finance, "notes": tuple(notes), "tail_notes": tuple(tail),
           "market": _market(n, econ), "upfront_gaps": gaps, "sizes": sizes,
           "centre_values": {k: float(v) for k, v in packs.ledger_values(ledger).items()
                             if v is not None}}
    view = _view(ctx, case, result, centre=True)
    return CaseBundle(view=view, case=case, result=result, bills=got, compiled=compiled,
                      finance=finance, context=ctx)


def _variant_values(variant) -> dict[str, float]:
    if isinstance(variant, AssumptionsLedger):
        from services.study import packs

        return {k: float(v) for k, v in packs.ledger_values(variant).items() if v is not None}
    return {k: float(v) for k, v in dict(variant).items()}


def _capex_case(case, centre_values: Mapping[str, float], variant: Mapping[str, float]):
    """
    The case at a battery cost bound: each changed ledger cost row moves its
    part's cost (× new / old), the asset's `overnight_cost` with it, and the
    asset's own FOM line by the change of Σ part cost × FOM share (the pack
    ties FOM to the inverter investment). Replacements (`part_lifetimes`)
    and the remaining-life terminal value follow the parts in the engine.

    WORKAROUND (engine ask: `finance.case.scale_capex(case, f, *, asset=,
    part=)`): `scale_capex` scales EVERY asset's every part by one factor, but
    a guided CAPEX row prices ONE part of ONE asset (a battery bound must not
    move the inverter with the storage block, nor PV). The part and the
    asset's sum are replaced together, so `AssetFinance`'s parts check holds.
    """
    import dataclasses as dc

    factors: dict[tuple[str, str], float] = {}
    for key, value in variant.items():
        if key not in CAPEX_ROW_PART:
            raise EngineRefused("bound_row_not_supported",
                                f"{key!r} is not a CAPEX row of the case")
        old = centre_values.get(key)
        if old is None or old == 0.0:
            raise EngineRefused("bound_row_not_supported", f"{key!r} has no centre value")
        factors[CAPEX_ROW_PART[key]] = float(value) / float(old)
    assets, fom_factor = [], {}
    for a in case.assets:
        mine = {part: f for (asset, part), f in factors.items() if asset == a.name}
        if not mine:
            assets.append(a)
            continue
        names = {p.name for p in a.parts}
        if not set(mine) <= names:
            raise EngineRefused("bound_row_not_supported",
                                f"{a.name} has no part {sorted(set(mine) - names)}")
        parts = tuple(dc.replace(p, overnight_cost=None if p.overnight_cost is None
                                 else p.overnight_cost * mine.get(p.name, 1.0)) for p in a.parts)
        costs = [p.overnight_cost for p in parts]
        total = None if any(c is None for c in costs) else float(sum(costs))
        fom_old = sum((p.overnight_cost or 0.0) * (p.fom_share or 0.0) for p in a.parts)
        fom_new = sum((p.overnight_cost or 0.0) * (p.fom_share or 0.0) for p in parts)
        fom_factor[a.name] = fom_new / fom_old if fom_old else 1.0
        assets.append(dc.replace(a, overnight_cost=total, parts=parts))

    def line(ln):
        f = next((fom_factor[a] for a in fom_factor
                  if ln.source == "asset" and ln.stream == "fom" and ln.key.endswith(f":{a}")),
                 None)
        if f is None or ln.amount is None:
            return ln
        return dc.replace(ln, amount=ln.amount * f)

    templates = tuple(dc.replace(t, lines=tuple(line(ln) for ln in t.lines))
                      for t in case.templates)
    return dc.replace(case, assets=tuple(assets), templates=templates)


def _rate_case(case, rate: float):
    """
    The case at a discount-rate bound (C5 as amended at the IC S0b gate):
    `inputs.wacc_nominal` and `cost_of_equity` (all equity, row 26) AND the
    case's valuation basis `lp_basis` (`discount_rate` and every asset's own
    rate that was set) at the bound, so the remaining-life terminal value
    annuitises and discounts at it — as GS's rate bound moved its salvage.
    """
    import dataclasses as dc

    inputs = case.inputs.model_copy(update={"wacc_nominal": float(rate),
                                            "cost_of_equity": float(rate)})
    lp = case.lp_basis
    lp = dc.replace(lp, discount_rate=float(rate),
                    asset_discount_rates={a: (None if r is None else float(rate))
                                          for a, r in lp.asset_discount_rates.items()})
    return dc.replace(case, inputs=inputs, lp_basis=lp)


def bound_case(bundle: CaseBundle, variant, *, kind: str) -> CaseBundle:
    """
    A tornado bound derived from the option's ONE `FinanceCase` (C5): no
    value-flow ledger is rebuilt, no network is read, the dispatch and the
    bills are the centre's (sizes fixed). `variant` is the bound's ledger or
    `{row key: value}`.

    * `kind="capex"` — a battery cost row moves its upfront part
      (`_capex_case`; replacements, terminal value and FOM follow).
    * `kind="rate"` — `discount_rate` moves the WACC, the cost of equity and
      the valuation basis (`_rate_case`). The LP solved at the centre rate,
      so the result's WACC gate is the engine's `wacc_gate` read against the
      basis the LP used, and reads `differs`: accepted for rate rows only and
      disclosed `wacc_gate_differs_on_rate_bound` (GS refused
      `discount_rate_differs_from_lp`).

    A price bound is a re-dispatch: `option_case` on the variant network.
    """
    import dataclasses as dc

    from services.finance.engine import run_case, wacc_gate

    if bundle.case is None:
        raise EngineRefused("case_not_established", "the centre case was not established")
    values = _variant_values(variant)
    extra: tuple[str, ...] = ()
    if kind == "capex":
        changed = {k: v for k, v in values.items() if k in CAPEX_ROW_PART}
        case = _capex_case(bundle.case, bundle.context["centre_values"], changed)
        result = run_case(case, pack=None)
    elif kind == "rate":
        if RATE_ROW not in values:
            raise EngineRefused("bound_row_not_supported", "a rate bound moves discount_rate")
        case = _rate_case(bundle.case, values[RATE_ROW])
        result = run_case(case, pack=None)
        result = dc.replace(result, gate=wacc_gate(dc.replace(case, lp_basis=bundle.case.lp_basis)))
        extra = ("wacc_gate_differs_on_rate_bound",)
    else:
        raise EngineRefused("bound_kind_unknown", f"{kind!r} is capex or rate (a price bound "
                            "re-dispatches: option_case on the variant network)")
    view = _view(bundle.context, case, result, centre=False, extra_notes=extra)
    return dc.replace(bundle, view=view, case=case, result=result)
