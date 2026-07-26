# Design: chat `compare_scenarios` + full UI navigate

**Status:** approved  
**Date:** 2026-07-26

## Goals

1. Dedicated chat tool for A-vs-B scenario comparison (structured deltas).
2. Agent can open any main panel, Results sub-tab, bottom asset tab, and the compare rail from user commands.

## `compare_scenarios`

- Inputs: `project_a`, `project_b`, `focus` (overview|capacity|dispatch|economics|emissions|prices|curtailment|lost_load|storage_cycling|all), `open_compare_rail` (bool).
- Reads both `/results-summary` payloads without activating either project.
- Returns headline KPIs + focus section + `delta = b − a` where numeric.
- When `open_compare_rail`, also emits `ui_event` navigate → Results + rail + A/B + compare tab.

## `ui_open_panel` → navigate (enhanced)

Optional: `results_tab`, `bottom_tab`, `compare_rail`, `compare_a`, `compare_b`, `compare_tab`.

SSE: after tool success, if result has `_ui_event`, emit `ui_event` frame. ChatPanel applies to uiStore; Results / BottomPanel / CompareView listen for tab requests.

## Mapping

Panel ids map to `SlidePanel` / canvas / palette / import-export. Results tabs use Results ids (`capex`, `dispatch`, …). Bottom tabs use BottomPanel names (`Buses`, `Generators`, …).
