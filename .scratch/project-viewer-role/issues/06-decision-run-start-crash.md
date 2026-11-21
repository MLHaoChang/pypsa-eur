<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 06 The DecisionPanel crashes when a run starts: the 202 body has no `solved`/`pending`

Status: needs-triage
Source: E2E walk for 02 (PR #1806, shots in `.scratch/project-viewer-role/shots-02/`). The bug is on master; #1806 did not cause it.

## Problem

Starting a study run from the Decision page blanks the panel with a TypeError until the refetch lands.

- `pypsa-gui/frontend/src/pages/decision/DecisionPanel.tsx:209-213`: `startRun.onSuccess` writes the raw POST response into the run query cache (`qc.setQueryData(dq.run(...), rec)`).
- `pypsa-gui/backend/services/study/runner.py:529-531`: that 202 body is only `{status, study_id, fidelity, options, solves_charged, campaign}`.
- `pypsa-gui/frontend/src/pages/decision/RunStep.tsx:25,27`: `run.solved.includes(id)` and `run.pending.includes(id)` throw on that body.
- `GET /studies/{id}/run` (`routers/studies.py:877-892`, `runner.py` `get_study_run`) returns the full record, with `solved` and `pending`.

## Options for the fix

- Return the same record shape from the POST as from the GET (backend, `runner.py`, which #1806 also changes).
- Or stop seeding the cache with the POST body and only invalidate (frontend).

## Acceptance

- A test starts a run and renders RunStep from the cached POST result without a throw.
- Shot of the Decision page right after "Run" in the real app.
