"""Checked transmission losses (`branch-losses/1`, `network-losses/1`;
assistant-assurance 10, S1b).

Reads the LP loss variables (`lines_t.loss`, `transformers_t.loss`) and the
served demand (`loads_t.p`) raw, with no zero-fill, and classifies each entity
with `services.results.losses._validity_rows`: a branch or load that is not
complete over the requested window gives no fact, so a missing value is never
answered as 0 MWh. A real zero is answered. GK B conditions L1-L10 are in
issue 10's Comments.
"""

import math

import pandas as pd

from harness import derived
from harness import profile_common as common

PROFILE = "branch-losses/1"
NETWORK_PROFILE = "network-losses/1"
PROFILES = (PROFILE, NETWORK_PROFILE)
MAX_BRANCHES = 64
MAX_LOADS = 64  # the share of demand reads every Load (lost_load bounds them alike)
BRANCH_CLASSES = ("Line", "Transformer")
# LP loss variables are bounded below by 0. A value this close below 0 is
# solver noise and reads as 0 (disclosed); anything further below is invalid.
NEGATIVE_TOLERANCE = derived.NEAR_ZERO
REFUSALS = frozenset(
    {
        "unsupported_snapshot_scope",
        "ac_pf_present",
        "network_scope_required",
        "unsupported_branch_population",
        "unsupported_load_population",
        "ambiguous_branch",
        "unknown_branch",
        "misaligned_source",
    }
)
METRICS = {
    "loss_mwh": {
        "formula_id": "branch-loss-energy-weighted/1",
        "unit": "MWh",
        "label": "Transmission losses (generator snapshot weights)",
    },
    "peak_loss_mw": {
        "formula_id": "branch-loss-peak/1",
        "unit": "MW",
        "label": "Peak transmission loss",
    },
}
NETWORK_METRICS = {
    **METRICS,
    "loss_pct_of_demand": {
        "formula_id": "branch-loss-share-of-load-demand/1",
        "unit": "%",
        "label": "Losses as a share of load demand",
    },
}
DISCLOSURE = (
    "LP transmission-loss variables of Lines and Transformers, energy-weighted with generator "
    "snapshot weights; the network peak is the peak of the per-snapshot sum. The share of demand "
    "divides by the energy-weighted loads_t.p of Loads only: storage charging and Link "
    "consumption are not counted as demand. A branch or load with a missing, infinite or "
    "misaligned value in the window gives no value."
)


def _validity_rows(*args):
    from services.results.losses import _validity_rows as rows

    return rows(*args)


def _entities(component, attribute, names, index):
    """Structural state (absent / misaligned) over the whole frame, plus the
    frame aligned to `index` as raw numbers (None where missing or infinite)."""
    from harness.calculations import _number

    table = component.dynamic.get(attribute)
    rows = _validity_rows(table, names, component.name, index)
    aligned = None
    out = {}
    for name, row in zip(names, rows):
        entry = {
            "class": component.name,
            "name": str(name),
            "state": row["state"],
            "values": None,
        }
        if row["state"] in ("complete", "incomplete"):
            if aligned is None:
                aligned = table.reindex(index)
            entry["values"] = [_number(v) for v in aligned[name]]
            entry["state"] = None  # decided per window in calculate
        out[f"{component.name}:{name}"] = entry
    return out


def source(ctx, entity, network, component_class=None):
    """Called under the network lock; refuses rather than guesses which branch."""
    from harness.calculations import _number
    from services import period_utils

    n = ctx.network
    index = common.single_period_index(n)
    state = ctx.solver_state
    # The same presence check as branch-loading (L5): after an AC power flow the
    # live frames are PF values, not the LP loss variables.
    if state.get("ac_pf_results") is not None or state.get("lopf_results") is not None:
        raise ValueError("ac_pf_present")
    if network != (entity == "network") and not component_class:
        raise ValueError("network_scope_required")
    shadowed = []
    if network:
        names = {c: list(n.components[c].static.index) for c in BRANCH_CLASSES}
        if sum(len(v) for v in names.values()) > MAX_BRANCHES:
            raise ValueError("unsupported_branch_population")
        if len(n.loads) > MAX_LOADS:
            raise ValueError("unsupported_load_population")
        # A branch literally named "network" is checkable only by naming its class.
        shadowed = [c for c, members in names.items() if "network" in map(str, members)]
    else:
        classes = (component_class,) if component_class else BRANCH_CLASSES
        names = {c: [entity] for c in classes if entity in n.components[c].static.index}
        if len(names) > 1:
            raise ValueError("ambiguous_branch")
    if not any(names.values()):
        raise ValueError("unknown_branch")
    weights = period_utils.snapshot_weights(n, "generators")
    if not weights.index.equals(index):
        raise ValueError("misaligned_source")
    branches = {}
    for c, members in names.items():
        branches.update(_entities(n.components[c], "loss", members, index))
    loads = (
        _entities(n.components["Load"], "p", list(n.loads.index), index)
        if network
        else {}
    )
    return {
        "version": 1,
        "profile": NETWORK_PROFILE if network else PROFILE,
        "entity_id": entity,
        "index": [v.isoformat() for v in index],
        "energy_weight": [_number(v) for v in weights],
        "branches": branches,
        "loads": loads,
        "shadowed_branch_classes": shadowed,
        "valid_solve": common.lp_outputs_live(ctx),
    }


