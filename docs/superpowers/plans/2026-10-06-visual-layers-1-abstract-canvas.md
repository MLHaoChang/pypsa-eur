# Visual layers, plan 1 of 3 — the abstract canvas as a campus process diagram

**Date:** 2026-10-06
**Parent:** `docs/superpowers/assessments/2026-10-06-campus-visual-layers-assessment.md` (§2, §7 and the owner decisions of 2026-10-06 recorded there)
**Siblings:** plan 2 (map view), plan 3 (3D site view)
**Status:** plan, written before the code; amended where building it proves it wrong
**Target:** `master`, one PR per increment

## The goal

The blank canvas (`pypsa-gui/frontend/src/pages/TopologyCanvas.tsx`) already is the abstract layer the owner describes: latent coordinates, deliberately decoupled from `bus.x`/`bus.y`, persisted in `layout.json`. What it draws today is a *grid* schematic: buses as nodes, branches as edges, every generator, load and store folded into at most four hidden bubbles per bus. A campus reads differently: the electrolyser, the data hall, the BESS and the gensets are the things the user is designing, and energy flows between *them*. This plan turns the grid schematic into a process diagram without breaking what it does for grid networks.

Owner framing (2026-10-06): primarily visualisation and user experience; every component the model holds must be visible; whatever carries meaning in 3D (plan 3) must show the same way here.

## What exists today (surveyed 2026-10-06)

- Nodes: `bus` and `assetGroup`; edges: `network` (`TopologyCanvas.tsx:1794-1795`). Asset groups are built per bus × {Thermal, Renewables, Storage, Load} (`buildAssetDescriptors`, `:113-132`) and hidden until the bus's context menu shows them (`visibleGroups` starts empty, `:2046`).
- Links are drawn `bus0 → bus1` only; `bus2`/`bus3` never appear in either canvas. Separately, creating a three-port Link **drops `bus2`** because `services/network_crud._drop_unknown_extras` (`:151`) keeps only catalog Input attributes, and PyPSA's catalog does not list the multi-port columns.
- Results overlay (`components/CanvasResultsContext.tsx`): edge colour by loading band (`loadingColor`, `:609`), a chip with |flow|, a direction arrow and loading %; bus donut; asset-group dispatch and SoC gauge. Transformers are looked up in the **Lines** map (`TopologyCanvas.tsx:931-933`), so a transformer never shows its flow. No motion: width is static `s_nom` (`:950`), there is no animated dash or particle.
- Persistence: `layout.json` via `GET/PUT /api/projects/{name}/layout` (`backend/routers/projects.py:3425-3514`), 300 ms debounce, server wins on load. **Open defect 7** (`docs/superpowers/OPEN-ITEMS.md:105-118`): `PUT /layout` 404s before the first project save, and the server copy beats a newer local copy, so drags can revert; `PersistedState.savedAt` exists and nothing compares it.
- Tests: `TopologyCanvas.busnode.test.tsx` (2), `topologyLayoutStore.test.tsx` (8), drag tests. Nothing covers `runLayout`, `EditableEdge` or the overlay.

## Increments

### A1 — Multi-port Links exist and are drawn

The smallest increment and the one plan 3 and the campus study need too.

- **Backend.** `_drop_unknown_extras` admits `bus\d+`, `efficiency\d+`, `p_min_pu\d*`-style multi-port columns for `Link` (regex on the key, not a list), and a test creates a CHP from the palette and asserts `bus2` and `efficiency2` survive. The 3D branch's smoke found this; the fix is a filter arm.
- **Both canvases.** A Link with `bus2`/`bus3` set draws one edge per extra port (`link-<name>#2`, `#3`) in the far bus's carrier colour, dashed like the main edge, selectable as the same component. The schematic's auto-layout treats the extra edges as ordinary edges for collision; the map draws them as chords with their own waypoint key.
- **Overlay.** `/results/links` serves `p0` only; `p2 = -p0 × efficiency2` is a derived value and is labelled as derived in the chip tooltip. A bulk `p1`/`p2` endpoint is a follow-up noted in the 3D branch's spec §8 and is not required here.
- **Tests.** A table-driven test over the palette: every item with a third port draws two edges.

### A2 — Process view: every asset is a node

