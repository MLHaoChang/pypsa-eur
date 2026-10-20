"""Bounded LOPF branch loading for checked answers (checked-results S3).

Matches `services/loadflow_summary.py` on the LOPF source: flow is the larger
terminal active power, max(|p0|, |p1|); the limit is rating·s_max_pu(t) with a
time-varying s_max_pu (a missing time-varying value falls back to the static
one, as the tab's `_smax_matrix`); overload means u > 1 + 1e-6. Unlike the tab,
a missing p0 or p1 at any snapshot, or a zero limit, makes the branch
unavailable rather than partial or bus0-only. An AC power-flow result in the
solve state refuses the capture, since live flows may then be PF values, and
both the recorded and the exact input fingerprints must match the solve.
"""

import math

import pandas as pd

PROFILE = "branch-loading/1"
NETWORK_PROFILE = "network-loading/1"
PROFILES = (PROFILE, NETWORK_PROFILE)
MAX_BRANCHES = 64
OVER_LIMIT_TOLERANCE = 1e-6
BRANCH_CLASSES = ("Line", "Transformer")
REFUSALS = frozenset({
    "ambiguous_branch", "unknown_branch", "unsupported_branch_population",
    "ac_pf_present", "network_scope_required",
})
METRICS = {
    "peak_branch_utilisation": {
        "formula_id": "branch-peak-utilisation/1",
        "unit": "%",
        "label": "Peak loading against the rating",
    },
    "hours_above_rating": {
        "formula_id": "branch-weighted-hours-above-rating/1",
        "unit": "h",
        "label": "Weighted hours above the rating",
    },
    "peak_branch_flow": {
        "formula_id": "branch-peak-active-flow/1",
        "unit": "MW",
        "label": "Peak active-power flow",
    },
    "branch_rating": {
        "formula_id": "branch-rating/1",
        "unit": "MVA",
        "label": "Rating (s_nom_opt if extendable, else s_nom)",
    },
}
NETWORK_METRICS = {
    "max_branch_utilisation": {
        "formula_id": "network-max-branch-utilisation/1",
        "unit": "%",
        "label": "Highest branch loading against its rating",
    },
    "branches_above_rating": {
        "formula_id": "network-branches-above-rating/1",
        "unit": "branches",
        "label": "Branches above their rating at any snapshot",
    },
}
DISCLOSURE = (
    "Active power (P only), from the LOPF: an active-power proxy, not an AC thermal assessment; "
    "no N-1 and no voltage limits. Flow is the larger terminal magnitude; the limit is the rating "
    "times s_max_pu per snapshot. Hours use generator snapshot weights. "
    "Lines and Transformers only; Links are excluded. "
    "A branch with any missing flow or zero limit in the window is unavailable."
)


def _s_max_pu(component, name, index):
    """Static s_max_pu with the time-varying override, NaN falling back to static."""
    from harness.calculations import _number

    static = _number(component.static.at[name, "s_max_pu"])
    dynamic = component.dynamic.get("s_max_pu")
    if dynamic is None or name not in dynamic.columns:
        return [static] * len(index)
    if not dynamic.index.equals(index):
        raise ValueError("misaligned_source")
    return [static if (v := _number(x)) is None else v for x in dynamic[name]]


def _branch(component, name, index):
    from harness.calculations import _number

    row = component.static.loc[name]
    field = "s_nom_opt" if bool(row["s_nom_extendable"]) else "s_nom"
    flows = {}
    for attribute in ("p0", "p1"):
        table = component.dynamic.get(attribute)
        if table is None or not table.index.equals(index) or name not in table.columns:
            flows[attribute] = [None] * len(index)
        else:
            flows[attribute] = [_number(v) for v in table[name]]
    return {
        "rating": _number(row[field]),
        "rating_field": field,
        "s_max_pu": _s_max_pu(component, name, index),
        **flows,
    }


