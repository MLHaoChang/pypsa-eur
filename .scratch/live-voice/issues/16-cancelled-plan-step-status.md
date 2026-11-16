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
