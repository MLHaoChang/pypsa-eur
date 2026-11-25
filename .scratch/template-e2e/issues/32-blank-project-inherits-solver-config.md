<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 32 A new blank project inherits the previous project's solver config (tariff, finance, value flows, periods)

Status: done
Blocked by: none. 15 ("study result and active project carry over", wontfix: not reproduced) is the same family; this is a reproduction with the evidence on disk.
Source: gap review 2026-11-21 (`.scratch/gap-review-2026-11-21/README.md`), walk W1 (shot `b9-ready`).
Severity: high (wrong inputs, silently)
Model: sonnet (diagnose first: `diagnosing-bugs` skill). Review: GK B.

## Problem

Right after creating "GR-W1 Hospital" (Your own site), the walk created "GR-W1 Blank" (New project › Blank, in a separate browser context, same user). The blank project's `solver_config.json` held the previous project's commercial tariff, the finance settings (`cod_by_asset` with `genset_1`, `site_pv`…), the value flows, and `multi_investment_periods: true`. The flow then showed section 7 `value_flows_invalid` ×14 ("asset 'genset_1' is not a Generator of the network…"). Likely path: blank create resets the network (`routers/network.py` `reset_network` ~671, `PyPSAService`) but keeps the in-memory solver config of the session's active project, and the first save writes it into the new project.

## User-visible impact

A user starting a fresh project gets another project's tariffs, finance and periods, and 14 errors about assets that are not there. In a shared workspace this leaks one project's commercial inputs into another.

## Acceptance criteria

- [ ] A failing test first: create a project with a non-default tariff and finance, then create a blank project in the same session. The blank project's `solver_config.json` equals the blank default.
- [ ] Every create path (blank, template, Your own site, import, duplicate) starts from its own source config, never the session's previous one. The test is parametrised over the paths.
- [ ] End to end in the real app: create "Your own site" (Hospital), then New project › Blank. Section 7 shows no inherited tariff and no `value_flows_invalid`, and section 1 shows a single period.

## Likely files

`pypsa-gui/backend/routers/network.py` (`reset_network`), the solver-config holder in `pypsa-gui/backend/services/` (the PyPSAService session state), `pypsa-gui/backend/routers/projects.py` (create and save), `pypsa-gui/frontend/src/pages/ProjectsHomePage.tsx` (createBlank ~320), `pypsa-gui/frontend/src/utils/projectActions.ts` (`resetBackendNetwork` ~951).

## Comments
