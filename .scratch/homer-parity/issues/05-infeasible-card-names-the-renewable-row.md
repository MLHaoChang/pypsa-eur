# 05 — The Issues panel's infeasible card names the minimum-renewable row

Status: needs-triage

Follow-up from homer-parity 01a (#1424), asked for by the UX reviewer.

## Problem

When a solve is infeasible because of the minimum renewable fraction, the solve log names the row and gives the reach:

```text
[INFEASIBLE]   • Minimum renewable fraction: the target of 100% (row min_renewable_fraction) needs 55,347.1 MWh of renewable electricity served; the renewable generators can produce at most 46,539.8 MWh.
```

The toast sends the user to the Issues panel, but the "No feasible solution" card there stays generic. The user is never shown which constraint failed unless they open the log.

## Ask

Surface the `_diagnose_infeasibility` hint lines (`renewable_fraction.infeasibility_lines`) on the Issues panel's infeasible card. At minimum, name the constraint ("Minimum renewable fraction") and the need/reach figures, plus a link that opens Solver settings › Off-grid constraints in place.

## Notes

- This is a UI change, so it needs a UX review.
- 01b's operating-reserve row should reuse the same path.
