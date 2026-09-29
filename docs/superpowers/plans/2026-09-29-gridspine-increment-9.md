# gridspine increment 9: connection-capacity screening

Written before the code, as with every increment. It will be amended where
building it proves it wrong.

## The question

A data centre, a large C&I site or an industrial load asks the network
operator one question before anything else: **how many MW can I connect at
this point, and what stops me at that number?** A generation or storage
developer asks the same question in the other direction.

Today gridspine can answer "is this grid state secure?" (AC load flow,
N-1/N-2, IEC 60909, SCR) for the hours it selects. It cannot answer "how much
more can this bus take?". An engineer can approximate it only by editing the
network and re-running until something breaks. This increment makes the
capacity a direct output: per candidate bus, per studied hour, in MW, with
the binding constraint named.

## What "capacity" means here

**Capacity at bus B in hour h, for a connection of kind K (load or
generation)** is the largest P ≥ 0 MW such that adding P at B, balanced as
described below, creates **no new violation and worsens no existing one**,
intact and under every N-1 outage in the study's contingency set.

That definition is deliberate. The base case can already carry violations:
case39 at some hours already has N-1 overloads unrelated to any new site. A
definition of "no violations" would return 0 MW everywhere, and a definition
that ignores the base case would hide a new site pushing an existing overload
deeper. The rule is:
- a constraint satisfied at P = 0 must stay satisfied;
- a constraint already violated at P = 0 must not get worse (within
  tolerance).

The criteria are the ones the N-1 screen already applies, reused rather than
redefined (`static/contingency.py`):
- **Thermal:** branch loading ≤ `LOADING_MAX_PCT` (100 %), intact and under
  N-1.
- **Voltage:** every bus within [`V_MIN_PU`, `V_MAX_PU`] = [0.9, 1.1] pu,
  intact and under N-1.

**Reported, not a limit:** for a generation connection, the SCR at B with the
new MVA added, from the existing `static/strength.py` convention. That module
rules that SCR bands are reported, not gates, and this increment does not
overturn that.

## Method: DC screen everywhere, AC where it matters

1. **DC screen, every bus, every hour.** With the PTDF and LODF already built
   in `static/lodf.py`, the DC capacity is closed-form. For each branch l and
   each outage k, the headroom on l divided by the sensitivity of l's flow to
   an injection at B (balanced as below) bounds P. The minimum over (l, k) is
   the DC capacity, and the (l, k) that attains it is the binding pair. This
   costs one pass of linear algebra per hour, so a full year of candidate
   buses stays cheap. It is labelled an **estimate**: the N-2 prune measured
   that DC underestimates loading on this fixture's critical branch.
2. **AC verification at the study's selected hours, at the requested buses.**
   Bisection on P, starting from the DC estimate:
   - at each trial P, run the AC load flow and the N-1 screen (lightsim2grid,
     the same code path as stage 2) and apply the definition above;
   - stop at a 1 MW tolerance (configurable);
   - report the AC capacity, the binding constraint (kind, element and
     contingency) and the DC estimate beside it, so the gap is visible rather
     than hidden.

## Modelling assumptions (each one ledgered)

- **Balancing.** Where the extra MW comes from, or goes to, decides which
  branches load up. This is a decision for the owner; see the open question
  below.
- **Power factor of the new connection:** 0.98 lagging for load, typical of a
  data centre with power-factor correction, and unity for generation. Both
  are configurable.
- **Contingency set:** the study's own N-1 set. Islanding outages are
  excluded from the binding search and listed separately, as the N-1 screen
  already does.
- **Ratings:** one rating per branch (the model has no short-term emergency
  rating), so N-1 uses the same 100 %. This is conservative, and the ledger
  says so.

## Output

- **`capacity.csv`** in the run directory and in each hour's handoff bundle,
  one row per (bus, hour, kind). Columns: `capacity_mw` (AC, or empty where
  only DC ran), `dc_estimate_mw`, `binding_kind` (thermal_intact,
  thermal_n1, v_low, v_high, none_up_to_cap), `binding_element`,
  `binding_contingency`, `scr_at_capacity` (generation only), `method`.
  `none_up_to_cap` means no constraint bound up to a search cap (default
  2,000 MW), which is reported as "≥ cap", never as infinity.
- **Schema** in `schema/`, validated on write and on read, like every other
  artifact.
- **GUI:** a "Connection capacity" section in the study panel: choose buses
  (default: every load bus), see the table per selected hour, and see the
  binding constraint for each.
- **No new chat tool yet.** The copilot reads the table through the existing
  result endpoints. A tool can follow once the table's shape has settled in
  use.

## Tests that decide it

- **Hand-built cases.** A 3-bus network with an obvious answer: one line
  rated 100 MVA, so capacity equals rating minus existing flow. DC and AC
  both agree to within tolerance and name that line.
- **Base case already violated.** Capacity is not zeroed by the existing
  overload but stops where that overload would worsen.
- **N-1 binds before intact.** A double-circuit case where the intact state
  has headroom but losing one circuit does not.
- **Voltage binds.** A long radial line where voltage, not loading, binds.
- **Consistency.** Adding the reported AC capacity to the network and
  re-running the full stage-2 screen shows no new violation, and adding
  capacity + tolerance shows one. This is the property the whole feature
  exists for, checked with the real screen rather than the bisection's own
  bookkeeping.
- **The browser run** at the end, as with increments 7 and 8.

## Out of scope, flagged

- Fault-level or breaker-duty limits: the model has no switchgear ratings.
  Once a rating table exists, fault-level headroom is a natural next
  criterion.
- Dynamic limits (fault ride-through, RoCoF): that is the compliance
  increment's question.
- Staged or time-varying connections (for example "200 MW now, 400 MW
  later"): the output is per hour, which is the raw material for that, but
  no scheduling is attempted.

## Decided by the owner, 2026-09-29: balancing is pro-rata over committed units

When the new load draws P at bus B, the **committed synchronous units** supply
it in proportion to their available upward headroom (`p_max - p` at that
hour). The slack takes only what those units cannot, and the losses as
always. A generation connection is the mirror image: committed units back
down in proportion to their downward headroom (`p - p_min`), and the slack
absorbs what is left.

Slack-only was the alternative. It was rejected because on case39 it routes
every added MW toward BUS_31, which makes the answer depend on where the
slack happens to sit. Pro-rata is closer to how the system would re-dispatch.
The choice is ledgered on every capacity result. It also means the DC
sensitivity is to a **distributed** balancing vector, not to the reference
bus: the PTDF column for B minus the headroom-weighted sum of the committed
units' columns, re-derived per hour because commitment changes by hour.
