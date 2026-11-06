<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# G2P-13: N-1 rows show the line only, with no per-transformer sub-rows

Status: needs-triage
Type: task
Found by: G2P-13 (#1194) PR body, "Skipped / risk".

## Problem

The N-1 line is one number. In the Expert table and the report it should list each site transformer as a sub-row, so the user sees which transformer outage costs the tonnes. Guided keeps the single line. Needs the per-Link split from `expected_outage_loss` (check whether it already returns it).

## Where to look

`services/results/grid_risk_production.py` (N-1 line, from `process_loss.expected_outage_loss` on the site-transformer Links), `GridRiskTab.tsx` Expert table and `chapters.GRID_RISK` in the report.
