<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 02 — Line-item model and validation

Status: ready-for-agent after 01 merges (GK B G-C1..G-C3, #1320 F1/F2; TDD plan to GK B first)
Type: task
Blocked by: 01
Gated: yes (GK B)
Model tier: opus
Size: M

Read [the spec](../spec.md) §3 (G1, G5–G9, G-C2) and §4, and GK B's conditions in
[01](01-design-plan.md) ## Comments, first. Send the TDD plan to GK B before
building (G-C8).

## Where

Build on #1320's branch `claude/dc-bc-02-inputs-model` @ `c04a2c76` (re-scope and
retitle that PR; GK B re-reviews it). Keep `BusinessCaseRefused` (kind + field
path), `require_runnable`, `CostDefault`, the `assumed` provenance,
`with_cost_defaults` and the defaults pack 2026-11-10 rows with their byte-equal
test. Move the DC classes (`GpuHourRevenue`, `ColocationRevenue`, `ItHardware`,
`Facility` per MW IT) out: they become rows of the DC preset in 07. GK B's delta
review of the re-scoped #1320 needs: the TDD plan first (G-C8); the 2026-11-10
pack rows unchanged, or re-proved by byte prefix; the F1/F2 tests carried onto
the LineItem columns; the `money_year` column (G-C1) in this slice's model.

## What to build

1. `models/business_case.py`: `LineItem` with the spec §4 columns (no tax column,
   G-C2; `money_year` column, G-C1); `BusinessCaseInputs` = `preset_id@version`,
   `receivable_days`, `lines`.
2. Closed enums: kind, category per kind (spec §3 table), unit, escalation (the six
   `ESCALATION_CLASSES` + `contract`), depreciation method. Price unit = currency
   per quantity unit; no conversion here (G5; conversions are 04's).
3. Validation (G9 kinds with `.field = "lines[<i>].<column>"`): `name_duplicate`,
   `category_not_in_kind`, `unit_not_allowed`, `quantity_missing`,
   `share_out_of_range`, `share_cycle` (G6: one level), `end_before_start`,
   `outside_horizon`, `year_out_of_range`, `indexation_missing`, `life_missing`,
   `rate_missing`, `price_not_established` (saved, blocks the run).
4. `preset_rule` (`requires`, `excludes`, `price_includes_energy`) as a field the
   model checks: `capex_missing:<category>`, `capex_not_owned:<category>`,
   `pass_through_price_includes_energy` (G7). One kind per refusal, no aliases
   (GK B 2026-11-10): #1320's `it_capex_not_owned` becomes `capex_not_owned` with
   the category in the field path and `pass_through_not_colocation` (never
   shipped) becomes `pass_through_price_includes_energy`; `it_capex_missing` and
   `facility_capex_missing` stay.
5. Linked sources are stored as `linked:<id>@<catalogue version>`; this slice
   checks the syntax only; 04 resolves them.

## Tests

- Round-trip of every column; `extra=forbid`.
- `Finite` on every float: an inf and a NaN test per column (#1320 F1).
- Every refusal names its kind and field path; a capex line without life, a share
  cycle, an excluded capex (#1320 F2), a pass-through under
  `price_includes_energy`.
- A preset default's `money_year` survives into the line (G-C1).
- The pack's previous version byte-equal (BC9).

## Not in this slice

Linked outputs and unit conversions (04), the engine (05), presets as data (07),
any route or UI.
