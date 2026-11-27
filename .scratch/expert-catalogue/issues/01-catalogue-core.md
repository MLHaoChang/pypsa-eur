<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 01 — Parameter catalogue core (01a): entries, asset types, guide files, `GET /api/catalogue`, range validation

Status: done; 01a-1 #1634, 01a-2 #2061, 01a-2b #2064, 01a-3a #2078, 01a-3b (this PR)
Type: task
Gated: yes (GK B; UX (23) OKs the rename diff table and guide drafts)
Spec: [spec.md](../spec.md) §3, §6.2, §7.2, §10.1
Depends on: none. The chat side is issue 05 (01b).

## What

Turn `services/asset_schema/` into the one Parameter catalogue (spec §3.1):

1. `FieldSpec` gains `id`, `scope`, `plain_name` (from `label`, no unit inside), `short_help`, `depth` (`essentials` replaces `key_param`; keep `key_param` as a derived property until slice 03), `range`, `default {value, source}`, `functions`, `aliases`, `options_plain`, `related`, `former_ids`. Groups move to the nine plain groups of spec §3.4 (keep the old ids as aliases on the wire until slice 03).
2. `asset_types.py`: `AssetTypeSpec` rows moved from `frontend/src/utils/assetTypes.ts` `TYPES` (data only), with `icon`, `symbols`, `ports`, `groups`, `functions`, `only_for`, `palette`, `colour` (token name).
3. `settings.py`: entries for project-level settings, starting with the ~35 `FIELD_LABELS` keys and `OptimizationLevers` (`models/energy_hub.py:151`); `EH_CUSTOM_COLUMNS` reads its kinds from here; `_network_input_fingerprint` and the IC digest stay byte-identical (pin test, AS-02).
4. `functions.py`: the `Function` enum (dispatch, expansion, load_flow, short_circuit, grid_compliance, adequacy, finance).
5. `guide/<id>.md` for every entry, asset type, group and function (first drafts; plain words; one "Example"). Exactly one file each, no orphans; shipped as package data in the frozen app (test beside `test_packaging_requirements`).
6. `catalogue.py` + `routers/catalogue.py`: `GET /api/catalogue` (ETag = content hash, `?function=&depth=` filters), `GET /api/catalogue/guide/{id}` (`{id}` validated against the catalogue, never a path). Both are `not_project` rows in the viewer-role write-gate table, with route-parity and golden-coverage rows. The payload is project-free; tonne-bus units stay per asset (`size_units()`). `GET /api/network/asset_schema/{component}` becomes a view of it with today's shape.
7. Range validation (spec §3.2): one validator in every write path (REST single edit, `_bulk`, preview/apply, import, chat writes, Library applies); hard bounds refuse new writes only; engine `inf` accepted, NaN refused; stored out-of-range values load, solve and show flagged; refusals open with the plain name. Defaults are shown, never written.
8. `component_discovery.py` and `attrs_doc.py` read text from the catalogue.
9. `frontend/src/catalogue/modelNames.generated.json` written by a backend script; a backend test checks it is current (the only frontend file in this slice).
10. CONTEXT.md: the terms of spec §1.
10b. Template-e2e gap 05 (spec §3.6 table): `load_profile` and grid-study settings entries with `options_plain`; `internal: true` on asset types; templates ship a plain display name per component (ids stay internal, existing projects unchanged).
11. Rename diff table in the PR body (spec §3.6): every changed label against `FIELD_LABELS`, `FIELD_WORDS`, `CARD_GROUPS`, `ledgerFieldLabels()`, Results, legends, ledger and reports; frontend words that must follow move in slice 03, or the old word stays.

## Likely files

`pypsa-gui/backend/services/asset_schema/{fields,asset_types,settings,functions,catalogue,not_offered}.py`, `.../asset_schema/guide/*.md`, `routers/catalogue.py`, `routers/network.py` (route body only), `models/energy_hub.py` (`EH_CUSTOM_COLUMNS` source), `services/component_discovery.py`, `services/asset_schema/attrs_doc.py`, the write paths (`services/network_crud.py`, `services/network_bulk.py`, import and Library apply), `smoke/assistant_route_parity_declarations.d/claude-expert-catalogue-01.json`, `tests/test_catalogue_*.py`, `pypsa-gui/CONTEXT.md`, `frontend/src/catalogue/modelNames.generated.json`.

## Acceptance

- Spec §10.1 "Catalogue completeness", "Guide files", "Project-free payload", "Fingerprint pin", "Validation parity" and "Defaults never written" tests pass; coverage rule §3.5 with a shrink-only `KNOWN_GAPS` for settings routes.
- `catalogue.ids.txt` committed; the existing `test_asset_schema*.py` pass unchanged (or with shape-preserving updates named in the PR).
- Route parity, golden coverage and viewer-role gate tests pass.
- UX (23) OKs the rename diff table against RAW-NAMES-1 and the owner's Q1 answer.
- PR-SIZE-1: guide text is content.

## Keeping both modes in step

The catalogue is mode-free: `depth: essentials` is the Guided set, everything else Expert depth. No mode-specific label or unit.

## Comments

- GK B (63), OK on #1597 @887ab0d6, slice-01 conditions:
  - T1: state where template display names live. Native template byte-identity either holds, or each changed file is named and its fixture updated on purpose. An old project with no display name falls back to its id (test).
  - T2: `internal: true` hides a unit only from lists, palette and canvases; never from KPIs, balances, costs, the ledger, chat reads or reports. Test: a sum that includes `__lost_concentrate` is unchanged by the flag.
  - T3: `options_plain` is display only; the stored value stays the raw option (round-trip test). An unknown stored option reads as itself and is flagged, never blank or coerced.
