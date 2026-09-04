"""
The adequacy-coupled planning loop — the controller (spec §2, plan §1).

Realises the Phase-6 decision record's candidate (i): **solve the LP under an
energy cap → run the sequential MC on the PLAN it produced → retune the cap →
re-solve**, until the plan meets the user's target on the MC's own LOLE rather
than on the LP proxy's shed energy. The two numbers are not the same standard:
the LP has perfect foresight over storage and no outages at all, so a plan that
sheds exactly its cap in the LP can lose load for tens of hours in the MC.

**This module is PURE CONTROL FLOW.** It imports no route, no `_state`, no
lock, no `pypsa`, and never sleeps. Everything expensive is behind the two
callables it is handed:

* ``solve_at(eps) -> SolveResult`` — one capacity-expansion solve at that cap,
  read out exactly as ``run_frontier_sweep`` reads its points
  (``status``/``condition``/``cost_eur``/``ens_mwh``/``cap_mwh``/``binding``/
  ``report``). Solve failures arrive as a status, never as an exception.
* ``evaluate() -> (plan_hash, metrics)`` — one sequential-MC evaluation of the
  network as the last solve left it, plus a hash of exactly what that MC read.

Keeping the loop this side of that line is what makes it testable at all: the
questions it answers ("did it stop at the first infeasible ε?", "did it ever
hand the solver the ≤ 0 sentinel?") are decisions, and a decision under test
should not need a solver.

WHY THE HASH AND NOT THE COST (plan [B3]). v1 skipped an evaluation when the
objective had not moved. That is unsound in both directions: degenerate optima
give equal cost for different plans, and with DSR configured the objective
moves (variable cost) while the plan stands still. The hash is taken over what
the MC actually reads — the ``(name, capacity_mw)`` unit vector, the
``(name, p_nom_mw, e_nom_mwh)`` storage vector and the residual bytes — so
identical hash ⇒ bit-identical MC result (same inputs, same seed, same draws,
positional CRN substreams held stable by the superset fleet of spec §1.2).
Reuse is then EXACT where cost equality was a guess.

WHY THE STEP IS INFORMED (plan [B4]). Under a high VoLL the cap is slack —
``binding == "voll"``, achieved shed far under the ceiling — and a blind ÷4
spends four or five full solves walking through a region where the plan does
not change at all. ``0.5 · eps · achieved/cap`` crosses that region in one
step and costs nothing when the cap is binding (the ÷4 still floors it).

WHY INFEASIBILITY STOPS BUT A TIME LIMIT DOES NOT (plan [S1]). The ε-feasible
sets are nested — F(ε′) ⊆ F(ε) for ε′ < ε — so the FIRST infeasible ε proves
every tighter one infeasible. A time-limit, numerical or validation failure is
not monotone in ε and proves nothing; conflating the two turns "the solver ran
out of time" into "no plan meets this standard", which are the same words for
opposite user actions.

TERMINATION IS TOTAL, and by construction rather than by argument: **every
iteration of both loops performs exactly one ``solve_at`` call, and neither
loop body is entered unless ``solves_used < budget``**, so the whole run makes
at most ``budget ≤ MAX_LOOP_SOLVES`` solves and then stops. Nothing else is
load-bearing — but two further monotonicities keep the run from wasting that
budget: the tightening step is strictly decreasing (``eps_next ≤ eps/4``) until
it reaches ``EPS_FLOOR_PERMYRIAD``, at which point a miss is final; and the
refinement bracket shrinks geometrically in log-ε and stops outright when the
midpoint reproduces the met endpoint's plan.

TWO ADJUDICATIONS, recorded because the master ratifies or reworks them.

1. **The evaluation skip needs a hash BEFORE the MC, and the spec's binding
   ``evaluate: () -> (plan_hash, metrics)`` cannot be asked for one.** Both
   clauses are binding, so the signature is honoured and the skip is offered
   as an OPTIONAL, duck-typed extra: when the callable carries a zero-argument
   ``plan_hash`` attribute the controller probes it first and skips the MC
   outright on a plateau. Without it the loop still calls ``evaluate`` and
   reuses the STORED metrics on a hash match — which costs an MC (the cheap
   side of the ledger, per plan §1 step 3) but preserves every correctness
   clause: reuse is never keyed on cost, and a differing hash is always
   evaluated. A route that wants the skip attaches the snapshot's hash it
   already computes; one that does not is unaffected.
2. **``budget_exhausted`` means "no answer", not "no refinement".** The spec
   says an exhausted budget reports ``budget_exhausted`` with the best
   verified met iterate as ``final``; the refinement rule separately lists
   "budget spent" as a normal refinement stop. They collide, because with a
   well-behaved LP the hash-equality stop provably cannot fire — a looser cap
   yields the same plan only when that plan is the UNCONSTRAINED optimum, and
   then the looser miss endpoint would have met too — so refinement runs to
   the budget on essentially every real network. Reporting ``budget_exhausted``
   for a run that VERIFIED a met plan would therefore label almost every
   successful study a failure. So: a verified met iterate makes the verdict
   ``met`` (with ``final`` un-refined but valid — plan [S5]/[N8]: a broken
   bracket degrades optimality, never validity), and ``budget_exhausted`` is
   kept for the run that never met, where ``final`` is None.
"""
from __future__ import annotations

