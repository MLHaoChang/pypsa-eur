<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 08 Size harmonic filters, not only screen harmonics

Status: needs-triage
Blocked by: none (gc/01e adds series capacitors to the scan; read it first)
Source: DC template review 2026-11-24 (`../README.md`). Stage 5.
Severity: major
Model: opus. Review: GK A.

## Problem

Harmonics are screened, never fixed: the IEEE 519 TDD row (`services/adequacy/grid_readiness.py` `_tdd_row`, `RULE_TDD`), the IEC 61000-3-6 harmonic row (`_harmonic_row`) and the impedance scan with a resonance clearance (`gridspine.static.harmonic_scan`, `campus_electrical_service.py:283-309`, `pages/CampusHarmonicScanSection.tsx`). No code sizes a filter: no `harmonic_filter`, tuned or C-type branch kind, no Mvar/tuning-order/Q-factor output (grep of `services/` and `gridspine/`). A failing TDD row ends in a red cell with no remedy, and filter cost is absent from the capex.

## Acceptance criteria

- [ ] A filter design step for a failing TDD or resonance row: candidate single-tuned (and C-type for low orders) banks at the PoC or MV bus; for each, the fundamental Mvar (counted in the reactive balance with the STATCOM, AS-05), tuning order detuned below the target harmonic, Q factor, the capacitor's voltage/current/kvar duty per IEEE 18 / IEEE 1531, and the resulting TDD and scan.
- [ ] The scan re-runs with the filter in place and shows the shifted parallel resonance against the clearance.
- [ ] The chosen filter is an equipment row with capex (library row, marked assumed) and enters the exports (RAW fixed shunt / DGS ElmFilt / PSCAD), see 09.
- [ ] Engine test against a hand-calculated single-tuned filter.

## Likely files

`gridspine/static/` (scan), `services/campus_electrical_service.py`, `services/adequacy/grid_readiness.py`, `services/library/defaults_pack/`, `pages/CampusHarmonicScanSection.tsx`.

## Comments
