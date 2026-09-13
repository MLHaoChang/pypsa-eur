"""Coupling-loop HTTP runner — validation, bindings, worker, study publish.

Lifted from ``routers/results.py`` (planning-loop router lift). The route
handler keeps mesh refusal and the FastAPI decorator; this module owns the
synchronous 422 set through ``publish_study``. State is injected so this
module never imports ``routers.*``.
"""
from __future__ import annotations

import contextvars as _contextvars
import logging
import math
import threading as _threading

from fastapi import HTTPException
from pydantic import BaseModel as _BaseModel

from models.schemas import Finite as _Finite
from services.adequacy.coupling import snapshot_hash as _snapshot_hash
from services.pypsa_service import PyPSAService

logger = logging.getLogger("pypsa_gui.results")

LOOP_WARNING_V1 = (
    "The map from the energy cap to MC-LOLE is a step function, not a curve: "
    "a range of caps produces the identical plan and therefore the identical "
    "LOLE, so ε* is the cheapest cap this search VERIFIED, not the largest cap "
    "that would still pass. Every iterate is a genuine optimum of its own "
    "constrained problem, but only iterates whose own MC evaluation met the "
    "target are answers — the bracket is a search heuristic, and tightening ε "
    "can raise MC-LOLE."
)

# [N5]. Stated where the number is read, because the mismatch is structural
# and no choice of ε can remove it.
MULTI_PERIOD_WARNING_V1 = (
    "This network has more than one period: the energy cap is enforced per "
    "period against each period's own demand, while the target is a horizon "
    "SUM of LOLE — a single scalar ε cannot say 'fix period 3 only', so an "
    "unreachable or budget-exhausted verdict is structurally likelier here. "
    "The per-iterate by_period rows are the diagnostic."
)

# [N6]. Three mechanisms, named, because the user's NEXT ACTION differs by
# which one is operating and a bare "unreachable" is unactionable.
# The margin-loop panel's OWN heading, verbatim.
#
# ★ A verdict that diagnoses a dead end and names the way out is only useful
# if the way out can be FOUND: "a planning reserve margin" is a lever, and the
# user still has to know the tool will search for one. This names the control
# they must click. `MarginLoopPanel.test.tsx` pins the panel to the same
# string, because a verdict naming a control that does not exist under that
# name is worse than no pointer at all.
MARGIN_LOOP_PANEL_LABEL = "Reliability-targeted reserve margin loop"

NEVER_BOUND_WITH_MARGIN_COPY_V1 = (
    "The cap never bound. On every iterate that solved, the LP's own shed "
    "energy stayed under the ceiling, so tightening the cap could not change "
    "the plan — and no cap can. What DID shape this plan is the firm-capacity "
    "standard: a reserve margin is already in force, and the loop is reporting "
    "the cap's failure, not the margin's. The loss of load the MC still sees "
    "comes from outages beyond what that margin buys. Raise the margin (or "
    "lower the target) rather than capping harder; the cap has no leverage "
    "here either way. HOW MUCH to raise it by is what the \""
    + MARGIN_LOOP_PANEL_LABEL + "\" on this tab searches for: the same "
    "target and the same sampler, on the lever that is actually shaping "
    "this plan."
)

NEVER_BOUND_COPY_V1 = (
    "The cap never bound. On every iterate that solved, the LP's own shed "
    "energy stayed under the ceiling (the binding column reads something "
    "other than 'system_cap' throughout), so tightening the cap could not "
    "change the plan — and no cap can. The loss of load the MC reports here "
    "comes from OUTAGES the LP does not model at all, not from energy the LP "
    "chose to shed: its deterministic view already covers demand, which is "
    "exactly why the cap has no leverage. What would move this number is firm "
    "capacity the LP sees no deterministic reason to build — a planning "
    "reserve margin, or the candidate unit itself. Capping harder will not. "
    "You do not have to size that margin by hand: the \""
    + MARGIN_LOOP_PANEL_LABEL + "\" on this tab runs this same search on "
    "that lever and certifies what it finds against this same MC-LOLE "
    "target."
)

