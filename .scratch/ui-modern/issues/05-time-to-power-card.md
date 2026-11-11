<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Time to power card on the KPI headline strip

Status: needs-triage
Blocked by: `.scratch/datacenter-grid-readiness/issues/27-time-to-power-fact.md`
Type: feature (follow-up to 03; owner decision TTP-1)
Reviewer: UX.

## What to build

Once the engine has a checked time-to-power fact, add its card to the data-centre set in `pypsa-gui/backend/services/results/headline_kpis.py` (`ORDER`, `CHECKED`) and its label to the strip's word map. No tile is shown before the fact exists (UX plan answer Q1, ui-modern 03).

## Comments
