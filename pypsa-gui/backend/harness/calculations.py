"""Bounded, raw-source generator calculations; never a total-system-cost alias.

A profile's definition hash (`definition_hash`) covers each metric's label, unit
and formula_id and the checked-answer renderer's version, not the formula code.
A change to a formula's arithmetic must bump its formula_id (``x/1`` -> ``x/2``),
or a record checked under the old formula reads as if nothing changed.
"""

from __future__ import annotations

import hashlib
import json
import math
from numbers import Real

import pandas as pd

from services import workflow_tools
from harness import ac_branch_calculations as ac_branch
from harness import branch_calculations as branch
from harness import capacity_calculations as capacity
from harness import carrier_calculations as carrier
from harness import copt_calculations as copt_screen
from harness import curtailment_calculations as curtailment
from harness import emissions_calculations as emissions
from harness import energy_balance_calculations as balance
from harness import grid_readiness_calculations as readiness
from harness import investment_calculations as investment
from harness import losses_calculations as losses
from harness import lost_load_calculations as lost_load
from harness import price_calculations as price
from harness import site_import_calculations as site_import
from harness import storage_cycles_calculations as cycles
from harness import storage_calculations as storage
from harness import storage_costs
from harness import solve_validity
from harness import time_to_power_calculations as time_to_power
from harness import system_cost_calculations as system_cost
from harness import renewable_share_calculations as renewable_share

PROFILE = "generator-dispatch/1"
MAX_SNAPSHOTS = 512
METRICS = {
    "dispatch_energy": {
        "formula_id": "generator-energy/1",
        "unit": "MWh",
        "label": "Generator energy",
        "inputs": ("p", "energy_weight"),
    },
    "marginal_expenditure": {
        "formula_id": "generator-marginal-expenditure/1",
        "unit": "EUR",
        "label": "Marginal dispatch expenditure",
        "inputs": ("p", "marginal_cost", "objective_weight"),
    },
    "peak_dispatch": {
        "formula_id": "generator-peak/1",
        "unit": "MW",
        "label": "Peak generator output",
        "inputs": ("p",),
    },
}
COST_METRICS = {
    "annual_fixed_cost": {
        "formula_id": "annual-capacity-cost/1",
        "unit": "EUR/year",
        "label": "Annual fixed capacity cost",
        "dependencies": (),
    },
    "allocated_fixed_cost": {
        "formula_id": "allocated-capacity-cost/1",
        "unit": "EUR",
        "label": "Allocated fixed capacity cost",
        "dependencies": ("annual_fixed_cost",),
    },
    "total_cost": {
        "formula_id": "linear-total-cost/1",
        "unit": "EUR",
        "label": "Total cost on the declared basis",
        "dependencies": ("allocated_fixed_cost", "marginal_expenditure"),
    },
}
COMPARISON_METRICS = {
    "cost_delta": {
        "formula_id": "candidate-minus-baseline-cost/1",
        "unit": "EUR",
        "label": "Cost change (candidate minus baseline)",
        "dependencies": ("total_cost",),
    },
    "cost_change_percent": {
        "formula_id": "cost-change-baseline-denominator/1",
        "unit": "%",
        "label": "Cost change relative to baseline",
        "dependencies": ("cost_delta",),
    },
}
DEFINITIONS = {**METRICS, **COST_METRICS, **COMPARISON_METRICS, **storage.METRICS, **storage_costs.METRICS,
               **carrier.METRICS, **branch.METRICS, **branch.NETWORK_METRICS, **ac_branch.METRICS,
               **lost_load.METRICS, **price.METRICS, **emissions.GENERATOR_METRICS,
               **emissions.NETWORK_METRICS, **curtailment.METRICS, **capacity.METRICS,
               **cycles.METRICS, **balance.METRICS, **site_import.METRICS, **investment.METRICS,
               **investment.LCOE_METRICS, **copt_screen.METRICS,
               **readiness.METRICS, **time_to_power.METRICS, **losses.METRICS, **system_cost.METRICS,
               **renewable_share.METRICS}
