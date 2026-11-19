# S1 pure reader and writer: gridspine/ingest/excel_layout.py, excel_export.py, excel_import.py

Status: ready-for-agent; audit 2026-11-19: S1a merged (#1646); S1b open in #1732, ticket 04 in #1733
Blocked by: GK A ruling on pypsa inside gridspine/ingest (core session PR1)

See `../spec.md`.

## Comments

GK B plan GO (2026-11-17), with conditions:

- E1. Every workbook is untrusted, the hidden rows included.
  - The raw-attribute row is checked against the class's PyPSA input attributes plus an explicit allow-list of custom columns. Anything else is unmapped, never a setattr.
  - The scale row must be finite, > 0 and from the known unit table. Anything else is unmapped.
  - A test tampers with a hidden row of each kind.
- E2. Read safety.
  - openpyxl reads with data_only. A formula cell without a cached value is unmapped, never evaluated.
  - Caps on sheets, rows, columns and uncompressed size, each refused with a typed limit.
  - defusedxml stays in force.
  - A fuzz pass over truncated or garbage files gives only ImportFormatError or report rows.
- E3. NaN is refused, inf is allowed only where the engine accepts it, and bools only in boolean columns. Each is a report row with a reason.
- E4. Units per asset: a size on a tonne bus is exported and read with the bus unit. The round-trip fixture has a tonne-bus asset.
- E5. Snapshots round-trip exactly, single and multi-period, weightings included. Dates are ISO with no timezone shift.
- E6. Valid legacy fixtures give the same network (frames and `_network_input_fingerprint`) as today's /import/excel.
- E7. The copied injection-safe writer and `workbook_view.text` run one shared case table.
- E8. One killing mutant per guard (E1–E3), listed in the PR body.

Design lead (session_01G7qCuo4TtQpLUWDE5GKpny, 2026-11-17), on the expert catalogue:

- The source is `services/asset_schema/fields.py`. It is read through `schema_payload` / `fields_for`, not copied. `label` is the plain name, `unit_display` the unit, `scale` = display / stored.
- A plain name never carries its unit. Each sheet has a visible header row of plain names and a visible unit row under it.
- Raw names go only in the hidden mapping row.
- The column list is keyed by model name, so catalogue slice 01 (#1597) can swap the strings without a format change.
- Carrier stays "Carrier" until slice 01's rename table lands. Sheet names Nodes, Lines, Transformers, Generators, Storage, Loads, Links are fine; slice 01 lists them for UX OK.
- A column with no catalogue entry (eh_* and others) is a hidden column in the template, kept in the hidden mapping and in export round-trips.

GK B size ruling (2026-11-17, PR-SIZE-1): split S1.

- S1a: the layout, the writer and the new-layout reader, with E1–E5, E7 and E8. It is stacked on the import contract (#1600).
- S1b: the PyPSA-native and raw-sheet readers, with E6 parity against today's /import/excel.
- S2 (the /import/excel reroute) waits until both have merged.

Contract (#1600, project-io core):
- `source_format` is "excel".
- Sidecar keys are project file names: `user_ts.json`, and `solver_config.json`, which is partial and merged over the defaults on create.
- Report values are JSON-finite.

GK B 64 review of #1646 @df4497ad (2026-11-17): NOT OK, asks 1-5 plus minor items. Ask 1 (exact round trip): openpyxl writes %.16g, and a scaled cell (×1e-3, ×100) cannot be exact on its own, because two stored floats can show as the same float (0.001918771917354847 / 0.0019187719173548471 at 1e-3; 190446.44222042646 / 190446.44222042648 at 100). GK B ruling: option A is GO, with conditions:

- X1. repr float writing through a scoped, locked swap of openpyxl's formatter, restored in `finally`, with a test where the save fails.
- X2. A hidden "Exact values" sheet keyed by sheet, asset, parameter and time step, carrying the shown float. It applies only when the visible cell equals that float. Tests: a moved row, a sorted sheet, an edited cell.
- X3. Entries are untrusted. They are parsed strictly (repr form, finite, no bool or coercion). A malformed, duplicate or orphan entry is reported and ignored. A tampered entry moves a value by at most 1 ulp.
- X4. The hidden sheet is under the same caps and formula pass as every sheet.
- X5. The name is reserved. A visible sheet with that name is not read. A missing sheet falls back to rescaling the visible value.
- X6. `==` pins at every site (statics, series, weightings, discount_rate). One targeted mutant per new guard, in the E8 table.