def _window(raw, scope):
    try:
        return common.window(raw["index"], scope)
    except ValueError as exc:
        if str(exc) == "reversed_window":
            raise
        raise ValueError("unsupported_snapshot_scope") from None


def _state(entry, a, b, load=False):
    """`complete`, `incomplete`, `absent`, `misaligned` or (branches) `invalid`."""
    if entry["state"] is not None:
        return entry["state"]
    window = entry["values"][a : b + 1]
    frame = pd.DataFrame({"v": [math.nan if v is None else v for v in window]})
    state = _validity_rows(frame, ["v"], entry["class"], frame.index)[0]["state"]
    if state != "complete":
        return state
    if load:
        return "incomplete" if any(v < 0 for v in window) else "complete"
    return "invalid" if any(v < -NEGATIVE_TOLERANCE for v in window) else "complete"


def _series(entry, a, b):
    return [max(v, 0.0) for v in entry["values"][a : b + 1]]


def calculate(raw, scope):
    """Every branch (and, for the share, every load) must be complete in the window."""
    if (
        raw.get("version") != 1
        or raw.get("profile") != scope["profile"]
        or raw["entity_id"] != scope["entity_id"]
    ):
        raise ValueError("losses_source_identity")
    a, b = _window(raw, scope)
    network = scope["profile"] == NETWORK_PROFILE
    definitions = NETWORK_METRICS if network else METRICS
    weight = raw["energy_weight"][a : b + 1]
    available, reasons = {}, {}
    if not raw["valid_solve"]:
        reasons = dict.fromkeys(definitions, "unverified_solve")
    elif not common.finite(weight) or any(w < 0 for w in weight):
        reasons = dict.fromkeys(definitions, "invalid_or_missing_weight")
    else:
        states = {key: _state(entry, a, b) for key, entry in raw["branches"].items()}
        bad = [s for s in states.values() if s != "complete"]
        if bad:
            reasons = dict.fromkeys(definitions, f"losses_{_worst(bad)}")
        else:
            series = [_series(entry, a, b) for entry in raw["branches"].values()]
            totals = [math.fsum(column) for column in zip(*series)]
            loss = math.fsum(w * t for w, t in zip(weight, totals))
            available = {"loss_mwh": loss, "peak_loss_mw": max(totals)}
            if network:
                _share(raw, a, b, weight, loss, available, reasons)
    return common.facts_and_slots(scope, definitions, available, reasons, b - a + 1)


_ORDER = ("misaligned", "invalid", "incomplete", "absent")


def _worst(states):
    return next(s for s in _ORDER if s in states)


def _share(raw, a, b, weight, loss, available, reasons):
    load_states = [_state(entry, a, b, load=True) for entry in raw["loads"].values()]
    if not load_states or any(s != "complete" for s in load_states):
        bad = [s for s in load_states if s != "complete"] or ["absent"]
        reasons["loss_pct_of_demand"] = f"load_demand_{_worst(bad)}"
        return
    demand = math.fsum(
        w * math.fsum(entry["values"][a + k] for entry in raw["loads"].values())
        for k, w in enumerate(weight)
    )
    if abs(demand) <= derived.NEAR_ZERO:
        reasons["loss_pct_of_demand"] = "near_zero_denominator"
        return
    available["loss_pct_of_demand"] = loss / demand * 100


def notes(raw, scope):
    """Always-shown scope line: the population, and any value read as 0. The
    window is named by the answer's header line."""
    a, b = _window(raw, scope)
    classes = [entry["class"] for entry in raw["branches"].values()]
    lines = [
        f"{classes.count('Line')} Lines and {classes.count('Transformer')} Transformers; "
        "Links are not branches here."
    ]
    for component_class in raw.get("shadowed_branch_classes", []):
        lines.append(
            f"A {component_class} named network exists; name its class to check that branch."
        )
    # Only an answered value can have read a value as 0 (the same condition as calculate).
    answered = raw["valid_solve"] and all(
        _state(entry, a, b) == "complete" for entry in raw["branches"].values()
    )
    rounded = answered and sum(
        1
        for entry in raw["branches"].values()
        for v in entry["values"][a : b + 1]
        if -NEGATIVE_TOLERANCE <= v < 0
    )
    if rounded:
        lines.append(
            f"{rounded} loss values within {NEGATIVE_TOLERANCE:g} MW below zero were read as 0 MW."
        )
    return lines
