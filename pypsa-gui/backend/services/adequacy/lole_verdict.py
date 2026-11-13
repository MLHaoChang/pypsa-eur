"""
The ONE rule every LOLE gate applies against a goal (adequacy loops 04,
GK A (25)).

``lole_verdict`` compares a sampled LOLE estimate with a goal, both in hours
over the same horizon (``lole_hours`` and a ``loop_goal`` target are
per-horizon already):

* ``miss`` — the estimate is above the goal;
* ``pass`` — the estimate is at or below the goal and EITHER its confidence
  interval's upper bound is too (``confident``), OR one more sampled
  shortfall hour at the heaviest snapshot would still leave it at or below
  the goal;
* ``not_confident`` — otherwise: at or below the goal, but not confidently,
  and one more sampled shortfall hour at the heaviest snapshot would put it
  above. That is never a pass.

**One sampled shortfall hour at the heaviest snapshot** is
``event_h = w_max / n``: a shortfall in one snapshot of one draw adds that
snapshot's weight over the draw count to the estimate, and the heaviest
snapshot gives the largest single increment. ``w_max`` is the largest
positive weight of the array the MC accumulated LOLE with (snapshot weight ×
period years, so it is per-horizon on a multi-period network) and ``n`` is
the draws that produced the estimate (adaptive stopping), never the requested
budget. A single outage EVENT can short several consecutive hours (a long
MTTR), so it can move the estimate by more than ``event_h``: the rule bounds
one more hour, not one more outage.

``settle_draws`` names what would settle a ``not_confident`` estimate: about
``ceil(n · ((ci_hi − lole) / (goal − lole))²)`` draws if the mean and spread
hold, the count at which the interval's upper bound reaches the goal. It is
``None`` with a reason when that count is above the engine cap, when the
estimate equals the goal, or when the interval is degenerate.
"""

from __future__ import annotations

import math

__all__ = ["event_hours", "goal_fields", "lole_verdict", "not_confident_copy", "settle_text"]


def event_hours(weights, n_samples) -> float | None:
    """One sampled shortfall hour at the heaviest snapshot: ``w_max / n``.
    ``None`` when there is no positive weight or no draw."""
    w_max = max((float(w) for w in weights if float(w) > 0.0), default=None)
    if w_max is None or not n_samples or int(n_samples) <= 0:
        return None
    return w_max / int(n_samples)


def _settle(lole: float, hi: float, target: float, n: int, max_draws: int):
    if not (math.isfinite(hi) and hi > lole):
        return None, "the confidence interval is degenerate"
    if not target > lole:
        return None, "the estimate equals the goal"
    need = math.ceil(n * ((hi - lole) / (target - lole)) ** 2)
    if need > max_draws:
        return None, f"beyond the engine cap of {max_draws} draws"
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
    """What would settle a ``not_confident`` verdict, in words (D5)."""
    if goal.get("settle_draws") is not None:
        return (f"~{int(goal['settle_draws'])} draws if the mean and spread "
                "hold would settle it")
    return f"more draws alone will not settle it ({goal.get('settle_reason')})"


def not_confident_copy(mc: dict, target_h: float, lever: str) -> str:
    """A loop's verdict for status ``not_confident``: the nearest iterate's
    ``mc`` block (with its ``goal``) against the goal. ``lever`` names the
    iterate's setting, e.g. ``"ε = 0.035‱"``. The loop restores the user's
    own config on this status (adequacy loops 04, D4)."""
    goal = mc["goal"]
    hi = mc["lole_ci"][1]
    event = goal.get("event_h")
    event_clause = (
        f"one sampled shortfall hour at the heaviest snapshot "
        f"({float(event):.3g} h) would put it above"
        if event is not None else
        "the MC did not report what one sampled shortfall hour is worth")
    return (
        f"Not confident: no plan was verified against {target_h:g} h. The "
        f"nearest, at {lever}, estimates {float(mc['lole_hours']):.3g} h, at "
        f"or below the goal, but its 95% interval reaches {float(hi):.3g} h "
        f"and {event_clause}, so it is not a pass; "
        f"{settle_text(goal)}. Your original config has been re-solved, so "
        "the network you are holding is your own plan, not that one.")


def goal_fields(nearest, final) -> dict:
    """A finished loop record's ``event_h`` / ``settle_draws`` /
    ``settle_reason``: the nearest iterate's goal verdict when the loop ended
    not confident, else the final iterate's, else all None."""
    row = nearest if nearest is not None else final
    goal = ((row or {}).get("mc") or {}).get("goal") or {}
    return {k: goal.get(k) for k in ("event_h", "settle_draws", "settle_reason")}
