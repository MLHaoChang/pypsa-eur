# 02c — The DC backup NPV reads a backup genset's fuel curve

Status: needs-triage

Follow-up from homer-parity 02a (#1466), a condition of GK B's ruling 3.

## Problem

`dc_backup.backup_cost_delta` prices the genset leg's fuel from the backup spec's heat rate (or the pack's
efficiency), with no no-load term. When a backup genset carries a fuel curve (`fuel_curve_slope`,
`fuel_curve_no_load`, `fuel_price`), the solve prices it from the curve while the backup NPV ignores it. 02a states
this precedence in the assessment's cost notes (`fuel_curve_ignored: …`); the two numbers still differ.

## Wanted

When every backup genset carries a curve, the backup NPV prices the genset leg's fuel from the curves: the slope × fuel
price per MWh, plus the no-load fuel per on-hour over the run hours. With a mixed fleet, use a stated rule, such as
MW-weighted, and say it in the notes. The `fuel_curve_ignored` note then goes, or says which gensets still use
the heat rate.

## Tests

- A curve-carrying fleet: the NPV equals a hand calculation from the curve, no-load term included.
- No curve: the cost block is byte-equal to the one before.
