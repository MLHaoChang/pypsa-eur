<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Cost units on tonne assets in the BottomPanel and the chat rows

Status: done
Gated: GK B, UX.
Type: bug
Parent: 05d (#1565)
Blocked by: 05d merged.

The edit drawer, the creation form and Economics → Asset costs show per-tonne costs on a tonne-bus asset (05d). Two surfaces still read the class-level schema without an asset name and show EUR/kW, EUR/kWh and EUR/MWh:

- the BottomPanel table headers (`useAssetSchema(class)`; one table spans many assets, so a header has no single bus);
- the ParameterMemory suggestion chips and the chat confirmation rows (`valueWords` reads the class-level spec).

## Done when

- A cost cell, chip or row for an asset on a tonne bus reads per tonne (scale 1); an energy-bus asset is unchanged.
- A test pins one of each.

## Comments

**GK B4 plan GO conditions (05f, BottomPanel tonne units), and where each is met.**
1. The route (`GET /api/network/asset_schema/{component}/table_units`, named `tonne_assets` in the plan) returns `cost_units` (unit and scale) per tonne row, from the same `cost_units` the card uses, so a cell equals the card for the same asset and field.
2. An asset whose size bus is missing or has no known unit is listed in `unreadable`, never treated as energy; the table gives it no per-unit claim.
3. Route hygiene: fragments for the not-via-chat entry and the route-parity declaration (`ui-pointer`, decision TONNE-ASSETS-POINTER-1: the owner kept the light route with no chat read tool, GK B4 reviews it), and the route tests (`test_route_chat_disposition`, `test_project_route_acl_policy`, `test_write_surface_lock_policy`, `test_golden_coverage`) with `assistant_route_parity`, `assistant_control_matrix` and `assistant_inventory` `--check`.
4. The frontend query is keyed on the project and `network_revision` (`BottomPanel.tsx`).
5. Tests: mixed, all-energy (header unchanged from master), past the render cap, unreadable, revision refresh; guard mutants are listed in the PR body.
6. The memory refusal is pinned by `tests/test_memory_parameters.py::test_a_value_on_a_product_or_missing_bus_is_not_saved_or_offered`. ParameterMemory suggestion chips and the chat rows need no change: memory refuses a value on a tonne bus, so no chip or row carries a tonne asset's cost.

## Triage (2026-11-20, master 87cf52a7)

Checked by code reading only. Still true: `ParameterMemory.tsx` still reads the class-level spec. Blocked by 05d: merged (#1565), so unblocked. Gated: GK B and UX.