import logging
import math

logger = logging.getLogger(__name__)

# Each iterate is a full capacity-expansion solve plus an MC evaluation; the
# wall-time budget the route promises is `max_solves + 1` (the closing restore
# is outside it, plan [N7]). Eight is already tens of minutes on a real
# network — beyond that the user wants a queued job, not a request-scoped
# worker thread.
MAX_LOOP_SOLVES = 8

# A HARD BACKSTOP ONLY. `_wrap_with_ens_cap` reads `permyriad <= 0` as NO
# TARGET and returns the loosest plan, so a step that reaches 0 would leave the
# loop holding the UNCONSTRAINED plan while believing it produced the tightest
# one (plan [S3]). The real stopping floor is the energy one below.
EPS_FLOOR_PERMYRIAD = 0.01

# The real floor, read off the report rather than off ε: once the cap is under
# a megawatt-hour the constrained plan IS the zero-shed plan, so a miss there
# cannot be tightened away and grinding ε down only re-proves it.
ENERGY_FLOOR_MWH = 1.0

_OPTIMAL = ("ok", "optimal")

_ROW_KEYS = ("eps_permyriad", "solve_status", "condition", "cost_eur",
             "ens_mwh", "cap_mwh", "binding", "plateau", "mc")


def _f(v) -> float | None:
    """A report number, or None. The route reads these out of a solved sink,
    and a failed iterate has none of them — None is the honest answer, where a
    0.0 would enter the cheapest-final comparison as a free plan."""
    try:
        out = float(v)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _is_infeasible(status, condition) -> bool:
    """Spec §2's test, on the condition. The status is consulted only when the
    condition is empty: a solver that reports `status="infeasible"` with no
    condition string is stating the same monotone fact, and reading it as an
    ordinary failure would spend the rest of the budget re-proving it."""
    if "infeasible" in str(condition).lower():
        return True
    return not condition and "infeasible" in str(status).lower()


def _tighten(eps: float, ens: float | None, cap: float | None) -> float:
    """``max(min(eps/4, 0.5·eps·achieved/cap), EPS_FLOOR_PERMYRIAD)``.

    The ÷4 is the step's FLOOR, not its size (factor 2 cannot even reach the
    floor inside the budget: ⌈log₂ 1000⌉ = 10 > 8). The informed term is what
    crosses a slack cap in one solve. A missing or zero ``cap`` — a failed
    iterate, or a report with no cap at all — degrades to the blind step
    rather than dividing by zero, and a plan that sheds NOTHING drives the
    informed term to 0, which is exactly the case the clamp exists for.
    """
    step = eps / 4.0
    if (ens is not None and cap is not None and cap > 0.0 and ens >= 0.0):
        informed = 0.5 * eps * (ens / cap)
        if math.isfinite(informed):
            step = min(step, informed)
    return max(step, EPS_FLOOR_PERMYRIAD)


def _mc_block(metrics: dict) -> dict:
    """The spec's per-iterate `mc` block: a PROJECTION of `mc_adequacy`'s
    dict, not a forward of it. The aggregator's own extras (`converged`,
    `time_basis`, `resolution_floor_h`, the standing warning) describe the
    study, not this iterate, and belong to the payload the route assembles
    once. `by_period` rides on every iterate because on a multi-period network
    it is the only way to see WHICH period drives a miss (plan [N4]/[N5])."""
    lo, hi = tuple(metrics.get("lole_ci") or (0.0, 0.0))
    elo, ehi = tuple(metrics.get("eue_ci") or (0.0, 0.0))
    return {
        "engine": "mc",
        "fidelity": "sequential_mc",
        "lole_hours": float(metrics["lole_hours"]),
        "lole_ci": [float(lo), float(hi)],
        "eue_mwh": float(metrics.get("eue_mwh") or 0.0),
        "eue_ci": [float(elo), float(ehi)],
        "n_samples": int(metrics.get("n_samples") or 0),
        "by_period": dict(metrics.get("by_period") or {}),
    }


