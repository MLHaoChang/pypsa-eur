"""
The pro forma: one option's investment case (guided investment study MVP-1,
phase S5).

Plan: docs/superpowers/plans/2026-09-28-guided-investment-study-mvp1-v2.md (S5;
review v1 B5, B6, S2, S9, N12; review v2 BC-7; the S1-S4 gate carries).

:func:`build_investment_case` expands ONE solved option into a year-by-year
cash flow on ONE basis — real, pre-tax, without subsidy, in the study's one
currency year, discounted at the ledger's real rate (the rate the LP used) —
and scoped to the option's OWN assets (the battery StorageUnit and, for
``bess_pv``, the PV Generator). No annuity appears anywhere in the cash flow:

* **CAPEX**, year 0: the battery at ``packs.battery_upfront_eur_per_mw(ledger,
  max_hours) x p_nom_opt`` (inverter + hours x storage, from the ledger) —
  never ``upfront_cost_series``, which on this pack back-calculates one
  lifetime from a two-annuity ``capital_cost`` and overstates it (gate S4);
  the gap is disclosed in ``upfront_gaps``. PV at ``upfront_cost_series x
  p_nom_opt`` (its ``overnight_cost``, one lifetime, exact).
* **Replacements**: the inverter share of the battery upfront at every
  inverter lifetime strictly inside the horizon (10 and 20 on the seed).
* **Fixed O&M** per operating year: the sum over the option's assets of
  ``asset_economics.fom_cost_eur`` (annual on a flat year). **Variable
  O&M**: their ``vom_cost_eur``. Never the system ``cost_breakdown.opex``,
  which holds the grid energy bill.
* **Savings**: ``bill_baseline - bill_option``, both from the bill calculator
  on the same (ledger-applied) tariff. The six bill components are the value
  streams and sum to it.
* **Market revenue at duals** (battery ``discharge_revenue_eur -
  charge_cost_eur``, PV ``revenue_eur``) is REPORTED with ``engine=lp_duals``
  and EXCLUDED from ``net_cash_flow``: the bill already prices that energy.
* **Horizon** = the storage block's ledger lifetime (whole years). What lives
  past it is salvage, valued as the present value at the horizon of the
  annuities the LP charged for its remaining life (``salvage_basis =
  annuity_pv``): the PV array, and the inverter bought at the last
  replacement (the year-20 inverter has five of its ten years left at 25).
  With that rule the case's NPV is the LP's objective saving over the
  baseline times the annuity factor — exactly, for every option (PV too).
* **Flat-network path** (N12): a flat year has no ``by_period``; the annual
  values are expanded over ``horizon_years``. A multi-period network is
  refused.

Refused, typed (:class:`ProformaError`): a ledger or tariff with a second
currency year (gate S2 BC-S2-4 carry; MVP-1 converts nothing), a lifetime
that is not whole years (gate S2 nit), a discount rate other than the LP's,
the baseline option. A bill that could not be computed makes the case
``not_established`` — never a zero saving (gate S1 carry).
"""
from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any

import pandas as pd

from models.study import (
    AssumptionsLedger,
    CaseKpis,
    CaseProvenance,
    CaseSources,
    CashFlowYear,
    Fidelity,
    InvestmentCase,
    MarketRevenueAtDuals,
    Tariff,
    UpfrontGap,
    ValueStream,
)
from services.solver.periodized_costs import _annuity
from services.study import packs
from services.study import questions as Q
from services.study import tariff as study_tariff

__all__ = [
    "BILL_COMPONENTS", "EPSILON_MW", "ProformaError", "is_zero_size", "build_investment_case", "irr", "npv",
    "payback",
]

# The bill's six components (`tariff.BillComponents`), each a value stream.
BILL_COMPONENTS: tuple[tuple[str, str], ...] = (
    ("energy", "Energy charges"),
    ("demand", "Demand charge on peak import"),
    ("capacity", "Capacity charge"),
    ("fixed", "Fixed charges"),
    ("network", "Network charges"),
    ("export_credit", "Export credit"),
)
ENGINES = ("cash_flow_expander", "bill_calculator", "lp", "lp_duals", "ledger")
# S6 (gate S5 carry): a size at or below this is "no investment" — judged by
# its size, never by the sign of an NPV that is 0 +- solver noise. The ONE
# threshold; `services/study/findings.py` reads it from here.
EPSILON_MW = 1e-3


