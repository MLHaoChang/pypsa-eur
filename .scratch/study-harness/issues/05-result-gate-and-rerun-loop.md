<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 05 — Result gate (LC-20 rules) and the bounded fix-and-rerun loop L2

Status: ready-for-agent
Type: task
Blocked by: 04
Gated: yes (GK B; GK A on the engine-fact readers)
Model tier: opus
Owner decisions: SH-FOLD-1, AI-558, TOOLSEL-1, LC-20 Q5 = A
Absorbs: LC-20 (`.scratch/live-copilot/issues/20-results-plausibility-check.md`): its rule set becomes the `plaus.*` fact source
Design: [design.md](../design.md) §5, §6.2

## What to build

1. LC-20's native rule set (its Scope 1 list, each rule with id, facts, threshold and basis, severity, explanation template) as `plaus.*` facts in `services/run_gates.py`, plus `kpi.*` readers over the LC-03 adapters.
2. The user-facing read: GK B picks a `checks` argument on an existing results read (TOOLSEL-1) or LC-20's one new read tool; the same rule set serves both.
3. L2: trigger = failed `block` result rule or non-optimal solve; triage reads first; ≤2 rounds per case, ≤2 changes per round; each round consumes a solve; no re-solve without a change (SC-14).
4. Engine loops (GK A): where the result gate is reliability, `run_coupling_loop` (ENS cap, energy lever) or `run_margin_loop` (reserve margin, firm-capacity lever) replaces the agent loop; never nested; the loop's converged/not-converged result and `goal_sources` are the gate fact; its `max_solves` is bounded by the profile and counted against the solve budget and the ≤2 rounds.
6. Scorecard-row rules gate by verdict (only `pass`); the `assumed` flag is carried, and a pass on assumed inputs is reported as a screening pass.
5. Solve budget enforced; exhaustion → `needs_user`.

## Acceptance criteria

- [ ] LC-20's done-when: `oj-grid` clean returns no warnings; `oj-grid-bad` with one seeded defect per rule returns each finding traced to a fact; LC-T19 extra-claim injection is withheld.
- [ ] An infeasible eh_site fixture: triage reads run, ≤2 changes are applied, one re-solve; ledger rows carry `rule` and `round`.
- [ ] A third round is refused; the run is `needs_user` with the failing rules.
- [ ] A profile with `native:run_margin_loop` never starts an agent turn for that rule.
- [ ] The solve count in `run.budget.used` equals the solve-queue jobs of the run.
- [ ] A scorecard row with `warn`, `unknown` or missing fails a `block` rule; an `assumed` pass shows "screening pass" in the gate record.

## Likely files

`pypsa-gui/backend/services/run_gates.py`, `pypsa-gui/backend/services/results_plausibility.py` (new), `pypsa-gui/backend/harness/runner.py`, `pypsa-gui/backend/harness/catalogue.py` (only if GK B picks the argument route), `pypsa-gui/backend/tests/test_results_plausibility.py` (new)

## Comments
