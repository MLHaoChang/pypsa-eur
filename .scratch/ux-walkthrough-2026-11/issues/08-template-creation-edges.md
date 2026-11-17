<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Template creation: dev 404 text, duplicate name, one unverified project

Status: needs-triage
Type: finding (keyed walkthrough audit, UX-NEXT-1)
Journey steps: J1 Pick template
Severity: medium
Send to: template-e2e (decision inventory)

## What happens

- 3-Bus Tutorial, IEEE 14-Bus and Belgium Grid return 404 in a dev run and the toast says "Template import failed: Template '3bus' is registered but its network.nc is missing — run project_templates/_build.py to regenerate it." It names a developer path. Same as novice-path audit item 11 (still open in dev runs; packaged builds build the files) (`shots/tpl-404.small.png`).
- A template whose name already exists creates nothing: "A project named X already exists, so nothing was created" with "Open the existing project". No rename, no numbered copy.
- Unverified: a copy of "Industrial Hydrogen Hub" made by an interrupted first attempt opened as an ordinary network: no `eh_template.json`, section 1 offers "Start from an example site" and Run study is disabled ("Needs an Energy Hub site"). It could not be reproduced cleanly (the name was taken). template-e2e should check that a template created from the card gets its Energy Hub files.

## Done when

The 404 shows plain words; a duplicate name offers a numbered copy; template-e2e covers the hydrogen card.
