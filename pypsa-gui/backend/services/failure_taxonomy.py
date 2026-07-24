"""
Solve-failure taxonomy — turn a raw ``(status, condition)`` outcome into an
actionable category + plain-language hint for the UI.

``run_simulation`` returns ``(status, condition)``. A FAILED solve carries a
cryptic ``condition`` string — PyPSA/linopy emit ``'infeasible'``,
``'unbounded'``, ``'infeasible_or_unbounded'``, ``'suboptimal'``; our own
paths emit ``'validation_failed'`` or an exception's ``str()`` (e.g.
``"'snapshot' is not a valid dimension..."``). The user sees that string with
no idea what to do about it.

``classify_failure`` maps the outcome to a small set of user-facing categories,
each with a short ``title`` and a concrete next-step ``hint``, so the GUI can
show "why it failed and what to try" instead of a raw
``warning / infeasible_or_unbounded``. It returns ``None`` for a successful or
user-aborted run (nothing to explain) — the caller stores ``None`` to clear any
stale failure from a previous run.

Pure + dependency-free → unit-testable in isolation; no PyPSA import.
"""
from __future__ import annotations

# Conditions that mean "the solve succeeded" — never a failure card.
_SUCCESS_CONDITIONS = {"optimal", "lopf+ac_pf_ok", "ok"}
_SUCCESS_STATUSES = {"ok", "optimal"}

# Substring → (category, title, hint). Checked in ORDER, first match wins, so
# more-specific combined cases (infeasible_or_unbounded) precede their parts.
# Matched against a lowercased haystack built from condition (+ optional detail).
_RULES: tuple[tuple[tuple[str, ...], str, str, str], ...] = (
    (
        ("validation_failed",),
        "validation",
        "Validation failed before solving",
        "The model has blocking errors that were caught before the solver "
        "started. Review each error listed below, fix it (the “View” button "
        "jumps to the offending component), then run again.",
    ),
    (
        ("infeasible_or_unbounded",),
        "infeasible_or_unbounded",
        "Infeasible or unbounded",
        "The solver's presolve detected the model is EITHER over-constrained "
        "(no feasible solution) OR under-constrained (cost can fall without "
        "limit) but couldn't tell which. Check both: a binding limit that "
        "can't be met (demand > available capacity, a too-tight CO₂ cap), and "
        "any extendable asset with no upper capacity bound paired with a "
        "negative/zero cost. Turning OFF presolve makes HiGHS report which "
        "one it is.",
    ),
    (
        ("infeasible",),
        "infeasible",
        "No feasible solution",
        "The constraints can't all be satisfied at once. Common causes: demand "
        "exceeds available generation + import capacity in some hour; a CO₂ cap "
        "or other global constraint is too tight; capacity min/max bounds "
        "conflict; or a reserve/SoC target can't be met. Relax the binding "
        "limit — or set a Value of Lost Load (VOLL) to price unserved demand "
        "instead of forbidding it — and re-run.",
    ),
    (
        ("unbounded",),
        "unbounded",
        "Unbounded objective",
        "The optimiser can lower total cost without limit. This almost always "
        "means an extendable generator/line/link with NO upper capacity bound "
        "(p_nom_max / s_nom_max = ∞) combined with a negative or zero marginal "
        "cost, so the LP builds infinite cheap capacity. Add a finite upper "
        "bound or a non-negative cost to the offending asset and re-run.",
    ),
    (
        ("time_limit", "time limit", "timelimit"),
        "time_limit",
        "Solver time limit reached",
        "The solver stopped at the configured time limit before finishing. "
        "Increase the time limit (and, for unit-commitment / MIP runs, loosen "
        "the MIP gap), or shrink the model — fewer snapshots, or use "
        "representative periods — and re-run.",
    ),
    (
        ("singular", "numerical", "ill-conditioned", "ill conditioned",
         "scaling", "matrix is singular"),
        "numerical",
        "Numerical trouble",
        "The solver hit ill-conditioning — coefficients spanning a very wide "
        "magnitude range in the same model (tiny costs next to huge ones, or "
        "near-zero capacities). Try the objective auto-scale option, normalise "
        "outlier cost/capacity values, then re-run.",
    ),
    (
        # NOTE: condition for a generic exception is `str(exc)`, which does NOT
        # contain the class name ("KeyError" etc.) — so we match the MESSAGE
        # shapes PyPSA/xarray/pandas actually produce (several documented in
        # CLAUDE.md): "'tuple' object has no attribute 'lower'" (multi-period
        # statistics), "...not in index" (assign_duals on a stale MultiIndex),
        # "'snapshot' is not a valid dimension..." (lost axis name), etc.
        ("not a valid dimension", "dim_0", "in a buffer", "cannot include dtype",
         "object has no attribute", "not in index", "'snapshot'",
         "is not a valid", "reindex"),
        "setup_error",
        "Model build error",
        "The LP couldn't be constructed — a structural problem (snapshot/index "
        "mismatch, a missing or mistyped attribute) rather than an economics "
        "one. Open the log and read the TRACEBACK lines for the exact frame. "
        "Re-running after a fresh project load often clears a transient index "
        "state.",
    ),
    (
        ("suboptimal",),
        "suboptimal",
        "Stopped at a suboptimal point",
        "The solver returned a feasible but not provably-optimal solution "
        "(it stopped early). Results exist but may not be the true least-cost "
        "outcome. Tighten the MIP gap or remove the time limit to converge "
        "fully, then re-run.",
    ),
)


def classify_failure(status: str | None, condition: str | None) -> dict | None:
    """
    Classify a finished solve outcome.

    Returns ``None`` for success or a user-initiated abort (nothing to explain),
    otherwise a dict ``{category, title, hint, detail}`` where ``detail`` is the
    raw condition string (so the UI can still show the underlying token).
    """
    status_l = (status or "").strip().lower()
    cond_l = (condition or "").strip().lower()

    # Success — no failure card. A successful LP that also ran AC PF keeps
    # status "ok"/"optimal" even if Stage-2 AC PF failed (that path mutates
    # only `condition`, appending "; ac_pf_failed: ..."), so the status check
    # correctly treats it as a non-failure — the LP results are valid.
    if status_l in _SUCCESS_STATUSES or cond_l in _SUCCESS_CONDITIONS:
        return None
    # User-initiated abort — intentional, not a failure.
    if status_l == "aborted" or cond_l.startswith("user_aborted"):
        return None

    haystack = cond_l
    for needles, category, title, hint in _RULES:
        if any(n in haystack for n in needles):
            return {
                "category": category,
                "title": title,
                "hint": hint,
                "detail": condition or "",
            }

    # Unknown non-success outcome — generic but still actionable.
    return {
        "category": "solver_error",
        "title": "Solve failed",
        "hint": "The solver returned a non-optimal status. Check the log for "
                "details — the TRACEBACK and ERROR lines show what went wrong.",
        "detail": condition or "",
    }
