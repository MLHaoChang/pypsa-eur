<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 01: "Edit ›" on the island-control assumptions jumps to Expert Capacity bounds

Status: done

Done in #1673 (20728b0b).
Impact: high
Templates: Island Microgrid (any template whose ledger rows file to Assets with no panel)
Found by: control-strategy walkthrough, 2026-11-17 (master 9491878d)

## Repro

1. Projects › New project › From template › Island Microgrid. The project opens in Guided.
2. Open section 5, Assets & sizing. The Assumptions card lists 7 defaults.
3. Press "Edit ›" on "Island limits in the optimisation: off (assumed)".

## Actual

The app switches to Expert and opens Assets & sizing › Capacity bounds (diesel_new and bess_new min/max). None of the island settings are there.
The same happens for every island row: "Existing island", "Island control set-up", "Island unit settings", "Rate-of-change limit during the transition", "Island limits in the optimisation" and "Island break-even".
`GET /api/projects/{name}/flow` gives those rows `target: {section: "assets", entity: "<ledger item>"}` with no panel, component or field. `openFlowTargetInPlace` (`layout/flowNavigation.ts:398`) then falls through to `openSectionInExpert`.

## Expected

Each row's "Edit ›" opens the editor that holds the value, in Guided (as a field drawer over the section card, with "Back to Assets & sizing"):
- the island requirements and unit control modes: today in Grid study › Island; per the design, the Controls block of section 5;
- "Island limits in the optimisation": the switch that turns the island rows on.

A row with no editor yet says so in words and does not switch modes.

## Breaks

- `docs/agents/review-ux.md`, Navigation: "A bare `openFlowTarget` switches Guided to Expert". The in-place opener does the same here, through its fallback.
- `.scratch/project-workflow/design.md:402`, rule 7: an Expert-only control has a Guided route that names the field.
- EXPERT-GUIDED-1.

## Fix sketch

Give the island ledger rows a `panel` (and `field` where one exists) in `services/flow_service.py` (`_LEDGER_SECTIONS` / the row builder). Add a test that every `assumed_default` row with `section: assets` names a panel.
