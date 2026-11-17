<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 02 — The connection limit cannot be changed in Guided; "Import efficiency" opens an empty Link form

Status: ready-for-agent
Size: S
Gated: UX
Likely files: pypsa-gui/frontend/src/pages/hubDesign/cards/SiteCard.tsx ("Connection limit" KPI ~l.145); pypsa-gui/frontend/src/pages/hubDesign/SectionView.tsx; pypsa-gui/frontend/src/components/FieldDrawer.tsx; pypsa-gui/backend/services/flow_service.py (assumption rows → drawer target, ~l.855-899)
Journey fit: set up site (section 3) → readiness. The limit feeds the energization check that sections 4 and 9 show.

## What

Design §2.3 makes "confirm limits and ramp" **R** for the data centre, and "connection MW" **R** for the generic site.

- Guided section 3 shows the limit as a read-only header ("grid_import, 40 MW" on eh_datacenter, "35 MW" on eh_concentrator_staged). No control changes it. The Generic Site creation form has "Connection limit (MW)", but the template projects do not.
- The ramp limit and the oscillation band are fine: their Edit opens a value / source / note editor in a drawer, and Expert "Grid connection" shows the same editor.
- The **Import efficiency (assumed)** row's Edit opens a blank *new Link* form (empty Name, empty Capacity, From bus unset) instead of the `grid_import` link.
- In Expert, the limit is the `grid_import` Link's Capacity in the property panel. Grid connection itself only offers the "Point of connection" picker.

## Acceptance

- Guided section 3 has a "Connection limit (MW)" field, per period on staged templates (energization schedule). Expert shows the same value under the same label.
- The Import efficiency Edit opens the existing import link's efficiency.
- A browser test sets the limit in Guided and reads it back in Expert.
- (UX) On staged templates the per-period limit is edited as drawer rows, with the energization check (stage peaks, §2.2) beside them.
- (UX) The section header drops the raw name `grid_import` and names the connection in plain words (05).
