"""Bounded AC power-flow branch loading for checked answers (AA-13, A2.8).

One Line or Transformer, from the AC power flow's own result snapshot
(`solver_state["ac_pf_results"]`), read with the Load flow tab's readers
(`services/loadflow_summary`: `_frame`, `_smax_matrix`, `_rating`,
`_ac_convergence`) so the figures match its `ac_pf` source. Flow is the larger
terminal apparent power, max(hypot(p0, q0), hypot(p1, q1)); a branch with no
reactive flow recorded (a Transformer, today) falls back to the larger active
power and says so. The limit is rating·s_max_pu(t); overload is u > 1 + 1e-6.

Values are computed over the converged snapshots of the window only. Every
snapshot whose convergence is not `True` is excluded, and the evidence then
carries a **required warning slot** (`ac_pf_convergence`, disposition
`warning`): the receipt keeps it in `result_slots` whatever metrics were asked,
so `request_complete` stays false (receipt policy "5"). Unknown convergence
(`ok` null or unreadable, AA-12) takes precedence over not converged. A window
with no converged snapshot, or a convergence list that is not aligned with the
snapshots, refuses the capture. `branch-loading/1` is unchanged: it still
refuses when AC power-flow results exist.
"""

import math

import pandas as pd

from harness import branch_calculations as branch

PROFILE = "ac-branch-loading/1"
PROFILES = (PROFILE,)
WARNING_METRIC = "ac_pf_convergence"
REFUSALS = frozenset(
    {
        "ac_pf_not_available",
        "ac_pf_convergence_unavailable",
        "ac_pf_not_converged",
        "ac_pf_convergence_unknown",
    }
)
METRICS = {
    "ac_peak_branch_utilisation": {
        "formula_id": "ac-branch-peak-utilisation/1",
        "unit": "%",
        "label": "Peak AC loading against the rating, over converged snapshots",
    },
    "ac_hours_above_rating": {
        "formula_id": "ac-branch-weighted-hours-above-rating/1",
        "unit": "h",
        "label": "Weighted hours above the rating, over converged snapshots",
    },
    "ac_peak_apparent_flow": {
        "formula_id": "ac-branch-peak-apparent-flow/1",
        "unit": "MVA",
        "label": "Peak apparent-power flow, over converged snapshots",
    },
    "ac_branch_rating": {
        "formula_id": "ac-branch-rating/1",
        "unit": "MVA",
        "label": "Rating (s_nom_opt if extendable, else s_nom)",
    },
}
DISCLOSURE = (
    "AC power flow: apparent power from the flow's own results, max(hypot(p0, q0), hypot(p1, q1)); "
    "no N-1 and no voltage limits. The limit is the rating times s_max_pu per snapshot. "
    "Hours use generator snapshot weights. Values cover converged snapshots only; a "
    "non-converged or unknown snapshot is excluded and named in the receipt's warning. "
    "A branch with any missing flow or zero limit at a converged snapshot is unavailable."
)
P_FALLBACK_NOTE = (
    "No reactive flow is recorded for this branch: loading uses active power only "
    "(P proxy), and the apparent-power flow is unavailable."
)


def _series(frame, name, length):
    if frame is None or name not in frame.columns:
        return [None] * length
    from harness.calculations import _number

    return [_number(v) for v in frame[name]]


def source(ctx, entity, component_class=None):
    """Called under the network lock; refuses rather than guesses which branch."""
    from harness.calculations import MAX_SNAPSHOTS, _number, solve_matches_inputs
    from services import loadflow_summary, period_utils

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
    if not isinstance(state.get("ac_pf_results"), dict):
        raise ValueError("ac_pf_not_available")
    classes = (component_class,) if component_class else branch.BRANCH_CLASSES
    names = [(c, entity) for c in classes if entity in n.components[c].static.index]
    if len(names) > 1:
        raise ValueError("ambiguous_branch")
    if not names:
        raise ValueError("unknown_branch")
    mask, _ = loadflow_summary._ac_convergence(n, index, 0, len(index) - 1, state)
    if mask is None:
        raise ValueError("ac_pf_convergence_unavailable")
    # True converged, False not converged; anything else (null, unreadable) is unknown.
    converged = [
        e.get("ok") if e.get("ok") is True or e.get("ok") is False else None
        for e in state["ac_pf_convergence_list"]
    ]
    weights = period_utils.snapshot_weights(n, "generators")
    if not weights.index.equals(index):
        raise ValueError("misaligned_source")
    component_class, name = names[0]
    comp = "lines" if component_class == "Line" else "transformers"
    t_accessor = comp + "_t"
    comp_df = getattr(n, comp)
    label = pd.Index([name])
    frames = {
        attr: loadflow_summary._frame(n, t_accessor, attr, "ac_pf", label, index, state)
        for attr in ("p0", "p1", "q0", "q1")
    }
    rating, rating_field = loadflow_summary._rating(comp_df.loc[name])
    smax = loadflow_summary._smax_matrix(n, comp_df, t_accessor, label, index)[name]
    return {
        "version": 1,
        "profile": PROFILE,
        "entity_id": entity,
        "index": [v.isoformat() for v in index],
        "energy_weight": [_number(v) for v in weights],
        "converged": converged,
        "branch": {
            "class": component_class,
            "name": name,
            "rating": rating,
            "rating_field": rating_field,
            "s_max_pu": [_number(v) for v in smax],
            **{
                attr: _series(frame, name, len(index)) for attr, frame in frames.items()
            },
        },
        "valid_solve": state.get("status") == "completed"
        and state.get("condition") == "lopf+ac_pf_ok"
        and solve_matches_inputs(ctx, after_pf=True),
    }


