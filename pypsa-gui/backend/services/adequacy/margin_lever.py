"""
The margin ⇄ controller substitution (margin-loop spec §1).

`services/adequacy/coupling.py` is a search over ONE scalar that gets STRICTER
as it gets SMALLER: it shrinks multiplicatively toward zero, asserts the
iterate is strictly positive, bisects in log space with a geometric midpoint,
tests "the miss is the LOOSER endpoint" as ``miss > met``, and breaks ties on
equal cost by preferring the LARGER value (the cheapest standard among equal
plans). A planning reserve margin runs the other way — larger is stricter, and
its loose end is ``0``, which no multiplicative search can leave.

So the margin is not fed to the controller. Its RECIPROCAL is::

    x = 1 / (1 + m)          m ≥ 0  ⇒  x ∈ (0, 1]
    m = (1 / x) − 1

and every one of those five sites is then correct for the margin without a
line of `coupling.py` changing (spec §0, the hard constraint — the Phase-7
suite is the regression oracle for this lever precisely because it is
untouched):

* the multiplicative shrink toward 0 raises ``m`` without bound, and ``m = 0``
  is ``x = 1``, a legitimate loose end with no clamp problem;
* ``assert e > 0`` holds by construction for every finite ``m``;
* ``mid = sqrt(met·miss)`` stays inside the bracket because BOTH endpoints are
  strictly positive — the property an additive lever (``limit − m``) would
  lose the moment an endpoint hit zero, collapsing the bracket at exactly the
  place it matters;
* ``miss > met`` reads "the miss is the looser one", which under ``x`` is the
  SMALLER margin, which is what a miss is;
* the ``key=(cost, -x)`` tie-break prefers the larger ``x``, i.e. the SMALLEST
  margin among equal-cost met plans — the cheapest certified standard.

WHY A MODULE AND NOT TWO LINES IN THE ROUTE. There are two directions and
they must be exact inverses: the route converts on the way in (``eps0``, every
``solve_at``) and on the way out (every stored row, ``lever_star``). A second
copy of either formula is a second definition of the lever, and the failure
mode is silent — a margin reported one iterate out of step with the plan that
was actually solved. Pure by construction: no routes, no ``_state``, no
``pypsa``, so the property test that pins it needs nothing but arithmetic.
"""
from __future__ import annotations

import math

# The schema's own bound on `SolverConfig.reserve_margin` (`le=5`, see
# models/schemas.py: 15 typed for 15 % is a 1500 % margin, and a plan sized
# against it is a fiction). The loop generates margins the user never typed,
# so it must respect the same bound — otherwise a `restore="final"` run
# persists a margin the config schema would refuse on the next PUT.
MAX_MARGIN = 5.0

# How far past the incumbent plan's own TIGHT margin the first informed step
# lands (spec §2.3). `firm_mw / peak_mw − 1` is the smallest margin at which
# the incumbent is tight: at exactly that value the plan is feasible,
# unchanged, same hash, same LOLE, and flagged `binding` while nothing moved.
# The step must therefore STRICTLY exceed it, and 5 % is enough to clear float
# noise in the peak without skipping the region the search is trying to
# bracket.
STEP_OVERSHOOT = 0.05


def to_x(m: float) -> float:
    """The controller's coordinate for a reserve margin ``m ≥ 0``.

    A negative margin is not a weaker standard, it is a nonsense one — and
    `_prm_margin` reads anything ``≤ 0`` as NO STANDARD AT ALL, so silently
    coercing it would hand the loop an unconstrained plan while it believed it
    was solving the loosest constrained one. Refuse instead.
    """
    try:
        val = float(m)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"reserve margin {m!r} is not a number") from exc
    if not math.isfinite(val) or val < 0.0:
        raise ValueError(
            f"reserve margin {val!r} is outside the lever's domain: the "
            "standard is defined for m ≥ 0 (0 == firm capacity equal to the "
            "peak), and a negative margin is not a weaker standard but no "
            "standard at all")
    return 1.0 / (1.0 + val)


def to_margin(x: float) -> float:
    """The reserve margin the controller's ``x`` stands for.

    ``x ≤ 0`` is the controller's own "no target" sentinel in ε-space and has
    no image here: it would map to a margin of ``-1`` or worse, which is
    `_prm_margin`'s "no standard". The controller never produces one (it
    asserts ``e > 0`` before every solve), so reaching this is a binding bug
    and must not be papered over with a clamp.
    """
    try:
        val = float(x)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"lever coordinate {x!r} is not a number") from exc
    if not math.isfinite(val) or val <= 0.0:
        raise ValueError(
            f"lever coordinate {val!r} is outside (0, 1]: x ≤ 0 is the "
            "controller's NO-TARGET sentinel and stands for no margin at all")
    return 1.0 / val - 1.0
