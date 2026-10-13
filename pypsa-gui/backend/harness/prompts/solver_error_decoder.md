---
constant: _SOLVER_ERROR_DECODER
trailing_space: [facts]
---

Solver-error decoder (#3). `full` is the exact pre-split literal (the tool imperative sits in the MIDDLE, so it cannot be facts + chaining); facts and chaining are the tools-off halves, kept word-multiset-equal to `full` by test_solver_and_rubric_halves_cover_the_same_words.

## full

Solver-error decoding. On ANY failed or aborted run, call
get_simulation_log_history BEFORE answering and quote the failing TRACEBACK
frame. Common causes: 'infeasible' = over-constrained bounds or a CO2 cap
too tight / capacities too small to meet load; 'dim_0' in a linopy/xarray
error = a time-series (_t) frame lost its index name 'snapshot'; "cannot
include dtype 'M' in a buffer" = a multi-period → flat demotion tripping a
pandas MultiIndex reindex bug; an assign_duals KeyError on a DatetimeIndex =
a stale MultiIndex left on a dual _t frame after a period change; a 500 with
a short plain-text body from /results/* = NaN or Inf leaked into JSON
rendering. Explain the likely cause in plain terms and suggest the
corrective lever (loosen the bound, rebuild snapshots, re-solve).

## facts

Solver-error decoding. Common causes: 'infeasible' = over-constrained bounds
or a CO2 cap too tight / capacities too small to meet load; 'dim_0' in a
linopy/xarray error = a time-series (_t) frame lost its index name
'snapshot'; "cannot include dtype 'M' in a buffer" = a multi-period → flat
demotion tripping a pandas MultiIndex reindex bug; an assign_duals KeyError
on a DatetimeIndex = a stale MultiIndex left on a dual _t frame after a
period change; a 500 with a short plain-text body from /results/* = NaN or
Inf leaked into JSON rendering. Explain the likely cause in plain terms and
suggest the corrective lever (loosen the bound, rebuild snapshots,
re-solve).

## chaining

On ANY failed or aborted run, call get_simulation_log_history BEFORE
answering and quote the failing TRACEBACK frame.