COST_PROFILES = frozenset({"generator-cost/1", "network-linear-cost/1", storage_costs.PROFILE})
# AA-09 S2: a retained baseline is comparable only on these scope keys, the same
# window identity (fact_identity.window: boundaries, snapshot count, weight basis)
# and the compared metric's unit. The conversation and the calculation share them.
COMPARISON_KEYS = ("profile", "project_id", "entity_id", "start", "end", "cost_basis")
COMPARED_METRIC = "total_cost"
STORAGE_PROFILES = frozenset({storage.PROFILE, storage_costs.PROFILE})
PROFILES = {
    PROFILE: tuple(METRICS),
    "generator-cost/1": tuple(METRICS) + tuple(COST_METRICS),
    "network-linear-cost/1": tuple(METRICS) + tuple(COST_METRICS),
    storage.PROFILE: tuple(storage.METRICS),
    storage_costs.PROFILE: tuple(storage.METRICS) + tuple(storage_costs.METRICS) + tuple(COST_METRICS),
    carrier.PROFILE: tuple(carrier.METRICS),
    branch.PROFILE: tuple(branch.METRICS),
    branch.NETWORK_PROFILE: tuple(branch.NETWORK_METRICS),
    ac_branch.PROFILE: tuple(ac_branch.METRICS),
    lost_load.PROFILE: tuple(lost_load.METRICS),
    price.PROFILE: tuple(price.METRICS),
    emissions.GENERATOR_PROFILE: tuple(emissions.GENERATOR_METRICS),
    emissions.NETWORK_PROFILE: tuple(emissions.NETWORK_METRICS),
    curtailment.PROFILE: tuple(curtailment.METRICS),
    losses.PROFILE: tuple(losses.METRICS),
    capacity.PROFILE: capacity.DEFAULT_METRICS,
    cycles.PROFILE: tuple(cycles.METRICS),
    balance.PROFILE: tuple(balance.METRICS),
    site_import.PROFILE: tuple(site_import.METRICS),
    investment.PROFILE: tuple(investment.METRICS),
    investment.LCOE_PROFILE: tuple(investment.LCOE_METRICS),
    copt_screen.PROFILE: tuple(copt_screen.METRICS),
    investment.PROFILE_V2: tuple(investment.METRICS_V2),
    investment.LCOE_PROFILE_V2: tuple(investment.LCOE_METRICS_V2),
    copt_screen.PROFILE_V2: tuple(copt_screen.METRICS_V2),
    readiness.PROFILE: tuple(readiness.METRICS),
    time_to_power.PROFILE: tuple(time_to_power.METRICS),
    system_cost.PROFILE: tuple(system_cost.METRICS),
    renewable_share.PROFILE: tuple(renewable_share.METRICS),
}
# Profiles added after the pilot's first five run only when listed explicitly
# in PYPSA_GUI_ANSWER_ASSURANCE_PROFILES (`rollout.profile_enabled`).
OPT_IN_PROFILES = frozenset({carrier.PROFILE, *branch.PROFILES, *ac_branch.PROFILES, lost_load.PROFILE, price.PROFILE,
                             *emissions.PROFILES, curtailment.PROFILE, capacity.PROFILE,
                             cycles.PROFILE, balance.PROFILE, site_import.PROFILE,
                             *investment.PROFILES, *copt_screen.PROFILES,
                             readiness.PROFILE, time_to_power.PROFILE, losses.PROFILE, system_cost.PROFILE,
                             renewable_share.PROFILE})
DISCLOSURE = (
    "Energy uses generator snapshot weights; expenditure uses objective snapshot weights. "
    "Expenditure excludes fixed, startup, shutdown and quadratic costs; it is not total system cost. "
    "Currency follows the model's EUR convention; no price year is inferred."
)


def _number(value):
    try:
        if isinstance(value, bool) or not isinstance(value, Real):
            return None
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _series(component, attribute, entity, index):
    dynamic = component.dynamic.get(attribute)
    if dynamic is not None and entity in dynamic.columns:
        if not dynamic.index.equals(index) or not dynamic.index.is_unique:
            raise ValueError("misaligned_source")
        return [_number(v) for v in dynamic[entity]]
    return [_number(component.static.at[entity, attribute])] * len(index)


def solve_matches_inputs(ctx, after_pf=False):
    """Both recorded solve fingerprints match the current inputs: the lossy one
    and the exact one the Load flow tab requires. After an AC power flow the
    live network keeps the LP's tables (owner decision PF-SAVE-1: the flow's
    live only in `ac_pf_results`), so the LP solve stays current through a
    flow chained to it (`lopf+ac_pf_ok`) and through a failed one; a flow
    record needs the LP's record beside it. A standalone flow (condition
    `ac_pf_ok`) reads not current to an LP-only reader: it fails closed.
    `after_pf` (study harness 15) also asks that a flow there ran on the
    current edit counter and succeeded (a PF result, never an LP one)."""
    n, state = ctx.network, ctx.solver_state
    flowed = state.get("ac_pf_results") is not None
    condition = state.get("condition")
    if after_pf and flowed and (
        state.get("ac_pf_network_revision") != int(getattr(ctx, "network_revision", 0) or 0)
        or condition != "lopf+ac_pf_ok"
    ):
        return False
    from services.run_grid_pf import AC_PF_FAILED

    # A flow that failed restores the LP's tables too (`ac_pf_service.run_ac_pf_stage`): the
    # failure is judged by the PF rules alone (GK B SH-15 (1)); no other suffix is accepted.
    lp_kept = (
        condition == "optimal"
        or (flowed and condition == "lopf+ac_pf_ok")
        or (isinstance(condition, str) and condition.startswith(AC_PF_FAILED))
    )
    return (
        # A flow keeps the LP's record beside its own; an LP record alone is no solve's.
        flowed == (state.get("lopf_results") is not None)
        and state.get("status") == "completed"
        and lp_kept
        and n.meta.get("assistant_solve_input_fingerprint")
        == workflow_tools._network_input_fingerprint(ctx)
        and n.meta.get("assistant_solve_exact_input_fingerprint")
        == workflow_tools._network_input_fingerprint(ctx, exact=True)
    )


