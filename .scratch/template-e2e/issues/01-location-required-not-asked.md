<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 01 — Location is a required input, but no template asks for it and Run does not wait for it

Status: ready-for-agent
Owner: builder session_01G7qCumsdzuVnJQAD41wFxk (after #1586/#1594)
Size: S
Gated: UX; GK B (run gate)
Decided: TEMPLATE-GAPS-1 Q1 (a), a missing location blocks Run until it is set (2026-11-17)
Likely files: pypsa-gui/backend/services/flow_service.py (project section state, ~l.1214-1335); pypsa-gui/backend/services/run_gates.py (readiness); pypsa-gui/frontend/src/layout/NewProjectWizard.tsx (DcLoadForm); pypsa-gui/frontend/src/pages/hubDesign/cards/GenericSiteForm.tsx; pypsa-gui/frontend/src/components/site3d/SiteAnchorField.tsx
Journey fit: pick template → set up site (section 1) → readiness. Hands on to the profiles (6), which need coordinates, and to the readiness line in 9.

## What

Design §2.3 marks location **R** for every template. In the app (verified 2026-11-17, master 882d7114):

- No template sets a location; every bus has lon/lat 0.
- Guided section 1 says "The site has no location: no bus is placed" with **Open here ›**, which opens a drawer with Latitude (°) and Longitude (°) fields. So the user *can* set it.
- The creation forms do not ask for it: the DC form (IT load, PUE, backup mode, architecture) and the Generic Site form (site type, stages, connection…) have no location field.
- Section 9 says "Ready to run. 1 note: Project & site: The site has no location". The run starts without a location.

## Acceptance

- Q1 is answered. Then, either the missing location blocks Run like any other required input, or it stays a note and the design's **R** for location becomes **opt**. Section 1's state follows the answer.
- If it blocks: section 1 reads `incomplete` until a location is set, and the readiness line links to the same Latitude/Longitude drawer.
- A test covers each template's section 1 state with and without a location.
- (UX) If Q1 says block: New project asks for the location (DC form, Generic Site form, template cards), and the Readiness line links to the same Latitude/Longitude drawer.

## Comments

- Seen again 2026-11-20 (template walk, master 1f6905e3): DC, H2 and microgrid still open with 'The site has no location: no bus is placed' in Guided step 1 and as a note on Readiness and run.

- Seen again 2026-11-21 (template walk, master 6b421b06): IPP, concentrator and ammonia all open with "The site has no location: no bus is placed" in Guided section 1 (and "1 note" in the Expert header).

- Extended 2026-11-21 (gap review, `.scratch/gap-review-2026-11-21/README.md`, walk W1, master 76707f13): **the existing fix path does not clear the item.** On the DC staged template, section 1's "site has no location" item → "Open here ›" / "› Site coordinates" opens the lat/lon drawer. Confirming 52.52, 13.4 reads back "confirmed" and writes `site_anchor.json` (`services/site_scene/anchor.py`). The item and "1 to check" stay, because `site_location_missing` in `flow_service._presence` (~l.1002) reads Bus x/y, never the site anchor. Design.md §2.4 J12 says buses inherit the site location. Added acceptance for this issue:
  - [ ] Confirming the site coordinates in the section-1 drawer clears `site_location_missing`: either the flow fact reads the anchor, or confirming writes the anchor to the buses that have no position (J12). One rule, tested both ways.
  - [ ] End to end in the real app: DC staged › section 1 › Open here › confirm coordinates → the item disappears, section 1 turns complete, and Run's note list no longer names the location.
