<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 07 The 3D site has no data-centre plant: cooling, substation, MV switchgear, phases

Status: needs-triage
Blocked by: none (te/01: the template ships with no location, so the scene has no anchor until the user places one)
Source: DC template review 2026-11-24 (`../README.md`). Stage 4.
Severity: minor
Model: sonnet. Review: UX.

## Problem

The 3D scene (`frontend/src/pages/SiteCanvas.tsx`, `frontend/src/site3d/`) places parametric templates (`site3d/templates.ts`: hall, transformer, yard, plant, pvField, pvRoof, reservoir, cube) and five hero GLBs (`site3d/heroes.ts`). For the DC templates the data hall is only the Load fallback (`site3d/assetLibrary.ts:264-268`), and there is no cooling plant (no `cooling`/`chiller` in `assetLibrary.ts`, although `cooling` is a load of its own in the network), no HV/MV substation, no switchgear building. The staged template's phases are not shown as a build-out over time. Result projection (`site3d/resultsLayer.tsx`) works per asset.

## Acceptance criteria

- [ ] DC kinds in the library: data hall (one per phase, dated), cooling plant (sized from the cooling load), HV/MV substation with its transformers, MV switchgear building, genset yard, BESS/UPS; footprints from `services/asset_footprints` where they exist, else marked placeholder.
- [ ] A period slider shows what is built in each period of the staged template.
- [ ] Results colour the cooling plant by its load, like other assets.

## Likely files

`frontend/src/site3d/assetLibrary.ts`, `templates.ts`, `resultsLayer.tsx`, `backend/services/site_scene/`.

## Comments