def is_zero_size(p_mw: float | None) -> bool:
    """The ONE size rule: at or below ``EPSILON_MW`` is no investment."""
    return p_mw is not None and p_mw <= EPSILON_MW

_OPERATING_FLAGS = {
    "resilience_value": "not_in_mvp1",
    "tax": "pre_tax_basis",
    "depreciation": "pre_tax_basis",
    "debt_service": "no_financing_in_mvp1",
}


class ProformaError(ValueError):
    """The case will not be built. `code` is stable and machine-read."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


# ── KPI arithmetic ────────────────────────────────────────────────────────

def npv(rate: float, cash_flows: list[float]) -> float:
    """Σ CF_t / (1 + r)^t, year 0 undiscounted (Excel: CF0 + NPV(r, CF1:CFn))."""
    return float(sum(cf / (1.0 + rate) ** t for t, cf in enumerate(cash_flows)))


def irr(cash_flows: list[float], lo: float = -0.99, hi: float = 10.0,
        tol: float = 1e-12) -> float | None:
    """
    The rate where the NPV is zero, by bisection on ``[lo, hi]``; None when
    the NPV has one sign at both ends (no sign change, nothing bracketed).
    """
    f_lo, f_hi = npv(lo, cash_flows), npv(hi, cash_flows)
    if f_lo == 0.0:
        return lo
    if f_hi == 0.0:
        return hi
    if (f_lo > 0) == (f_hi > 0):
        return None
    for _ in range(500):
        mid = (lo + hi) / 2.0
        f_mid = npv(mid, cash_flows)
        if (f_mid > 0) == (f_lo > 0):
            lo, f_lo = mid, f_mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return (lo + hi) / 2.0


def payback(cash_flows: list[float]) -> float | None:
    """
    Years to the FIRST crossing of zero by the cumulative cash flow, linear
    inside the crossing year; None when it never crosses.
    """
    cum = 0.0
    for t, cf in enumerate(cash_flows):
        before = cum
        cum += cf
        if t > 0 and before < 0.0 <= cum:
            return (t - 1) + (-before) / cf
    return None


def _annuity_pv_factor(rate: float, years: float) -> float:
    """PV, one year before the first payment, of 1 a year for ``years``."""
    if years <= 0:
        return 0.0
    if rate == 0.0:
        return float(years)
    return (1.0 - (1.0 + rate) ** -years) / rate


# ── inputs ────────────────────────────────────────────────────────────────

def _value(ledger: AssumptionsLedger, key: str) -> float:
    v = packs.ledger_values(ledger).get(key)
    if v is None or not math.isfinite(float(v)):
        raise ProformaError("ledger_row_missing", f"the ledger has no value for {key!r}")
    return float(v)


def _whole_years(ledger: AssumptionsLedger, key: str) -> int:
    """Gate S2 nit: whole years only (Python's round turns 12.5 into 12)."""
    v = _value(ledger, key)
    if v <= 0 or abs(v - round(v)) > 1e-9:
        raise ProformaError(
            "lifetime_not_whole_years",
            f"{key} is {v!r}; the pro forma books whole years, so a lifetime "
            "must be a whole number of years")
    return int(round(v))


def _one_currency_year(ledger: AssumptionsLedger, tariff: Tariff,
                       study_year: int | None) -> int:
    """
    The study's ONE currency year (gate S2 BC-S2-4 carry). MVP-1 converts
    nothing, so a money row, the tariff or the study in another year is
    refused, typed.
    """
    years: dict[int, list[str]] = {}
    for row in ledger.rows:
        if row.currency_year is not None and row.unit.startswith("EUR"):
            years.setdefault(int(row.currency_year), []).append(row.key)
    if tariff.currency != "EUR":
        raise ProformaError("currency_mixed",
                            f"the tariff is in {tariff.currency}; the ledger is in EUR")
    if tariff.currency_year is not None:
        years.setdefault(int(tariff.currency_year), []).append(f"tariff {tariff.tariff_id}")
    if study_year is not None:
        years.setdefault(int(study_year), []).append("study")
    if len(years) > 1:
        detail = "; ".join(f"{y}: {', '.join(sorted(keys)[:4])}" for y, keys in sorted(years.items()))
        raise ProformaError(
            "currency_year_mixed",
            f"the case would mix currency years ({detail}); MVP-1 converts "
            "nothing — re-enter the values in one currency year")
    if not years:
        raise ProformaError("currency_year_unstated",
                            "no money row, tariff or study states a currency year")
    return next(iter(years))


def _bill(value) -> study_tariff.Bill | None:
    if value is None:
        return None
    if isinstance(value, study_tariff.Bill):
        return value
    return study_tariff.Bill.model_validate(value)


def _bill_of(n, tariff: Tariff) -> study_tariff.Bill:
    p0 = getattr(n.links_t, "p0", None)
    imp = p0[packs.IMPORT_LINK] if p0 is not None and packs.IMPORT_LINK in p0 else None
    exp = p0[packs.EXPORT_LINK] if p0 is not None and packs.EXPORT_LINK in p0 else None
    return study_tariff.BillCalculator().bill(imp, exp, tariff, n.snapshot_weightings)


def _live_asset_economics(n, cfg) -> dict | None:
    from services.adequacy.eh_report import _live_result_df
    from services.results.asset_economics import compute_asset_economics

    return compute_asset_economics(n, cfg, result_df=_live_result_df)


def _econ_row(econ: Mapping | None, cls: str, name: str) -> dict | None:
    if not econ:
        return None
    for row in econ.get(cls) or []:
        if row.get("name") == name:
            return row
    return None


def _p_nom_opt(df: pd.DataFrame, name: str) -> float:
    try:
        v = float(df.at[name, "p_nom_opt"])
    except (KeyError, TypeError, ValueError):
        v = float("nan")
    if not math.isfinite(v):
        raise ProformaError("option_not_solved", f"{name} has no p_nom_opt")
    return max(v, 0.0)


def _upfront_series(n, comp_class: str, name: str) -> float | None:
    """
    `upfront_cost_series` per MW for one asset, None when it cannot resolve.

    Read-only on ``n`` (gate S5 BC-S5-1): no periodized-cost fill and revert,
    which would write the network's cost columns. The pack sets everything
    the read needs (PV ``overnight_cost``; the battery's ``lifetime`` and
    ``discount_rate`` for the back-calculation the gap discloses).
    """
    from services.solver.periodized_costs import upfront_cost_series

    try:
        v = float(upfront_cost_series(n, comp_class)[name])
    except (KeyError, ValueError, TypeError):
        return None
    return v if math.isfinite(v) else None


def _pack_notes(n) -> list[str]:
    """The pack's honesty notes as bare codes (``synthetic_load_profile:x`` → code)."""
    meta = (getattr(n, "meta", None) or {}).get(packs.PACK_META_KEY) or {}
    out = []
    for note in meta.get("honesty_notes") or []:
        code = str(note).split(":", 1)[0]
        if code and code.replace("_", "").isalpha() and code.islower():
            out.append(code)
    return out


_CODE_RE = re.compile(r"[a-z]+(_[a-z]+)*")


def _tariff_codes(tariff: Tariff) -> list[str]:
    """
    The tariff's own honesty notes (gate S5 BC-S5-3). The library's seed
    tariffs carry codes; a supplied tariff may carry prose, which is never
    dropped silently: it is named ``tariff_has_uncoded_notes``.
    """
    codes = [n for n in tariff.honesty_notes if _CODE_RE.fullmatch(n)]
    if len(codes) != len(tariff.honesty_notes):
        codes.append("tariff_has_uncoded_notes")
    return codes


def _codes(notes) -> list[str]:
    """Other engines' notes, reduced to digit-free codes."""
    out = []
    for note in notes or ():
        code = str(note).split(":", 1)[0]
        if code.startswith("technology_costs_are_"):
            code = "technology_costs_are_projections"
        if code and code.replace("_", "").isalpha() and code.islower():
            out.append(code)
    return out


# ── the case ──────────────────────────────────────────────────────────────

def build_investment_case(n_option, cfg_option, n_baseline, ledger: AssumptionsLedger,
                          bills: Mapping[str, Any] | None, option_id: str, *,
                          study_id: str, tariff: Tariff,
                          fidelity: Fidelity | str | None = None,
                          asset_economics: Mapping | None = None,
                          study_currency_year: int | None = None,
                          question=Q.BESS_AT_SITE,
                          project_ref: str | None = None,
                          model_hash: str | None = None) -> InvestmentCase:
    """
    One option's investment case against the grid-only baseline (see the
    module docstring for every rule).

    ``bills`` maps ``baseline`` and ``option`` to bill-calculator ``Bill``s
    (or their JSON); a missing one is computed from ``n_baseline`` /
    ``n_option`` on ``tariff``. ``asset_economics`` is the per-class payload
    of ``compute_asset_economics`` (the runner stores it); when None it is
    computed from the option's live frames with ``cfg_option``.
    """
    try:
        opt = Q.option(question, option_id)
    except KeyError as exc:
        raise ProformaError("option_unknown", str(exc)) from None
    if not opt.free_assets:
        raise ProformaError("baseline_has_no_case",
                            f"{option_id!r} is the baseline every case is measured against")
    fidelity = Fidelity(fidelity) if fidelity is not None else None
    currency_year = _one_currency_year(ledger, tariff, study_currency_year)
    if not isinstance(n_option.snapshots, pd.DatetimeIndex):
        raise ProformaError("network_not_one_flat_year",
                            "the pro forma expands ONE flat year; this network is multi-period")
    rate = _value(ledger, "discount_rate")
    lp_rate = getattr(cfg_option, "discount_rate", rate)
    if lp_rate is not None and abs(float(lp_rate) - rate) > 1e-12:
        raise ProformaError(
            "discount_rate_differs_from_lp",
            f"the ledger's rate {rate!r} is not the rate the LP used ({lp_rate!r}); "
            "the case and the LP must share one basis — re-run the study")
    horizon = _whole_years(ledger, "battery_storage_lifetime_years")
    inv_life = _whole_years(ledger, "battery_inverter_lifetime_years")
    replacement_years = tuple(range(inv_life, horizon, inv_life))

    notes: list[str] = ["basis_real_pre_tax_no_subsidy", "currency_year_stated",
                        "single_year_extrapolated", "perfect_foresight_dispatch"]
    if tariff.demand_charge is not None:
        notes += ["demand_charge_perfect_foresight", "duals_include_demand_charge"]
    notes.append("no_degradation")

    # ── bills: the savings (a null bill is not established, never a zero)
    bills = dict(bills or {})
    base_bill = _bill(bills.get("baseline"))
    if base_bill is None and n_baseline is not None:
        base_bill = _bill_of(n_baseline, tariff)
    opt_bill = _bill(bills.get("option"))
    if opt_bill is None:
        opt_bill = _bill_of(n_option, tariff)
    sources = CaseSources(bill_refs=["bill_calculator:none", f"bill_calculator:{option_id}"])
    provenance = CaseProvenance(
        ledger_hash=packs.ledger_hash(ledger), library_version=ledger.ledger_version,
        tariff_id=tariff.tariff_id, project_ref=project_ref, model_hash=model_hash,
        engines=list(ENGINES))
    common = dict(case_id=f"{study_id}.{option_id}", study_id=study_id, option_id=option_id,
                  currency_year=currency_year, fidelity=fidelity, horizon_years=horizon,
                  discount_rate=rate, sources=sources, provenance=provenance)
    missing_bills = [w for w, b in (("baseline", base_bill), ("option", opt_bill))
                     if b is None or b.annual_bill is None]
    if missing_bills:
        return InvestmentCase(
            **common, status="not_established",
            completeness={"cash_flow": "not_established", "kpis": "not_established",
                          "value_streams": "not_established"},
            honesty_notes=tuple(dict.fromkeys(
                notes + [f"bill_unavailable_{w}" for w in missing_bills])))

    # ── the option's own assets, their sizes and their economics
    econ = asset_economics
    if econ is None:
        try:
            econ = _live_asset_economics(n_option, cfg_option)
        except Exception:  # noqa: BLE001 — not established below, never a zero
            econ = None
    has_bat = packs.BATTERY_NAME in n_option.storage_units.index
    has_pv = packs.PV_NAME in n_option.generators.index
    assets = ([("storage_units", packs.BATTERY_NAME)] if has_bat else []) + \
             ([("generators", packs.PV_NAME)] if has_pv else [])
    rows = {name: _econ_row(econ, cls, name) for cls, name in assets}
    absent = sorted(a for a, r in rows.items() if r is None or r.get("fom_cost_eur") is None)
    if absent:
        return InvestmentCase(
            **common, status="not_established",
            completeness={"cash_flow": "not_established", "kpis": "not_established",
                          "value_streams": "not_established"},
            honesty_notes=tuple(dict.fromkeys(notes + ["asset_economics_unavailable"])))
    if any(r.get("by_period") for r in rows.values()):
        raise ProformaError("network_not_one_flat_year",
                            "asset economics carries per-period rows; the pro forma "
                            "expands one flat year")

    capex_by: dict[str, float] = {}
    inverter_replacement = 0.0
    salvage_by: dict[str, float | None] = {}
    gaps: list[UpfrontGap] = []
    p_bat = 0.0
    if has_bat:
        p_bat = _p_nom_opt(n_option.storage_units, packs.BATTERY_NAME)
        hours = float(n_option.storage_units.at[packs.BATTERY_NAME, "max_hours"])
        up = packs.battery_upfront_eur_per_mw(ledger, hours)
        capex_by[packs.BATTERY_NAME] = up["total"] * p_bat
        inverter_replacement = up["inverter"] * p_bat
        # The inverter bought at the last purchase inside the horizon has life
        # left at its end; valued on the annuity the LP charged (one basis).
        last_buy = max(range(0, horizon, inv_life))
        remaining = last_buy + inv_life - horizon
        salvage_by["battery_inverter"] = (inverter_replacement * _annuity(rate, inv_life)
                                          * _annuity_pv_factor(rate, remaining))
        back = _upfront_series(n_option, "StorageUnit", packs.BATTERY_NAME)
        back_total = None if back is None else back * p_bat
        gaps.append(UpfrontGap(
            asset=packs.BATTERY_NAME, ledger_upfront_eur=capex_by[packs.BATTERY_NAME],
            back_calculated_upfront_eur=back_total,
            gap_eur=None if back_total is None else back_total - capex_by[packs.BATTERY_NAME]))
        # U2 WP7 C1: a battery written as its two parts reads the same upfront
        # everywhere (`upfront_cost_series` sums the parts): no gap to disclose.
        same = back_total is not None and abs(back_total - capex_by[packs.BATTERY_NAME]) \
            <= 1e-9 * max(1.0, capex_by[packs.BATTERY_NAME])
        notes += ["inverter_replaced_at_its_lifetime",
                  "battery_upfront_from_two_parts" if same
                  else "battery_upfront_from_ledger_not_back_calculated"]
    if has_pv:
        q_pv = _p_nom_opt(n_option.generators, packs.PV_NAME)
        per_mw = _upfront_series(n_option, "Generator", packs.PV_NAME)
        if per_mw is None:
            raise ProformaError("pv_upfront_unresolved",
                                "the PV upfront cost does not resolve (upfront_cost_series)")
        capex_by[packs.PV_NAME] = per_mw * q_pv
        life = float(n_option.generators.at[packs.PV_NAME, "lifetime"])
        pv_rate = n_option.generators.at[packs.PV_NAME, "discount_rate"]
        pv_rate = rate if pv_rate is None or not math.isfinite(float(pv_rate)) else float(pv_rate)
        if not math.isfinite(life) or life <= 0:
            salvage_by[packs.PV_NAME] = None
        elif life < horizon:
            raise ProformaError("lifetime_shorter_than_horizon",
                                "PV would need a replacement inside the horizon, "
                                "which MVP-1 does not model")
        else:
            salvage_by[packs.PV_NAME] = (capex_by[packs.PV_NAME] * _annuity(pv_rate, life)
                                         * _annuity_pv_factor(rate, life - horizon))

    fom = sum(float(r["fom_cost_eur"]) for r in rows.values())
    vom = sum(float(r.get("vom_cost_eur") or 0.0) for r in rows.values())
    by_asset_market: dict[str, float] = {}
    if has_bat:
        r = rows[packs.BATTERY_NAME]
        by_asset_market[packs.BATTERY_NAME] = (float(r.get("discharge_revenue_eur") or 0.0)
                                               - float(r.get("charge_cost_eur") or 0.0))
    if has_pv:
        by_asset_market[packs.PV_NAME] = float(rows[packs.PV_NAME].get("revenue_eur") or 0.0)
    market = sum(by_asset_market.values())

    salvage_missing = [a for a, v in salvage_by.items() if v is None]
    salvage = None if salvage_missing else float(sum(salvage_by.values()))
    salvage_flag = ("salvage_not_computed:" + ",".join(salvage_missing)) if salvage_missing else None

    savings = float(base_bill.annual_bill) - float(opt_bill.annual_bill)
    capex_total = float(sum(capex_by.values()))

    # ── the years
    years: list[CashFlowYear] = []
    cum = 0.0
    net_flows: list[float] = []
    for t in range(horizon + 1):
        if t == 0:
            row = dict(capex=capex_total, replacements=0.0, opex_fixed=0.0,
                       opex_variable=0.0, bill_baseline=None, bill_option=None,
                       savings=0.0, market_revenue_at_duals=0.0, salvage=0.0)
            flags = {"bill_baseline": "build_year", "bill_option": "build_year"}
        else:
            last = t == horizon
            row = dict(capex=0.0,
                       replacements=inverter_replacement if t in replacement_years else 0.0,
                       opex_fixed=fom, opex_variable=vom,
                       bill_baseline=float(base_bill.annual_bill),
                       bill_option=float(opt_bill.annual_bill), savings=savings,
                       market_revenue_at_duals=market,
                       salvage=(salvage if last else 0.0))
            flags = {"salvage": salvage_flag} if last and salvage_flag else {}
        # Market revenue at duals is NOT in the net cash flow (BC-7).
        net = (row["savings"] + (row["salvage"] or 0.0) - row["capex"]
               - row["replacements"] - row["opex_fixed"] - row["opex_variable"])
        disc = net / (1.0 + rate) ** t
        cum += disc
        net_flows.append(net)
        years.append(CashFlowYear(
            year=t, **row, fuel=0.0, co2_cost=0.0, contract_revenue=0.0,
            resilience_value=None, tax=None, depreciation=None, debt_service=None,
            net_cash_flow=net, discounted_cash_flow=disc, cumulative_discounted=cum,
            unavailable={**flags, **_OPERATING_FLAGS}))

    # ── KPIs
    kpi_flags: dict[str, str] = {"lcoe": "not_in_scope_mvp1", "lcoh": "not_applicable",
                                 "dscr_min": "no_financing_in_mvp1"}
    npv_value = npv(rate, net_flows)
    irr_value = irr(net_flows) if capex_total > 0 else None
    if irr_value is None:
        kpi_flags["irr"] = "irr_undefined"
    disc_flows = [y.discounted_cash_flow for y in years]
    pb = payback(net_flows) if capex_total > 0 else None
    pbd = payback(disc_flows) if capex_total > 0 else None
    for key, v in (("payback_simple", pb), ("payback_discounted", pbd)):
        if v is None:
            kpi_flags[key] = "never_pays_back" if capex_total > 0 else "no_investment"
    lcos = None
    if not has_bat:
        kpi_flags["lcos"] = "not_applicable"
    else:
        discharge = float(rows[packs.BATTERY_NAME].get("discharge_mwh") or 0.0)
        if discharge <= 0:
            kpi_flags["lcos"] = "no_discharge"
        else:
            bat = rows[packs.BATTERY_NAME]
            bat_flows = [capex_by[packs.BATTERY_NAME]] + [
                (inverter_replacement if t in replacement_years else 0.0)
                + float(bat["fom_cost_eur"]) + float(bat.get("vom_cost_eur") or 0.0)
                - (salvage_by["battery_inverter"] if t == horizon else 0.0)
                for t in range(1, horizon + 1)]
            lcos = npv(rate, bat_flows) / (discharge * _annuity_pv_factor(rate, horizon))
            notes.append("lcos_excludes_charging_energy_cost")
    if salvage is None:
        kpi_flags["salvage_eur"] = salvage_flag
    kpis = CaseKpis(
        npv=npv_value, irr=irr_value, payback_simple=pb, payback_discounted=pbd,
        lcoe=None, lcos=lcos, lcoh=None, dscr_min=None, capex_total=capex_total,
        salvage_eur=salvage, unavailable=kpi_flags)

    # ── value streams: the bill's components, summing to the savings
    streams = []
    for key, label in BILL_COMPONENTS:
        delta = (float(getattr(base_bill.by_component, key))
                 - float(getattr(opt_bill.by_component, key)))
        share = delta / savings if savings != 0.0 else None
        streams.append(ValueStream(
            key=key, label=label, annual_value=delta, share=share, engine="bill_calculator",
            unavailable={} if share is not None else {"share": "zero_savings"}))

    # Exact for every option on the annuity salvage basis (gate S5 BC-S5-2):
    # upfront CAPEX, replacements and salvage discount to the annuities the
    # LP charged, so NPV = LP objective saving x AF(r, H) >= 0 at the optimum,
    # and with it IRR >= r and discounted payback <= H whenever there is an
    # investment. Magnitudes inform; the signs are the LP's, not evidence.
    notes.append("npv_nonnegative_at_optimum_by_construction")
    sizes = [p_bat] if has_bat else []
    if has_pv:
        sizes.append(q_pv)
    if all(is_zero_size(q) for q in sizes):
        notes.append("size_zero_no_investment")
    elif capex_total > 0:
        notes.append("irr_and_discounted_payback_bounded_at_optimum_by_construction")
    notes.append("market_revenue_at_duals_excluded_from_cash_flow")
    if salvage is None:
        notes += ["salvage_not_computed", "npv_excludes_uncomputed_salvage"]
    else:
        notes.append("salvage_annuity_pv_remaining_life")
    notes += _tariff_codes(tariff)
    notes += _pack_notes(n_option)
    notes += _codes(base_bill.honesty_notes) + _codes(opt_bill.honesty_notes)
    notes += _codes(ledger.honesty_notes)
    if econ is not None and asset_economics is None:
        sources = sources.model_copy(update={"asset_economics_ref": "live_frames"})
    elif asset_economics is not None:
        sources = sources.model_copy(update={"asset_economics_ref": "run_record"})

    return InvestmentCase(
        **{**common, "sources": sources}, status="ok",
        salvage_basis=None if salvage is None else "annuity_pv",
        years=years, kpis=kpis, value_streams=streams,
        market_revenue_at_duals=MarketRevenueAtDuals(annual_value=market, by_asset=by_asset_market),
        upfront_gaps=gaps,
        completeness={"cash_flow": "ok", "kpis": "ok", "value_streams": "ok",
                      "resilience": "skipped", "tax": "skipped"},
        honesty_notes=tuple(dict.fromkeys(notes)))
