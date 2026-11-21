<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 01 — An asset card's edit form opens with every value blank

Status: needs-triage
Type: bug
Found by: expert-catalogue 02a (#1804) end-to-end check; pre-existing on master

## What happens

Opening a Generator card's edit form through a `propertiesEditRequest` (the ⌘K Parameters jump, `openParameter`) shows every field blank: Name, Bus, Capacity. A canvas double-click on master does the same once the schema is cached, so the jump only exposes it.

## Where

`pypsa-gui/frontend/src/layout/PropertiesPanel.tsx`, the cards' `propertiesEditRequest` effects (not yet diagnosed past that point).

## Why it is its own package

Six open PRs touch `PropertiesPanel.tsx` (#1787 #1786 #1778 #1773 #1772 #1626); the coordinator queues the fix once they settle.

## Acceptance

- Opening the edit form by request and by canvas double-click, with the schema cached and uncached, shows the asset's current values.
- A vitest pins both paths.
- `pypsa-gui/frontend/scripts/catalogue-search-shots.mjs` asserts the landed field's stored value (100 MW for mainland_supply on Island Microgrid) in all four mode × theme runs; the empty value is never pinned as expected.
