<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 07 The failed-run banner gives the wrong advice when the run failed for `project_view_only`

Status: needs-triage
Source: E2E walk for 02 (PR #1806, shot `vr02-02a-member-run-refused-live`). The bug is on master.

## Problem

After 02, a run whose requester lost write access fails with error kind `project_view_only`. The banner still says to fix the inputs:

- `pypsa-gui/frontend/src/pages/decision/RunStep.tsx:51-54`: the banner shows `run.error` and always appends "Check your answers and assumptions, then run again." It ignores the kind.
- `pypsa-gui/frontend/src/utils/decisionVocabulary.ts:306`: `ERROR_COPY` has no `project_view_only` entry.
- `pypsa-gui/frontend/src/components/runMonitor/runWords.ts:305` already has the right words ("View only: ask the project owner for edit access."), but only in `DRAFT_REFUSAL_WORDS`.

## Reach today

On master, decision studies run only in local mode (`services/study/__init__.py:17`, `decision_studies_enabled()`), where the one user is never a viewer. So this banner is latent until decision studies come to multi-user mode, or until a run surface that multi-user mode serves shows the same fixed advice.

## Acceptance

- For `project_view_only`, the banner tells the user to ask the owner for edit access and does not invite a re-run. The copy is reused from one place, not duplicated.
- Other failure kinds keep the current advice.
- Shot of the banner in the real app.
