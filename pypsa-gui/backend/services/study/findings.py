"""
Findings: verdict, value streams, fixed-size tornado (guided investment
study MVP-1, phase S6).

Plan: docs/superpowers/plans/2026-09-28-guided-investment-study-mvp1-v2.md (S6,
"Amended at S6 start"; review v1 B5; review v2 BC-1, BC-7; the S1-S5 gate
carries).

The question is "Do I need a BESS at my site?", so every judgement here is
on the BATTERY's value, not an option's:

* **Zero size** (gate S5 carry). A battery with ``p_nom_opt <= EPSILON_MW``
  is "no investment": it is never re-dispatched, its NPV (0 +- solver noise)
  is not read for a sign, and its attribution is ``skipped`` with
  ``size_zero_no_investment``. The verdict judges it by its size.
* **Battery attribution** (:class:`models.study.BatteryAttribution`). A
  battery-only option's battery NPV is its NPV. A ``bess_pv`` option's is
  its NPV less a PV-ONLY REFERENCE: the option's network with the
  StorageUnit OMITTED (never fixed at zero, BC-1) and PV fixed at the
  option's size, re-dispatched (``method="battery_removed_same_pv"``). The
  PV rows (CAPEX, FOM, salvage) of the two cases are the same numbers and
  cancel exactly. Without the reference the attribution is
  ``not_established`` (``bess_pv_value_not_attributable_to_battery``) and
  the verdict cannot name that option.
* **Verdict** (:func:`verdict`). Candidates are the options with a battery
  above ``EPSILON_MW`` and an established battery NPV; the best is the
  largest battery NPV at the centre. ``not_recommended`` when there is no
  candidate or the best is <= 0 at the centre (and every battery option is
  judged); ``recommended`` when the best is > 0 at the centre and no tornado
  bound turns it negative; ``marginal`` when one does. Without an ``ok``
  tornado on the best option the verdict is ``not_established`` unless the
  centre alone already decides ``not_recommended``. The sentence is a fixed
  template whose numbers are ``{{fact_id}}`` references.
* **Tornado** (:func:`run_tornado`, ``method=redispatch_fixed_sizes``, review
  v1 B5). On the best candidate (and its PV-only reference), sizes FIXED at
  ``p_nom_opt`` (``p_nom_extendable=False``), for each key driver of the
  question at its range bounds: the energy-price level and the demand-charge
  price RE-DISPATCH the fixed-size network at the perturbed tariff
  (``tariff.tariff_from_ledger``, the one path the pack, the LP and the bill
  share) and RECOMPUTE the baseline bill at that tariff with the bill
  calculator (no solve: the baseline has no flexible asset; BC-7); the
  battery cost rows change CAPEX, replacements and the FOM tied to the
  inverter investment only — the case is rebuilt from the variant ledger on
  the already-solved network, whose dispatch is unchanged at fixed sizes; the
  discount rate changes the discounting and the annuity-basis salvage, and
  goes to BOTH the variant ledger and the variant ``SolverConfig`` (else the
  pro forma refuses ``discount_rate_differs_from_lp``).
* **Range semantics** (gate S2 [S5]). A driver's bounds are its ledger
  range; a customised value OUTSIDE that range re-centres the range's
  relative width on the value (``range_recentred_on_user_value``).

Solving is injected (``solve(network, cfg, variant_id)``): the worker
(``services/study/tornado_runner.py``) solves each variant on a throw-away
study-owned fork through the queue; the tests solve in process.
"""
from __future__ import annotations

import math
import pathlib
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from models.study import (
    AssumptionsLedger,
    BatteryAttribution,
    Bill,
    DecisionQuestion,
    Fidelity,
    FinancialBasis,
    Figure,
    Findings,
    InvestmentCase,
    LedgerRow,
    Robustness,
    Tariff,
    TornadoRow,
    ValueStream,
    Verdict,
)
from services.study import packs
from services.study import proforma
from services.study import questions as Q
from services.study import run_hashes
from services.study import tariff as study_tariff
from services.study.proforma import BILL_COMPONENTS

__all__ = [
    "BY_CONSTRUCTION", "DISPATCH_ROWS", "EPSILON_MW", "FindingsRefused", "NPV_TOL_EUR",
    "STREAMS", "TORNADO_AUX", "TornadoContext", "TornadoOutcome", "VariantFailed",
    "assemble_findings", "attribute", "bounds_for", "centre_attributions",
    "context_from_disk", "estimate_tornado_solves", "explain", "is_zero_size",
    "load_inputs", "run_tornado", "value_streams", "verdict",
]

# A battery at or below `EPSILON_MW` is "no investment" (gate S5 carry); the
# threshold and its predicate are the pro forma's, so the case's
# `size_zero_no_investment` note and the verdict cannot disagree.
EPSILON_MW = proforma.EPSILON_MW
# A bound's battery NPV below minus this is a sign flip; within it, break-even.
NPV_TOL_EUR = 1.0


def is_zero_size(p_mw: float | None) -> bool:
    """The ONE size rule (``proforma.is_zero_size``)."""
    return proforma.is_zero_size(p_mw)

# The bill's six components (`proforma.BILL_COMPONENTS`) in the question's
# streams (gate S3 N8): the per-MWh network charge travels with the energy it
# is levied on; the contracted capacity charge with the demand charge.
STREAMS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("demand_charge_reduction", "Demand-charge reduction", ("demand", "capacity")),
    ("energy_shift", "Energy time-shift", ("energy", "network")),
    ("export_credit", "Export credit", ("export_credit",)),
    ("fixed", "Fixed charges", ("fixed",)),
)
if sorted(c for _k, _l, cs in STREAMS for c in cs) != sorted(k for k, _ in BILL_COMPONENTS):
    raise ImportError("findings.STREAMS must cover the bill's six components exactly")

# The key drivers the tornado re-dispatches; the rest need no solve.
DISPATCH_ROWS = ("energy_price_level", "demand_charge_price")
CAPEX_ROWS = ("battery_storage_eur_per_kwh", "battery_inverter_eur_per_kw")
RATE_ROWS = ("discount_rate",)
# The two S5 codes: at the LP optimum NPV >= 0, IRR >= rate and discounted
# payback <= horizon by construction (gate S5 carry: all three, not NPV only).
BY_CONSTRUCTION = ("npv_nonnegative_at_optimum_by_construction",
                   "irr_and_discounted_payback_bounded_at_optimum_by_construction")


class TornadoStopped(RuntimeError):
    """The solve callable answered None: aborted, or out of budget."""


