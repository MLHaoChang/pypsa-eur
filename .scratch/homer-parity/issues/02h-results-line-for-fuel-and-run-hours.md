# 02h — Show the fuel and run-hours blocks of the cost breakdown in Results

Status: needs-triage

Follow-up from homer-parity 02b (#1506).

## Problem

`services/results/cost_breakdown.py` returns a `fuel` block (fuel burnt by each generator with a fuel curve, `fuel_curve.fuel_use`) and a `run_hours` block (`fuel_curve.run_hours`), but no Results card reads them. A user who set a fuel curve, an O&M cost per run hour or a lifetime in run hours sees the costs those produce and never the fuel or the hours behind them.

## Wanted

- One line per generator with a fuel curve in the Results cost view: fuel burnt per period, and run hours, next to the costs they drive.
- Placement agreed with the UX reviewer before building (the project workflow's Results section).
- Maybe split the run-hours helpers out of `fuel_curve.py` while there, if the Results reader makes that cleaner.

## Acceptance

- The two blocks render for a network with a fuel-curve generator and stay hidden without one; UX OK on the head.
