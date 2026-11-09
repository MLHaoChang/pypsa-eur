<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 11 — Harness profile: eh_h2_hub and eh_h2_hub_staged

Status: ready-for-agent
Type: task
Blocked by: 07
Gated: yes (GK B; GK A for the IC counterfactual rule's engine dependency)
Model tier: opus
Owner decisions: SH-PILOT-1
Design: [design.md](../design.md) §9.3 "eh_h2_hub"

## What to build

1. Profiles `eh_h2_hub@1` and `eh_h2_hub_staged@1`: scope `_H2_HUB`, archetype `strong_grid`.
2. Facts: H2 offtake served per hour, electrolyser utilisation (a new reader over the solved network; none exists today), LOLE with the fuel cell as firm backup.
3. Investment rules `not_evaluated` with the reason "IC counterfactual not established" until [issue 19](19-h2-offtake-ic-counterfactual.md) lands its `ic.counterfactual` fact (GK A). The engine already has the counterfactual (H2e, #312: `pypsa-gui/backend/services/results/finance_case.py:1467`, `services/results/baseline.py:25`); issue 19 adds the single-year hub's reference-purchase data. The report lists it as a gap until then. Offtake is demand and never tunable.
4. Tunables: wind/PV/BESS candidate max, electrolyser and H2 storage size within bounds.

## Acceptance criteria

- [ ] A run on each template reaches hand-off; offtake and LOLE rules pass on the fixture.
- [ ] A seeded undersized electrolyser fails the offtake rule; L2 sizes it within bounds in ≤2 rounds.
- [ ] Investment rules gate on `ic.counterfactual` (issue 19, `services/ic_counterfactual.py`; `warn`): evaluated once it reads `established` (both hubs ship the grey-H2 reference purchase), `not_evaluated` with the fact's reasons while it reads `not_established` or `not_run`; the run is never blocked by them.

## Likely files

`pypsa-gui/backend/services/run_profiles.py`, `pypsa-gui/backend/services/run_gates.py`, `pypsa-gui/backend/harness/workflows/profiles/eh_h2_hub/`, `pypsa-gui/backend/tests/test_run_profile_h2_hub.py` (new)

## Comments

### GK B (38), plan verdicts (verbatim, relayed in the SH-11 hand-off)

- Plan GO with conditions. Q1 = A: band [0.40, 0.95], warn, a screening band ("sizing screen, not a design limit"), read from the frozen profile. Q2 = A: read the stored case only; running run_investment_case is a follow-up shared with 10b. Q3 = A: staged L2 tunes only electrolyser_new_* p_nom_max. No GK A needed while no engine code changes.
- Conditions H1–H10:
  - H1: acceptance 2 shows shed_hours > 0 from a real solve. NoEvidence for an infeasible solve, a missing capture, a stale fingerprint or another solve. A missing, several or renamed offtake load each gets a test.
  - H2: generator weightings; per-period worst; a test with unequal period weights.
  - H3: utilisation reuses the asset_economics capacity-factor code or a shared helper, not a copy.
  - H4: each envelope attr is the active one (p_nom for fixed, p_nom_max for extendable); per template, a bounded edit changes the solve; above the bound is refused out_of_bounds.
  - H5: tests at _match_edit: fuel_cell, grid_import and the staged existing electrolyser give outside_entry; the offtake p_set is refused never-tunable.
  - H6: loop_goal with metrics.horizon_years.
  - H7: a budget arithmetic test (1 + 2 L2 + loop 3 + restore + EH study); raise 8 if needed; exhaustion stops with its typed reason.
  - H8: ic.counterfactual is warn; assert gate.passed in all three states.
  - H9: mutants.py plus the named hand mutants.
  - H10: at the trial merge, inventory and control-matrix --check, test_harness_l2_loop, test_harness_run_eh, and the existing profiles byte-unchanged.
- Amendment GO (the continuation._queue_run objective compare), conditions A1–A4:
  - A1: if cfg is missing or the function returns None → unverified, with a test.
  - A2: (a) a granted eh_h2_hub run settles solve.1 completed; (b) a manifest objective off by 1.0 stays unverified, and kills the drop-compare mutant; (c) eh_site unchanged.
  - A3: check what _compute_run_objective returns for the staged mode. If it is None, stop and send GK B the finding.
  - A4: leave experiment_execution.py:211 out of this PR and file a .scratch ticket with the repro numbers (manifest 506,529,579.17 vs saved 505,116,197.62; diff = objective_constant 1,413,381.55).

### Builder notes (SH-11 (2), 9 Nov)

- Blocker 2 (staged utilisation 1.23), root cause: `solve_queue._apply_capture` re-hydrates the resident context with `_hydrate_context_from_disk`, which imported the saved netCDF into the populated network. PyPSA skips every component the network already has (keeping its old static row) and takes the new series, so the live network held the solve's dispatch next to its pre-solve `p_nom_opt` (0 for `electrolyser_new_2035`, dispatching 11.1 MW). Fixed by importing into a fresh network (`tests/test_hydrate_replaces_resident_network.py`, red first); the reader now also fails closed (`dispatch_exceeds_capacity`) and reads only a current solve.
- H3: one helper, `services/results/asset_economics.link_capacity_factor`, used by asset_economics' Link rows and the utilisation reader.
- H7: the budget is 13, every reservation at its cap: the solve and its EH study (budget_solves 2), each of the 2 L2 rounds re-enters the solve stage (re-solve and EH study again), the coupling loop's 3 and its closing re-solve. Measured on both templates: the EH study reserves 2 and settles 1; the loop reserves 4.
- A2 (a): confirmed with the netCDF fixture that the amendment is still needed: on master's `continuation.py` the granted eh_h2_hub run settles solve.1 `uncertain`. A3: `_compute_run_objective` is not None on the staged hub (tested).
- A4: filed as [39](39-experiment-objective-constant.md).
- Follow-ups, out of scope: the owner's CBAM sensitivity and gas-vs-electric heat baseline (8 Nov); `eh_datacenter`'s missing stage `.md` files.
