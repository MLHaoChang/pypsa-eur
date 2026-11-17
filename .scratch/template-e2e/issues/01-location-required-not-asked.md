<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 01 — Location is a required input, but no template asks for it and Run does not wait for it

Status: needs-triage
Size: S
Gated: UX; GK B (run gate)
Blocked by: owner question Q1 in [decisions.md](../decisions.md#owner-questions)
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
