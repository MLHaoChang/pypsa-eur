"""
The single-owner finance engine (IC P4 plan WP4.5; C8–C10, C12, C13).

`run_case(case, pack=None, *, layers=None)` assembles the WPs on a plain
`FinanceCase` (plan C1 — nothing here reads a network):

    timeline → operating cash (total, and the counterfactual's) → incremental
    → debt on the incremental CFADS → incentives → tax (levered, unlevered and
    on the total) → cash series → returns, cover ratios, LCOE, payback,
    lifecycle-cost NPV → the WACC gate; `solve_ppa` re-runs it on a scaled
    contract price.

Cash series (per axis year, index 0 = the financial-close year, SAM's):
- equity pre-tax = EBITDA − capex − replacement − financing fees − debt
  service + draws + reserve interest − DSRA funding + grants (SAM's
  `cf_project_return_pretax`, verified);
- equity post-tax = pre-tax − tax + ITC + PTC (SAM `_aftertax`);
- project (unlevered) = EBITDA − capex − replacement + grants, less the tax
  computed without interest or IDC, plus the credits (C9);
- lifecycle = the equity post-tax cash on the TOTAL (not incremental) cash.

Tax: the depreciable basis = installed capex incl. contingency + IDC − grants
(C6); the ITC reduces the basis of the classes of its assets (`asset:class`
names) on the layers that take it; replacement capex depreciates as a vintage;
the remaining basis is written off in the last year (a book-value terminal
sells at it: no gain). Financing fees: `amortised` over the first tranche's
tenor, `not_deducted`, or — with fees and no choice — not established.

LCOE (SAM's `lcoe_nom`, verified on all oracle cases to 1e-12): (NPV of the
operating revenue − NPV of the post-tax equity cash) / NPV of the degraded
generation, at the cost of equity; real: the energy discounted at the real
rate (1 + r)/(1 + inflation) − 1.

Every metric whose inputs are not established is None, with the section's
reasons (plan C12, ADR-0001).
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import brentq

from services.finance.case import (
    CONTRACT_CLASS, DEGRADATION_SOURCE, FinanceCase, FinanceRefused, TemplateLine,
)
from services.finance.cashflow import Operating, build_operating
from services.finance.debt import Debt, build_debt
from services.finance.incentives import Incentives, build_incentives
from services.finance.metrics import irr, npv
from services.finance.packs.base import JurisdictionPack
from services.finance.tax import DepreciationClass, TaxLayer, TaxResult, compute_tax
from services.finance.tax_layers import OwnerAsset, resolve_tax_layers
from services.finance.timeline import Timeline, build_timeline

GATE_TOL = 1e-6


@dataclass
class FinanceResult:
    case: FinanceCase
    tl: Timeline
    op: Operating                          # the owner's total operating cash
    op_incremental: dict[str, np.ndarray | None]
    debt: Debt
    incentives: Incentives
    tax: TaxResult | None
    tax_unlevered: TaxResult | None
    cash: dict[str, np.ndarray | None]
    metrics: dict[str, float | None]
    gate: dict
    sections: dict[str, str] = field(default_factory=dict)
    reasons: dict[str, list[str]] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)
    # The terminal value the cash series used (book value / incremental EBITDA
    # multiple resolved) and the counterfactual's operating cash — the report's
    # cashflow lines reconcile to the cash series through them (WP4.6b review B2).
    terminal: np.ndarray | None = None
    op_counterfactual: Operating | None = None


# ── metrics ──────────────────────────────────────────────────────────────────

def payback(cash: np.ndarray | None) -> float | None:
    """Years from index 0 (the financial close) until the cumulative cash
    first crosses from negative to ≥ 0, linearly interpolated inside the year
    (stated); 0.0 when it is never negative; None when it never recovers
    (WP4.5 review B6: a zero start is not a payback)."""
    if cash is None:
        return None
    cum = np.cumsum(cash)
    if (cum >= 0.0).all():
        return 0.0
    for i in range(1, len(cum)):
        if cum[i - 1] < 0.0 <= cum[i]:
            return float(i - 1 + (-cum[i - 1]) / cash[i])
    return None


def payback_sustained(cash: np.ndarray | None) -> bool | None:
    """Whether the cumulative cash stays ≥ 0 after the payback year."""
    pb = payback(cash)
    if pb is None:
        return None
    cum = np.cumsum(cash)
    return bool((cum[int(np.ceil(pb)):] >= -1e-9).all())


def _cover_ratio(cfads: np.ndarray | None, rates: list[float], start: int, years: int,
                 debt: float) -> float | None:
    if cfads is None or debt <= 0:
        return None
    pv, disc = 0.0, 1.0
    for m in range(years):
        i = start + m
        if i >= len(cfads):
            break
        disc /= 1.0 + rates[min(m, len(rates) - 1)]
        pv += cfads[i] * disc
    return float(pv / debt)


def _nan_stats(a: np.ndarray) -> tuple[float | None, float | None]:
    v = a[~np.isnan(a)]
    return (float(v.min()), float(v.mean())) if len(v) else (None, None)


# ── assembly ─────────────────────────────────────────────────────────────────

def _with_itc_classes(layers: tuple[TaxLayer, ...], itc_assets: tuple[str, ...]) -> tuple:
    """Classes named `asset:class` take the ITC reduction only for the ITC's
    assets (WP4.4); SAM's class-only names keep their own flags."""
    out = []
    for layer in layers:
        classes = tuple(
            dataclasses.replace(c, itc_reduces=c.name.split(":", 1)[0] in itc_assets)
            if ":" in c.name else c for c in layer.depreciation)
        out.append(dataclasses.replace(layer, depreciation=classes))
    return tuple(out)


