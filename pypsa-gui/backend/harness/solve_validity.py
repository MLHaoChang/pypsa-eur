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
import re
from types import SimpleNamespace
from numbers import Real

from services.solver.assumptions import _MIP_KEYS_BY_SOLVER, _resolve_mip_kwargs
from services.solver.process_uc import (
    committable_counts,
    other_committable,
    planning_uc_mode,
)

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
        if mod not in static.columns or ext not in static.columns:
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


# assistant-assurance 18: what the solve call actually applied, sealed in
# `network.meta` next to the input fingerprints by `solver_service`. A run
# sealed before this record keeps the reconstruction mirror below.
APPLIED_KEY = "assistant_solve_applied"
_RECORD_VERSION = 1
_UC_MODES = (None, "linearised", "binary", "plain")
# A record that is present but unreadable: the reads fail closed (an integer
# solve with no recorded gap), never back onto the mirror.
MALFORMED = object()


def _option(value):
    """`value` as a JSON-safe scalar for the record, or `None` to leave it out."""
    if isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, Real) and math.isfinite(float(value)):
        return float(value)
    return None


# The meta leaves with every NetCDF export and bundle, and `solver_options` is
# free-form user input: a credential-like key keeps its name, never its value.
_SECRET_KEY = re.compile(
    r"secret|password|passwd|token|key|licen[cs]e|access_?id|credential", re.I
)
REDACTED = "<redacted>"


def applied_record(solver_name, solver_options, uc_kwargs, process_uc_mode) -> dict:
    """The record `solver_service` seals for one solve: the solver, the options
    dict it passed, whether `linearized_unit_commitment` was passed (process
    units, or the island's linearised UC on the full path; PyPSA applies it
    network-wide, so it is one flag), and the process-UC mode the plan used.

    Options keep JSON-safe scalars only, so the record survives the NetCDF
    meta; any other value is named under `"dropped"`, never left out silently.
    A credential-like key's value is stored as `REDACTED`."""
    options, dropped = {}, []
    for key, value in (solver_options or {}).items():
        kept = _option(value)
        if not isinstance(key, str) or kept is None:
            dropped.append(str(key))
        elif _SECRET_KEY.search(key):
            options[key] = REDACTED
        else:
            options[key] = kept
    return {
        "v": _RECORD_VERSION,
        "solver": solver_name,
        "options": options,
        "dropped": dropped,
        "linearized_unit_commitment": bool(
            (uc_kwargs or {}).get("linearized_unit_commitment")
        ),
        "process_uc_mode": process_uc_mode,
    }


def applied_record_of(n):
    """The sealed record, `None` for a run sealed before it, or `MALFORMED`."""
    meta = getattr(n, "meta", None)
    if not isinstance(meta, dict) or APPLIED_KEY not in meta:
        return None
    rec = meta[APPLIED_KEY]
    if (
        not isinstance(rec, dict)
        or rec.get("v") != _RECORD_VERSION
        or not isinstance(rec.get("solver"), str)
        or not isinstance(rec.get("options"), dict)
        or not isinstance(rec.get("dropped"), list)
        or not isinstance(rec.get("linearized_unit_commitment"), bool)
        or rec.get("process_uc_mode") not in _UC_MODES
    ):
        return MALFORMED
    return rec


def _integer_commitment(n, config) -> bool:
    """Committable units the solve kept binary, under the effective process-UC
    mode: the one the solve recorded (`applied_record_of`), else the mirror of
    `process_uc.planning_uc_mode` for a run sealed before the record.
    "linearised" relaxes commitment network-wide, so the solve is an LP; "plain"
    solves the process units as plain Links, leaving only the other committable
    units binary. A malformed record keeps every committable unit binary."""
    if not _committable(n):
        return False
    rec = applied_record_of(n)
    if rec is MALFORMED:
        return True
    if rec is not None:
        if rec["linearized_unit_commitment"]:
            return False
        mode = rec["process_uc_mode"]
    else:
        setting = SimpleNamespace(process_uc=_field(config, "process_uc", "auto"))
        mode = planning_uc_mode(n, setting)
        if mode == "linearised":
            return False
    if mode == "plain":
        return bool(sum(other_committable(n)))
    return True


def is_mip(n, config=None) -> bool:
    return _integer_commitment(n, config) or _modular(n)


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

    A run sealed with `applied_record_of` is read from that record (the options
    the solve call passed); the mirror above serves only runs sealed before it.
    """
    rec = applied_record_of(n)
    if rec is MALFORMED:
        return None
    if rec is not None:
        return _gap_in(rec["solver"], rec["options"])
    solver = _field(config, "solver_name")
    if not isinstance(solver, str) or solver.lower() not in _MIP_KEYS_BY_SOLVER:
        return None
    options = _field(config, "solver_options") or {}
    if not isinstance(options, dict):
        return None
    if _committable(n):
        mip_gap = _field(config, "mip_gap")
        if mip_gap is None:
            return None
        options = _resolve_mip_kwargs(solver, mip_gap, 0, options)
    return _gap_in(solver, options)


def _gap_in(solver, options) -> float | None:
    """The relative gap under `solver`'s own key in `options`, or `None`."""
    if solver.lower() not in _MIP_KEYS_BY_SOLVER:
        return None
    value = options.get(_MIP_KEYS_BY_SOLVER[solver.lower()][0])
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    value = float(value)
    return value if math.isfinite(value) and value >= 0 else None


