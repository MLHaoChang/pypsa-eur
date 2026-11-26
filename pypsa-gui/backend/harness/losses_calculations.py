"""Bounded transmission-loss checks over the LP's loss variables (assistant-assurance 10, S1b).

The reader of `services/results/losses.py:_validity_rows` (A1.7): each Line,
Transformer and Load is classified on its RAW values, before the route's
zero-fill, so a missing, infinite, absent or misaligned value never becomes a
loss or demand figure. A real 0 MW is a value and is answered as 0 MWh.

Figures follow `/results/losses` (source `lopf`): per-snapshot loss times the
generator (energy) snapshot weight, peak as the largest single-branch |loss|,
served demand as the weighted sum of `loads_t.p`, and the share of demand in
percent. Validity is judged over the whole solved horizon, so a branch with one
bad value anywhere gives no figure for any window. AC power-flow losses
(`source=ac_pf`) are out of scope: a network with a power-flow record refuses.
"""

import math

from harness import profile_common
from harness.derived import NEAR_ZERO

PROFILE = "branch-losses/1"
MAX_BRANCHES = 64
MAX_LOADS = 64
BRANCH_CLASSES = ("Line", "Transformer")
REFUSALS = frozenset({
    "unsupported_snapshot_scope", "ac_pf_present", "unsupported_branch_population",
    "ambiguous_branch", "unknown_branch", "misaligned_source",
})
# Every reason a slot can carry; pinned by exact set equality in the tests.
REASONS = frozenset({
    "unverified_solve", "losses_not_recorded", "incomplete", "absent", "misaligned",
    "no_loads", "network_scope_required", "near_zero_denominator",
})
# Worst first: the reason a population carries is its worst member's state.
_STATE_ORDER = ("misaligned", "absent", "incomplete")
METRICS = {
    "loss_energy": {
        "formula_id": "branch-loss-energy/1",
        "unit": "MWh",
        "label": "Transmission loss energy",
    },
    "peak_loss": {
        "formula_id": "branch-loss-peak/1",
        "unit": "MW",
        "label": "Largest single-branch loss",
    },
    "demand_energy": {
        "formula_id": "served-demand-energy/1",
        "unit": "MWh",
        "label": "Served demand",
    },
    "loss_pct_of_demand": {
        "formula_id": "loss-share-of-demand/1",
        "unit": "%",
        "label": "Losses as a share of served demand",
    },
}
NETWORK_ONLY = frozenset({"demand_energy", "loss_pct_of_demand"})
DISCLOSURE = (
    "Losses are the LP's own loss variables (transmission_losses on the solve), weighted by generator "
    "snapshot weights; AC power-flow losses are not covered. A Line, Transformer or Load with a missing, "
    "infinite, absent or misaligned value anywhere in the solved horizon gives no figure, and the network "
    "totals and the share of demand need every one of them complete."
)


def source(ctx, entity):
    """Called under the network lock; the raw frames are classified before any fill."""
    from harness.calculations import _number
    from services import period_utils
    from services.results.losses import _validity_rows

    n = ctx.network
    index = profile_common.single_period_index(n)
    state = ctx.solver_state
    if state.get("ac_pf_results") is not None or state.get("lopf_results") is not None:
        raise ValueError("ac_pf_present")
    if entity == "network":
        names = [(c, str(name)) for c in BRANCH_CLASSES for name in n.components[c].static.index]
        if len(names) > MAX_BRANCHES or len(n.loads) > MAX_LOADS:
            raise ValueError("unsupported_branch_population")
    else:
        names = [(c, entity) for c in BRANCH_CLASSES if entity in n.components[c].static.index]
        if len(names) > 1:
            raise ValueError("ambiguous_branch")
    if not names:
        raise ValueError("unknown_branch")
    weights = period_utils.snapshot_weights(n, "generators")
    if not weights.index.equals(index):
        raise ValueError("misaligned_source")

    def rows(frame, members, component):
        out = {}
        aligned = None
        for row in _validity_rows(frame, members, component, index):
            if row["state"] == "complete":
                aligned = frame.reindex(index) if aligned is None else aligned
                values = [_number(v) for v in aligned[row["name"]]]
            else:
                values = None
            out[f"{component}:{row['name']}"] = {"state": row["state"], "values": values}
        return out

    branches, recorded = {}, False
    for component in BRANCH_CLASSES:
        members = [name for c, name in names if c == component]
        if not members:
            continue
        frame = n.components[component].dynamic.get("loss")
        recorded = recorded or (frame is not None and len(frame.index) > 0)
        branches.update(rows(frame, members, component))
    loads = {}
    if entity == "network":
        loads = rows(n.loads_t.p if hasattr(n.loads_t, "p") else None, [str(x) for x in n.loads.index], "Load")
    return {
        "version": 1,
        "profile": PROFILE,
        "entity_id": entity,
        "index": [v.isoformat() for v in index],
        "energy_weight": [_number(v) for v in weights],
        "branches": branches,
        "loads": loads,
        "losses_recorded": recorded,
        "valid_solve": profile_common.lp_outputs_live(ctx),
    }


