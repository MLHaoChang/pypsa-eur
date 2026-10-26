"""The project's one reliability goal (UX-flow audit row 1, W3b-R).

Section 8 owns the goal (allowed shortfall, energy strictness, MC draws) and
section 3 the site type; all four are stored on the solver config. Every
reliability study (EH reference design, the coupling loop, the margin loop,
the sequential MC) and the readiness mirror read them through
``effective_goal``, the only place that applies the order:

    the stored solver-config value
    → the project template's ``pack_overrides`` / ``recommended_archetype``
    → the study's own default (the pack's value; ``DEFAULT_MC_DRAWS`` draws).

Nothing is written on read: a template value stays a fallback until the
user saves a goal, and a saved value always wins.
"""
from __future__ import annotations

import math
from dataclasses import asdict
from typing import Any

# The goal fields the LP never reads. They are left out of every input
# fingerprint and digest (`goal_free_config`), so saving a goal neither marks
# a solve stale nor moves an investment-case digest. `ens_cap_permyriad` is
# also part of the goal but stays in: it is an LP constraint.
GOAL_FIELDS = frozenset({"target_lole_h", "mc_draws", "site_archetype"})

ARCHETYPES = ("strong_grid", "weak_flexible", "off_grid")
DEFAULT_ARCHETYPE = "strong_grid"
# The engine default every MC-based study shares (mc, coupling and margin
# loops, `DEFAULT_EH_MC_DRAWS`).
DEFAULT_MC_DRAWS = 500

# Where a value came from, as the studies' results name it.
STORED, TEMPLATE, DEFAULT, OVERRIDE = "stored", "template", "default", "override"


