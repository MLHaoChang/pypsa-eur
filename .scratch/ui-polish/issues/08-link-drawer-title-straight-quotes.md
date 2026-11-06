<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Guided drawer title for a Link shows 'grid_import' in straight quotes

Status: needs-triage
Type: wording
Found by: UIP-01 (#1195), relayed by the coordinator.

## Problem

The title wraps the component id in straight quotes (`'grid_import'`), a repr-style quote. Use plain words or curly quotes “grid_import”.

## Where to look

The Guided field drawer title for a Link (`pypsa-gui/frontend/src/layout/properties/`; `AssetFields.tsx`, `openTimeSeriesFor`'s Guided drawer).
