<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 52 — A run's investment case cannot re-solve its baseline

Status: ready-for-agent
Gated: GK A (solve count), GK B co-review (harness and finance)
Type: task
Parent: SH 16 (eh_concentrator_staged@1), GK A (28) Q7, Q8

## Problem

A case whose owner keeps existing assets values the plan against the site as it stands today: a baseline
re-solve of the network without the plan's new builds (`services/results/baseline.py`, `solve_baseline`: one
`run_simulation` of the whole multi-period network, cached by key, `_CACHE_MAX` 4). Inside a harness run that
case is refused twice, because the run's budget does not count that LP:

- `services/run_envelope.py:1458-1466`: the envelope refuses `run_investment_case` with `baseline_solve`
  (GK B (53) B1), so the step goes to the user's card;
- `routers/results.py:2173-2177`: approving that card does not help: `_ic_build_case` raises
  `FinanceRefused("baseline_solve")` whenever the case runs in a run and the plan needs a baseline (GK B F1,
  pinned in `tests/test_run_profile_ipp.py:971-980`).

`harness/run_ic.py:25` also settles the case step with `SOLVES = 0`.

So `eh_concentrator_staged@1` cannot establish its counterfactual (the site as today) in a run: its solve stage
does not run the case, and its `conc.ic.counterfactual`, `conc.ic.basis` and `conc.kpi.npv` rules warn and
read a stored case the user ran outside the run (not evaluated otherwise). `eh_ipp_hybrid@1` runs the case
but its shipped template keeps no existing asset, so it needs no baseline.

## Ask (a counted reservation)

- `solve_baseline` reports whether it solved (cache hit or miss); the case's record carries `baseline_solves`
  (0 or 1).
- The case step books a reserving audit row at admission (reserve 1: the baseline LP), and `run_ic` settles
  its `solves` from the record, so `run_envelope.solves_used` counts the baseline on a miss and not on a hit.
- With the reservation in place, the envelope admits the case (`baseline_solve` only when the solves left do
  not cover it) and `_ic_build_case` solves the baseline in a run whose reservation covers it.
- The concentrator profile then runs `run_investment_case` after the queued solve again, its budget counts
  1 plan + 1 baseline (+ 2 per L2 round), and `conc.ic.counterfactual` and `conc.ic.basis` go back to
  `block`; drop the issue-52 line from its `missing_tools` and README.

## Test

- `solves_used` equals the real LP count on a cache miss (plan + baseline) and on a hit (plan only).
- A run whose solves left cannot cover the baseline is refused `baseline_solve`, nothing solved.
- `tests/test_run_profile_concentrator_runs.py`: the demo run reaches the hand-off with the case
  established against `site_today` and both case rules `block` and passing.
