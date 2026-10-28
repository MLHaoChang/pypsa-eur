<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 03 — Export picker in Hand-off › Exports, and a chat tool

Status: ready-for-agent
Type: task
Blocked by: 02
Gated: yes (UI; UX review with screenshots)
Model tier: sonnet
Size: S

Read [the spec](../spec.md) first, owner decision RW-TABS-1.

## What to build

- A "Results workbook (Excel)" choice in the one Export chooser (Hand-off › Exports, owner decision SAVE-1). It shows the views it will include (the selected cards' views, pre-ticked), lets the user tick further tabs or "All result tabs", and downloads `/api/results/workbook.xlsx`. Guided and Expert both reach it.
- A chat tool `export_results_workbook(views?, all?)` that builds the same file and delivers it like `export_asset_results`, with its catalogue entry, error kinds and the inventory regenerated.

## Acceptance

- [ ] UX OK on Guided and Expert screenshots of the picker.
- [ ] The chat tool and the UI produce byte-identical sheet contents for the same view list.
- [ ] test_loadflow_navigation.py and test_openai_tool_selection.py pass after the catalogue change.
