# ADR-0001's contract stays as flags and nulls; no generic `Resolved[T]` wrapper

A figure that may be unresolvable is expressed as it is today — a nullable field
plus an explicit availability flag, per ADR-0001 — and NOT as a generic wrapper
type (`Resolved[T]`, `Maybe[T]`, an availability monad) threaded through schemas,
routers and the client.

## Why this keeps being proposed

The observation that prompts it is real and will recur: the concept has several
spellings. `available`, `capex_lifetime_available`, `capital_costs_available`,
`lopf_available`, `ac_pf_available`, `source_available`, `partial`, and a bare
`| None` on the figure itself. Any architecture pass that greps for them finds
nine schema classes and eight router sites and concludes the concept is
scattered. A wrapper type would also make the unavailable branch impossible to
FORGET — a stronger guarantee than a test, because it is structural.

That is a fair argument. It was measured on 2026-08-27 and did not survive the
measurement.

## What the measurement found

  * 43 schema classes / 381 annotated fields. Nine carry an availability flag,
    and all nine spell it the same way: `available: bool`.
  * 8 availability-key sites across the dict-returning routers, in 2 files.
  * Of 25 nullable NUMERIC schema fields, most are INPUT nullability
    (`GeneratorCreate.overnight_cost`, `discount_rate`, `ramp_limit_up`) meaning
    "the user did not specify", not "we could not compute". ADR-0001 does not
    govern those, and wrapping them would assert something false about them. The
    genuine result-unresolvable population is roughly four fields.
  * The client side is already consolidated: one `COST_UNAVAILABLE` constant,
    `UnavailableCell` at 19 production call sites, and `UnavailableBlock` (added
    the same day, commit `dacb649e`) at the remaining six. Both counts exclude
    test files — a call site in a test is not a place the copy can drift.
  * The failure mode has a fence. `tests/test_availability_wire_conformance.py`
    scans `routers/` for a function that consumes a `_compute_*_summary` and
    returns a dict literal with no availability key — which is exactly the one
    real defect this family produced (`get_economics_by_carrier` returning a
    bare `{}` on its not-ready path).

So the concept is not scattered; it is uniform, small, and already guarded. The
several "spellings" are mostly one spelling plus a handful of scope-qualified
names that say WHICH source is unavailable — information a generic wrapper would
have to carry anyway.

## Consequences

The cost is accepted deliberately: the unavailable branch remains something a
consumer can forget, caught by a conformance test rather than by the type
system. If that test is ever deleted, this decision loses its support and should
be revisited rather than inherited.

Two triggers that WOULD justify reopening:

  * the availability flag genuinely diverges — a second spelling of the plain
    payload-level `available` appears, or a class starts meaning something
    different by it;
  * a dropped-availability defect ships that the wire-conformance scan could not
    have caught, which would show the fence is the wrong shape rather than the
    wrong strength.

Absent those, prefer the cheap thing: when a new results surface is added, give
it `available: bool` spelled exactly that way, and let the conformance test hold
the line.

Recorded because this proposal has now been raised and rejected on evidence
once; without the numbers written down, the next pass re-derives it from the
same grep and pays for the measurement again.
