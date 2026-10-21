"""Bounded COPT screening adequacy for checked answers (checked-results S4b).

The classical two-state convolution, rebuilt in plain numpy over integer
1 MW steps, independent of `services/adequacy/copt.py`'s table and helpers. The
engine's fleet split (which units are two-state and which are must-take) is
taken as the declared input; the residual load is recomputed here from the
network and must equal the engine's. Conventions are the engine's: LOLP(t) =
P[available < r(t)] with the grid index ceil(r/1 - 1e-12), EUE(t) =
sum over k < that index of p_k (r - k), hours with r <= 0 add nothing, and
LOLE and EUE are generator-weighted horizon totals. Fleets the plain table does
not describe (a profiled unit, a period-varying capacity, a folded static
availability, a capacity off the 1 MW grid) are refused, named.
"""

import math

import numpy as np

from harness import profile_common as common

PROFILE = "copt-screening/1"
DELTA_MW = 1.0
SNAP = 1e-12  # the engine's grid snap
REL_TOLERANCE = 1e-9
REFUSALS = frozenset({
    "network_scope_required", "no_copt_fleet", "unusable_outage_rate", "copt_conventions_not_reproduced",
    "unsupported_snapshot_scope", "copt_inputs_not_finite",
})
GRID_TOLERANCE = 1e-12  # the engine splits any larger fraction across two grid points
METRICS = {
    "screening_lole": {
        "formula_id": "copt-screening-lole/1",
        "unit": "h",
        "label": "Screening LOLE over the modelled horizon (COPT)",
    },
    "screening_eue": {
        "formula_id": "copt-screening-eue/1",
        "unit": "MWh",
        "label": "Screening EUE over the modelled horizon (COPT)",
    },
    "screening_lolp_max": {
        "formula_id": "copt-screening-lolp-max/1",
        "unit": "probability",
        "label": "Highest hourly loss-of-load probability (COPT)",
    },
}
DISCLOSURE = (
    "COPT screening (fidelity analytic_convolution): thermal two-state units only; storage, links, "
    "imports and the network are excluded, and must-take generation is netted at its availability. "
    "Not comparable to a statutory standard. LOLE and EUE are generator-weighted totals over the "
    "modelled horizon, not per year. Computed from the current network; no solve is needed."
)


def _residual(n, cfg):
    """Demand on electrical buses less must-take availability, recomputed here; the
    must-take membership is the engine's declared split."""
    from services.adequacy.copt import must_take_generators, solved_capacity
    from services.adequacy.demand import demand_frame_for
    from services.adequacy.metrics import electrical_columns

    snapshots = n.snapshots
    electrical = set(electrical_columns(n, list(n.buses.index)))
    frame = demand_frame_for(n, cfg, demand_scaled_in_place=False)
    demand = np.zeros(len(snapshots))
    for load in n.loads.index:
        if str(n.loads.at[load, "bus"]) not in electrical:
            continue
        if frame is not None and load in frame.columns:
            demand += frame[load].reindex(snapshots).fillna(0.0).to_numpy(dtype=float)
        else:
            demand += float(n.loads.at[load, "p_set"] or 0.0)
    p_max_pu = n.generators_t.p_max_pu
    take = np.zeros(len(snapshots))
    for g in must_take_generators(n):
        cap = solved_capacity(n.generators.loc[g])
        if g in p_max_pu.columns:
            take += np.maximum(p_max_pu[g].reindex(snapshots).fillna(0.0).to_numpy(dtype=float), 0.0) * cap
        else:
            static = float(n.generators.at[g, "p_max_pu"])
            take += max(static if math.isfinite(static) else 1.0, 0.0) * cap
    return demand - take


def _weights(n):
    """Generator weights times the period's years, rebuilt from the network."""
    w = n.snapshot_weightings["generators"].reindex(n.snapshots).to_numpy(dtype=float)
    if getattr(n.snapshots, "nlevels", 1) > 1:
        years = n.investment_period_weightings["years"]
        w = w * np.array([float(years.loc[p]) for p in n.snapshots.get_level_values(0)])
    return w


def _must_take_varies(n):
    """True when the walk gives any must-take generator a per-period capacity."""
    from services.adequacy.copt import _membership_walk
    from services.adequacy.metrics import electrical_columns

    electrical = set(electrical_columns(n, list(n.buses.index)))
    return any(row["source"] == "missing" and series is not None
               for _g, _cap, series, row in _membership_walk(n, electrical))


