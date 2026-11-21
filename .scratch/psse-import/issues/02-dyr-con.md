<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# PSS/E import 02: dynamic data (.dyr) and contingencies (.con)

Status: ready-for-agent
Type: task
Owner decision: IMPORTS-1, IMPORT-FLOW-1, HOLISTIC-1, RAW-NAMES-1, PR-SIZE-1
Gated: yes (GK A, gridspine)
Blocked by: 01 (PR #1652)
Files: `gridspine/ingest/psse_dyr_parse.py` (pure reader), `gridspine/ingest/psse_import.py`,
`gridspine/ingest/import_result.py` (`.con` on the psse spec only), tests under `tests/gridspine/`.

## What

- `psse_dyr_parse.py` reads `.dyr` records (`IBUS 'MODEL' ID con... /`, `@!` comments,
  records over several lines) with their file line. Models in `handoff/dyr_writer.DYR_CONS`
  get named parameters; others keep an ordered `values` list and count as unmapped by model.
  A non-numeric value, an unclosed quote or a missing `/` is a ContractError naming the line.
- `read_psse` given a `.dyr` matches (bus number, machine ID) to imported generators and stores
  the `psse_dynamics.json` sidecar:
  `{"format": 1, "source", "records": [{"line", "bus", "bus_name", "machine", "generator"
  (PyPSA name or null), "mbase", "bus_kv", "model", "parameters" | "values"}]}`.
  A second record for the same machine and model is refused.
- `read_psse` given a `.con` reads it with `contingency_list.read_contingency_list` onto the
  imported network; rows go to a `psse_contingencies.json` sidecar, unmatched elements to
  unmapped rows. No new `.con` parser.
- `.con` joins the psse `ImporterSpec` extensions (core asked for it).
- Sidecar keys go to the expert-catalogue lead before the PR opens; PR1 enum values stay.

## Comments

GK A (29) plan GO, 2026-11-17, with these conditions:

- Open question answered: a record on a bus with no generator (load, FACTS or other models)
  goes in the sidecar with `generator: null` plus a warning. The warning is one capped list
  with the true total and per-model counts, not one line per record (#1353).
- D1 Known model, wrong count: a `DYR_CONS` model with more or fewer CONs than listed is never
  zip-truncated or padded; it keeps `values` with an unmapped row "parameter count N,
  expected M". Test both directions.
- D2 Machine base: dynamic parameters are pu on MBASE. Each matched record carries `mbase`
  (and the bus kV) from the RAW, so no consumer assumes the system base. Test it.
- D3 ID matching: machine IDs compared as strings after stripping quotes and whitespace
  (`'1'`, `1` and `' 1'` match the same generator); `'A'` vs `'a'` follows PSS/E
  (case-sensitive unless cited otherwise). Test each.
- D4 Not only own-writer oracles: besides the dyr_writer and contingencies.py round trips, a
  hand-written `.dyr` with literal expected parameters for at least one model per family
  (GENROU-type, exciter, governor, a renewable REGC/REEC/REPC), and a hand-written `.con` with
  literal expected rows by bus number, including a renamed bus (RAW-NAMES-1) so the
  `psse_number` mapping is exercised.
- D5 Lone files: a `.dyr` or `.con` without a `.raw` is refused with ImportFormatError naming
  what is missing; two `.raw` files are refused. Test each. Adding `.con` to the psse spec must
  not make `for_extension` ambiguous with another importer; test that.
- D6 The `.con` path refuses verbs `read_contingency_list` does not know as unmapped rows with
  the line, never a silent drop. Count in = mapped + unmapped, by hand count.
- D7 Mutants: one per refusal, the count check (D1) and the ID normalisation (D3), each with
  `-x` against its own test; survivors in the PR body.
- The PR body carries a `Session:` line.

GK A (31) confirmed, 2026-11-18, that the D1–D7 plan GO above stands for the PR2 build.
