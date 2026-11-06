<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# The setup suggester ignores process-link draw and reads tonne loads as MW

Status: done
Model: Opus
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

## Comments

### 2026-11-05: triage sweep (AA-FILL-2), role ready-for-agent

Was: `needs-triage`. Files: `pypsa-gui/backend/services/adequacy/eh_setup.py`, `tests/test_eh_setup_suggest.py`, the `eh_concentrator_staged` template (drop `process_load: true` and its skip). Blockers: none (owner decision G2P-10-SUGGEST-1 made). Reviewer: GK A. Note open PR #1142 touches `services/adequacy/eh_columns.py`; rebase onto it if it lands first.

### 2026-11-06: plan GO (GK A 14), conditions

1. Reuse: electric Loads from `site_peak.load_split`; process units from `process_uc.process_mask` (a string "False" does not count); one bus predicate, `site_peak.electric_bus`, shared by `load_split` and the suggester.
2. total_peak is the sum of bus_peak after the process draw is added.
3. Draw = p_nom × max p_max_pu on bus0, p_nom only; an unsized extendable process unit adds 0 MW and gets a note.
4. A figure that includes rated process draw says so in its reason.
5. No criticality_tier keyword (option D is draw only).
6. Sidecars regenerated; byte-identical for every TEMPLATE_META id except the dropped key.
7. Red-first tests: tonne Load out of every MW figure; process draw makes bus0 critical; non-electric bus0 adds no MW; "False" adds no MW; concentrator suggests `site`.