class VariantFailed(RuntimeError):
    """One variant's solve failed (not an abort); ``code`` is machine-read."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(f"{code}: {message}")
        self.code = code


# ── value streams ─────────────────────────────────────────────────────────

def value_streams(bill_baseline: Bill, bill_option: Bill) -> list[ValueStream]:
    """
    The savings ``bill_baseline - bill_option`` split into the question's
    streams. Each stream is the sum of its components' deltas (baseline minus
    option): the export credit is a NEGATIVE bill component, so more export
    is a positive stream. The streams sum to the savings exactly. A null
    component makes its stream null with a flag; zero savings leave every
    share null (``zero_savings``).
    """
    base, opt = bill_baseline.by_component, bill_option.by_component
    savings = None
    if bill_baseline.annual_bill is not None and bill_option.annual_bill is not None:
        savings = float(bill_baseline.annual_bill) - float(bill_option.annual_bill)
    out: list[ValueStream] = []
    for key, label, comps in STREAMS:
        missing = [c for c in comps
                   if getattr(base, c) is None or getattr(opt, c) is None]
        flags: dict[str, str] = {}
        if missing:
            value = None
            flags["annual_value"] = "bill_component_unavailable"
        else:
            value = float(sum(float(getattr(base, c)) - float(getattr(opt, c))
                              for c in comps))
        if value is None:
            share = None
            flags["share"] = "bill_component_unavailable"
        elif not savings:
            share = None
            flags["share"] = "zero_savings" if savings == 0.0 else "bill_unavailable"
        else:
            share = value / savings
        out.append(ValueStream(key=key, label=label, annual_value=value, share=share,
                               engine="bill_calculator", unavailable=flags))
    return out


# ── range semantics (gate S2 [S5]) ────────────────────────────────────────

def bounds_for(row: LedgerRow) -> tuple[float, float, tuple[str, ...]] | None:
    """
    ``(low, high, notes)`` the tornado evaluates a driver at, or None when
    the row has no value or no range. A value inside its range keeps it. A
    value outside it (a user's quote beyond the library's band) re-centres
    the range's RELATIVE width on the value: the band's ends as ratios of its
    midpoint, times the value (a +-30 % band stays +-30 %, around the user's
    number), so both bounds bracket the value the verdict judges.
    """
    if row.value is None or row.range is None:
        return None
    v, lo, hi = float(row.value), float(row.range.low), float(row.range.high)
    if lo <= v <= hi:
        return lo, hi, ()
    mid = (lo + hi) / 2.0
    if mid == 0.0:
        return v - (hi - lo) / 2.0, v + (hi - lo) / 2.0, ("range_recentred_on_user_value",)
    return v * lo / mid, v * hi / mid, ("range_recentred_on_user_value",)


def _with_value(ledger: AssumptionsLedger, key: str, value: float) -> AssumptionsLedger:
    rows = [r.model_copy(update={"value": float(value), "unavailable": {}})
            if r.key == key else r for r in ledger.rows]
    return ledger.model_copy(update={"rows": rows})


# ── the inputs ────────────────────────────────────────────────────────────

@dataclass
class OptionInput:
    """One solved option: its network (read from disk) and its run record."""

    option_id: str
    network: Any
    bill: Bill | None
    asset_economics: Mapping | None = None
    caveats: tuple[str, ...] = ()


@dataclass
class TornadoContext:
    study_id: str
    question: DecisionQuestion
    intake: Mapping[str, Any]
    ledger: AssumptionsLedger
    library: Any
    tariff: Tariff                 # the ledger-applied (effective) tariff
    baseline_network: Any          # the `none` fork, solved (for its p0)
    baseline_bill: Bill | None
    options: dict[str, OptionInput]
    fidelity: Fidelity | None = None
    study_currency_year: int | None = None


SolveFn = Callable[[Any, Any, str], Any]


@dataclass
class TornadoOutcome:
    attributions: list[BatteryAttribution]
    robustness: Robustness
    cases: dict[str, InvestmentCase] = field(default_factory=dict)
    reference_cases: dict[str, InvestmentCase] = field(default_factory=dict)
    solves: int = 0
    # The PV-only reference's bill per bess_pv option (the battery's streams).
    reference_bills: dict[str, Bill] = field(default_factory=dict)


# ── sizes, networks, bills ────────────────────────────────────────────────

def _size(n, component: str, name: str) -> float | None:
    df = getattr(n, component)
    if name not in df.index:
        return None
    try:
        v = float(df.at[name, "p_nom_opt"])
    except (KeyError, TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def battery_size(n) -> float | None:
    return _size(n, "storage_units", packs.BATTERY_NAME)


def pv_size(n) -> float | None:
    return _size(n, "generators", packs.PV_NAME)


def has_pv(option_id: str, question: DecisionQuestion = Q.BESS_AT_SITE) -> bool:
    return Q.PV in Q.option(question, option_id).free_assets


def copy_network(n):
    """
    A deep copy of a solved network without its solver model (PyPSA refuses
    to copy one that still holds it; a network read from disk has none).
    """
    model = getattr(n, "_model", None)
    if model is None:
        return n.copy()
    n._model = None
    try:
        return n.copy()
    finally:
        n._model = model


def fixed_size_network(n, *, drop_battery: bool = False):
    """
    A copy of a solved option network with every sized asset FIXED at its
    ``p_nom_opt`` (``p_nom_extendable=False``). An asset at or below
    ``EPSILON_MW`` — and the battery when ``drop_battery`` (the PV-only
    reference) — is OMITTED, never fixed at zero (BC-1: preflight errors on
    a non-extendable asset with ``p_nom = 0``).
    """
    m = copy_network(n)
    for component, cls, name in (("storage_units", "StorageUnit", packs.BATTERY_NAME),
                                 ("generators", "Generator", packs.PV_NAME)):
        size = _size(m, component, name)
        if size is None and name not in getattr(m, component).index:
            continue
        if (drop_battery and name == packs.BATTERY_NAME) or size is None or is_zero_size(size):
            m.remove(cls, name)
            continue
        df = getattr(m, component)
        df.loc[name, "p_nom"] = size
        df.loc[name, "p_nom_min"] = 0.0
        df.loc[name, "p_nom_max"] = size
        df.loc[name, "p_nom_extendable"] = False
    return m


def _bill_of(n, tariff: Tariff, fidelity=None) -> Bill:
    p0 = getattr(n.links_t, "p0", None)
    imp = p0[packs.IMPORT_LINK] if p0 is not None and packs.IMPORT_LINK in p0 else None
    exp = p0[packs.EXPORT_LINK] if p0 is not None and packs.EXPORT_LINK in p0 else None
    return study_tariff.BillCalculator().bill(imp, exp, tariff, n.snapshot_weightings,
                                              fidelity=fidelity)


def _case(ctx: TornadoContext, n, cfg, ledger, tariff, bills, option_id,
          asset_economics=None) -> InvestmentCase:
    return proforma.build_investment_case(
        n, cfg, None, ledger, bills, option_id, study_id=ctx.study_id, tariff=tariff,
        fidelity=ctx.fidelity, asset_economics=asset_economics,
        study_currency_year=ctx.study_currency_year, question=ctx.question)


def _npv(case: InvestmentCase | None) -> float | None:
    if case is None or case.status != "ok" or case.kpis is None:
        return None
    return float(case.kpis.npv)


# ── attribution ───────────────────────────────────────────────────────────

def attribute(option_id: str, case: InvestmentCase | None, p_bat: float | None, *,
              question: DecisionQuestion = Q.BESS_AT_SITE,
              reference_case: InvestmentCase | None = None,
              reference_npv: float | None = None,
              reference_missing: str | None = None,
              fidelity: Fidelity | None = None,
              currency_year: int | None = None) -> BatteryAttribution:
    """
    One option's :class:`BatteryAttribution` from its centre case and, for a
    ``bess_pv`` option, the PV-only reference (a case, or ``reference_npv``
    when the reference is the baseline itself: no PV left to keep).
    ``reference_missing`` names why a ``bess_pv`` reference is absent.
    """
    pv = has_pv(option_id, question)
    method = "battery_removed_same_pv" if pv else "battery_only"
    common = dict(option_id=option_id, method=method, fidelity=fidelity,
                  currency_year=currency_year)
    flags: dict[str, str] = {}
    if p_bat is None:
        reason = "battery_not_solved"
        return BatteryAttribution(
            **common, status="not_established", battery_p_nom_mw=None, battery_npv=None,
            option_npv=None, reference_npv=None, battery_payback_simple=None,
            unavailable={k: reason for k in ("battery_p_nom_mw", "battery_npv", "option_npv",
                                             "reference_npv", "battery_payback_simple")},
            notes=(reason,))
    option_npv = _npv(case)
    if option_npv is None:
        flags["option_npv"] = "case_not_established"
    if is_zero_size(p_bat):
        # Judged by its size: the NPV (0 +- solver noise) is not read.
        reason = "size_zero_no_investment"
        return BatteryAttribution(
            **common, status="skipped", battery_p_nom_mw=p_bat, battery_npv=None,
            option_npv=option_npv, reference_npv=None, battery_payback_simple=None,
            unavailable={**flags, "battery_npv": reason, "reference_npv": reason,
                         "battery_payback_simple": reason},
            notes=(reason,))
    if option_npv is None:
        return BatteryAttribution(
            **common, status="not_established", battery_p_nom_mw=p_bat, battery_npv=None,
            option_npv=None, reference_npv=None, battery_payback_simple=None,
            unavailable={**flags, "battery_npv": "case_not_established",
                         "reference_npv": "case_not_established",
                         "battery_payback_simple": "case_not_established"},
            notes=("case_not_established",))
    if not pv:
        pb = case.kpis.payback_simple
        return BatteryAttribution(
            **common, status="ok", battery_p_nom_mw=p_bat, battery_npv=option_npv,
            option_npv=option_npv, reference_npv=None, battery_payback_simple=pb,
            unavailable={"reference_npv": "not_applicable_battery_only",
                         **({} if pb is not None else {
                             "battery_payback_simple": case.kpis.unavailable.get(
                                 "payback_simple", "never_pays_back")})},
            notes=("battery_value_is_the_option_value",))
    ref = _npv(reference_case) if reference_case is not None else reference_npv
    if ref is None:
        code = "bess_pv_value_not_attributable_to_battery"
        return BatteryAttribution(
            **common, status="not_established", battery_p_nom_mw=p_bat, battery_npv=None,
            option_npv=option_npv, reference_npv=None, battery_payback_simple=None,
            unavailable={"battery_npv": code,
                         "reference_npv": reference_missing or "reference_not_computed",
                         "battery_payback_simple": code},
            notes=tuple(c for c in (code, reference_missing) if c))
    notes = ["battery_removed_same_pv_reference", "pv_rows_cancel_exactly"]
    if reference_case is None:
        notes.append("reference_is_the_baseline")
        net = [y.net_cash_flow for y in case.years]
    else:
        net = [o.net_cash_flow - r.net_cash_flow
               for o, r in zip(case.years, reference_case.years, strict=True)]
    pb = proforma.payback(net)
    return BatteryAttribution(
        **common, status="ok", battery_p_nom_mw=p_bat, battery_npv=option_npv - ref,
        option_npv=option_npv, reference_npv=ref, battery_payback_simple=pb,
        unavailable={} if pb is not None else {"battery_payback_simple": "never_pays_back"},
        notes=tuple(notes))


# ── the tornado ───────────────────────────────────────────────────────────

def _row(ledger: AssumptionsLedger, key: str) -> LedgerRow | None:
    return next((r for r in ledger.rows if r.key == key), None)


def _kind(key: str) -> str | None:
    if key in DISPATCH_ROWS:
        return "redispatch"
    if key in CAPEX_ROWS:
        return "capex_only"
    if key in RATE_ROWS:
        return "rate_only"
    return None


def _skip_code(ledger: AssumptionsLedger, tariff: Tariff, key: str) -> str | None:
    """Why a driver has no bar (a code), or None when it has one."""
    row = _row(ledger, key)
    if row is None:
        return "row_missing"
    if _kind(key) is None:
        return "row_not_modelled_in_tornado"
    if row.value is None:
        return row.unavailable.get("value") or "row_has_no_value"
    if row.range is None:
        return "row_has_no_range"
    if key == "energy_price_level" and len(
            {float(b.price_per_mwh) for b in tariff.energy_bands}) <= 1:
        # One flat band: the level rescales nothing (gate S4 [S5]).
        return "energy_price_level_no_effect_single_band"
    if key == "demand_charge_price" and tariff.demand_charge is None:
        return "tariff_has_no_demand_charge"
    return None


def _candidates(attributions: list[BatteryAttribution]) -> list[BatteryAttribution]:
    return [a for a in attributions if a.status == "ok" and a.battery_npv is not None
            and a.battery_p_nom_mw is not None and not is_zero_size(a.battery_p_nom_mw)]


def option_sizes(ctx: TornadoContext) -> dict[str, tuple[float | None, float | None]]:
    """``{option_id: (battery MW, PV MW)}`` of the solved options."""
    return {oid: (battery_size(o.network), pv_size(o.network)) for oid, o in ctx.options.items()}


def estimate_tornado_solves(question: DecisionQuestion, ledger: AssumptionsLedger,
                            tariff: Tariff,
                            sizes: Mapping[str, tuple[float | None, float | None]]) -> int:
    """
    The worst-case solve count, known before the first solve (the engine's
    own promise; ``campaign`` is charged one solve at a time against it): one
    PV-only reference per ``bess_pv`` option with a battery and PV above
    epsilon, plus two re-dispatches per dispatch-sensitive driver — doubled
    when the best option could be a ``bess_pv`` one (its reference is
    re-dispatched at every bound too). Cost and rate rows solve nothing, and
    a zero-size battery is never re-dispatched.
    """
    refs, pv_candidate = 0, False
    for oid, (p_bat, p_pv) in sizes.items():
        if not Q.option(question, oid).free_assets:
            continue
        if p_bat is None or is_zero_size(p_bat) or not has_pv(oid, question):
            continue
        pv_candidate = True
        if p_pv is not None and not is_zero_size(p_pv):
            refs += 1
    dispatch = sum(1 for k in question.key_drivers
                   if _kind(k) == "redispatch" and _skip_code(ledger, tariff, k) is None)
    return refs + dispatch * 2 * (2 if pv_candidate else 1)


def _solved(solve: SolveFn, net, cfg, variant_id: str):
    out = solve(net, cfg, variant_id)
    if out is None:
        raise TornadoStopped(variant_id)
    return out


def _price_bound(ctx: TornadoContext, n_centre, option_id: str, key: str, value: float,
                 solve: SolveFn, variant_id: str, *, reference: bool) -> InvestmentCase:
    """A dispatch-sensitive bound: re-dispatch the fixed sizes at the tariff."""
    vl = _with_value(ctx.ledger, key, value)
    vt = packs.effective_tariff(ctx.intake, vl, ctx.library, n_centre.snapshots)
    cfg = packs.option_solver_config(vl, vt)
    net = fixed_size_network(n_centre, drop_battery=reference)
    study_tariff.write_tariff_prices(net, vt, packs.IMPORT_LINK, packs.EXPORT_LINK)
    solved = _solved(solve, net, cfg, variant_id)
    # BC-7: the baseline bill at the SAME perturbed tariff (no solve).
    bills = {"baseline": _bill_of(ctx.baseline_network, vt, ctx.fidelity),
             "option": _bill_of(solved, vt, ctx.fidelity)}
    return _case(ctx, solved, cfg, vl, vt, bills, option_id)


def _capex_bound(ctx: TornadoContext, n_centre, option_id: str, key: str, value: float,
                 bill: Bill, *, reference: bool) -> InvestmentCase:
    """
    A battery cost bound: CAPEX only, no re-dispatch. The sizes are fixed and
    the dispatch at fixed sizes does not depend on the battery's cost, so the
    case is rebuilt from the variant ledger on the already-solved network:
    the year-0 CAPEX, the inverter replacements and their salvage move with
    the ledger (``packs.battery_upfront_eur_per_mw``), and so does the FOM,
    which the pack ties to the inverter investment
    (``packs.battery_fom_eur_per_mw``, read back by asset economics from the
    network's ``fom_cost``). The bills are the centre's.
    """
    vl = _with_value(ctx.ledger, key, value)
    net = copy_network(n_centre)
    if packs.BATTERY_NAME in net.storage_units.index:
        hours = float(net.storage_units.at[packs.BATTERY_NAME, "max_hours"])
        net.storage_units.loc[packs.BATTERY_NAME, "capital_cost"] = (
            packs.battery_capital_cost_eur_per_mw(vl, hours))
        net.storage_units.loc[packs.BATTERY_NAME, "fom_cost"] = packs.battery_fom_eur_per_mw(vl)
    cfg = packs.option_solver_config(vl, ctx.tariff)
    return _case(ctx, net, cfg, vl, ctx.tariff, {"baseline": ctx.baseline_bill, "option": bill},
                 option_id)


def _rate_bound(ctx: TornadoContext, n_centre, option_id: str, key: str, value: float,
                bill: Bill, *, reference: bool) -> InvestmentCase:
    """
    The discount-rate bound: no re-dispatch. The variant rate goes to the
    ledger AND the ``SolverConfig`` (else `discount_rate_differs_from_lp`),
    and to the assets' ``discount_rate`` and the battery's two-annuity
    ``capital_cost``, which the pro forma and asset economics read.
    """
    vl = _with_value(ctx.ledger, key, value)
    net = copy_network(n_centre)
    if packs.BATTERY_NAME in net.storage_units.index:
        hours = float(net.storage_units.at[packs.BATTERY_NAME, "max_hours"])
        net.storage_units.loc[packs.BATTERY_NAME, "capital_cost"] = (
            packs.battery_capital_cost_eur_per_mw(vl, hours))
        net.storage_units.loc[packs.BATTERY_NAME, "discount_rate"] = float(value)
    if packs.PV_NAME in net.generators.index:
        net.generators.loc[packs.PV_NAME, "discount_rate"] = float(value)
    cfg = packs.option_solver_config(vl, ctx.tariff)
    return _case(ctx, net, cfg, vl, ctx.tariff, {"baseline": ctx.baseline_bill, "option": bill},
                 option_id)


@dataclass
class _Target:
    """What one bound is evaluated on: the option and, for bess_pv, its reference."""

    option_id: str
    network: Any                   # the option's solved network (centre)
    bill: Bill
    reference_network: Any = None  # the centre PV-only reference, solved
    reference_bill: Bill | None = None
    reference_is_baseline: bool = False


def _battery_npv_at(ctx: TornadoContext, tgt: _Target, key: str, value: float,
                    solve: SolveFn, tag: str) -> float | None:
    kind = _kind(key)

    def one(reference: bool) -> float | None:
        n = tgt.reference_network if reference else tgt.network
        bill = tgt.reference_bill if reference else tgt.bill
        vid = f"{tag}r" if reference else tag
        if kind == "redispatch":
            case = _price_bound(ctx, tgt.network, tgt.option_id, key, value, solve, vid,
                                reference=reference)
        elif kind == "capex_only":
            case = _capex_bound(ctx, n, tgt.option_id, key, value, bill, reference=reference)
        else:
            case = _rate_bound(ctx, n, tgt.option_id, key, value, bill, reference=reference)
        return _npv(case)

    npv = one(False)
    if npv is None:
        return None
    if tgt.reference_network is None:
        return npv  # battery only, or the reference is the baseline (NPV 0)
    ref = one(True)
    return None if ref is None else npv - ref


def _centre(ctx: TornadoContext, solve: SolveFn | None):
    """
    The centre cases and the battery attribution of every solved option;
    with ``solve``, the PV-only reference of each ``bess_pv`` option is
    solved too (without it, those options stay ``not_established``).
    """
    cases: dict[str, InvestmentCase] = {}
    refs: dict[str, InvestmentCase] = {}
    ref_nets: dict[str, tuple[Any, Bill | None, bool]] = {}
    attributions: list[BatteryAttribution] = []
    stopped = False
    centre_cfg = packs.option_solver_config(ctx.ledger, ctx.tariff)
    for oid, opt in ctx.options.items():
        spec = Q.option(ctx.question, oid)
        if not spec.free_assets:
            continue
        p_bat = battery_size(opt.network)
        case = None
        if opt.bill is not None and ctx.baseline_bill is not None:
            case = _case(ctx, opt.network, centre_cfg, ctx.ledger, ctx.tariff,
                         {"baseline": ctx.baseline_bill, "option": opt.bill}, oid,
                         asset_economics=opt.asset_economics)
            cases[oid] = case
        # The year the CASE was built on (`proforma._one_currency_year`: the
        # ledger's when the tariff states none), never the tariff's optional
        # field (gate S6 BC-S6-2).
        kw: dict[str, Any] = dict(question=ctx.question, fidelity=ctx.fidelity,
                                  currency_year=(case.currency_year if case is not None
                                                 else ctx.tariff.currency_year))
        if (has_pv(oid, ctx.question) and p_bat is not None and not is_zero_size(p_bat)
                and _npv(case) is not None):
            p_pv = pv_size(opt.network)
            if p_pv is None or is_zero_size(p_pv):
                kw["reference_npv"] = 0.0     # no PV to keep: the baseline
                ref_nets[oid] = (None, None, True)
            elif solve is None:
                kw["reference_missing"] = "reference_not_computed_tornado_not_run"
            elif stopped:
                kw["reference_missing"] = "reference_not_computed_aborted"
            else:
                ref_net = fixed_size_network(opt.network, drop_battery=True)
                try:
                    solved = _solved(solve, ref_net, centre_cfg, f"ref-{oid}")
                except TornadoStopped:
                    stopped = True
                    kw["reference_missing"] = "reference_not_computed_aborted"
                except VariantFailed as exc:
                    kw["reference_missing"] = f"reference_{exc.code}"
                else:
                    bill = _bill_of(solved, ctx.tariff, ctx.fidelity)
                    refs[oid] = _case(ctx, solved, centre_cfg, ctx.ledger, ctx.tariff,
                                      {"baseline": ctx.baseline_bill, "option": bill}, oid)
                    kw["reference_case"] = refs[oid]
                    ref_nets[oid] = (solved, bill, False)
        attributions.append(attribute(oid, case, p_bat, **kw))
    return cases, refs, ref_nets, attributions, stopped


def _ref_bills(ref_nets) -> dict[str, Bill]:
    return {oid: bill for oid, (_n, bill, _b) in ref_nets.items() if bill is not None}


def centre_attributions(ctx: TornadoContext) -> tuple[list[BatteryAttribution],
                                                      dict[str, InvestmentCase]]:
    """The attributions without any solve (``bess_pv`` needs the tornado run)."""
    cases, _refs, _nets, attributions, _stopped = _centre(ctx, None)
    return attributions, cases


def run_tornado(ctx: TornadoContext, solve: SolveFn, *,
                stop: Callable[[], bool] = lambda: False,
                progress: Callable[..., None] = lambda **_kw: None) -> TornadoOutcome:
    """
    Centre cases, battery attribution (with the PV-only references), the
    best candidate, then the fixed-size tornado on it. An abort (``stop()``
    true, or ``solve`` answering None) keeps every bar already computed and
    names the rest in ``robustness.pending``.
    """
    solves = 0

    def counted(net, cfg, vid):
        nonlocal solves
        if stop():
            return None
        out = solve(net, cfg, vid)
        if out is not None:
            solves += 1
        return out

    cases, refs, ref_nets, attributions, stopped = _centre(ctx, counted)
    keys = list(ctx.question.key_drivers)
    rob = Robustness(status="not_established")
    cands = _candidates(attributions)
    if stopped:
        return TornadoOutcome(attributions, rob.model_copy(update={
            "pending": [k for k in keys if _skip_code(ctx.ledger, ctx.tariff, k) is None],
            "note": "tornado_aborted", "solves_charged": solves}),
            cases, refs, solves, _ref_bills(ref_nets))
    if not cands:
        return TornadoOutcome(attributions, rob.model_copy(update={
            "status": "skipped", "note": "no_battery_candidate", "solves_charged": solves}),
            cases, refs, solves, _ref_bills(ref_nets))
    best = max(cands, key=lambda a: a.battery_npv)
    if best.battery_npv <= 0.0:
        return TornadoOutcome(attributions, rob.model_copy(update={
            "status": "skipped", "note": "best_battery_npv_not_positive_at_centre",
            "option_id": best.option_id, "npv_centre": best.battery_npv,
            "solves_charged": solves}), cases, refs, solves, _ref_bills(ref_nets))

    opt = ctx.options[best.option_id]
    ref_n, ref_bill, ref_is_base = ref_nets.get(best.option_id, (None, None, False))
    tgt = _Target(best.option_id, opt.network, opt.bill, ref_n, ref_bill, ref_is_base)
    rows: list[TornadoRow] = []
    skipped: dict[str, str] = {}
    pending: list[str] = []
    failed = False
    for i, key in enumerate(keys):
        code = _skip_code(ctx.ledger, ctx.tariff, key)
        if code is not None:
            skipped[key] = code
            continue
        if stopped:
            pending.append(key)
            continue
        row = _row(ctx.ledger, key)
        low, high, notes = bounds_for(row)
        progress(current=key)
        values: dict[str, float | None] = {}
        flags: dict[str, str] = {}
        try:
            for side, v in (("low", low), ("high", high)):
                values[side] = _battery_npv_at(ctx, tgt, key, v, counted, f"t{i}{side[0]}")
                if values[side] is None:
                    flags[f"npv_{side}"] = "case_not_established"
        except TornadoStopped:
            stopped = True
            pending.append(key)
            continue
        except (proforma.ProformaError, packs.PackError, study_tariff.TariffError,
                VariantFailed) as exc:
            failed = True
            code = getattr(exc, "code", "tornado_row_failed")
            for side in ("low", "high"):
                values.setdefault(side, None)
                if values[side] is None:
                    flags[f"npv_{side}"] = code
        lo_v, hi_v = values.get("low"), values.get("high")
        swing = None if lo_v is None or hi_v is None else abs(hi_v - lo_v)
        if swing is None:
            flags["swing"] = "bound_not_established"
            failed = True
        rows.append(TornadoRow(
            key=key, label=row.label, unit=row.unit, centre_value=float(row.value),
            low_value=low, high_value=high, npv_low=lo_v, npv_high=hi_v, swing=swing,
            evaluation=_kind(key), notes=notes, unavailable=flags))
    rows.sort(key=lambda r: (r.swing is None, -(r.swing or 0.0)))
    status = "ok" if not (stopped or failed) else "not_established"
    note = "tornado_aborted" if stopped else ("tornado_row_failed" if failed else None)
    rob = Robustness(status=status, tornado=rows, pending=pending, note=note,
                     option_id=best.option_id, npv_centre=best.battery_npv,
                     skipped=skipped, solves_charged=solves)
    return TornadoOutcome(attributions, rob, cases, refs, solves, _ref_bills(ref_nets))


# ── the verdict ───────────────────────────────────────────────────────────

_TEMPLATES = {
    "recommended": (
        "Recommended: a battery of {{battery_p_nom_mw}} MW with {{battery_max_hours}} "
        "hours of storage has a positive battery NPV of {{battery_npv}} at the centre "
        "and at every tornado bound{pv}."),
    # Gate S6 re-gate BC-S6-v2-1: an unjudged option could be better at the
    # centre, and could itself flip, so the recommendation is scoped to the
    # options judged and says so in the sentence the client reads.
    "recommended_among_judged": (
        "Recommended among the battery options the study could judge: a battery of "
        "{{battery_p_nom_mw}} MW with {{battery_max_hours}} hours of storage has a "
        "positive battery NPV of {{battery_npv}} at the centre and at every tornado "
        "bound{pv}. Other battery options were not judged, so a different size may "
        "be better."),
    "marginal": (
        "Marginal: a battery of {{battery_p_nom_mw}} MW with {{battery_max_hours}} "
        "hours of storage has a battery NPV of {{battery_npv}} at the centre{pv}, "
        "but it turns negative within the tornado's bounds on the drivers listed."),
    "not_recommended_best": (
        "Not recommended: the best battery option has a battery NPV of "
        "{{battery_npv}} at the centre, which is not positive."),
    "not_recommended_none": (
        "Not recommended: the model sized no battery above zero, or none has a "
        "positive battery NPV, at the centre."),
}
_PV_CLAUSE = ", measured against the same PV of {{pv_p_nom_mw}} MW alone"
_DIGIT = re.compile(r"(?<![A-Za-z])\d")


def sentence_is_digit_free(sentence: str) -> bool:
    """No digit outside a ``{{fact_id}}`` reference (the S7 prose rule)."""
    return not _DIGIT.search(re.sub(r"\{\{[a-z_]+\}\}", "", sentence))


def _fig(key, label, value, unit, engine, *, flag=None, basis=None, currency_year=None,
         fidelity=None) -> Figure:
    if unit == "EUR" and currency_year is None and value is not None:
        # Gate S6 BC-S6-2: a money figure whose currency year no case states is
        # null with a flag, never a number without its year (and never a 500).
        value, flag = None, "currency_year_unknown"
    return Figure(key=key, label=label, value=value, unit=unit, engine=engine,
                  basis=basis, currency_year=currency_year, fidelity=fidelity,
                  unavailable=None if value is not None else (flag or "not_computed"))


def verdict(attributions: list[BatteryAttribution], robustness: Robustness | None, *,
            streams: list[ValueStream] | None = None,
            option_networks: Mapping[str, Any] | None = None,
            caveats: Mapping[str, tuple[str, ...]] | None = None,
            question: DecisionQuestion = Q.BESS_AT_SITE,
            fidelity: Fidelity | None = None,
            expected: Sequence[str] | None = None) -> Verdict:
    """
    The verdict from the battery attributions and the tornado (see the
    module docstring for the rule). ``streams`` are the named option's value
    streams (for the main caveat), ``option_networks`` its network (for the
    sizes in the facts), ``caveats`` the run's per-option caveats
    (``size_at_upper_bound:<asset>``).

    ``expected`` names every battery option the run was asked to judge (gate
    S6 BC-S6-1). One with no ``ok`` or ``skipped`` attribution — pending,
    failed, never run, changed since the run, or not attributable — is
    UNJUDGED. ``marginal`` and ``not_recommended`` then become
    ``not_established`` (an unjudged option could be the robust one, or the
    one worth building). ``recommended`` stands only as a scoped statement:
    a judged battery pays at the centre and at every bound, which establishes
    that a battery pays here, but NOT that this size is the one to build (an
    unjudged option better at the centre that flips at a bound would make
    the complete verdict ``marginal`` on another size). So the sentence is
    ``recommended_among_judged``, which says so, and
    ``options_not_all_judged`` is a disclosure as well as a reason (gate S6
    re-gate BC-S6-v2-1).
    """
    reasons: list[str] = []
    batteries = [a for a in attributions if Q.max_hours(Q.option(question, a.option_id))]
    judged = {a.option_id for a in batteries if a.status in ("ok", "skipped")}
    expected_ids = list(dict.fromkeys(
        list(expected if expected is not None else []) + [a.option_id for a in batteries]))
    unjudged = [o for o in expected_ids if o not in judged]
    for a in batteries:
        if a.status == "not_established":
            reasons.extend(c for c in a.notes if c not in reasons)
    if unjudged:
        reasons.append("options_not_all_judged")
    cands = _candidates(attributions)
    best = max(cands, key=lambda a: a.battery_npv) if cands else None
    currency_year = next((a.currency_year for a in attributions if a.currency_year), None)
    basis = FinancialBasis()

    if best is None or best.battery_npv <= 0.0:
        if unjudged or not batteries:
            return Verdict(status="not_established",
                           reasons=tuple(reasons or ["no_battery_option_judged"]))
        facts = {}
        if best is None:
            tpl = "not_recommended_none"
            reasons.append("no_battery_candidate")
        else:
            tpl = "not_recommended_best"
            facts["battery_npv"] = _fig("battery_npv", "Battery NPV", best.battery_npv,
                                        "EUR", "cash_flow_expander", basis=basis,
                                        currency_year=currency_year, fidelity=fidelity)
        return Verdict(status="ok", class_="not_recommended", sentence=_TEMPLATES[tpl],
                       sentence_template=tpl, facts=facts,
                       option_id=None if best is None else best.option_id,
                       headline_kpis=list(facts.values()),
                       disclosures=BY_CONSTRUCTION if best is not None else (),
                       reasons=tuple(reasons))

    if robustness is None or robustness.status != "ok" or robustness.option_id != best.option_id:
        if robustness is None:
            codes = ["tornado_not_run"]
        elif robustness.status == "ok":
            codes = ["tornado_on_another_option"]
        else:
            codes = ["tornado_not_established"] + ([robustness.note] if robustness.note else [])
        return Verdict(status="not_established", option_id=best.option_id,
                       reasons=tuple(reasons + codes))

    bounds = [(r.key, v) for r in robustness.tornado for v in (r.npv_low, r.npv_high)
              if v is not None]
    flips = [k for k, v in bounds if v < -NPV_TOL_EUR]
    klass = "marginal" if flips else "recommended"
    if klass == "marginal" and unjudged:
        # An unjudged option could be the robust one (BC-S6-1).
        return Verdict(status="not_established", option_id=best.option_id,
                       reasons=tuple(reasons))
    n = (option_networks or {}).get(best.option_id)
    pv_mw = pv_size(n) if n is not None else None
    with_pv = (best.method == "battery_removed_same_pv" and pv_mw is not None
               and not is_zero_size(pv_mw))
    hours = Q.max_hours(Q.option(question, best.option_id))
    facts = {
        "battery_npv": _fig("battery_npv", "Battery NPV", best.battery_npv, "EUR",
                            "cash_flow_expander", basis=basis, currency_year=currency_year,
                            fidelity=fidelity),
        "battery_p_nom_mw": _fig("battery_p_nom_mw", "Battery power", best.battery_p_nom_mw,
                                 "MW", "lp", fidelity=fidelity),
        "battery_payback_simple": _fig(
            "battery_payback_simple", "Battery simple payback", best.battery_payback_simple,
            "years", "cash_flow_expander", basis=basis, currency_year=currency_year,
            fidelity=fidelity, flag=best.unavailable.get("battery_payback_simple")),
        "battery_max_hours": _fig("battery_max_hours", "Hours of storage", hours, "h",
                                  "ledger"),
    }
    if with_pv:
        facts["pv_p_nom_mw"] = _fig("pv_p_nom_mw", "PV power", pv_mw, "MW", "lp",
                                    fidelity=fidelity)
    tpl = "recommended_among_judged" if unjudged else klass
    sentence = _TEMPLATES[tpl].replace("{pv}", _PV_CLAUSE if with_pv else "")
    drivers = (flips or [r.key for r in robustness.tornado])
    drivers = list(dict.fromkeys(drivers))[:3]
    top = max((s for s in streams or [] if s.annual_value is not None),
              key=lambda s: s.annual_value, default=None)
    bound_caveats = [c for c in (caveats or {}).get(best.option_id, ())
                     if c.startswith("size_at_upper_bound")]
    if top is not None and top.key == "demand_charge_reduction":
        main = "demand_charge_perfect_foresight"
    elif bound_caveats:
        main = "size_at_upper_bound"
    else:
        main = "perfect_foresight_dispatch"
    disclosures = list(BY_CONSTRUCTION)
    if bound_caveats:
        disclosures.append("size_at_upper_bound")
    if best.method == "battery_removed_same_pv":
        disclosures.append("battery_value_against_pv_only_reference")
    if unjudged:
        disclosures.append("options_not_all_judged")
    return Verdict(
        status="ok", class_=klass, sentence=sentence, sentence_template=tpl, facts=facts,
        headline_kpis=[facts["battery_npv"], facts["battery_p_nom_mw"],
                       facts["battery_payback_simple"]],
        drivers=drivers, main_caveat=main, option_id=best.option_id,
        disclosures=tuple(disclosures), reasons=tuple(reasons))


# ── explain (per option asset) ────────────────────────────────────────────

def explain(option_id: str, n) -> list[dict]:
    """
    The sizing explanation of an option's own assets (battery, PV) on its
    network, through ``services/study/explain.py`` — the implementation the
    copilot's ``explain_investment`` delegates to, with readers over THIS
    network, so the guided flow needs no copilot.
    """
    from services.study import explain as study_explain

    results, kpis = study_explain.network_readers(n)
    out = []
    for cls, component, name in (("StorageUnit", "storage_units", packs.BATTERY_NAME),
                                 ("Generator", "generators", packs.PV_NAME)):
        if name not in getattr(n, component).index:
            continue
        try:
            payload = study_explain.explain_asset(n, cls, name, results=results,
                                                  asset_kpis=kpis)
        except Exception as exc:  # noqa: BLE001 — named, never a silent gap
            payload = {"unavailable": f"explain_failed:{type(exc).__name__}"}
        out.append({"option_id": option_id, "component_class": cls, "name": name,
                    **payload})
    return out


# ── loading from disk and assembling the findings ─────────────────────────

class FindingsRefused(RuntimeError):
    """The findings (or a tornado) cannot be built. ``status`` is the HTTP code."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.status = status
        self.code = code
        self.message = message

    @property
    def detail(self) -> dict:
        return {"error_kind": self.code, "message": self.message}


@dataclass
class StudyInputs:
    """What the last run left on disk, checked (no network read yet)."""

    study: Any
    question: DecisionQuestion
    run: dict
    findings: dict
    ledger: AssumptionsLedger
    library: Any
    tariff: Tariff
    rows: dict[str, Any]                 # option_id -> fork row (owned, solved)
    recorded: dict[str, str]             # option_id -> the run's network hash
    current: dict[str, str | None]       # option_id -> the file's hash now

    @property
    def changed(self) -> list[str]:
        # The ONE hash rule (`run_hashes.fork_matches`), shared with the case
        # route's 409 and the report's `stale`.
        return sorted(o for o, h in self.recorded.items()
                      if not run_hashes.fork_matches(h, self.current.get(o)))

    def sizes(self) -> dict[str, tuple[float | None, float | None]]:
        out = {}
        for res in self.findings.get("options") or []:
            if res.get("option_id") not in self.rows:
                continue
            sizes = {s["asset"]: s.get("p_nom_opt") for s in res.get("sizes") or []}
            out[res["option_id"]] = (sizes.get(packs.BATTERY_NAME), sizes.get(packs.PV_NAME))
        return out


def study_ledger(study) -> AssumptionsLedger:
    """The study's ledger as the routes read it (stored, else a fresh seed)."""
    from services.study import ledger as ledger_mod
    from services.study import library as study_library

    if study.ledger is not None:
        return ledger_mod.with_study_currency_year(study.ledger, study.currency_year)
    question = Q.get_question(study.question_id) or Q.BESS_AT_SITE
    seeded = study_library.seed_ledger(question, study.intake, study_library.load_library())
    return ledger_mod.with_study_currency_year(seeded, study.currency_year)


def load_inputs(study, base_dir, db, base_uuid: str) -> StudyInputs:
    """
    The last run's records and the forks it solved, verified: the study was
    run (404 ``study_never_run``), the ledger is the one the forks were built
    from (409 ``ledger_changed_since_run``), and each fork is owned by the
    study (an unowned one is dropped, never read). Fork files are hashed
    here (cheap); networks are read later, with the S5 [B1] hash rule.
    """
    import uuid as uuid_mod

    from db.models import Project
    from services.study import forks as study_forks
    from services.study import library as study_library
    from services.study import store

    question = Q.get_question(study.question_id)
    try:
        run = store.load_aux(base_dir, study.study_id, "run")
        faux = store.load_aux(base_dir, study.study_id, "findings")
    except store.StudyUnreadable:
        raise FindingsRefused(500, "study_unreadable", "the run record cannot be read") from None
    if run is None or faux is None or question is None:
        raise FindingsRefused(404, "study_never_run", "this study has not been run")
    ledger = study_ledger(study)
    hashes = faux.get("hashes") or {}
    if not run_hashes.ledger_matches(ledger, hashes):
        raise FindingsRefused(409, "ledger_changed_since_run", (
            "the assumptions ledger changed after the run; re-run the study (the "
            "LP sized the options on the old one)"))
    library = study_library.load_library()
    try:
        tariff = packs.effective_tariff(study.intake, ledger, library)
    except packs.PackError as exc:
        raise FindingsRefused(422, exc.code, exc.message) from None
    rows: dict[str, Any] = {}
    recorded: dict[str, str] = {}
    current: dict[str, str | None] = {}
    by_fork = hashes.get("option_network_hashes") or {}
    for res in faux.get("options") or []:
        ref = res.get("project_ref")
        if res.get("solve_status") != "ok" or not ref:
            continue
        try:
            row = db.get(Project, uuid_mod.UUID(str(ref)))
        except (TypeError, ValueError):
            row = None
        if row is None or not study_forks.is_study_owned(
                row, study_id=study.study_id, base_uuid=str(base_uuid)):
            continue
        oid = res["option_id"]
        rows[oid] = row
        recorded[oid] = by_fork.get(str(row.id))
        current[oid] = run_hashes.fork_file_hash(row)
    return StudyInputs(study, question, run, faux, ledger, library, tariff, rows,
                       recorded, current)


def context_from_disk(inp: StudyInputs) -> TornadoContext:
    """
    The tornado's inputs, every network read from its fork's FILE (never a
    resident context a user may have opened) and only when it is the network
    the run solved (the S5 [B1] rule): a changed fork is left out, and a
    changed or missing baseline refuses (409 ``fork_changed_since_run``).
    """
    from services.study import runner as study_runner

    details = inp.run.get("details") or {}
    fidelity = next((r.get("fidelity") for r in inp.findings.get("options") or []
                     if r.get("fidelity")), None)
    nets: dict[str, Any] = {}
    for oid, row in inp.rows.items():
        if inp.recorded.get(oid) is None:
            continue
        n, h = study_runner.fork_network_from_disk(row)
        if not run_hashes.fork_matches(inp.recorded[oid], h):
            continue
        nets[oid] = n
    if "none" not in nets:
        raise FindingsRefused(409, "fork_changed_since_run", (
            "the baseline's network is missing or changed after the run; re-run "
            "the study"))

    def bill(oid):
        raw = (details.get(oid) or {}).get("bill")
        return None if raw is None else Bill.model_validate(raw)

    options = {oid: OptionInput(oid, n, bill(oid), (details.get(oid) or {}).get("asset_economics"),
                                tuple((details.get(oid) or {}).get("caveats") or ()))
               for oid, n in nets.items() if Q.option(inp.question, oid).free_assets}
    return TornadoContext(
        study_id=inp.study.study_id, question=inp.question, intake=dict(inp.study.intake),
        ledger=inp.ledger, library=inp.library, tariff=inp.tariff,
        baseline_network=nets["none"], baseline_bill=bill("none"), options=options,
        fidelity=Fidelity(fidelity) if fidelity else None,
        study_currency_year=inp.study.currency_year)


_CODE = re.compile(r"^[a-z]+(_[a-z]+)*(:[a-z0-9_,]+)?$")


def _digit_free(codes) -> tuple[str, ...]:
    """Honesty notes are codes; a code with digits in its head is dropped loudly."""
    out = []
    for c in codes:
        head = str(c).split(":", 1)[0]
        out.append(head if _CODE.match(head) and not _DIGIT.search(head)
                   else "uncoded_note_dropped")
    return tuple(dict.fromkeys(out))


def assemble_findings(study, base_dir, db, base_uuid: str, *,
                      loaded: tuple[StudyInputs, TornadoContext] | None = None) -> Findings:
    """
    The findings of the last run (plan S6): the options as the run recorded
    them; the battery's value per option; the tornado when one ran on THIS
    run's forks and ledger (else ``robustness`` is not established and
    names why); the verdict; the named option's value streams; the sizing
    explanation of every solved option's battery and PV; completeness per
    section; the hashes it was computed from; and honesty notes as codes.

    ``loaded`` is ``(load_inputs(...), context_from_disk(...))`` already
    computed by the caller (the S7 report, which reads the same networks for
    the cases), so no network is read twice.
    """
    from models.study import BaselineResult, FindingsHashes, OptionResult
    from services.study import runner as study_runner
    from services.study import store

    if loaded is None:
        inp = load_inputs(study, base_dir, db, base_uuid)
        ctx = context_from_disk(inp)
    else:
        inp, ctx = loaded
    faux = inp.findings
    notes: list[str] = list(faux.get("honesty_notes") or ())
    changed = inp.changed
    if changed:
        notes.append("fork_changed_since_run")
    tornado = store.load_aux(base_dir, study.study_id, TORNADO_AUX)
    hashes = faux.get("hashes") or {}
    fresh = (tornado is not None and tornado.get("status") in ("done", "aborted")
             and tornado.get("ledger_hash") == hashes.get("ledger_hash")
             and tornado.get("option_network_hashes") == hashes.get("option_network_hashes")
             and not changed)
    ref_bills: dict[str, Bill] = {}
    if fresh:
        attributions = [BatteryAttribution.model_validate(a) for a in tornado["attributions"]]
        robustness = Robustness.model_validate(tornado["robustness"])
        ref_bills = {k: Bill.model_validate(v)
                     for k, v in (tornado.get("reference_bills") or {}).items()}
    else:
        attributions, _cases = centre_attributions(ctx)
        note = "tornado_stale" if tornado is not None else "tornado_not_run"
        robustness = Robustness(status="not_established", note=note,
                                pending=[k for k in inp.question.key_drivers
                                         if _skip_code(inp.ledger, inp.tariff, k) is None])
        if tornado is not None:
            notes.append("tornado_stale")
    caveats = {oid: o.caveats for oid, o in ctx.options.items()}
    nets = {oid: o.network for oid, o in ctx.options.items()}
    # Every battery option the run was asked for (BC-S6-1): pending, failed
    # or changed ones have no attribution and count as unjudged.
    expected = [o["option_id"] for o in faux.get("options") or []
                if Q.max_hours(Q.option(inp.question, o["option_id"]))]
    judged_rob = robustness if (fresh or tornado is not None) else None
    # The streams of the option the verdict names — the battery's increment
    # over the PV-only reference for a bess_pv option.
    pre = verdict(attributions, judged_rob, option_networks=nets, caveats=caveats,
                  question=inp.question, fidelity=ctx.fidelity, expected=expected)
    streams_option = pre.option_id or next(
        (a.option_id for a in sorted(_candidates(attributions), key=lambda a: -a.battery_npv)),
        None)
    streams: list[ValueStream] = []
    streams_status = "not_established"
    if streams_option is not None and streams_option in ctx.options:
        opt = ctx.options[streams_option]
        against = ref_bills.get(streams_option)
        if has_pv(streams_option, inp.question) and against is None:
            streams_status = "not_established"
            notes.append("value_streams_need_the_pv_only_reference")
        elif opt.bill is not None and (against or ctx.baseline_bill) is not None:
            streams = value_streams(against or ctx.baseline_bill, opt.bill)
            streams_status = "ok"
            if against is not None:
                notes.append("value_streams_battery_increment_over_pv_only_reference")
    v = verdict(attributions, judged_rob, streams=streams, option_networks=nets,
                caveats=caveats, question=inp.question, fidelity=ctx.fidelity,
                expected=expected)
    if changed and v.status == "ok":
        v = v.model_copy(update={"reasons": tuple(v.reasons) + ("fork_changed_since_run",)})
    explained: list[dict] = []
    for oid, opt in ctx.options.items():
        explained.extend(explain(oid, opt.network))

    options = [OptionResult.model_validate(o) for o in faux.get("options") or []]
    notes += ["tornado_method_redispatch_fixed_sizes", "perfect_foresight_dispatch"]
    if inp.tariff.demand_charge is not None:
        notes.append("demand_charge_perfect_foresight")
    if any(a.status == "skipped" for a in attributions):
        notes.append("size_zero_no_investment")
    if any("bess_pv_value_not_attributable_to_battery" in a.notes for a in attributions):
        notes.append("bess_pv_value_not_attributable_to_battery")
    if any(float(w) != 1.0 for w in ctx.baseline_network.snapshot_weightings["objective"]):
        notes.append("snapshot_weightings_not_unit")
    notes += list(BY_CONSTRUCTION)
    base_hash = study_runner._network_hash(pathlib.Path(base_dir) / "network.nc")
    return Findings(
        options=options, options_status=faux.get("options_status", "not_established"),
        pending_options=faux.get("pending_options") or [],
        hashes=FindingsHashes(ledger_hash=hashes.get("ledger_hash"),
                              base_network_hash=base_hash,
                              option_network_hashes=hashes.get("option_network_hashes") or {}),
        baseline=BaselineResult.model_validate(faux["baseline"]),
        verdict=v, robustness=robustness, explain=explained,
        battery_attribution=attributions, value_streams=streams,
        value_streams_option=streams_option if streams else None,
        value_streams_status=streams_status,
        completeness={"options": faux.get("options_status", "not_established"),
                      "verdict": v.status, "robustness": robustness.status,
                      "value_streams": streams_status,
                      "battery_attribution": ("ok" if expected and all(
                          any(a.option_id == o and a.status in ("ok", "skipped")
                              for a in attributions) for o in expected)
                          else "not_established"),
                      "explain": "ok" if explained else "not_established"},
        honesty_notes=_digit_free(notes))


TORNADO_AUX = "tornado"