def classify(n, config) -> dict:
    """`{"kind", "gap", "optimal", "disclosure"}` for the live solve."""
    if is_stochastic(n):
        return {
            "kind": "stochastic",
            "gap": None,
            "optimal": False,
            "disclosure": "Stochastic (scenario) runs are not supported by checked answers.",
        }
    if not is_mip(n, config):
        return {"kind": "lp", "gap": 0.0, "optimal": True, "disclosure": None}
    gap = applied_gap(n, config)
    if gap is None:
        return {
            "kind": "mip",
            "gap": None,
            "optimal": False,
            "disclosure": "Integer (MIP) solve; the relative optimality gap was not recorded.",
        }
    return {
        "kind": "mip",
        "gap": gap,
        "optimal": gap <= SOLVE_GAP_TOLERANCE,
        "disclosure": f"Integer (MIP) solve to a relative optimality gap of {gap:.3g}; "
        "costs may exceed the optimum by up to that share.",
    }


def _effective(cls) -> float | None:
    # An LP's classify() gap is 0.0, so it needs no branch of its own.
    if cls["gap"] is None:  # a stochastic run or an unrecorded MIP gap
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
        if (
            isinstance(objective, bool)
            or not isinstance(objective, Real)
            or not math.isfinite(objective)
        ):
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


# What a reader outside checked answers says in place of a MIP's bus prices
# (assistant-assurance 17): one cause sentence on every surface, the frontend's
# `NOT_KEPT_WORDS.mip_duals_not_marginal.why` (sectionWords.ts; parity test
# `test_mip_cause_sentence_matches_the_frontend_words`).
MIP_PRICES_CAUSE = "An integer (MIP) solve has no marginal prices."
MIP_PRICES_TEXT = f"Not available. {MIP_PRICES_CAUSE}"


def mip_price_limit(n, config) -> str | None:
    """`DUALS_NOT_MARGINAL` when `buses_t.marginal_price` holds a MIP's zeros, else None.

    The one gate for every reader outside checked answers (assistant-assurance
    17). It is `classify`'s own MIP test, so a reader and a checked answer never
    disagree on the solve kind; unlike `classify` it also gates a stochastic run
    that is a MIP. `config` is the solver config of the solve that wrote the
    prices (process-UC mode decides whether commitment stayed binary); `None`
    reads as the defaults.
    """
    return DUALS_NOT_MARGINAL if is_mip(n, config) else None


# The solve's config could not be read, so whether it was a MIP is not known.
SOLVER_CONFIG_UNREADABLE = "solver_config_unreadable"


def solved_price_limit(state, n) -> str | None:
    """`mip_price_limit` for a run's solver state: keyed on the solve that wrote the prices.

    `solver_service` records the limit in `last_solve_settings["price_limit"]`,
    judged with the config that solve used, only when it is set. Without that
    record (an LP solve, or a solve from before it) `n` is judged with the
    current config, which errs only toward withholding, except for a MIP solved
    before the record existed whose config has since been changed to an LP.
    """
    settings = state.get("last_solve_settings") or {}
    if isinstance(settings, dict) and settings.get("price_limit"):
        return settings["price_limit"]
    return mip_price_limit(n, state.get("solver_config"))


def price_limit_without_config(n) -> str | None:
    """`mip_price_limit` when the solve's config cannot be read: fails closed.

    Only the process-UC mode in the config can turn a committable network into
    an LP, so a network with no committable or modular unit is an LP whatever
    the config, and one that is a MIP under the defaults is reported as a MIP.
    """
    if not (_committable(n) or _modular(n)):
        return None
    return DUALS_NOT_MARGINAL if is_mip(n, None) else SOLVER_CONFIG_UNREADABLE


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
    return {
        **classify(n, ctx.solver_state.get("solver_config")),
        "objective": _objective(n),
    }


def raw_delta_limit(delta, candidate, baseline) -> str | None:
    """`delta_limit` over two captured records; a record without one (captured
    before this check) establishes no delta sign."""
    if not isinstance(candidate, dict) or not isinstance(baseline, dict):
        return GAP_NOT_RECORDED
    return delta_limit(
        delta,
        candidate,
        candidate.get("objective"),
        baseline,
        baseline.get("objective"),
    )


def notes(raw) -> list[str]:
    """The answer line naming the MIP gap (or that it was not recorded)."""
    solve = raw.get("solve") if isinstance(raw, dict) else None
    text = solve.get("disclosure") if isinstance(solve, dict) else None
    return [text] if isinstance(text, str) and text else []