def _net(op: Operating) -> np.ndarray | None:
    if op.revenue is None or op.costs is None:
        return None
    return op.revenue - op.costs


def run_case(case: FinanceCase, pack: JurisdictionPack | None = None, *,
             layers: tuple[TaxLayer, ...] | None = None, _solve: bool = True) -> FinanceResult:
    fin = case.inputs
    tl = build_timeline(case)
    reasons: dict[str, list[str]] = {}
    flags: list[str] = list(case.flags)
    op = build_operating(case, tl)
    flags += op.flags
    n = tl.n
    last = n - 1

    # Counterfactual (C13): incremental operating cash = total − counterfactual.
    cf_net = np.zeros(n)
    cf_op = None
    if case.counterfactual:
        cf_case = dataclasses.replace(case, templates=case.counterfactual, counterfactual=())
        cf_op = build_operating(cf_case, tl)
        cf_net = _net(cf_op)
        if cf_net is None:
            reasons["counterfactual"] = cf_op.reasons["operating"]
    net_total = _net(op)
    inc_net = None if (net_total is None or cf_net is None) else net_total - cf_net
    op_inc = dataclasses.replace(
        op, revenue=None if inc_net is None else np.clip(inc_net, 0, None),
        costs=None if inc_net is None else np.clip(-inc_net, 0, None))

    debt = build_debt(fin, op_inc, tl)
    flags += debt.flags
    if not debt.established():
        reasons["debt"] = debt.reasons
    inc = build_incentives(case, tl, op, pack)
    flags += inc.flags
    if not inc.established():
        reasons["incentives"] = inc.reasons

    # ── tax ──
    tax = tax_u = tax_total = None
    tax_reasons: list[str] = []
    if fin.tax_losses is None:
        tax_reasons.append("input_missing:tax_losses")
    capex_total = None if op.capex is None else float(op.capex.sum())
    fees_total = float(debt.fees.sum())
    fee_deduction = np.zeros(n)
    if fees_total > 0:
        if fin.financing_fee_tax is None:
            tax_reasons.append("input_missing:financing_fee_tax")
        elif fin.financing_fee_tax == "amortised" and debt.tranches:
            c = tl.index(tl.cod_year)
            ten = debt.tranches[0].tranche.tenor_years
            fee_deduction[c:c + ten] = fees_total / ten
    if layers is None:
        if pack is None:
            tax_reasons.append("tax_pack_missing")
        elif capex_total is not None:
            idc = debt.idc_total
            owner_assets = []
            for a in case.assets:
                if a.overnight_cost is None:
                    continue
                share = a.overnight_cost * (1.0 + (fin.contingency_share or 0.0))
                owner_assets.append(OwnerAsset(
                    a.name, a.carrier or "", share + idc * share / capex_total if capex_total else 0.0))
            res = resolve_tax_layers(pack, fin, owner_assets, case.cod)
            flags += res.flags
            if res.missing:
                tax_reasons += [f"tax_input_missing:{m}" for m in res.missing]
            layers = res.layers
    if capex_total is None:
        tax_reasons.append("capex_not_established")
    if inc_net is None or op.terminal is None and fin.terminal_value.method != "book_value":
        tax_reasons.append("operating_not_established")
    if not debt.established():
        tax_reasons.append("debt_not_established")
    if not inc.established():
        tax_reasons.append("incentives_not_established")
    terminal = op.terminal
    if case.counterfactual and fin.terminal_value.method == "multiple_of_ebitda" and \
            inc_net is not None and fin.terminal_value.value is not None:
        # The investment's own EBITDA, not the owner's total with the supply
        # bill (WP4.5 review B1).
        terminal = np.zeros(n)
        terminal[last] = fin.terminal_value.value * float(inc_net[last])
    if not tax_reasons and layers:
        layers = _with_itc_classes(tuple(layers), inc.itc_assets)
        basis = capex_total + debt.idc_total - inc.grant_basis_reduction
        basis_u = capex_total - inc.grant_basis_reduction
        if inc.grant_basis_reduction:
            flags.append("grant_reduces_basis_pro_rata")
        vintages = _replacement_vintages(fin, tl)
        if fin.terminal_value.method == "book_value":
            try:
                probe = compute_tax(tl, layers, ebitda=np.zeros(n), basis=basis,
                                    itc_amount=inc.itc_amount, losses="offset_other_income",
                                    vintages=vintages)
                book = probe.remaining_basis[layers[-1].name]
            except ValueError:
                book = 0.0                    # the tax runs below report the invalid basis
            terminal = np.zeros(n)
            terminal[last] = book
            flags.append(f"terminal_book_value:{layers[-1].name}")
        ebitda_inc = inc_net + terminal
        common = dict(itc_amount=inc.itc_amount, losses=fin.tax_losses, vintages=vintages,
                      write_off_remaining=True)
        grant_inc = inc.grant_taxable if inc.grant_taxable is not None else np.zeros(n)
        try:
            tax = compute_tax(tl, layers, ebitda=ebitda_inc, basis=basis, interest=debt.interest,
                              other_income=debt.reserve_interest + grant_inc,
                              other_deductions=fee_deduction, **common)
            tax_u = compute_tax(tl, layers, ebitda=ebitda_inc, basis=basis_u, other_income=grant_inc,
                                **common)
            tax_total = compute_tax(tl, layers, ebitda=ebitda_inc + cf_net, basis=basis,
                                    interest=debt.interest,
                                    other_income=debt.reserve_interest + grant_inc,
                                    other_deductions=fee_deduction, **common)
            flags += tax.flags
            if inc.itc_amount and debt.idc_total > 0:
                flags.append("itc_base_excludes_idc")
            if inc.itc_amount and any(not ly.itc_basis_reduction for ly in layers) and \
                    any(ly.itc_basis_reduction for ly in layers):
                flags.append("state_itc_basis_unreduced")
        except ValueError as e:
            # An impossible basis (e.g. reductions above a class's basis) is a
            # reason, never an exception (WP4.4 review r3-2).
            tax = tax_u = tax_total = None
            tax_reasons.append(f"tax_basis_invalid:{e}")
    elif not tax_reasons:
        tax_reasons.append("tax_layers_missing")
    if tax_reasons:
        reasons["tax"] = sorted(set(tax_reasons))

    # ── cash ──
    cash: dict[str, np.ndarray | None] = dict.fromkeys(
        ("ebitda", "equity_pre_tax", "equity_post_tax", "project_pre_tax", "project_post_tax",
         "lifecycle_post_tax", "cfads", "tax", "credits"))
    if inc_net is not None and terminal is not None and op.capex is not None and \
            inc.established():                   # a grant unresolved → no headline (review B4)
        ebitda = inc_net + terminal
        cash["ebitda"] = ebitda
        base = ebitda - op.capex - op.replacement + inc.grant
        cash["project_pre_tax"] = base
        cash["cfads"] = debt.cfads
        if debt.established():
            cash["equity_pre_tax"] = (base - debt.fees - debt.service + debt.draws
                                      + debt.reserve_interest - debt.dsra_funding)
        credits = inc.itc + inc.ptc
        cash["credits"] = credits
        if tax is not None and cash["equity_pre_tax"] is not None:
            cash["tax"] = tax.total_liability
            cash["equity_post_tax"] = cash["equity_pre_tax"] - tax.total_liability + credits
            cash["lifecycle_post_tax"] = (cash["equity_pre_tax"] + cf_net
                                          - tax_total.total_liability + credits)
        if tax_u is not None:
            cash["project_post_tax"] = base - tax_u.total_liability + credits

    # ── metrics ──
    m: dict[str, float | None] = {}
    flags_m: list[str] = []

    def rate_irr(key, series, rate, rate_name):
        s = cash[series]
        if s is None:
            m[f"{key}_irr"] = m[f"{key}_npv"] = None
            return
        v, f = irr(s)
        m[f"{key}_irr"] = v
        flags_m.extend(f"{key}:{x}" for x in f)
        m[f"{key}_npv"] = npv(rate, s)
        if rate is None:
            flags_m.append(f"{key}_npv_not_established:{rate_name}_missing")

    rate_irr("project_post_tax", "project_post_tax", fin.wacc_nominal, "wacc_nominal")
    rate_irr("project_pre_tax", "project_pre_tax", fin.wacc_nominal, "wacc_nominal")
    rate_irr("equity_post_tax", "equity_post_tax", fin.cost_of_equity, "cost_of_equity")
    rate_irr("equity_pre_tax", "equity_pre_tax", fin.cost_of_equity, "cost_of_equity")
    m["lifecycle_npv"] = (npv(fin.cost_of_equity, cash["lifecycle_post_tax"])
                          if cash["lifecycle_post_tax"] is not None else None)
    m["payback_years"] = payback(cash["equity_post_tax"])
    if payback_sustained(cash["equity_post_tax"]) is False:
        flags_m.append("payback_not_sustained")
    m["min_dscr"], m["avg_dscr"] = _nan_stats(debt.dscr)
    m["min_dscr_senior"], m["avg_dscr_senior"] = _nan_stats(debt.dscr_senior)
    m["debt_size"] = debt.amount if debt.established() else None
    llcr = plcr = None
    if debt.established() and debt.tranches and debt.cfads is not None:
        c = tl.index(tl.cod_year)
        rates = [r for r in _tranche_rates(debt)]
        if len(debt.tranches) > 1:
            flags_m.append("cover_ratios_at_debt_weighted_rate")
        tenor = max(t.tranche.tenor_years for t in debt.tranches)
        llcr = _cover_ratio(debt.cfads, rates, c, tenor, debt.amount)
        plcr = _cover_ratio(debt.cfads, rates, c, tl.analysis_years, debt.amount)
    m["llcr"], m["plcr"] = llcr, plcr
    # LCOE (SAM's definition, generalised — WP4.5 review B2): (PV of the
    # investment's value gross of its own operating costs − PV of the post-tax
    # equity cash) / PV of its generation, i.e. PV of everything the energy
    # costs. The value = incremental operating cash + the asset costs (fom,
    # costs — every actual line with no counterfactual counterpart); without
    # a counterfactual it is SAM's revenue exactly.
    energy = [e for e in op.energy_mwh.values()]
    lcoe = lcoe_r = None
    if energy and all(e is not None for e in energy) and inc_net is not None and \
            cash["equity_post_tax"] is not None and fin.cost_of_equity is not None:
        e_tot = np.sum(energy, axis=0)
        value = inc_net + _asset_costs(op, case)
        r = fin.cost_of_equity
        k = np.arange(n)
        d = (1.0 + r) ** k
        num = float(np.sum(value / d) - np.sum(cash["equity_post_tax"] / d))
        de = float(np.sum(e_tot / d))
        lcoe = num / de if de > 0 else None
        if de > 0:
            if fin.inflation is None:
                flags_m.append("lcoe_real_not_established:inflation_missing")
            else:
                rr = (1.0 + r) / (1.0 + fin.inflation) - 1.0
                lcoe_r = num / float(np.sum(e_tot / (1.0 + rr) ** k))
        if case.counterfactual:
            flags_m.append("lcoe_on_incremental_value")
    m["lcoe_nominal_per_mwh"], m["lcoe_real_per_mwh"] = lcoe, lcoe_r
    flags += flags_m

    gate = wacc_gate(case)
    result = FinanceResult(case=case, tl=tl, op=op, op_incremental={"net": inc_net, "counterfactual": cf_net},
                           debt=debt, incentives=inc, tax=tax, tax_unlevered=tax_u, cash=cash,
                           metrics=m, gate=gate, reasons=reasons, flags=sorted(set(flags)),
                           terminal=terminal, op_counterfactual=cf_op)
    sections = {"operating": "ok" if inc_net is not None and terminal is not None else "not_established",
                "debt": "ok" if debt.established() else "not_established",
                "incentives": "ok" if inc.established() else "not_established",
                "tax": "ok" if tax is not None else "not_established"}
    result.sections = sections
    if _solve and fin.solve_ppa is not None:
        sol = solve_ppa(case, pack, layers=layers)
        result.flags = sorted(set(result.flags) | set(sol.pop("solve_ppa_flags", [])))
        m.update(sol)
    return result


