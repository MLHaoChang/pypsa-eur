<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-45 — No polling: renewable-profile and investment-decision use a `start_task` plan

Status: done (#1427 merged 2026-11-12; GK B (52) OK @767e831e)
Type: task
Model: Sonnet
Reviewers: GK B
Blocked by: none (refines [LC-12](12-job-completion-continuation.md), which is done)
Source: owner decision C1C9-FILE-1 (2026-11-10, "C5, C4, C3"); change C5 of the workflow review (artifact RKkscAxy5yZG6EuxjWH71W, "Walkthrough check"); `.scratch/ui-modern/audit.md`.
Evidence: **confirmed, text only.** The chat was not walked (no API key); the claim was read from the workflow text and the code, not measured. In the panel UI a run shows its own progress, so the cost is in chat only.

## What

Two workflows make the model poll a long job itself:

- `harness/workflows/renewable-profile.md` (generate step, ~line 123): polls `solve_queue_list` until the profile job completes.
- `harness/workflows/investment-decision.md` (verdict step, lines 48-51): runs `run_decision_study`, polls `get_study_findings`, runs stage `tornado`, polls again.

Rewrite both steps as a one-step `start_task` plan, as the sensitivity skill does ("do not poll"), so the conversation continues by itself when the job ends. For investment-decision the plan covers both runs (options, then tornado), so the user is not asked to come back between them.

Add a decision-study adapter to `harness/job_adapters.py` if `wait_for_job` does not reach those jobs yet (the review did not check this; check first).

## Why

Polling costs wasted turns and tokens, and the user must return and ask "is it done?". Plans and continuation already exist (`harness/continuation.py`).

## Done when

- Neither workflow text tells the model to poll; each names the plan step and what the continuation does on completion and on a failed job (the refusal is read to the user).
- A test shows each path continuing without a poll call, including the failed-job path.
- Safety rules stay: the run cards are still the user's own cards (`run_decision_study` approval unchanged); no envelope admission.
- Release-note fragment added.

## Likely files

`pypsa-gui/backend/harness/workflows/renewable-profile.md`, `investment-decision.md`, `harness/job_adapters.py`, `harness/continuation.py`, `harness/skills/sensitivity/SKILL.md` (the pattern), tests under `pypsa-gui/backend/tests/`.

## GK B conditions (plan GO, session 50) and where each is met

PR #1427. Tests are in `pypsa-gui/backend/tests/test_lc45_no_polling_plans.py` (follower) and `test_lc45_real_records.py` (records the runners really save).

- L1 profile artifact tied to the job, source and technology: `test_a_completed_profile_job_without_its_own_artifact_is_uncertain`, `test_an_unreadable_artifact_listing_is_uncertain`.
- L2 unknown kind never completed; solve-queue jobs keep `_queue_run`: `test_an_unknown_job_kind_is_never_completed`.
- L3 study follower bound to its own run's `started_at`: `test_a_newer_run_never_settles_this_step`.
- L4 terminal states: budget or deadline stop is partial with the reason, user abort fails with "aborted", failed run fails with its error, done with an unsolved option or a not-established tornado is partial; a skipped tornado is completed (the study's answer): `test_lc45_real_records.py` (real runs and `tornado_runner._save`).
- L5 vanished record, unreadable study and the follow cap settle uncertain: `test_a_vanished_record_settles_uncertain`, `test_a_study_that_never_ends_settles_uncertain_at_the_cap`; malformed records (N1): `test_a_malformed_record_*`.
- L6 read under the caller's identity and ACL: `test_a_study_the_caller_cannot_read_is_refused_not_followed`.
- L7 restart: `Runner._recover` settles a waiting study step uncertain: `test_a_restart_settles_a_waiting_study_step_uncertain`.
- L8 admission unchanged: only `QUEUE_WAIT_TOOLS` grew; run-monitor, runner and loop suites pass.
- L9 workflow texts have no poll wording; the generated `investment-decision.md` comes from `workflows/sources/`; inventory and control-matrix `--check` pass.
- L10 the real follower runs (short poll and cap); only the native reads are faked.
- L11 hand mutants are all killed; the two defensive layers against malformed records are equivalent to each other.


### 2026-11-12: hand-off (LC-45 builder)

Follow-ups, not done: take the study `attempt` from the start call's result instead of re-reading after it returns (GK B, non-blocking); the `aborting` status mapping has no test; test_workflow_provider_parity was not compared against master (sandbox connection error); no live chat walk (no API key). By design a harness run's own profile/study steps stay on run_explore's judge (plan-only gating); the profile step trusts list_pending_profiles.
