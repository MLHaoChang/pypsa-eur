<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 07 — Report and hand-off stages; eh_site pilot end-to-end

Status: ready-for-agent
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

## Folded from #970

Owner decision HARNESS-970-1 (3 Nov). Three items from #970 land here, all after the pilot works. None changes the acceptance criteria above.

Items 1 and 2 are filed as their own issues, [07b](07b-trajectory-evaluation.md) and [07c](07c-report-comment-revisions.md), both blocked by this one (a coordinator decision under HARNESS-970-1). Item 3 stays here.

1. **Trajectory evaluation (HH-05B).** The pilot acceptance run is one trajectory. HH-05B turns the pilot, and later each profile's run, into a reproducible suite: a closed case list with fixed fixtures, an independent native oracle per case, positive and refusal controls, and a report that counts unauthorized effects, wrong-project results and false native completion as release blockers (refusing everything must fail). Build it as `harness/trajectory_evaluation.py` plus an offline CLI under `tools/`, with the fake provider; no paid provider runs and no wall-clock thresholds in CI. Its first cases are the ones the folded issues need: 22's barrier cases, 23's survive-a-restart case, 24's kill-between-call-and-charge case. **HH-05A is skipped** (#970's baseline characterization pack); only the barrier test lives on, as issue 22's failing test.
2. **Report comments → revisions (HH-08A).** A user reads the hand-off report and comments ("shorten the grid section", "show the BESS duration in hours"). HH-08A keeps each comment as a durable item until it is addressed or withdrawn: edits are a closed set (report paragraph/bullet text, workbook text and number formats), they preserve the evidence references and disclosures, they never change a number, formula, chart range or source assumption, and an unsupported comment stays open with its reason. Revisions are new copies against the saved report's digest (a stale base is refused). Builds on the harness chapter and revert preview of this issue and on the reports service (`services/reports/`); recheck it against master, since #970 is pinned at #944. A revision that needs a model call goes through issue 24's reservation. Numeric repairs (#970's 08B and 08C) stay out.
3. **Per-request-part coverage at hand-off (HH-09A, reduced).** The brief records an objective but not which parts of the request were asked for. Add one check to the hand-off: each part of the approved request (deliverable, case, stated target) is listed as `covered`, `partly` or `gap`, from the native receipts and gates, and any `gap` is named in the report and the completion notice (the same treatment as SC-15's unsupported chapters). No IntentContract store, no decision packets and no second authority over acceptance; HH-09A's larger store is dropped with HH-09B.
