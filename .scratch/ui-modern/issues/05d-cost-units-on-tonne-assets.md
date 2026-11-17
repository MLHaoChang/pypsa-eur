<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# The edit drawer's cost units on tonne assets

Status: needs-triage
Blocked by: 05c (PR #1448).
Gated: GK B, UX.
Type: bug

Follow-up to 05c (UX review of #1448). 05c makes the drawer's quantity fields (capacity, bounds, initial energy) follow the bus unit: t/h and t on a tonne bus. The cost fields still read per-energy units on an asset on a tonne bus: €/kWh, €/kWh/yr and €/MWh (overnight cost, fixed O&M, marginal cost, and the Store's per-energy costs). Their unit and scale (`display = stored × scale`) should follow the bus too (€/t, €/t/yr, €/(t/h)), without changing stored values.

## Done when

- A cost field on an asset on a tonne bus shows a per-tonne unit; an energy-bus asset is unchanged.
- A test pins one of each on `eh_concentrator_staged`; the scale for a tonne bus is decided first (no kWh scale applies).
