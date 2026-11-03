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

- [ ] LC-20's done-when: `oj-grid` clean returns no warnings; `oj-grid-bad` with one seeded defect per rule returns each finding traced to a fact. LC-T19 extra-claim withholding for plausibility findings depends on the assurance follow-up "Checked plausibility findings" (`.scratch/assistant-assurance/issues/10-metric-calculation-and-validity.md`), not on Codex K (GK B amendment on #851).
- [ ] An infeasible eh_site fixture: triage reads run, ≤2 changes are applied, one re-solve; ledger rows carry `rule` and `round`.
- [ ] A third round is refused; the run is `needs_user` with the failing rules.
- [ ] A profile with `native:run_margin_loop` never starts an agent turn for that rule.
- [ ] The solve count in `run.budget.used` equals the solve-queue jobs of the run.
- [ ] A scorecard row with `warn`, `unknown` or missing fails a `block` rule; an `assumed` pass shows "screening pass" in the gate record.
- [ ] A family rule (`ride_through:*`, `motor_start:*`, …) passes only when every row of the family passes; zero rows → `not_evaluated`.
- [ ] LOLE basis: on a multi-year horizon the h/yr goal is converted once to the engine loop's horizon-basis `target_lole_h`; one engine-loop call counts as one round.

## Likely files

`pypsa-gui/backend/services/run_gates.py` (the `plaus.*` adapters only), `pypsa-gui/backend/harness/runner.py`, new tests for the adapters. The rule set lives in `pypsa-gui/backend/services/results/plausibility.py`, owned by Codex K (#851); this issue must not create `services/results_plausibility.py`. The user-facing read is K's `get_results(result_kind="plausibility")` and `GET /api/results/plausibility`, with no new tool (GK B plan verdict on #851, TOOLSEL-1).

## Comments

## Folded from #970

Owner decision HARNESS-970-1 (3 Nov). HH-06 (bounded solve → check → correct cycles) and HH-10 (immutable queued solves) fold in here, on `eh_site@1` (SH-PILOT-1), not as #970's new `capacity_isolated@1` profile. They add nothing to this issue's acceptance criteria above; they are the hardening to build once the loop works.

1. **Bounded cycles (HH-06).** The loop's limits are the existing ones (`run.budget` rounds, solves, the profile's tunables); HH-06 adds that each cycle is a recorded record: the solve job, the gate's native findings, the proposed change (a card or an in-envelope edit), and the next solve. The run ends `needs_user` with the question unanswered rather than looping or declaring success when the gate cannot pass inside the caps. #970's cap of three solves per lineage is a proposal; keep this issue's existing round and solve caps.
2. **Immutable queued solve (HH-10).** The solve step's `solve_queue_enqueue` should solve the exact input the gate checked: capture the input at enqueue (network, snapshots, solver config), solve that capture on an isolated copy, and record its identity in the evidence so a user edit made while the job waits can neither change nor be mistaken for the result. Today `settle_job` / the queue's freshness fact (`run_gates`) detect a stale result after the fact; HH-10 prevents the wrong solve. This needs issue 22's compare-and-write for the enqueue step (the identity compared is the identity captured).
3. **What changes for eh_site@1.** #970's HH-10 admits only `Generator.p_nom` / `marginal_cost` corrections on a template-independent network and refuses an active `eh_template.json` or grid-readiness sidecar. eh_site@1's corrections are `Generator.p_nom_max`, `StorageUnit.p_nom_max` and `StorageUnit.max_hours` (`run_profiles.py:186-206`) on a project that does have the template sidecar. GK B ruled on 3 Nov: the captured solve input includes the template sidecars byte for byte, with their sha256 in the captured identity; the capture is solved on a private context under `solving_context` through `build_lp_chain`, with the template's pre-solve steps reproduced. An active template is never a reason to refuse. A sidecar write between enqueue and pickup is either captured or refused. The first code PR for this section brings its plan to GK B. Do not narrow eh_site@1 to fit HH-10's scope (SH-ENVELOPE-1).
4. **Plausibility.** The result gate's plausibility facts come from outsourced K (#851, draft; `services/results/plausibility.py`, absent on master `a56cf0b06`). Block the plausibility part of this folded work on K merging; never copy K's thresholds into the harness or add a competing module. Optimal solver status alone proves no reliability or adequacy claim.
5. **Native loops.** AS-06's sizing loop (#991, #992) and the margin and coupling loops are native study engines, not a second agent loop. If a profile registers one, the agent does not wrap another loop around it. #970's source map predates AS-06 and AS-10; recheck against master.

Test first (HH-10's barrier): an external edit lands between the gate pass and the job's pickup; the solve still uses the captured input and the evidence says so.