def _asset_costs(op: Operating, case: FinanceCase) -> np.ndarray:
    """The investment's own costs per year (> 0): the cost part of every
    actual line with no counterpart in the counterfactual (the shared bill,
    commodity and connection keys stay netted as savings) — WP4.5 review r2.
    The adapter's C5 bill-effect pair (`DEGRADATION_SOURCE`) is avoided-bill
    value, not a cost of the investment: skipped (WP4.6a review B1). Without a
    counterfactual, net + these = the operating revenue exactly."""
    shared = {ln.key for t in case.counterfactual for ln in t.lines}
    out = np.zeros(op.tl.n)
    for key, arr in op.lines.items():
        meta = op.line_meta.get(key)
        if meta is not None and meta.source == DEGRADATION_SOURCE:
            continue
        if arr is not None and key not in shared:
            out += np.clip(-arr, 0.0, None)
    return out


def _replacement_vintages(fin, tl: Timeline) -> tuple:
    """(axis index, escalated amount, asset) per replacement (plan C6)."""
    r = fin.escalation.get("capex") or 0.0
    return tuple((tl.index(year), amount * (1.0 + r) ** (year - tl.base_year), asset)
                 for year, asset, amount in fin.replacement_capex
                 if tl.cod_year <= year < tl.cod_year + tl.analysis_years)


