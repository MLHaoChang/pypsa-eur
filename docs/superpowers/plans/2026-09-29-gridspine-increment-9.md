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

## Stage A, as built (2026-09-29)

`static/capacity.py` (`capacity_dc`, `capacity_ac`, `apply_connection`) and
`schema/capacity.py`. The N-1 screen's per-contingency loop was split into
`_branch_outcomes` / `_unit_outcomes`, which return the raw loading and
voltage arrays, and a summary step. The capacity search compares those arrays
element by element. The screen's own 94 tests pass unchanged on the refactor.

**Measured on the hand-built grids**, each as worked out on paper:

| grid | kind | DC (MW) | AC (MW) | binds |
|---|---|---|---|---|
| radial, 95.3 MVA line, 30 MW existing | load | 65.3 | 62.2 | that line, intact (AC carries Q at pf 0.98, plus losses) |
| parallel pair, 20 MW existing | load | 75.3 | 72.9 | the surviving circuit under N-1 |
| parallel pair, 120 MW existing (N-1 already at ~126 %) | load | 0.0 | 0.0 | the existing overload may not deepen |
| same | generation | 240.0 | 240.9 | relieves it, until the reversed flow reaches the same level |
| 200 km line, rated far above flow | load | 1900.3 | 27.8 | **voltage**, which DC cannot see |

**What case39 says, and why it is not a bug.** On native case39 at its stored
operating point, **every** load bus reads about 3 MW. The chain:
- `BUS_02-BUS_03` is already at **111.7 %** after losing `BUS_26-BUS_27`;
- pro-rata balancing puts about **72 %** of any new load on G_BUS_30, which
  holds 790 of the 1,101 MW of committed upward headroom;
- G_BUS_30's power crosses that branch, so every load bus has a
  post-contingency distribution factor of **9–76 %** on it.

A 5 % distribution-factor threshold (the PJM/MISO convention for holding a
new connection responsible for an existing constraint) was checked and
changes nothing: all 39 buses exceed it. The answer is a true property of
this operating point combined with the owner's balancing rule.

Generation discriminates as expected, because it relieves that branch:

| bus | DC (MW) | AC (MW) | binds |
|---|---|---|---|
| BUS_03 | 906.5 | 942.8 | a new N-1 overload on `BUS_03-BUS_04` |
| BUS_08 | 240.4 | 196.2 | AC below DC: the known DC blind spot |

The study's own hours, with their unit commitment and RES, will read
differently. That is what stage B runs.

**Verification.**
- 22 tests, including a pandapower-only oracle. It re-solves at the answer
  and at the answer + 2 MW, with no capacity code in the loop, for one
  worsened-existing case and one new-violation case.
- Nine mutations were each caught by the test written for them: existing
  violations allowed to deepen; N-1 ignored; slack-only balancing; headroom
  not capped; power factor ignored; DC N-1 ignored; voltage not checked; the
  cap reported as infinity; decommitted units given a share.
- Cost: about 0.3–0.7 s per AC search on case39 (5–12 evaluations).

## Stage B, as built: the study and the backend

**Amended while building.** The plan put the AC search inside the study, at
the requested buses. Reading the driver changed that: an AC search costs
about 0.5 s per bus, per kind, per hour, and asking about a different bus
would have meant re-running the whole study. So:

