<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 05 — Chat side of the catalogue (01b): `find_parameter`, `open_parameter`, plain names in results, display replacement in text and speech

Status: ready-for-agent
Type: task
Gated: yes (GK B)
Spec: [spec.md](../spec.md) §3.6, §7.1, §7.3, §10.1 "Chat"
Depends on: 01. Opens after #1575, #1590 and #1586 merge (GK B ruling on #1597).

## What

1. `find_parameter(query, asset_type?, function?)`: up to 8 entries with `assets` capped at 10 per entry plus `assets_total` and `assets_truncated`; reads only the active project under its ACL (viewers may read, VIEWER-ROLE-1). Read-only.
2. `open_parameter(entry_id, asset?)`: navigate event kind `parameter`; a `former_ids` id resolves to the current id (named in the result); an unknown id gets the typed refusal `unknown_parameter` (fragment in `pypsa-gui/tool-error-kinds.d/`). Safety tier: navigation.
3. Write-tool results gain `plain_name` beside each model name; refusals open with the plain name.
4. System prompt line: use plain names; the pilot-eval scorer counts raw names in replies.
5. Display replacement (spec §7.3): assistant text only, backticked tokens only, whole-word snake_case rule shared with the guard; never bare prose, fenced code, user messages or quoted user text; stored transcript unchanged; same function on the spoken path before TTS. A unit table of cases.

## Likely files

`pypsa-gui/backend/harness/catalogue.py` (two tool blocks at the end of `TOOLS`, `TOOL_ROUTES`), `services/chat_tools.py` (dispatchers), the write tools' result builders, the harness prompt, the pilot scorer, the TTS text path, `smoke/assistant_route_parity_declarations.d/claude-expert-catalogue-05.json`, `tool-error-kinds.d/claude-expert-catalogue-05.json`, the frontend display-replacement helper beside `ChatPanel` (after #1556) with its case-table vitest, tests.

## Acceptance

- Inventory `--check`, route parity, control matrix, tool-schema and error-kind manifest tests pass.
- Cap, ACL, `former_ids` and `unknown_parameter` tests; the display-replacement case table (text and TTS).

## Keeping both modes in step

`open_parameter` lands in the Expert panel or the Guided field drawer by the current mode; it never switches mode.
