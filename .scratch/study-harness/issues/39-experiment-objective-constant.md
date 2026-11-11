<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 39 — The experiment refresh compares the queue's total, not the bare LP objective

Status: done (#1334)
Type: bug
Gated: yes (GK B)
Model tier: sonnet
Parent: [11-profile-h2-hub.md](11-profile-h2-hub.md) (GK B (38) A4)

## What

`harness/experiment_execution.py:211` compares the queued job's manifest objective with `saved.network.objective`. The manifest stores the queue's own total (`routers.simulation._compute_run_objective`: the LP objective plus `objective_constant`, which carries the built assets' CAPEX). On a network with a non-zero constant the two never agree, so a completed experiment child reads `experiment_result_mismatch`.

Repro (eh_h2_hub, SH-11): manifest 506,529,579.17 vs saved 505,116,197.62; the difference is the objective constant, 1,413,381.55.

SH-11 fixed the same compare in `harness/continuation._queue_run` (recompute `_compute_run_objective` on the saved network with its saved config; no config or no total is no evidence).

## Acceptance

- The refresh recomputes the total on the saved network with the child's saved solver config and compares it exactly; a missing config or a None total is `experiment_result_mismatch`, never a match.
- A test on a network with a non-zero objective constant (eh_h2_hub) that reads unavailable today and available after the fix; a manifest objective off by 1.0 stays a mismatch (kills the drop-compare mutant).
