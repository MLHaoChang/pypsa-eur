<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 01 — After a Re-run, Expert Results tables show the previous run's capacities

Status: needs-triage
Impact: high
Templates: IPP; Ammonia (Key capacities card only)
Found by: template walk, 2026-11-21 (master 6b421b06)
Shot: ../shots/ipp-e-capexp.png

## What

IPP template, Expert. Run LOPF (battery 32.2 MW built). Capacity Bounds › bess Max 50 → 20 MW (Properties confirms "Maximum capacity: Customised 20 MW"). Run LOPF again (7.5 s upper bound). Results › Summary then shows, side by side:

- Total system cost EUR -4.52 M (was -4.55) and New-build capital cost EUR 1.28 M per year (was 2.06): these follow the 20 MW cap (20 × 63.04 EUR/kW/yr ≈ 1.26 M).
- Key capacities: battery 32 MW, new build 32 MW; Capacity expansion › Build › Investments by asset: bess +32.2 MW, CAPEX EUR 2.0 M; "New-build CAPEX by component class" EUR 2.03 M per year. Those are the old run.

The same Build tab therefore says "Total new investment per year EUR 1.28 M" and "New-build CAPEX ... EUR 2.03 M per year". After a third solve (discount-rate edit, issue 02) the tables showed 20 MW, so the view catches up one solve late. I read the tables 2 s and again about 30 s after the solve ended; both showed 32 MW.

Ammonia shows the same lag in the Key capacities card: after the first Expert run it said "No capacities for this solve." (while Capacity expansion listed 4 built assets); after the second run it listed grid, wind, solar, NH3 import and battery with no new build.

## Why it matters

The user changes a bound, re-runs, and the sizing table says the bound was not respected. Nothing says which numbers are current.

## Suggested fix

Refresh every Results card and table from the same result id after a solve, and show the run time or id on the Results header so a stale view is visible.
