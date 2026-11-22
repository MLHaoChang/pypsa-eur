<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# A stopped effectful step in a plan without a brief can be re-run at once

Status: needs-triage
Type: task
Found by: live-voice 16 (`16-cancelled-plan-step-status.md`), GK B (64) on the 16 plan.

Issue 16 gave a step whose job was stopped its own status, `cancelled`. In a plan with
a brief, `assistant_tasks._gate_brief` refuses to run a failed or cancelled effectful
step until `resolve_task_step(retry)` reopens it. A plan **without** a brief has no such
gate: an aborted effectful step (a solve the user just cancelled) can be re-called by the
model at once, since `next_step` still offers it with its args. This predates 16, where
the same step read `failed`.

## Scope

- Decide whether a non-brief plan's cancelled effectful step needs the user's word (a
  `resolve_task_step(retry)` or a Resume click) before it runs again.
- `_gate_brief` and `_view`'s `next_step`, with tests for both plan kinds.

## Comments
