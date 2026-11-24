<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 03 DC template gensets count CO2 per MWh of fuel as per MWh of electricity

Status: needs-triage
Blocked by: none
Source: DC template review 2026-11-24 (`../README.md`). Stage 3.
Severity: major (a wrong number on the axis the owner wants to constrain)
Model: sonnet. Review: GK A.

## Problem

`build_eh_datacenter` (`project_templates/eh_templates.py:155-190`) gives the `gas` carrier `co2_emissions` 0.20 t/MWh (a fuel-energy intensity) and adds the gensets with no `efficiency`, so PyPSA's default 1.0 applies. Emissions are `dispatch × co2_emissions / efficiency` (`services/compare/emissions.py`), so each MWh of genset electricity is counted at 0.20 t, roughly a third of what a gas genset at the spec's own heat rate emits (`GensetSpec.heat_rate_mwh_fuel_per_mwh_el` default 2.8, `models/flex_archetypes.py:39`; that heat rate is used only for the backup costing in `services/adequacy/dc_backup.py:1400`). The grid carrier's 0.35 t/MWh is one hard-coded figure for every location and year.

## Acceptance criteria

- [ ] Genset `efficiency` = 1 / heat rate (from the load spec's genset entry, else the library default, marked assumed); a test checks t/MWh_el = carrier intensity × heat rate.
- [ ] The grid intensity is a per-period input with its source shown as assumed; the Emissions view names it.
- [ ] The marginal cost and the CO2 count read the same efficiency (no double count with `co2_price`).

## Likely files

`project_templates/eh_templates.py`, `services/adequacy/dc_backup.py`, `services/solver/assumptions.py` (co2 price), `services/compare/emissions.py`.

## Comments
