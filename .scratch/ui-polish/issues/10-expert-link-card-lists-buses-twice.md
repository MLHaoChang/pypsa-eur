<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Expert Link card lists From/To bus twice

Status: needs-triage
Type: bug (layout)
Found by: UIP-01 (#1195), relayed by the coordinator.

## Problem

The Expert card for a Link shows bus0/bus1 as From/To in two places. Show them once (Expert never shows less than Guided, but not twice).

## Where to look

Expert Link edit card, `pypsa-gui/frontend/src/layout/properties/` (`AssetFields.tsx`).
