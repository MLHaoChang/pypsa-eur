<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# `solve_queue.enqueue_unique` silently drops a different solver config

Status: in-review
Type: bug
Found by: AA-04 S5 plan (`04-action-and-job-outcomes.md`); GK B (30) Q3 (a): outside S5.

## Problem

`SolveQueue.enqueue_unique` keys on the project only. When the project already
has a queued or running job, it returns that job and discards the new call's
`solver_config_json` (its docstring says so). A caller that queued a solve with
other settings is answered `already_queued: true` with no sign that its
settings were not used: the duplicate is prevented, the conflict is not shown.

## Wanted

A re-enqueue whose config snapshot differs from the active job's is refused
with its own kind (or answered with a field that says the settings differ),
so the user can wait, abort or keep the queued settings knowingly. Same-config
re-enqueues stay idempotent.

## Notes

- `services/solve_queue.py` `enqueue_unique`; `routers/solve_queue.py` `enqueue_solve`.
- Issue 04 S5's hold (`harness/job_holds.py`) denies a chat re-enqueue while the
  store has the project's solve, but the HTTP route and the panel still meet
  the silent drop.

## Comments

### 2026-11-07: triage sweep (FILL-1107)

- Likely files: `solve_queue.enqueue_unique` and its callers/tests; new tool-error kind JSON.
- Blocked by: none.
- Model tier: sonnet.
- Gated: GK B.
- Size: S.
- Refuse (own kind) or flag a differing config snapshot; same config stays unchanged.