def _tranche_rates(debt: Debt) -> list[float]:
    """Per-year rates for cover ratios: the single tranche's, or the
    debt-weighted average per year (stated)."""
    ts = debt.tranches
    tenor = max(t.tranche.tenor_years for t in ts)
    out = []
    for m in range(tenor):
        num = den = 0.0
        for t in ts:
            rates = t.tranche.rate if isinstance(t.tranche.rate, list) else [t.tranche.rate]
            r = rates[min(m, len(rates) - 1)]
            num += r * t.amount
            den += t.amount
        out.append(num / den if den else 0.0)
    return out


# ── the WACC gate (C10) ──────────────────────────────────────────────────────

def wacc_gate(case: FinanceCase) -> dict:
    """`wacc_vs_discount_rate_consistent` per plan C10, with each leg's state
    (`ok` / `differs` / `n/a` / None when a needed value is unknown) and the
    bases the LP used; never feeds back into the LP."""
    fin, lp = case.inputs, case.lp_basis
    legs: dict[str, str | None] = {}
    if lp is None or lp.discount_rate is None or fin.wacc_nominal is None:
        legs["discount_rate"] = None
    else:
        legs["discount_rate"] = "ok" if abs(fin.wacc_nominal - lp.discount_rate) <= GATE_TOL else "differs"
    differing = []
    if lp is not None and fin.wacc_nominal is not None:
        for a, r in sorted(lp.asset_discount_rates.items()):
            if r is not None and abs(r - fin.wacc_nominal) > GATE_TOL:
                differing.append(a)
        legs["asset_rates"] = "differs" if differing else "ok"
    else:
        legs["asset_rates"] = None
    if lp is not None and lp.auto_discount_periods:
        if fin.inflation is None or lp.inflation_rate is None:
            legs["inflation"] = None
        else:
            legs["inflation"] = "ok" if abs(fin.inflation - lp.inflation_rate) <= GATE_TOL else "differs"
    else:
        legs["inflation"] = "n/a"
    states = [v for v in legs.values() if v != "n/a"]
    consistent = None if any(v is None for v in states) else all(v == "ok" for v in states)
    return {
        "wacc_vs_discount_rate_consistent": consistent,
        "legs": legs,
        "assets_with_other_rates": differing,
        "values": {"wacc_nominal": fin.wacc_nominal,
                   "lp_discount_rate": None if lp is None else lp.discount_rate,
                   "inflation": fin.inflation,
                   "lp_inflation_rate": None if lp is None else lp.inflation_rate,
                   "asset_discount_rates": {} if lp is None else dict(lp.asset_discount_rates)},
        "annuity_basis": "the LP annualised capex at its nominal discount rate on real costs",
        "pv_basis": ("real (Fisher), auto_discount_periods on" if lp is not None and
                     lp.auto_discount_periods else "not used (auto_discount_periods off)"),
    }


