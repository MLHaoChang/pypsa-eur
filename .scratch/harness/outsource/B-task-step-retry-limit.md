<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Outsourced task B: a failing plan step stops after two retries

Branch: `outsource/task-step-retry-limit` · one PR against `master` · PR body starts with `Outsourced task B`

## Read first

1. `AGENTS.md` (repo root).
2. `pypsa-gui/backend/harness/README.md`, section "Durable assistant tasks and file delivery" and its subsection "Study brief (LC-23)". Read especially the bullets "Reconciliation (SC-12, all plans)" and "Correction (SC-14, LC-19)".
3. `pypsa-gui/backend/services/assistant_tasks.py`: `_correction` (~l.207), `_view` (~l.216), `resolve_task_step` (~l.405), `_gate_brief` (~l.520).
4. `pypsa-gui/backend/tests/test_assistant_tasks_study_brief.py`. Your tests go here; reuse its `session`, `refused`, `brief` and `run` helpers.

## Goal in plain words

The assistant can run a durable plan (`start_task`). When a step fails or its outcome is uncertain:

- the plan stops;
- the assistant reads the suggested triage tools;
- it may call `resolve_task_step(task_id, "retry")`, which reopens the step. That call shows the user a confirmation card each time.

Nothing limits how often this repeats. A step that keeps failing for the same reason produces card after card. Add a fixed limit: **a step can be retried at most 2 times.** A third retry is refused with a typed reason that tells the assistant to stop, explain what failed, and let the user decide. The user can fix the inputs themselves or cancel the plan and start a new one.

## User-facing outcome

- After two failed retries the assistant stops asking "retry?" and explains the problem instead.
- **No new buttons, modes, settings or UI copy.** The existing cancel and new-plan paths are unchanged.
- Plans whose steps succeed behave exactly as before.

## Files to touch

- `pypsa-gui/backend/services/assistant_tasks.py` (the only production file).
- `pypsa-gui/backend/tests/test_assistant_tasks_study_brief.py`: new tests.
- `pypsa-gui/backend/harness/README.md`: one sentence appended to the "Correction (SC-14, LC-19)" bullet, and one clause in the plain-plan paragraph that says "`resolve_task_step` requires ordinary destructive confirmation…".
- `pypsa-gui/tool-error-kinds.json`: **only if** it already lists `assistant_tasks.py` kinds such as `task_paused_at_card` when you branch (that means outsourced task C has merged). In that case add `task_step_retry_limit` with `"surface": "inline"` and a `why` of more than 30 characters. Otherwise leave the file alone.
- `doc/release-notes.d/outsource-task-step-retry-limit.md`.

## Files NOT to touch

- `harness/loop.py`, `harness/continuation.py`, `harness/confirm.py`, `harness/budget.py`, `harness/catalogue.py`, `routers/*`, any frontend file, `scripts/coord/*`.
- Do not change existing error kinds, task file `version` numbers, step limits (12/24 steps, 1800 chars) or the SC-11/SC-12 rules.

## Contract

- Add a module constant `TASK_STEP_RETRIES_MAX = 2`.
- Each step may carry `"retries": int` in the task file. A missing key means 0, so older task files keep working; do not migrate them.
- In `resolve_task_step(task_id, outcome="retry")`:
  1. Run every existing check unchanged and in the same order: `invalid_task_resolution`, `task_resolution_not_needed`, `task_step_running`.
  2. Then, if `step.get("retries", 0) >= TASK_STEP_RETRIES_MAX`, raise via the module's `_refuse("task_step_retry_limit", <message>)` (HTTP 409) **without changing or saving the task**. Message: "This step has failed after 2 retries. Tell the user what failed and ask how to go on: they can fix the inputs, or cancel_task and start a new plan."
  3. Otherwise increment `step["retries"]`, then continue exactly as today (status `pending`, pop `error`/`late_receipt`/`receipt`, save).
  - A refused retry never increments the counter.
  - `outcome="completed"` neither reads nor changes the counter.
