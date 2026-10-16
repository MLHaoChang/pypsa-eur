# P3: a chronological year at 3 h as a staged-template time base

Status: needs-triage
Type: task
Blocked by: 03
Gated: **yes** (solver: it changes the weighting convention the staged templates share)
Model tier: opus
Size: M

Read [the design](../design.md) §2.4 and §4.2 (owner decision D4) first.

## What to build

1. **`eh_site.stage_network`** takes a time base: `week` (today) or `year_3h`.
   - `year_3h`: 2,920 snapshots per period, with weightings `objective` = `generators` =
     `stores` = 3.
   - Stores are `e_cyclic_per_period` over the year.
   - `min_up_time` and `min_down_time` are converted to snapshots, rounded up, and the ledger
     says so.
2. **Measure before building B.** Run a demo concentrator-sized network (about 30 components,
   3 periods) at `week`, `year_3h` and a full hourly year on HiGHS on the CI runner, and report
   the times to the coordinator. The owner's D4b gate is ≤ 10 min for `year_3h`.
3. **Audit everything that assumes 1 h or 168 h snapshots:**
   - tariffs: 15-min billing, demand charges, ratchets;
   - results tiles;
   - `time_aggregation_service`;
   - the campus study's hourly tables;
   - stress and outage windows.

   Fix each, or refuse `year_3h` there with a reason.

## Acceptance

- A Store with a 21-day delivery cycle is planned the same on `year_3h` and on the hourly year,
  within a tolerance the ticket states.
- The week-based templates are unchanged (regression).
