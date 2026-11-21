<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 01 — An asset card's edit form opens with every value blank

Status: needs-triage
Type: bug
Found by: expert-catalogue 02a (#1804) end-to-end check; pre-existing on master

## What happens

Opening a Generator card's edit form through a `propertiesEditRequest` (the ⌘K Parameters jump, `openParameter`) shows every field blank: Name, Bus, Capacity. A canvas double-click on master does the same once the schema is cached, so the jump only exposes it.

## Where

`pypsa-gui/frontend/src/layout/PropertiesPanel.tsx`, the cards' `propertiesEditRequest` effects; see Root cause.

## Root cause (diagnosed 2026-11-21, Playwright, real app)

- The request effect at `layout/PropertiesPanel.tsx:213-219` calls `startEdit()` as soon as the detail card mounts and clears the request. On a cold page `schema` is still undefined (`useAssetSchema.ts:56` returns nothing until the units call finishes), so `seedForm([], asset)` (`assetForm.ts:77`, via `PropertiesPanel.tsx:202`) returns `{}`. `schema` is not in the effect's dependencies, so the form is never refilled.
- Evidence: cold palette jump and cold canvas double-click both give blank Name/Bus/Capacity; the second attempt in the same page (schema cached) gives `100`. `asset_schema/Generator` and `units/<name>` are requested only after the jump. Selecting the asset and pressing "Edit Generator" fills correctly even after a reload. Guided's field drawer renders the same `PropertiesPanel` (`App.tsx:857`) and shows the same race.
- Likely fix: in each card's request effect, return early without clearing the request while `!schema`, and add `schema` to the dependencies. The other cards use the same pattern (`:397 :570 :752 :976 :1385 :1623 :1821`); check each.

## Why it is its own package

Six open PRs touch `PropertiesPanel.tsx` (#1787 #1786 #1778 #1773 #1772 #1626); the coordinator queues the fix once they settle.

## Acceptance

- Opening the edit form by request and by canvas double-click, with the schema cached and uncached, shows the asset's current values.
- A vitest pins both paths.
- MC → plain words (marginal cost) at PropertiesPanel.tsx:269: the Generator card's meta line says “Marginal cost 40 €/MWh” (or the catalogue's plain name if slice 03 has landed), and the row `layout/PropertiesPanel.tsx | MC` leaves `pypsa-gui/frontend/src/uiCopyGuard.allow.json` and its pinned key set in `uiCopyGuard.test.ts` (expert-catalogue 02c).
- `pypsa-gui/frontend/scripts/catalogue-search-shots.mjs` asserts the landed field's stored value (100 MW for mainland_supply on Island Microgrid) in all four mode × theme runs; the empty value is never pinned as expected.