- `_view`:
  - when the next step has `retries > 0`, add `next_step["retries"] = n`;
  - when `retries >= TASK_STEP_RETRIES_MAX` and the step is failed or uncertain, `next_step["correction"]` (under a brief) becomes `{"stop": "retry_limit", "then": "explain what failed; the user decides: fix the inputs, or cancel_task"}` and omits `triage`/`reread`;
  - plain plans (no brief) still get no `correction` key, as today.
  - Keep the view within the existing 3800-char budget.
- `cancel_task` must still work on a step at the limit.
- The limit applies to plain plans and briefs alike.

## Acceptance tests to add (in `tests/test_assistant_tasks_study_brief.py`)

1. `test_a_step_is_retried_at_most_twice`:
   - plain plan with one `export_network_nc` step;
   - fail it (`tasks.finish_step(begin_step(...), error="x")`), retry, fail again, retry, fail again;
   - the third `resolve_task_step(..., "retry")` is refused `task_step_retry_limit`;
   - the task file on disk is byte-identical before and after the refused call;
   - `get_task` shows `next_step.retries == 2`.
2. `test_a_refused_retry_does_not_count`: a retry refused with `task_step_running` (use the `Future` worker pattern from `test_detached_timeout_worker_blocks_reconciliation` in `tests/test_assistant_capabilities.py`) leaves `retries` absent or 0. After the worker finishes, two retries are still allowed.
3. `test_retry_limit_correction_stops_instead_of_triage` (brief with a `run_simulation` step; reuse the `test_sc14_…` pattern):
   - after two retries and a third failure, `next_step.correction == {"stop": "retry_limit", ...}` with no `triage`;
   - `len(json.dumps(view)) <= 3800`.
4. `test_cancel_still_works_at_the_retry_limit`.
5. `test_old_task_files_without_retries_still_retry`: delete the `retries` key from a saved task file; a retry is accepted and sets `retries == 1`.
6. Existing tests must pass unchanged, in particular `tests/test_assistant_task_outcomes.py`, `tests/test_assistant_capabilities.py` (each retries a step at most once) and `tests/test_assistant_tasks_study_brief.py`.

Commands (repo root; install once with `pixi install --frozen -e default -e test`):

```bash
pixi run gui-tests tests/test_assistant_tasks_study_brief.py tests/test_assistant_task_outcomes.py tests/test_assistant_capabilities.py tests/test_task_solver_outcome.py
pixi run gui-tests tests/test_tool_error_kind_manifest.py
pixi run gui-tests   # full backend suite before opening the PR
```

`gui-tests` runs `python -m pytest` inside `pypsa-gui/backend`.

## Repo rules

- Branch `outsource/task-step-retry-limit` from current `master`. Open one PR. Never force-push. Do not merge; our coordinator merges.
- PR body starts with the line `Outsourced task B`, then follows `.github/pull_request_template.md`. Mark it as AI-generated.
- Add a release-note fragment `doc/release-notes.d/outsource-task-step-retry-limit.md`:

  ```
  * Fix(pypsa-gui): The assistant retries a failing plan step at most twice; then it stops, explains what failed and lets the user decide (`task_step_retry_limit`).
  ```

  Never edit `doc/release_notes.md` (see `doc/release-notes.d/README.md`).
- Docs and tests go in the same PR. Never skip, loosen or delete an existing test. Make no paid model calls and commit no credentials.

## Done when

- [ ] The five new tests pass; the listed existing suites pass unchanged.
- [ ] A refused third retry leaves the task file unchanged on disk (tested).
- [ ] `tests/test_tool_error_kind_manifest.py` passes. It must pass whether or not task C has merged; see "Files to touch".
- [ ] The full `pixi run gui-tests` is green locally, or the PR lists each failure that also fails on `master`.
- [ ] The diff touches only the listed files; README sentence and release-note fragment present.
- [ ] One PR on `outsource/task-step-retry-limit`, with a body starting `Outsourced task B`.
