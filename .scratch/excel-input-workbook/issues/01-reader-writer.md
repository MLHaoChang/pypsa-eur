# S1 pure reader and writer, gridspine/ingest/excel_workbook.py

Status: ready-for-agent
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