def source(ctx, entity):
    from services.adequacy.copt import fleet_and_residual
    from services.adequacy.copt_endpoint import build_copt_payload
    from services.pypsa_service import PyPSAService

    if entity != "network":
        raise ValueError("network_scope_required")
    n, cfg = ctx.network, ctx.solver_state.get("solver_config")
    try:
        units, engine_residual, weights = fleet_and_residual(n, cfg=cfg)
        payload = build_copt_payload(n, cfg, get_lock=PyPSAService.get_lock)
    except ValueError as exc:  # an outage rate outside [0, 1)
        raise ValueError("unusable_outage_rate") from exc
    if not units or payload is None:
        raise ValueError("no_copt_fleet")
    if payload["fleet"]["delta_mw"] != DELTA_MW or any(
        u.profile is not None or u.capacity_series is not None
        or (u.folded_constant is not None and u.folded_constant != 1)
        or abs(u.capacity_mw - round(u.capacity_mw)) >= GRID_TOLERANCE
        for u in units
    ) or _must_take_varies(n):
        raise ValueError("copt_conventions_not_reproduced")
    residual, weights_here = _residual(n, cfg), _weights(n)
    engine_weights = weights.reindex(n.snapshots).to_numpy(dtype=float)
    if not (np.isfinite(residual).all() and np.isfinite(weights_here).all()
            and np.isfinite(engine_residual.to_numpy(dtype=float)).all() and np.isfinite(engine_weights).all()):
        raise ValueError("copt_inputs_not_finite")
    metrics = payload["metrics"]
    return {
        "version": 1,
        "profile": PROFILE,
        "entity_id": entity,
        "index": [str(v) for v in n.snapshots],
        "units": [{"name": u.name, "capacity_mw": int(round(u.capacity_mw)), "q": float(u.q)} for u in units],
        "weights": [float(v) for v in weights_here],
        "engine_weights": [float(v) for v in engine_weights],
        "residual": [float(v) for v in residual],
        "engine_residual": [float(v) for v in engine_residual.reindex(n.snapshots)],
        "engine": {"screening_lole": metrics["lole_hours"], "screening_eue": metrics["eue_mwh"],
                   "screening_lolp_max": metrics["lolp_max"]},
        "fidelity": payload.get("fidelity"),
    }


def distribution(units):
    """P[available = k MW] for k = 0..sum of capacities, two states per unit."""
    p = np.ones(1)
    for unit in units:
        c, q = unit["capacity_mw"], unit["q"]
        out = np.zeros(len(p) + c)
        out[: len(p)] += q * p
        out[c:] += (1.0 - q) * p
        p = out
    return p


def hourly(p, r):
    """(LOLP, EUE) of one hour at residual load r on the 1 MW grid, strict."""
    if r <= 0:
        return 0.0, 0.0
    k0 = min(int(math.ceil(r / DELTA_MW - SNAP)), len(p))
    k = np.arange(k0)
    # A probability: summation can land an ulp above 1 when r exceeds the whole fleet.
    return min(max(math.fsum(p[:k0]), 0.0), 1.0), math.fsum(p[:k0] * (r - k * DELTA_MW))


def calculate(raw, scope):
    if raw.get("version") != 1 or raw.get("profile") != PROFILE or raw["entity_id"] != scope["entity_id"]:
        raise ValueError("copt_source_identity")
    if "start" in scope:
        raise ValueError("unsupported_snapshot_scope")
    residual, engine_residual = raw["residual"], raw["engine_residual"]
    scale = max([1.0] + [abs(v) for v in engine_residual])
    reasons, available = {}, {}
    if (len(residual) != len(engine_residual) or not common.finite(residual + engine_residual + raw["weights"])
            or any(abs(a - b) > REL_TOLERANCE * scale for a, b in zip(residual, engine_residual))):
        reasons = dict.fromkeys(METRICS, "residual_mismatch")
    elif any(abs(a - b) > REL_TOLERANCE * max(1.0, abs(b)) for a, b in zip(raw["weights"], raw["engine_weights"])):
        reasons = dict.fromkeys(METRICS, "weights_mismatch")
    else:
        p = distribution(raw["units"])
        hours = [hourly(p, r) for r in residual]
        figures = {
            "screening_lole": math.fsum(w * lolp for w, (lolp, _e) in zip(raw["weights"], hours)),
            "screening_eue": math.fsum(w * eue for w, (_l, eue) in zip(raw["weights"], hours)),
            "screening_lolp_max": max((lolp for lolp, _e in hours), default=0.0),
        }
        for metric, value in figures.items():
            engine = raw["engine"][metric]
            if engine is None or abs(value - engine) > REL_TOLERANCE * max(1.0, abs(engine)):
                reasons[metric] = "headline_mismatch"
            else:
                available[metric] = value
    return common.facts_and_slots(scope, METRICS, available, reasons, len(raw["index"]))


def notes(raw):
    return [
        f"{len(raw['units'])} two-state units convolved on a 1 MW grid; an hour whose available capacity "
        "equals the residual load is not a loss.",
        "Screening (analytic_convolution), not a statutory standard: compare with the LP proxy, not with a "
        "reliability target.",
    ]
