# Coupling report: ride-through, island and N-1 to lost tonnes and EUR

Status: ready-for-agent (triage 2026-10-28: still blocked by 12; gated, plan to GK A first)
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

## Triage (2026-10-28)

**Verified on master (41ade3be):** not built. No "grid risk to production" section exists. The inputs exist or arrive with 12:
- ride-through results arrive with 12;
- island hours and pickup blocks come from the island sidecar (`services/solver/island.py`, `gridspine/schema/island.py`, `campus_island_report.py`);
- outage rates and MTTR are in the adequacy archetypes;
- the LP's lost tonnes come from the process view (04) and process loss (08).

### Agent brief

**Category:** enhancement (gated: send GK A the plan before code)
**Summary:** a "Grid risk to production" report section and Guided tile group that turns each grid result into tonnes, then EUR, with no new loop.

**Desired behaviour:**
- One line per grid risk:
  - **Ride-through:** failing hours × units that trip × restart lost tonnes.
  - **Island:** units outside the pickup blocks × island duration × rate.
  - **N-1:** the site transformer's outage rate × MTTR × the rate of the units it feeds.
  - **Connection curtailment:** the LP's own lost tonnes, shown, not recomputed.
- Each line shows its tonnes, its EUR at the bus's `lost_production_value`, and the result file it came from.
- EUR is null and displayed "Unavailable" when the value is missing. A risk whose input is missing reads as such, not 0.
- Total = Σ lines.

**Acceptance:**
- [ ] On A and B, every line traces to its source result file (named in the payload).
- [ ] The total equals the sum of the lines.
- [ ] A bus with no `lost_production_value` gives tonnes and a null EUR.
- [ ] A network with no process unit omits the section.

**Out of scope:** any LP feedback loop (P4, design §7); new grid studies.

**Likely files:** new `pypsa-gui/backend/services/results/grid_risk_production.py`, `pypsa-gui/backend/routers/results.py`, `pypsa-gui/backend/services/results/process_view.py` (read only), the report chapter code, `pypsa-gui/frontend/src/pages/Results.tsx`, tests.
**Blocked by:** 12 (12b if split); 08 is done.
**Model:** Opus (reads gridspine and adequacy results, gated). The Guided tiles need the UX reviewer's OK.
