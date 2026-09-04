"""
The cost-vs-availability frontier (spec §5.6, Phase 5).

The ε-constraint study: sweep the reliability target Ē and, at each value,
re-solve and record what the least-cost plan meeting it costs. The result is
the trade-off the whole feature is named for — CapEx (and OpEx) against
availability of energy — as a *curve*, where the single-point mechanics of
Phases 1–4 give one point on it.

WHY ε-CONSTRAINT AND NOT A VOLL SWEEP. Sweeping VoLL and sweeping the cap are
Lagrangian duals and trace the same Pareto frontier only where C*(Ē) is
convex. This repo breaks convexity: `solver_service` switches to MILP with a
MIP gap when `committable` generators are present, so a VoLL sweep can skip
unsupported portions and return points that are not on the frontier at all.
The spec requires that path be disabled or warned; it is simply not
implemented. The ε-constraint sweep has no such problem — every point is a
genuine optimum of its own constrained problem — though the CURVE through
them may be non-convex, which is reported rather than smoothed away.

WHY CAPACITY IS NOT FROZEN. The contingency sweep (classes B/C) pins
capacities because it asks "what does this plan do when an asset fails".
This study asks the opposite question — "what plan would you build for this
standard" — so capacity expansion must re-optimise at every point. Sharing
the contingency driver would silently answer the wrong question.

Each point is assembled from the adequacy report the solver already emits,
not recomputed here: that report is where the cost axis has its shed-cost
exclusion enforced by construction (`CostBlock.excludes_shed_cost` is typed
`Literal[True]`), and duplicating the assembly would be the one place the two
could drift.
"""
from __future__ import annotations

import dataclasses
import logging
import math
import queue
import threading

logger = logging.getLogger(__name__)

# A frontier point costs one full capacity-expansion solve. Twelve is already
# a minute-scale study on a real network; beyond that the user wants a queued
# job, not a request-scoped worker thread.
MAX_FRONTIER_POINTS = 12

# Spread over the range that matters: adequate systems run ~0.1–1‱, and the
# decade above that is where the cost gradient is steep enough to see a knee.
DEFAULT_TARGETS_PERMYRIAD = (100.0, 50.0, 20.0, 10.0, 5.0, 2.0, 1.0, 0.5)


class FrontierBudgetError(ValueError):
    pass


class FrontierConfigError(ValueError):
    pass


def _validate(targets: list[float]) -> list[float]:
    if not targets:
        raise FrontierConfigError(
            "the frontier needs at least one reliability target to sweep")
    if len(targets) > MAX_FRONTIER_POINTS:
        raise FrontierBudgetError(
            f"{len(targets)} frontier points exceed the budget of "
            f"{MAX_FRONTIER_POINTS} — each point is a full capacity-expansion "
            "solve; narrow the range or coarsen the spacing")
    out = []
    for t in targets:
        try:
            v = float(t)
        except (TypeError, ValueError):
            raise FrontierConfigError(f"target {t!r} is not a number")
        if not (math.isfinite(v) and v > 0):
            raise FrontierConfigError(
                f"target {v!r} must be > 0 — the frontier sweeps ACTIVE "
                "targets, and 0/None mean 'no target', which is the "
                "unconstrained point the sweep already reports separately")
        out.append(v)
    # Loosest first: the cheap end solves fastest, so a user watching progress
    # sees the curve grow from the side they already understand.
    return sorted(set(out), reverse=True)


def non_convexity_warning(network, cfg) -> str | None:
    """
    The ε-constraint points stay valid under a non-convex C*, but the curve
    through them can bend the wrong way and a "knee" read off it may be an
    artefact of the MIP gap rather than economics. Say so rather than letting
    the shape imply a precision it does not have.
    """
    reasons = []
    gens = getattr(network, "generators", None)
    if gens is not None and not gens.empty and "committable" in gens.columns:
        try:
            n_uc = int(gens["committable"].fillna(False).astype(bool).sum())
        except Exception:                                     # noqa: BLE001
            n_uc = 0
        if n_uc:
            reasons.append(f"{n_uc} committable generator(s) make the problem a MILP")
    opts = getattr(cfg, "solver_options", None) or {}
    for key in ("mip_rel_gap", "mip_gap", "MIPGap", "ratioGap"):
        try:
            gap = float(opts.get(key)) if key in opts else 0.0
        except (TypeError, ValueError):
            gap = 0.0
        if gap > 0:
            reasons.append(f"a nonzero MIP gap ({key}={opts[key]})")
            break
    if not reasons:
        return None
    return (
        "Total system cost is not a convex function of the target here: "
        + " and ".join(reasons)
        + ". Each point is still a genuine optimum of its own constrained "
        "problem, but the curve between points may be non-monotone and an "
        "apparent knee can be an artefact of the solver tolerance rather "
        "than economics."
    )


def _restore_base(network, lock, cfg, log_queue, final_state_update):
    """
    The closing re-solve with the user's ORIGINAL config, through the real
    state sink — the study must leave the foreground results exactly as the
    user's own solve would, not on whichever target happened to be swept last.

    Returns ``(ok, status)``. A restore that raises is REPORTED
    (``base_restored=False``), not propagated: the sweep's own answer is still
    a valid answer, and the one thing the caller must not be told is that the
    foreground is the user's plan when it demonstrably is not.

    The solver's own status rides along, exactly as the contingency sweep's
    ``_restore_base_guarded`` does. It has to: a re-solve that comes back
    ``infeasible`` did not raise and did not restore anything either, and this
    function used to discard that word and report a bare ``True`` — the one
    reading the caller must never be given (shipped-code review, finding 13).
    """
    from services.solver_service import run_simulation

    final_sink: dict = {}
    try:
        status, _condition = run_simulation(
            cfg, network, lock, threading.Event(),
            log_queue if log_queue is not None else queue.SimpleQueue(),
            state_update=final_state_update or (lambda **kw: final_sink.update(kw)),
        )
    except Exception as exc:                                  # noqa: BLE001
        logger.exception(
            "frontier: the closing base re-solve FAILED — the network is left "
            "on the last swept target and the foreground results do not "
            "describe the user's own config")
        return False, f"raised: {exc}"
    return True, str(status)


