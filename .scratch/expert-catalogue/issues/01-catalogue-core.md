<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 01 — Parameter catalogue core (01a): entries, asset types, guide files, `GET /api/catalogue`, range validation

Status: ready-for-agent
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
