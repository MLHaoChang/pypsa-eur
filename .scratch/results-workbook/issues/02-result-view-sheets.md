<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 02 — One sheet and native charts per result view, over the whole horizon

Status: ready-for-agent
Type: task
Blocked by: 01
Gated: yes (results data; GK B, GK A for the adequacy, island and grid-forming views' numbers)
Model tier: opus
Size: L

Read [the spec](../spec.md) first, item 4 of "The workbook".

## What to build

Fill 01's sheet-builder registry for every result view the app's result tabs show (`frontend/src/pages/Results.tsx` `TABS`, `services/result_views.py`), except Overview/Summary/Investment case (01) and Asset Detail (the existing per-asset export covers it). Each sheet: a short key-results block, then the view's full data over every snapshot and investment period, then 1–3 native charts of its key results, chosen in one table in the code. Read each view's data from the same backend function its tab's route uses, so the workbook and the tab cannot disagree.

## Acceptance

- [ ] Every view in the registry has a test on a fixture run: the sheet's key numbers equal its route's payload; its charts reference its own cells.
- [ ] A view with no results for the run is listed in About as "no results", with no empty sheet.
- [ ] A table over Excel's row limit or the 25 MB cap is written at the coarsest resolution that fits, and the sheet says which.
- [ ] Multi-period runs show every period; nothing is limited to the first period.

## Out of scope

The UI and chat tool (03).