def run_frontier_sweep(network, lock, cfg, targets: list[float], *,
                       log_queue=None, final_state_update=None,
                       stop_event=None) -> dict:
    """
    Returns ``{"points": [...], "warning": str|None, "base_restored": bool,
    "base_restore_status": str|None, "aborted": bool}``. ``base_restored``
    says the closing re-solve did not RAISE; ``base_restore_status`` is the
    solver's own word on it, and a status that is not optimal means the
    foreground is not the user's plan however the flag reads.

    ``stop_event`` (Phase 12e) is checked BETWEEN points and acted on with a
    ``break``, never an exception: the closing restore below must run on the
    abort path exactly as it does on the happy one, and the points already
    swept are kept. The worst case after the click is therefore the in-flight
    solve plus the restore solve — the restore is itself a full
    ``run_simulation``, and an abort does not buy its way out of it.

    A point whose solve is not optimal is returned WITH its status and no
    numbers — an unreachable target is a real and interesting answer ("no
    plan meets this standard"), not a gap in the chart to be interpolated
    over.

    THE RESTORE IS EXCEPTION-SAFE (coupling-loop spec §1.3, plan [S8-b]).
    The sweep MUTATES the network — every point re-solves it under a different
    cap — so a mid-sweep exception without a ``finally`` leaves it on the last
    swept ε while the foreground results still describe the pre-study solve:
    the study silently rewrites the user's plan and says nothing. The closing
    re-solve therefore runs on every path, and ``base_restored`` states what
    actually happened rather than asserting success. When the sweep itself
    raises, the partial record travels with the exception as
    ``exc.frontier_result`` so the caller can report the restore truthfully
    instead of guessing. Validation failures raise BEFORE the try — nothing
    has been solved yet, so there is nothing to restore.
    """
    from services.adequacy.sweep import _solve_once

    eps = _validate(list(targets))
    if float(getattr(cfg, "voll", 0.0) or 0.0) <= 0:
        raise FrontierConfigError(
            "the frontier requires VOLL > 0 — with no slack generators the "
            "cap constrains nothing and every point collapses to the same "
            "unconstrained plan")

    warning = non_convexity_warning(network, cfg)
    points: list[dict] = []
    # Built up front and MUTATED in place so the record the exception path
    # hands back and the record the happy path returns are the same object.
    result = {"points": points, "warning": warning, "base_restored": False,
              "base_restore_status": None,
              "aborted": False}
    try:
        for e in eps:
            if stop_event is not None and stop_event.is_set():
                result["aborted"] = True
                break
            sweep_cfg = dataclasses.replace(cfg, ens_cap_permyriad=e)
            sink: dict = {}
            _solve_once(sweep_cfg, network, lock, log_queue, sink)
            status = sink.get("_status")
            if status not in ("ok", "optimal"):
                points.append({"target_permyriad": e, "status": sink.get("_condition")
                               or status or "failed", "point": None})
                continue
            rep = sink.get("adequacy_report")
            if not rep:
                # A target was set and the solve succeeded, so the report is
                # the contract. Its absence is a defect, not an empty result.
                points.append({"target_permyriad": e, "status": "no_report",
                               "point": None})
                continue
            sysblk = rep["target"]["system"]
            points.append({
                "target_permyriad": e,
                "status": "ok",
                "point": {
                    "cap_mwh": float(sysblk["cap_mwh"]),
                    "achieved_ens_mwh": float(sysblk["achieved_ens_mwh"]),
                    "achieved_shed_hours": float(sysblk["achieved_shed_hours"]),
                    "total_system_cost_eur": float(rep["cost"]["total_system_cost_eur"]),
                    "engine": rep["engine"],
                    "fidelity": rep["fidelity"],
                },
                "binding": rep["target"]["binding"],
                "period_basis": rep["cost"]["period_basis"],
            })
    except BaseException as exc:
        # The record rides along so the caller can report base_restored
        # truthfully; the `finally` below fills it in before this propagates.
        try:
            exc.frontier_result = result
        except AttributeError:                                # pragma: no cover
            pass
        raise
    finally:
        result["base_restored"], result["base_restore_status"] = _restore_base(
            network, lock, cfg, log_queue, final_state_update)
    return result


def knee_index(points: list[dict], voll: float) -> int | None:
    """
    The economic knee: the last point where a step of tightening still buys
    more avoided-shed value than it costs, i.e. where marginal system cost
    first exceeds ``VoLL × marginal ENS avoided`` (spec §5.6).

    Returns an index into the OK points, or None when fewer than two are
    usable or the crossing never happens inside the swept range — saying
    "the knee is not in this range" beats inventing one at an endpoint.
    """
    ok = [p for p in points if p.get("status") == "ok" and p.get("point")]
    if len(ok) < 2 or not (math.isfinite(voll) and voll > 0):
        return None
    # points are loosest-first, so each step tightens
    for i in range(len(ok) - 1):
        a, b = ok[i]["point"], ok[i + 1]["point"]
        d_cost = b["total_system_cost_eur"] - a["total_system_cost_eur"]
        d_ens = a["achieved_ens_mwh"] - b["achieved_ens_mwh"]
        if d_ens <= 0:
            continue
        if d_cost > voll * d_ens:
            return i
    return None