# ── solve-for-PPA (C9) ───────────────────────────────────────────────────────

def _scaled(case: FinanceCase, keys: set[str], s: float) -> FinanceCase:
    def scale(ln: TemplateLine) -> TemplateLine:
        if ln.key in keys and ln.amount is not None:
            return dataclasses.replace(ln, amount=ln.amount * s,
                                       price=None if ln.price is None else ln.price * s)
        return ln
    temps = tuple(dataclasses.replace(t, lines=tuple(scale(ln) for ln in t.lines))
                  for t in case.templates)
    return dataclasses.replace(case, templates=temps)


def solve_ppa(case: FinanceCase, pack: JurisdictionPack | None = None, *,
              layers=None) -> dict[str, float | str | None]:
    """The price of an owner-sold PPA for the target after-tax equity IRR in
    the target operating year (SAM `ppa_soln_mode=0`): `brentq` on the price
    scale of the contract's lines; the stored inputs are never mutated."""
    sp = case.inputs.solve_ppa
    out: dict[str, float | str | None] = {"solved_ppa_price": None, "solve_ppa_status": None}
    lines = [ln for t in case.templates for ln in t.lines if ln.contract_id is not None or
             ln.stream == "ppa_settlement"]
    if sp.contract_id is not None:
        lines = [ln for ln in lines if ln.contract_id == sp.contract_id]
    else:
        lines = [ln for ln in lines if ln.stream == "ppa_settlement"]
        if len({ln.contract_id for ln in lines}) > 1:
            out["solve_ppa_status"] = "solve_ppa_ambiguous_contract"
            return out
    if not lines:
        out["solve_ppa_status"] = "solve_ppa_contract_not_found"
        return out
    lines = [ln for ln in lines if ln.amount != 0.0]           # a zero line is inert
    if not lines:
        out["solve_ppa_status"] = "solve_ppa_contract_not_found"
        return out
    for ln in lines:
        if ln.stream != "ppa_settlement" or ln.esc_class != CONTRACT_CLASS:
            out["solve_ppa_status"] = "solve_ppa_not_linear"
            return out
        if ln.amount is None or ln.amount <= 0:
            out["solve_ppa_status"] = "solve_ppa_not_owner_sold"
            return out
        if ln.changes_dispatch:
            out["solve_ppa_status"] = "solve_ppa_needs_redispatch"
            return out
        if ln.price is None:
            out["solve_ppa_status"] = "solve_ppa_price_unknown"
            return out
    keys = {ln.key for ln in lines}
    # The reported price is the earliest template's (its money year stated).
    first_t = min(case.templates, key=lambda t: t.first_year)
    ref = [ln for ln in first_t.lines if ln.key in keys and ln.price is not None]
    price0 = (ref or lines)[0].price
    out["solved_ppa_price_money_year"] = first_t.money_year or case.base_year
    tl = build_timeline(case)
    end = tl.index(tl.cod_year) + sp.target_year      # exclusive: operating years 1 … target
    if sp.target_year > tl.analysis_years:
        out["solve_ppa_flags"] = ["solve_ppa_target_year_clamped"]
        end = tl.n

    def g(s: float) -> float | None:
        """NPV at the target of the truncated equity cash, or None when the
        case is not established at that price (e.g. sculpted debt above the
        uses — WP4.5 review B3)."""
        try:
            r = run_case(_scaled(case, keys, s), pack, layers=layers, _solve=False)
        except FinanceRefused:
            return None
        eq = r.cash["equity_post_tax"]
        return None if eq is None else npv(sp.target_irr, eq[:end])

    lo, f_lo = 0.0, g(0.0)
    if f_lo is None:
        out["solve_ppa_status"] = "solve_ppa_cash_not_established"
        return out
    if f_lo > 0:
        out["solve_ppa_status"] = "solve_ppa_no_root:target_met_at_zero_price"
        return out
    hi, f_hi = 1.0, g(1.0)
    while f_hi is not None and f_hi < 0 and hi < 1e4:
        lo, f_lo = hi, f_hi
        hi *= 1.5
        f_hi = g(hi)
    if f_hi is None:
        # Shrink back towards the last established point until established.
        top = hi
        for _ in range(60):
            mid = 0.5 * (lo + top)
            v = g(mid)
            if v is None:
                top = mid
            elif v < 0:
                lo, f_lo = mid, v
            else:
                hi, f_hi = mid, v
                break
            if top - lo < 1e-9 * max(1.0, top):
                break
        if f_hi is None:
            out["solve_ppa_status"] = "solve_ppa_no_root:cash_not_established_above"
            return out
    if f_hi < 0:
        out["solve_ppa_status"] = "solve_ppa_no_root"
        return out
    s = brentq(lambda x: g(x), lo, hi, xtol=1e-12, rtol=1e-12, maxiter=200)
    out["solved_ppa_price"] = price0 * s
    out["solve_ppa_status"] = "ok"
    solved = run_case(_scaled(case, keys, s), pack, layers=layers, _solve=False)
    v, _ = irr(solved.cash["equity_post_tax"][:end])
    out["solved_equity_irr_at_target_year"] = v
    return out


__all__ = ["FinanceResult", "run_case", "solve_ppa", "wacc_gate", "payback", "payback_sustained",
           "DepreciationClass"]
