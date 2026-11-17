<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 02 — Parameter guide drawer, ⌘K parameter search, `openParameter`, raw-name guard over `src/`

Status: ready-for-agent
Type: task
Gated: yes (UX (23); GK B for the client and chat renderer)
Spec: [spec.md](../spec.md) §4.3, §6, §7.1 (navigate kind), §7.2
Depends on: 01. `CommandPalette.tsx` and `uiNavigate.ts` edits after THEME-LIGHT (b) merges; `ChatPanel.tsx` render-time names after #1556 merges.

## What

1. `src/catalogue/`: `api.ts` (fetch `GET /api/catalogue`, ETag), `useCatalogue.ts` (react-query, keyed by hash), `searchIndex.ts` (plain name, aliases, short help, guide text, model name; ranking per spec §4.3), `openParameter.ts` (one jump: asset panel or Guided field drawer, depth, unfold, scroll, focus, flash; through `withReturn`).
2. `src/catalogue/GuideDrawer.tsx`: right drawer, searchable, browse by asset type, group, function; entry layout of spec §6.2; Esc closes, focus returns.
3. ⌘K palette: a "Parameters" source (entries and asset values), Enter → `openParameter`, Shift+Enter → guide. In Guided, hits open the field drawer (EXPERT-GUIDED-1).
4. `uiNavigate.ts`: handle navigate kind `parameter` from `open_parameter`.
5. Chat render-time names: `ChatPanel` markdown renders any token exactly matching a catalogue model name as its plain name; links `guide:<id>` open the drawer.
6. `uiCopyGuard.test.ts` covers all of `src/` (spec §7.2) with `src/uiCopyGuard.allow.json` (shrink-only); fix `CapacityBoundsEditor.tsx:170` and "MC" (`PropertiesPanel.tsx:263`, only if #1565 has merged, else allowlist row naming slice 03).

## Likely files

`frontend/src/catalogue/*` (new), `components/CommandPalette.tsx`, `components/uiNavigate.ts`, `components/ChatPanel.tsx` (small hook-in), `uiCopyGuard.test.ts`, `uiCopyGuard.allow.json`, `pages/CapacityBoundsEditor.tsx`.

## Acceptance

- Search ranking and jump tests in both modes (spec §10.1 "Search").
- Guard passes over all of `src/`; every allowlist row has a reason and a fixing slice.
- Shots (Guided and Expert, light and dark): palette with parameter hits, guide drawer entry, guide browse by function, a Guided hit opening the field drawer.
- UX (23) told where it sits in the workflow before building (steward "UX reviewer").

## Keeping both modes in step

Search, the guide and chat navigation work identically in both; Guided lands in the field drawer, never switches mode on its own.