- **A classification module shared with 3D.** The 3D branch's `site3d/assetLibrary.ts` matches a component to one of 21 types by class, carrier, far-side carrier and port count. The *match* part (rules, `matchType`, labels, colours, no geometry) moves to a main-bundle-safe `frontend/src/utils/assetTypes.ts` that both canvases and the 3D library import. One taxonomy, one icon set (`layout/paletteIcons.tsx` on the branch already has one per palette item), one colour per type across all three layers. Plan 3 S0 lands the library; this increment extracts the pure half. If A2 ships before S0, the module is created here and S0 re-points the library at it.
- **A view toggle, `Assets: grouped | individual`,** on the canvas toolbar beside the legend, persisted in `layout.json` (`version` bump, old documents read as `grouped`). *Individual* draws one `asset` node per Generator, Load, StorageUnit, Store and single-port Link, labelled with the type icon, name and the sizing parameter the type names (`40 MWh`, `12 MW`), connected to its bus by the existing dashed asset edge. *Grouped* is today's behaviour. Default: *individual* when the network has at most 40 non-bus components, *grouped* above that, overridable; the threshold is a constant with a test.
- **Positions.** Individual nodes are persisted like buses (`nodes[]` in `PersistedState`), auto-placed by a ring around their bus on first appearance, draggable, with the same overlap ring. A node whose component is deleted is pruned on save (never on read), the same orphan rule the 3D sidecar uses.
- **Selection and editing.** Click selects the component (`setSelectedComponent`), double-click opens the same editor the properties panel uses. Nothing new in the panel.
- **Overlay.** The asset node shows what the group bubble shows for one asset: dispatch with direction, SoC gauge for storage, effective capacity per investment period; the `byAssetGroup*` maps gain a per-component variant built from the same chunks (no new endpoint).
- **Tests.** Node construction from a fixture network (counts, labels, ids), prune-on-save, default-mode threshold, overlay mapping for one of each class.

### A3 — Flow that moves

- **An animated-flow edge style** when the overlay is on and the component has a non-zero flow at the selected time step: a dash pattern whose `stroke-dashoffset` advances in the flow direction at a speed proportional to |p|/rating (clamped), on top of the loading-band colour, plus an optional **width by flow** mode (toolbar toggle, off by default so the static `s_nom` width stays the default). Pure CSS animation on the path, no per-frame React work; one `<style>` keyframe per speed bucket (5 buckets), not per edge.
- **`prefers-reduced-motion`** turns the dash motion off and keeps the colour band and the arrow chip (the same rule the 3D branch applies to rotors).
- **Transformers** get their flow from `/results/transformers` (new fetch in `CanvasResultsContext`, keyed `Transformer:<name>`), fixing the Lines-map lookup. The map's transformer overlay follows for free since it shares the provider.
- **Tests.** The speed bucket function is pure and tested; a jsdom test asserts the class and the CSS variable on an edge with flow and their absence under reduced motion.

### A4 — Positions never revert

Closes OPEN-ITEMS 7.

- On load, pick the **newest** of server, memory cache and localStorage by `savedAt`, and if local is newer than server, push it (one PUT) rather than discard it.
- When `PUT /layout` answers 404 because the project has not been saved yet, keep the layout in the memory cache and flush it in the save path that already orders layout after network (`AppHeader.tsx`, `Sidebar.tsx`, `projectActions.ts`); a toast says "Layout will be saved with the project" once, not per drag.
- A failed PUT for any other reason surfaces once in the status bar instead of silently.
- **Tests.** Newest-wins selection; the 404-then-save sequence ends with the server holding the dragged positions; the sequence in the finding (`findings/2026-07-31-blank-canvas-node-drags-revert.md`) is the regression test.

### A5 — Reading aids and coverage

- Legend gains the asset-type rows when *individual* is on, with the shared colours.
- Minimap draws asset nodes in their type colour (React Flow's minimap does not draw edges; accepted).
- "Auto-layout" in *individual* mode lays assets out by type around their bus (loads south, generation north, storage east — the same zone words the 3D packer uses, so the two layers agree on where things are *roughly*).
- Tests for `runLayout` (determinism, tier rows, no overlap after push-apart) and for `EditableEdge` waypoint insert / move / remove, which have none today.

## Acceptance

1. A CHP created from the palette holds `bus2` and `efficiency2`, and both canvases draw its heat edge.
2. With *individual* on, every Generator, Load, StorageUnit, Store and Link of a campus template is a node with the right icon, label and sizing figure; dragging one persists; deleting its component removes the node on the next save.
3. The abstract canvas, the map and the 3D view show the same type name and colour for the same component.
4. With the overlay on and playback running, edges with flow move in the flow direction; under reduced motion they do not move and still show colour and arrow; transformers show their loading.
5. The revert sequence from the 2026-07-31 finding no longer reproduces; a drag before the first save survives the save.
6. `tsc -b`, `vitest run` and the backend suite are green; the main chunk grows by at most 5 kB gzipped per increment (the 3D branch's budget rule).

## Out of scope, ledgered

- Edge bundling or orthogonal routing (a real single-line-diagram layout engine). The owner's "abstract" layer does not need it; revisit if campuses exceed ~60 components.
- A second time control; the shared `SnapshotPicker` stays the only one.
- Per-asset result endpoints beyond what the chunked series already serve.

## Order and first step

A1 (two days, backend first) → A4 (two to three days) → A2 (one to two weeks) → A3 (one week) → A5 (three to four days). A1 and A4 are independent of plan 3; A2 shares the type module with plan 3 S0 and whichever lands first creates it.

First step: the `_drop_unknown_extras` test that creates a CHP and asserts `bus2` survives, red, then the filter arm.