def source(ctx, entity):
    """Called under the network lock. Preserve missing values rather than fill."""
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
    generators = n.components["Generator"]
    if entity not in generators.static.index:
        raise ValueError("unknown_generator")
    weights = n.snapshot_weightings
    dispatch = generators.dynamic.get("p")
    if (
        not weights.index.equals(index)
        or not weights.index.is_unique
        or dispatch is None
        or not dispatch.index.equals(index)
        or not dispatch.index.is_unique
        or not dispatch.columns.is_unique
        or set(dispatch.columns) != set(generators.static.index)
    ):
        raise ValueError("misaligned_source")
    if entity not in dispatch.columns:
        raise ValueError("missing_dispatch")
    configured = generators.static.loc[entity]
    if _number(configured["sign"]) != 1:
        raise ValueError("unsupported_generator_sign")
    capacity = _number(
        configured["p_nom_opt"]
        if configured["p_nom_extendable"]
        else configured["p_nom"]
    )
    inputs = {
        "p": [_number(v) for v in dispatch[entity]],
        "energy_weight": [_number(v) for v in weights["generators"]],
        "objective_weight": [_number(v) for v in weights["objective"]],
        "marginal_cost": _series(generators, "marginal_cost", entity, index),
        "p_min": _series(generators, "p_min_pu", entity, index),
        "p_max": _series(generators, "p_max_pu", entity, index),
    }
    fingerprint = workflow_tools._network_input_fingerprint(ctx)
    state = ctx.solver_state
    valid_solve = solve_matches_inputs(ctx)
    return {
        "index": [v.isoformat() for v in index],
        "inputs": inputs,
        "capacity": capacity,
        "input_fingerprint": fingerprint,
        "solved_fingerprint": n.meta.get("assistant_solve_input_fingerprint"),
        "status": state.get("status"),
        "condition": state.get("condition"),
        "valid_solve": valid_solve,
        "profile": PROFILE,
        "capital_cost": _number(configured["capital_cost"]),
        "linear_cost": not bool(configured["committable"])
        and all(
            v == 0
            for v in _series(generators, "marginal_cost_quadratic", entity, index)
        ),
        "bus": str(configured["bus"]),
    }


