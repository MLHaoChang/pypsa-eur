# Flexibility-market revenue from reserved BESS/UPS headroom

Status: ready-for-agent
Model: Opus
Type: task
Blocked by: 03
Gated: yes (finance, solver)
Model tier: opus
Size: M

## What to build

- Reserve products (FCR / aFRR / mFRR style, generic names) as headroom the BESS or UPS commits, with the critical-backup energy kept untouchable.
- Revenue goes into the investment case. The reserved headroom shows in dispatch.
- Prices are user time series. No market-specific price data is shipped.

## Comments

### 2026-11-05: triage sweep (AA-FILL-2), role ready-for-agent

Was: `needs-triage`. Files: reserve-product constraints in the solve path, investment-case revenue lines, dispatch view. Blockers: 03. Reviewers: GK A (solver/adequacy engine) and GK B (finance).
