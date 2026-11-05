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
- [x] An infeasible eh_site fixture: triage reads run, ≤2 changes are applied, one re-solve; ledger rows carry `rule` and `round`.
- [x] A third round is refused; the run is `needs_user` with the failing rules.
- [x] A profile with `native:run_margin_loop` never starts an agent turn for that rule.
- [x] The solve count in `run.budget.used` equals the solve-queue jobs of the run.
- [x] A scorecard row with `warn`, `unknown` or missing fails a `block` rule; an `assumed` pass shows "screening pass" in the gate record.
- [x] A family rule (`ride_through:*`, `motor_start:*`, …) passes only when every row of the family passes; zero rows → `not_evaluated`.
- [x] LOLE basis: on a multi-year horizon the h/yr goal is converted once to the engine loop's horizon-basis `target_lole_h`; one engine-loop call counts as one round.

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

## Plan verdicts (4 Nov, builder session_01G7qCunbwx8oa1iofP1qkqa)

Split: PR A (L2, solve budget, scorecard verdict rules, HH-06 cycle records), PR B (HH-10), PR C (engine loops). Plausibility stays out (Comment 4). Q3 confirmed: the user-facing read is K's `get_results(result_kind="plausibility")`, no new tool.

### GK B (19): GO WITH CHANGES

