"""
The asset-sizing loop — the controller (AS-06, `.scratch/asset-sizing-loop/issues/06-sizing-loop-runner.md`).

**Solve → forward checks on the fixed capacities → cuts → re-solve**, by the revised rule the prototype settled
(`.scratch/datacenter-grid-readiness/research/prototype-as-loop/RESULTS.md`, sections 4 and 5).

**This module is PURE CONTROL FLOW**, as `coupling.py` is. It imports no route, no `pypsa`, no gridspine and
never sleeps. Everything expensive is behind the callables it is handed:

* ``solve(state) -> result``: one capacity-expansion solve under ``state["cuts"]`` with the library assets in
  ``state["frozen"]`` fixed. ``result`` is ``{"status", "condition", "cost_eur", "sizes"}``; ``sizes`` maps an
  asset to its solved capacity. A solve failure arrives as a status, never as an exception.
* ``forward(result, state) -> checks``: the forward pass on the fixed capacities. Each check is
  ``{"name", "ok", "lever", "cut", ...}``:

  - ``lever`` is ``"cut"`` (a failure proposes the ``cut`` dict, keys of ``CUT_DIRECTIONS``), ``"none"`` (no
    asset the LP sizes can fix it: the grid sets it) or ``"filter_cost"`` (harmonics: the check prices a
    filter, ``filter_cost_eur``, and never cuts);
  - ``library`` (optional) maps a library asset to the size the solved one rounds up to;
  - ``skipped`` (optional) marks a campus-model check that could not run; it is named in the verdict.
* ``final_solve(state) -> result`` (optional): the full-year solve that confirms a converged run whose rounds
  used a reduced year. The forward pass runs again on it with ``state["final"] = True``; its verdict is the
  run's.

THE STOPPING RULE (revised, issue 06).

1. **converged**: every check passes (a harmonics filter cost is a cost, not a failure). Campus checks that
   could not run are named: "converged (campus checks not run: …)", never a plain converged.
2. **unresolved**: a failing check has no lever. The grid alone sets it, so no round can lift it.
3. **not converged**, in three ways, each naming the failing checks:

   - a tolerance stop, total cost (LP cost plus filter cost) moving < 0.5 % and every unfrozen capacity
     < 2 %, with a check still failing. The prototype's creep moved the cost 0.02 % a round with Pst six
     times over, so the stop is never read as convergence;
   - a failing check with a lever whose proposed cut is no tighter than the table already holds: the next
     LP would return the same plan;
   - the round budget, ``MAX_ROUNDS`` = 5, spent.

CUTS ARE NEVER REMOVED, and each key moves only in its own direction (``CUT_DIRECTIONS``): an upper bound
only falls, a floor only rises. A looser proposal is ignored. So the feasible set only shrinks and the cost is
non-decreasing; a cost that falls means a cut was lost, and is flagged in ``warnings`` (on
the LP cost: a filter cost may fall for its own reasons).

LIBRARY ASSETS (the transformer) round up to the library size the forward pass names (the largest, when two
checks name one asset; the last named, in a round that names none), and freeze at that size at the end of round
``freeze_after`` (2). A frozen size is never changed again, so it cannot flip between library sizes. ``library``
in the result holds the latest rounded-up sizes, also for a run that converged before the freeze.

TERMINATION IS TOTAL: each round makes exactly one ``solve`` call, the loop runs at most ``MAX_ROUNDS`` rounds,
and ``final_solve`` runs at most once, after a converged verdict.
"""
from __future__ import annotations

import logging
import math
import time

logger = logging.getLogger(__name__)

#: The round budget (issue 06: stop at 5 iterations).
MAX_ROUNDS = 5
#: Library assets freeze at the end of this round.
FREEZE_AFTER = 2
#: The tolerance stop: relative change in total cost, and the largest relative change in an unfrozen capacity.
COST_TOL = 0.005
CAPACITY_TOL = 0.02

#: Each cut and its direction: an "upper" bound only falls, a "floor" only rises.
CUT_DIRECTIONS = {
    "import_cap_mw": "upper",    # import flow at the PoC, p(t) <= cap (not the transformer's p_nom)
    "pst_rhs_mva": "upper",      # cos psi P_res + sin psi Q_res <= rhs
    "p_floor_mw": "floor",       # smoothing power floor, sum P_sm >= floor
    "e_floor_mwh": "floor",      # smoothing energy floor, sum E_sm >= floor
    "gfm_mva_min": "floor",      # sum over GFM stores of P + S_x >= min
    "pq_margin_mvar": "floor",   # added to Q_load in the per-hour PQ row
}

UNRESOLVED_COPY = "unresolved, needs grid reinforcement or EMT study"
LEVERS = ("cut", "none", "filter_cost")

_OPTIMAL = ("ok", "optimal")


def _budget(max_rounds) -> int:
    return max(1, min(int(max_rounds), MAX_ROUNDS))