def _warn_if_crn_broke(plan_hash, reused: dict, fresh: dict) -> None:
    """Identical hash ⇒ bit-identical MC is the WHOLE basis for reuse (plan
    [B3] on top of the superset fleet of spec §1.2). When the probe is absent
    the loop has the fresh metrics in hand anyway, so it checks — if this ever
    fires, common random numbers stopped holding across iterates upstream and
    nobody would otherwise find out."""
    try:
        broke = _mc_block(reused) != _mc_block(fresh)
    except Exception:                                         # noqa: BLE001
        return
    if broke:
        logger.warning(
            "coupling loop: plan hash %s reproduced DIFFERENT MC metrics — "
            "common random numbers are not holding across iterates",
            plan_hash)


def _row(eps, status, condition, cost, ens, cap, binding, plateau, mc) -> dict:
    return {
        "eps_permyriad": float(eps),
        "solve_status": str(status) if status is not None else "failed",
        "condition": condition,
        "cost_eur": cost,
        "ens_mwh": ens,
        "cap_mwh": cap,
        "binding": binding,
        "plateau": bool(plateau),
        "mc": mc,
    }


def run_coupling_loop(solve_at, evaluate, *, target_lole_h: float, eps0: float,
                      max_solves: int, stop_event=None,
                      on_iteration=None) -> dict:
    """
    Drive the cap until the PLAN meets ``target_lole_h`` on the MC's LOLE.

    Returns ``{"status", "iterations", "final", "confident", "eps_star",
    "solves_used"}`` — the shapes the route serialises verbatim (spec §2).
    ``status`` is one of ``met`` / ``unreachable`` / ``budget_exhausted`` /
    ``aborted`` / ``failed``; ``final`` is the CHEAPEST VERIFIED met iterate,
    which is a stronger claim than "the last one": ε → MC-LOLE can genuinely
    RISE as ε tightens (storage-for-thermal substitution under foresight is
    one of the three unreachability mechanisms), so the bracket is a search
    heuristic and only evaluated iterates are answers (plan [S5]).

    ``stop_event`` is checked before every solve, so an abort costs at most
    the iterate already in flight. ``on_iteration`` is called with each row as
    it completes — evaluated, reused OR failed — and is where the route grows
    its record; this module never touches storage.
    """
    target = float(target_lole_h)
    # [N7]: the route validates the bound, and the controller enforces it
    # rather than trusting a caller — 50 solves is an hour of someone's
    # afternoon, and the promise made to the user was eight.
    budget = max(1, min(int(max_solves), MAX_LOOP_SOLVES))
    # The ≤ 0 sentinel can arrive from the REQUEST too (an unset
    # `ens_cap_permyriad` reaches the loop as 0), not only from the step.
    eps = max(_f(eps0) or 0.0, EPS_FLOOR_PERMYRIAD)

    iterations: list[dict] = []
    metrics_by_hash: dict[str, dict] = {}
    solves = 0

    probe = getattr(evaluate, "plan_hash", None)
    if not callable(probe):
        probe = None

    def _emit(row: dict) -> None:
        iterations.append(row)
        if on_iteration is None:
            return
        try:
            on_iteration(row)
        except Exception:                                     # noqa: BLE001
            # The hook belongs to the route's record. A minutes-long study
            # must not be thrown away because the thing it was being appended
            # to misbehaved — the answer is still an answer.
            logger.exception(
                "coupling loop: the on_iteration hook raised; the iterate is "
                "kept but the caller's record may now be short a row")

    def _iterate(e: float):
        """One solve (+ evaluation). Returns ``(row, kind, plan_hash)`` with
        ``kind`` in {met, miss, infeasible, failed, error}."""
        nonlocal solves
        # Spec §2: assert, never solve with the sentinel. Reaching here with
        # eps ≤ 0 means the step clamp was removed, and the loop would then
        # believe the loosest plan in existence is the tightest.
        assert e > 0, (
            f"the loop must never solve at eps={e!r} — ≤ 0 is NO TARGET")
        solves += 1
        try:
            res = solve_at(e) or {}
        except Exception as exc:                              # noqa: BLE001
            # `solve_at` is specified never to raise; taking that on faith
            # turns a solver-process death into a stack trace out of a worker
            # thread and no record at all.
            logger.exception("coupling loop: solve_at raised at eps=%r", e)
            row = _row(e, "failed", f"solve_at raised: {exc}",
                       None, None, None, None, False, None)
            _emit(row)
            return row, "error", None

        status = res.get("status")
        condition = res.get("condition")
        cost = _f(res.get("cost_eur"))
        ens = _f(res.get("ens_mwh"))
        cap = _f(res.get("cap_mwh"))
        binding = res.get("binding")

        if status not in _OPTIMAL:
            # NEVER evaluated: `p_nom_opt` still holds the previous plan, so
            # an MC here would score the wrong iterate against this ε.
            row = _row(e, status, condition, cost, ens, cap, binding,
                       False, None)
            _emit(row)
            return row, ("infeasible" if _is_infeasible(status, condition)
                         else "failed"), None

        plan_hash = None
        metrics = None
        plateau = False
        # The cheap pre-test is the report's OWN computation: a cap that did
        # not bind cannot have changed the plan through the cap.
        reusable = binding != "system_cap"

        if reusable and probe is not None:
            try:
                plan_hash = probe()
            except Exception:                                 # noqa: BLE001
                logger.exception("coupling loop: the plan-hash probe raised; "
                                 "falling back to a full evaluation")
                plan_hash = None
            if plan_hash is not None and plan_hash in metrics_by_hash:
                metrics = metrics_by_hash[plan_hash]
                plateau = True

        if metrics is None:
            try:
                plan_hash, fresh = evaluate()
            except Exception as exc:                          # noqa: BLE001
                logger.exception("coupling loop: evaluate raised at eps=%r", e)
                row = _row(e, status, f"evaluate failed: {exc}",
                           cost, ens, cap, binding, False, None)
                _emit(row)
                return row, "error", None
            if reusable and plan_hash is not None \
                    and plan_hash in metrics_by_hash:
                metrics = metrics_by_hash[plan_hash]
                plateau = True
                _warn_if_crn_broke(plan_hash, metrics, fresh)
            else:
                metrics = fresh
                # A caller that cannot produce a hash gets no reuse rather
                # than a None key that would make every unhashable iterate a
                # plateau of every other one.
                if plan_hash is not None:
                    metrics_by_hash.setdefault(plan_hash, fresh)

        try:
            mc = _mc_block(metrics)
        except Exception as exc:                              # noqa: BLE001
            # Metrics the loop cannot read are `evaluate` misbehaving, and the
            # invariant worth more than the diagnosis is that NOTHING escapes
            # this function: an exception out of a worker thread leaves the
            # route's record running for ever with no iterates on it.
            logger.exception("coupling loop: unusable metrics at eps=%r", e)
            row = _row(e, status, f"evaluate returned unusable metrics: {exc}",
                       cost, ens, cap, binding, False, None)
            _emit(row)
            return row, "error", None
        row = _row(e, status, condition, cost, ens, cap, binding, plateau, mc)
        _emit(row)
        kind = "met" if mc["lole_hours"] <= target else "miss"
        return row, kind, plan_hash

    def _aborted() -> bool:
        try:
            return bool(stop_event is not None and stop_event.is_set())
        except Exception:                                     # noqa: BLE001
            return False

    # ── tightening ────────────────────────────────────────────────────────
    verdict: str | None = None
    met_eps: float | None = None
    met_hash = None
    miss_eps: float | None = None

    while True:
        if _aborted():
            verdict = "aborted"
            break
        if solves >= budget:
            break
        row, kind, plan_hash = _iterate(eps)

        if kind == "error":
            verdict = "failed"
            break
        if kind == "infeasible":
            # Nested feasibility: every tighter ε is infeasible too. No met
            # iterate can exist here (one would have left this loop), so the
            # standard is out of reach for this network.
            verdict = "unreachable"
            break
        if kind == "met":
            met_eps, met_hash = eps, plan_hash
            break
        if kind == "failed":
            # Not monotone in ε and carries no ens/cap to inform a step.
            nxt = _tighten(eps, None, None)
            if nxt >= eps:
                # Already at the backstop with nothing solved and nothing
                # proven: the search space is spent, but a time limit is not a
                # proof of unreachability, so this is "stopped without an
                # answer", not "no plan exists".
                break
            eps = nxt
            continue

        # a miss
        miss_eps = eps
        if row["cap_mwh"] is not None and row["cap_mwh"] < ENERGY_FLOOR_MWH:
            # The constrained plan already equals the zero-shed plan.
            verdict = "unreachable"
            break
        nxt = _tighten(eps, row["ens_mwh"], row["cap_mwh"])
        if nxt >= eps:
            # The hard ε backstop, reached with a miss: nothing tighter is
            # allowed to be tried, so the standard is out of reach here too.
            verdict = "unreachable"
            break
        eps = nxt

    # ── refinement (plan [S4]) ────────────────────────────────────────────
    # Bisect log-ε between the tightest evaluated MISS and the loosest MET.
    # The bracket is infeasibility-free by the same nesting [S1]. A met
    # ITERATE 0 has no miss endpoint and therefore no bracket — the cheap case
    # stops at one solve, which is the whole point of it.
    while verdict is None and met_eps is not None and miss_eps is not None:
        if _aborted():
            verdict = "aborted"
            break
        if solves >= budget:
            break
        if not miss_eps > met_eps * (1.0 + 1e-9):
            break
        mid = math.sqrt(met_eps * miss_eps)
        if not (met_eps < mid < miss_eps):
            # The bracket has collapsed to float resolution; another solve
            # would re-solve an endpoint.
            break
        row, kind, plan_hash = _iterate(mid)
        if kind == "error":
            verdict = "failed"
            break
        if kind in ("infeasible", "failed"):
            # Neither half of the bracket is now trustworthy and the answer in
            # hand is already valid; stop rather than guess a direction.
            break
        if kind == "met":
            if plan_hash is not None and plan_hash == met_hash:
                # The met endpoint is already the loosest cap producing this
                # plan, so no further solve can lower the cost.
                break
            met_eps, met_hash = mid, plan_hash
        else:
            miss_eps = mid

    # ── the verdict ───────────────────────────────────────────────────────
    verified = [r for r in iterations
                if r["mc"] is not None and r["mc"]["lole_hours"] <= target]
    final = min(
        verified,
        key=lambda r: (r["cost_eur"] if r["cost_eur"] is not None
                       else math.inf, -r["eps_permyriad"]),
    ) if verified else None

    if verdict is None:
        # See the module docstring, adjudication 2.
        verdict = "met" if final is not None else "budget_exhausted"

    return {
        "status": verdict,
        "iterations": iterations,
        "final": final,
        # Reported, never iterated for: the interval's width is a DRAWS
        # decision, and tightening the cap to buy confidence spends solves on
        # a question more draws answer (plan §1, the stopping band).
        "confident": bool(final is not None
                          and final["mc"]["lole_ci"][1] <= target),
        "eps_star": final["eps_permyriad"] if final is not None else None,
        "solves_used": solves,
    }