def calculate(raw, entity):
    """Formula-owned masks, complete populations, and independently testable sums."""
    inputs = raw["inputs"]
    count = len(raw["index"])
    capacity = raw["capacity"]
    facts, slots = [], []
    for metric, definition in METRICS.items():
        valid = []
        for i in range(count):
            p, lo, hi = (inputs[key][i] for key in ("p", "p_min", "p_max"))
            ok = (
                raw["valid_solve"]
                and inputs.get("physical_ok", [True] * count)[i]
                and capacity is not None
                and capacity >= 0
                and p is not None
                and lo is not None
                and hi is not None
                and 0 <= lo <= hi
                and capacity * lo - 1e-8 <= p <= capacity * hi + 1e-8
            )
            ok = ok and all(inputs[key][i] is not None for key in definition["inputs"])
            for weight in ("energy_weight", "objective_weight"):
                if weight in definition["inputs"]:
                    ok = ok and inputs[weight][i] is not None and inputs[weight][i] > 0
            if ok:
                valid.append(i)

        def aggregate(indices):
            if metric == "peak_dispatch":
                return max(inputs["p"][i] for i in indices)
            return math.fsum(
                math.prod(inputs[key][i] for key in definition["inputs"])
                for i in indices
            )

        complete = len(valid) == count
        try:
            value = aggregate(valid) if valid else None
        except (OverflowError, ValueError):
            value, complete = None, False
        if value is not None and not math.isfinite(value):
            value, complete = None, False
        slot = {
            "metric": metric,
            "disposition": "answered" if complete else "unavailable",
            "valid_snapshots": len(valid),
            "requested_snapshots": count,
            "excluded_indices": [
                raw["index"][i] for i in range(count) if i not in valid
            ],
        }
        if complete:
            facts.append(
                {
                    "path": "/" + metric,
                    "entity_id": entity,
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
                else "invalid_or_missing_raw_data"
            )
            # A subset is explicitly labelled, never available as a full-horizon fact.
            slot["partial_value"] = value
        slots.append(slot)
    return facts, slots


# The version of the checked-answer text renderer (`assurance.Turn.validate_profile`).
# Bump it when that renderer changes how a checked value is worded; every
# profile's definition hash then changes with it.
RENDERER_VERSION = 1


def _no_floats(value):
    """Refuse a float anywhere in the hash input: its JSON spelling is not canonical."""
    if isinstance(value, float):
        raise TypeError("definition_hash_float")
    if isinstance(value, dict):
        for item in value.values():
            _no_floats(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _no_floats(item)


def definition_hash(profile, metrics=None):
    """``"sha256:<hex>"`` over the profile's metrics in order (or `metrics`,
    the ones a scope requested, in their requested order), each metric's
    label, unit and formula_id (as `definition` gives them for this profile) and
    `RENDERER_VERSION`; canonical JSON (sorted keys, no spaces, UTF-8).

    The hash covers formula ids, not formula code: a formula change must bump
    its formula_id. A metric without a definition (the "unsupported" fallback)
    and an unknown profile are refused, never hashed; so is a float input.
    """
    rows = []
    for metric in PROFILES[profile] if metrics is None else metrics:
        d = definition(metric, profile)
        if d["formula_id"] == "unsupported":
            raise ValueError("unsupported_metric_definition")
        rows.append({"metric": metric, "label": d["label"], "unit": d["unit"],
                     "formula_id": d["formula_id"]})
    payload = {"metrics": rows, "renderer_version": RENDERER_VERSION}
    _no_floats(payload)
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


# A /2 profile names the same metrics as its /1 predecessor with the ruled figure labels (AA 28).
PROFILE_DEFINITIONS = {
    investment.PROFILE_V2: investment.METRICS_V2,
    investment.LCOE_PROFILE_V2: investment.LCOE_METRICS_V2,
    copt_screen.PROFILE_V2: copt_screen.METRICS_V2,
}
# successor -> predecessor: listing the /1 id in PYPSA_GUI_ANSWER_ASSURANCE_PROFILES enables its /2.
PREDECESSORS = {
    investment.PROFILE_V2: investment.PROFILE,
    investment.LCOE_PROFILE_V2: investment.LCOE_PROFILE,
    copt_screen.PROFILE_V2: copt_screen.PROFILE,
}


def definition(metric, profile=None):
    if metric == "cost_delta":
        # AA-09 S2: a delta keeps the compared metric's unit; a change of a
        # percentage is in percentage points.
        unit = definition(COMPARED_METRIC, profile)["unit"]
        return {**COMPARISON_METRICS[metric], "unit": "pp" if unit == "%" else unit}
    if profile == storage_costs.PROFILE and metric == "total_cost":
        return storage_costs.TOTAL_DEFINITION
    if profile in PROFILE_DEFINITIONS and metric in PROFILE_DEFINITIONS[profile]:
        return PROFILE_DEFINITIONS[profile][metric]
    return DEFINITIONS.get(
        metric, {"label": metric, "unit": "", "formula_id": "unsupported"}
    )


def slot_warnings(slots):
    """The "<metric>: <reason>" line for each slot not answered: the answer's
    receipt limitations and the checked chart's warnings (AA-05 S5b B3)."""
    return [s["metric"] + ": " + s["reason"] for s in slots if s["disposition"] != "answered"]


# Result slots a receipt admits (`assurance_shapes.Receipt.result_slots`).
MAX_RESULT_SLOTS = 20


def required_warnings(raw, scope):
    """The required warning slots of a profile's evidence (AA-13, A2.8): [] for
    every profile but `ac-branch-loading/1`. Each one stays in the receipt's
    `result_slots`, before the metric slots, whatever metrics were requested,
    so `request_complete` stays false while it is there.

    The cap rule: a warning counts toward the twenty result slots and is never
    dropped. So a request whose metrics plus its warnings exceed twenty is
    refused at capture (`too_many_metrics_for_warnings`), before any slot is
    built: with one warning, nineteen metrics are checked and twenty refused.
    """
    from harness.assurance_scope import every_entity

    if every_entity(scope) or scope["profile"] not in ac_branch.PROFILES:
        return []
    warnings = ac_branch.required_warnings(raw, scope)
    if warnings and len(requested_metrics(scope)) + len(warnings) > MAX_RESULT_SLOTS:
        raise ValueError("too_many_metrics_for_warnings")
    return warnings


def requested_metrics(scope):
    return scope.get("metrics", PROFILES[scope["profile"]])


def scope_definition_hash(scope):
    """`definition_hash` over the metrics `scope` requests (comparison and
    optional metrics included), or None when one has no definition: such a
    record is captured with its hash not recorded, never refused here."""
    try:
        return definition_hash(scope["profile"], requested_metrics(scope))
    except ValueError:
        return None


def _window(raw, scope):
    if "start" not in scope:
        return raw
    # Only exact existing snapshot boundaries; no inferred timezone/window.
    start, end = (
        pd.Timestamp(scope["start"]).isoformat(),
        pd.Timestamp(scope["end"]).isoformat(),
    )
    a, b = raw["index"].index(start), raw["index"].index(end)
    if b < a:
        raise ValueError("reversed_window")
    return {
        **raw,
        "index": raw["index"][a : b + 1],
        "inputs": {k: v[a : b + 1] for k, v in raw["inputs"].items()},
    }


def _every_source(ctx, scope):
    """Every entity of the scope's class, sorted, counted against the answer bound
    before any entity is read or calculated (issue 10 K1, K4)."""
    from harness import answer_sections

    names = sorted(str(name) for name in ctx.network.components[scope["component_class"]].static.index)
    if not names:
        raise ValueError("empty_population")
    # A receipt slot names its entity in at most 128 characters (UTF-16 units in the
    # browser); refuse before any calculation rather than publish an answer that fails read-back.
    if any(len(name.encode("utf-16-le")) // 2 > 128 for name in names):
        raise ValueError("population_name_too_long")
    answer_sections.admit(len(names), len(requested_metrics(scope)))
    if scope["profile"] == PROFILE:
        members = {name: {"generators": {name: source(ctx, name)}, "passive_costs": [],
                          "unsupported_cost_components": []} for name in names}
        index = members[names[0]]["generators"][names[0]]["index"]
    else:
        members = {name: branch.source(ctx, name, False, scope["component_class"]) for name in names}
        index = members[names[0]]["index"]
    population = {"version": 1, "component_class": scope["component_class"], "entities": names,
                  "index": index, "members": members}
    if scope["profile"] != PROFILE:
        # branch-loading/1 "*" covers Lines; Transformers are named as excluded.
        population["transformers_excluded"] = len(ctx.network.components["Transformer"].static)
    return population


def _every_calculate(raw, scope):
    """Each member through its own one-entity profile; a fact's path names its member."""
    if raw.get("version") != 1 or raw.get("component_class") != scope["component_class"]:
        raise ValueError("population_source_identity")
    facts, slots = [], []
    for i, name in enumerate(raw["entities"]):
        member_facts, member_slots = scoped_calculate(raw["members"][name], {**scope, "entity_id": name})
        facts += [{**f, "path": f"/{i}{f['path']}"} for f in member_facts]
        slots += member_slots
    return facts, slots


def scoped_source(ctx, scope, owner=None, investment_state=None):
    """Capture complete raw populations under the existing project lock, with
    the solve's LP/MIP classification and gap (assistant-assurance 10, S2)."""
    raw = _scoped_source(ctx, scope, owner, investment_state)
    if isinstance(raw, dict):
        raw["solve"] = solve_validity.capture(ctx)
    return raw


def _scoped_source(ctx, scope, owner=None, investment_state=None):
    from harness.assurance_scope import every_entity

    if every_entity(scope):
        return _every_source(ctx, scope)
    if scope["profile"] == carrier.PROFILE:
        return carrier.source(ctx, scope["entity_id"])
    if scope["profile"] == lost_load.PROFILE:
        return lost_load.source(ctx, scope["entity_id"])
    if scope["profile"] == price.PROFILE:
        return price.source(ctx, scope["entity_id"])
    if scope["profile"] in emissions.PROFILES:
        return emissions.source(ctx, scope["entity_id"], scope["profile"] == emissions.NETWORK_PROFILE)
    if scope["profile"] == curtailment.PROFILE:
        return curtailment.source(ctx, scope["entity_id"])
    if scope["profile"] == losses.PROFILE:
        return losses.source(ctx, scope["entity_id"])
    if scope["profile"] == capacity.PROFILE:
        return capacity.source(ctx, scope["entity_id"], scope.get("component_class"))
    if scope["profile"] == cycles.PROFILE:
        return cycles.source(ctx, scope["entity_id"], scope.get("component_class"))
    if scope["profile"] == balance.PROFILE:
        return balance.source(ctx, scope["entity_id"])
    if scope["profile"] == site_import.PROFILE:
        return site_import.source(ctx, scope["entity_id"])
    if scope["profile"] in copt_screen.PROFILES:
        return copt_screen.source(ctx, scope["entity_id"], scope["profile"])
    if scope["profile"] == readiness.PROFILE:
        return readiness.source(ctx, scope["entity_id"])
    if scope["profile"] == time_to_power.PROFILE:
        return time_to_power.source(ctx, scope["entity_id"])
    if scope["profile"] == system_cost.PROFILE:
        return system_cost.source(ctx, scope["entity_id"])
    if scope["profile"] == renewable_share.PROFILE:
        return renewable_share.source(ctx, scope["entity_id"])
    if scope["profile"] in investment.PROFILES:
        return investment.source(ctx, scope["entity_id"], investment_state, scope["profile"])
    if scope["profile"] in branch.PROFILES:
        return branch.source(ctx, scope["entity_id"], scope["profile"] == branch.NETWORK_PROFILE,
                             scope.get("component_class"))
    if scope["profile"] in ac_branch.PROFILES:
        return ac_branch.source(ctx, scope["entity_id"], scope.get("component_class"))
    if scope["profile"] in STORAGE_PROFILES:
        adapter = storage_costs if scope["profile"] == storage_costs.PROFILE else storage
        return _comparison({"storage_unit": adapter.source(ctx, scope["entity_id"])}, scope, owner)
    if scope["profile"] != "network-linear-cost/1":
        return _comparison(
            {
                "generators": {scope["entity_id"]: source(ctx, scope["entity_id"])},
                "passive_costs": [],
                "unsupported_cost_components": [],
            },
            scope,
            owner,
        )
    n = ctx.network
    names = list(n.generators.index)
    if not 1 <= len(names) <= 32:
        raise ValueError("unsupported_generator_population")
    passive = []
    for component_class in ("Line", "Transformer"):
        for name, row in n.components[component_class].static.iterrows():
            passive.append(
                {
                    "class": component_class,
                    "name": name,
                    "capacity": _number(
                        row["s_nom_opt"] if row["s_nom_extendable"] else row["s_nom"]
                    ),
                    "capital_cost": _number(row["capital_cost"]),
                }
            )
    if len(passive) > 64:
        raise ValueError("unsupported_branch_population")
    # These components have distinct intertemporal/commitment/cost semantics.
    # Their mere presence prevents a complete network total, even at zero cost.
    unsupported = [
        c
        for c in ("Link", "Store", "StorageUnit", "ShuntImpedance")
        if len(n.components[c].static)
    ]
    raw = {
        "generators": {name: source(ctx, name) for name in names},
        "passive_costs": passive,
        "unsupported_cost_components": unsupported,
    }
    if not unsupported:
        raw["balance"] = _balance_source(ctx)
    return _comparison(raw, scope, owner)


def _balance_source(ctx):
    """Original loads and both branch terminals for a bounded DC-LP check."""
    n = ctx.network
    if len(n.buses) > 64 or len(n.loads) > 64:
        raise ValueError("unsupported_balance_population")
    loads = n.components["Load"]
    result = {"buses": list(n.buses.index), "loads": [], "branches": []}
    for name, row in loads.static.iterrows():
        result["loads"].append(
            {
                "bus": row["bus"],
                "sign": _number(row["sign"]),
                "p": _series(loads, "p_set", name, n.snapshots),
            }
        )
    for component_class in ("Line", "Transformer"):
        component = n.components[component_class]
        for name, row in component.static.iterrows():
            result["branches"].append(
                {
                    "bus0": row["bus0"],
                    "bus1": row["bus1"],
                    "p0": _series(component, "p0", name, n.snapshots),
                    "p1": _series(component, "p1", name, n.snapshots),
                    "capacity": _number(
                        row["s_nom_opt"] if row["s_nom_extendable"] else row["s_nom"]
                    ),
                    "maximum": _series(component, "s_max_pu", name, n.snapshots),
                }
            )
    return result


def _balance_mask(raw):
    balance = raw["balance"]
    count = len(next(iter(raw["generators"].values()))["index"])
    masks = []
    for i in range(count):
        terms = {bus: [] for bus in balance["buses"]}
        valid = True
        for generator in raw["generators"].values():
            p = generator["inputs"]["p"][i]
            if p is None or generator["bus"] not in terms:
                valid = False
            else:
                terms[generator["bus"]].append(p)
        for load in balance["loads"]:
            p, sign = load["p"][i], load["sign"]
            if p is None or sign is None or load["bus"] not in terms:
                valid = False
            else:
                terms[load["bus"]].append(sign * p)
        for flow in balance["branches"]:
            p0, p1, capacity, maximum = (
                flow["p0"][i],
                flow["p1"][i],
                flow["capacity"],
                flow["maximum"][i],
            )
            if (
                any(v is None for v in (p0, p1, capacity, maximum))
                or capacity < 0
                or maximum < 0
                or max(abs(p0), abs(p1)) > capacity * maximum + 1e-6
                or flow["bus0"] not in terms
                or flow["bus1"] not in terms
            ):
                valid = False
            else:
                terms[flow["bus0"]].append(-p0)
                terms[flow["bus1"]].append(-p1)
        try:
            valid = valid and all(
                abs(math.fsum(values))
                <= 1e-6 + 1e-8 * math.fsum(abs(v) for v in values)
                for values in terms.values()
            )
        except (OverflowError, ValueError):
            valid = False
        masks.append(bool(valid))
    return masks


def _comparison(raw, scope, owner):
    if not scope.get("comparison_evidence_id"):
        return raw
    from services import assurance_records as records

    if owner is None:
        raise ValueError("comparison_owner_required")
    baseline = records.evidence(owner, scope["comparison_evidence_id"])
    base_scope = baseline.get("request_scope", {})
    if (
        not base_scope.get("retain")
        or base_scope.get("comparison_evidence_id")
        or scope["profile"] == PROFILE
        or comparison_mismatch(raw, scope, baseline)
    ):
        raise ValueError("incompatible_comparison_basis")
    return {
        **raw,
        "comparison": {
            "evidence_id": baseline["evidence_id"],
            "source_sha256": baseline["source_sha256"],
            "raw": baseline["raw"],
            "scope": base_scope,
        },
    }


def _comparison_index(raw, scope):
    """The candidate's captured snapshot index, as the evidence record reads it."""
    if scope["profile"] in STORAGE_PROFILES:
        return raw["storage_unit"]["index"]
    return next(iter(raw["generators"].values()))["index"]


def _period(raw, scope):
    """The in-window snapshot index and objective weights, exactly as captured.

    Only the comparable cost profiles' raw shapes are read; any other shape, a
    population whose members disagree or a window not in the index raises."""
    if scope["profile"] == storage_costs.PROFILE:
        unit = raw["storage_unit"]
        pairs = [(unit["index"], unit["costs"]["objective_weight"])]
    elif scope["profile"] in ("generator-cost/1", "network-linear-cost/1"):
        pairs = [(g["index"], g["inputs"]["objective_weight"]) for g in raw["generators"].values()]
    else:
        raise ValueError("unknown_period_shape")
    index, weights = pairs[0]
    if any(p != pairs[0] for p in pairs) or len(index) != len(weights):
        raise ValueError("unknown_period_shape")
    a = index.index(scope["start"]) if "start" in scope else 0
    b = index.index(scope["end"]) + 1 if "end" in scope else len(index)
    if b <= a:
        raise ValueError("unknown_period_shape")
    return list(index[a:b]), list(weights[a:b])


def comparison_mismatch(raw, scope, baseline):
    """The first of window/basis/period/unit on which a sealed baseline record
    differs from the candidate (its raw and scope), or None when comparable.

    Period is the record's stored window (AA-05 S5a) and, exactly, the two raws'
    in-window snapshot index and objective weights; anything unreadable is a
    mismatch, never a match. Unit is the baseline's sealed fact unit against the
    candidate's current definition."""
    from harness import fact_identity

    base_scope = baseline.get("request_scope") or {}
    for key in COMPARISON_KEYS:
        if base_scope.get(key) != scope.get(key):
            return key
    try:
        base_window = baseline["window"]
        window = fact_identity.window(_comparison_index(raw, scope), scope)
        if base_window["snapshots"] is None or any(
            base_window[k] != window[k] for k in ("start", "end", "snapshots")
        ):
            return "period"
        base_basis = base_window["weight_basis"].get(COMPARED_METRIC)
        if base_basis is not None and base_basis != window["weight_basis"].get(COMPARED_METRIC):
            return "period"
        if _period(raw, scope) != _period(baseline["raw"], base_scope):
            return "period"
    except (KeyError, TypeError, ValueError, AttributeError, StopIteration, IndexError):
        return "period"
    # A baseline without the compared fact stays comparable: its slot reads
    # unavailable (baseline_total_unavailable), never 0.
    units = {f.get("unit") for f in baseline.get("facts") or () if f.get("metric") == COMPARED_METRIC}
    if units - {definition(COMPARED_METRIC, scope["profile"])["unit"]}:
        return "unit"
    return None


def scoped_calculate(raw, scope):
    """A bounded calculation DAG; missing dependencies never become zero."""
    from harness.assurance_scope import every_entity

    if every_entity(scope):
        return _every_calculate(raw, scope)
    if scope["profile"] == carrier.PROFILE:
        return carrier.calculate(raw, scope)
    if scope["profile"] in branch.PROFILES:
        return branch.calculate(raw, scope)
    if scope["profile"] in ac_branch.PROFILES:
        return ac_branch.calculate(raw, scope)
    if scope["profile"] == lost_load.PROFILE:
        return lost_load.calculate(raw, scope)
    if scope["profile"] == price.PROFILE:
        return price.calculate(raw, scope)
    if scope["profile"] in emissions.PROFILES:
        return emissions.calculate(raw, scope)
    if scope["profile"] == curtailment.PROFILE:
        return curtailment.calculate(raw, scope)
    if scope["profile"] == losses.PROFILE:
        return losses.calculate(raw, scope)
    if scope["profile"] == capacity.PROFILE:
        return capacity.calculate(raw, scope)
    if scope["profile"] == cycles.PROFILE:
        return cycles.calculate(raw, scope)
    if scope["profile"] == balance.PROFILE:
        return balance.calculate(raw, scope)
    if scope["profile"] == site_import.PROFILE:
        return site_import.calculate(raw, scope)
    if scope["profile"] in investment.PROFILES:
        return investment.calculate(raw, scope)
    if scope["profile"] in copt_screen.PROFILES:
        return copt_screen.calculate(raw, scope)
    if scope["profile"] == readiness.PROFILE:
        return readiness.calculate(raw, scope)
    if scope["profile"] == time_to_power.PROFILE:
        return time_to_power.calculate(raw, scope)
    if scope["profile"] == system_cost.PROFILE:
        return system_cost.calculate(raw, scope)
    if scope["profile"] == renewable_share.PROFILE:
        return renewable_share.calculate(raw, scope)
    if scope["profile"] == storage.PROFILE:
        return storage.calculate(raw["storage_unit"], scope)
    if scope["profile"] == storage_costs.PROFILE:
        return storage_costs.calculate(raw, scope)
    metrics = requested_metrics(scope)
    generators = raw["generators"]
    if "balance" in raw:
        mask = _balance_mask(raw)
        generators = {
            name: {**value, "inputs": {**value["inputs"], "physical_ok": mask}}
            for name, value in generators.items()
        }
    populations = [_window(value, scope) for value in generators.values()]
    if not populations:
        raise ValueError("empty_population")
    if any(p["index"] != populations[0]["index"] for p in populations):
        raise ValueError("misaligned_population")
    count = len(populations[0]["index"])
    inputs = {
        name: calculate(_window(value, scope), name)
        for name, value in generators.items()
    }
    available = {}
    reasons = {}
    for metric in METRICS:
        values = [
            next((f["value"] for f in facts if f["metric"] == metric), None)
            for facts, _ in inputs.values()
        ]
        if all(v is not None for v in values):
            try:
                value = max(values) if metric == "peak_dispatch" else math.fsum(values)
                if math.isfinite(value):
                    available[metric] = value
            except (OverflowError, ValueError):
                pass
        reasons[metric] = "invalid_or_missing_raw_data"
    if scope["profile"] == "network-linear-cost/1":
        # A network peak is max(sum(p_t)), not sum(generator peaks).
        if "peak_dispatch" in available:
            available["peak_dispatch"] = max(
                math.fsum(p["inputs"]["p"][i] for p in populations)
                for i in range(count)
            )
    basis = scope.get("cost_basis")
    cost_safe = (
        basis is not None
        and not raw["unsupported_cost_components"]
        and all(p["valid_solve"] and p["linear_cost"] for p in populations)
    )
    for metric in COST_METRICS:
        reasons[metric] = (
            "explicit_cost_basis_required"
            if basis is None
            else "unsupported_or_invalid_cost_population"
        )
    # A nonlinear dispatch cost invalidates expenditure as well as its total.
    if scope["profile"] != PROFILE and not cost_safe:
        available.pop("marginal_expenditure", None)
        reasons["marginal_expenditure"] = reasons["total_cost"]
    capacity_terms = [(p["capacity"], p["capital_cost"]) for p in populations]
    capacity_terms += [(p["capacity"], p["capital_cost"]) for p in raw["passive_costs"]]
    if cost_safe and all(
        c is not None and c >= 0 and k is not None and k >= 0 for c, k in capacity_terms
    ):
        try:
            available["annual_fixed_cost"] = math.fsum(c * k for c, k in capacity_terms)
            available["allocated_fixed_cost"] = (
                available["annual_fixed_cost"] * basis["annual_fixed_cost_fraction"]
            )
            if "marginal_expenditure" in available:
                available["total_cost"] = math.fsum(
                    [
                        available["allocated_fixed_cost"],
                        available["marginal_expenditure"],
                    ]
                )
        except (OverflowError, ValueError):
            pass
    _comparison_values(raw, available, reasons)
    allowed = PROFILES[scope["profile"]] + (
        tuple(COMPARISON_METRICS) if scope["profile"] != PROFILE else ()
    )
    facts, slots = [], []
    for metric in metrics:
        value = available.get(metric) if metric in allowed else None
        ok = value is not None and math.isfinite(value)
        slot = {
            "metric": metric,
            "disposition": "answered" if ok else "unavailable",
            "requested_snapshots": count,
        }
        if metric in METRICS:
            excluded = set().union(
                *(
                    set(
                        next(s for s in slots if s["metric"] == metric)[
                            "excluded_indices"
                        ]
                    )
                    for _, slots in inputs.values()
                )
            )
            slot["valid_snapshots"] = count - len(excluded)
        if ok:
            d = definition(metric)
            facts.append(
                {
                    "path": "/" + metric,
                    "entity_id": scope["entity_id"],
                    "metric": metric,
                    "value": value,
                    "unit": d["unit"],
                    "formula_id": d["formula_id"],
                }
            )
        else:
            slot["reason"] = reasons.get(metric, "unsupported_metric")
        slots.append(slot)
    return facts, slots


def _comparison_values(raw, available, reasons):
    """Shared same-basis candidate-minus-baseline accounting, never recursive."""
    if "comparison" not in raw:
        reasons.update({m: "compatible_retained_baseline_required" for m in COMPARISON_METRICS})
        return
    comparison = raw["comparison"]
    if "comparison" in comparison["raw"]:
        raise ValueError("nested_comparison")
    baseline_facts, _ = scoped_calculate(comparison["raw"], comparison["scope"])
    # A missing or None baseline total reads unavailable, never 0.
    base = next((f["value"] for f in baseline_facts if f["metric"] == COMPARED_METRIC), None)
    # A percent of a percent is not stated; the delta is in percentage points.
    percent = definition(COMPARED_METRIC, comparison["scope"]["profile"])["unit"] == "%"
    if base is not None and COMPARED_METRIC in available:
        available["cost_delta"] = available[COMPARED_METRIC] - base
        if base != 0 and not percent:
            available["cost_change_percent"] = available["cost_delta"] / abs(base) * 100
    reasons["cost_delta"] = "baseline_total_unavailable"
    reasons["cost_change_percent"] = (
        "derived_unit_mismatch" if percent else "baseline_denominator_zero_or_unavailable"
    )
    if "cost_delta" in available:
        # A delta inside the two solves' combined MIP gap has no established sign.
        limit = solve_validity.raw_delta_limit(
            available["cost_delta"], raw.get("solve"), comparison["raw"].get("solve"))
        if limit:
            for metric in ("cost_delta", "cost_change_percent"):
                available.pop(metric, None)
                reasons[metric] = limit


def _comparison_disclosure(scope):
    return (
        " Comparison uses candidate minus baseline, divided by the absolute baseline total for percentage change."
        if scope.get("comparison_evidence_id") else ""
    )


def disclosure(scope):
    if scope["profile"] == carrier.PROFILE:
        return carrier.DISCLOSURE
    if scope["profile"] in branch.PROFILES:
        return branch.DISCLOSURE
    if scope["profile"] in ac_branch.PROFILES:
        return ac_branch.DISCLOSURE
    if scope["profile"] == lost_load.PROFILE:
        return lost_load.DISCLOSURE
    if scope["profile"] == price.PROFILE:
        return price.DISCLOSURE
    if scope["profile"] in emissions.PROFILES:
        return emissions.DISCLOSURE
    if scope["profile"] == curtailment.PROFILE:
        return curtailment.DISCLOSURE
    if scope["profile"] == losses.PROFILE:
        return losses.DISCLOSURE
    if scope["profile"] == capacity.PROFILE:
        return capacity.disclosure(scope)
    if scope["profile"] == cycles.PROFILE:
        return cycles.DISCLOSURE
    if scope["profile"] == balance.PROFILE:
        return balance.DISCLOSURE
    if scope["profile"] == site_import.PROFILE:
        return site_import.DISCLOSURE
    if scope["profile"] in (investment.PROFILE, investment.PROFILE_V2):
        return investment.DISCLOSURE
    if scope["profile"] in investment.LCOE_PROFILES:
        return investment.LCOE_DISCLOSURE
    if scope["profile"] in copt_screen.PROFILES:
        return copt_screen.DISCLOSURE
    if scope["profile"] == readiness.PROFILE:
        return readiness.DISCLOSURE
    if scope["profile"] == time_to_power.PROFILE:
        return time_to_power.DISCLOSURE
    if scope["profile"] == system_cost.PROFILE:
        return system_cost.DISCLOSURE
    if scope["profile"] == renewable_share.PROFILE:
        return renewable_share.DISCLOSURE
    if scope["profile"] == storage_costs.PROFILE:
        return storage_costs.disclosure(scope)
    if scope["profile"] == storage.PROFILE:
        return storage.DISCLOSURE
    if scope["profile"] == PROFILE:
        return DISCLOSURE
    basis = scope.get("cost_basis")
    if not basis:
        return "Cost accounting requires an explicit currency, price year and annual fixed-cost allocation."
    return (
        f"Declared model basis: {basis['currency']}, price year {basis['price_year']}; "
        f"annual fixed-cost fraction {basis['annual_fixed_cost_fraction']}. "
        "Capital cost is treated as an annual capacity charge on total installed capacity; "
        "this is not the solver objective or a discounted NPV. Expenditure uses objective weights. "
        "The declared price year is an assumption, not a verified currency conversion. "
        "Only linear generator operation and passive branch capacity costs are supported."
        + _comparison_disclosure(scope)
    )
