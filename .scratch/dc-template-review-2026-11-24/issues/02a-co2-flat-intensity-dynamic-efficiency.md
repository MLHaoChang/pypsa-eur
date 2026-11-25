<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 02a The flat CO₂ intensity divides by the static efficiency

Status: needs-triage
Blocked by: none (02 PR 1, #1987, introduced the shared helper)
Source: GK A (37) on #1987 @bf7783c3, a follow-up that did not block the OK.
Severity: minor
Model: sonnet. Review: GK A.

## Problem

`services/co2_override.flat_t_per_mwh` is the one NaN fallback for a generator's CO₂ per MWh out, shared by the Emissions view and the CO₂ limit goal's cap row. It divides the carrier's intensity by the generator's static `efficiency`. PyPSA's own `primary_energy` row, and the cap's fallback before #1987, use the time-varying `efficiency` (`generators_t.efficiency`) where one is set.

## User-visible impact

For a genset whose efficiency varies over time, the Emissions figure and the cap row's fallback count its CO₂ at the static efficiency, so they can differ from what the LP constrains.

## Acceptance criteria

- [ ] `flat_t_per_mwh` (or its callers) uses the dynamic efficiency per snapshot when one is set, for both readers (Emissions and the cap row).
- [ ] Test: a genset whose efficiency varies over time; Emissions' tonnes equal the cap row's left-hand side at the solution.

## Likely files

`services/co2_override.py`, `services/results/emissions.py`, `services/solver/co2_cap.py`, `tests/test_dc_co2_cap.py`.

## Comments
