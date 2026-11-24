<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 10 The grid-forming / grid-following choice does not reach PSCAD or PowerFactory

Status: needs-triage
Blocked by: gc/23 (the campus bundle must be downloadable first)
Source: DC template review 2026-11-24 (`../README.md`). Stage 7 and 8.
Severity: major
Model: opus. Review: GK A.

## Problem

- PSS/E: GFM converters are left out of the DYR on purpose (REGFM_A1/B1 CON order unverified, `gridspine/handoff/dyr_writer.py:60-66`, owner decision I8); their parameters travel by name in `gfm_models.csv` from the campus bundle (`gridspine/handoff/campus_bundle.py`).
- PSCAD: `gridspine/handoff/emt_pack.py` has no grid-forming reference at all (0 matches for gfm/grid-forming); the EMT pack does not say which converter is GFM or carry its droop/H.
- PowerFactory: `gridspine/handoff/dgs_writer.py` has none either; the control mode is lost.
- The AS-07 control-settings sheet (`services/control_settings.py`, rows incl. `gfm_droop`, `gfm_h`) is written only by the sizing loop runner (`services/adequacy/sizing_loop_runner.py:636-643`) next to the DYR; no route or UI serves it.

## Acceptance criteria

- [ ] The EMT pack and the DGS export each carry, per converter, its control mode (GFM droop / GFM VSM / GFL) and the tuned parameters (droop, H, current limit, Q droop) in a documented table the user's model template reads; a test reads them back.
- [ ] The control-settings sheet is a download beside the packs, with each row's status (set / ok / flagged / needs EMT).
- [ ] Writing REGFM records stays off until their CON order is verified (I8); the hand-off note says so.

## Likely files

`gridspine/handoff/emt_pack.py`, `dgs_writer.py`, `campus_bundle.py`, `pypsa-gui/backend/services/control_settings.py`, `routers/campus_electrical.py`.

## Comments
