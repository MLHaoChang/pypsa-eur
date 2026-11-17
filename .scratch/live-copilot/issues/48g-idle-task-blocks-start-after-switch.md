<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-48g — An idle task in another project blocks a new plan after a switch

Status: needs-triage
Model tier: opus
Size: S
Blocked by: none
Gated: GK B
Likely files:
- pypsa-gui/backend/services/assistant_tasks.py (`_refuse_switch`, `begin_step`, `_release_sessions`)
Type: task
Parent: [48f-switch-guard-unreadable-active-task.md](48f-switch-guard-unreadable-active-task.md) (GK B non-blocking note on #1558)

Since #1558 (owner decision LC48F-REFUSE-1), `_refuse_switch` fails closed when the chat's task cannot be read in the open project. A task that has ended (`completed`, `cancelled`) lets go of its chat in the same write (GK B E), so it never blocks. A task that is idle but not ended (`ready`, `needs_review`, `paused_at_card`) keeps the chat. After a project switch the guard cannot read it, so it refuses a new plan with `switch_in_progress` (`active.status: "unreadable"`). Nothing of that task runs. The user's ways out are switching back and cancelling it there, or opening a new chat.

## Done when

- The owner decides whether an idle task in another project blocks a new plan:
  - keep refusing (fail closed, as today);
  - read the task in its own project (by its `project_id`, with the owner check) and refuse only when it really runs;
  - or detach an idle task at the switch.
- The guard follows that decision, with a test that switches projects while the chat's task is `ready` (and `needs_review`) and then starts another plan.
