<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 10 No cost per tonne of ammonia (or other process product) in any read

Status: needs-triage
Related: 09 (Products tab), `template-e2e/28`.

## Problem

`process_view` gives output, buy-in and lost production per product bus; `lcoh` gives EUR/kg for hydrogen only (the figure the report quotes, `eh_report.py`). Nothing gives EUR/t for ammonia, so the Products tab (09) shows ammonia output with a note that no cost per tonne exists.

## Acceptance criteria

- [ ] One backend read gives the cost per tonne of each process product, from the solve's costs; no figure is recomputed in the frontend.
- [ ] The report and the Products tab show the same figure to the same rounding (a test compares them).
- [ ] Engine-adjacent: GK A and GK B review.
