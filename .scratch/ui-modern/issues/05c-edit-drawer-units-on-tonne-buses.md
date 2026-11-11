<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# The edit drawer shows MW / MWh on tonne buses

Status: needs-triage
Type: bug
Model: Sonnet. Reviewer: GK B, UX.

Found in GK B (44)'s review of PR #1353 (R6).

## What is wrong

The Guided section list and the 3D scene take a size's unit from its bus: t/h (t for a store) on a tonne bus. The edit drawer's field schema is static MW / MWh (`pypsa-gui/backend/services/asset_schema/fields.py`, around lines 191, 292 and 364). On `eh_concentrator_staged` six assets and one load therefore read t/h or t in the list and MW or MWh in the drawer that "Edit ›" opens.

## Done when

- The drawer's unit for a size field follows the component's bus (`process_products.bus_unit`), as `flow_service._size_unit` does.
- A test on `eh_concentrator_staged` pins the drawer's unit for one asset on a tonne bus and one on an energy bus.