def source(ctx, entity, network, component_class=None):
    """Called under the network lock; refuses rather than guesses which branch."""
    from harness.calculations import MAX_SNAPSHOTS, _number
    from services import period_utils, workflow_tools

    n = ctx.network
    index = n.snapshots
    if (
        isinstance(index, pd.MultiIndex)
        or not isinstance(index, pd.DatetimeIndex)
        or not index.is_unique
        or not index.is_monotonic_increasing
        or not 1 <= len(index) <= MAX_SNAPSHOTS
        or len(n.investment_periods)
    ):
        raise ValueError("unsupported_snapshot_scope")
    state = ctx.solver_state
    if state.get("ac_pf_results") is not None or state.get("lopf_results") is not None:
        raise ValueError("ac_pf_present")
    if network != (entity == "network") and not component_class:
        raise ValueError("network_scope_required")
    shadowed = []
    if network:
        names = [(c, str(name)) for c in BRANCH_CLASSES for name in n.components[c].static.index]
        # A branch literally named "network" is checkable only by naming its class.
        shadowed = [c for c, name in names if name == "network"]
        if len(names) > MAX_BRANCHES:
            raise ValueError("unsupported_branch_population")
    else:
        classes = (component_class,) if component_class else BRANCH_CLASSES
        names = [(c, entity) for c in classes if entity in n.components[c].static.index]
        if len(names) > 1:
            raise ValueError("ambiguous_branch")
    if not names:
        raise ValueError("unknown_branch")
    weights = period_utils.snapshot_weights(n, "generators")
    if not weights.index.equals(index):
        raise ValueError("misaligned_source")
    fingerprint = workflow_tools._network_input_fingerprint(ctx)
    return {
        "version": 1,
        "profile": NETWORK_PROFILE if network else PROFILE,
        "entity_id": entity,
        "index": [v.isoformat() for v in index],
        "energy_weight": [_number(v) for v in weights],
        "branches": {f"{c}:{name}": {"class": c, "name": name, **_branch(n.components[c], name, index)}
                     for c, name in names},
        "links_excluded": len(n.components["Link"].static),
        "shadowed_branch_classes": shadowed,
        "valid_solve": state.get("status") == "completed"
        and state.get("condition") == "optimal"
        and n.meta.get("assistant_solve_input_fingerprint") == fingerprint
        and n.meta.get("assistant_solve_exact_input_fingerprint")
        == workflow_tools._network_input_fingerprint(ctx, exact=True),
    }


def _window(raw, scope):
    index = raw["index"]
    if "start" not in scope:
        return 0, len(index) - 1
    a = index.index(pd.Timestamp(scope["start"]).isoformat())
    b = index.index(pd.Timestamp(scope["end"]).isoformat())
    if b < a:
        raise ValueError("reversed_window")
    return a, b


def _loading(branch, weights, a, b):
    """Per-branch values over the window; None when any input is missing."""
    rating = branch["rating"]
    if rating is None or not math.isfinite(rating) or rating <= 1e-9:
        return None
    flows, utilisation = [], []
    for i in range(a, b + 1):
        p0, p1, smax, w = branch["p0"][i], branch["p1"][i], branch["s_max_pu"][i], weights[i]
        if p0 is None or p1 is None or smax is None or w is None or w < 0:
            return None
        limit = rating * smax
        if not math.isfinite(limit) or limit <= 1e-9:
            return None
        flow = max(abs(p0), abs(p1))
        flows.append(flow)
        utilisation.append(flow / limit)
    try:
        return {
            "peak_branch_utilisation": 100 * max(utilisation),
            "hours_above_rating": math.fsum(
                weights[a + k] for k, u in enumerate(utilisation) if u > 1 + OVER_LIMIT_TOLERANCE
            ),
            "peak_branch_flow": max(flows),
            "branch_rating": rating,
            "_above": any(u > 1 + OVER_LIMIT_TOLERANCE for u in utilisation),
        }
    except (OverflowError, ValueError):
        return None


def calculate(raw, scope):
    """Every branch of the population must be complete before a network value exists."""
    from harness.calculations import requested_metrics

    if (
        raw.get("version") != 1
        or raw.get("profile") != scope["profile"]
        or raw["entity_id"] != scope["entity_id"]
    ):
        raise ValueError("branch_source_identity")
    a, b = _window(raw, scope)
    values = (
        {key: _loading(branch, raw["energy_weight"], a, b) for key, branch in raw["branches"].items()}
        if raw["valid_solve"] else {key: None for key in raw["branches"]}
    )
    available = {}
    if all(v is not None for v in values.values()):
        if scope["profile"] == NETWORK_PROFILE:
            available = {
                "max_branch_utilisation": max(v["peak_branch_utilisation"] for v in values.values()),
                "branches_above_rating": sum(v["_above"] for v in values.values()),
            }
        else:
            available = {k: v for k, v in next(iter(values.values())).items() if not k.startswith("_")}
    definitions = NETWORK_METRICS if scope["profile"] == NETWORK_PROFILE else METRICS
    facts, slots = [], []
    for metric in requested_metrics(scope):
        value = available.get(metric)
        answered = metric in definitions and value is not None and math.isfinite(value)
        slot = {
            "metric": metric,
            "disposition": "answered" if answered else "unavailable",
            "requested_snapshots": b - a + 1,
        }
        if answered:
            definition = definitions[metric]
            facts.append({
                "path": "/" + metric,
                "entity_id": scope["entity_id"],
                "metric": metric,
                "value": value,
                "unit": definition["unit"],
                "formula_id": definition["formula_id"],
            })
        else:
            slot["reason"] = (
                "unverified_solve" if not raw["valid_solve"]
                else "invalid_or_missing_branch_data" if metric in definitions
                else "unsupported_metric"
            )
        slots.append(slot)
    return facts, slots


def notes(raw):
    """Always-shown scope line: which branches, and how many Links were left out."""
    classes = [branch["class"] for branch in raw["branches"].values()]
    lines = [
        f"{classes.count('Line')} Lines and {classes.count('Transformer')} Transformers; "
        f"{raw['links_excluded']} Links excluded."
    ]
    for component_class in raw.get("shadowed_branch_classes", []):
        lines.append(f"A {component_class} named network exists; name its class to check that branch.")
    return lines
