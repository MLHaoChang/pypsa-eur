# 3D site view — extending the asset library

The 3D site is **generated on demand from the model's assets through a library**
(owner decisions of 2026-10-06, `docs/superpowers/plans/2026-10-06-visual-layers-3-3d-site-view.md`
S1, S2, S4). No mesh is modelled per project and no project overrides the
library (owner decision 9 of 2026-09-28: a product-line library is a repo
change, not a per-project setting). Everything below is a **data change** —
no canvas code is touched to add a type or swap a model.

The library is two tables and one manifest:

| What | Where | Shared with |
|---|---|---|
| **Which type** a component is: id, label, colour, icon, match rules | `src/utils/assetTypes.ts` (`TYPES`) | the schematic and the map (main bundle) |
| **How the 3D view draws it**: size rule, template + numbers, zone, placement rule, hero, summary | `src/site3d/assetLibrary.ts` (`APPEARANCE`, keyed by the same id) | 3D only |
| **Hero models** (GLB files standing in for parametric units) | `src/site3d/heroes.ts` (`HERO_MODELS`) + `public/site3d/models/` | 3D only |

Validation runs when the module loads (`validateAssetTypes`, `validateLibrary`):
a bad entry fails every test file that imports the library, naming the entry
and the field.

## Add an asset type

Three edits and one test row.

1. **The type**, in `src/utils/assetTypes.ts` `TYPES`. Table order is match
   order — the first type whose rule matches wins — so put the new entry
   **before** its class's fallback (the rule with no `carrier`; exactly one
   per class, and it must be the last type matching that class).

   ```ts
   { id: 'tidal', label: 'Tidal turbines', color: '#0369a1', icon: 'renewable',
     match: [{ cls: 'Generator', carrier: /tidal/i }] },
   ```

   `icon` is one of `ASSET_ICON_NAMES` (a palette item id or a lucide name the
   palette lacks; `utils/assetTypeIcon.tsx` maps each to a component and its
   union type is exhaustive, so a new icon name fails to compile until drawn).
   Rules may also require a far bus's carrier (`farCarrier`), a third port
   (`hasBus2`) or the port the equipment stands at (`port`) — see `Match`.
   Carrier regexes must not carry the `g` or `y` flag (the table is frozen).

   If instead you want an **existing fallback** to stop calling a carrier
   "generic" (the overlay's "'x' drawn as a generic block" line, S4), widen
   that fallback rule's `genericUnless` regex rather than adding a type.

2. **Its appearance**, in `src/site3d/assetLibrary.ts` `APPEARANCE`, under the
   same id (the library refuses an id in one table and not the other):

   ```ts
   tidal: {
     zone: 'south',                                             // north | west | east | northeast | south | roof
     size: { Generator: { param: 'p_nom', unit: 'MW' } },       // which parameter sizes it; 'mwh' = p_nom × max_hours
     geometry: { template: 'turbineArray', params: { per: 2, hubHeight: 20, rotorDiameter: 18, spacingD: 3, perRow: 4, towerDiameter: 2, ratedRpm: 15, towerColor: '#e5e7eb', bladeColor: '#f3f4f6' } },
     placement: { clearanceM: 10 },                             // optional S2 rule: anchor, adjacentTo, clearanceM, keepOutM, orientation
     hero: 'turbine',                                           // optional: a HERO_MODELS id that stands in for the heroable units
     summary: s => `Tidal turbines — ${s.amount} MW as ${s.count} × ${s.params.per} MW`,
   },
   ```

   `template` is one of `TEMPLATE_IDS` in `src/site3d/templates.ts` (unitGrid,
   tankArray, turbineArray, pvField, pvRoof, hall, yard, bay, transformer,
   reservoir, manifold, composite …); each template documents its `params`
   and declares the animation anchors the results layer drives. Every numeric
   param must be finite and > 0. A new *shape* is a new template in
   `templates.ts` (pure geometry, tested there); a new *type* almost never
   needs one.

3. **One test row** in each of the two palette-driven tables, so a palette or
   library change is seen from both sides:
   - `src/utils/assetTypes.test.ts` — `EXPECTED` (palette item → type id) if
     the type has a palette item; otherwise add a `matchType` precedence case.
   - `src/site3d/assetLibrary.test.ts` — the same `EXPECTED`, plus the
     17-asset-type count in `'has 17 asset types and 4 infrastructure types'`.
   The legend (`legendFor`), the readout, the hover label and the results layer
   pick the type up with no further change.

Then `tsc -b` and `vitest run src/site3d src/utils/assetTypes.test.ts`.

## Swap or add a hero model

A hero is a CC0 / CC-BY GLB that replaces the parametric boxes of a type's
heroable units (containers, turbines, tanks, PV tables, halls). The loader is
model-agnostic; the manifest row tells it how to fit the file.

1. **Process the file** as `public/site3d/models/README.md` prescribes
   (`gltf-transform optimize … --compress false --texture-compress webp
   --join-named false --flatten false`): no Draco, no meshopt — the packaged
   app is offline and must never fetch a decoder (`src/site3d/noCdn.test.ts`
   guards this). Put it in `public/site3d/models/` and record its source and
   licence in that README's table; a CC-BY model needs its credit added to
   `MODELS_ATTRIBUTION` in `pages/SiteCanvas.tsx`.

2. **The manifest row**, in `src/site3d/heroes.ts` `HERO_MODELS`:

   ```ts
   tank: {
     id: 'tank', file: 'detail-tank.glb', rootScale: [-1, 1, 1],            // the root node's scale as the file has it (a negative = a mirror the loader removes)
     bounds: { min: [-0.424, 0, -0.2576], max: [0.424, 0.4153, 0.2576] },   // model-space bounds after node transforms
     yawDeg: 90, fit: 'box',                                                 // 'box' per axis | 'hub' (turbines; needs `rotor`) | 'tile' (tileBy 'height' | 'depth')
   },
   ```

   `src/site3d/heroes.test.ts` measures every file's bounds and root scale
   from the GLB and fails when the row no longer matches, and pins the gzipped
   size budget — run it after any file change and copy the measured numbers
   into the row. A new id must be added to the `HeroModel['id']` union.

3. **Point the type at it**: `hero: '<id>'` in the type's `APPEARANCE` entry,
   and the expectation in `assetLibrary.test.ts` `'the types that look like a
   hero model name it'`. While a model loads, or if it fails, the parametric
   form is drawn — a missing or broken file never blanks the site.

## What is deliberately not here

- A per-project library override or upload (owner decision 9, 2026-09-28).
- Generative (text- or image-to-3D) meshes (assessment §4.4).
- Vendor-faithful equipment, glTF export, paid photorealistic tiles
  (plan 3 S6, parked).

## Optional per-asset dimensions

A user may enter a total width/depth envelope and optional height in the shared
Properties/Site editor. These values are presentation metadata in `sites.json`,
not a per-project library, mesh, electrical rating or topology override. An
explicit envelope uses a simple labelled proxy for packing, rendering and area
checks; Reset restores the existing library estimate. Missing height remains an
estimate. Explicit dimensions do not scale when installed/optimised capacity
changes: review them after capacity changes. Equipment bounds and campus land
area do not establish access clearances, safety compliance or planning approval.
