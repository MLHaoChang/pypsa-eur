<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 21 — Error-kind manifest: derive `_refuse("<kind>", ...)` kinds

Status: needs-triage
Type: task
Source: gatekeeper review of #573 (LC-23 slice 1), 2026-10-21

## Problem

`tests/test_tool_error_kind_manifest.py` derives the backend's error kinds
from `error_kind` dict literals and `tool_error` yield sites. Services that
refuse through a helper, `_refuse(kind, message)` (for example
`services/assistant_tasks.py`), build the dict from a variable, so their kinds
are invisible to the scan. They can still reach a `tool_error` frame through
tool dispatch, but `pypsa-gui/tool-error-kinds.json` cannot list them: an
entry the scan cannot derive fails as stale.

Missing today from `assistant_tasks.py` alone: `invalid_task`,
`invalid_task_tool`, `invalid_task_args`, `invalid_task_reference`,
`task_not_found`, `task_step_uncertain`, `task_step_running`,
`task_step_already_completed`, `task_resolution_not_needed`,
`invalid_brief`, `brief_inputs_unreadable`, `task_pause_needs_user`,
`task_paused_at_card`, `task_not_paused`, `task_brief_stale`,
`task_resolution_unverifiable`, `task_reference_too_large`, and others.

## Scope

1. Teach the derivation to read the first positional string literal of calls
   to a module-level `_refuse` helper.
2. Classify every newly derived kind in `tool-error-kinds.json` (`inline`
   unless the panel must show a banner).
3. Keep the derived set a superset; no kind is listed that the scan cannot see.

## Done when

The manifest test passes with the `_refuse` kinds of `assistant_tasks.py`,
`experiments.py` and `run_evidence.py` classified.
