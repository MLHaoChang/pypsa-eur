<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# `solve_queue.enqueue_unique` silently drops a different solver config

Status: done (#1261)
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

### 2026-11-07: GK B plan verdict, GO WITH CHANGES (recorded verbatim)

- Q1. Compare canonically, not as raw strings: json.loads both, then compare dicts (or sort_keys dumps). Key order or whitespace must never read as a conflict. Test: the same config serialised with a different key order → already_queued.
- Q2. The conflict check sits in the same critical section as enqueue_unique's active-job lookup, under the queue's lock, so no second enqueue can land between check and insert. Test: two threads, differing configs → exactly one job, and the other raises.
- Q3. Every 409 consumer branches on error_kind, never on status alone. This route can already 409 (the solve_capture project mismatch). List every caller of the enqueue route and of solve_queue_enqueue in the PR body: the frontend panels, the chat tool's `_route` mapping, harness/runner. Show that none reads a 409 as "already queued" or as success. If the panel UI shows a generic error, say so as the skipped item.
- Q4. Harness: in a run, a refused solve step (solve_config_conflict) fails or waits; it never completes and never captures. Test through runner.step_capture (issue 07 C1): a conflicting enqueue leaves no capture record and no settled solve.
- Q5. The typed refusal gets a fail-closed test at the catching site in routers/solve_queue.py, with a base-class mutant that the test kills. Plus a chat-tool test against the real dispatcher signature (no `_probe` monkeypatch) showing error_kind reaches the tool result.
- Q6. The 409 body names the existing job id only, never its config or its owner. The route's existing project ACL stays in front of the check.
- Q7. Opt-in default False: test that the campus/gridspine callers' behaviour is byte-unchanged (differing config → (existing, False) as today). Put the kind in your own tool-error-kinds.d fragment; at the trial merge run test_tool_error_kind_manifest, ChatPanel.manifest.test.tsx and smoke/json_fragments.py.
