"""Bounded, raw-source generator calculations; never a total-system-cost alias."""

from __future__ import annotations

import math
from numbers import Real

import pandas as pd

from services import workflow_tools
from harness import branch_calculations as branch
from harness import capacity_calculations as capacity
from harness import carrier_calculations as carrier
from harness import copt_calculations as copt_screen
from harness import curtailment_calculations as curtailment
from harness import emissions_calculations as emissions
from harness import energy_balance_calculations as balance
from harness import investment_calculations as investment
from harness import lost_load_calculations as lost_load
from harness import price_calculations as price
from harness import site_import_calculations as site_import
from harness import storage_cycles_calculations as cycles
from harness import storage_calculations as storage
from harness import storage_costs

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
               **carrier.METRICS, **branch.METRICS, **branch.NETWORK_METRICS,
               **lost_load.METRICS, **price.METRICS, **emissions.GENERATOR_METRICS,
               **emissions.NETWORK_METRICS, **curtailment.METRICS, **capacity.METRICS,
               **cycles.METRICS, **balance.METRICS, **site_import.METRICS, **investment.METRICS,
               **investment.LCOE_METRICS, **copt_screen.METRICS}
COST_PROFILES = frozenset({"generator-cost/1", "network-linear-cost/1", storage_costs.PROFILE})
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
    lost_load.PROFILE: tuple(lost_load.METRICS),
    price.PROFILE: tuple(price.METRICS),
    emissions.GENERATOR_PROFILE: tuple(emissions.GENERATOR_METRICS),
    emissions.NETWORK_PROFILE: tuple(emissions.NETWORK_METRICS),
    curtailment.PROFILE: tuple(curtailment.METRICS),
    capacity.PROFILE: tuple(capacity.METRICS),
    cycles.PROFILE: tuple(cycles.METRICS),
    balance.PROFILE: tuple(balance.METRICS),
    site_import.PROFILE: tuple(site_import.METRICS),
    investment.PROFILE: tuple(investment.METRICS),
    investment.LCOE_PROFILE: tuple(investment.LCOE_METRICS),
    copt_screen.PROFILE: tuple(copt_screen.METRICS),
}
# Profiles added after the pilot's first five run only when listed explicitly
# in PYPSA_GUI_ANSWER_ASSURANCE_PROFILES (`rollout.profile_enabled`).
OPT_IN_PROFILES = frozenset({carrier.PROFILE, *branch.PROFILES, lost_load.PROFILE, price.PROFILE,
                             *emissions.PROFILES, curtailment.PROFILE, capacity.PROFILE,
                             cycles.PROFILE, balance.PROFILE, site_import.PROFILE,
                             *investment.PROFILES, copt_screen.PROFILE})
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


def solve_matches_inputs(ctx):
    """Both recorded solve fingerprints match the current inputs: the lossy one
    and the exact one the Load flow tab requires. After an AC power flow the
    live outputs are PF values (ac_pf_service overwrites them and keeps the LP
    aside), so the live network no longer holds the LP solve."""
    n, state = ctx.network, ctx.solver_state
    return (
        state.get("ac_pf_results") is None
        and state.get("lopf_results") is None
        and state.get("status") == "completed"
        and state.get("condition") == "optimal"
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


def definition(metric, profile=None):
    if profile == storage_costs.PROFILE and metric == "total_cost":
        return storage_costs.TOTAL_DEFINITION
    return DEFINITIONS.get(
        metric, {"label": metric, "unit": "", "formula_id": "unsupported"}
    )


def requested_metrics(scope):
    return scope.get("metrics", PROFILES[scope["profile"]])


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


def scoped_source(ctx, scope, owner=None, investment_state=None):
    """Capture complete raw populations under the existing project lock."""
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
    if scope["profile"] == capacity.PROFILE:
        return capacity.source(ctx, scope["entity_id"], scope.get("component_class"))
    if scope["profile"] == cycles.PROFILE:
        return cycles.source(ctx, scope["entity_id"], scope.get("component_class"))
    if scope["profile"] == balance.PROFILE:
        return balance.source(ctx, scope["entity_id"])
    if scope["profile"] == site_import.PROFILE:
        return site_import.source(ctx, scope["entity_id"])
    if scope["profile"] == copt_screen.PROFILE:
        return copt_screen.source(ctx, scope["entity_id"])
    if scope["profile"] in investment.PROFILES:
        return investment.source(ctx, scope["entity_id"], investment_state, scope["profile"])
    if scope["profile"] in branch.PROFILES:
        return branch.source(ctx, scope["entity_id"], scope["profile"] == branch.NETWORK_PROFILE,
                             scope.get("component_class"))
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
        or any(
            base_scope.get(k) != scope.get(k)
            for k in (
                "profile",
                "project_id",
                "entity_id",
                "start",
                "end",
                "cost_basis",
            )
        )
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


def scoped_calculate(raw, scope):
    """A bounded calculation DAG; missing dependencies never become zero."""
    if scope["profile"] == carrier.PROFILE:
        return carrier.calculate(raw, scope)
    if scope["profile"] in branch.PROFILES:
        return branch.calculate(raw, scope)
    if scope["profile"] == lost_load.PROFILE:
        return lost_load.calculate(raw, scope)
    if scope["profile"] == price.PROFILE:
        return price.calculate(raw, scope)
    if scope["profile"] in emissions.PROFILES:
        return emissions.calculate(raw, scope)
    if scope["profile"] == curtailment.PROFILE:
        return curtailment.calculate(raw, scope)
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
    if scope["profile"] == copt_screen.PROFILE:
        return copt_screen.calculate(raw, scope)
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
    base = next((f["value"] for f in baseline_facts if f["metric"] == "total_cost"), None)
    if base is not None and "total_cost" in available:
        available["cost_delta"] = available["total_cost"] - base
        if base != 0:
            available["cost_change_percent"] = available["cost_delta"] / abs(base) * 100
    reasons["cost_delta"] = "baseline_total_unavailable"
    reasons["cost_change_percent"] = "baseline_denominator_zero_or_unavailable"


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
    if scope["profile"] == lost_load.PROFILE:
        return lost_load.DISCLOSURE
    if scope["profile"] == price.PROFILE:
        return price.DISCLOSURE
    if scope["profile"] in emissions.PROFILES:
        return emissions.DISCLOSURE
    if scope["profile"] == curtailment.PROFILE:
        return curtailment.DISCLOSURE
    if scope["profile"] == capacity.PROFILE:
        return capacity.DISCLOSURE
    if scope["profile"] == cycles.PROFILE:
        return cycles.DISCLOSURE
    if scope["profile"] == balance.PROFILE:
        return balance.DISCLOSURE
    if scope["profile"] == site_import.PROFILE:
        return site_import.DISCLOSURE
    if scope["profile"] == investment.PROFILE:
        return investment.DISCLOSURE
    if scope["profile"] == investment.LCOE_PROFILE:
        return investment.LCOE_DISCLOSURE
    if scope["profile"] == copt_screen.PROFILE:
        return copt_screen.DISCLOSURE
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
