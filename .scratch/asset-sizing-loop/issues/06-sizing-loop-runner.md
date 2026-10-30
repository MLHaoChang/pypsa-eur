<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# AS-06 — Sizing loop runner

Status: ready-for-agent
Type: task
Blocked by: 03, 04, 05
Model: Opus
Reviewer: GK A + GK B

Spec: [spec.md](../spec.md). Source: research note section 5, [RESULTS.md](../../datacenter-grid-readiness/research/prototype-as-loop/RESULTS.md).

## Scope

Add a `*_loop_runner` in the `ADQ/coupling.py` pattern: solve, forward pass on fixed capacities (`campus_flow.size_transformers`, `campus_reactive`, `strength.escr`, `flicker`, `grid_readiness` rows, `gfm_tuning`), cuts table (note 4.4 with the AS-03/04/05 revisions), convergence rule. Cuts are never removed; library assets round up and freeze after iteration 2; stop at 5 iterations. Revised stopping rule: converged when every check passes or no new cut is generated; a tolerance stop (cost change < 0.5 %, capacities < 2 %) with any check still failing is reported "not converged", never converged. Failures with no lever report "unresolved". Harmonics report a filter cost, not a cut.

## Acceptance

- [ ] Tests first: cost is non-decreasing across rounds; cuts accumulate.
- [ ] The weak-flat intent D prototype case passes in 2 rounds (note cuts needed 9).
- [ ] A synthetic run with cost moving < 0.02 % per round while Pst is 6x over returns "not converged".
- [ ] A check with no lever (SCR set by grid alone) stops with "unresolved, needs grid reinforcement or EMT study".
- [ ] Transformer rounds up and is frozen after round 2; it cannot flip sizes.
- [ ] Full-year uses a tsam-reduced year for inner rounds and a full-year final solve; run time recorded.

## PRs

## Comments
