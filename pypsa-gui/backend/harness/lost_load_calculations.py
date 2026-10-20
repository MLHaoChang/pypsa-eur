"""Bounded lost-load checks over the solve's VoLL-slack capture (checked-results S4).

The slacks are removed after the solve, so the source is the capture the same
solve wrote (`solver_state["last_lost_load"]`, wiped on every solve claim and
written beside the input fingerprints). Totals are rebuilt from the per-snapshot
shed frame, never read from the capture's own totals; the conventions are the
engine's (`services/adequacy/metrics.py`): negatives clipped as LP dust, shed
hours where summed shed exceeds 1e-3 MW, generator weights for energy and
hours, objective weights times the solve-time VoLL for cost.
"""

import math

import pandas as pd

PROFILE = "lost-load/1"
MAX_LOADS = 64
SHED_THRESHOLD_MW = 1e-3
REFUSALS = frozenset({
    "no_lost_load_capture", "unknown_load", "unsupported_load_population",
    "legacy_lost_load_capture", "no_electrical_loads", "unsupported_snapshot_scope",
    "misaligned_source",
})
METRICS = {
    "lost_load_energy": {
        "formula_id": "lost-load-energy/1",
        "unit": "MWh",
        "label": "Unserved energy",
    },
    "peak_shed": {
        "formula_id": "lost-load-peak/1",
        "unit": "MW",
        "label": "Peak shed power",
    },
    "shed_hours": {
        "formula_id": "lost-load-weighted-shed-hours/1",
        "unit": "h",
        "label": "Weighted hours with shed above 1e-3 MW",
    },
    "lost_load_cost": {
        "formula_id": "lost-load-cost-at-voll/1",
        "unit": "EUR",
        "label": "Unserved energy at the solve's VoLL",
    },
}
DISCLOSURE = (
    "Involuntary load shedding through the solve's VoLL slacks only; demand response is excluded. "
    "Energy and hours use generator snapshot weights; cost uses objective weights times the base VoLL "
    "the solve used (critical-load VoLL premiums are not included). The shed is not checked against "
    "demand: the solve-time demand is not captured. Without a VoLL slack, lost load was not modelled "
    "and no value is given."
)


def source(ctx, entity):
    """Called under the network lock; the capture must belong to the verified solve."""
    from harness.calculations import MAX_SNAPSHOTS, _number, solve_matches_inputs
    from services import period_utils
    from services.adequacy.metrics import electrical_columns

    n = ctx.network
    index = n.snapshots
    if (
        isinstance(index, pd.MultiIndex)
        or not isinstance(index, pd.DatetimeIndex)
        or not 1 <= len(index) <= MAX_SNAPSHOTS
        or len(n.investment_periods)
    ):
        raise ValueError("unsupported_snapshot_scope")
    capture = ctx.solver_state.get("last_lost_load") or {}
    frame = capture.get("lost_load_t")
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise ValueError("no_lost_load_capture")
    if not frame.index.equals(index) or not frame.columns.is_unique:
        raise ValueError("misaligned_source")
    if any(str(c) not in n.loads.index for c in frame.columns):
        # Older captures were keyed by bus; a bus id must never pass as a Load.
        raise ValueError("legacy_lost_load_capture")
    if entity == "network":
        # As the engine: energy, peak and cost over every captured Load (the
        # Lost load card's total), shed hours over the electrical ones only.
        columns = [str(c) for c in frame.columns]
        electrical = [str(c) for c in electrical_columns(n, columns)]
        if len(columns) > MAX_LOADS:
            raise ValueError("unsupported_load_population")
        if not electrical:
            raise ValueError("no_electrical_loads")
    elif entity in frame.columns:
        columns = electrical = [entity]
    else:
        raise ValueError("unknown_load")
    energy = period_utils.snapshot_weights(n, "generators")
    objective = period_utils.snapshot_weights(n, "objective")
    return {
        "version": 1,
        "profile": PROFILE,
        "entity_id": entity,
        "index": [v.isoformat() for v in index],
        "energy_weight": [_number(v) for v in energy.reindex(index)],
        "objective_weight": [_number(v) for v in objective.reindex(index)],
        "shed": {c: [_number(v) for v in frame[c]] for c in columns},
        "electrical": electrical,
        "voll": _number(capture.get("voll_eur_per_mwh")),
        "valid_solve": solve_matches_inputs(ctx),
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


def calculate(raw, scope):
    """Any missing shed or weight in the window makes every metric unavailable."""
    from harness.calculations import requested_metrics

    if raw.get("version") != 1 or raw.get("profile") != PROFILE or raw["entity_id"] != scope["entity_id"]:
        raise ValueError("lost_load_source_identity")
    a, b = _window(raw, scope)
    rows = range(a, b + 1)
    values = [raw["shed"][c][i] for c in raw["shed"] for i in rows]
    weights = [raw["energy_weight"][i] for i in rows] + [raw["objective_weight"][i] for i in rows]
    available, reasons = {}, {}
    complete = raw["valid_solve"] and all(v is not None and math.isfinite(v) for v in values + weights)
    if complete and all(w >= 0 for w in weights):
        totals = [math.fsum(max(raw["shed"][c][i], 0.0) for c in raw["shed"]) for i in rows]
        electrical = [math.fsum(max(raw["shed"][c][i], 0.0) for c in raw["electrical"]) for i in rows]
        available = {
            "lost_load_energy": math.fsum(raw["energy_weight"][i] * t for i, t in zip(rows, totals)),
            "peak_shed": max(totals),
            "shed_hours": math.fsum(
                raw["energy_weight"][i] for i, t in zip(rows, electrical) if t > SHED_THRESHOLD_MW
            ),
        }
        voll = raw["voll"]
        if voll is not None and voll > 0:
            available["lost_load_cost"] = voll * math.fsum(
                raw["objective_weight"][i] * t for i, t in zip(rows, totals)
            )
        else:
            reasons["lost_load_cost"] = "voll_missing_or_zero"
    base_reason = "unverified_solve" if not raw["valid_solve"] else "invalid_or_missing_shed_data"
    facts, slots = [], []
    for metric in requested_metrics(scope):
        value = available.get(metric)
        answered = metric in METRICS and value is not None and math.isfinite(value)
        slot = {
            "metric": metric,
            "disposition": "answered" if answered else "unavailable",
            "requested_snapshots": b - a + 1,
        }
        if answered:
            definition = METRICS[metric]
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
                reasons.get(metric, base_reason) if metric in METRICS else "unsupported_metric"
            )
        slots.append(slot)
    return facts, slots


def notes(raw):
    """Always-shown scope line: which Loads the figure covers."""
    if raw["entity_id"] == "network":
        return [
            f"{len(raw['shed'])} Loads with a VoLL slack; shed hours count the "
            f"{len(raw['electrical'])} electrical Loads only, as the engine does."
        ]
    return ["This Load's own VoLL slack only."]
