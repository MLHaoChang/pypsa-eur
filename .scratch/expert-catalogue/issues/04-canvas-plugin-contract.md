<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 04 — Canvases on one visual language: `assetVisual()`, glyph set, tokens, asset types from the catalogue, function lens

Status: ready-for-agent
Type: task
Gated: yes (UX (23))
Spec: [spec.md](../spec.md) §5, §4.4 (canvas side), §10.1 "Plug-in contract"
Depends on: 01; 02 (lens store). Starts after THEME-LIGHT (c) merges and the ui-cohesion audit (session `session_01G7qCunZpDhHZhfRd2msNML`) has reported on the canvases; its findings feed the state list. Avoids #1578 (campus electrical) files.

## What

1. `src/canvas/visuals.ts`: `assetVisual(typeId, view, state)` from the catalogue's asset types; `readToken()` for code that cannot read CSS variables, refreshed on theme change.
2. `src/canvas/glyphs/`: the closed glyph set (today's `AssetIcon` glyphs from `TopologyCanvas.tsx:470`, `paletteIcons.tsx`, `assetTypeIcon.tsx`, plus IEC transformer, shunt, series reactor, switch) in an exhaustive `GLYPHS` record.
3. SLD (`pages/TopologyCanvas.tsx`, `pages/topologyAssets.ts`, `pages/topologyEdges.ts`): nodes from `assetVisual`; one handle per declared port; category if/else chains and `CARRIER_BUS_COLORS` / `CARRIER_LINK_COLORS` replaced by tokens.
4. Map (`pages/MapCanvas.tsx`): one generic divIcon builder from glyph + token; `CATEGORY_STYLE` goes.
5. 3D (`site3d/assetLibrary.ts`, `site3d/layout.ts`): `APPEARANCE` keyed by the catalogue's `symbols.site3d`; unknown types fall back to the category block + glyph label.
6. States in all three views (selected, Assumed present, candidate, out of service, needs input for the lens) and result bands on tokens (`CanvasResultsContext.tsx:721-734`).
7. Lens on canvases: dim ignored assets, show `only_for` assets, overlays declare their function.
8. `utils/assetTypes.ts` reads the catalogue (keeps its function signatures for callers); its `TYPES` data goes.
9. Plug-in fixtures: the three example types of spec §5.2 render in palette, SLD, map, 3D, panel, search and guide with no code change; no hex literal in canvas files (widen `charts/chartThemeTokens.test.ts`).

## Likely files

`frontend/src/canvas/*` (new), `pages/TopologyCanvas.tsx`, `pages/topologyAssets.ts`, `pages/topologyEdges.ts`, `pages/MapCanvas.tsx`, `pages/SiteCanvas.tsx`, `site3d/assetLibrary.ts`, `site3d/layout.ts`, `utils/assetTypes.ts`, `utils/assetTypeIcon.tsx`, `layout/paletteIcons.tsx`, `components/CanvasResultsContext.tsx`, `components/charts/chartThemeTokens.test.ts`, `index.css` (new `--color-asset-*` and state tokens, values per THEME-LIGHT / ui-cohesion).

## Acceptance

- Plug-in contract test and glyph exhaustiveness pass; no hex in canvas files.
- Shots: the same project in SLD, map and 3D, light and dark, Guided and Expert, with: a candidate line before and after a solve, a shunt through the Load flow lens, selection, Assumed dot, a loading overlay.
- If over PR-SIZE-1, split: 04a visuals + glyphs + SLD, 04b map + 3D + lens.

## Keeping both modes in step

The canvases are shared by both modes; the lens and states read the same catalogue in each.
