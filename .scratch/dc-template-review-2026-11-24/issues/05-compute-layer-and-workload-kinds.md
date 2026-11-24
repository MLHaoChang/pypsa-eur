<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 05 Compute load to IT power: workload kinds refused, no compute inputs, a dead field

Status: needs-triage
Blocked by: none
Source: DC template review 2026-11-24 (`../README.md`). Stage 1.
Severity: major
Model: sonnet. Review: GK A + UX.

## Problem

The owner's chain starts from "assume the compute load". The template starts one step later, from IT MW:

- `DataCentreLoadSpec.utilisation` accepts `ai_training | inference | mixed | flat` (`models/flex_archetypes.py:21`), and its schema default is `mixed`, but `dc_load_spec` refuses everything except `ai_training` ("only 'ai_training' is modelled", `project_templates/eh_templates.py` in `dc_load_spec`). Inference and mixed campuses, a large share of new builds, cannot be modelled.
- There is no compute input (accelerators × power per accelerator, servers per rack, utilisation by job class) that derives IT MW; `rack_kw` only back-computes a rack count (`services/dc_it_energy.py:203`).
- `waste_heat_export` is accepted by the spec (`models/flex_archetypes.py:168`) and read nowhere: a silently ignored input.
- `ups_distribution_loss_share` is documented as inside PUE and not added (`dc_load_profile.py:217`); that is consistent, but the PUE check (`check_pue`) is the only guard and it is not shown.

## Acceptance criteria

- [ ] `inference` and `mixed` get hourly shapes (diurnal request load; a training/inference split) with their sources marked assumed, or the schema stops offering them; no accepted value is refused later.
- [ ] An optional compute block (accelerator count, kW per accelerator incl. host share, utilisation) derives IT MW per phase, shown next to the typed IT MW; typed IT MW still wins when given.
- [ ] `waste_heat_export` is either modelled (a heat bus with a sink and a price) or removed from the schema; a test fails on any spec field no code reads.

## Likely files

`models/flex_archetypes.py`, `services/dc_load_profile.py`, `services/dc_it_energy.py`, `project_templates/eh_templates.py`, `frontend/src/pages/hubDesign/cards/DcLoadForm.tsx`.

## Comments
