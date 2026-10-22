<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 21 — Error-kind manifest: derive `_refuse("<kind>", ...)` kinds

Status: ready-for-agent
Outsourced: Codex (2026-10-22) — branch outsource/refuse-kinds-manifest
Type: task
Baseline: master `514c8feede1177b520a4dde74a4dffaf12cf696a`
Source: gatekeeper review of #573 (LC-23 slice 1), 2026-10-21; PR #567 assessment ("typed error manifest"); brief `.scratch/harness/outsource/C-refuse-kinds-manifest.md`

## Problem

`tests/test_tool_error_kind_manifest.py` derives error kinds from
`"error_kind": <literal>` dicts and `tool_error` yields. The modules that refuse
through `_refuse(kind, message)` build the dict from a variable, so their 57
kinds are invisible to the scan and missing from `pypsa-gui/tool-error-kinds.json`:
`services/assistant_tasks.py` (27), `services/experiments.py` (19) and
`services/run_evidence.py` (11). They can still reach a `tool_error` frame.

## Scope

1. The derivation reads the first string-literal argument of bare `_refuse(...)`
   calls in an explicit module list (the three above).
2. A guard test fails if a listed module's `_refuse` stops putting `kind` under
   `error_kind`, or if a call passes a non-literal kind.
3. Classify all 57 kinds as `inline`, each with a `why`. No frontend change.
4. Out of scope (follow-ups): the other `_refuse` modules (`workflow_tools`,
   `edit_card`, `job_adapters`, …) and `workflow._refuse(...)` attribute calls.

## Done when

The manifest test passes with these kinds classified; the frontend
`ChatPanel.manifest.test.tsx` passes unchanged; the diff touches only the test,
the JSON and a release-note fragment.

## Comments
