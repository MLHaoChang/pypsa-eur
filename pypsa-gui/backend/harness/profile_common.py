"""Shared plumbing for the bounded profiles added from checked-results S5 on.

Each profile owns its source capture and its arithmetic; this module only
holds the snapshot-scope check, the exact window and the fact/slot shape the
receipt contract expects, so they are not copied into every profile.
"""

import math

import pandas as pd


def single_period_index(n):
    """The snapshots every bounded profile supports, or a named refusal."""
    from harness.calculations import MAX_SNAPSHOTS

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
    return index


def lp_outputs_live(ctx):
    """The live outputs are the verified LP solve's: no AC power flow has replaced them."""
    from harness.calculations import solve_matches_inputs

    return solve_matches_inputs(ctx)


def window(index, scope):
    """Inclusive positions of the requested exact snapshot bounds."""
    if "start" not in scope:
        return 0, len(index) - 1
    a = index.index(pd.Timestamp(scope["start"]).isoformat())
    b = index.index(pd.Timestamp(scope["end"]).isoformat())
    if b < a:
        raise ValueError("reversed_window")
    return a, b


def finite(values):
    return all(v is not None and math.isfinite(v) for v in values)


def facts_and_slots(scope, definitions, available, reasons, requested_snapshots):
    """Answered slots carry a fact; every other requested metric names its reason."""
    from harness.calculations import requested_metrics

    facts, slots = [], []
    for metric in requested_metrics(scope):
        value = available.get(metric)
        answered = metric in definitions and value is not None and math.isfinite(value)
        slot = {
            "metric": metric,
            "disposition": "answered" if answered else "unavailable",
            "requested_snapshots": requested_snapshots,
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
            slot["reason"] = reasons.get(metric, "unsupported_metric") if metric in definitions else "unsupported_metric"
        slots.append(slot)
    return facts, slots
