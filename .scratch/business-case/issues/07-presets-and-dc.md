<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 07 — Preset mechanism, generic offered rows and the DC presets

Status: needs-triage (blocked by 05; TDD plan to GK B first)
Type: task
Blocked by: 05
Gated: yes (GK B)
Model tier: opus
Size: M

Read [the spec](../spec.md) §4 (presets), §6.1–§6.2 and the DC spec §4–§5, §9
first. Send the TDD plan to GK B before building.

## What to build

1. `project_templates/business_case_presets/<template>.json` (versioned) and a
   loader: `preset(template_id) -> BusinessCaseInputs` of pre-filled lines with
   `preset_rule`; applying a preset copies rows the user then edits.
2. `eh_site`: no forced rows; the offered rows of §6.1 for the "add line" picker.
3. The DC preset (both DC templates): R1 GPU-hour (`price_includes_energy`;
   requires it_hardware + facility), R2 colocation (requires facility, excludes
   it_hardware, `capex_not_owned`), R3 pass-through (refused under R1,
   `pass_through_price_includes_energy`), facility and IT hardware capex, staff, network,
   maintenance, insurance, property tax, from #1320's pack rows with their
   `money_year` (G-C1). Refusal kinds per spec G7 (no aliases).
4. BCA-SCOPE-1 Q1: a `hint` (range, source, year) per revenue row, shown beside
   the empty cell, never a value.

## Tests

- Applying a preset then running gives the DC numbers #1320's model gave for the
  same inputs (parity).
- Every preset row validates under 02; every default has a source, range and
  `assumed`; no revenue row has a price.
- BC9: pack rows appended, previous version byte-equal; unpinned callers of
  `load_defaults_pack()` listed.
