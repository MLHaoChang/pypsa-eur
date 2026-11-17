<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Cost units on tonne assets in the BottomPanel and the chat rows

Status: needs-triage
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