# ── the loops' plan hash (Phase 12d: one implementation, testable) ────────

def snapshot_hash(mc_inputs) -> str:
    """sha256 over exactly what the MC reads — the sorted ``(name,
    capacity_mw, profile bytes, capacity-series bytes)`` unit vector, the
    sorted ``(name, p_nom_mw, e_nom_mwh, capacity-series bytes)`` storage
    vector, and the residual bytes. NOT the objective: degenerate optima give
    equal cost for different plans. Equal hash ⇒ bit-identical MC under the
    same seed and draw count, so the loops' plateau reuse is exact. Both
    certifying loops' ``_hash`` delegate here (12c-pre shipped-code review,
    finding 5; 12d plan §2.6)."""
    import hashlib
    import numpy as _np
    h = hashlib.sha256()
    for name, cap, prof in sorted(
            (str(u.name), float(u.capacity_mw),
             # Phase 12c-pre: the sampler also reads the unit's
             # availability series; hash its bytes so "exactly what the
             # MC reads" stays true (shipped-code review, finding 5).
             (b"" if getattr(u, "profile", None) is None
              else _np.asarray(u.profile, dtype=_np.float64).tobytes())
             # Phase 12d: …and its capacity series, in MW per hour.
             + b"\x1e"
             + (b"" if getattr(u, "capacity_series", None) is None
                else _np.asarray(u.capacity_series, dtype=_np.float64).tobytes()))
            for u in mc_inputs.units):
        h.update(f"{name}\x1f{cap!r}\x1e".encode() + prof + b"\x1e")
    h.update(b"\x1d")
    for row, cs in sorted(
            ((str(s.name), float(s.p_nom_mw), float(s.e_nom_mwh)),
             b"" if getattr(s, "capacity_series", None) is None
             else _np.asarray(s.capacity_series, dtype=_np.float64).tobytes())
            for s in mc_inputs.storage):
        h.update(("\x1f".join(repr(v) for v in row) + "\x1e").encode() + cs + b"\x1e")
    h.update(b"\x1d")
    h.update(mc_inputs.residual.tobytes())
    return h.hexdigest()