- UX (23), OK on #1597 @887ab0d6: existing projects show the plain display name where one exists (a label beside the unchanged id); `internal: true` assets stay reachable in Results and reports as what they mean ("Concentrate shortfall").
- Agreed with homer-parity 04a and GK B (62): "Load growth starts from year" has default source `none`, required when a rate is set; the UI pre-fills the first period and saves it explicitly; the server's 422 stays.
- GK B (63), plan GO for 01a as three PRs, landed in order 01a-1 (core + route) → 01a-2 (settings, coverage, display names) → 01a-3 (range validation; last because it touches every write path). GK B (64) keeps the GO.
  - Ids: derived `<scope>.<slug(plain name)>`, pinned by `catalogue.ids.txt`; the pin test fails on any change to a derived id and tells the author to set an explicit `id` equal to the old one. Unique case-insensitively; a slug collision is refused, never suffixed or truncated (#1127).
  - T1 location: a dict keyed on (template id, component class, component id), served on the network meta payload; a user-added component with the same id under another class gets no name; delete and re-add of the same class and id keeps it (state in the PR); fallback test.
  - C1: ETag = hash of the exact response bytes per query; `?depth` and `?function` each change it; a stale If-None-Match gets 200 with the new body.
  - C2: `asset_schema/{component}` as a view is byte-equal to a full payload recorded before the move.
  - C3: guide ids are dict lookups only; unknown id, `../`, `%2e%2e` and empty all return the same 404 body.
  - C4: route parity, ACL policy, write-surface lock, golden coverage, route_chat_disposition and test_packaging_requirements run at the trial merge.
  - C5: `EH_CUSTOM_COLUMNS` derived from entries keeps its exact order; fingerprint and IC digest pinned before the move.
  - C6: load growth rate finite and > -100; the base year has no default and is required with a rate (422); same words as homer 04a/#1604.
  - C7: the coverage test asserts `KNOWN_GAPS` only shrinks, against a committed list.
  - C8: validate only the fields a write changes; an edit to another field of an asset already out of range is never refused (test).
  - C9: imports (legacy, PSS/E, Library apply of a stored project) load out-of-range values and flag them, never refuse or clamp; interactive edits, bulk, preview/apply and chat writes refuse. One test per path.
  - C10: ranges are in the stored model unit; a bus-unit-dependent field is checked in the stored unit, with a test on a non-MW bus (#1347).
  - C11: inf accepted only on fields whose engine default is inf (listed, tested); NaN refused everywhere.
  - C12: one mutant per write path that skips the validator, each killed by that path's own test; a redundant layer is mutated together with its sibling (#1127 M10).
- UX (24), OK on #1634 @701e2dea (01a-1): Generator `p_max_pu` keeps "Availability" and `p_min_pu` "Minimum output". For slice 03: a parity test for "Rating" (Line/Transformer `s_nom`) against today's "Capacity"; the `committable` plain name keeps "unit commitment".
- GK B (65), 2026-11-17 16:20Z, plan GO for DGS-EXTRAS-1 (claude/dgs-extras-fields, after #1634) on these conditions:
  - X1: the solve reads none of rated_mva, harmonic_*, sk_*, rx_*; a pin test sets each on a Load and a Generator and shows `_network_input_fingerprint` and the IC digest byte-identical (or a field that feeds a calculation is named and folded in under AS-02, appended only when present).
  - X2: one validator on every write path (POST, PUT, bulk/preview, chat edit tool, experiment copy, Excel input if it writes these classes); bulk and the chat tool refuse a negative sk_max_mva, a bad harmonic_source, NaN and inf like the single edit (#738); floats finite (#1320); bool refused.
  - X3: Generator/Load in PROCESS_CUSTOM_COLUMNS makes no reader treat a plain generator or load as a process unit; readers listed in the PR body; a test shows a generator with only the new fields is not a process unit.
  - X4: list every ensure_eh_column caller that can pass Load and what it now writes; test that untouched loads gain no column (save/load byte-identical).
  - X5: "" and null (and NaN after a netcdf/csv round trip) read as not set everywhere (drawer, exports, importer); round-trip test through save, reload and bundle export/import.
  - X6: harmonic_spectrum length bounded with a typed 422; no reader parses or evaluates it unsafely.
  - X7: the re-record is pinned: every class other than Generator and Load is byte-equal to the previous recording.
  - X8: the deferred sk_min ≤ sk_max cross-check is ticketed now (01a-3) and linked in the PR body.

- 2026-11-23 status sweep: landed in #1634 (347f55dd8c); #1804 (3cfc9173be); #1807 (3af79cda45); #1812 (a65c921c3c): catalogue core, routers and generated names are on master. Likely complete; PR-body rename table and range validators on every write path not checked.

- 2026-11-27, 01a-3b (C9 scope): every import that loads a network returns `range_flags` and `range_flags_total` (`ranges.import_flags`): netcdf, csv, matpower, legacy Excel, workbook preview and import, model import (PSS/E and the other converters). "Library apply of a stored project" has no route today; the nearest are `library_reference/apply` (a write, funnelled through the update validator) and `from_template`. The legacy folder import copies files and loads no network. A future import route calls `ranges.import_flags` on its candidate. Time-varying `*_t` min/max pairs are not flagged (R6, issue 11).

## Audit (2026-11-25)

- 2026-11-25 audit: #1634 in master log; asset_schema has catalogue.py, routers/catalogue.py, guide/, catalogue.ids.txt, modelNames.generated.json, but no settings.py and catalogue range is always None (catalogue.py:205).
