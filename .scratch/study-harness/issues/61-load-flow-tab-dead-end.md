<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 61 Results › Load flow sends Energy Hub users to a switch they cannot turn on

Status: ready-for-agent
Blocked by: none (related: 51, which sets a scope preset for the four grid templates only)
Source: gap review 2026-11-21 (`.scratch/gap-review-2026-11-21/README.md`), walk W3 (shots `e10-loadflow-switch`, `e11-checked`, `e13-lf`).
Model: sonnet. Review: UX.

## Problem

On an Energy Hub project (data-centre template, after a run), Results › AC power flow & grid › Load flow shows two things at once: "Not modelled … Switch it on under What the Run computes › Load flow ›" (`frontend/src/pages/results/LoadFlow.tsx:56`, `RunAgain`) and "Not applicable: the network has no lines or transformers". The link opens Solver & mode (`openPanel('scenarios', 'simparams')`), where Load flow is greyed out. `services/flow_scope.py:115` marks `("grid", "load_flow")` `NOT_YET` with `_NOT_HERE`, so clicking does nothing. The real load flow for these templates is the campus Grid study, and the tab never mentions it.

## User-visible impact

The user follows the app's own instruction and reaches a control that cannot be used. This is the "jump away and back" that design.md §1.4 removes.

## Acceptance criteria

- [ ] When `flow_scope` reports `("grid","load_flow")` as not available for the project, the tab does not offer the switch. It shows one state, the reason from the availability table, and, when the project has a campus/Grid study, "Run the Grid study ›", which opens that section.
- [ ] When the network has no lines or transformers, the tab shows only "Not applicable" with its reason, never "Not modelled" as well.
- [ ] The availability comes from `flow_scope` (the `/flow` scope payload), not a frontend copy of the table. A vitest covers the three cases: available, not available with a Grid study, and no branches.
- [ ] Grid templates (IEEE 14 etc.) keep today's switch and link.
- [ ] End to end in the real app: create from the data-centre template, Run LOPF, open Results › Load flow, follow "Run the Grid study ›", run it, and reach the Grid code & connection table. On an IEEE 14 project the tab still offers the AC power flow switch, and it works.

## Likely files

`pypsa-gui/frontend/src/pages/results/LoadFlow.tsx`, the `/flow` scope hook it can read (`frontend/src/layout/flowSections.ts` or the scope API module), `pypsa-gui/backend/services/flow_scope.py` (read only).

## Comments
