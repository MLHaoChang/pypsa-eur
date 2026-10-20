# Power-architecture comparison: AC / MV UPS / 800 VDC / SST

Status: needs-triage
Type: task
Blocked by: 03
Gated: yes (finance)
Model tier: opus
Size: M

## What to build

- Architecture as a DC template parameter. Each option carries a conversion-loss chain (feeds the effective PUE), capex per MW, and which grid services it can offer (feeds 02).
- A compare view across architectures: energy, cost, grid-readiness verdicts.
- Data rows are "assumed" with sources, in a defaults-pack version (never edit a shipped pack).
