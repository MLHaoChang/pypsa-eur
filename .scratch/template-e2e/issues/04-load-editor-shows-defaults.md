<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 04 — The load editor shows profile-generator defaults, not the load; non-electric loads get MW; DC load inputs are creation-only

Status: ready-for-agent
Size: M
Gated: UX
Likely files: pypsa-gui/frontend/src/layout/PropertiesPanel.tsx (Load PROFILE block); pypsa-gui/frontend/src/components/series/ (load profile generator form); pypsa-gui/frontend/src/pages/hubDesign/cards/DcLoadForm.tsx; pypsa-gui/backend/services/flow_service.py (loads section)
Journey fit: set up site (section 4) → readiness → results. Peaks per period feed the connection check (3) and the reliability goal (8).

## What

- Guided section 4 lists each load with its peak ("residential … Peak load: 7.5 MW"). Its **Edit ›** drawer, like Expert's load property panel, opens the profile generator at its defaults: Archetype "industrial" and an empty "Peak MW". Neither the load's own shape nor its 7.5 MW appears, even though the panel header says "Profile Loaded · 168 pts". A user who edits here replaces the template profile without seeing it.
- Non-electric loads get electric units. eh_concentrator_staged's `shipments` is a concentrate load in t/h ("Peak load: 485.85 t/h"), yet its editor asks for "Peak MW" with an "industrial" archetype.
- Data centre: IT load, PUE at design, backup mode and power architecture are asked only in the creation form. After creation the probe found PUE, critical share, backup mode and architecture in neither Guided sections 4/5 nor Expert's load panel. §2.3 lists them as **P/opt** decisions in sections 4 and 5. In Expert, backup-related values (ride-through, UPS bridge, genset N+k) sit in the Scenarios "Grid study" panel instead (see 06).

## Acceptance

- The load editor opens on the load's current profile source (template / measured / generated) and peak. Changing the archetype is an explicit replace.
- The peak field takes the carrier's unit (t/h, t/d, MW_H2…).
- PUE, critical share, backup mode and (DC only) power architecture are editable after creation, in section 4 (load) or section 5 (backup and architecture), in both modes.
- (UX) Where each moved decision lands: IT load, PUE and critical share in section 4 (on the load row); backup mode and power architecture (DC only) in section 5. The unit is part of the field label ("Peak load (t/h)").
