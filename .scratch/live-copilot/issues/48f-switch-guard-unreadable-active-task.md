<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-48f — The goal-switch guard when the active task cannot be read

Status: ready-for-agent
Model tier: opus
Size: S
Blocked by: none
Gated: GK B
Likely files:
- pypsa-gui/backend/services/assistant_tasks.py (`_refuse_switch`, `_read`)
Type: task
Parent: [48d-wire-recovery-rows.md](48d-wire-recovery-rows.md) (GK B N-c on #1515)

`_refuse_switch` (owner decision LC48D-GOAL-1) lets a start or resume through when `session.task_id` names a task that `_read` cannot read. The task file is read under the open project, so after the chat switches projects the old task is "gone". A task still running in the other project (a harness run, say) then does not block a start in this one. This existing behaviour was kept on purpose in #1515 ("nothing runs that a switch would cut"); it holds only if nothing of the other project's task still executes.

Owner decision LC48F-REFUSE-1: refuse with `switch_in_progress` when the active task cannot be read (fail closed, across projects).

## Done when

- The owner decides whether a running task in another project blocks a start: refuse with `switch_in_progress`, or allow it because the projects are separate.
- The guard follows that decision, with a test that switches the chat's project while its task runs and then starts another plan.
- Carried in from GK B N-a on #1515: `test_a_run_start_for_another_plan_is_refused_while_a_task_runs` in `tests/test_lc48d_d23_wire_recovery.py` also asserts that no task file exists for `run-1` after the refusal, and that `session.task_id` is unchanged.
