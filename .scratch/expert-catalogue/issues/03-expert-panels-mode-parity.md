<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 03 — Expert panels and Guided cards on one `CatalogueField`: depth, groups, lens, readiness, mode parity

Status: ready-for-agent
Type: task
Gated: yes (UX (23))
Spec: [spec.md](../spec.md) §4.1, §4.2, §4.4 (panels and readiness), §10.1 "Mode parity"
Depends on: 01, 02. Starts after #1565 (PropertiesPanel, AssetFields, assetSchema) and THEME-LIGHT (b) merge.

## What

1. `src/catalogue/CatalogueField.tsx`: plain name, unit, input by kind, range check, Assumed chip, (i) → guide drawer, provenance chip, short help at Advanced; `data-testid="param-<entry id>"`.
2. `layout/properties/AssetFields.tsx` and `layout/PropertiesPanel.tsx`: groups and order from the asset type, depth selector (Key · Basic · Advanced · Full detail), folded group summaries, in-panel filter ("/"), lens filter; the per-type `hidden=` props go (types declare their groups).
3. Guided cards (`pages/hubDesign/cards/*`) and `components/FieldDrawer.tsx` render `CatalogueField` for catalogued entries; `depth: key` decides what a card shows.
4. Readiness "needs" per lens function: each missing entry by plain name with "›" via `openParameter` (both modes).
5. Remove `utils/fieldLabels.ts` `FIELD_LABELS` and `ledgerFieldLabels()` once callers read entries (`FieldDrawer`, `ProvenanceChip`, `runWords`, `GridReadinessScorecard`, `AssumptionsCard`); template ledgers keep values, ranges, sources.
6. Mode-parity test (spec §10.1) over every template fixture, plus the static "no label for a catalogued model name in `pages/hubDesign/` or `layout/properties/`" test.

## Likely files

`frontend/src/catalogue/CatalogueField.tsx`, `layout/properties/AssetFields.tsx`, `layout/PropertiesPanel.tsx`, `pages/hubDesign/cards/*`, `components/FieldDrawer.tsx`, `utils/fieldLabels.ts`, `hooks/useFieldLabels.ts`, `components/ProvenanceChip.tsx`, `components/runMonitor/runWords.ts`, `components/gridReadiness/GridReadinessScorecard.tsx`, `pages/hubDesign/AssumptionsCard.tsx`, tests beside each.

## Acceptance

- Mode parity: identical plain name, unit, value and Assumed mark in Guided and Expert for every shared entry, every template.
- "Expert never shows less than Guided" holds (Basic ⊇ Key) and is tested.
- Shots per state in Guided and Expert: each depth, a folded group summary, the lens on Load flow with a "needs" line, a field refusal, an Assumed chip with its key.
- If over PR-SIZE-1, split: 03a Expert panel (1, 2, 4), 03b Guided cards + label removal + parity (3, 5, 6). Both land before template-e2e's Expert pass.

## Keeping both modes in step

This slice is where both modes switch to the one component, in one PR; the parity test guards it from then on.
