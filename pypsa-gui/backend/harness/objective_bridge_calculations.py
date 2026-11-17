"""Checked objective-to-cost bridge (`objective-bridge/1`; assistant-assurance 10, S3).

Reconciles the solver objective with the reported total system cost through
`services.results.objective_decomposition.objective_bridge`. The objective is
read only after the post-solve rescale has divided the objective scale back
out. A term the run cannot identify (a slack penalty under an ordinary carrier,
an objective still scaled) is unavailable, never 0. Under myopic foresight or a
rolling horizon the objective covers only the last period or window, so no
row is answered. GK B condition G3 is in issue 10's Comments.
"""

import math

from harness import profile_common as common
from services.results.objective_decomposition import residual_tolerance

PROFILE = "objective-bridge/1"
REFUSALS = frozenset({"network_scope_required", "ac_pf_present", "unsupported_snapshot_scope"})
# Bridge order (UX 25): the objective, the reported total and its penalty share,
# the terms between them, the residual last.
METRICS = {
    "objective_eur": {
        "formula_id": "solver-objective-unscaled/1",
        "unit": "EUR",
        "label": "Solver objective",
    },
    "reported_total_cost_eur": {
        "formula_id": "cost-breakdown-total/1",
        "unit": "EUR",
        "label": "Total system cost",
    },
    "penalty_cost_eur": {
        "formula_id": "slack-penalty-in-total/1",
        "unit": "EUR",
        "label": "Load-shedding and slack penalties within the total",
    },
    "nonextendable_fixed_cost_eur": {
        "formula_id": "nonextendable-fixed-cost/1",
        "unit": "EUR",
        "label": "Fixed cost of assets the solve could not size (not in the objective)",
    },
    "period_weighting_adjustment_eur": {
        "formula_id": "period-weighting-adjustment/1",
        "unit": "EUR",
        "label": "Period-weighting adjustment",
    },
    "reserve_revenue_eur": {
        "formula_id": "reserve-revenue-in-objective/1",
        "unit": "EUR",
        "label": "Reserve revenue (subtracted in the objective)",
    },
    "residual_eur": {
        "formula_id": "objective-bridge-residual/1",
        "unit": "EUR",
        "label": "Unexplained residual",
    },
}
# The bridge field each metric reads.
_FIELDS = {
    "objective_eur": "objective_eur",
    "reported_total_cost_eur": "reported_total_eur",
    "penalty_cost_eur": "penalty_eur",
    "nonextendable_fixed_cost_eur": "nonextendable_fixed_cost_eur",
    "period_weighting_adjustment_eur": "period_weighting_adjustment_eur",
    "reserve_revenue_eur": "reserve_revenue_eur",
    "residual_eur": "residual_eur",
}
# Why a row is unavailable when the bridge could not compute it.
_MISSING = {
    "objective_eur": "objective_unavailable",
    "reported_total_cost_eur": "reported_total_unavailable",
    "penalty_cost_eur": "penalty_not_identifiable",
    "nonextendable_fixed_cost_eur": "bridge_unavailable",
    "period_weighting_adjustment_eur": "bridge_unavailable",
    "reserve_revenue_eur": "bridge_unavailable",
    "residual_eur": "bridge_unavailable",
}
DISCLOSURE = (
    "The solver objective includes penalty and slack terms and period weightings, "
    "so it is not the total system cost."
)


def source(ctx, entity):
    """Called under the network lock; the bridge is a whole-run, network-only reading."""
    from harness.calculations import _number
    from services.results.cost_breakdown import compute_cost_breakdown
    from services.results.objective_decomposition import objective_bridge
    from services.solver_service import SolverConfig

    if entity != "network":
        raise ValueError("network_scope_required")
    state = ctx.solver_state
    if state.get("ac_pf_results") is not None or state.get("lopf_results") is not None:
        raise ValueError("ac_pf_present")
    n = ctx.network
    index = common.single_period_index(n)
    cfg = state.get("solver_config") or SolverConfig()
    try:
        cost_breakdown = compute_cost_breakdown(n, cfg)
    except Exception:  # the bridge then reads every cost-side row as unavailable
        cost_breakdown = None
    bridge = objective_bridge(n, cost_breakdown, cfg)
    return {
        "version": 1,
        "profile": PROFILE,
        "entity_id": entity,
        "index": [v.isoformat() for v in index],
        "scope": bridge["scope"],
        "objective_scale": _number(bridge["objective_scale"]),
        "bridge": {key: _number(bridge[field]) for key, field in _FIELDS.items()},
        "valid_solve": common.lp_outputs_live(ctx),
    }


def _value(raw, metric):
    v = raw["bridge"].get(metric)
    return v if v is not None and math.isfinite(v) else None


def calculate(raw, scope):
    """Every row reads the whole run; a partial window is refused, not pro-rated."""
    if (
        raw.get("version") != 1
        or raw.get("profile") != scope["profile"]
        or raw["entity_id"] != scope["entity_id"]
    ):
        raise ValueError("objective_bridge_source_identity")
    try:
        a, b = common.window(raw["index"], scope)
    except ValueError as exc:
        if str(exc) == "reversed_window":
            raise
        raise ValueError("unsupported_snapshot_scope") from None
    if (a, b) != (0, len(raw["index"]) - 1):
        raise ValueError("unsupported_snapshot_scope")
    available, reasons = {}, {}
    if not raw["valid_solve"]:
        reasons = dict.fromkeys(METRICS, "unverified_solve")
    elif raw["scope"] != "horizon":
        reasons = dict.fromkeys(METRICS, "objective_scope_not_horizon")
    else:
        for metric in METRICS:
            value = _value(raw, metric)
            if value is None:
                reasons[metric] = _MISSING[metric]
            else:
                available[metric] = value
    return common.facts_and_slots(scope, METRICS, available, reasons, b - a + 1)


def reconciled(objective, residual):
    """True when the residual is within the stated tolerance of the objective."""
    return abs(residual) <= residual_tolerance(objective)


def notes(raw, scope):
    """The match state in plain words, stated once, or why nothing is answered."""
    if not raw["valid_solve"]:
        return []
    if raw["scope"] != "horizon":
        part = "window" if raw["scope"] == "last_window" else "period"
        return [f"Not available: the objective covers only the last {part}."]
    lines = []
    objective, residual = _value(raw, "objective_eur"), _value(raw, "residual_eur")
    if objective is not None and residual is not None:
        tolerance = residual_tolerance(objective)
        lines.append(
            f"Matches within {tolerance:.6g} EUR."
            if reconciled(objective, residual)
            else f"Does not match: the residual is {residual:.6g} EUR, "
            f"above the {tolerance:.6g} EUR tolerance."
        )
    elif raw["objective_scale"] not in (None, 1.0):
        lines.append("Not available: the objective is still scaled for the solver.")
    if _value(raw, "penalty_cost_eur") is None:
        lines.append(
            "Not available: a slack generator has an ordinary carrier, so its penalty "
            "cannot be separated from the total."
        )
    return lines
