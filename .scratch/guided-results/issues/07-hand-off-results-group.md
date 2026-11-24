<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 07 Results › Hand-off is an empty group: move Reports, Exports and the Specialist packet into it (R6)

Status: ready-for-agent
Blocked by: none (R0/R1 are on master: `pages/results/groups.ts` `CARD_GROUPS`)
Design: `.scratch/project-workflow/card-workspaces.md` §3.6 and §8 row R6. Owner decisions: RES-CARDS, CARD-WS, HOLISTIC-1.
Source: gap review 2026-11-21 (`.scratch/gap-review-2026-11-21/README.md`), design map.
Model: sonnet. Review: UX.

## Problem

`CARD_GROUPS` in `frontend/src/pages/results/groups.ts` ends with `{ id: 'handoff', label: 'Hand-off', question: 'What leaves the tool', tabs: [] }`. The Hand-off card shows nothing. Reports (`pages/ReportsPanel.tsx`) and the Export chooser (`pages/ImportExport.tsx` and the past-run export) live elsewhere, with duplicate entry points (design audit #10, #16). The planning → dynamics per-hour bundles are reachable only in `GridspinePanel`.

## User-visible impact

The results journey ends at a card that says "What leaves the tool" and offers nothing. Users look for reports and exports in two or three places.

## What (card-workspaces.md §3.6)

- **Reports** tab: today's Reports panel, the same component moved. Section 10's sidebar Reports row opens it.
- **Exports** tab: the one Export chooser (bundle, NetCDF, CSV, Excel, MATPOWER, the results workbook), the same component moved. The sidebar "Export…" opens Hand-off › Exports, not a standalone dialog. Per-view CSVs stay on their views.
- **Specialist packet** tab: on a planning → dynamics project, the dispatch source, ranked snapshots, assumptions ledger and per-hour bundles. On a project with a campus Grid study, the campus PSS/E bundles once `grid-compliance/23` lands; until then the tab says what it will hold.
- The last tab of every other group ends with "Next: Hand-off › Reports ›". Hand-off is always on (`locked_reason="Always on."`).
- Remove the duplicate entries the moves make redundant. No second Reports or Export component.

## Acceptance criteria

- [ ] `CARD_GROUPS.handoff.tabs` holds reports, exports and specialist_packet. `cardPlaceOf` finds them, and a vitest pins the group order and that no view sits in two groups.
- [ ] The sidebar Reports row and "Export…" open these tabs (vitest on the navigation targets). No other entry opens a separate Reports or Export surface.
- [ ] Guided section 10 "Results & reports" links to Hand-off › Reports.
- [ ] `template-journeys.mjs` still passes: its "Download report" step finds the button in its new home; update the selector only, never the assertion.
- [ ] End to end in the real app: on a data-centre project after a run, open Results › Hand-off. Generate and download the report, export the results workbook and the bundle. On a planning → dynamics project, download one per-hour bundle from Specialist packet.

## Likely files

`pypsa-gui/frontend/src/pages/results/groups.ts`, `pypsa-gui/frontend/src/pages/Results.tsx`, `pypsa-gui/frontend/src/pages/ReportsPanel.tsx`, `pypsa-gui/frontend/src/pages/ImportExport.tsx` (the chooser part), `pypsa-gui/frontend/src/layout/Sidebar.tsx` (entry points), `pypsa-gui/frontend/src/pages/GridspinePanel.tsx` (bundle list, reused).

## Comments

- 2026-11-24: built in 0cfee78d (Hand-off tabs, `ExportsTab`, `SpecialistPacketTab`, sidebar and `Export…` retargeted, groups vitest).
- 2026-11-24 end-to-end check (owner: CHECK FIRST), `template-journeys.mjs --mode short --template eh_datacenter` against the fixture backend on master `5e8cbcdc9`, a solved data-centre template (solve 38 s): PASS 19/19. "Download report (.docx) delivers a file" passed from its Hand-off home. "Input workbook (Excel)" delivered `eh_datacenter-short-input-workbook.xlsx`, 30,495 bytes, a valid zip with 20 parts and an About sheet. NOT delivered: "Results workbook (Excel)" after a Guided study (journey gap 30, xfail; already filed as `.scratch/template-e2e/issues/30-guided-run-results-workbook-empty.md`, needs-triage). Not checked: the bundle download and the per-hour bundle on a planning → dynamics project (the journey does not cover them; no live API/Vite app and no such project in this container), and no screenshots taken. Status therefore back to ready-for-agent until the owner decides on 30 and the missing checks.
