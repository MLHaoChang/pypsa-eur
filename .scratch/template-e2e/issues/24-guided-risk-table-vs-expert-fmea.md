<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 24 — Guided risk table disagrees with Expert FMEA and the report

Status: ready-for-agent
Source: decide-report-journeys.md (walk 2026-11-17, master 0ab07339)
Severity: misleads
Journey fit: results: numbers must agree between Guided, Expert and report.

## What

Cost, LOLE and the top risk agree across all three on every template (PASS). The Guided risk table does not: DC gensets EUR 0/yr against EUR 760/yr in Expert FMEA, genset_new (EUR 12,529) only in Expert; microgrid diesel EUR 0 against EUR 302 k each; hospital genset_1..3 at EUR 0 and critical_feeder omitted; ammonia 'Nothing to show yet' while Expert lists EUR 17,915 and Improve says grid import is 100% of the risk. Raw names show (genset_new, diesel_1, critical_feeder, weak_flexible; see 05, 11). Hospital: the 50% must-stay-on share is stated only in section 4, never in results or report; Expert LCOE EUR 413/MWh has no caveat.

## Acceptance

- Guided risk rows equal Expert FMEA rows.
- Results state the critical share and MW kept on.
- Implausible LCOE carries a caveat.

## Triage (2026-11-20, master b7e5dee3)

Checked by code reading only; the app was not re-run. Acceptance as above: Guided risk rows equal Expert FMEA rows, results state the critical share and MW kept on, implausible LCOE carries a caveat. Overlaps the FMEA-unpriced work already merged (#1662): re-check the EUR 0 rows in the app first. Gated: GK A. Blocked by: none.
