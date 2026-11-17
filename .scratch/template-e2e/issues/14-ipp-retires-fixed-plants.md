<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 14 — IPP run retires both existing plants and reports a negative cost

Status: needs-triage
Impact: high
Templates: eh_ipp_hybrid
Found by: Expert-mode walk, 2026-11-17 (master 0ab07339)

## What

"2 assets retired · saves €0.00" (wind_farm 60 MW and pv_plant 41.7 MW to 0), "Total system cost -€4.55 M", Key capacities "No capacities for this solve". Readiness warning shows the raw id "commercial.arbitrage_loop_via_storage" (import -31.44, export 184.46 per MWh). Guided says "sized by the study".

## Why it matters

The user's owned plants vanish from the result; a profit is labelled a cost.

## Suggested fix

Do not retire fixed plants; label a profit as profit; plain-language arbitrage warning.
