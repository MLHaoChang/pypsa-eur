# Flexibility-market revenue from reserved BESS/UPS headroom

Status: needs-triage
Type: task
Blocked by: 03
Gated: yes (finance, solver)
Model tier: opus
Size: M

## What to build

- Reserve products (FCR / aFRR / mFRR style, generic names) as headroom the BESS or UPS commits, with the critical-backup energy kept untouchable.
- Revenue goes into the investment case. The reserved headroom shows in dispatch.
- Prices are user time series. No market-specific price data is shipped.