def _number(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if math.isfinite(v) else None


def valid_lole(v: Any) -> bool:
    n = _number(v)
    return n is not None and n >= 0


def valid_draws(v: Any) -> bool:
    from services.adequacy.mc import MAX_DRAWS

    return isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= MAX_DRAWS


def valid_archetype(v: Any) -> bool:
    return v in ARCHETYPES


def invalid_goal_fields(data: dict) -> dict[str, Any]:
    """The goal fields in a stored config that ``PUT /solver_config`` would
    refuse, with their values. A load or bundle import drops them to None
    (the resolver then falls back) and names them, never refuses the file."""
    checks = (("target_lole_h", valid_lole), ("mc_draws", valid_draws),
              ("site_archetype", valid_archetype))
    return {k: data[k] for k, ok in checks
            if (data or {}).get(k) is not None and not ok(data[k])}


def effective_goal(cfg: Any, template: dict | None) -> dict[str, dict[str, Any]]:
    """``{field: {"value", "source"}}`` for ``target_lole_h`` (h/yr),
    ``ens_cap_permyriad`` (‱), ``mc_draws`` and ``site_archetype``.
    ``cfg`` is a SolverConfig or its dict; ``template`` the project's
    ``eh_template.json`` (None for a project without one). A ``default``
    value of None means "the pack's own value"."""
    get = cfg.get if isinstance(cfg, dict) else (lambda k: getattr(cfg, k, None))
    po = (template or {}).get("pack_overrides") or {}

    def pick(stored: Any, ok, templated: Any, default: Any) -> dict[str, Any]:
        if stored is not None and ok(stored):
            return {"value": stored, "source": STORED}
        if templated is not None and ok(templated):
            return {"value": templated, "source": TEMPLATE}
        return {"value": default, "source": DEFAULT}

    def ens_ok(v: Any) -> bool:
        n = _number(v)
        return n is not None and n > 0       # 0 / None = no ENS target

    return {
        "target_lole_h": pick(get("target_lole_h"), valid_lole,
                              po.get("target_lole_h"), None),
        "ens_cap_permyriad": pick(get("ens_cap_permyriad"), ens_ok,
                                  po.get("ens_cap_permyriad"), None),
        "mc_draws": pick(get("mc_draws"), valid_draws, None, DEFAULT_MC_DRAWS),
        "site_archetype": pick(get("site_archetype"), valid_archetype,
                               (template or {}).get("recommended_archetype"),
                               DEFAULT_ARCHETYPE),
    }


def active_goal(solver_state: dict) -> dict[str, dict[str, Any]]:
    """The open project's goal: its live solver config and its template."""
    from services import network_lengths
    from services.flow_service import read_template

    project_dir = network_lengths.active_project_dir()
    template = read_template(project_dir) if project_dir is not None else None
    return effective_goal(solver_state.get("solver_config"), template)


def goal_sources(goal: dict, overridden: set[str] | frozenset[str] = frozenset()) -> dict[str, str]:
    """Each field's source as a study result names it; a field the request
    set itself is an ``override``."""
    return {k: (OVERRIDE if k in overridden else v["source"]) for k, v in goal.items()}


def goal_free_config(cfg: Any) -> dict:
    """The solver config as a dict without the goal fields the LP never
    reads: the input to every fingerprint and digest of the config."""
    d = cfg if isinstance(cfg, dict) else asdict(cfg)
    return {k: v for k, v in d.items() if k not in GOAL_FIELDS}


def eh_request_with_goal(body: Any, goal: dict) -> tuple[Any, dict[str, str]]:
    """The EH study request with each goal field it omits taken from the
    goal, and each field's source. A field the request sets wins for that
    run (``override``); a ``default`` leaves the pack's own value."""
    overridden: set[str] = set()
    update: dict[str, Any] = {}
    if body.archetype is None:
        update["archetype"] = goal["site_archetype"]["value"]
    else:
        overridden.add("site_archetype")
    po = dict(body.pack_overrides or {})
    for key in ("target_lole_h", "ens_cap_permyriad"):
        if po.get(key) is not None:
            overridden.add(key)
        elif goal[key]["source"] != DEFAULT:
            po[key] = goal[key]["value"]
            # A target certifies with the MC: strong_grid's factory metric
            # is 'none', which the pack refuses beside a target (as
            # `buildEhStudyBody` sets it).
            if key == "target_lole_h" and po.get("certification_metric") is None:
                po["certification_metric"] = "mc_lole"
    if po != (body.pack_overrides or {}):
        update["pack_overrides"] = po
    # A non-object `mc` is left for the runner to refuse.
    mc = dict(body.mc or {}) if body.mc is None or isinstance(body.mc, dict) else None
    if mc is not None:
        if mc.get("draws") is not None:
            overridden.add("mc_draws")
        elif goal["mc_draws"]["source"] != DEFAULT:
            mc["draws"] = goal["mc_draws"]["value"]
            update["mc"] = mc
    sources = goal_sources(goal, overridden)
    return (body.model_copy(update=update) if update else body), sources


def loop_goal(body: Any, goal: dict, nyears: float | None) -> tuple[float | None, int, dict[str, str]]:
    """``(target, draws, sources)`` for the coupling and margin loops. A
    request's own ``target_lole_h`` is HORIZON-basis hours and wins; the
    goal's h/yr target goes on the horizon basis here (× ``nyears``, the
    identity when the horizon is unknown, as the panel's `wireTarget`).
    ``sources`` names only the two fields a loop reads: the margin loop's
    payload carries no ENS-space key."""
    overridden: set[str] = set()
    target = getattr(body, "target_lole_h", None)
    if target is not None:
        overridden.add("target_lole_h")
    elif goal["target_lole_h"]["value"] is not None:
        target = float(goal["target_lole_h"]["value"])
        if nyears is not None and math.isfinite(nyears) and nyears > 0:
            target *= nyears
    draws = getattr(body, "draws", None)
    if draws is not None:
        overridden.add("mc_draws")
    else:
        draws = goal["mc_draws"]["value"]
    sources = goal_sources(goal, overridden)
    return target, int(draws), {k: sources[k] for k in ("target_lole_h", "mc_draws")}


def stored_config(project_dir) -> dict | None:
    """A project's solver config as a dict: the live one when it is the open
    project, else its saved ``solver_config.json``; None when neither reads."""
    import json
    import os

    from services import network_lengths
    from services.pypsa_service import PyPSAService

    active = network_lengths.active_project_dir()
    if active is not None and os.path.realpath(active) == os.path.realpath(project_dir):
        try:
            return asdict(PyPSAService.get_solver_state()["solver_config"])
        except Exception:                                # noqa: BLE001 — read the file
            pass
    try:
        raw = json.loads((project_dir / "solver_config.json").read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None
