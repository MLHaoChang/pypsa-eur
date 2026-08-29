"""
How a certified lever value is SPELLED for the user to type.

★ This module exists because one panel printed two different numbers for one
certified value, six lines apart, on both of the loops that have one.

On the margin loop the verdict said ``set reserve_margin = 0.6716`` (`%g`,
six significant figures) under a restore explainer saying ``reserve_margin =
0.671600430725``. On the cap loop — shipped since Phase 7 — the disagreement
was worse and pointed the other way: the verdict printed six significant
figures while the panel's own sentence used the BADGE formatter (two
significant figures below 1), so a certified ``0.0347281`` was rendered
``0.035``.

Neither is cosmetic, and each is unsafe in the direction its lever runs:

* a reserve margin is a THRESHOLD on required firm capacity, so a SHORTER
  value is a LOOSER standard;
* an ENS cap is a CEILING on unserved energy, so a value rounded UP is a
  LOOSER standard.

Either way the user is told to type a number that buys a cheaper build than
the plan the study certified, in a sentence whose entire purpose is to let
them reproduce it. A display number in a table may round; an INSTRUCTION to
type a value may not.

So both surfaces now print `format_lever_value`, which is a deliberate MIRROR
of the frontend's ``String(Number(v.toPrecision(12)))`` — not an independent
formatting choice. The same table of (value, spelling) pairs is asserted in
both languages, and the backend re-derives it from `node` itself wherever node
is installed.
"""
from __future__ import annotations

import math
from decimal import Decimal

# How many significant figures a certified lever value is quoted to, on BOTH
# sides of the wire. Twelve, not six, and not the badge's two.
LEVER_SIGFIGS = 12


def format_lever_value(value: float) -> str:
    """The certified value AS THE USER MUST TYPE IT — JavaScript's spelling.

    A deliberate mirror of ``String(Number(v.toPrecision(12)))``. Python's
    `repr` is not that expression and gets three cases wrong:

    * an integral value — JS prints ``1``, `repr` prints ``1.0``;
    * ``1e-6 ≤ v < 1e-4`` — JS still prints fixed (``0.0000123456``) where
      `repr` has already switched to exponential;
    * a padded exponent — JS writes ``1e-9``, `repr` writes ``1e-09``.

    The digits themselves are shared: both languages print the SHORTEST
    decimal that round-trips, so `repr`'s mantissa is JS's mantissa and
    `Decimal` only moves the point.
    """
    y = float(f"{float(value):.{LEVER_SIGFIGS}g}")
    if not math.isfinite(y):
        # Not reachable for either lever, but a formatter that raises is worse
        # than one that says so.
        return repr(y)
    s = repr(y)
    if s.endswith(".0"):
        return s[:-2]
    if "e" not in s:
        return s
    exponent = int(s.split("e")[1])
    if -7 < exponent < 21:
        # JS prints fixed across this whole range; Python gave up at 1e-4.
        # `Decimal` re-spells the SAME shortest-round-trip digits without an
        # exponent, so no precision is invented or lost.
        return format(Decimal(s), "f")
    # Outside it both print an exponent, and only the zero padding differs.
    mantissa = s.split("e")[0]
    return f"{mantissa}e{'-' if exponent < 0 else '+'}{abs(exponent)}"
