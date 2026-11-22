<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 22 Business-case run: `network_busy` reads as "the investment case is out of date"

Status: done
Blocked by: none
Source: gap review 2026-11-21 (`.scratch/gap-review-2026-11-21/README.md`), walk W2.
Model: sonnet. Review: GK B.

## Problem

"Run the business case" was refused three times with "the investment case is out of date (the project changed since it was run)", once straight after re-running the investment case. `GET /results/business_case` showed the real reason: `network_busy`, from the 2 s network-lock wait (`routers/results.py` `_IC_LOCK_WAIT_S = 2.0`, ~2286). The text "Fix the 3 refusals above" also stayed on screen after those refusals were fixed (`BusinessCaseRun.tsx`).

## User-visible impact

The user re-runs the investment case in a loop to fix a refusal that is not the problem.

## Acceptance criteria

- [ ] `network_busy` reaches the UI as its own reason: "The project is busy with another job; try again in a moment", with a Retry. It is never mapped to the stale message (`services/business_case/runner.py` `StaleInvestmentCase`). Tested on the route and in a vitest.
- [ ] The refusal summary is computed from the current refusals and disappears when there are none.
- [ ] End to end in the real app: hold the network lock (start a run), click Run the business case, and see the busy reason. After the run, Run succeeds.

## Likely files

`pypsa-gui/backend/routers/results.py` (`_bc_read_solve`), `pypsa-gui/backend/services/business_case/runner.py`, `pypsa-gui/frontend/src/pages/results/investment/BusinessCaseRun.tsx`.

## Comments
