<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# The setup suggester ignores process-link draw and reads tonne loads as MW

Status: needs-triage
Type: task
Blocked by: none
Gated: **yes** (adequacy engine code)
Model tier: opus
Size: M

## Why

Owner decision G2P-10-SUGGEST-1 (option D, 2026-10-29). On `eh_concentrator_staged` the
Energy Hub setup suggester (`suggest_eh_setup` in
`pypsa-gui/backend/services/adequacy/eh_setup.py`) proposes the wrong tags:

- the site's electrical draw is on process links (crusher, mills, flotation), which the
  suggester does not read, so it does not find the critical bus from the draw;
- the product loads are in tonnes per hour, which it reads as MW.

Until this lands, the template sets `eh_critical` on the `site` bus itself, carries
`process_load: true` in its metadata, and `tests/test_eh_setup_suggest.py` skips templates
with that flag, as it skips `generation_only` ones.

## What to build

The suggester counts process-link electrical draw (bus0 power) as load on the bus it draws
from, and leaves non-electric (tonne) loads out of the MW totals.

## Acceptance

- [ ] `suggest_eh_setup` on `eh_concentrator_staged` suggests `site` as the critical bus.
- [ ] No suggested MW figure includes a tonne load.
- [ ] `tests/test_eh_setup_suggest.py` drops the `process_load` skip, and every template passes.

## File pointers

- `pypsa-gui/backend/services/adequacy/eh_setup.py` (`suggest_eh_setup`)
- `pypsa-gui/backend/tests/test_eh_setup_suggest.py` (`TEMPLATES`)
- `pypsa-gui/backend/project_templates/eh_concentrator.py` (`eh_critical`, `process_load`)
