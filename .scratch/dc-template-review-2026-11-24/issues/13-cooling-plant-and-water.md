<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 13 Cooling as a plant (chillers, free cooling, thermal storage, water), not a load share

Status: needs-triage
Blocked by: 04 (the ambient series drives chiller COP and free-cooling hours)
Source: DC template review 2026-11-24 (`../README.md`). Stage 9.
Severity: major
Model: opus. Review: GK A + UX.

## Problem

Cooling is a load: `cooling = (1 − critical_share) × facility` on the MV bus (`project_templates/eh_templates.py` `_apply_dc_profile`), or a fixed day shape in the default single-year build (`eh_templates.py:179-184`). PUE is the only cooling parameter. So the model cannot:

- shift cooling with chilled-water thermal storage (a cheap flexibility lever against a capped connection);
- trade water for electricity (evaporative vs dry coolers; no WUE, no water carrier or cost);
- use waste heat (`waste_heat_export` is read nowhere, see 05);
- size the chiller plant, whose MVA and motor starts also feed stage 5 (motor-start screen, transformer loading).

## Acceptance criteria

- [ ] An optional cooling block per phase: chiller capacity and COP(T), free-cooling threshold, optional chilled-water storage (MWh_th, losses), cooling type (dry/evaporative with litres per MWh_th); defaults marked assumed.
- [ ] When on, the cooling load comes from IT heat ÷ COP(T) instead of the PUE share, and PUE becomes an output reported per hour and per year next to WUE.
- [ ] Thermal storage dispatch is co-optimised; a test shows it moves cooling energy out of import-capped hours.
- [ ] Guided DC card shows cooling type and storage on/off; Expert edits all fields.

## Likely files

`project_templates/eh_templates.py`, `services/dc_load_profile.py`, `models/flex_archetypes.py`, `frontend/src/pages/hubDesign/cards/DcLoadForm.tsx`.

## Comments
