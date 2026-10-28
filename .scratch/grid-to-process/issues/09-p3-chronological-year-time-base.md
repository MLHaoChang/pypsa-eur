# P3: an hourly chronological year as a staged-template time base

Status: done (2026-10-28: `stage_network(time_base="year")`, the inventory-cadence validator rule and the audit are built; the B measurement exceeded the 30-min budget, so 10 waits on the owner's choice)
Type: task
Blocked by: 03
Gated: **yes** (solver: it changes the staged templates' time base and adds a validator rule)
Model tier: opus
Size: M

Read [the design](../design.md) §2.4 and §4.2 first. Owner decision D4 (2026-10-16): a full
hourly year.

## What to build

1. **`eh_site.stage_network` takes a time base:** `week` (today) or `year`.
   - `year` means 8,760 hourly snapshots per period.
   - The weightings are `objective` = `generators` = `stores` = 1. The investment-period
     weighting stays the period's years.
   - Stores are `e_cyclic_per_period` over the year.
   - Min up and down times stay in hours.
2. **Measure before template 10 is built.**
   - Run a demo concentrator-sized network (about 30 components, 3 periods, 26,280 snapshots,
     linearised UC) on HiGHS on the CI runner.
   - Report the solve time and peak memory to the coordinator for the owner, beside the `week`
     base for scale.
   - **Proposed budget: ≤ 30 min, within the runner's memory.**
   - **If it is exceeded:** template 10 waits, and chained representative days (design §4.2,
     option c) becomes a later gated solver item with its own plan check.
3. **Check what assumes one week per period** (not one hour per snapshot):
   - `rolling_horizon` and `lf_period_length_h` in `solver_service.py`;
   - `time_aggregation_service.py`;
   - `routers/network_time_axis.py`;
   - `validation_service.py`;
   - `profile_shapes.py`;
   - `study/packs.py`;
   - the campus study's per-hour tables (26,280 rows) and its ranking.

   Each is tested on the `year` base, or refused there with a reason.
4. **The inventory validator rule** (design §2.4, gated): a `week` template refuses an inventory
   whose delivery is not continuous or at most weekly, and names it.

## Acceptance

- A Store with a 21-day delivery cycle plans its fill and drain over the year, and its level
  carries across shipments.
- The same network on the `week` base is refused by the validator, with the store named.
- The week-based templates are unchanged (regression).
- The measurement is reported, with the solve time, the peak memory, the runner and the solver
  version.

## Triage (2026-10-28)

**Verified on master (41ade3be):** not built. `project_templates/eh_site.py` `stage_network` tiles the one-week `SNAPSHOTS` per period and has no time-base argument. Stores are already set `e_cyclic_per_period`. No validator rule for inventory cadence exists.

### Agent brief

**Category:** enhancement (gated: send GK A the plan before code)
**Summary:** `stage_network` takes a `year` time base (8,760 h per period), the week-assuming code paths are audited, the inventory-cadence validator rule is added, and the cost of a B-sized year solve is measured.

**Desired behaviour:**
- `stage_network(..., time_base="week")` is the default, and every existing template's output is byte-identical to today.
- `time_base="year"`:
  - 8,760 hourly snapshots per period;
  - weightings `objective` = `generators` = `stores` = 1, with the investment-period weighting still the period's years;
  - stores cyclic per period;
  - min up and down times unchanged (hours).
- Each place listed in item 3 above is either tested on `year` or refuses `year` with a named reason. The audit result is a table in the PR body.
- The validator refuses a `week`-base network holding a Store whose delivery is neither continuous nor at most weekly, naming the store (design §2.4).
- **The measurement:**
  - a B-sized demo network (~30 components, 3 periods, linearised UC) on HiGHS;
  - report solve time, peak memory, the machine (a GitHub-hosted runner if a job can host it; else the session container, with CPU and RAM named) and the HiGHS version;
  - send it to the coordinator before the PR asks for its OK.

**Acceptance:** the four checks above under "Acceptance". In addition:
- [ ] The 21-day Store test runs on a small year network inside the normal test budget. Mark the B-sized measurement run `slow`, or keep it out of the default suite.
- [ ] The week-template regression covers every `*_staged` template's objective, to the unit.

**Out of scope:** template 10 itself; chained representative days (design §4.2 (c)) unless the measurement exceeds the budget, which is then a new gated item for the owner, not this PR.

**Likely files:** `pypsa-gui/backend/project_templates/eh_site.py`, `pypsa-gui/backend/services/validation_service.py`, `pypsa-gui/backend/services/solver_service.py`, `pypsa-gui/backend/services/time_aggregation_service.py`, `pypsa-gui/backend/routers/network_time_axis.py`, `pypsa-gui/backend/services/profile_shapes.py`, `pypsa-gui/backend/services/study/packs.py`, new tests under `pypsa-gui/backend/tests/`.
**Blocked by:** none (03 is done). This blocks 10, through the measurement's verdict.
**Model:** Opus (engine, gated).
