<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 04 The PUE's ambient temperature ignores the site's location

Status: done
Blocked by: none (te/01, site location in Guided, gives the anchor this reads)
Source: DC template review 2026-11-24 (`../README.md`). Stage 1.
Severity: major (cooling energy, peak facility load and so the PoC size are understated at a hot site)
Model: sonnet. Review: GK A + UX.

## Problem

The facility load is `IT MW × utilisation(t) × PUE(T(t))` (`services/dc_load_profile.py:125-129`), with `PUE(T) = pue_at_design + slope × max(0, T − pue_design_temp_c)` (`:102-104`). `T(t)` is `ambient_c` (`:92-99`): a cosine around `ambient_annual_mean_c` 10 °C, ±9 K seasonal, ±5 K diurnal (`models/flex_archetypes.py:153-155`), "temperate (Central European) site, no weather data" (`dc_load_profile.py:62`). The same fixed defaults feed the profile generator (`services/profile_gen/availability.py:52`).

Nothing sets these from the project's location or a weather year, and no Guided or Expert field shows them (`DcLoadForm.tsx` has IT load, PUE at design, backup and architecture only). A desert site with a 30 °C+ mean is modelled as if it were in Central Europe, so the PUE slope almost never engages.

## Acceptance criteria

- [x] With a site location set, the ambient series comes from the same weather source the renewable profiles use (or, offline, a climate-zone preset chosen from the latitude), marked with its source; without one it stays the assumed temperate default and says so.
- [x] The DC load card shows the ambient basis (mean, design-day peak) and the resulting peak PUE and peak facility MW; Expert shows and edits the three ambient fields.
- [x] Test: the same spec at a hot-climate preset gives a higher peak facility load and annual energy than at the default, by the slope × excess temperature.

## Likely files

`services/dc_load_profile.py`, `services/profile_gen/availability.py`, `project_templates/eh_templates.py` (`dc_load_preset`), `frontend/src/pages/hubDesign/cards/DcLoadForm.tsx`, `dcLoadForm.ts`.

## Comments
