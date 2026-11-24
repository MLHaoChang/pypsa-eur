<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 02 CO2 as a constraint for the data-centre templates (Guided and Expert)

Status: needs-triage
Blocked by: none (03 should land first so the cap binds on right numbers)
Source: DC template review 2026-11-24 (`../README.md`). Stage 3.
Severity: major
Model: sonnet. Review: GK A + UX.

## Problem

CO2 is reported (Expert `pages/results/Emissions.tsx`, `services/compare/emissions.py`) and can be priced (`co2_price`, null in both DC `solver_config.json`), but not set as a target for the DC templates:

- the templates add no `GlobalConstraint`; a cap exists only as a generic Expert "CO2 cap preset" in `frontend/src/pages/SolverSettings.tsx:1070-1121` (constant 1e8 t);
- the Guided goal card has no emissions goal; Guided shows no CO2 figure (no `co2`/`emission` in `pages/hubDesign/*.tsx` or `services/results/headline_kpis.py`);
- the frontier ignores CO2 (01).

## User-visible impact

"Stay under X t CO2 a year" (or a carbon intensity per MWh of IT) cannot be stated as a design goal, so the optimiser never trades gensets for storage or clean supply to meet it.

## Acceptance criteria

- [ ] A DC goal field "CO2 limit" (t/yr, or t/MWh of IT energy), empty by default, written as a `primary_energy` GlobalConstraint on `co2_emissions` per investment period; the existing `fuel_curve.co2_cap_refusal` guard still applies.
- [ ] Infeasible under the cap gets a typed refusal naming the cap and the least emissions reachable.
- [ ] Guided results show annual CO2 and the cap's shadow price (EUR/t) when it binds; Expert shows the same.
- [ ] Test: on the single-year DC template a cap below the unconstrained emissions binds and lowers genset energy.

## Likely files

`project_templates/eh_templates.py`, `services/reliability_goal.py` (goal fields), `routers/simulation.py` (`solver_config`), `pages/hubDesign/cards/GoalCard.tsx`, `services/results/headline_kpis.py`.

## Comments
