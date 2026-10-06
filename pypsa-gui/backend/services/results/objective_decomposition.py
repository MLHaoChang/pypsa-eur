"""
Objective decomposition for `/results/objective_decomposition`.

Lifted from `routers.results` in the Phase 3 follow-up. Phase 2 deferred this
handler because its body CALLED the `get_cost_breakdown` route function and
inspected the result for a 204 `Response`. The router still makes that call —
so the gate, the 204 and the `_state` read all stay there — and passes the
result in as `cost_breakdown`. The `isinstance(cb, dict)` check is unchanged
and covers every shape the router can hand over: a payload dict, a `Response`,
or `None`.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("pypsa_gui.results")

# (component class, nominal attribute) for every capacity-bearing class.
_NOMINAL = (
    ("Generator", "p_nom"), ("StorageUnit", "p_nom"), ("Store", "e_nom"),
    ("Link", "p_nom"), ("Line", "s_nom"), ("Transformer", "s_nom"),
)


def _fixed_cost_by_period(n, cfg) -> dict:
    """
    Per investment period (``None`` on a flat network), the fixed cost the
    LP charged and the fixed cost it never saw, both UNWEIGHTED (one period's
    worth, before any years or objective weighting):

      * ``extendable``     — Σ (capital_cost + fom_cost) × nom_opt over
                             extendable assets active in the period. This is
                             exactly the LP's capacity term (PyPSA charges
                             `periodized_cost` on the full optimised capacity
                             of every active extendable; measured on flat and
                             multi-period networks, see
                             tests/test_fom_reconciliation.py).
      * ``nonextendable``  — the same over NON-extendable active assets.
                             `n.statistics()` counts these, the LP does not:
                             their capacity is fixed, so their cost is a
                             constant the optimiser never carries.

    Computed inside the same periodized-cost fill the solve and every report
    use, so `capital_cost` is annuitised with the configured defaults and
    `fom_cost` is on the per-horizon basis the LP charged.
    """
    import pandas as pd

    from services.solver_service import SolverConfig, with_periodized_cost_defaults

    cfg = cfg if cfg is not None else SolverConfig()
    is_mp = isinstance(n.snapshots, pd.MultiIndex)
    periods = list(n.investment_periods) if is_mp else [None]
    out = {p: {"extendable": 0.0, "nonextendable": 0.0} for p in periods}
    with with_periodized_cost_defaults(n, cfg):
        for cls, nom in _NOMINAL:
            comp = n.c[cls]
            df = comp.static
            if df.empty or f"{nom}_opt" not in df.columns:
                continue
            rate = comp.capital_cost.reindex(df.index).fillna(0.0)
            if "fom_cost" in df.columns:
                rate = rate + df["fom_cost"].fillna(0.0)
            cost = rate * df[f"{nom}_opt"].fillna(df[nom]).fillna(0.0)
            ext_col = f"{nom}_extendable"
            ext = (df[ext_col].fillna(False).astype(bool) if ext_col in df.columns
                   else pd.Series(False, index=df.index))
            for p in periods:
                if p is None:
                    active = pd.Series(True, index=df.index)
                else:
                    active = n.get_active_assets(cls, p).reindex(df.index).fillna(False).astype(bool)
                out[p]["extendable"] += float(cost[ext & active].sum())
                out[p]["nonextendable"] += float(cost[~ext & active].sum())
    return out


def _bridge(n, cost_breakdown, cfg) -> dict:
    """
    Explain `lp_total − cost_breakdown.total` with named terms:

        lp_total = cost_breakdown_total
                   − nonextendable_fixed_cost_eur
                   + period_weighting_adjustment_eur
                   + demand_charge_eur
                   + residual_gap_eur

      * ``nonextendable_fixed_cost_eur`` — fixed cost of assets the LP could
        not size (years-weighted, the reporting basis). The golden fixture's
        non-extendable Line alone is EUR 7.5 bn of it.
      * ``period_weighting_adjustment_eur`` — reporting weights each period
        by `investment_period_weightings.years` (undiscounted money actually
        spent); the LP weights it by `.objective` (1.0 by default, PV × years
        under auto-discount). Applied to what the LP charges: OPEX and the
        extendable fixed cost.
      * ``demand_charge_eur`` — the site demand charge the LP carried
        (decision study S3; the config the solve recorded in `n.meta`, or
        `cfg.demand_charge` for a network solved before that record): Σ price × peak import per
        billing period, recomputed from `links_t.p0` by the bill calculator,
        never read from `n.model`. ``0.0`` when no charge is configured;
        ``None`` (and left in the residual) when the import links carry no
        dispatch.
      * ``residual_gap_eur`` — whatever those do not explain: custom LP terms
        (the curtailment-subsidy wrapper, VOLL slacks, the DSR slack
        generators the `dsr_price_eur_per_mwh` tier adds — price × Σ w·p of
        the shed load; a design solve records it per bus in
        `buses_t["ic_dsr_p"]` — and an objective scale not yet reverted).
        Zero on a plain solve.

    `lp_basis_total` is the reported cost re-expressed on the LP's basis, so
    `residual_gap_eur = lp_total − lp_basis_total`.
    """
    import pandas as pd

    from services.period_utils import period_years_map, years_for_period

    fixed = _fixed_cost_by_period(n, cfg)
    is_mp = isinstance(n.snapshots, pd.MultiIndex)
    years_map = period_years_map(n)
    by_period = {}
    for e in cost_breakdown.get("by_period") or []:
        try:
            by_period[int(e["period"])] = e
        except (TypeError, ValueError, KeyError):
            continue
    lp_basis = 0.0
    nonext_reported = 0.0
    for p, f in fixed.items():
        if p is None:
            years, weight = 1.0, 1.0
            opex_raw = float(cost_breakdown.get("opex", 0.0) or 0.0)
        else:
            years = years_for_period(years_map, p)
            weight = float(n.investment_period_weightings.at[p, "objective"])
            entry = by_period.get(int(p))
            # by_period OPEX is already × years; one period's worth is / years.
            opex_raw = (float(entry["opex"]) / years) if entry and years else 0.0
        lp_basis += weight * (opex_raw + f["extendable"])
        nonext_reported += years * f["nonextendable"]
    cb_total = float(cost_breakdown["total"])
    # The demand charge is refused on multi-period networks, so it is one
    # flat-network term at LP weight 1.0; it is not in `cost_breakdown`.
    # Priced with the charge the SOLVE carried (recorded on the network by
    # `run_simulation`), not `cfg`'s, which may have changed since (gate S3
    # [N4]); `cfg`'s only for a network solved before that record existed.
    from services.study.tariff import (
        demand_charge_eur_from_network,
        solved_demand_charge_config,
    )

    demand_charge = demand_charge_eur_from_network(n, solved_demand_charge_config(
        n, getattr(cfg, "demand_charge", None) if cfg is not None else None))
    return {
        "nonextendable_fixed_cost_eur": nonext_reported,
        "period_weighting_adjustment_eur": lp_basis - (cb_total - nonext_reported),
        "demand_charge_eur": demand_charge,
        "lp_basis_total": lp_basis + (demand_charge or 0.0),
        "is_multi_period": is_mp,
    }


def compute_objective_decomposition(n, cost_breakdown, cfg=None):
    """
    Decompose ``n.objective + n.objective_constant`` into its LP-side
    components and reconcile it with ``cost_breakdown.total``. Always returns
    a dict; every field degrades to ``None`` rather than raising, and
    non-finite floats are nulled for JSON safety.

    ``gap_eur`` / ``gap_pct`` keep their meaning (LP total minus reported
    total). The bridge fields say where that gap comes from — see `_bridge`.
    A plain solve reconciles to a ``residual_gap_eur`` of ~0; anything left
    there is an LP term the reporting surfaces do not model. ``cfg`` is the
    solver config the network was priced under (``None`` → defaults).
    """
    import math as _math
    out: dict = {
        "n_objective": None,
        "n_objective_constant": None,
        "lp_total": None,
        "baseline_objective_constant": None,
        "pypsa_gui_objective_scale": None,
        "cost_breakdown_total": None,
        "gap_eur": None,
        "gap_pct": None,
        # Multi-period myopic mode only: per-period (variable, constant) captured
        # by _run_myopic_foresight. Sum gives the full horizon LP total.
        "myopic_period_objectives": None,
        "myopic_horizon_total": None,
        # Reconciliation bridge: gap_eur = −nonextendable_fixed_cost_eur
        #   + period_weighting_adjustment_eur + demand_charge_eur
        #   + residual_gap_eur.
        "nonextendable_fixed_cost_eur": None,
        "period_weighting_adjustment_eur": None,
        "demand_charge_eur": None,
        "lp_basis_total": None,
        "residual_gap_eur": None,
        "residual_gap_pct": None,
    }
    # Per-period myopic objectives, if present.
    me = getattr(n, "_myopic_period_objectives", None)
    if isinstance(me, list) and me:
        try:
            out["myopic_period_objectives"] = [
                {"period": int(p), "variable": float(v), "constant": float(c), "total": float(v + c)}
                for (p, v, c) in me
            ]
            out["myopic_horizon_total"] = sum(v + c for (_, v, c) in me)
        except Exception:
            pass
    try:
        out["n_objective"] = float(n.objective) if getattr(n, "objective", None) is not None else None
    except Exception:
        pass
    try:
        out["n_objective_constant"] = float(getattr(n, "objective_constant", 0.0) or 0.0)
    except Exception:
        out["n_objective_constant"] = float(getattr(n, "_objective_constant", 0.0) or 0.0)
    try:
        out["baseline_objective_constant"] = float(getattr(n, "_baseline_objective_constant", 0.0) or 0.0)
    except Exception:
        pass
    try:
        out["pypsa_gui_objective_scale"] = float(getattr(n, "_pypsa_gui_objective_scale", 1.0) or 1.0)
    except Exception:
        pass
    if out["n_objective"] is not None and out["n_objective_constant"] is not None:
        out["lp_total"] = out["n_objective"] + out["n_objective_constant"]
    # Try cost_breakdown.total — call the function directly to avoid an HTTP round-trip.
    try:
        cb = cost_breakdown
        if isinstance(cb, dict) and "total" in cb:
            out["cost_breakdown_total"] = float(cb["total"])
            if out["lp_total"] is not None:
                gap = out["lp_total"] - out["cost_breakdown_total"]
                out["gap_eur"] = gap
                if abs(out["cost_breakdown_total"]) > 1e-9:
                    out["gap_pct"] = gap / out["cost_breakdown_total"] * 100.0
    except Exception:
        pass
    # Bridge the gap. Skipped under myopic foresight: `n.objective` is then
    # only the last period's LP (see `myopic_horizon_total`), so there is no
    # single LP total to reconcile against — see services/cost_totals.py.
    if (isinstance(cost_breakdown, dict) and "total" in cost_breakdown
            and out["lp_total"] is not None and not out["myopic_period_objectives"]):
        try:
            b = _bridge(n, cost_breakdown, cfg)
            out["nonextendable_fixed_cost_eur"] = b["nonextendable_fixed_cost_eur"]
            out["period_weighting_adjustment_eur"] = b["period_weighting_adjustment_eur"]
            out["demand_charge_eur"] = b["demand_charge_eur"]
            out["lp_basis_total"] = b["lp_basis_total"]
            residual = out["lp_total"] - b["lp_basis_total"]
            out["residual_gap_eur"] = residual
            if abs(b["lp_basis_total"]) > 1e-9:
                out["residual_gap_pct"] = residual / b["lp_basis_total"] * 100.0
        except Exception:
            logger.exception(
                "objective_decomposition bridge failed; gap_eur is reported "
                "without its breakdown",
            )
    # Sanity: replace NaN/Inf with None for JSON safety.
    for k, v in list(out.items()):
        if isinstance(v, float) and not _math.isfinite(v):
            out[k] = None
    return out