def merge_cuts(cuts: dict, proposed: dict) -> tuple[dict, set]:
    """``(merged, changed)``: ``cuts`` with each proposed key moved in its own direction, never loosened. A key
    already held is kept; ``changed`` names the keys that are new or strictly tighter."""
    merged, changed = dict(cuts), set()
    for key, value in proposed.items():
        if key not in CUT_DIRECTIONS:
            raise ValueError(f"unknown sizing cut {key!r}; known: {', '.join(CUT_DIRECTIONS)}")
        v = float(value)
        if not math.isfinite(v):
            raise ValueError(f"sizing cut {key} must be finite, got {value!r}")
        old = merged.get(key)
        tighter = old is None or (v < old if CUT_DIRECTIONS[key] == "upper" else v > old)
        if tighter and (old is None or abs(v - old) > 1e-9 * max(1.0, abs(old))):
            merged[key] = v
            changed.add(key)
    return merged, changed


def _solved(result) -> bool:
    return (isinstance(result, dict) and result.get("status") in _OPTIMAL
            and result.get("condition", "optimal") in _OPTIMAL)


def _capacity_move(a: dict, b: dict, frozen: dict) -> float:
    """The largest relative change of an unfrozen capacity between two rounds."""
    worst = 0.0
    for k in set(a) | set(b):
        if k in frozen:
            continue
        u, v = float(a.get(k, 0.0)), float(b.get(k, 0.0))
        worst = max(worst, abs(v - u) / max(abs(u), abs(v), 1.0))
    return worst


def _lever(check: dict) -> str:
    return check.get("lever", "cut")


def _why(result) -> str:
    """The failed solve's condition, for the verdict."""
    result = result or {}
    return result.get("condition") or result.get("status") or "unknown"


def _evaluate(result: dict, checks: list[dict]) -> dict:
    """The row fields a forward pass gives: what fails, what has no lever, the filter cost, the total."""
    for c in checks:
        if _lever(c) not in LEVERS:
            raise ValueError(f"check {c.get('name')}: lever {c.get('lever')!r} is not one of {LEVERS}")
    filter_cost = sum(float(c.get("filter_cost_eur") or 0.0) for c in checks if _lever(c) == "filter_cost")
    failing = [c for c in checks if not c.get("ok") and _lever(c) != "filter_cost"]
    return {
        "checks": checks,
        "failing": [c["name"] for c in failing],
        "unresolved": [c["name"] for c in failing if _lever(c) == "none"],
        "not_run": [c["name"] for c in checks if c.get("skipped")],
        "filter_cost_eur": filter_cost,
        "total_cost_eur": float(result["cost_eur"]) + filter_cost,
    }


def _round_up(library: dict, checks: list[dict]) -> None:
    """Record this round's rounded-up library sizes: the largest any check names for an asset. An asset no
    check names this round keeps its last size."""
    sizes: dict = {}
    for c in checks:
        for asset, size in (c.get("library") or {}).items():
            sizes[asset] = max(sizes.get(asset, -math.inf), float(size))
    library.update(sizes)


def _converged_text(not_run) -> str:
    return f"converged (campus checks not run: {", ".join(not_run)})" if not_run else "converged"


