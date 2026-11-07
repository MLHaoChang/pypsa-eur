<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Guided drawer title for a Link shows 'grid_import' in straight quotes

Status: done
Type: wording
Found by: UIP-01 (#1195), relayed by the coordinator.

## Problem

The title wraps the component id in straight quotes (`'grid_import'`), a repr-style quote. Use plain words or curly quotes “grid_import”.

## Where to look

The Guided field drawer title for a Link (`pypsa-gui/frontend/src/layout/properties/`; `AssetFields.tsx`, `openTimeSeriesFor`'s Guided drawer).

## Comments

### 2026-11-07: triage sweep (FILL-1107)

- Likely files: `pypsa-gui/frontend/src/layout/properties/AssetFields.tsx` (`openTimeSeriesFor` Guided drawer title), `layout/flowNavigation.ts:164`.
- Blocked by: none.
- Model tier: sonnet.
- Gated: UX (12).
- Size: S.
- Plain words or curly quotes; add a test pinning the title.
