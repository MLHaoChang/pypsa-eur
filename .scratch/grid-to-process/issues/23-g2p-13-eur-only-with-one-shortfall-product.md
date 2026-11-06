<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# G2P-13: lines 1–2 are priced in EUR only with exactly one shortfall product

Status: needs-triage
Type: task
Found by: G2P-13 (#1194) PR body, "Skipped / risk".

## Problem

With several shortfall products EUR is null because the campus CSVs do not name the product the lost tonnes are of. Either have the campus CSVs carry the product, or let the user choose the product the line is priced at, so EUR is shown with several products too.

## Where to look

`services/results/grid_risk_production.py`, the flag `several_shortfall_products`; the campus dip/restart CSVs (`campus_dip_trips.csv`).