def _worst(members):
    states = {m["state"] for m in members.values()}
    return next((s for s in _STATE_ORDER if s in states), None)


def calculate(raw, scope):
    """Unavailable, with the state as the reason, unless every member the metric reads is complete."""
    if raw.get("version") != 1 or raw.get("profile") != PROFILE or raw["entity_id"] != scope["entity_id"]:
        raise ValueError("branch_losses_source_identity")
    a, b = profile_common.window(raw["index"], scope)
    rows = range(a, b + 1)
    weights = [raw["energy_weight"][i] for i in rows]
    available, reasons = {}, {}
    if not raw["valid_solve"] or not profile_common.finite(weights) or min(weights) < 0:
        reasons = dict.fromkeys(METRICS, "unverified_solve")
    else:
        network = raw["entity_id"] == "network"
        loss_reason = "losses_not_recorded" if not raw["losses_recorded"] else _worst(raw["branches"])
        if loss_reason:
            reasons["loss_energy"] = reasons["peak_loss"] = loss_reason
        else:
            series = [m["values"] for m in raw["branches"].values()]
            available["loss_energy"] = math.fsum(weights[k] * v[i] for v in series for k, i in enumerate(rows))
            available["peak_loss"] = max(abs(v[i]) for v in series for i in rows)
        if not network:
            reasons.update(dict.fromkeys(NETWORK_ONLY, "network_scope_required"))
        else:
            demand_reason = "no_loads" if not raw["loads"] else _worst(raw["loads"])
            if demand_reason:
                reasons["demand_energy"] = reasons["loss_pct_of_demand"] = demand_reason
            else:
                available["demand_energy"] = math.fsum(
                    weights[k] * m["values"][i] for m in raw["loads"].values() for k, i in enumerate(rows))
                if loss_reason:
                    reasons["loss_pct_of_demand"] = loss_reason
                elif abs(available["demand_energy"]) <= NEAR_ZERO:
                    reasons["loss_pct_of_demand"] = "near_zero_denominator"
                else:
                    available["loss_pct_of_demand"] = 100.0 * available["loss_energy"] / available["demand_energy"]
    return profile_common.facts_and_slots(scope, METRICS, available, reasons, b - a + 1)


def notes(raw, scope, label):
    """Always-shown scope lines: how many members the figures stand on, and which are not complete."""
    del scope, label
    lines = [f"{len(raw['branches'])} branches"
             + (f" and {len(raw['loads'])} Loads" if raw["entity_id"] == "network" else "")
             + " classified on their raw values over the whole solved horizon."]
    bad = sorted(f"{key} ({m['state']})" for key, m in {**raw["branches"], **raw["loads"]}.items()
                 if m["state"] != "complete")
    if bad and raw["losses_recorded"]:
        shown = ", ".join(bad[:5]) + (f" and {len(bad) - 5} more" if len(bad) > 5 else "")
        lines.append("Not complete: " + shown + ".")
    return lines
