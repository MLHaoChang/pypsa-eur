# P3: an hourly chronological year as a staged-template time base

Status: needs-triage
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
