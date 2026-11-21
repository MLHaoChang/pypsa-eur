<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 31 Four journeys have no CI browser check: Expert panels on PRs, investment case, grid study, imports

Status: needs-triage
Blocked by: none (the imports check waits on `project-io/04`; the viewer check is in `project-viewer-role/05`)
Source: gap review 2026-11-21 (`.scratch/gap-review-2026-11-21/README.md`), coverage map.
Owner decisions: E2E-TEMPLATES-1; CI-SLOTS-1 and RETRO-CI-1 (CI cost).
Model: sonnet. Review: GK B.

## Problem

CI runs two browser jobs: `gui-template-journeys` (`template-journeys.mjs`; short mode on PRs, full mode on schedule) and `gui-owner-journeys` (five `smoke-copilot-*.mjs` scripts). Everything else is HTTP tests or screenshot scripts run by hand. The coverage map found no CI browser check for:
- the Expert panels on PRs: only the schedule's full mode walks the ten Expert blocks;
- the investment case and business case (`qa_investment_case.py` and route tests only; `bc-line-editor-shots.mjs` by hand);
- the Grid study and grid compliance (route tests only; `grid-risk-shots.mjs` and `poc-xr-shots.mjs` by hand);
- imports (`pages/ImportExport.tsx` has no browser check; the model imports have no UI yet).

## User-visible impact

A broken button or a dead end in these four journeys reaches master with CI green. The walks of 17–21 Nov found several such dead ends, for example `study-harness/61`.

## Question for the owner

These checks cost CI minutes (CI-SLOTS-1).
1. **(Recommended) Add them to the schedule's full mode**, one journey per area on one template each (data-centre for investment and grid study). PRs keep today's short mode. Failures are reported by the daily run.
2. Add them to PR short mode as well, path-filtered to each area's files. This catches breaks before merge and costs about 5–8 min per PR that touches these paths.
3. Not now: the HTTP tests are enough.

## Acceptance criteria (option 1)

- [ ] `template-journeys.mjs --mode full` gains: Expert panels open, edit, save and read back for one template; Investment case built and NPV shown; Grid study run and the Grid code table shown; Excel export re-imported as a project.
- [ ] Each new step fails on a deliberate break (shown on a scratch branch, with the run quoted in the PR).
- [ ] End to end: a dispatch of the scheduled job on the PR branch is green.

## Likely files

`pypsa-gui/frontend/scripts/template-journeys.mjs`, `pypsa-gui/frontend/scripts/template-journeys-lib.mjs`, `.github/workflows/test.yaml` (option 2 only).

## Comments
