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

__all__ = ["event_hours", "lole_verdict"]


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
