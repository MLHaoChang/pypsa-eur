<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-25 — A failing plan step stops after two retries

Status: ready-for-agent
Outsourced: Codex (2026-10-22) — branch outsource/task-step-retry-limit
Type: task
Baseline: master `514c8feede1177b520a4dde74a4dffaf12cf696a`
Source: PR #567 assessment (LOOP-01/02, priority 3 "bounded validation/correction loops"); brief `.scratch/harness/outsource/B-task-step-retry-limit.md`
Related: [LC-19](19-run-lifecycle-and-failure.md), [LC-23](23-study-copilot.md) SC-14

## Problem

A failed or uncertain plan step can be reopened with
`resolve_task_step(retry)` without limit. Each retry shows the user another
confirmation card, so a step that keeps failing for the same reason produces
card after card instead of an explanation.

## Scope

1. `TASK_STEP_RETRIES_MAX = 2` per step, counted in the task file
   (`retries`; a missing key means 0).
2. A third retry is refused `task_step_retry_limit` (409) and nothing is saved.
   Refused retries (`task_step_running` and the like) do not count.
3. Under a brief, `next_step.correction` becomes
   `{"stop": "retry_limit", ...}` instead of the triage list.
4. `cancel_task` and `completed` reconciliation are unchanged. No UI change.

## Done when

The five tests in `.scratch/harness/outsource/B-task-step-retry-limit.md` pass; the existing task suites pass
unchanged; the error-kind manifest test passes (with an `inline` entry if issue
21's derivation has merged); the README sentence and the release-note fragment
are in the PR.

## Comments
