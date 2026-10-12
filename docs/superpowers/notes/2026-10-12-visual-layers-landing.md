# Visual layers — what landed on `claude/visual-layers`, and what UAT should look at

Date: 2026-10-12 (work started 2026-10-06)
Scope: the three plans of 2026-10-06 (`docs/superpowers/plans/2026-10-06-visual-layers-{1,2,3}-*.md`), every increment, built on top of the 3D site view branch (PR #93, `claude/3d-site-visualization-gatc5z`) and master as of 2026-10-12. One integration branch, one merge commit per increment, each increment built by its own agent on a worktree and merged after type-check and the affected test directories passed. The owner's instruction: implement every feature before a UAT review of the packaged macOS app.

Nothing here was run on a real GPU or in the packaged app; §5 lists what UAT must cover for that reason.

## 1. The branch

`claude/visual-layers` = PR #93's branch (3D site view + master 2026-10-12) + 59 commits in ten merges, in landing order: S1, M1, A1+A4, S2, M2, A2+A3, S4, S3, S5, M3+M4+M5. Opened as a stacked PR on #93's branch; GitHub re-targets it to master when #93 merges.

| Check on the final tree | Result |
|---|---|
| `tsc -b` | clean |
| `vitest run` (full) | 395 files, **4,201 tests, all pass**; 4 files (`ChatPanel.manifest`, `ChatPanel.sendRequest`, `TariffBuilder`, `tariffModel`) timed out at file level under the full parallel load and pass when run alone (368 tests), the same load effect noted in §6 |
| `npm run build` | clean; main `spa` chunk **1,036.11 kB gzipped** against **1,003.42 kB** for current master built the same way (+32.7 kB for every main-bundle piece of the three plans: asset nodes and moving flow on the schematic, the map's tools, dialogs, legend, minimap, import and OSM layers, the topology check, the shared taxonomy); `SiteCanvas` chunk 319.06 kB gzipped (311.41 kB at the Phase 2 close; S2–S5 live there) |
| backend `pytest` (full, one process) | PENDING |

**CI did not run on either PR.** GitHub Actions is disabled for the repository (`run_workflow` answers "Actions has been disabled for this repository"; no run exists for any push after 2026-10-06). The figures above and in `notes/2026-10-06-3d-site-view-s0-landing.md` are therefore the only automated gate until Actions is re-enabled; the owner should re-enable it before merging, or run the three suites locally.

## 2. Layer 1 — the abstract canvas (plan 1)

**A1 — multi-port Links exist and are drawn.** `services/network_crud._drop_unknown_extras` admits `bus{i}`/`efficiency{i}` for a set port and drops an unset port's keys (`LinkCreate` defaults `bus3=""`), so a CHP from the palette keeps its heat port and no empty `bus3` column appears (`_LINK_PORT_RE`, `_link_port_extras`). Master's own fix for the same class landed meanwhile (every field a Create model declares survives); the merge unites the two: declared keys pass, unset ports still fall. Both canvases draw one edge per extra port (`link-<name>#2`, pure `pages/topologyEdges.ts`), coloured by the far bus's carrier, with a derived flow chip (`p_i ≈ -p0 × efficiency_i`, labelled derived). Closes OPEN-ITEMS 14.

**A4 — positions never revert.** `topologyLayoutStore.loadLayoutNewestWins` picks the newest of server, memory and localStorage by `savedAt` and pushes a newer local copy once; a 404 before the first save keeps the layout in memory with one toast; any other failed PUT is said once. Closes OPEN-ITEMS 7; the 2026-07-31 finding is marked fixed.

**A2 — every asset is a node.** Toolbar toggle *Assets: grouped | individual*, persisted in `layout.json` (now version 2, `assetMode`; version-1 documents read as grouped). Individual mode draws one node per Generator, Load, StorageUnit, Store and asset-type Link (types `feeder`, `transformer`, `switchyard`, `manifold` stay edges), with the shared icon, name and sizing figure, placed on a ring around the bus, draggable, pruned on save when the component is gone; default individual at ≤ 40 non-bus components (`INDIVIDUAL_MAX_COMPONENTS`). Per-component results (`CanvasResultsContext.byAsset`), legend rows per type, auto-layout by zone words.

**A3 — flow that moves.** A dash advancing in the flow direction, speed from `utils/flowMotion.flowSpeedBucket` (|p|/rating in fifths), one keyframe plus five duration classes in a single style block, zero per-frame React work; *width by flow* toggle off by default; reduced motion keeps colour band and arrow and stops the dash. Transformers get their flow from `/results/transformers` (`byTransformer`, cache key shared with the 3D view) on both canvases, fixing the Lines-map lookup.

**A5 (partly).** `runLayout` and the waypoint maths (`pages/edgeWaypoints.ts`) are under test; the minimap and legend changes rode with A2.

Note for the owner: the zone words the schematic uses for auto-layout (generation north, loads south, storage east, conversion west) follow the plan; the 3D library's zones differ (PV/wind south, loads north-east, thermal west). One of the two should give way; recorded, not decided.

## 3. Layer 2 — the map view (plan 2)

**M1 — geometry is a project sidecar.** `map_layout.json` (`services/map_layout_service.py`; `GET/PUT /api/projects/{name}/map_layout`, same lock, cap and degrade rules as `/layout`, validated shape) holds routed waypoints per branch (`line:`/`link:`/`tr:` keys, interior `[lng, lat]`) and bubble offsets; in `_BUNDLE_FILES`, so scenarios, snapshots, export and Save-As carry it. `pages/mapLayoutStore.ts` mirrors the sites store (memory-first, debounced PUT, flush at the save paths, localStorage only as fallback, read-only refusal) and migrates the old localStorage keys once. A renamed branch keeps its route (`_rename_map_route`; the frontend re-keys its document too).

**M2 — lengths follow geometry, by consent.** Project setting `settings.derive_lengths_from_geometry` (`PATCH /api/projects/{name}/settings`, Project info toggle, default off). `POST /api/network/lengths/from_geometry` writes Line **and** Link lengths from the route when one exists, else the chord, records provenance (`lengths` table: typed | chord | route), logs, and returns the impedance-rescale previews the existing dialog consumes; a bus drag does the same when the setting is on. An amber discrepancy badge marks any branch whose stored length disagrees with its geometry by more than 10 % or 100 m, with a one-click "Use geometry" (allowed even when the setting is off). The campus electrical draft reads derived lengths end to end (test).

**M3 — adopt real topology.** `POST /api/projects/{name}/map_layout/import` takes GeoJSON or KML (stdlib parser), matches LineStrings to branches by `name` else by endpoints within a tolerance, Polygons to sites, and returns a match report; nothing is written without `?apply=true`; applied routes carry `source: 'import'` and, with the setting on, lengths follow. `GET .../map_layout/osm_suggestions?bbox=` serves `power=line|minor_line|cable` ways and substations through the 3D view's Overpass client as a dimmed suggestion layer with ODbL attribution; "Use as route" writes `source: 'osm'`. `GET /api/geocode?q=` proxies a configured Nominatim-compatible endpoint (`PYPSAGUI_GEOCODER_URL`; hidden when unset) for an address box in the placement strip.

**M4 — build on the map.** Add bus at a click (writes x/y), connect mode with a map-side dialog (length defaults to the chord), palette drops carry a coordinate (a dropped bus is placed there; other assets get the nearest placed bus prefilled). Hub templates start **unplaced** at (0, 0); the unplaced-buses panel says "Place your campus: pick the grid connection on the map" and offers "Place all buses around this point" (200 m ring).

**M5 — topology check, legend, minimap.** Issues panel group *Topology check* (`utils/topologyCheck.ts`: unplaced buses, buses within 5 m, a transformer whose buses are more than 500 m apart, length disagreeing with geometry, a route leaving every site) with deep links; a map legend (voltage tiers, carriers, asset types from the shared taxonomy) and a minimap, both folding under 640 px; `EditableLine` and the bus-drag commit are under test.

## 4. Layer 3 — the 3D site view (plan 3)

**S0 — landed.** PR #93; details in `notes/2026-10-06-3d-site-view-s0-landing.md`. Master re-merged on 2026-10-12.

**S1 — shared taxonomy.** `utils/assetTypes.ts` (22 types: ids, labels, colours, icons, match rules, `matchType`, `legendFor`, sizing figure) and `utils/assetTypeIcon.tsx`; the 3D library joins its appearance table to it. The bundle guard asserts the module's import closure is main-bundle safe.

**S2 — placement that is plausible.** Library entries carry a `placement` rule (`anchor: yard | between | far`, `adjacentTo`, `clearanceM`, `keepOutM`, `orientation`); the packer honours anchors (a transformer sits midway between its two buses' yards, facing the far one), adjacency (a BESS beside its transformer), clearance and keep-out as hard constraints, zone-packing the rest; `site3d/placementCheck.ts` turns the arrangement into findings (outside the boundary, overlap, keep-out, clearance, not between its buses, not facing its far bus) shown as red/amber outlines, in the overlay's *Layout* section and in the Issues panel group *Site layout*. *Arrange all* re-packs everything after a confirm. Rule numbers are placeholders with a source comment each (gensets 30 m from a hall; H₂ storage 50 m from everything; PV 10 m clearance) — **owner check**.

**S3 — distances that reach the model.** `site3d/siteDistances.ts`: along the map route when one exists, else Manhattan × `ROUTING_FACTOR` (1.2, placeholder with source comment). Shown on the branch's label ("TR1 → Campus 33 kV · 86 m"); chevrons run along the route. With the project setting on, a gizmo drag or Arrange writes a straight two-point route (`source: 'site'`) for every affected branch that has no user route and queues the length rewrite; a user, import or OSM route is never overwritten. The campus draft reads the result (test).

**S4 — generated on demand.** A backend guard test proves no template or creation route creates a site. The empty state is a readiness checklist (a placed bus; a branch; a boundary or a bus to draw one around) with buttons to where each is fixed; **Generate 3D site** draws the default rectangle, arranges with the S2 rules, fetches context and flies the camera in (900 ms, jump cut under reduced motion); *Open in 3D* flies in too. The overlay says when a component is drawn as a generic block because the library has no type for it. `src/site3d/library/README.md` says how to add a type or swap a hero.

**S5 — visual and UX value.** A site thumbnail captured off the WebGL canvas once per fresh solve and on the overlay's *Snapshot* button (`PUT/GET /api/projects/{name}/sites/{site_id}/thumbnail`, `sites/<id>/thumb.png`, bundle-carried) shown as a *Site card* on the Guided-mode hub-design page with *Open in 3D*; the same picture closes the study report's executive summary as figure `site_3d` when a site exists. *Walk* mode (first person, WASD, eye height 1.7 m, Escape leaves). The findings list and the checklist are keyboard-reachable.

## 5. What UAT must cover (cannot be seen from here)

1. **Real GPU, packaged app.** Frame rate, shadows, hero model look, the fly-in, the walk mode, the thumbnail capture (follows the spike's `gl.render` → read-back pattern; jsdom has no WebGL). Build with `bash pypsa-gui/build-macos.sh`.
2. **A live Overpass fetch** for a real campus (context and OSM suggestions); the public endpoint rate-limits, `PYPSAGUI_OVERPASS_URL` names a private one.
3. **One arranged campus, judged.** Open a campus, Generate 3D site, look at where the transformers, BESS, gensets and H₂ storage landed; the rule numbers are placeholders to tune (§4 S2).
4. **Lengths by consent.** Turn *Derive lengths from geometry* on in Project info, bend a route on the map and move an asset in 3D; the rescale dialog should appear and the discrepancy badge should clear.
5. **The three legends agree.** The same component should show the same type colour and icon on the schematic (individual mode), the map legend and the 3D legend.
6. **A template campus starts unplaced** and places itself around one click.

## 6. Open items noticed while building (not fixed)

- Any `/api/network/` write, including the length rewrite, invalidates dispatch results, so a hub project must be re-solved before the campus draft reads new lengths; the UI does not say so.
- `MapCanvas` keeps an inline copy of the derivation handler that `utils/lengthsDerived.ts` now provides for the site; a one-line swap.
- `NewConnectionDialog` in `TopologyCanvas.tsx` and `components/MapConnectDialog.tsx` have the same field set; collapsing the schematic's onto the shared one is a one-import change.
- Zone words differ between the schematic auto-layout and the 3D library (§2 note).
- A Link whose own name contains `#<digits>` is mis-parsed as a port edge id.
- The schematic legend's voltage swatches differ slightly from the map's (`utils/voltageTiers.ts` centralises the map side).
- `SiteCanvas.tsx` is ~1,100 lines; the settings / sink / placement hooks would sit better in a `useSiteLengths` hook.
- Two `BottomPanel.test.tsx` render-cap tests exceed their 5 s timeout under full parallel load and pass alone; one `ParticipantsDesigner.test.tsx` test is order-dependent in the same way.
- Pre-existing: `test_openpyxl_parses_uploads_with_defusedxml_in_this_environment` fails where `defusedxml` is not installed; ruff findings in untouched lines of `routers/projects.py`, `network_crud.py`, `test_project_locks.py`, `test_energy_hub_templates.py`.
- `.gitignore`'s `node_modules/` does not ignore a `node_modules` symlink, so the worktree setup used here shows one as untracked; none was committed.
