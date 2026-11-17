<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# The edit drawer's cost units on tonne assets

Status: ready-for-agent
Model tier: sonnet
Size: S
Likely files: `pypsa-gui/backend/services/asset_schema/fields.py`, `pypsa-gui/backend/tests/test_asset_schema_units.py`, `pypsa-gui/frontend/src/hooks/useAssetSchema.ts`, `api/assetSchema.ts` (the files 05c touched)
Blocked by: none (05c merged, #1448).
Gated: GK B, UX.
Type: bug

Follow-up to 05c (UX review of #1448). 05c makes the drawer's quantity fields (capacity, bounds, initial energy) follow the bus unit: t/h and t on a tonne bus. The cost fields still read per-energy units on an asset on a tonne bus: €/kWh, €/kWh/yr and €/MWh (overnight cost, fixed O&M, marginal cost, and the Store's per-energy costs). Their unit and scale (`display = stored × scale`) should follow the bus too (€/t, €/t/yr, €/(t/h)), without changing stored values.

## Done when

- A cost field on an asset on a tonne bus shows a per-tonne unit; an energy-bus asset is unchanged.
- A test pins one of each on `eh_concentrator_staged`; the scale for a tonne bus is decided first (no kWh scale applies).

## Comments

**2026-11-17 triage (SCALE-1).** The blocker 05c merged (#1448). Blocked by: none. The builder states the tonne scale in the plan to GK B before coding.
