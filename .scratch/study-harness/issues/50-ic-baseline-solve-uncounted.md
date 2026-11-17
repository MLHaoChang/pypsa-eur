<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 50 — The investment case's baseline re-solve is not counted as a solve

Status: ready-for-agent
Gated: GK A (solve count), GK B co-review (harness and finance)
Type: bug
Parent: SH 16 (eh_concentrator_staged@1), GK A (28) Q7 = C

## Problem

`harness/run_ic.py:25` settles the investment-case step with `SOLVES = 0` ("the case solves no LP", SH-14), and
`run_investment_case` is not in `services/run_envelope.RUN_TOOLS`. A case whose owner keeps existing assets
re-solves the network without the plan's new builds (`services/results/baseline.py`, `solve_baseline`: one
`run_simulation` of the whole multi-period network, cached by key, `_CACHE_MAX` 4). That LP is never counted, so
`run_envelope.solves_used` and `run.budget.used.solves` undercount by one per case run on a cache miss.

Affected profiles (every profile whose solve stage runs `run_investment_case`): `eh_ipp_hybrid@1` (no baseline
on the shipped template: no kept existing asset) and `eh_concentrator_staged@1` (the site as it stands today:
one baseline solve per case run). `eh_ammonia_staged@1` does not run the case.

## Ask (option A of SH 16 Q7)

- `solve_baseline` reports whether it solved (cache hit or miss); the case's record carries `baseline_solves`
  (0 or 1).
- `run_ic` settles the step's `solves` from the record, and the case step books a reserving audit row
  (reserve 1 at admission, settle the actual), so `solves_used` counts it.
- The concentrator profile's budget then counts the baseline: 1 plan + 1 baseline, + 2 per L2 round; drop the
  "+1 baseline solve … not counted" words from its `missing_tools` and README.

## Test

- `solves_used` equals the real LP count on a cache miss (plan + baseline) and on a hit (plan only).
- Red→green anchor: `tests/test_run_profile_concentrator_runs.py` pins `solves_used == 1` beside a spy that
  shows `solve_baseline` solved once; the fix turns that into `== 2`.