- **The study** writes the **DC screen for every bus, both kinds, at every
  selected hour**: `capacity.csv` in the run directory and each hour's rows in
  its bundle (listed in the bundle manifest). The three capacity ledger lines
  (definition, the owner's balancing rule, power factor and DC-vs-AC) ride
  with every screened run.
- **The AC answer is on demand.** `POST /api/gridspine/{name}/capacity {bus,
  kind}` rebuilds each selected hour's network exactly as the handoff pass
  does (`load_case39_res` plus `apply_snapshot` from the run's own
  `dispatch.csv` and `loads.csv`) and runs the search. It upserts the result
  into `capacity.csv` and into each bundle's copy, so a downloaded bundle
  carries it.
- `GET /api/gridspine/{name}/capacity` returns the table, with NaN as null.

**Refusals and safety:**
- 404 before a screened run, with the reason.
- 409 while a study is queued or running, since it is about to rewrite the
  run directory.
- 422 for an unknown bus (the driver's message) or a malformed request
  (pydantic: `kind` is `load` or `generation`, `bus` is 1–64 characters).
- The search runs in the threadpool.
- A per-run lock serialises the upserts, so two concurrent searches cannot
  lose one another's rows.
- `gridspine.drivers.capacity` was added to the app's PyInstaller hidden
  imports. The packaging guard would otherwise have failed, as it did for
  `year_study` at the #22/#58 merge.
- The route inventory was regenerated with `tools/openapi_diff.py`, and the
  diff is exactly the two new routes.

**Tests:**
- 8 study-level tests on a real two-hour run.
- 14 backend tests: the real solved-study fixture for behaviour, stubs for
  wiring.
- Six mutations, each caught: upsert appending, bundles not updated, bundle
  dropping the file, ledger omitting the rule, the spec entry missing, and
  the search run on the event loop.

## Stage C, and the browser run (2026-09-29)

**Setup.** A "Connection capacity" section in the study panel, shown once a
study completes:
- choose the hour (the study's selected hours) and the connection kind;
- every bus is listed with its capacity, marked `≈` with a "DC est." tag, or
  exact with an "AC" tag;
- what binds is said in words: "overload of X after losing Y", "low voltage
  at Z", or "nothing binds up to the cap", shown as `≥ cap`;
- "Compute AC" runs the on-demand search for that bus and refreshes the
  table. It is disabled while the project has a queued or running study.

**Browser run.** Chromium, driving the real app on fresh app data.

*Run 1: a flat hand-written client dispatch.*
- Every bus read **0.0 MW**, for both load and generation.
- Checked against that run's own base case rather than taken on trust: the
  flat 416.9 MW-per-unit dispatch has **3 intact overloads** (worst 160 %) and
  violations in **44 of 60** N-1 cases. So every direction deepens one of
  them, and 0 MW is the honest answer for that state. The test data was
  unrealistic; the rule was not wrong.

*Run 2: a generated 24 h UC study* (study completed in 15 s; hours 3, 17 and
19 selected).
- **Generation:** 26 of 39 buses can take more than 0 MW at hour 3, several
  over 1 GW (BUS_06 ≈1,203 MW, BUS_08 ≈1,196 MW).
- **Load:** only 4 buses can take any (≈89–92 MW), bound by `BUS_06-BUS_11`
  after losing `BUS_01-BUS_02`.
- **AC at BUS_16**, one request of about 1.6–2.0 s across the three hours:
  13.8 / 3.1 / 6.3 MW.
- The downloaded `bundle_h3.zip` carries `capacity.csv` (listed in its
  manifest) with the AC row and its DC estimate beside it, plus the three
  capacity ledger lines.

**Found and fixed from the browser run:**
1. **A limit set by an existing overload read as headroom.** BUS_16's
   13.8 MW at hour 3 is bound by `BUS_02-BUS_03`, already overloaded under
   N-1 before anything connects. The figure is the 0.5 % worsening tolerance
   over the bus's distribution factor, not room on the network. Every result
   now carries `binding_preexisting`, and the panel says "— already
   overloaded before connection". Re-checked in the browser: hour 3 is
   flagged; hours 17 and 19, which bind on constraints that were within
   limits, are not.
2. **DC and AC disagreed about the rule.** DC gave that row 0.0 because it
   applied no tolerance at all. It now allows an already-violated flow the
   same `worsen_tol_pct` of its rating that AC does.
3. **Layout.** In the narrow panel the Bus and Capacity columns ran together
   ("BUS_1613.8 MW"), and values and tags wrapped. Those columns no longer
   wrap; only the binding text does.

All three were fixed test-first, except the layout, which was re-checked by
screenshot.
