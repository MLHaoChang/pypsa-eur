<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# A cancelled plan step reads cancelled, not failed

Status: needs-triage
Type: task
Found by: live-voice 11b (`11-nonblocking-monitoring.md`), GK B (59) on the 11b plan.

A plan step whose job was stopped on purpose (`cancel_job`, `abort_simulation`,
`solve_queue_abort`, the study and report aborts) settles with step status `failed`
today. `assistant_tasks._settle` maps every native `aborted` to `failed`. The
error reads "The solve ended aborted; …" and `step.job.outcome` is `aborted`, but
the status the plan, its counters and the plan UI read is `failed`. The user
cancelled; nothing failed.

11b kept this as it is (GK B: step accounting unchanged), and its B2 test pins the
distinct facts instead: the tool result is `cancel: cancelled`,
`step.job.outcome == 'aborted'`, and no continuation is armed.

## Scope

- `services/assistant_tasks._settle`: a `cancelled` step status for native
  `aborted`, for every abort path, not only `cancel_job`.
- The task counters, the task's own status, `resume_task` / `next_step` and the
  continuation rules (no continuation today; keep that).
- `test_oj07_edit_relaunch_walk_journey.test_oj08_progress_then_cancel_gives_no_walk`
  pins `failed` and changes with it, as does the 11b B2 cancel test.
- The plan UI that shows step status. This goes to UX review.

## Comments

- GK B (64) plan GO 2026-11-17 at master 0ab07339, conditions:
  C1 one mapping in `_settle`: native `aborted` + caller `failed` → `cancelled`, nothing
  else. Queue: all cases aborted → cancelled; any failed, completed+aborted → failed; an
  empty case list never reads `cancelled`.
  C2 `cancelled` in every step-status enumeration (TS unions, task view, any validator);
  inventory --check, route parity, control matrix and result snapshots at the trial merge;
  an old task file with an aborted-failed step loads and reads as before.
  C3 each reader (assistant_tasks 473/487/1298/1469, run_explore 1294, run_gates 1102)
  tested as for failed except the correction; the retry cap on a cancelled step; failed-step
  counts/summaries meaning "did not complete" include cancelled, listed in the PR.
  C4 run_gates settles a cancelled step at once with outcome `aborted`; the test fails on master.
  C5 correction and error text by equality; "cancelled_by_user" only on cancel_job and
  abort_simulation, neutral wording for study and queue stops; each path's text tested.
  C6 targeted mutants per guard, listed in the PR body with killing tests. UI label to UX.
  Open question: keep today's non-brief behaviour; follow-up issue names that an aborted
  effectful step can be re-called at once.
