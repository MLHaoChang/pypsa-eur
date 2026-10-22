# Energy-flow Sankey and energy-flows export

Status: done (#423, 1a9ca8d3)
Type: task
Blocked by: none
Gated: no (post-processing of solved results; no LP change)
Model tier: sonnet
Size: M

Read [the spec](../spec.md) first.

## What to build

- **Backend:** `energy_flows(n, period, window)` attributes each snapshot's site energy into Source → Via → Destination flows, in MWh.
  - Sources: each generator carrier, grid import.
  - Via: Direct, or a storage unit.
  - Destinations: each load (or load group), EV, export, losses.
  - Battery charge is tracked by origin with a stated rule (proportional pool, documented). Discharge then carries that mix, so "Solar via Battery" and "Grid via Battery" stay apart.
  - The flows conserve energy: sources = destinations + losses + Δstorage. A test checks this to a stated tolerance.
- **Route:** `/results/energy_flows`, plus a CSV export of the same rows.
- **Frontend:** a Sankey view in results (reuse the recharts Sankey from `ValueFlowsView.tsx`), with a period/date-range slider and a hover tooltip (Source / Via / Destination / MWh), plus a summary series below.
- A value the server can't establish shows as unavailable, never as zero (ADR-0001).

## Done when

It works on the data-centre, microgrid and H2-hub templates, the conservation test passes, and the CSV matches the chart.
