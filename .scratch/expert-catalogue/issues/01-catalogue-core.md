<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 01 — Parameter catalogue core: entries, asset types, guide files, `GET /api/catalogue`, chat finder

Status: ready-for-agent
Type: task
Gated: yes (GK B; UX (23) OKs the plain-name table and guide drafts)
Spec: [spec.md](../spec.md) §3, §6.2, §7, §10.1
Depends on: none (the harness tool block waits for #1575, #1590, #1586, or rebases onto them)

## What

Turn `services/asset_schema/` into the one Parameter catalogue (spec §3.1):

1. `FieldSpec` gains `id`, `scope`, `plain_name` (from `label`), `short_help`, `depth` (`key` replaces `key_param`; keep `key_param` as a derived property until slice 03), `range`, `default {value, source}`, `functions`, `aliases`, `options_plain`, `related`, `former_ids`. Groups move to the nine plain groups of spec §3.4 (keep the old ids as aliases on the wire until slice 03).
2. `asset_types.py`: `AssetTypeSpec` rows moved from `frontend/src/utils/assetTypes.ts` `TYPES` (data only), with `icon`, `symbols`, `ports`, `groups`, `functions`, `only_for`, `palette`, `colour` (token name).
3. `settings.py`: entries for project-level settings, starting with the ~35 `FIELD_LABELS` keys and `OptimizationLevers` (`models/energy_hub.py:151`); `EH_CUSTOM_COLUMNS` reads its kinds from here.
4. `functions.py`: the `Function` enum (dispatch, expansion, load_flow, short_circuit, grid_compliance, adequacy, finance).
5. `guide/<id>.md` for every entry, asset type, group and function (first drafts; plain words; one "Example").
6. `catalogue.py` + `routers/catalogue.py`: `GET /api/catalogue` (ETag = content hash, `?function=&depth=` filters), `GET /api/catalogue/guide/{id}`. `GET /api/network/asset_schema/{component}` becomes a view of it with today's shape.
7. Range validation in the Asset write path (REST and chat writes), refusals opening with the plain name.
8. Chat: `find_parameter` and `open_parameter` (navigate kind `parameter`) per spec §7.1; write-tool results gain `plain_name`; `component_discovery.py` and `attrs_doc.py` read text from the catalogue.
9. `frontend/src/catalogue/modelNames.generated.json` written by a backend script; a backend test checks it is current (the only frontend file in this slice).
10. CONTEXT.md: the terms of spec §1.

## Likely files

`pypsa-gui/backend/services/asset_schema/{fields,asset_types,settings,functions,catalogue,not_offered}.py`, `.../asset_schema/guide/*.md`, `routers/catalogue.py`, `routers/network.py` (route body only), `models/energy_hub.py` (`EH_CUSTOM_COLUMNS` source), `services/component_discovery.py`, `services/asset_schema/attrs_doc.py`, `harness/catalogue.py` (two tool blocks at the end, `TOOL_ROUTES`), `services/chat_tools.py` (dispatchers), `smoke/assistant_route_parity_declarations.d/claude-expert-catalogue-01.json`, `tests/test_catalogue_*.py`, `pypsa-gui/CONTEXT.md`, `frontend/src/catalogue/modelNames.generated.json`.

## Acceptance

- Spec §10.1 "Catalogue completeness" and "Validation parity" tests pass; coverage rule §3.5 with a shrink-only `KNOWN_GAPS` for settings routes.
- `catalogue.ids.txt` committed; the existing `test_asset_schema*.py` pass unchanged (or with shape-preserving updates named in the PR).
- Inventory `--check`, route parity and tool-schema tests pass.
- UX (23) OKs the plain-name table (one table in the PR body) against RAW-NAMES-1 and the owner's Q1 answer.
- PR-SIZE-1: guide text is content; if code exceeds ~1,500 lines, split the chat tools into 01b.

## Keeping both modes in step

The catalogue is mode-free: `depth: key` is the Guided set, everything else Expert depth. No mode-specific label or unit.