UNREACHABLE_COPY_V1 = (
    "No cap this search could reach produced a plan that met the target on "
    "the MC's own LOLE. Three mechanisms produce this, and they call for "
    "different responses: (a) the LP has perfect FORESIGHT over storage while "
    "the MC dispatches greedily, so a plan that leans on storage looks "
    "adequate to the solver and is not; (b) demand response serves the LP's "
    "cap but is EXCLUDED as a resource in the MC, so tightening ε buys cost "
    "without buying MC-LOLE and the plan stops changing; (c) tightening ε can "
    "substitute storage for thermal capacity and RAISE MC-LOLE. Check the "
    "per-iterate binding column and by_period rows before raising the target."
)


class CouplingLoopRequest(_BaseModel):
    # `target_lole_h` is the only required field, and it is HORIZON-basis
    # hours (the panel does the h/yr conversion so the wire stays unit-safe).
    # Optional here rather than required-by-pydantic so a missing target is
    # refused with the route's own sentence instead of a schema dump.
    target_lole_h: _Finite | None = None
    draws: int | None = None
    seed: int | None = None
    eps0: _Finite | None = None
    max_solves: int | None = None
    restore: str | None = None


def start_coupling_loop(
    body: CouplingLoopRequest | None,
    *,
    solver_state: dict,
    state_update,
    publish_study,
):
    """
    Start the adequacy-coupled planning loop in a worker thread (spec §3).

    Solve the LP under an energy cap, run the sequential MC on the PLAN it
    produced, retune the cap, re-solve — until the plan meets the user's
    target on the MC's own LOLE rather than on the LP proxy's shed energy.
    The two are not the same standard: the LP has perfect foresight over
    storage and no outages at all, so a plan that sheds exactly its cap in the
    LP can lose load for tens of hours in the MC.

    ASYNCHRONOUS BY CONSTRUCTION: up to ``max_solves`` full capacity-expansion
    solves plus an MC evaluation each, plus the closing restore — minutes to
    tens of minutes.

    VALIDATION IS SYNCHRONOUS WHEREVER IT IS CHEAP, and here that is the whole
    set. Every refusal below is knowable from the config and one snapshot, and
    the alternative is not "a slower error" but a WRONG ANSWER: under a
    rolling or myopic strategy every capped solve fails validation, so the
    loop would burn its budget and report ``unreachable`` — "no plan meets
    this standard" — when the truth is "this strategy cannot enforce a cap".
    """
    import dataclasses
    import queue as _queue
    import time

    from services.adequacy.coupling import MAX_LOOP_SOLVES, run_coupling_loop
    from services.adequacy.lever_text import format_lever_value
    from services.adequacy.mc import (
        MAX_DRAWS,
        MC_WARNING_V1,
        mc_adequacy,
        snapshot_inputs,
    )
    from services.adequacy.metrics import horizon_years, resolve_time_basis
    from services.adequacy.sweep import _solve_once

    # ── the synchronous 422 set ───────────────────────────────────────────
    target = getattr(body, "target_lole_h", None)
    try:
        target = float(target) if target is not None else None
    except (TypeError, ValueError):
        target = None
    if target is None or not (target > 0):
        raise HTTPException(
            422,
            "target_lole_h is required and must be > 0: the loop searches for "
            "the cheapest cap whose plan meets a RELIABILITY STANDARD, and a "
            "target of zero (or none) is not a standard — it is the demand "
            "that no draw ever sheds an hour, which no finite plan can buy")

    draws = getattr(body, "draws", None)
    draws = 500 if draws is None else int(draws)
    if draws < 1:
        raise HTTPException(422, "draws must be a positive number of samples")
    if draws > MAX_DRAWS:
        raise HTTPException(
            422,
            f"draws={draws} exceeds the engine cap of {MAX_DRAWS} draws per "
            "evaluation — and the loop pays that cost once per iterate")
    seed = getattr(body, "seed", None)
    seed = 0 if seed is None else int(seed)

    max_solves = getattr(body, "max_solves", None)
    max_solves = MAX_LOOP_SOLVES if max_solves is None else int(max_solves)
    if not (1 <= max_solves <= MAX_LOOP_SOLVES):
        raise HTTPException(
            422,
            f"max_solves must be between 1 and {MAX_LOOP_SOLVES} (got "
            f"{max_solves}) — each solve is a full capacity expansion, so the "
            "budget is the wall-clock promise this request makes")

    restore = getattr(body, "restore", None) or "base"
    if restore not in ("base", "final"):
        raise HTTPException(
            422,
            f"restore must be 'base' or 'final' (got {restore!r}): 'base' "
            "re-solves with your original config, 'final' leaves you holding "
            "the certified plan at ε*")

    cfg = solver_state.get("solver_config")
    if cfg is None or float(getattr(cfg, "voll", 0.0) or 0.0) <= 0:
        raise HTTPException(
            422, "the coupling loop requires a VOLL > 0 in solver settings — "
                 "with no load-shedding slacks the cap constrains nothing and "
                 "every iterate collapses to the same unconstrained plan")

    strategy = str(getattr(cfg, "solve_strategy", "full") or "full")
    if strategy in ("rolling", "myopic"):
        raise HTTPException(
            422,
            f"the reliability target is not supported with the {strategy!r} "
            "solve strategy: each LP window would need its own demand "
            "denominator, so every capped solve fails validation. The loop "
            "would spend its whole budget on failed iterates and report "
            "'unreachable' — which is a statement about the strategy, not "
            "about the network. Use the full strategy, or unset the target.")

    # The ONE snapshot the validation reads, taken under the mutation lock.
    # `keep_zero_capacity=True` from the very first call (spec §1.2): the
    # sampled fleet's MEMBERSHIP must be invariant across iterates or the
    # positional CRN substreams shift under it, and a fleet that is empty here
    # would be empty for every evaluation too.
    n = PyPSAService.get_network()
    # Phase 12f: the same up-front refusal the margin loop makes, for the same
    # reason and against the same defect. A non-finite value in one of the five
    # finite-default LP bounds is a blocking preflight error, so EVERY iterate
    # would come back `validation_failed`, the loop would spend its whole
    # budget, and the verdict copy would advise "Raise max_solves, or start
    # from a tighter eps0" — advice that can never work here.
    #
    # Guarded at BOTH loops deliberately: this codebase already learned that a
    # guard repeated at seven call sites is the one the eighth route forgets.
    from services.validation_service import _check_nonfinite_bounds as _cnb
    for _iss in _cnb(n):
        raise HTTPException(422, _iss.message)
    with PyPSAService.get_lock():
        try:
            # Phase 12c-0: the LP's demand basis — the plan the loop
            # certifies was built on it (the fifteenth finding).
            inputs = snapshot_inputs(n, keep_zero_capacity=True, cfg=cfg)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        nyears = float(horizon_years(n))

    if not inputs.units:
        raise HTTPException(
            422,
            "nothing to sample: no electrical generator carries resolvable "
            "occurrence data (unavailability + MTTR), so the sampled fleet is "
            "empty — an empty fleet would report the entire horizon as loss of "
            "load, which is a statement about missing input data, not about "
            "the system")

    # [S11] the up-front resolution floor. One shortfall hour in one draw
    # contributes that hour's WEIGHT to the mean, so the smallest non-zero
    # LOLE these draws can resolve is `min positive weight / draws`. A target
    # under it is undecidable: every verdict the run could reach — met or
    # missed — would be an artefact of the sample size, and the study would
    # answer with a confident number that means nothing.
    _w = inputs.weights[inputs.weights > 0]
    floor_h = (float(_w.min()) / draws) if _w.size else None
    if floor_h is not None and target < floor_h:
        need = int(math.ceil(float(_w.min()) / target))
        raise HTTPException(
            422,
            f"target_lole_h={target:g} h is below this study's resolution "
            f"floor of {floor_h:g} h at {draws} draws: a single shortfall "
            "hour in a single draw already exceeds it, so no verdict here "
            "could distinguish a compliant plan from a lucky sample. About "
            f"{need} draws would resolve it.")

    eps0 = getattr(body, "eps0", None)
    if eps0 is None:
        eps0 = getattr(cfg, "ens_cap_permyriad", None)
    try:
        eps0 = float(eps0) if eps0 is not None else None
    except (TypeError, ValueError):
        eps0 = None
    # An unset cap reaches the loop as the ≤ 0 NO-TARGET sentinel, which the
    # controller would clamp to its hard backstop and start the search two
    # decades tighter than any real plan needs. 100‱ is the loose end of the
    # frontier's own default spread — the cheap side, where a solve is fast.
    if eps0 is None or not (eps0 > 0):
        eps0 = 100.0

    lock = PyPSAService.get_lock()
    base_cfg = cfg
    basis = resolve_time_basis(nyears)

    # ── the bindings (spec §3) ────────────────────────────────────────────

    def _snapshot():
        # `base_cfg` is captured in the request — the worker never reads
        # `solver_state` — and the scalers do not change across iterates.
        with lock:
            return snapshot_inputs(n, keep_zero_capacity=True, cfg=base_cfg)

    def _hash(mc_inputs) -> str:
        """sha256 over exactly what the MC reads — the sorted
        ``(name, capacity_mw)`` unit vector, the sorted
        ``(name, p_nom_mw, e_nom_mwh)`` storage vector, and the residual bytes.

        NOT the objective (plan [B3]): degenerate optima give equal cost for
        different plans, and with DSR configured the objective moves (variable
        cost) while the plan stands still. Equal hash ⇒ bit-identical MC under
        the same seed and draw count, so reuse is exact where cost equality
        was a guess.
        """
        # Phase 12d: one implementation for both loops, testable
        # (`tests/test_adequacy_activity.py` E8).
        return _snapshot_hash(mc_inputs)

    _margin_bound_flag = [False]

    def solve_at(eps: float) -> dict:
        """One capped capacity-expansion solve into a PRIVATE sink, read out
        exactly as ``run_frontier_sweep`` reads its points. Solve failures
        come back as a status — the controller is specified never to see an
        exception from here, and a raise would cost the whole study."""
        sink: dict = {}
        _solve_once(dataclasses.replace(base_cfg, ens_cap_permyriad=float(eps)),
                    n, lock, None, sink)
        status = sink.get("_status")
        out = {"status": status, "condition": sink.get("_condition"),
               "cost_eur": None, "ens_mwh": None, "cap_mwh": None,
               "binding": None, "report": None}
        if status not in ("ok", "optimal"):
            return out
        rep = sink.get("adequacy_report")
        if not rep:
            # A target WAS set and the solve succeeded, so the report is the
            # contract. Its absence is a defect, and reporting it as a failed
            # iterate keeps the loop from evaluating a plan it cannot describe.
            out["status"] = "no_report"
            out["condition"] = "the solve returned no adequacy report"
            return out
        # Did a reserve margin shape this iterate? The controller's row keeps
        # nine fixed keys and drops the report, and `coupling.py` is
        # deliberately not touched (it is the regression oracle for the margin
        # loop), so the answer is captured HERE, where the report is in hand.
        # `binding` itself can never say this: it is computed purely from the
        # ENS caps and reads "voll" even when a margin is what bound.
        _rm = (rep.get("reserve_margin") or {}).get("by_period") or []
        if any(bool(p.get("binding")) for p in _rm):
            _margin_bound_flag[0] = True
        sysblk = rep["target"]["system"]
        out.update(
            report=rep,
            cost_eur=float(rep["cost"]["total_system_cost_eur"]),
            ens_mwh=float(sysblk["achieved_ens_mwh"]),
            cap_mwh=float(sysblk["cap_mwh"]),
            binding=rep["target"]["binding"],
        )
        return out

    # The floor the payload reports, refreshed by every evaluation so the
    # value that ships is the FINAL evaluation's own (spec v1.2 §3) rather
    # than the up-front estimate. With `draws == max_draws` the sample count
    # is pinned, so the two agree — which is the point: a drifting n_samples
    # would mean the floor under the verdict was not the floor that was
    # validated.
    eval_state: dict = {"floor": floor_h}

    def evaluate():
        mc_inputs = _snapshot()
        # §3's normative call. `max_draws=N` is what PINS the sample count:
        # merely ignoring cov_target leaves the adaptive 2000-draw cap in play
        # and n_samples drifts between iterates, which breaks the common
        # random numbers the plateau reuse rests on.
        # `stop_event` is NEVER passed here: this is a REPLAY of one batch
        # sequence (see `mc_adequacy`'s note), and the loop's own abort is
        # checked between iterates, never inside an evaluation.
        metrics = mc_adequacy(mc_inputs, draws=draws, seed=seed,
                              max_draws=draws, stop_event=None)
        try:
            eval_state["floor"] = metrics.get("resolution_floor_h")
        except AttributeError:                                # noqa: BLE001
            pass
        return _hash(mc_inputs), metrics

    def _plan_hash():
        """The duck-typed probe of spec v1.2 §1. The hash comes from the
        snapshot alone, so the controller can skip an MC on a plateau instead
        of running one and throwing the result away."""
        return _hash(_snapshot())

    evaluate.plan_hash = _plan_hash

    # ── the record (closed over, never reached through `solver_state`) ──────────
    stop_event = _threading.Event()
    record: dict = {
        "study": "coupling_loop",
        "status": "running",
        "target_lole_h": target,
        "basis": basis,
        "horizon_years": nyears,
        "draws": draws,
        "seed": seed,
        "eps0": eps0,
        "max_solves": max_solves,
        "restore": restore,
        "base_restored": False,
        "confident": False,
        "eps_star": None,
        "resolution_floor_h": floor_h,
        "solves_used": 0,
        "iterations": [],
        "final": None,
        "verdict": None,
        "warning": MC_WARNING_V1 + " " + LOOP_WARNING_V1,
        "error": None,
        "started_at": time.time(),
        "finished_at": None,
        "thread": None,
        "stop_event": stop_event,
    }

    def on_iteration(row: dict) -> None:
        """Grow the record by REBINDING, never by appending in place.

        ``get_coupling_loop`` serves a shallow copy, so an in-place append
        hands the serializer the very list this thread is mutating: a mid-run
        GET can then be written half-way through an append, and a client
        polling every second can watch its own earlier history change. A fresh
        list per iterate makes every snapshot immutable by construction — each
        GET's list is a prefix of the next, permanently.
        """
        with PyPSAService.get_solver_state_lock():
            record["iterations"] = record["iterations"] + [row]

    # The solver's own word on the closing re-solve, for the payload. A cell
    # rather than a return value because `_restore_closing` returns a bool
    # that four call sites already read (12e shipped-code review, S1).
    _restore_word: list = [None]

    def _restore_closing(met: bool, eps_star) -> bool:
        """The closing re-solve — the route's job, on EVERY path (spec §3,
        §1.3's pattern). The loop mutates the network once per iterate, so
        without this it is left on whichever ε happened to be last while the
        foreground results still describe the pre-study solve: the study
        silently rewrites the user's plan and says nothing.

        ``"final"`` is only meaningful on a met verdict — there is no
        certified cap otherwise — so it falls back to base rather than
        applying a cap nothing verified.
        """
        from services.adequacy.sweep import restore_is_clean
        from services.solver_service import run_simulation

        use_final = (restore == "final" and met and eps_star is not None)
        if use_final:
            final_cfg = dataclasses.replace(
                base_cfg, ens_cap_permyriad=float(eps_star))
            # Persisted through the normal config path (read-modify-write
            # under the solver-state lock, exactly as PUT /solver_config
            # does) BEFORE the solve: the user asked to hold ε*, and a
            # restore whose solve fails must still leave the setting they
            # will re-run with, with `base_restored` reporting the failure.
            with PyPSAService.get_solver_state_lock():
                solver_state["solver_config"] = dataclasses.replace(
                    solver_state["solver_config"],
                    ens_cap_permyriad=float(eps_star))
        else:
            final_cfg = base_cfg
        try:
            status, condition = run_simulation(
                final_cfg, n, lock, _threading.Event(), _queue.SimpleQueue(),
                state_update=state_update)
        except Exception as exc:                              # noqa: BLE001
            logger.exception(
                "coupling loop: the closing re-solve FAILED — the network is "
                "left on the last iterate's cap and the foreground results do "
                "not describe the plan the verdict is about")
            _restore_word[0] = f"raised: {exc}"
            return False
        # The CONDITION, through the shared predicate — never `status`. This
        # read `status in ("ok", "optimal")`, and linopy's `SolverStatus.ok`
        # also covers `time_limit`, `iteration_limit`, `terminated_by_limit`,
        # `suboptimal` and `imprecise`: a closing re-solve that hit the MIP
        # time limit (`mip_time_limit_s` is a shipped setting) reported `ok`,
        # so the panel said "restored" while the foreground was a time-limited
        # dispatch. The frontier and the contingency sweep had the same bug and
        # it was found there first (12e shipped-code review, S1); this is the
        # same defect in the two loops, fixed the same way and through the same
        # one predicate rather than a fourth copy of the vocabulary.
        word = str(condition or status)
        _restore_word[0] = word
        if not restore_is_clean(word):
            logger.warning(
                "coupling loop: the closing re-solve returned %r — it ran but "
                "did not restore the plan the verdict is about", word)
        return restore_is_clean(word)

    def _verdict_copy(status: str, eps_star, rows=None) -> str:
        _margin_bound = _margin_bound_flag[0]
        if status == "unreachable":
            # WHICH unreachable? The three-mechanism copy assumes the cap was
            # doing something and the MC disagreed with it. The commonest case
            # in practice (QA round S17) is that the cap never bound at all —
            # the LP sheds nothing at any ceiling because its outage-free view
            # already covers demand — and telling that user to check storage
            # foresight and DSR sends them after mechanisms that are not
            # happening. It is diagnosable from the rows, so diagnose it.
            solved = [r for r in (rows or [])
                      if r.get("solve_status") in ("ok", "optimal")]
            if solved and not any(r.get("binding") == "system_cap"
                                  for r in solved):
                # WHICH never-bound? `report.binding` is computed purely from
                # the ENS caps, so it reads "voll" even when a reserve margin
                # is what actually shaped the plan. Prescribing a margin to a
                # user who already set one reads as the tool not knowing what
                # they configured — the diagnosis is right, only the
                # recommendation is stale. The margin's own block says whether
                # it was in force, so ask it.
                if _margin_bound:
                    return NEVER_BOUND_WITH_MARGIN_COPY_V1
                return NEVER_BOUND_COPY_V1
            return UNREACHABLE_COPY_V1
        if status == "met" and eps_star is not None:
            # `format_lever_value`, never `%g` — and never the badge's two
            # significant figures either. The panel's own restore explainer
            # prints this same number, and the two disagreed IN THE SAME
            # PANEL: the verdict said 0.0347281 where the explainer said
            # 0.035. An ENS cap is a CEILING on unserved energy, so a value
            # rounded UP is a strictly LOOSER standard that need not
            # reproduce the certified plan. One number, spelled once.
            cap_text = format_lever_value(eps_star)
            if restore == "final":
                return (
                    f"A plan meeting {target:g} h was verified at ε* = "
                    f"{cap_text}‱, and that cap has been APPLIED to your "
                    f"solver settings (ens_cap_permyriad = {cap_text}) and "
                    "re-solved — the network you are holding is the certified "
                    "plan.")
            return (
                f"A plan meeting {target:g} h was verified at ε* = "
                f"{cap_text}‱. Your original config has been re-solved, so "
                "the network you are holding is NOT that plan: to keep it, set "
                f"ens_cap_permyriad = {cap_text} and re-solve.")
        if status == "aborted":
            return ("The study was aborted between iterates. Any iterates "
                    "already evaluated are shown; the closing restore ran, so "
                    "the network is back on your own config.")
        if status == "budget_exhausted":
            return (
                f"The solve budget ({max_solves}) was spent without verifying "
                "a plan that meets the target. Nothing here says the target is "
                "unreachable — only that this search did not reach it. Raise "
                "max_solves, or start from a tighter eps0.")
        return ("The study did not complete. The iterates recorded below are "
                "what it managed before it stopped.")

    def worker():
        res: dict | None = None
        err: str | None = None
        try:
            try:
                res = run_coupling_loop(
                    solve_at, evaluate, target_lole_h=target, eps0=eps0,
                    max_solves=max_solves, stop_event=stop_event,
                    on_iteration=on_iteration)
            except BaseException as exc:                      # noqa: BLE001
                # The controller is total by construction; this is the belt
                # for a broken binding above, and it must never leave the
                # record stuck on "running" for the rest of the session.
                logger.exception("coupling loop: the controller raised")
                err = str(exc)
        finally:
            status = (res or {}).get("status") or "failed"
            eps_star = (res or {}).get("eps_star")
            try:
                base_restored = _restore_closing(status == "met", eps_star)
            except BaseException:                             # noqa: BLE001
                logger.exception("coupling loop: the restore itself raised")
                base_restored = False
            iterations = (res or {}).get("iterations")
            if iterations is None:
                iterations = record["iterations"]
            warning = MC_WARNING_V1 + " " + LOOP_WARNING_V1
            if any(len((r.get("mc") or {}).get("by_period") or {}) > 1
                   for r in iterations):
                warning += " " + MULTI_PERIOD_WARNING_V1
            # ONE atomic apply, under the same lock `on_iteration` rebinds
            # under: a GET landing between the status flip and the verdict
            # would otherwise serve a finished study with a running study's
            # empty final, which is the one shape the panel cannot render.
            with PyPSAService.get_solver_state_lock():
                record.update(
                    status=status,
                    iterations=iterations,
                    final=(res or {}).get("final"),
                    confident=bool((res or {}).get("confident")),
                    eps_star=eps_star,
                    solves_used=int((res or {}).get("solves_used") or 0),
                    resolution_floor_h=eval_state["floor"],
                    base_restored=bool(base_restored),
                    # The solver's word, so a restore that RAN and still did
                    # not put the plan back can be named rather than merely
                    # denied (12e shipped-code review, S1).
                    base_restore_status=_restore_word[0],
                    verdict=_verdict_copy(
                        status, eps_star,
                        (res or {}).get("iterations")),
                    warning=warning,
                    error=err,
                    finished_at=time.time(),
                )

    # The worker carries the REQUEST's context (the /simulation/run pattern):
    # the active project lives in a ContextVar and a bare Thread does not
    # inherit it, so without this the closing restore's `state_update` and
    # the `restore="final"` config write would land in the PROCESS foreground
    # — a different project's state from the one the caller is polling. The
    # study record itself is CLOSED OVER rather than reached through `solver_state`
    # (post_mc's pattern), so it cannot be redirected by a context switch at
    # all; post_frontier's in-thread `solver_state["frontier"].update(...)` is the
    # anti-pattern this deliberately does not copy.
    _ctx = _contextvars.copy_context()
    t = _threading.Thread(target=lambda: _ctx.run(worker), daemon=True,
                          name="adequacy-coupling-loop")
    record["thread"] = t
    # Publish and START under one lock hold. `_study_running` tests
    # `thread.is_alive()`, and a registered-but-not-yet-started thread reports
    # False — so a second POST arriving in that window would read the record as
    # stale state, claim the surface, and put two loops on the same network.
    publish_study("coupling_loop", record, t)
    return {"status": "running", "target_lole_h": target, "draws": draws,
            "seed": seed, "eps0": eps0, "max_solves": max_solves,
            "restore": restore, "basis": basis,
            "resolution_floor_h": floor_h}
