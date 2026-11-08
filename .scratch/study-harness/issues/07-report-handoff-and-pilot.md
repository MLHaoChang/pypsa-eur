<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 07 — Report and hand-off stages; eh_site pilot end-to-end

Status: done (#1257)
Type: task
Blocked by: 05, 06
Gated: yes (GK B)
Model tier: opus
Owner decisions: SH-PILOT-1, SH-FOLD-1, LC-21 Q6 = A, AI-558
Absorbs: LC-21 (`.scratch/live-copilot/issues/21-network-study-report.md`) as stage 7; LC-23 slice 4 (hand-off close)
Design: [design.md](../design.md) §3 stages 7–8, §9.3 eh_site

## What to build

1. LC-21's network-study chapters in the existing evidence pipeline (its option A), plus the **harness chapter**: gates per stage, change ledger, source ledger, envelope grant and refusals, gaps.
2. Narrative from admitted facts only (≤12 per explanation); unsupported chapters listed as gaps (SC-15).
3. Hand-off: native KPI read-back from `profile.kpis`, the resume point, the notice, and "Revert the run's changes" as one LC-16 preview.
4. The full `eh_site@1` profile (design §9.3) and the pilot acceptance run.

## Acceptance criteria

- [ ] **Pilot**: from one prompt, an eh_site run in envelope mode goes intake → hand-off with the panel closed after approval, on the test fixture with the fake provider; the report has the harness chapter; the user gets one completion notice.
- [ ] The same run in step-by-step mode reaches the same hand-off with one card per write/run (COP-1).
- [ ] `POST /reports/generate` no longer answers `no_evidence` for a solved network-only project (LC-21).
- [ ] Every number in the narrative passes the number audit; an injected extra claim is withheld.
- [ ] Revert preview lists exactly the ledger rows.

## Likely files

`pypsa-gui/backend/services/reports/chapters.py`, `pypsa-gui/backend/services/reports/evidence.py`, `pypsa-gui/backend/harness/runner.py`, `pypsa-gui/backend/services/run_profiles.py`, `pypsa-gui/backend/smoke/` (one pilot smoke), `pypsa-gui/backend/tests/test_harness_run_pilot.py` (new)

## Comments

### GK B (24): plan verdict, GO WITH CHANGES (C1–C14, verbatim)

Split: PR A = 1a–1d + C4, C5, C6, C9, C10; PR B after #1136 = C1–C3, C7, C8, C11–C14.

C1. run_eh_study. It is in run_envelope.NEVER_LISTED: an EH study is always the user's own card, in envelope mode too (GK B (8) ruling; it applies a pack to the project). So:
- the pilot counts it as one card in envelope mode, not "no panel";
- a test shows that a grant listing it is refused;
- its arguments (archetype, stages, budget_solves, pack_overrides, dtc_*, dsr_buses, mc) come from the profile and the project's reliability goal, fixed at the grant, never from model output (#1072). A test shows a model-supplied pack_overrides never reaches the call; the card shows the values;
- any project argument that is not the run's own project is refused (#875).

C2. Solve accounting. The EH study's solves count against the envelope's solves cap: its budget_solves ≤ the remaining solves, with a test. The LOLE rule goes through reliability_goal.loop_goal with metrics.horizon_years(n); its solve count is solves_used + probe_solves + any restore-final re-solve (SH-05).

C3. Gates fail closed. For every new run_gates fact (eh.readiness.ready, eh.review.blockers, energization_mw, lole, kpi.unserved_mwh, report.status, report.unsupported_listed, handoff.kpis_read), an unreadable source, a missing report record or a truncated card fails the gate and never passes it (#1079). One test per fact.

C4. Report narrative sends. Each LLM send of the run's report job is reserved through send_reservation.admit under the run's lease before the provider call. Charging on settle is not enough: it lets the run overspend its cap. A refusal (no_cap, over budget, lease_lost) settles the step with its kind and makes no provider call; test it with a counting fake provider. Addendum: after an admitted job, the run's ledger holds no open reservation. That covers a job that finishes, one refused mid-way and one where the provider raises; each reservation settles or is released.

C5. Number withholding fails closed. A paragraph whose number audit errors or cannot parse is withheld too; a mutant that makes the audit raise must be killed. The Reports panel's human path stays byte-identical to master's: copy master's test into the PR and run it there.

C6. The bound context. Add the report job to BOUND_CONTEXT_TOOLS and to the sweep test. Add a test where another project is active while the run's report runs: it reads the run's project. Addendum: generate_report has no project argument, so the project comes from the bound context only. Keep a test that a route call made with no bound context and another project in the foreground still reads the run's project, or is refused. It must never read the foreground project.

C7. Revert. Keep the exact-set test (preview rows ∪ "by hand" rows == the ledger's non-reverted rows). Also test:
- explore-fork rows excluded;
- no apply_project_changes ever in the run's steps;
- the preview refused as stale when the project changed after the hand-off. Name the LC-16 check you rely on.

C8. eh_site@1 in place. Resolve reads the profile by id, so in-flight runs pick up the new stages. Test that a stored run record from before the change, sitting at solve with no report or handoff stage, resolves without error and goes to the new stages with their gates. It must never pass them on missing data.

C9. Network chapters. A missing source gives not_established plus a hint, never 0 or a made-up value. A project-scoped read carries project_id = the run's project, and a 404/409 is no_data. One unsolved-network test per chapter. The load_flow proxy disclosure is required, with a test that it is present.

C10. Route 1b. Unsolved still returns 400 no_evidence; add route tests. New error kinds go only in a tool-error-kinds.d fragment. At the trial merge I will run the inventory, route-parity and control-matrix checks, plus the route ACL, disposition and write-lock tests if a route changes.

C11. The EH stub, if needed. Patch the service function with autospec, so the real signature is enforced, and run it real in smoke/accept_harness_pilot.py. The PR says which job runs that smoke.

C12. handoff.notified as a test post-condition: agreed. The test calls _notify twice after completion and asserts exactly one notice and one email.

C13. HH-09A coverage. "covered" only from a receipt or a passing gate. No receipt, or an unreadable one, gives "gap", never "covered". A mutant that defaults to covered must be killed.

C14. KPI read-back. Value, unit and source come from the native reader only; no number from model text enters run.handoff.

Stage-7 run link (GK B ruling): the route never detects a run. Only the harness passes the run binding (task path, lease owner), explicitly, when it launches the stage-7 job. A human request through the route or the Reports panel is never strict and never charged to a run's budget, even while a run sits at its report stage. Test (PR B): a route call while a run is at stage 7 → non-strict, no reservation on the run's ledger.

### GK B (25): PR B split, GO (B1 → B2 → B3, one PR open at a time)

C1–C3, C7, C8 and C11–C14 carry over. B1 = stages 7/8 in place; B2 = the EH input/result rules and run_eh_study (C1, C2); B3 = the pilot and its smoke (C11). Conditions on B1 (verbatim in substance):

- B1-a (C8 sync). A stage becomes pending only when it is missing, or was stored "skipped" because the profile lacked it; a stage the user or a gate skipped stays skipped. The sync records why a stage was skipped (or tells the two cases apart); test both.
- B1-b (old grants). An in-flight run whose envelope was granted before stages 7/8 existed must not have cross() take generate_report on that grant: stage 7 pauses at its card unless the stored envelope explicitly lists it. Test with a pre-change run record in envelope mode.
- B1-c (stage-7 launch). The step settles from the job record. A missing or unreadable record, or a job that ends refused, fails the step and never completes it (C3/#1079). Keep the C4 reservation addendum on this path: after the job the run's ledger holds no open reservation, whether the job finished, was refused or raised.
- B1-d (hand-off). Neither run_handoff nor run_report ever calls apply_project_changes; assert it with a spy over both steps. preview_stale is the C7 stale check, named in the PR.
- B1-e (ReservingProvider.close). Fold it in only if #1154 and #1150 don't touch send_reservation.py at the trial merge; otherwise its own follow-up.

### GK B (31): B2 plan verdict, GO WITH CHANGES (verbatim, restated 2026-11-06 22:28Z)

GK B (26)'s E1–E9 were unrecoverable; this verdict replaces them (coordinator ruling, option B).

> GK B (31), SH-07 B2: binding plan verdict, restated per the coordinator's ruling. GO WITH CHANGES. This list replaces the missing E1–E9 and my earlier message; record it verbatim in issue 07's ## Comments.
>
> Scope (GK B (25) split): B2 covers the EH input and result rules and run_eh_study. B3 keeps the pilot and its smoke (C11). B1's C12–C14 are done and B2 must not regress them.
>
> From issue 07 (GK B (24)), as they apply to B2:
> - C1. run_eh_study stays in NEVER_LISTED.
>   - Envelope mode shows it as one card.
>   - Test that a grant listing it is refused.
>   - Its args come from the profile and the goal, fixed at the grant (stepwise: at solve-stage entry, Q2 A), never from model output (#1072). A model-supplied pack_overrides never reaches the call (autospec spy, from an L2 turn and from a tampered step).
>   - The card shows the args.
>   - It runs only on the bound run project, never the foreground (#875). Join BOUND_CONTEXT_TOOLS and the §6.6 sweep test.
> - C2. Solve accounting.
>   - budget_solves ≤ the solves left; zero left gives needs_user eh_budget and no call.
>   - The LOLE rule goes through loop_goal(horizon_years(n)).
>   - The engine's solves = solves_used + probe_solves + the closing re-solve whenever it ran.
> - C3. Each new fact fails closed, one test per fact: eh.readiness.ready, eh.energization.min_mw, eh.study.status, eh.review.blockers, kpi.unserved_mwh, the eh_site loop fact. An unreadable source, a missing record or a truncated card never passes (#1079).
> - C4 addendum. After the run_eh_study step, the run's ledger holds no open reservation. Cover done, failed, refused (409/422/campaign) and a raise.
> - C7. The revert exact-set test stays green. The step adds no ledger rows (F10).
> - C8. eh_site@1 changes in place. Tests:
>   - an old run already at solve, with no run_eh_study step, reads not_evaluated → needs_user, never passed;
>   - an old run before solve materialises the new step.
> - C10. New kinds go only in your tool-error-kinds.d fragment. I'll run the trial-merge checks (kinds manifest, inventory, and route parity/control matrix if routes or tools change).
>
> New, from master's code (F):
> - F1. Freeze the goal fully. eh_request_with_goal fills any omitted archetype, target_lole_h, ens_cap_permyriad (plus certification_metric) and mc.draws from the LIVE goal at call time.
>   - The frozen args carry all of them resolved, covered by the digest and shown on the card.
>   - Test: edit the goal after the freeze, then dispatch. Either the record's goal_sources read override with the frozen values, or the step stops needs_user eh_args.
> - F2. Status mapping. The record says done, failed, aborted or running, never "completed".
>   - Only done passes.
>   - A done study that skipped a requested stage for budget fails the rule and names the stage. Test it.
> - F3. Record identity (#1038, #1123). The settle, eh.study.status and eh.review.blockers use the record the step's own dispatch published: started_at captured under the solver-state lock, and review_latest called on that record, never on "latest".
>   - Tests: a user-started EH study after the run's own; and the same with the activity bindings cleared.
> - F4. Settle count. The actual count is the sum of pipeline.stages[].solves_charged.
>   - A missing or unreadable count keeps the whole reservation.
>   - Actual > reserved charges the actual count, never capped down. Test it.
>   - Name the extra campaign charge from _campaign_gated in the PR.
> - F5. Dispatch only after the solve_queue job settled completed. A 409 or 422 settles the step refused, releases the reservation, and does not retry. Test it.
> - F6. kpi.unserved_mwh reads the run's own solve (bound ctx, the run's project_id, that solve's revision). Stale or unsolved data reads not_evaluated. Add a non-unit snapshot_weightings test.
> - F7. energization_mw: NaN or non-numeric reads as missing. Test that not_evaluated at the INPUT gate blocks, for both this fact and eh.readiness.ready.
> - F8. Q1 = A: eh.review.blockers goes in the result gate. Record the deviation in design.md §9.3 in the same PR. An erroring or truncated review fails the rule.
> - F9. Q3 = A: run_margin_loop through the existing LOOP_FACT engine path, restore base.
>   - Test target = goal h/yr × horizon_years(n) on a network whose horizon is not 1 year.
>   - The gate's LOLE comes from the loop record only, never from the EH study's MC LOLE.
>   - The engine loop and the agent loop never act on the same rule.
> - F10. No project write. run_eh_study runs on a private copy (eh_study.py:1158). Test that the step leaves the project's network revision and the run ledger unchanged.
> - F11. Mutants. Beyond mutants.py, hand-mutate each new guard and name the test that kills it:
>   - the args digest compare;
>   - the zero-left check;
>   - the F3 identity check;
>   - the F2 status map;
>   - the F1 freeze;
>   - the envelope refusal of run_eh_study;
>   - each fact's fail-closed branch.
>   List the survivors in the PR body.
>
> Process: one PR, with a release-note fragment and a kinds fragment.
>
> GK B (31), session_01G7qCuiUB2VeFL67ZyXeCSQ

### GK B (32): B3 plan verdict, GO WITH CHANGES (L1–L9, verbatim)

> - **L1, split under PR-SIZE-1.** B3a is items 1–3 (the engine card, the EH evidence, recovery). B3b is items 4–6 (the pilot, the smoke, §9.3), built on B3a. B3a goes first, since the pilot depends on it.
> - **L2, item 1: which branch.** Key it on the run record. A run that has never had any envelope id takes the card branch. A run with any envelope id at all (pending, granted, revoked or expired) keeps today's `no_envelope` → needs_user. That fails closed and needs no "was it ever granted" history.
> - **L3, item 1: the card binds the number.** The card shows the clamp computed when the card is built, and on Continue the reservation is ≤ that shown max_solves. If the budget fell meanwhile, recompute it lower. It is never higher. If the recomputed clamp is 0, it is budget_spent with no call. restore stays base. Tests: the budget is lowered between card and Continue, and the reservation equals the lower value. Hand mutant: reserve the uncapped rule max_solves.
> - **L4, item 2.** Match on project, ctx, job_id and started_at. Make the reservation release idempotent: a second resolve, or resolve plus a late settle, releases once. Test a double reconcile.
> - **L5, item 3: one follower.** The "no live follower" check and the follower registration happen under one lock. A tick and a takeover racing on the same step start at most one follower. Test two concurrent recover() calls.
> - **L6, item 3: older steps.** A waiting step stored before deadline_at existed has no deadline. It never times out by deadline; it goes to (b) or (c). Test a step with deadline_at absent.
> - **L7, item 5: the smoke is offline and bounded.** No network: weather, tariff and fetch_site_context come from fixtures or the isolated Library. Give it a wall-clock bound (state it) that fails cleanly. If the real EH study can't run offline inside the bound, keep the smoke as a pixi task outside CI and say so in the PR. Don't add a flaky CI step.
> - **L8, item 6.** An eh_site@1 change must not alter a run already started, beyond C8's synced stages (report, handoff) and E3's frozen explore. Your in-place C8 test asserts this for an old run record. If a §9.3 line would change an in-flight run's inputs or explore, bump to eh_site@2 instead, and ask me first.
> - **L9, mutants.** Hand mutants, each with its killing test named: L2: the card branch is taken when any envelope id exists; the L3 cap; item 2: the generic run_evidence fallback is put back for eh_study; item 3: a lost record re-dispatches the study; the L5 lock. Plus mutants on the changed lines. Record L1–L9 in issue 07.

### GK B (33): B3b plan verdict, GO WITH CHANGES (P1–P12, verbatim in substance)

Standing from GK B (32): L7 (the smoke is offline and bounded, or stays out of CI) and L8 (no change to in-flight runs; one that is needed becomes eh_site@2, asked first). A report job is tied to a run only by the harness's own explicit launch; a human request is never strict.

Driving the real pilot found three defects (F-a: the grant runs inside the host's chat turn, whose turn cell outranks the hand-over's request binding, so the new run was fingerprinted on the host and the new project's setup reset the host's user_ts and solver config; F-b: `_solve_job`/`job_of` read the EH study's job as the run's solve; F-c: a template project's first solve read as unverified, as its write-back backs the network series into user_ts) and three blockers (Q1 kpi.unserved_mwh refuses multi-period; Q2 no reliability goal at the grant; Q3 run_margin_loop refuses on the shipped eh_site network).

Slices: PR 1 = F-a, F-b, F-c with failing-first tests (the data-loss fix lands first). PR 2 = Q1–Q3, the pilot and the smoke. Each under 1,500 lines.

- P1 (F-a). The grant binds a fresh turn cell and releases it on every exit path, a raise included. Tests: the host's user_ts frames, solver config and network revision byte-equal after the grant; the new task's and envelope's fingerprints equal the new project's; the host cell is bound again after the grant, also after a raising grant; hand mutant: drop the cell.
- P2 (F-b). `run_gates._solve_job` and `run_loops.job_of` match tool == solve_queue_enqueue exactly. Test on the shipped eh_site@1 step order (solve.2 is run_eh_study), sv.solve.state evaluates. Mutant: the old "latest solve.*" rule.
- P3 (F-c). The one save runs before start() and before any fingerprint or freeze, and changes no solver config. Tests: the first solve of a fresh created project reads verified; the save does not bump the network revision again. The GUI create route is the named remaining risk.
- P4 (Q1 → A). A multi-period branch in `run_gates._kpi_unserved_mwh` only; `lost_load_calculations.source` and Check answers untouched (B is GK A's ground). Keep every F6 identity check, frame index == n.snapshots, columns ⊆ loads. Per investment period: sum(frame × snapshot_weightings.generators) within the period; never investment_period_weightings. The gate compares the worst period with the cap and names it; never the sum over periods. Tests: non-unit generator weightings differing between periods; cap passes per period but fails on the sum, and the reverse; misaligned frame; other_solve; each refusal NoEvidence. Hand mutants: sum over periods; drop the weightings.
- P5 (Q2 → A plus B's refusal). The goal is a value the user gave, shown on the grant card, unit-checked in h/yr, never a model default; written to the new project's solver config before the freeze. A profile with an LOLE rule and no resolved goal refuses the grant `goal_missing` (own tool-error-kinds.d fragment). The goal stays out of `_network_input_fingerprint` and the investment-case digest, with a test.
  - P5a (GK B (33), plain grant). A plain (no-intake) unattended grant whose profile needs the goal: a goal stored in the project's own solver config counts; a goal from template data alone counts only as the grant card shows it, with value, unit (h/yr) and source ("from the eh_site template"); a stored goal is shown with its source too; a code default never counts. Built: `run_envelope.card_goal` / `read_for_card` (the card's `detail` row and `goal` field), the granted envelope freezes `goal`. Tests: a template-sourced goal on the card with its source; a default-only config refuses goal_missing; hand mutant "count the default" killed by a default carrying a value.
  - F1 (GK B (34), NOT OK on #1257 @4b8bd6f7, blocking). A plain unattended grant re-read `card_goal` at the click and froze that, not the goal the card showed; goal fields are outside the input fingerprint, so `task_brief_stale` never caught a change. Fixed: the card renders `envelope.goal` (`EnvelopeSection`, `envelope-goal`) and sends it back as `EnvelopeDecision.goal` (strict: value, unit h/yr, source); `decide` freezes exactly it and refuses `goal_changed` when it is absent or its value or source differs from the live `card_goal`. Tests: template→stored, stored→template, stored value change, no echo; the model rejects another unit or field; frontend shows and echoes the goal. Hand mutants killed: drop the comparison; drop the value comparison.
  - W1 (GK B (34), non-blocking). In envelope mode `_loop_converged` never compares the loop record's `target_lole_h` / `goal_sources` with `envelope["goal"]`. Filed as [38](38-loop-goal-matches-grant.md).
- P6 (Q3 → A under eh_site@2). Switching a granted @1 run's engine is an L8 change: ship eh_site@2 with run_coupling_loop (max_solves 3, restore base); PROFILES keeps @1 byte-equal (pin test against master), resolve keeps @1 for stored runs; new intakes use @2, and P5's goal param lives in @2's intake. A test that every reader of a profile id or version accepts @1 and @2 (#1132 F1). Loop solves = solves_used + probe_solves + closing re-solve, charged to the run; goal via active_loop_goal/horizon_years. Probe `_refusal` with the grant: listing run_coupling_loop for the harness's own step never admits it on a model turn (#1123).
- P7. fetch_site_context, genset fuel price, import_urdb_tariff and run_investment_case (§9.3 diffs) are follow-ups, each filed under .scratch.
- P8 (L7). smoke/accept_harness_pilot.py: a hard 300 s timeout in the driver itself; fake provider; a socket guard refusing any non-loopback connect, with a test that it trips; run only through the gui-qa-drivers task.
- P9. In the pilot only the EH study may be stubbed (C11); the coupling loop and the solve are real.
- P10. Hand mutants plus `scripts/coord/mutants.py` on run_gates.py, killing tests named in the PR body.
- P11. A report job is tied to a run only by the harness's own explicit launch; nothing infers a run from state.
- P12. The release-note fragment states that a run granted before the change keeps eh_site@1.

### GK B (33): stepwise pilot design, GO on A (S1–S6, verbatim in substance)

A step-by-step verdict on an intake run creates the project through the same hand-over as the envelope path and grants nothing.
- S1. Creating the project is a write, so the stepwise verdict card states "creates project <name> from this intake" and the digest. The hand-over runs only on that user click, on exactly the digest the card showed; a drift refuses as in the envelope path (tested in stepwise).
- S2. Nothing is granted: no envelope reads granted, no approval is on record by an envelope, `run_envelope.admit` returns None for a later write. The pilot counts exactly one user card per write or run from the hand-over to the hand-off (equality).
- S3. The same hand-over code, no fork: P1, P3 and P5 apply in stepwise. One parametrised test runs both verdicts against the host-unchanged and fingerprint assertions.
- S4. The early return stays for every non-intake run, pinned by a test that a plain run's stepwise verdict calls no `_grant_setup`.
- S5. Hand mutants with their killing tests: stepwise also grants the envelope; stepwise skips `_grant_setup` on intake; the digest check dropped in stepwise.
- S6. L8 is not engaged: a decided run is past `decide()`, and an undecided stepwise intake run dead-ended before. Said in the PR body.
- S1a. Until the card renders the intake and sends its digest ([37](37-plan-card-intake-rows.md)), an intake run's verdict, stepwise or envelope, refuses an absent digest (`intake_stale`), and a drifted one. The GUI cannot start an intake run until 37 lands.

### GK B (33): capture of a stepwise run's own solve, GO on A (C1–C5)

The stepwise pilot stopped at the result gate: only an envelope-admitted solve was captured, so `kpi.unserved_mwh` read `no_capture`. C (a user-accepted not_evaluated KPI) is refused (#1079).
- C1. The capture is keyed on the step binding, never session state: only the run's own pending `solve_queue_enqueue` step (its call id, its frozen args), dispatched after its user card was approved. Any other call in a run session gets no capture.
- C2. One capture path: `solve_capture.requested(run ctx)` with the same identity checks and the project-mismatch 409; the envelope path byte-unchanged, pinned by comparing a capture record from each path.
- C3. Tests: the stepwise solve carries capture.exact and the pilot reaches hand-off with exactly one card per write or run; an ordinary chat or copilot solve gets no capture; the off-step call gets no capture; a denied card captures and enqueues nothing.
- C4. Hand mutants with killing tests: drop the wrap; capture every solve in a run session; capture before the card approval.
- C5. The seam is named in the PR body; a capture is evidence, never an admission.

### GK B (33): stepwise engine card, GO on A (E1–E5)

The engine step is carded in stepwise; `generate_report` (P11's explicit launch, no project input) and `read_handoff` need none.
- E1. Through the existing confirmation path, recorded as a user approval, bound to the engine step's frozen args; it states the solve bound, the base restore and the charge to the run. Already B3a's `run_loops.card_engine` + `run_envelope.reserve_carded` (the result gate's card).
- E2. Deny → waits for the user, zero solves, nothing reserved, no restore; a later Continue runs the loop once (`test_harness_engine_card.py`).
- E3. The gate is "never granted", read from the run's own envelope record (`never_granted`); envelope mode has no card (`test_a_granted_run_never_takes_the_card_branch`). A granted-then-revoked run is not `never_granted`, so it keeps B3a's `no_envelope` stop.
- E4. Pilot: cards == {solve.1, solve.2, result_gate.1}; harness launches == {report.1, handoff.1}; the engine step's settled solves == solves_used + probe_solves + the closing re-solve.
- E5. Hand mutants: skip the card in stepwise; card also in envelope mode; deny still runs the loop.

## Folded from #970

Owner decision HARNESS-970-1 (3 Nov). Three items from #970 land here, all after the pilot works. None changes the acceptance criteria above.

Items 1 and 2 are filed as their own issues, [07b](07b-trajectory-evaluation.md) and [07c](07c-report-comment-revisions.md), both blocked by this one (a coordinator decision under HARNESS-970-1). Item 3 stays here.

1. **Trajectory evaluation (HH-05B).** The pilot acceptance run is one trajectory. HH-05B turns the pilot, and later each profile's run, into a reproducible suite: a closed case list with fixed fixtures, an independent native oracle per case, positive and refusal controls, and a report that counts unauthorized effects, wrong-project results and false native completion as release blockers (refusing everything must fail). Build it as `harness/trajectory_evaluation.py` plus an offline CLI under `tools/`, with the fake provider; no paid provider runs and no wall-clock thresholds in CI. Its first cases are the ones the folded issues need: 22's barrier cases, 23's survive-a-restart case, 24's kill-between-call-and-charge case. **HH-05A is skipped** (#970's baseline characterization pack); only the barrier test lives on, as issue 22's failing test.
2. **Report comments → revisions (HH-08A).** A user reads the hand-off report and comments ("shorten the grid section", "show the BESS duration in hours"). HH-08A keeps each comment as a durable item until it is addressed or withdrawn: edits are a closed set (report paragraph/bullet text, workbook text and number formats), they preserve the evidence references and disclosures, they never change a number, formula, chart range or source assumption, and an unsupported comment stays open with its reason. Revisions are new copies against the saved report's digest (a stale base is refused). Builds on the harness chapter and revert preview of this issue and on the reports service (`services/reports/`); recheck it against master, since #970 is pinned at #944. A revision that needs a model call goes through issue 24's reservation. Numeric repairs (#970's 08B and 08C) stay out.
3. **Per-request-part coverage at hand-off (HH-09A, reduced).** The brief records an objective but not which parts of the request were asked for. Add one check to the hand-off: each part of the approved request (deliverable, case, stated target) is listed as `covered`, `partly` or `gap`, from the native receipts and gates, and any `gap` is named in the report and the completion notice (the same treatment as SC-15's unsupported chapters). No IntentContract store, no decision packets and no second authority over acceptance; HH-09A's larger store is dropped with HH-09B.