Q1: L2 goes into eh_site@1 in place (SH-04's L1 precedent, #1072), on condition C8. Q2: a capture solved after the inputs changed is not applied (C6f).

- **C1 SH-22 path.** L2 edits go through the generalised `run_loops` write_guard into `run_envelope.admitted_write`; the fail-closed default for `ATOMIC_TOOLS` stays. L1's run_loops tests pass unchanged after the generalisation, and the PR says so.
- **C2 max_changes.** Admitted calls are counted at envelope admission, under the envelope's lock, per round. A call refused at admission or pickup does not count. The 3rd is refused with a typed kind (tool-error-kinds.d fragment). A "verified change" for no_change is a receipt-settled link (`settle_own`), not a ledger row or tool_result.
- **C3 Triage.** Native triage outputs reach the model fenced as untrusted data and size-capped. A triage read that raises stops the run `needs_user` `triage_unreadable`, never a blind round.
- **C4 One solve counter.** `run.budget.used.solves` and `_standing`'s envelope check read one function, or a test pins that they agree. Engine-loop solves add to the same counter. Check-and-enqueue runs under the held locks (two enqueues at budget−1 never both pass; tested). L2 rounds count against `budget.rounds`. A failed or cancelled job counts as spent.
- **C5 Readiness reader.** A family prefix must be a known family (refused at load otherwise). A block rule reading not_evaluated (zero rows) never passes; the run goes `needs_user`.
- **C6 HH-10 capture.**
  - a. Temp dir, manifest written last, atomic rename. At pickup each file's sha256 is checked against the manifest, and the manifest's sha256 against the identity on the job row. A missing dir is `capture_mismatch`.
  - b. One function lists the sidecars, called by both the normal pre-solve path and the capture; a test shows that a sidecar the pre-solve reads is captured.
  - c. Test that `_network_input_fingerprint(exact=True)` of the hydrated private network equals the project's at enqueue.
  - d. `solving_context(private)` binds the transforms, the solve and the restore (#992); the PR names per setting any pre-solve step not reproduced (island apply, commercial PoC writer, user code).
  - e. The write-back compare runs under the project locks against the full recomputed identity (exact fingerprint + network_revision + sidecar hashes).
  - f. On `applied: false` the gate never reads the job's results as the project's; the run goes `needs_user` `inputs_changed`, the cycle record and evidence say so, and the solve counts as spent.
  - g. A capture is removed once its job is settled and the evidence recorded. A boot sweep removes orphans and honours the replica pin and lease (#1058). `.harness/captures` is excluded from project export, the fingerprint, sidecar listings and template byte-identity (tested).
  - h. The barrier test is red on master first.
- **C7 HH-06.** When the gate's last verdict is fail or not_evaluated, no path ends the run as success. The cap-reached, exhausted and no_change cases are each tested.
- **C8 (Q1).** An envelope granted before L2 existed does not silently gain L2's update_component scope: the recorded grant governs, and an L2 edit outside it is refused (tested). SH-22's eh_site@1 byte-identity pin is updated deliberately, and the PR body names it.
- **C9 PR C (GK A co-reviews).** A provider that fails if called shows that no agent turn starts for an engine-driven rule. max_solves = min(tunable, budget left), counted per C4. The multi-year LOLE conversion is pinned. Never nested: if only engine rules fail, no L2 round runs.
- **C10.** Each PR lists the conditions it closes. PR A waits for #1079 before touching run_loops or assistant_tasks.

### GK A (11): GO on item 4 (PR C)

- Horizon conversion: through `reliability_goal.loop_goal` (`metrics.horizon_years(n)`, fed by `active_loop_goal`, as `margin_loop_runner` already does), not eh_study's `inputs.nyears` or the snapshot span. Pinned by a 3-year test whose snapshot weights are not 1 h.
- Solve count from the result record, no engine change: `solves_used` + `probe_solves` + a `restore="final"` re-solve if one runs. A test shows `used.solves` includes the probe.
1. A round runs from the start call to the loop's terminal record (converged, not_converged, aborted, error). A cancelled run stops the loop through its stop path (tested).
2. `max_solves` = min(profile value, solves left), capped at `MAX_LOOP_SOLVES`; below 1 the loop is not started and the run goes `needs_user` (budget spent), never a 422 or a silent skip (tested).
3. `restore` defaults to "base". "final" (a solver-config write, LC-40) is allowed only when the profile's approval covers that write, otherwise refused (tested).
4. Not nested: an engine-driven rule is out of L2's failing set and starts no agent turn. A second engine rule in the same round waits for the first loop's terminal state; two loops never run at once on one ctx.
5. `adequacy.loop.converged` reads the loop's own result record. A missing record, not-converged result or goal-less target (`loop_goal` → None) fails the rule, `needs_user`. `goal_sources` is copied as served.
6. The native call goes through the chat_tools handler on the run's bound context, so the 422 set surfaces as a typed refusal; a fail-closed test at the catching site, with a mutant that catches the base class (#1020 rule).

### C6f split (4 Nov, builder session_01G7qCuio3L298TQBsceFqor; GK B accepted)

PR B (#1083) closes the gate side of C6f: a captured job is read as the project's only when `applied is True`, and `false`, missing or non-bool is no evidence. PR A carries the rest of C6f: on `applied: false` the run goes `needs_user` `inputs_changed`, the cycle record and evidence say so, and the solve counts as spent (tested there, with the HH-06 cycle records and the L2 budget).

## PR C plan verdicts (4 Nov, builder session_01G7qCunDhV74iECmHrF3cpp)

### GK A (12): GO (GK B (21) agrees)

Plan: an `engine` block on a result rule (fact `adequacy.loop.converged`); non-engine result rules first, engine rules out of L2's failing set and run one at a time by the harness (no agent turn); a follower settles the step from the loop's own record; the loop's solves charged to the run. eh_site@1 unchanged (option A).

- Q1 (a). The closing re-solve runs on every path (`_restore_closing`); charge +1 whenever it ran, read from `base_restore_status` being set, not from `restore`. An aborted loop's closing re-solve is charged too.
- Q2 (a). `restore: "final"` needs the granted envelope's `runs` to list the engine tool with `restore: "final"`; a profile block alone does not cover a solver-config write.
- Q3 (a). One terminal-set constant in `services/adequacy/coupling.py` (on master: met, budget_exhausted, unreachable, aborted, failed). The AST scan covers values written to a record's "status" and the settled `status` variable, fails on any literal outside the set, and keeps the per-iterate statuses ("no_report", "infeasible") out by an explicit allow-list. Converged is `status == "met"` only. `_STUDY_TERMINAL` stays or includes the constant; never a fork.
- Q4 (a). A failure in the final gate is needs_user with its failing rules; no L2 round, no engine rerun.
- 7. Exactly one settle: follow, recover and cancel settle by compare-and-set on the step's state; solves charged once (two-settle test).
- 8. Identity under `solver_state_lock` (#1038): follow and recover accept `_state[study]` only when its `started_at` and job handle equal the step's own; a stranger loop in the slot settles `lost`.
- 9. Reservation: max_solves + overhead reserved under the envelope lock at start, replaced by the actual at settle; `lost` keeps the full reservation; reservations never exceed `run.budget.solves`.
- 10. Cancel goes through the abort route's stop path; the test asserts the stop_event set and the step settled `aborted` with its closing solve charged.
- 11. A start refused because the ctx is busy (409) is `engine_refused`, needs_user; never waits or retries.
- 12. A loop_goal → None 422 at start is `engine_refused` with a detail naming the missing goal; the gate fact is NoEvidence.
- Mutants at review: overhead dropped from the clamp; MAX_LOOP_SOLVES cap dropped; probe_solves dropped; closing re-solve charged only for "final"; restore-final coverage check removed; `except Exception` at the call site; converged = in terminal set; raising provider swapped for a no-op; settle CAS removed; started_at identity check removed. The PR body names the test that kills each.

### GK B (21): harness conditions (part of the GO)

- H1 (#1088, #1070). Every task or ledger write from `follow_engine` and `_recover` goes under `runner._file_lock` and checks this process holds the live lease; a stale process writes nothing (tested after a takeover).
- H2 (#1058). The engine's deadline settles on its own timer; the boot sweep honours the replica pin and the lease.
- H3 (#1083, #1001). Each engine hold or tuned-value write goes through envelope admission and the chain head (`admitted_write` / `settle_own`), re-captures the record's revision and bumps the network revision; a user edit landing mid-loop settles not-applied and is never folded into the chain.
- H4 (#875). Admission refuses any `*project*` argument other than the run's own; the engine tool's args come only from the rule's `engine` block, never from model input (tested).
- H5. The clamp and count tests show solves_used, probe_solves and the closing re-solve.
- H6 (#1101 `_driving_rule`). Engine ledger rows name the engine rule as `rule`, with the engine's stage and loop; never `rule: null`.
- H7 (#992). An engine on a private copy binds `PyPSAService.solving_context` around the transforms, the solve and the restore.