def run_sizing_loop(solve, forward, *, max_rounds: int = MAX_ROUNDS, freeze_after: int = FREEZE_AFTER,
                    cost_tol: float = COST_TOL, capacity_tol: float = CAPACITY_TOL, final_solve=None,
                    stop_event=None, on_iteration=None) -> dict:
    """
    Run the loop until the revised rule stops it.

    Returns ``{"status", "verdict", "rounds", "final", "cuts", "frozen", "library", "failing", "unresolved",
    "not_run",
    "warnings", "seconds"}``. ``status`` is ``converged`` / ``not_converged`` / ``unresolved`` / ``failed`` /
    ``aborted``; ``verdict`` is the sentence for the user. ``final`` is the row the verdict reads (the full-year
    row when ``final_solve`` ran). ``stop_event`` is checked before every solve; ``on_iteration`` sees each row.
    """
    budget = _budget(max_rounds)
    freeze_after = max(1, int(freeze_after))
    cuts: dict = {}
    frozen: dict = {}
    library: dict = {}       # each library asset's latest rounded-up size
    rounds: list[dict] = []
    warnings: list[str] = []
    t0 = time.perf_counter()

    def done(status, verdict, row=None, **extra):
        last = row or (rounds[-1] if rounds else None)
        return {"status": status, "verdict": verdict, "rounds": rounds, "final": last, "cuts": dict(cuts),
                "frozen": dict(frozen), "library": dict(library), "failing": (last or {}).get("failing", []),
                "unresolved": (last or {}).get("unresolved", []), "not_run": (last or {}).get("not_run", []),
                "warnings": warnings, "seconds": time.perf_counter() - t0, **extra}

    def stopped() -> bool:
        return stop_event is not None and stop_event.is_set()

    for k in range(1, budget + 1):
        if stopped():
            return done("aborted", f"aborted before round {k}")
        state = {"round": k, "cuts": dict(cuts), "frozen": dict(frozen)}
        t = time.perf_counter()
        result = solve(state)
        solve_s = time.perf_counter() - t
        row = {"round": k, "basis": "rounds", "cuts": dict(cuts), "frozen": dict(frozen), "solve_s": solve_s}
        if not _solved(result):
            cond = _why(result)
            row.update(status=(result or {}).get("status"), condition=cond, forward_s=0.0)
            rounds.append(row)
            if on_iteration is not None:
                on_iteration(row)
            return done("failed", f"failed: round {k} solve did not reach an optimum ({cond})", row)
        t = time.perf_counter()
        checks = forward(result, state)
        row.update(status=result["status"], condition=result.get("condition", "optimal"),
                   cost_eur=float(result["cost_eur"]), sizes=dict(result.get("sizes") or {}),
                   forward_s=time.perf_counter() - t, **_evaluate(result, checks))
        prev = rounds[-1] if rounds else None
        rounds.append(row)
        # The LP cost alone: a filter cost may fall for its own reasons.
        if prev is not None and row["cost_eur"] < prev["cost_eur"] * (1 - 1e-9) - 1e-9:
            warnings.append(f"round {k}: LP cost fell from {prev['cost_eur']:.6g} to {row['cost_eur']:.6g}; "
                            "cuts only shrink the feasible set, so a cut was lost")
        _round_up(library, checks)
        # Freeze the library assets, once, at the end of round `freeze_after`, at their latest rounded-up size.
        if k == freeze_after:
            for asset, size in library.items():
                frozen.setdefault(asset, size)
        if on_iteration is not None:
            on_iteration(row)

        if not row["failing"]:
            break
        if row["unresolved"]:
            return done("unresolved", f"{UNRESOLVED_COPY}: {", ".join(row['unresolved'])}", row)
        if prev is not None:
            dc = abs(row["total_cost_eur"] - prev["total_cost_eur"]) / max(abs(prev["total_cost_eur"]), 1e-9)
            move = _capacity_move(prev["sizes"], row["sizes"], frozen)
            row.update(cost_change=dc, capacity_move=move)
            if dc < cost_tol and move < capacity_tol:
                return done("not_converged",
                            f"not converged: tolerance stop (cost moved {dc:.3%}, capacities {move:.2%}) with "
                            f"checks still failing: {", ".join(row['failing'])}", row)
        # Merge every failing check's cut first: a check whose key another check tightened further is not stale,
        # since the next LP still differs on that key.
        proposed = [c for c in checks if c["name"] in row["failing"]]
        changed: set = set()
        for c in proposed:
            cuts, more = merge_cuts(cuts, c.get("cut") or {})
            changed |= more
        stale = [c["name"] for c in proposed if not set(c.get("cut") or {}) & changed]
        row["cuts_after"] = dict(cuts)
        if stale:
            return done("not_converged",
                        f"not converged: no tighter cut for {", ".join(stale)}; checks still failing: "
                        f"{", ".join(row['failing'])}", row)
        if k == budget:
            return done("not_converged",
                        f"not converged in {budget} rounds; checks still failing: "
                        f"{", ".join(row['failing'])}", row)

    last = rounds[-1]
    if final_solve is None:
        return done("converged", _converged_text(last["not_run"]), last)
    if stopped():
        return done("aborted", "aborted before the full-year solve", last)
    state = {"round": len(rounds) + 1, "cuts": dict(cuts), "frozen": dict(frozen), "final": True}
    t = time.perf_counter()
    result = final_solve(state)
    row = {"round": state["round"], "basis": "full_year", "cuts": dict(cuts), "frozen": dict(frozen),
           "solve_s": time.perf_counter() - t}
    if not _solved(result):
        cond = _why(result)
        row.update(status=(result or {}).get("status"), condition=cond, forward_s=0.0)
        return done("failed", f"failed: the full-year solve did not reach an optimum ({cond})", row)
    t = time.perf_counter()
    checks = forward(result, state)
    row.update(status=result["status"], condition=result.get("condition", "optimal"),
               cost_eur=float(result["cost_eur"]), sizes=dict(result.get("sizes") or {}),
               forward_s=time.perf_counter() - t, **_evaluate(result, checks))
    if on_iteration is not None:
        on_iteration(row)
    if row["unresolved"]:
        return done("unresolved", f"{UNRESOLVED_COPY}: {", ".join(row['unresolved'])}", row)
    if row["failing"]:
        return done("not_converged", "not converged: reduced-year rounds passed, full year failed: "
                    f"{", ".join(row['failing'])}", row)
    return done("converged", _converged_text(row["not_run"]), row)
