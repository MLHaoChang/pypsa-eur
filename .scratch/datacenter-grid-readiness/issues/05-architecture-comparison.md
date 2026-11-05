# Power-architecture comparison: AC / MV UPS / 800 VDC / SST

Status: ready-for-agent
Model: Opus
Type: task
Blocked by: 03
Gated: yes (finance)
Model tier: opus
Size: M

## What to build

- Architecture as a DC template parameter. Each option carries a conversion-loss chain (feeds the effective PUE), capex per MW, and which grid services it can offer (feeds 02).
- A compare view across architectures: energy, cost, grid-readiness verdicts.
- Data rows are "assumed" with sources, in a defaults-pack version (never edit a shipped pack).

## Comments

### 2026-11-05: triage sweep (AA-FILL-2), role ready-for-agent

Was: `needs-triage`. Files: DC template parameters, defaults-pack version (new version, never edit a shipped pack), compare view in the frontend. Blockers: 03. Reviewers: GK A and GK B (finance); UX for the compare view.
