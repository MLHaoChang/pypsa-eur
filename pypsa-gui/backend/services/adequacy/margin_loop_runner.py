"""Margin-loop HTTP runner — validation, bindings, worker, study publish.

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

MARGIN_LOOP_WARNING_V1 = (
    "The map from the reserve margin to MC-LOLE is a step function, not a "
    "curve: a range of margins forces the identical build and therefore the "
    "identical LOLE, so m* is the cheapest margin this search VERIFIED, not "
    "the smallest margin that would still pass. Every iterate is a genuine "
    "optimum of its own constrained problem, but only iterates whose own MC "
    "evaluation met the target are answers — the bracket is a search "
    "heuristic. The margin buys FIRM CAPACITY against a peak the LP already "
    "covers deterministically, which is why it can move a number no energy "
    "cap can; it does not buy energy, so a network short of energy rather "
    "than of capacity will not respond to it."
)

MARGIN_MULTI_PERIOD_WARNING_V1 = (
    "This network has more than one period: the margin is enforced per "
    "period against each period's own peak, while the target is a horizon "
    "SUM of LOLE — a single scalar margin cannot say 'fix period 3 only', so "
    "an unreachable or budget-exhausted verdict is structurally likelier "
    "here. The per-iterate by_period rows are the diagnostic. Note also that "
    "when the ACTIVE EXTENDABLE SET is identical in every period the LP has "
    "one horizon-wide nominal variable, so the standard degenerates to a "
    "single one at the largest peak (the report's `horizon_wide` flag)."
)

# The probe of §2.3 runs at the user's own margin — but at exactly 0 there is
# no standard at all (`_prm_margin` reads `<= 0` as "no margin"), the wrapper
# installs nothing and the report carries no reserve-margin block, so there
# would be nothing to read the incumbent's tight margin OFF. A margin this
# small is numerically "no margin" for every purpose except that it makes the
# standard — and therefore its report block — exist.
PROBE_MARGIN = 1e-4


class MarginLoopRequest(_BaseModel):
    # No `m0`: the starting margin is not a user parameter but a MEASUREMENT
    # (§2.3, the probing solve). A user-supplied start would be the one number
    # in this request that can silently make the study worthless — too small
    # and the search walks through a region where the plan does not change,
    # too large and it overshoots the bracket entirely.
    target_lole_h: _Finite | None = None
    draws: int | None = None
    seed: int | None = None
    max_solves: int | None = None
    restore: str | None = None


def start_margin_loop(
    body: MarginLoopRequest | None,
    *,
    solver_state: dict,
    state_update,
    publish_study,
):
    """
    Drive the PLANNING RESERVE MARGIN until the plan meets the user's target
    on the sequential MC's own LOLE (margin-loop spec §2).

    The sibling of ``POST /results/coupling_loop`` and the answer to the case
    that one cannot serve: on a network whose firm capacity already covers
    demand deterministically, the LP sheds nothing at any energy cap, so no ε
    changes the plan and the cap loop can only report ``unreachable``. The
    loss of load the MC sees there comes from OUTAGES the LP does not model at
    all, and the lever that buys firm capacity the LP sees no deterministic
    reason to build is the margin.

    ASYNCHRONOUS BY CONSTRUCTION: one probing solve plus up to ``max_solves``
    full capacity expansions with an MC evaluation each, plus the closing
    restore.

    VALIDATION IS SYNCHRONOUS WHEREVER IT IS CHEAP, and for this lever that is
    the whole set — ``reserve_margin_facts`` is explicitly preflight-callable
    ("nothing in this function touches ``n.model``"), so the ceiling and the
    unpriceable-asset refusal both cost zero solves. Two of the cap loop's
    refusals are deliberately NOT copied: a VoLL is not required (the margin
    is a constraint, not a price), and ``myopic`` is allowed (each myopic
    iteration's snapshots are exactly one investment period, which is the peak
    the standard is defined against — the margin's own validator downgrades it
    to a warning, and refusing it here would deny a supported configuration).
    """
    import dataclasses
    import hashlib
    import queue as _queue
    import time

    from services.adequacy.coupling import MAX_LOOP_SOLVES, run_coupling_loop
    from services.adequacy.lever_text import format_lever_value
    from services.adequacy.margin_lever import (
        MAX_MARGIN,
        STEP_OVERSHOOT,
        to_margin,
        to_x,
    )
    from services.adequacy.mc import (
        MAX_DRAWS,
        MC_WARNING_V1,
        mc_adequacy,
        snapshot_inputs,
    )
    from services.adequacy.metrics import horizon_years, resolve_time_basis
    from services.adequacy.sweep import _solve_once
    from services.solver_service import _prm_margin, reserve_margin_facts
    from services.validation_service import (
        _check_nonfinite_bounds,
        _check_reserve_margin,
    )

    # ── the synchronous 422 set (§2.4) ────────────────────────────────────
    target = getattr(body, "target_lole_h", None)
    try:
        target = float(target) if target is not None else None
    except (TypeError, ValueError):
        target = None
    if target is None or not (target > 0):
        raise HTTPException(
            422,
            "target_lole_h is required and must be > 0: the loop searches for "
            "the cheapest reserve margin whose plan meets a RELIABILITY "
            "STANDARD, and a target of zero (or none) is not a standard — it "
            "is the demand that no draw ever sheds an hour, which no finite "
            "plan can buy")

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
            "budget is the wall-clock promise this request makes (the probing "
            "solve of the informed step is one more, outside it)")

    restore = getattr(body, "restore", None) or "base"
    if restore not in ("base", "final"):
        raise HTTPException(
            422,
            f"restore must be 'base' or 'final' (got {restore!r}): 'base' "
            "re-solves with your original config, 'final' leaves you holding "
            "the certified plan at m*")

    cfg = solver_state.get("solver_config")
    if cfg is None:
        raise HTTPException(
            422, "no solver configuration is set for this project — the loop "
                 "builds every iterate from it")

    # NO VoLL REQUIREMENT (§2.4). The cap loop needs one because without
    # load-shedding slacks its lever constrains nothing; the margin is a
    # CONSTRAINT on installed firm capacity and binds whether or not unserved
    # energy carries a price.

    strategy = str(getattr(cfg, "solve_strategy", "full") or "full")
    if strategy == "rolling":
        raise HTTPException(
            422,
            "the reserve margin is not supported with the 'rolling' solve "
            "strategy: PyPSA solves each window independently, so the "
            "constraint would be built against that WINDOW's peak demand "
            "rather than the period's — a weaker standard than the one you "
            "set, enforced under its name. Every iterate would fail the same "
            "blocking validation and the loop would report 'unreachable', "
            "which is a statement about the strategy, not about the network. "
            "Use the full strategy. ('myopic' IS supported: each iteration's "
            "snapshots are exactly one investment period, which is the peak "
            "the standard is defined against — only its report is partial.)")

    # The ONE snapshot the validation reads, taken under the mutation lock.
    # `keep_zero_capacity=True` from the very first call (coupling spec §1.2):
    # the sampled fleet's MEMBERSHIP must be invariant across iterates or the
    # positional CRN substreams shift under it — and it is what keeps the
    # UNBUILT peaker, the very asset a margin exists to force into being, in
    # the fleet at all.
    n = PyPSAService.get_network()
    lock = PyPSAService.get_lock()
    with lock:
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

    # The up-front resolution floor. One shortfall hour in one draw
    # contributes that hour's WEIGHT to the mean, so the smallest non-zero
    # LOLE these draws can resolve is `min positive weight / draws`. A target
    # under it is undecidable.
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

    base_cfg = cfg
    basis = resolve_time_basis(nyears)

    # ── the facts the standard knows before an LP exists (§2.4) ───────────
    #
    # `reserve_margin_facts` returns None for a config with no margin set, so
    # the preflight reads it at a NOMINAL margin. Only `required_mw` scales
    # with that number; the peaks, the derates, the unpriceable list and
    # `max_achievable_mw` — everything read below — do not.
    m_user = _prm_margin(base_cfg) or 0.0
    probe_margin = max(m_user, PROBE_MARGIN)
    facts_cfg = dataclasses.replace(base_cfg, reserve_margin=probe_margin)
    with lock:
        try:
            facts = reserve_margin_facts(n, facts_cfg)
        except Exception as exc:                              # noqa: BLE001
            raise HTTPException(
                422,
                "the firm-capacity standard could not be measured on this "
                f"network, so the loop has no ceiling to search under: {exc}"
            ) from exc
        # Unpriceable assets — refused with the VALIDATOR'S OWN SENTENCE, not
        # a second one. The loop's own gate would pass (it needs one priceable
        # unit, not all of them) and then EVERY iterate would fail the same
        # blocking validation, ending `budget_exhausted` and advising "raise
        # max_solves", which can never work here.
        margin_issues = _check_reserve_margin(n, facts_cfg)
        # Phase 12f: the SAME up-front refusal, for the same reason. A
        # non-finite value in one of the five finite-default LP bounds is a
        # blocking preflight error, so every iterate would fail validation and
        # the loop would end `budget_exhausted` advising "raise max_solves" —
        # `_margin_out_of_reach` only relabels `validation_failed` when the
        # MARGIN is the cause, and it is not here. This check has to be CALLED,
        # not merely allowed through the filter below: `_check_reserve_margin`
        # is one sub-validator, not `validate_for_run`, so a code it never
        # produces can never appear in `margin_issues`.
        margin_issues = margin_issues + _check_nonfinite_bounds(n)
    for iss in margin_issues:
        # Phase 12g: every `nonfinite_*` code, by prefix. The first version
        # listed the two 12f codes literally, so each category 12g adds would
        # have slipped past this guard and the loop would have spent its
        # budget refusing — the K6 outcome this guard exists to prevent.
        if iss.code == "reserve_margin_unpriceable_assets" \
                or iss.code.startswith("nonfinite_"):
            raise HTTPException(422, iss.message)

    # The ceiling: a margin is achievable iff EVERY period can reach it, so
    # the binding period is the one that fails first and the aggregate is
    # `min`, not `max` (plan §2.2 — `max` would let the search run past a
    # margin one period already makes impossible). `max_achievable_mw` is
    # `inf` on the ordinary network (PyPSA's default `p_nom_max`), where the
    # ceiling is the schema's own bound instead.
    m_max = math.inf
    m_max_where: tuple[str, float, float] | None = None
    for P, per in ((facts or {}).get("stash", {}).get("periods") or {}).items():
        try:
            peak = float(per.get("peak_mw") or 0.0)
            reach = float(per.get("max_achievable_mw") or 0.0)
        except (TypeError, ValueError):
            continue
        if peak <= 0:
            continue
        here = reach / peak - 1.0
        if here < m_max:
            m_max, m_max_where = here, (str(P), peak, reach)
    if m_max <= 0:
        where = ""
        if m_max_where is not None and m_max_where[0] != "ALL":
            where = f" in period {m_max_where[0]}"
        peak_mw = m_max_where[1] if m_max_where else 0.0
        reach_mw = m_max_where[2] if m_max_where else 0.0
        raise HTTPException(
            422,
            f"no reserve margin is reachable on this network{where}: the "
            f"whole fleet — every extendable at its p_nom_max, derated — tops "
            f"out at {reach_mw:,.1f} MW against a {peak_mw:,.1f} MW peak, a "
            f"margin of {m_max:.1%}, so even a margin of 0 % (firm capacity "
            "equal to the peak) is out of reach. Every iterate would be "
            "refused by the same blocking preflight the solver runs, and the "
            "loop would spend its whole budget proving it. Raise a p_nom_max, "
            "add candidate capacity, or enter outage data for assets the "
            "standard currently cannot price.")
    # The search's own upper bound: the fleet ceiling, or the schema's `le=5`
    # when the fleet is unbounded.
    m_ceiling = min(m_max, MAX_MARGIN)

    # ── the bindings (§2.1) ───────────────────────────────────────────────

    def _snapshot():
        # `base_cfg` is captured in the request — the worker never reads
        # `solver_state` — and the scalers do not change across iterates.
        with lock:
            return snapshot_inputs(n, keep_zero_capacity=True, cfg=base_cfg)

    def _hash(mc_inputs) -> str:
        """sha256 over exactly what the MC reads — the sorted
        ``(name, capacity_mw)`` unit vector, the sorted
        ``(name, p_nom_mw, e_nom_mwh)`` storage vector, and the residual
        bytes. NOT the objective: degenerate optima give equal cost for
        different plans. Equal hash ⇒ bit-identical MC under the same seed and
        draw count, so the controller's plateau reuse is exact."""
        # Phase 12d: one implementation for both loops, testable
        # (`tests/test_adequacy_activity.py` E8).
        return _snapshot_hash(mc_inputs)

    def _margin_out_of_reach(m: float) -> str | None:
        """Is THIS margin impossible from the candidate set — the same
        constant arithmetic `_check_reserve_margin` blocks on, asked at a
        specific margin (§2.5)?

        A second implementation of the derating chain here would be a second
        standard, so the numbers come from `reserve_margin_facts` — the very
        function the validator and the LP wrapper share.
        """
        try:
            with lock:
                f = reserve_margin_facts(
                    n, dataclasses.replace(base_cfg, reserve_margin=float(m)))
        except Exception:                                     # noqa: BLE001
            return None
        if not f:
            return None
        for P, per in (f["stash"].get("periods") or {}).items():
            try:
                required = float(per.get("required_mw") or 0.0)
                reach = float(per.get("max_achievable_mw") or 0.0)
            except (TypeError, ValueError):
                continue
            if required <= 0 or not math.isfinite(required):
                continue
            if reach < required:
                where = "" if str(P) == "ALL" else f" in period {P}"
                return (
                    f"infeasible: no plan built from this candidate set "
                    f"reaches a {m:.1%} reserve margin{where} — it needs "
                    f"{required:,.1f} MW of derated firm capacity and the "
                    f"whole fleet tops out at {reach:,.1f} MW")
        return None

    _ceiling_missed = [False]
    _last_at_ceiling = [False]
    # IEEE 39-bus review, F1. The CLAMP below evaluates `m_ceiling` when the
    # controller's step overshoots the fleet ceiling — but the controller
    # records the coordinate it ASKED for (`coupling.py:_row`), and that
    # coordinate is what `_translate`, `lever_star`, the verdict and the
    # `restore="final"` config write all read. Measured on the stressed
    # IEEE 39 network (ceiling 19.9 %): rows 15.75 % -> 363 % -> 131.5 %,
    # verdict "verified at a reserve margin of 131.5%", `reserve_margin` left
    # at 1.315, and the closing re-solve refused by the study's own preflight
    # (`reserve_margin_unreachable`). The margin ACTUALLY solved is recorded
    # here, keyed by the controller's `x`, and consulted at the two places a
    # coordinate becomes a user-facing margin. The controller never solves one
    # `x` twice and the pre-controller probe never becomes a row, so the map is
    # unambiguous; an `x` not in it (a solve refused before it ran) still
    # translates exactly as before.
    _solved_margin: dict[float, float] = {}

    def solve_at(x: float) -> dict:
        _last_at_ceiling[0] = False
        """One capacity-expansion solve at the margin ``x`` stands for, read
        out exactly as ``run_frontier_sweep`` reads its points. Solve failures
        come back as a status — the controller is specified never to see an
        exception from here, and a raise would cost the whole study."""
        m = to_margin(x)
        out = {"status": None, "condition": None, "cost_eur": None,
               "ens_mwh": None, "cap_mwh": None, "binding": None,
               "report": None}

        if m > MAX_MARGIN * (1.0 + 1e-9):
            # The controller's blind step multiplies the margin by ~4 per
            # iterate, so it can walk past the schema's own bound in two
            # steps. Solving there would build a plan against a margin the
            # config schema refuses — and `restore="final"` would then persist
            # a value the next PUT rejects. Stopping is honest and free.
            out.update(
                status="error",
                condition=(
                    f"infeasible: a reserve margin of {m:.1%} is beyond the "
                    f"configured maximum of {MAX_MARGIN:.0%} — the search has "
                    "run out of lever, not out of budget"))
            return out

        # THE CLAMP, and it is the difference between a verdict and a guess.
        # The controller's blind step multiplies the margin ~4x per iterate, so
        # from a small start it can leap clean over `m_ceiling` — and an
        # over-ceiling solve fails validation, gets relabelled `infeasible`
        # below, and the nesting logic then (correctly, given what it was told)
        # concludes every stricter margin is infeasible too. The loop reports
        # `unreachable` having never evaluated the reachable region at all.
        # Found live in S19.3: ceiling 271%, last evaluated margin 18%, verdict
        # "unreachable" — with a plan that meets the target sitting between
        # them. Clamping makes the strictest REACHABLE margin the thing that
        # gets evaluated, so an `unreachable` verdict is one this study
        # actually verified.
        if m > m_ceiling:
            if _ceiling_missed[0]:
                # Already evaluated AT the ceiling and it missed. Nothing
                # stricter exists to try, so this is a real refusal rather
                # than another clamp to the same plan.
                out.update(
                    status="error",
                    condition=(
                        f"infeasible: the strictest reachable margin "
                        f"({m_ceiling:.1%}) was evaluated and still missed the "
                        "target — the candidate set, not the search, is the "
                        "limit"))
                return out
            m = m_ceiling
            _last_at_ceiling[0] = True
        _solved_margin[float(x)] = float(m)
        sink: dict = {}
        _solve_once(dataclasses.replace(base_cfg, reserve_margin=m),
                    n, lock, None, sink)
        status = sink.get("_status")
        condition = sink.get("_condition")
        out.update(status=status, condition=condition)

        if status not in ("ok", "optimal"):
            # §2.5. An out-of-reach margin is a BLOCKING PREFLIGHT ERROR, not
            # an infeasible LP (linopy raises TypeError on a constant
            # constraint and `Generator-p_nom` does not exist when nothing
            # extendable is active), so it arrives as `validation_failed` —
            # which `_is_infeasible` matches on neither the status nor the
            # condition. The controller would treat it as transient, keep
            # stepping, and end `budget_exhausted` advising "raise
            # max_solves", which can never work. Relabel it — but ONLY when
            # the facts confirm the margin is the cause: a validation failure
            # from anything else is not monotone in the margin and proves
            # nothing about tighter ones.
            if "validation_failed" in str(condition).lower():
                why = _margin_out_of_reach(m)
                if why is not None:
                    out["condition"] = why
            return out

        rep = sink.get("adequacy_report")
        if not rep:
            # A margin WAS set and the solve succeeded, so the report is the
            # contract. Its absence is a defect, and reporting it as a failed
            # iterate keeps the loop from evaluating a plan it cannot describe.
            out.update(status="no_report",
                       condition="the solve returned no adequacy report")
            return out

        # §2.1: `binding` comes from the MARGIN's own block. `target.binding`
        # is computed purely from the ENS caps and reads "voll" on every
        # margin run, which would make the controller's `reusable` pre-test
        # (`binding != "system_cap"`) permanently true and offer plateau reuse
        # on iterates where the margin demonstrably rebuilt the plan.
        rm_rows = ((rep.get("reserve_margin") or {}).get("by_period")) or []
        binding = ("system_cap" if any(bool(r.get("binding")) for r in rm_rows)
                   else "voll")

        metrics_blk = rep.get("metrics") or {}
        ens = metrics_blk.get("ens_mwh")
        if ens is None:
            ens = ((rep.get("target") or {}).get("system") or {}).get(
                "achieved_ens_mwh")
        try:
            ens = float(ens) if ens is not None else None
        except (TypeError, ValueError):
            ens = None

        out.update(
            report=rep,
            cost_eur=float(rep["cost"]["total_system_cost_eur"]),
            ens_mwh=ens,
            # §2.2, and it is LOAD-BEARING. The controller ends the search
            # with `unreachable` when `cap_mwh is not None and cap_mwh <
            # ENERGY_FLOOR_MWH`. On a margin-only report `cap_mwh` is `0.0` —
            # the ENS cap's per-period loop never runs, so `SystemTarget.
            # cap_mwh` is emitted as its initialised zero — so passing it
            # through fires that test on the FIRST miss and every run ends
            # `unreachable` after one solve, indistinguishable in the payload
            # from the real thing. None makes the test a genuine no-op, which
            # is the only correct reading for a lever with no energy cap.
            cap_mwh=None,
            binding=binding,
        )
        return out

    def _solved_margin_at(x: float) -> float | None:
        """What ``solve_at`` would ACTUALLY solve at ``x``, without solving —
        the controller's optional ``solved_value`` probe (B1 / IEEE 39-bus
        review, F5).

        The clamp above maps every coordinate whose margin exceeds the fleet
        ceiling onto that one ceiling, so the refinement bisection can ask for
        a NEW coordinate that stands for a standard already solved: a full
        capacity expansion plus its MC, spent rebuilding the met endpoint's
        own plan and stopped only by the plan-hash check afterwards. Two
        ceiling iterates with identical cost and identical MC LOLE are what
        that looks like in the record (measured on the stressed IEEE 39-bus
        network).

        It MIRRORS `solve_at`'s decision tree rather than approximating it,
        and returns None wherever that function answers without solving —
        None is "nothing would be solved here", which the controller reads as
        "cannot tell" and pays the solve for. Both branches are cheap and
        pure: `to_margin` is arithmetic and the ceiling was computed before
        the study started.
        """
        try:
            m = float(to_margin(x))
        except ValueError:
            return None
        if m > MAX_MARGIN * (1.0 + 1e-9):
            return None                       # refused: out of lever
        if m > m_ceiling:
            if _ceiling_missed[0]:
                return None                   # refused: the ceiling missed
            return float(m_ceiling)
        return m

    solve_at.solved_value = _solved_margin_at

    eval_state: dict = {"floor": floor_h}

    def evaluate():
        """IDENTICAL to the coupling loop's (§2.1): the same snapshot with
        `keep_zero_capacity=True`, the same pinned
        `mc_adequacy(inputs, draws=N, seed=S, max_draws=N)` call — merely
        ignoring `cov_target` would leave the adaptive cap in play and
        `n_samples` would drift between iterates, breaking the common random
        numbers the plateau reuse rests on — and the same plan hash."""
        mc_inputs = _snapshot()
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
        return _hash(_snapshot())

    evaluate.plan_hash = _plan_hash

    def _position_x0() -> tuple[float, float | None]:
        """§2.3 — the informed step, done by PRE-POSITIONING x0 rather than by
        changing the controller.

        With `cap_mwh=None` the controller's informed term is skipped and
        `_tighten` degrades to the blind `x/4`, which in margin terms is
        `m: 0 → 3` — a large but safe first jump that spends a solve learning
        nothing when the incumbent plan already carries a comfortable margin.
        So the route measures the incumbent first:

            m_tight = min over P of (firm_mw_P / peak_mw_P) − 1
            x0      = to_x(m_tight · (1 + STEP_OVERSHOOT))

        `m_tight` is the smallest margin at which the incumbent plan is TIGHT
        — at exactly that value the plan is feasible, unchanged, same hash,
        same LOLE, and flagged `binding` while nothing moved — so the step
        must STRICTLY exceed it, hence the overshoot. The aggregate is `min`
        because the first period to bind is the binding one; `max` would step
        past it and overshoot the bracket entirely.

        Returns ``(x0, m_tight)``.
        """
        res = solve_at(to_x(probe_margin))
        tights: list[float] = []
        if res.get("status") in ("ok", "optimal"):
            rows = ((res.get("report") or {}).get("reserve_margin") or {}).get(
                "by_period") or []
            for row in rows:
                try:
                    peak = float(row.get("peak_mw") or 0.0)
                    firm = float(row.get("firm_mw") or 0.0)
                except (TypeError, ValueError):
                    continue
                if peak > 0 and math.isfinite(firm):
                    tights.append(firm / peak - 1.0)
        if not tights:
            # No measurement (a failed probe, or a report with no usable
            # period): fall back to the user's own margin and let the
            # controller's blind step do the searching. A guess here would be
            # a worse start than no start.
            logger.info("margin loop: no tight margin measurable from the "
                        "probing solve; starting from the configured margin")
            m_start = min(max(probe_margin, STEP_OVERSHOOT), m_ceiling)
            return to_x(max(m_start, PROBE_MARGIN)), None
        m_tight = min(tights)
        base = max(m_tight, 0.0)
        m_start = base * (1.0 + STEP_OVERSHOOT)
        if not m_start > base:
            # `base == 0`: the incumbent is tight at (or below) a zero margin,
            # where a multiplicative overshoot is still zero — and a margin of
            # 0 installs no standard at all. The smallest step that changes
            # anything is the overshoot itself.
            m_start = STEP_OVERSHOOT
        m_start = max(min(m_start, m_ceiling), PROBE_MARGIN)
        return to_x(m_start), m_tight

    def _translate(row: dict) -> dict:
        """§2.6: the controller's `eps_permyriad` IS the substitution's `x`,
        an internal coordinate with no meaning to a user — 0.76 is not a
        margin, not a percentage and not a per-myriad ENS cap. Every row is
        translated on its way into the record, so `x` never reaches the wire
        at all.

        F1 (IEEE 39-bus review): the margin SOLVED, not the one the
        controller asked for — they differ on a clamped iterate."""
        return {
            "lever_value": _solved_margin.get(
                float(row["eps_permyriad"]), to_margin(row["eps_permyriad"])),
            "solve_status": row["solve_status"],
            "condition": row["condition"],
            "cost_eur": row["cost_eur"],
            "ens_mwh": row["ens_mwh"],
            "cap_mwh": row["cap_mwh"],
            "binding": row["binding"],
            "plateau": row["plateau"],
            "mc": row["mc"],
        }

    # ── the record (closed over, never reached through `solver_state`) ──────────
    stop_event = _threading.Event()
    record: dict = {
        "study": "margin_loop",
        # The frontend discriminator (spec §3): the column header, the badge
        # suffix and `restoreSentence`'s CONFIG FIELD NAME all come off these,
        # so a margin run can never tell the user to set the cap's field.
        "lever": "reserve_margin",
        "lever_label": "planning reserve margin",
        "lever_unit": "%",
        "status": "running",
        "target_lole_h": target,
        "basis": basis,
        "horizon_years": nyears,
        "draws": draws,
        "seed": seed,
        "margin0": None,
        "margin_tight": None,
        # M7: the FLEET ceiling, null when unbounded — `m_ceiling` also
        # carries the schema cap the search stops at, which is not a
        # ceiling any fleet has.
        "margin_ceiling": (None if not math.isfinite(m_max)
                           else float(m_max)),
        # …and the bound the SEARCH actually stops at, always finite
        # (whole-branch review, B2). The two are different numbers whenever
        # the fleet is unbounded or reaches past the schema's own cap, and
        # the panel showed only the first: "ceiling unbounded" beside an
        # `unreachable` verdict whose own sentence says "the search is
        # bounded above by 500%". Both true, one about the fleet and one
        # about the search, and read together in one panel they contradict.
        # The verdict copy below reads this same value, so the two cannot
        # drift.
        "search_ceiling": float(m_ceiling),
        "max_solves": max_solves,
        "restore": restore,
        "base_restored": False,
        "confident": False,
        "lever_star": None,
        "resolution_floor_h": floor_h,
        "solves_used": 0,
        # The probing solve of §2.3 is OUTSIDE the controller's budget, so it
        # is reported separately rather than folded into `solves_used`: the
        # budget is a promise about the search, and a user timing the run
        # should be able to account for every solve it made.
        "probe_solves": 0,
        "iterations": [],
        "final": None,
        "verdict": None,
        "warning": MC_WARNING_V1 + " " + MARGIN_LOOP_WARNING_V1,
        "error": None,
        "started_at": time.time(),
        "finished_at": None,
        "thread": None,
        "stop_event": stop_event,
    }

    def on_iteration(row: dict) -> None:
        """Grow the record by REBINDING, never by appending in place.

        `get_margin_loop` serves a shallow copy, so an in-place append hands
        the serializer the very list this thread is mutating: a mid-run GET
        can then be written half-way through an append, and a client polling
        every second can watch its own earlier history change. A fresh list
        per iterate makes every snapshot immutable by construction.
        """
        # Did the iterate that just finished sit AT the ceiling and miss? If
        # so no stricter margin exists to try, and `solve_at` refuses the next
        # request outright rather than clamping to the same plan forever.
        mc = row.get("mc") or {}
        lole = mc.get("lole_hours")
        if (_last_at_ceiling[0] and lole is not None
                and float(lole) > target):
            _ceiling_missed[0] = True
        with PyPSAService.get_solver_state_lock():
            record["iterations"] = record["iterations"] + [_translate(row)]

    # The solver's own word on the closing re-solve, for the payload. A cell
    # rather than a return value because `_restore_closing` returns a bool
    # that four call sites already read (12e shipped-code review, S1).
    _restore_word: list = [None]

    def _restore_closing(met: bool, m_star) -> bool:
        """The closing re-solve — the route's job, on EVERY path. The loop
        mutates the network once per iterate, so without this it is left on
        whichever margin happened to be last while the foreground results
        still describe the pre-study solve.

        `"final"` writes **`reserve_margin`** and nothing else: a user-set ENS
        cap is carried through untouched (every config here is built from
        `base_cfg`), because the study tuned one standard and the user asked
        for both.
        """
        from services.adequacy.sweep import restore_is_clean
        from services.solver_service import run_simulation

        use_final = (restore == "final" and met and m_star is not None)
        if use_final:
            final_cfg = dataclasses.replace(
                base_cfg, reserve_margin=float(m_star))
            # Persisted through the normal config path (read-modify-write
            # under the solver-state lock, exactly as PUT /solver_config does)
            # BEFORE the solve: the user asked to hold m*, and a restore whose
            # solve fails must still leave the setting they will re-run with,
            # with `base_restored` reporting the failure.
            with PyPSAService.get_solver_state_lock():
                solver_state["solver_config"] = dataclasses.replace(
                    solver_state["solver_config"], reserve_margin=float(m_star))
        else:
            final_cfg = base_cfg
        try:
            status, condition = run_simulation(
                final_cfg, n, lock, _threading.Event(), _queue.SimpleQueue(),
                state_update=state_update)
        except Exception as exc:                              # noqa: BLE001
            logger.exception(
                "margin loop: the closing re-solve FAILED — the network is "
                "left on the last iterate's margin and the foreground results "
                "do not describe the plan the verdict is about")
            _restore_word[0] = f"raised: {exc}"
            return False
        # The CONDITION, through the shared predicate — see the coupling
        # loop's twin for why the status is the wrong half to read.
        word = str(condition or status)
        _restore_word[0] = word
        if not restore_is_clean(word):
            logger.warning(
                "margin loop: the closing re-solve returned %r — it ran but "
                "did not restore the plan the verdict is about", word)
        return restore_is_clean(word)

    def _both_standards_clause() -> str:
        try:
            cap = float(getattr(base_cfg, "ens_cap_permyriad", 0.0) or 0.0)
        except (TypeError, ValueError):
            cap = 0.0
        if cap <= 0:
            return ""
        return (
            f" Your energy cap (ens_cap_permyriad = {cap:g}‱) was left in "
            "force for every iterate and was never rewritten, so the "
            "certified plan meets BOTH standards.")

    def _verdict_copy(status: str, m_star, rows=None) -> str:
        if status == "met" and m_star is not None:
            if restore == "final":
                return (
                    f"A plan meeting {target:g} h was verified at a reserve "
                    f"margin of {m_star:.1%}, and that margin has been "
                    f"APPLIED to your solver settings (reserve_margin = "
                    f"{format_lever_value(m_star)}) and re-solved — the "
                    "network you are holding "
                    "is the certified plan." + _both_standards_clause())
            return (
                f"A plan meeting {target:g} h was verified at a reserve "
                f"margin of {m_star:.1%}. Your original config has been "
                "re-solved, so the network you are holding is NOT that plan: "
                # `format_lever_value`, never `%g`: the panel's own
                # restore explainer prints this same number two lines
                # above, and `%g`'s six significant figures made the
                # two disagree IN THE SAME PANEL — the verdict said
                # 0.6716 where the explainer said 0.671600430725. A
                # margin is a THRESHOLD on required firm capacity, so
                # the shorter value is a strictly LOOSER standard that
                # need not reproduce the certified plan.
                f"to keep it, set reserve_margin = "
                f"{format_lever_value(m_star)} and re-solve."
                + _both_standards_clause())
        if status == "unreachable":
            ceiling = (f"{m_ceiling:.1%}" if math.isfinite(m_ceiling)
                       else "unbounded")
            reason = (
                "the largest margin your candidate set can reach"
                if m_max <= MAX_MARGIN else
                "the largest margin the configuration schema allows")
            return (
                "No reserve margin this search could reach produced a plan "
                f"that met {target:g} h on the MC's own LOLE. The search is "
                f"bounded above by {ceiling} — {reason} — and beyond it no "
                "plan exists at all: the margin is refused by the same "
                "blocking preflight the solver runs, rather than by an "
                "infeasible LP. Under that ceiling, three mechanisms produce "
                "a miss and they call for different responses: (a) the added "
                "firm capacity is DERATED by the same outage data the MC "
                "samples, so a fleet of unreliable units buys less than its "
                "nameplate; (b) the standard is enforced at the PEAK, while "
                "loss of load in the MC can fall in hours the peak-"
                "coincidence window never measured; (c) energy-limited "
                "resources take a duration haircut, so a plan that meets the "
                "margin on storage can still run out of energy in a long "
                "outage. Check the per-iterate binding column and the "
                "by_period rows, then consider raising a p_nom_max, adding "
                "candidate capacity, or lowering the target.")
        if status == "aborted":
            return ("The study was aborted between iterates. Any iterates "
                    "already evaluated are shown; the closing restore ran, so "
                    "the network is back on your own config.")
        if status == "budget_exhausted":
            return (
                f"The solve budget ({max_solves}) was spent without verifying "
                "a plan that meets the target. Nothing here says the target "
                "is unreachable — only that this search did not reach it. "
                "Raise max_solves.")
        return ("The study did not complete. The iterates recorded below are "
                "what it managed before it stopped.")

    def worker():
        res: dict | None = None
        err: str | None = None
        try:
            try:
                x0, m_tight = _position_x0()
                with PyPSAService.get_solver_state_lock():
                    record["probe_solves"] = 1
                    record["margin0"] = to_margin(x0)
                    record["margin_tight"] = m_tight
                res = run_coupling_loop(
                    solve_at, evaluate, target_lole_h=target, eps0=x0,
                    max_solves=max_solves, stop_event=stop_event,
                    on_iteration=on_iteration)
            except BaseException as exc:                      # noqa: BLE001
                # The controller is total by construction; this is the belt
                # for a broken binding above, and it must never leave the
                # record stuck on "running" for the rest of the session.
                logger.exception("margin loop: the controller raised")
                err = str(exc)
        finally:
            status = (res or {}).get("status") or "failed"
            x_star = (res or {}).get("eps_star")
            m_star = None
            if x_star is not None:
                try:
                    # F1: the margin that iterate actually solved (they differ
                    # when the ceiling clamp fired), so the verdict names — and
                    # `restore="final"` persists — a margin the preflight
                    # accepts.
                    m_star = _solved_margin.get(float(x_star),
                                                to_margin(x_star))
                except ValueError:                            # noqa: BLE001
                    logger.exception("margin loop: unusable eps_star %r",
                                     x_star)
            try:
                base_restored = _restore_closing(status == "met", m_star)
            except BaseException:                             # noqa: BLE001
                logger.exception("margin loop: the restore itself raised")
                base_restored = False

            src_rows = (res or {}).get("iterations")
            if src_rows is None:
                rows_out = record["iterations"]
                final_out = None
            else:
                rows_out = [_translate(r) for r in src_rows]
                final_src = (res or {}).get("final")
                # Identity, not equality: two rows CAN carry the same numbers
                # (a plateau iterate differs only in its lever value, and a
                # broken binding could make even that equal), and picking the
                # wrong one would report a different iterate as the answer.
                final_out = next(
                    (out for src, out in zip(src_rows, rows_out)
                     if src is final_src), None)

            warning = MC_WARNING_V1 + " " + MARGIN_LOOP_WARNING_V1
            if any(len((r.get("mc") or {}).get("by_period") or {}) > 1
                   for r in rows_out):
                warning += " " + MARGIN_MULTI_PERIOD_WARNING_V1
            # ONE atomic apply, under the same lock `on_iteration` rebinds
            # under: a GET landing between the status flip and the verdict
            # would otherwise serve a finished study with a running study's
            # empty final, which is the one shape the panel cannot render.
            with PyPSAService.get_solver_state_lock():
                record.update(
                    status=status,
                    iterations=rows_out,
                    final=final_out,
                    confident=bool((res or {}).get("confident")),
                    lever_star=m_star,
                    solves_used=int((res or {}).get("solves_used") or 0),
                    resolution_floor_h=eval_state["floor"],
                    base_restored=bool(base_restored),
                    base_restore_status=_restore_word[0],
                    verdict=_verdict_copy(status, m_star, rows_out),
                    warning=warning,
                    error=err,
                    finished_at=time.time(),
                )

    # The worker carries the REQUEST's context (the /simulation/run pattern):
    # the active project lives in a ContextVar and a bare Thread does not
    # inherit it, so without this the closing restore's `state_update` and
    # the `restore="final"` config write would land in the PROCESS foreground
    # — a different project's state from the one the caller is polling.
    _ctx = _contextvars.copy_context()
    t = _threading.Thread(target=lambda: _ctx.run(worker), daemon=True,
                          name="adequacy-margin-loop")
    record["thread"] = t
    # Publish and START under one lock hold. `_study_running` tests
    # `thread.is_alive()`, and a registered-but-not-yet-started thread reports
    # False — so a second POST arriving in that window would read the record as
    # stale state, claim the surface, and put two loops on the same network.
    publish_study("margin_loop", record, t)
    return {"status": "running", "study": "margin_loop",
            "lever": "reserve_margin", "target_lole_h": target,
            "draws": draws, "seed": seed, "max_solves": max_solves,
            "restore": restore, "basis": basis,
            "margin_ceiling": (None if not math.isfinite(m_max)
                               else float(m_max)),
            "resolution_floor_h": floor_h}
