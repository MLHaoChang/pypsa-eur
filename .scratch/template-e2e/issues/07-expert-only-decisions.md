<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 07 — Decisions found in neither mode, and Expert-only decisions Guided never names

Status: needs-triage
Size: M
Gated: UX
Likely files: pypsa-gui/frontend/src/layout/PropertiesPanel.tsx (Load and Generator blocks); pypsa-gui/frontend/src/pages/hubDesign/SectionView.tsx; pypsa-gui/backend/services/flow_service.py (section items); pypsa-gui/frontend/src/pages/SolverSettings.tsx
Journey fit: set up site and assets (sections 1, 3, 4, 5, 7, 8) in Guided. Hands on to readiness, which must name what is left.

## What

Design §3.5 lets Expert hold fields that Guided does not show. Guided must then name each field ("switch to Expert to edit") and never hide a blocker. With the assistant off, as in these journeys, naming the field is the only route Guided offers. The probe found:

**Found in neither mode** (on a load or asset form, in the panels, or in the drawers):
- load flexibility (curtailable / deferrable share), §2.1 section 4;
- critical share / criticality tier after creation (only eh_site's creation form has "Must stay on (%)");
- per-asset unit commitment (min load, min up/down, restart): Expert Grid study has only a global "Linearised unit commitment for the fixed-size gensets". Code check pending for the Haber-Bosch loop (`eh_ammonia.py`);
- parties / value split (§2.1 section 7): not checked; confirm or list.

**Expert only, and Guided does not name them:**
- model horizon of a template (periods, representative weeks vs chronological year): section 1's drawer shows them read-only;
- grid code profile and grid-code document, point of connection, export link, site time zone (section 3);
- scenario variants to compare (section 8);
- overnight cost and lifetime show a "Default" tag but no "assumed" mark, so finding 7 of decisions.md applies.

## Acceptance

- Each "neither mode" item is found (and the inventory corrected) or added to its section's form in Expert.
- Each "Expert only" item that matters for the run appears in Guided either as a decision (≤3 per card) or as a named line with "Open in Expert", per §3.5.