def _convergence(raw, a, b):
    """(converged positions, excluded snapshot labels, state) over window a..b."""
    window = raw["converged"][a : b + 1]
    kept = [a + k for k, ok in enumerate(window) if ok is True]
    excluded = [raw["index"][a + k] for k, ok in enumerate(window) if ok is not True]
    state = (
        "converged"
        if not excluded
        else "ac_pf_convergence_unknown"
        if any(ok is None for ok in window)
        else "ac_pf_not_converged"
    )
    return kept, excluded, state


def required_warnings(raw, scope):
    """The required warning slot when any snapshot of the window did not converge
    (or is unknown); [] when every one converged. `excluded_indices` are the
    excluded snapshots' labels, the same basis as the metric slots'."""
    a, b = branch._window(raw, scope)
    kept, excluded, state = _convergence(raw, a, b)
    if not excluded:
        return []
    count = b - a + 1
    return [
        {
            "metric": WARNING_METRIC,
            "disposition": "warning",
            "reason": f"{state} ({len(kept)} of {count} snapshots in the window converged)",
            "valid_snapshots": len(kept),
            "requested_snapshots": count,
            "excluded_indices": excluded,
        }
    ]


def _loading(raw, kept):
    """Per-branch values over the converged snapshots; None when any input there is missing."""
    data, weights = raw["branch"], raw["energy_weight"]
    rating = data["rating"]
    if rating is None or not math.isfinite(rating) or rating <= 1e-9:
        return None
    apparent = any(data["q0"][i] is not None for i in kept)
    flows, utilisation = [], []
    for i in kept:
        p0, p1, smax, w = data["p0"][i], data["p1"][i], data["s_max_pu"][i], weights[i]
        if p0 is None or smax is None or w is None or w < 0:
            return None
        if apparent:
            if data["q0"][i] is None:
                return None
            s1 = (
                math.hypot(p1, data["q1"][i])
                if p1 is not None and data["q1"][i] is not None
                else None
            )
            flow = max(math.hypot(p0, data["q0"][i]), s1 if s1 is not None else 0.0)
        else:
            flow = max(abs(p0), abs(p1) if p1 is not None else 0.0)
        limit = rating * smax
        if not math.isfinite(limit) or limit <= 1e-9:
            return None
        flows.append(flow)
        utilisation.append(flow / limit)
    try:
        return {
            "ac_peak_branch_utilisation": 100 * max(utilisation),
            "ac_hours_above_rating": math.fsum(
                weights[i]
                for i, u in zip(kept, utilisation)
                if u > 1 + branch.OVER_LIMIT_TOLERANCE
            ),
            "ac_peak_apparent_flow": max(flows) if apparent else None,
            "ac_branch_rating": rating,
        }
    except (OverflowError, ValueError):
        return None


def calculate(raw, scope):
    """Facts and slots over the converged snapshots of the window; refuses a
    window with none (unknown before not converged)."""
    from harness.calculations import requested_metrics

    if (
        raw.get("version") != 1
        or raw.get("profile") != scope["profile"]
        or raw["entity_id"] != scope["entity_id"]
    ):
        raise ValueError("ac_branch_source_identity")
    a, b = branch._window(raw, scope)
    kept, excluded, state = _convergence(raw, a, b)
    if not kept:
        raise ValueError(state)
    available = (_loading(raw, kept) if raw["valid_solve"] else None) or {}
    apparent = any(raw["branch"]["q0"][i] is not None for i in kept)
    facts, slots = [], []
    for metric in requested_metrics(scope):
        value = available.get(metric)
        answered = metric in METRICS and value is not None and math.isfinite(value)
        slot = {
            "metric": metric,
            "disposition": "answered" if answered else "unavailable",
            "valid_snapshots": len(kept),
            "requested_snapshots": b - a + 1,
        }
        if excluded:
            slot["excluded_indices"] = excluded
        if answered:
            definition = METRICS[metric]
            facts.append(
                {
                    "path": "/" + metric,
                    "entity_id": scope["entity_id"],
                    "metric": metric,
                    "value": value,
                    "unit": definition["unit"],
                    "formula_id": definition["formula_id"],
                }
            )
        else:
            slot["reason"] = (
                "unverified_solve"
                if not raw["valid_solve"]
                else "unsupported_metric"
                if metric not in METRICS
                else "apparent_power_unavailable"
                if metric == "ac_peak_apparent_flow" and available and not apparent
                else "invalid_or_missing_branch_data"
            )
        slots.append(slot)
    return facts, slots


def notes(raw, scope):
    """Always-shown lines: the converged share the values cover, and the P fallback."""
    a, b = branch._window(raw, scope)
    kept, excluded, _ = _convergence(raw, a, b)
    lines = [
        f"{raw['branch']['class']} values cover {len(kept)} of {b - a + 1} snapshots"
        + (
            "; the others did not converge or are unknown."
            if excluded
            else ", all converged."
        )
    ]
    if not any(raw["branch"]["q0"][i] is not None for i in kept):
        lines.append(P_FALLBACK_NOTE)
    return lines
