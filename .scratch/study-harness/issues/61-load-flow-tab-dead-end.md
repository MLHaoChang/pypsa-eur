<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 61 Results › Load flow offers the AC power-flow switch on a network with no lines, beside "Not applicable"

Status: done
Blocked by: none (related: 51, which sets a scope preset for the four grid templates only)
Source: gap review 2026-11-21 (`.scratch/gap-review-2026-11-21/README.md`), walk W3 (shots `e10-loadflow-switch`, `e13-lf`).
Model: sonnet. Review: UX.

## Problem

After a run on an Energy Hub project, the walk saw Results › AC power flow & grid › Load flow show two things at once. One is "Not modelled … Switch it on under What the Run computes › Load flow ›" (`frontend/src/pages/results/LoadFlow.tsx:56`, `RunAgain`, shown whenever AC power flow was not part of the run). The other is "Not applicable: the network has no lines or transformers". The tab never mentions the campus Grid study, which is where the load flow of these templates runs.

**Correction (2026-11-21, after the G3 builder's check on #1801).** The first version of this issue said the Solver & mode switch was greyed out. That does not hold: `GET /api/projects/{name}/flow/scope` reports `grid.load_flow` as `available` on every project the walks made. `flow_scope.option_status` lifts the `NOT_YET` row through `run_separability.runnable("loadflow")`. What stands is the contradictory pair of states, and the missing pointer to the Grid study. Only one walk observed the pair, on the walk's "GR-W3 DC grid" project, which had also had a raw `.xlsx` re-import. So reproduce it first on a fresh data-centre project. If the "Not applicable" state does not appear there, close this as not reproduced.

## User-visible impact

The user is told to switch on a computation that cannot apply to this network (it has no lines), and is not told where the site's load flow is.

## Acceptance criteria

- [ ] First, a reproduction on a fresh data-centre project (or a vitest with a branchless network), seen showing both states.
- [ ] When the network has no lines or transformers, the tab shows only "Not applicable" with its reason, never "Not modelled" and the switch as well.
- [ ] In that case, when the project has a campus/Grid study, the tab offers "Run the Grid study ›", which opens that section.
- [ ] Networks with branches (IEEE 14 etc.) keep today's switch and link.
- [ ] End to end in the real app: data-centre template › Run LOPF › Results › Load flow shows one state and the Grid study link; follow it and reach the Grid code & connection table. On IEEE 14 the AC power flow switch still works.

## Likely files

`pypsa-gui/frontend/src/pages/results/LoadFlow.tsx`, the `/flow` scope hook it can read (`frontend/src/layout/flowSections.ts` or the scope API module), `pypsa-gui/backend/services/flow_scope.py` (read only).

## Comments
