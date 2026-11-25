<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 15: The project control preset has no picker in the app

Status: needs-triage
Impact: medium
Templates: Island Microgrid, Data Center Energy Hub, every template with an island sidecar
Found by: CS-B PR 3 (issue 02) build, 2026-11-25

## What

`POST /api/campus-electrical/{project}/island/preset` (gridspine `select_preset`) selects the project's control preset, but no frontend file calls it: only the assistant's tool does. Since issue 02, each converter's Controls block (section 5) says "From the project preset: Grid forming support." in plain words, with no link, because nothing in the GUI can change the preset (UX 29, rule #1252). With the chat panel off (no API key), the preset cannot be changed at all.

## Expected

- The island panel (Expert › Grid study › Island; Guided in place) has a project preset picker: the four presets in words, "None", and the units each one would leave out and why (`skipped` from the route).
- Then the Controls block's preset line gains its "›" to that picker, through withReturn.

## Breaks

- `.scratch/project-workflow/design.md:302` (DC-04 control presets).
- design.md §6 rule 12 (examples stay editable): a template's preset cannot be changed in either mode without the assistant.
