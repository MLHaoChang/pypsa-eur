<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 07 — Preset mechanism, generic offered rows and the DC presets

Status: ready-for-agent (blocked by 05; Opus; GK B TDD plan first)
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

## Comments

### 2026-11-11: triage

Checked on master: `services/finance/business_case.py` and `project_templates/business_case_presets/` do not exist; 01 and 02 are done (#1322, #1320); business-case 04 is in progress in another session and was left alone. Verdict: ready-for-agent, held by its Blocked by line. Likely files, tests and acceptance are in the body.

### 2026-11-12: GK B plan GO (50), conditions P1–P12

Answers: Q1 `default_units` required, key set = `default_keys`', `default_unit_mismatch` at
`lines[i].default_keys[<col>]`, never fills. Q2 add `MW`, typed only. Q3 `linked_share` +
`linked_share_by_year` here; `kw_per_gpu` deferred to BC 08. Q4 variants
`eh_datacenter.gpu_cloud@1`, `eh_datacenter.colocation@1`; R3 a typed MWh cost_recovery line.

- P1 hash: hint, range_low/high never in the digest; linked_share(_by_year) only when set;
  default_units only beside non-empty default_keys. Pinned digests byte-equal.
- P2 hint display only, revenue only, currency never converted; equal cashflows/pnl/kpis.
- P3 linked_share finite in [0,1], linked lines only, by_year inside horizon, cell wins, None = 1.
- P4 composes with G3 drop: series × share × (1 − lost share), by hand; mutants fail.
- P5 MW only on capex typed lines; else `unit_not_allowed`; update every Unit enumeration.
- P6 run-time unit mismatch on a fixture pack refuses, never fills; currency/money-year skips unchanged.
- P7 JSON loaded on call; broken file raises at preset(), models import; unknown key/template/variant refused; version and pack pin checked.
- P8 pinned pack 2026-11-10 byte-equal by manifest hash; unpinned `load_defaults_pack()` callers listed.
- P9 no unsourced default (eh_site maintenance/insurance/property tax; DC staff/network); each default has source, range, assumed, pack stamp.
- P10 `default_unit_mismatch` must not trip the error-kind manifest tests.
- P11 every TEMPLATE_META id's sidecars byte-equal; business_case_presets/ shipped by pypsa-gui.spec:65.
- P12 mutants on presets.py and touched model/engine lines, plus the named hand mutants.
