<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Outsourced task C: classify the `_refuse(...)` error kinds in the tool-error manifest

Branch: `outsource/refuse-kinds-manifest` · one PR against `master` · PR body starts with `Outsourced task C`

Source issue: `.scratch/harness/issues/21-refuse-kinds-in-error-manifest.md`. This brief narrows that issue and supersedes its scope.

## Read first

1. `AGENTS.md` (repo root).
2. `pypsa-gui/tool-error-kinds.json`. Read its `$comment` in full: it explains the contract.
3. `pypsa-gui/backend/tests/test_tool_error_kind_manifest.py`. Read the docstrings; they explain why the derivation over-approximates on purpose.
4. `pypsa-gui/backend/harness/README.md`, "The contract" (rule 2, "Tools are data").

## Goal in plain words

When an assistant tool refuses, the chat panel shows the refusal either as a red banner or as a grey inline tool line. Every refusal kind is listed in `tool-error-kinds.json` with its surface and a reason. A backend test derives the kinds from the source code and fails when the list and the code disagree, so no new kind slips through unclassified.

The derivation only sees `"error_kind": "<literal>"` written inside a dict. Three service modules refuse through a helper, `_refuse(kind, message, ...)`, which builds that dict from a variable, so the test cannot see their kinds:

- `services/assistant_tasks.py` (27 kinds, e.g. `task_paused_at_card`);
- `services/experiments.py` (19 kinds);
- `services/run_evidence.py` (11 kinds).

These kinds do reach the chat as `tool_error` frames. Teach the derivation to read them, and classify all 57.

## User-facing outcome

- None today. Every new entry is `inline`, which is exactly how these refusals already render.
- **No new buttons, modes, UI copy or behaviour change.**
- The gain is a guard: a future refusal kind in these modules can no longer reach users unclassified.

## Files to touch

- `pypsa-gui/backend/tests/test_tool_error_kind_manifest.py`: extend the derivation and add guard tests.
- `pypsa-gui/tool-error-kinds.json`: add 57 entries.
- `doc/release-notes.d/outsource-refuse-kinds-manifest.md`.

## Files NOT to touch

- Any production module: `services/*.py`, `harness/*.py`, `routers/*`. Do not rename kinds or change messages.
- Frontend files (`ChatPanel.tsx`, `KIND_COPY`, `TOOL_ERROR_BANNER_KINDS`). No kind becomes a banner in this task.
- Existing manifest entries: do not reorder, reword or reclassify them. Append only; premerge waives an append-only overlap on this file.
- `scripts/coord/*`, `harness/README.md`.

## Contract

In `test_tool_error_kind_manifest.py`:

- Add a module constant:

  ```python
  _REFUSE_HELPER_MODULES: tuple[str, ...] = (
      "services/assistant_tasks.py",
      "services/experiments.py",
      "services/run_evidence.py",
  )
  ```

  The paths are relative to `pypsa-gui/backend`.
- Add `_refuse_helper_kinds(tree: ast.AST) -> set[str]`. It returns the first positional argument of every call `_refuse(<str literal>, ...)` whose function is the bare name `_refuse`. Ignore calls whose first argument is not a string constant, and record them in a module-level list so the guard test below can report them. Today there are none in these three modules.
- In `derive_tool_error_kinds()`, add `_refuse_helper_kinds(tree)` for files whose relative path is in `_REFUSE_HELPER_MODULES`. The rest of the derivation stays unchanged.
- New guard tests:
  1. `test_each_refuse_helper_module_still_raises_its_kind`: for each listed module, `ast` shows a top-level `def _refuse` whose first parameter is named `kind`, and whose body contains a dict literal with key `"error_kind"` mapped to the name `kind`. If someone renames the helper, the test fails instead of silently deriving nothing.
  2. Extend `test_the_derivation_finds_the_kinds_it_is_supposed_to_find` with three assertions: `"task_paused_at_card"` (assistant_tasks), one `experiments.py` kind (e.g. `"experiment_not_found"`) and one `run_evidence.py` kind (e.g. `"evidence_unavailable"`) are all in the derived set.
  3. `test_refuse_helper_calls_use_literal_kinds`: no dynamic first argument in the listed modules (the list from the helper above is empty).

In `tool-error-kinds.json`:

- Add one entry per new kind: `{"surface": "inline", "why": "<more than 30 chars>"}`.
- The `why` says, in one or two plain sentences, which tool refuses and what the assistant does next. Follow the style of the existing entries (e.g. `flow_project_not_open`), and read each `_refuse` call's message to write it.
- Add the entries at the end of `kinds`, in alphabetical order.

The existing test `test_the_manifest_still_describes_the_backend` must then pass with zero missing and zero stale kinds.

Out of scope: record these as follow-ups in the PR body; do not do them.

- Other modules that define `_refuse` (e.g. `workflow_tools.py`, `edit_card.py`, `job_adapters.py`).
- Calls such as `workflow._refuse(...)` made through a module attribute.

## Acceptance tests

Commands (repo root; install once with `pixi install --frozen -e default -e test`):

```bash
pixi run gui-tests tests/test_tool_error_kind_manifest.py
pixi run gui-tests   # full backend suite before opening the PR
cd pypsa-gui/frontend && npx vitest run src/components/ChatPanel.manifest.test.tsx src/components/ChatPanel.profile.test.tsx
```

The frontend manifest test asserts that every `inline` kind is in neither `KIND_COPY` nor `TOOL_ERROR_BANNER_KINDS`. None of the 57 kinds is there today, so it must pass unchanged.

## Repo rules

- Branch `outsource/refuse-kinds-manifest` from current `master`. Open one PR. Never force-push. Do not merge; our coordinator merges.
- PR body starts with the line `Outsourced task C`, then follows `.github/pull_request_template.md`. List the out-of-scope follow-ups in the body and mark it as AI-generated.
- Release-note fragment `doc/release-notes.d/outsource-refuse-kinds-manifest.md`:

  ```
  * Test(pypsa-gui): The tool-error manifest now covers the refusal kinds of assistant plans, network experiments and run evidence, so a new refusal there cannot reach the chat unclassified.
  ```

  Never edit `doc/release_notes.md` (see `doc/release-notes.d/README.md`).
- Never skip, loosen or delete an existing test. Make no paid model calls and commit no credentials.
- If `master` gains a new `_refuse` kind in these modules while your PR is open (for example outsourced task B adds `task_step_retry_limit`), add its entry when you merge `master` in.

## Done when

- [ ] `tests/test_tool_error_kind_manifest.py` passes, with the three new or extended guard tests.
- [ ] 57 entries are added (one per kind derived from the three modules), all `inline`, each with a `why` of more than 30 characters; no existing entry changed.
- [ ] The frontend manifest and profile tests pass unchanged.
- [ ] The full `pixi run gui-tests` is green locally, or the PR lists each failure that also fails on `master`.
- [ ] The diff touches only the three listed files.
- [ ] One PR on `outsource/refuse-kinds-manifest`, with a body starting `Outsourced task C`.
