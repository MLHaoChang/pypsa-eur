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


def compute_objective_decomposition(n, cost_breakdown):
    """
    Decompose ``n.objective + n.objective_constant`` into its LP-side
    components. Always returns a dict; every field degrades to ``None`` rather
    than raising, and non-finite floats are nulled for JSON safety.
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
    # Sanity: replace NaN/Inf with None for JSON safety.
    for k, v in list(out.items()):
        if isinstance(v, float) and not _math.isfinite(v):
            out[k] = None
    return out
