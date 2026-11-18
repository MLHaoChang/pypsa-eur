# 02c — The DC backup NPV reads a backup genset's fuel curve

Status: done (#1538)

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

## Comments

### Plan GO, GK B (58), 2026-11-14

Conditions: H4. Hand oracle for a mixed fleet (one curved genset, one on the heat rate) under the MW-weighted rule. The note names the rule and lists the gensets that stay on the heat rate. With no curve, the dict is byte-equal to the pre-change call. The note states the full-load-over-run-hours assumption. H5: every reader of `fuel_curve_ignored` (frontend included) moves to the new keys. The only readers were tests; the keys are now `fuel_curve_priced` and `fuel_heat_rate_priced`. H7: mutants on the new fuel path.
