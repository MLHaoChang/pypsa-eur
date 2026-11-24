<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 12 Design the grid-connected control strategy (GFM vs GFL from grid strength), not only the island

Status: needs-triage
Blocked by: none (walkthrough cs/02 adds the Controls block to the asset form; this fills it with a design)
Source: DC template review 2026-11-24 (`../README.md`). Stage 8.
Severity: major
Model: opus. Review: GK A + UX.

## Problem

The control design that exists is for the island:

- `gridspine/static/gfm_tuning.py` (`recommend_gfm_settings`, `:190`) bisects one droop and one equivalent H per GFM converter on a one-bus frequency model (`gridspine/static/frequency.py:325`); served by `services/campus_island_tuning.py` and `/island/gfm-settings`.
- `gridspine/static/voltage_control.py` gives steady-state Q modes (q_fixed, pf, v_control, q_of_v) and the PoC P–Q chart (gc/02, done).
- The template's `island_config.json` fixes each unit's mode (BESS `gfm_droop`, PV `gfl_qu`).

Nothing decides, for the grid-connected case, which converters must be grid-forming and with what settings from the connection's strength: the SCR/ESCR study (DC 11) and the SSTI/UIF screens produce numbers, but no rule turns a low SCR (or a required inertia/fast-frequency service in the profile) into "these units GFM, these GFL, this current headroom, this PLL bandwidth bound", and no Guided step shows the choice.

## Acceptance criteria

- [ ] A control-strategy step reads SCR/ESCR at the PoC and per converter bus, the profile's required services (`_FREQUENCY_SERVICES`) and the island plan, and proposes per converter: mode (GFM/GFL), Q mode and droop, GFM droop/H range, current headroom, with each rule's source (generic engineering or the profile clause) shown.
- [ ] The proposal is applied with one action and written to the same place the island tuning writes; the exports carry it (10).
- [ ] Guided shows the proposal under Controls with plain words; Expert edits it (parity test).
- [ ] Dynamic confirmation stays in the external tools (GC-SCOPE-1); every row says "confirm in EMT/RMS".

## Likely files

`gridspine/static/gfm_tuning.py`, `voltage_control.py`, `pypsa-gui/backend/services/campus_island_tuning.py`, `services/adequacy/grid_readiness.py`, `frontend/src/pages/CampusIslandGfmSection.tsx`.

## Comments
