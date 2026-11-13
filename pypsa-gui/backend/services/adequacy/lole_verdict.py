"""
The ONE rule every LOLE gate applies against a goal (adequacy loops 04,
GK A (25)).

``lole_verdict`` compares a sampled LOLE estimate with a goal, both in hours
over the same horizon (``lole_hours`` and a ``loop_goal`` target are
per-horizon already):

* ``miss`` — the estimate is above the goal;
* ``pass`` — the estimate is at or below the goal and EITHER its confidence
  interval's upper bound is too (``confident``), OR one more sampled draw as
  heavy as the heaviest seen would still leave it at or below the goal;
* ``not_confident`` — otherwise: at or below the goal, but not confidently,
  and one more such draw would put it above. That is never a pass.

**One more sampled draw as heavy as the heaviest seen, never less than one
shortfall hour at the heaviest snapshot** is
``event_h = max(w_max, d_max) / n`` (GK A (26), E1–E4): the estimate's
quantum is one draw, and one outage spans consecutive snapshots, so one more
sampled event moves the mean by that draw's whole shortfall over ``n``.
``d_max`` is the heaviest single draw's LOLE, taken from exactly the per-draw
array the mean is taken over; ``w_max`` is the largest positive weight of
the array the MC accumulated LOLE with (snapshot weight × period years, so it
is per-horizon on a multi-period network). The ``w_max`` floor keeps an
estimate with nothing sampled (0 h) from passing a goal it cannot resolve.
``n`` is the draws that produced the estimate (adaptive stopping), never the
requested budget. Only the horizon's ``event_h`` is emitted; no per-period
verdict exists (E3).

``settle_draws`` names what would settle a ``not_confident`` estimate: about
``ceil(n · ((ci_hi − lole) / (goal − lole))²)`` draws if the mean and spread
hold, the count at which the interval's upper bound reaches the goal (in user
text: "if the estimate and its range stay as they are"). It is ``None`` with a
reason when that count is above the engine cap (in user text, "the limit of N
draws"), when the estimate equals the goal, or when the interval is
degenerate.
"""

from __future__ import annotations

import math

__all__ = [
    "event_hours",
    "goal_fields",
    "lole_verdict",
    "not_confident_copy",
    "settle_text",
]


def event_hours(weights, n_samples, draws=()) -> float | None:
    """One more sampled draw as heavy as the heaviest seen, never less than
    one shortfall hour at the heaviest snapshot: ``max(w_max, d_max) / n``.
    ``draws`` is the per-draw LOLE array the estimate is the mean of.
    ``None`` when there is no draw, or neither a positive weight nor a
    shortfall."""
    n = int(n_samples or 0)
    if n <= 0:
        return None
    w_max = max((float(w) for w in weights if float(w) > 0.0), default=0.0)
    d_max = max((float(d) for d in draws), default=0.0)
    quantum = max(w_max, d_max)
    return quantum / n if quantum > 0.0 else None


def _settle(lole: float, hi: float, target: float, n: int, max_draws: int):
    if not (math.isfinite(hi) and hi > lole):
        return None, "the confidence interval is degenerate"
    if not target > lole:
        return None, "the estimate equals the goal"
    need = math.ceil(n * ((hi - lole) / (target - lole)) ** 2)
    if need > max_draws:
        return None, f"beyond the limit of {max_draws} draws"
    return need, None


def lole_verdict(lole_h, lole_ci, target_h, event_h, n_samples, max_draws) -> dict:
    """``{verdict, confident, event_h, settle_draws, settle_reason}`` for one
    estimate against one goal (module docstring). All hours per horizon."""
    lole = float(lole_h)
    target = float(target_h)
    hi = float(lole_ci[1]) if lole_ci is not None else math.nan
    confident = math.isfinite(hi) and hi <= target
    out = {
        "confident": bool(confident),
        "event_h": event_h,
        "settle_draws": None,
        "settle_reason": None,
    }
    if lole > target:
        out["verdict"] = "miss"
        return out
    if confident or (event_h is not None and lole + float(event_h) <= target):
        out["verdict"] = "pass"
        return out
    out["verdict"] = "not_confident"
    out["settle_draws"], out["settle_reason"] = _settle(
        lole, hi, target, int(n_samples), int(max_draws)
    )
    return out


def settle_text(goal: dict) -> str:
    """What would settle a ``not_confident`` verdict, in plain words (D5;
    UX (20) asked for plainer words than "if the mean and spread hold")."""
    if goal.get("settle_draws") is not None:
        return (
            f"about {int(goal['settle_draws'])} draws would settle it, if "
            "the estimate and its range stay as they are"
        )
    return f"more draws alone will not settle it ({goal.get('settle_reason')})"


def not_confident_copy(mc: dict, target_h: float, lever: str) -> str:
    """A loop's verdict for status ``not_confident``: the nearest iterate's
    ``mc`` block (with its ``goal``) against the goal. ``lever`` names the
    iterate's setting, e.g. ``"ε = 0.035‱"``. The loop restores the user's
    own config on this status (adequacy loops 04, D4)."""
    goal = mc["goal"]
    hi = mc["lole_ci"][1]
    event = goal.get("event_h")
    # UX (20): no figure here; on a one-draw estimate it equals the estimate
    # and reads as a typo.
    event_clause = (
        "one more sampled draw as heavy as the heaviest seen would put it above"
        if event is not None
        else "the study did not report how much one more sampled draw is worth"
    )
    settle = settle_text(goal)
    return (
        f"Not confident: no plan was confirmed to meet {target_h:g} h. The "
        f"nearest, at {lever}, estimates {float(mc['lole_hours']):.3g} h, at "
        f"or below the goal, but the top of its 95% range is "
        f"{float(hi):.3g} h and {event_clause}, so it is not a pass. "
        f"{settle[0].upper()}{settle[1:]}. Your original plan has been "
        "re-solved, so the network you have now is your own plan, not that "
        "one."
    )


def goal_fields(nearest, final) -> dict:
    """A finished loop record's ``event_h`` / ``settle_draws`` /
    ``settle_reason``: the nearest iterate's goal verdict when the loop ended
    not confident, else the final iterate's, else all None."""
    row = nearest if nearest is not None else final
    goal = ((row or {}).get("mc") or {}).get("goal") or {}
    return {k: goal.get(k) for k in ("event_h", "settle_draws", "settle_reason")}
