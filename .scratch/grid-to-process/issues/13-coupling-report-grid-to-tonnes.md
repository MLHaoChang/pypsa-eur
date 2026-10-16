# Coupling report: ride-through, island and N-1 to lost tonnes and EUR

Status: needs-triage
Type: task
Blocked by: 08, 12
Gated: **yes** (it reads gridspine and adequacy results)
Model tier: opus
Size: M

Read [the design](../design.md) §5 first.

## What to build

A "Grid risk to production" report section and Guided tile group that turns each grid result
into tonnes, then EUR:

- **Ride-through:** failing hours × the units that trip × restart lost tonnes.
- **Island:** the units outside the pickup blocks × island duration × rate.
- **N-1:** transformer outage rate × MTTR × the rate of the units it feeds.
- **Connection curtailment:** this is already the LP's lost tonnes, so it is shown, not
  recomputed.

Each line shows its tonnes and its EUR. EUR is null when the lost-production value is missing.
This ticket builds no new loop (design D5).

## Acceptance

- On A and B, every line traces to its source result file.
- The total equals the sum of the lines.
