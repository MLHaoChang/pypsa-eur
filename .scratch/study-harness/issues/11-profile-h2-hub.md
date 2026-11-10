<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 11 — Harness profile: eh_h2_hub and eh_h2_hub_staged

Status: done (#1318)
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

### GK A (19), on blocker 1 (verbatim, relayed in the SH-11 (2) hand-off)

- Option A, conditions SH11-L1..L5.
  - L1: eh.adequacy.lole stays block with the same engine. On an H2 hub it reads a typed NoEvidence naming the cause, and the run stops at result_gate. Map the runner's "nothing to sample" refusal at the fact reader; no separate template pre-check.
  - L2: the refused loop charges no solves.
  - L3: tests on both templates: the run stops with that reason, never "passed" or "not_started". Plus a test that a done EH study whose MC LOLE meets the goal still doesn't pass (F9). Mutate the mapping to pass/warn and show it killed.
  - L4: file the engine issue. Done: .scratch/adequacy-loops/issues/01-loops-sample-link-outages.md.
  - L5: SH-11 may merge with acceptance 1 marked blocked-by that issue; the PR body says so; no template data changes.
  - GK A wants the plan and the PR head: a GK A review.

### GK B (38), on blocker 2 (verbatim, relayed in the SH-11 (2) hand-off)

- Option B. The hydrate fix goes in its own PR, SH-11 stacks on it, and GK B's successor reviews it. Conditions (1)–(5): build the full network, then swap under mutation_lock and the netCDF lock, and a failed import leaves the old network intact; match load_project/reset_network and list long-lived references in the PR body; keep network.name, user_ts and transient state, with tests; the red tests plus an end-to-end test; regression suites in separate runs under 590 s; a release fragment.
- Done as PR #1307 (claude/hydrate-fresh-network); SH-11 stacks on it.

### GK A (19), plan GO for the LOLE part @8177da1a (verbatim, 10 Nov)

- GO with L1–L5 as planned plus SH11-L6..L9.
  - L6 (one source for the refusal). The gate matches a copy of the runner's message. Define NOTHING_TO_SAMPLE once, in the adequacy layer (e.g. services/adequacy/coupling.py), use it at all three refusal sites (coupling_loop_runner:250, margin_loop_runner:223, eh_stages:1150), and have run_gates import it. The message text must stay byte-equal: run `git grep` for the old text. Also add one end-to-end test: on an H2 template, run the real start_engine → route refusal → step error path with no fabricated "error" string, and show the gate reads `loop_cannot_sample_link_outages`. Today's test at test_run_profile_h2_hub.py:419 fabricates the error, so a runner rewording would silently drop the typed reason.
  - L7 (specificity). Only that refusal maps. Add a test that a different engine refusal (e.g. a 409 or another 422 detail) keeps its own outcome/reason, and kill a mutant that maps every refusal to LOOP_CANNOT_SAMPLE.
  - L8 (`_gate_on_refusal` is contained). It runs inside the `except` after `_engine_stop`, so if a fact reader raises there, start_engine raises instead of returning "engine_refused". Wrap it: a gate-read failure leaves the stop as it is and records that the gate read failed. Test it with a raising reader: start_engine still returns "engine_refused", and the stage is failed with `engine_refused`.
  - L9 (no change for other profiles). It now runs on every engine refusal (eh_site@2, datacenter, any profile with an engine rule), and it bumps stage["attempts"]. (a) State in the PR whether a refused engine counting a result-gate attempt changes any attempts reader (caps, needs_user wording, continuation). If one changes, don't bump. (b) List the result-gate fact readers that this now calls on a refusal, and confirm each is read-only, with no solve, job or review compute. (c) Test, on a non-H2 engine profile, that a refusal leaves run status, stage status, failure reason and the ledger as master does; only the stored gate rows may differ.

### GK B (39), on Q3 (verbatim, 10 Nov)

- Confirmed A. Q3 ruled which electrolyser the staged hub may size: electrolyser_new_* only, never the built one. It did not remove the other levers, and keeping one envelope shape for both hubs is right.
  - Q3-A1: the staged envelope test covers each of the five levers: its bound, an over-bound refusal, and the built (non-_new_) asset of each class refused at _match_edit. Testing only the electrolyser is not enough.
  - Q3-A2: a test that the staged and single-year hubs' L2 envelopes have the same (component, field) set, so they cannot drift apart. Mutate it once by dropping one lever from the staged profile; the test must fail.

### Builder notes (SH-11 (2), 9 Nov)

- Blocker 2 (staged utilisation 1.23), root cause: `solve_queue._apply_capture` re-hydrates the resident context with `_hydrate_context_from_disk`, which imported the saved netCDF into the populated network. PyPSA skips every component the network already has (keeping its old static row) and takes the new series, so the live network held the solve's dispatch next to its pre-solve `p_nom_opt` (0 for `electrolyser_new_2035`, dispatching 11.1 MW). Fixed by importing into a fresh network (`tests/test_hydrate_replaces_resident_network.py`, red first); the reader now also fails closed (`dispatch_exceeds_capacity`) and reads only a current solve.
- H3: one helper, `services/results/asset_economics.link_capacity_factor`, used by asset_economics' Link rows and the utilisation reader.
- H7: the budget is 13, every reservation at its cap: the solve and its EH study (budget_solves 2), each of the 2 L2 rounds re-enters the solve stage (re-solve and EH study again), the coupling loop's 3 and its closing re-solve. Measured on both templates: the EH study reserves 2 and settles 1; the loop reserves 4.
- A2 (a): confirmed with the netCDF fixture that the amendment is still needed: on master's `continuation.py` the granted eh_h2_hub run settles solve.1 `uncertain`. A3: `_compute_run_objective` is not None on the staged hub (tested).
- A4: filed as [39](39-experiment-objective-constant.md).
- Follow-ups, out of scope: the owner's CBAM sensitivity and gas-vs-electric heat baseline (8 Nov); `eh_datacenter`'s missing stage `.md` files.
