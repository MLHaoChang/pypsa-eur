<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# AS-06 — Sizing loop runner

Status: done
Type: task
Blocked by: 03, 04, 05
Model: Opus
Reviewer: GK A + GK B

Spec: [spec.md](../spec.md). Source: research note section 5, [RESULTS.md](../../datacenter-grid-readiness/research/prototype-as-loop/RESULTS.md).

## Scope

Add a `*_loop_runner` in the `ADQ/coupling.py` pattern: solve, forward pass on fixed capacities (`campus_flow.size_transformers`, `campus_reactive`, `strength.escr`, `flicker`, `grid_readiness` rows, `gfm_tuning`), cuts table (note 4.4 with the AS-03/04/05 revisions), convergence rule. Cuts are never removed; library assets round up and freeze after iteration 2; stop at 5 iterations. Revised stopping rule: converged when every check passes or no new cut is generated; a tolerance stop (cost change < 0.5 %, capacities < 2 %) with any check still failing is reported "not converged", never converged. Failures with no lever report "unresolved". Harmonics report a filter cost, not a cut.

## Acceptance

- [x] Tests first: cost is non-decreasing across rounds; cuts accumulate.
- [x] The weak-flat intent D prototype case passes in 2 rounds (note cuts needed 9).
- [x] A synthetic run with cost moving < 0.02 % per round while Pst is 6x over returns "not converged".
- [x] A check with no lever (SCR set by grid alone) stops with "unresolved, needs grid reinforcement or EMT study".
- [x] Transformer rounds up and is frozen after round 2; it cannot flip sizes.
- [x] Full-year uses a tsam-reduced year for inner rounds and a full-year final solve; run time recorded.

## PRs

## Comments

### GK A plan verdict (2026-10-31): GO for both PRs, with conditions

Answers: (a) the PQ margin is an optional `pq_q_margin_mvar` Link column read by pcs_statcom's pq row, fingerprinted only when present, with a pin test that existing fingerprints stay byte-identical; (b) scalar forward checks with optional campus adapters; a skipped adapter is named, and the verdict is then "converged (campus checks not run: …)", never a plain converged.

PR 1 (engine):
1. Converged needs every check passing (a harmonics filter cost excepted). A failing check that yields no new or tighter cut while its lever is not "none" is not_converged and names the check.
2. Monotone cuts, a direction per key: import_cap_mw and pst_rhs_mva only fall; the floors, gfm_mva_min and pq_margin_mvar only rise. A looser proposal is ignored (tested).
3. The import cap bounds the import flow p(t) at the PoC, not the transformer p_nom, so it cannot contradict a frozen transformer.
4. Pst rhs = d_lim · S_sc,min, S_sc,min = S_k''/c (IEC 60909 c factor), reusing flicker_inputs/gridspine; the shortfall tightening is a docstring formula with a row test.
5. Every sizing_cuts LP input enters `_network_input_fingerprint` when present and leaves it byte-identical when absent (normal and exact mode); inert controller settings never enter it.
6. The tolerance stop compares cost including the filter cost, consistently with the reported cost.

PR 2 (runner):
7. A run on a copy writes nothing of the open project's state (capacity_run record, caches, stashes, solve status, manifests, hourly audit); the active network and its results are byte-equal after a loop. AS-10 applies the result.
8. The forward pass runs again on the full-year final solve; the verdict is the full-year one ("reduced-year rounds passed, full year failed" is not_converged).
9. AS-07's sheet is built before n.model is dropped; spectrum and control_settings are called only through their public functions.
10. gridspine imports stay inside functions or behind the import guard; tests/test_packaging_requirements.py passes at the trial merge.
11. stop_event aborts between rounds and inside long solves; abort after round 1 is tested.
12. The slow acceptance case is marked slow; a fast fake-solve test covers the runner wiring.

### Builder note (PR 2): the runner's solve path

The runner solves each round with `n.optimize` on a private copy, not through `run_simulation`. `run_simulation` publishes a sizing record, a DSR commit, commercial terms and solve state for the open project (condition 7). The LP is still the project's: `solver_service.build_lp_chain` is the one extra_functionality chain both use (user code, curtailment cost, capex budget, ENS cap, import-energy cap, reserve margin, island, the AS rows, commercial bindings, objective scale), and the copy gets the energization schedule and the modelling assumptions first. Settings whose pre-solve step the copy does not reproduce are refused by name: island constraints, commercial bindings, Capacity > sizing off. A rolling, myopic or SCLOPF project is named in `basis_note`: the loop sizes one full-horizon LP. A stop request lands between rounds; one LP is not interrupted (accepted by GK A).
