"""
What a solve's optimality is worth (assistant-assurance 10, S2; GK B ruling G2a–G2f).

`calculations.solve_matches_inputs` already demands condition "optimal" and the
recorded input fingerprints, which hash the solver config. A MIP reported
"optimal" is only optimal to the relative gap the solver was given, so this
module answers three questions about the live solve:

- is it an LP, a MIP or a stochastic (scenario) run — decided from the
  network, never from a label;
- which relative gap did `solver_service` actually pass for it — the value
  under that solver's own option key, or `None` when no gap was passed
  (modular-only MIP, an unmapped solver, a missing or non-numeric value);
- which comparisons and prices that gap leaves unestablished.

Facts of the returned solution (dispatch, energy, per-unit expenditure) are not
made wrong by a gap. Only optimality-dependent claims are limited: total-cost
or objective deltas inside the combined gap band, and bus prices on a MIP. On
master's solve path PyPSA writes 0.0 to `buses_t.marginal_price` for a MIP,
since the solver returns no duals, so a MIP price is never a marginal price.
"""
from __future__ import annotations

import math
from numbers import Real

from services.solver.assumptions import _MIP_KEYS_BY_SOLVER, _resolve_mip_kwargs
from services.solver.process_uc import committable_counts

# A relative gap at or below this reads as optimal (GK B G2c).
SOLVE_GAP_TOLERANCE = 1e-6

# Extendable components whose `<attr>_mod` > 0 make the problem integer.
_MODULAR = (
    ("generators", "p_nom"),
    ("links", "p_nom"),
    ("storage_units", "p_nom"),
    ("stores", "e_nom"),
    ("lines", "s_nom"),
    ("transformers", "s_nom"),
)

GAP_NOT_RECORDED = "solve_gap_not_recorded"
DELTA_IN_GAP = "mip_gap_exceeds_delta"
DUALS_NOT_MARGINAL = "mip_duals_not_marginal"
STOCHASTIC = "unsupported_stochastic_run"


def _modular(n) -> bool:
    for table, attr in _MODULAR:
        static = getattr(n, table)
        mod = f"{attr}_mod"
        ext = f"{attr}_extendable"
        if static.empty or mod not in static.columns or ext not in static.columns:
            continue
        integer = static[ext].astype(bool) & (static[mod].astype(float) > 0)
        if bool(integer.any()):
            return True
    return False


def _committable(n) -> bool:
    gens, links = committable_counts(n)
    return bool(gens or links)


def is_stochastic(n) -> bool:
    return bool(getattr(n, "has_scenarios", False))


def is_mip(n) -> bool:
    return _committable(n) or _modular(n)


def _field(config, name, default=None):
    if isinstance(config, dict):
        return config.get(name, default)
    return getattr(config, name, default)


def applied_gap(n, config) -> float | None:
    """The relative gap `solver_service` passed to the solver for this network.

    Mirrors solver_service: user `solver_options` go to every solve, and the
    configured `mip_gap` is added under the solver's key only when a Generator
    or Link is committable (a user-set key wins). `None` means no gap reached
    the solver, so its own default applied and nothing records it.
    """
    if config is None:
        return None
    solver = _field(config, "solver_name")
    if not isinstance(solver, str) or solver.lower() not in _MIP_KEYS_BY_SOLVER:
        return None
    gap_key = _MIP_KEYS_BY_SOLVER[solver.lower()][0]
    options = _field(config, "solver_options") or {}
    if not isinstance(options, dict):
        return None
    if _committable(n):
        mip_gap = _field(config, "mip_gap")
        if mip_gap is None:
            return None
        options = _resolve_mip_kwargs(solver, mip_gap, 0, options)
    value = options.get(gap_key)
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    value = float(value)
    return value if math.isfinite(value) and value >= 0 else None


def classify(n, config) -> dict:
    """`{"kind", "gap", "optimal", "disclosure"}` for the live solve."""
    if is_stochastic(n):
        return {"kind": "stochastic", "gap": None, "optimal": False,
                "disclosure": "Stochastic (scenario) runs are not supported by checked answers."}
    if not is_mip(n):
        return {"kind": "lp", "gap": 0.0, "optimal": True, "disclosure": None}
    gap = applied_gap(n, config)
    if gap is None:
        return {"kind": "mip", "gap": None, "optimal": False,
                "disclosure": "Integer (MIP) solve; the relative optimality gap was not recorded."}
    return {"kind": "mip", "gap": gap, "optimal": gap <= SOLVE_GAP_TOLERANCE,
            "disclosure": f"Integer (MIP) solve to a relative optimality gap of {gap:.3g}; "
                          "costs may exceed the optimum by up to that share."}


def _effective(cls) -> float | None:
    if cls["kind"] == "lp":
        return 0.0
    if cls["kind"] != "mip" or cls["gap"] is None:
        return None
    return 0.0 if cls["gap"] <= SOLVE_GAP_TOLERANCE else cls["gap"]


def band(cls_a, objective_a, cls_b, objective_b) -> float | None:
    """|Δ| below this is within the two solves' combined gap: gap_a·|obj_a| +
    gap_b·|obj_b|, an LP (or a gap within tolerance) counting 0. `None` when a
    gap or objective is unknown, so no delta sign is established."""
    gaps = (_effective(cls_a), _effective(cls_b))
    if None in gaps:
        return None
    total = 0.0
    for gap, objective in zip(gaps, (objective_a, objective_b)):
        if gap == 0.0:
            continue
        if isinstance(objective, bool) or not isinstance(objective, Real) or not math.isfinite(objective):
            return None
        total += gap * abs(float(objective))
    return total


def delta_limit(delta, cls_a, objective_a, cls_b, objective_b) -> str | None:
    """The reason a total-cost/objective delta is not established, or None."""
    if STOCHASTIC in (_stochastic(cls_a), _stochastic(cls_b)):
        return STOCHASTIC
    width = band(cls_a, objective_a, cls_b, objective_b)
    if width is None:
        return GAP_NOT_RECORDED
    if width > 0 and abs(delta) <= width:
        return DELTA_IN_GAP
    return None


def _stochastic(cls):
    return STOCHASTIC if cls["kind"] == "stochastic" else None


def price_limit(cls) -> str | None:
    """Bus prices: a MIP's are not marginal prices on master's solve path."""
    if cls["kind"] == "stochastic":
        return STOCHASTIC
    if cls["kind"] == "mip":
        return DUALS_NOT_MARGINAL
    return None


def _objective(n):
    """The objective as the solver bounded it, in user units: solver_service
    reverses its objective scale on `n.objective` right after the solve."""
    value = getattr(n, "objective", None)
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def capture(ctx) -> dict:
    """The live solve's classification plus its objective, sealed with the raw
    record so a retained baseline carries its own."""
    n = ctx.network
    return {**classify(n, ctx.solver_state.get("solver_config")), "objective": _objective(n)}


def raw_delta_limit(delta, candidate, baseline) -> str | None:
    """`delta_limit` over two captured records; a record without one (captured
    before this check) establishes no delta sign."""
    if not isinstance(candidate, dict) or not isinstance(baseline, dict):
        return GAP_NOT_RECORDED
    return delta_limit(delta, candidate, candidate.get("objective"), baseline, baseline.get("objective"))


def notes(raw) -> list[str]:
    """The answer line naming the MIP gap (or that it was not recorded)."""
    solve = raw.get("solve") if isinstance(raw, dict) else None
    text = solve.get("disclosure") if isinstance(solve, dict) else None
    return [text] if isinstance(text, str) and text else []
