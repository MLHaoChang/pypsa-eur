<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# SC-06-layout implementation record

Status: implemented on `claude/site-scene-06-layout`. The PR, its head and review are recorded in the PR.

## Assignment contract

- **Unit.** SC-06-layout (`execution-manifest.yaml`), ticket [issues/07](../issues/07-layout-and-photo-proposals.md), layout half only (owner Q-SS-1). The photo half is parked until an engine is chosen.
- **Base.** Master `701328bd9f8ac443f2c53647976cf2ce3d0b398d`.
- **Gates.** D05 and D08 accepted in [sc-00-n/decisions.md](../sc-00-n/decisions.md). SC-01 has landed (native proposal lifecycle).
- **Plan review.** GK B GO (48), with conditions L1–L11, recorded in the ticket's Comments.
- **Open-PR overlap.** None: #1378 #1383 #1389 #1390 #1392 #1394 #1396 do not touch `services/site_scene`, `site3d` or `siteScene.ts`.

### Supported behavior

`POST .../scene/proposals` with `action: "import_layout"`, `inputs: {layout}`. There is no new route.

- **`{kind: "geojson", document}`**
  - RFC 7946 WGS84 only. A `crs` member may name only CRS84.
  - Polygon and MultiPolygon features become buildings; other geometry is skipped and counted in the preview.
  - Coordinates go through `frames.lnglat_to_local`, the scene's one projection: equirectangular at the Site origin, so it uses cos(lat).
- **`{kind: "raster", image, calibration, footprints}`**
  - `image` is the client-declared frame `{width_px, height_px, media_type}`. The server decodes nothing, so the 50 MP and 20 000 px limits and the inside-the-image check bound only that declared frame.
  - The calibration is a scale bar `{a_px, b_px, distance, unit}`, an `anchor_px` placed at `anchor_local` [e, n] m, and `rotation_deg`, the bearing of the drawing's up, clockwise from north. Pixel y is flipped.
- **Preview.** Native building records in the `layout` namespace, plus a `layout` read-back `{kind, metres_per_pixel, crs, extent_m, footprints, skipped}`. Buildings merge by id; the rest of the scene stays. Apply, cancel, receipt and retry are the existing lifecycle.
- **Manual correction.** `edit_buildings` with `namespace: "layout"` and the same `source_id` keeps the building's id. The UI offers a corner table (E/N metres).
- **Heights.** `height` / `height_m` in metres is `tagged`. Otherwise `levels` / `building:levels` × 3.0 m (D08) is `levels`. Otherwise 6.0 m is `fallback`. A text height is refused (`height_not_number`); levels that are not a whole number above 0 are refused (`levels_invalid`).

### Errors

All errors use existing scene kinds. There is no new kind, and `tool-error-kinds.json` is unchanged.

- **`invalid_input` 422, with `{field, code}`.** Codes: `unsupported_format` (DXF, IFC, PDF, SVG, DWG, and other image types), `scale_missing`, `scale_distance`, `scale_points_equal`, `placement_missing`, `rotation_invalid`, `crs_unsupported`, `crs_mismatch`, `far_from_site` (10 km), `remote_reference` (`$ref`, `href`, a linked crs), `layout_hole`, `layout_empty`, `outside_image`, `height_not_number`, `levels_invalid`, and the shape codes `layout_*`.
  - A unit or a non-finite distance goes through `frames.to_metres`, with `field: unit` or `field: value`.
  - GK B named `unsupported_format` as a kind; it is a `code` under `invalid_input`, because the scene contract has no such kind.
- **`resource_limit` 413.** `decoded_pixels`, `image_side`, and the D04 limits.
- **`invalid_geometry` 422.** A height of 0 or less, above 1000 m, or non-finite; a crossing footprint.
- **Frontend.** `LAYOUT_CODES` shows the server message in house style. Existing 413 copy reads `message`.

### Changed files

The frozen allowlist, plus one test outside it:

- **Backend.** `services/site_scene/{layout.py (new), native.py, proposals.py, verdicts.py}`.
- **Backend tests.** `tests/test_site_scene_layout.py` (new) and `tests/test_site_scene_proto_flow_verdicts.py`, plus `tests/test_sc07_scene_tools.py`. That last file is outside the allowlist: its one-line route/chat action parity now excludes `import_layout`, which stays route-only and is not a chat action.
- **Frontend.** `site3d/{LayoutImport.tsx, LayoutImport.test.tsx, layoutFile.ts, layoutFile.test.ts, SiteScenePanel.tsx}` and `api/siteScene.ts`.

### Review notes (mp-code-review, before the GK OK)

- **Read-only (L9/V37).** The scene routes have no viewer role: every member of the project's org may call them, and a read-only session is one where another user holds the edit lock. So "read-only is refused" is tested as the foreign lock (409 `project_locked`) plus the org boundary (`test_route_another_org_cannot_propose`), and the `/flow` verdict gives `read_only` for `can_edit=False`. Raised here for GK B; it is not yet accepted.
- **`properties.id` (L2).** A GeoJSON feature with no top-level `id` uses `properties.id` as its source id. That one property is kept, as the building's id; every other property, foreign member and name is dropped (`test_normalize_keeps_only_the_used_fields`).
- **Distance.** The 10 km check covers both kinds: a GeoJSON in the wrong place, and a drawing anchored far off the Site.
- **Re-import.** The preview's `layout.kept_layout_ids` names the earlier layout buildings a merge keeps (V78), before Apply.
- **Remote references.** `$ref`/`href` keys are refused anywhere except inside feature `properties`, which are never fetched or stored.
- **Limits.** The 50 MP and 20 000 px limits are written twice: in `layoutFile.ts` for the browser check, and in `layout.py`, which is authoritative. No test checks that the two agree.

### Exclusions

- The photo half.
- `POST .../scene/sources` and the private-original store: still deferred, with no D05 runtime claim.
- Upload of originals: the drawing never leaves the browser.
- PDF rasterization (refused).
- DXF/IFC adapters (refused by name).
- Chat/assistant access to `import_layout`.

## Test-first and result evidence

- **RED.** [evidence/red-1-collection.txt](evidence/red-1-collection.txt): the new seam test fails at collection on master, because `services.site_scene.layout` does not exist.
- **GREEN, backend** (`/root/.venv-pypsa-gui`). From `pypsa-gui/backend`: `python -m pytest tests/test_site_scene_layout.py tests/test_site_scene_*.py tests/test_sc07_scene_tools.py -q`, exit 0. `python smoke/assistant_inventory.py --check`, exit 0, unchanged.
- **GREEN, frontend.** `npx vitest run src/site3d src/api`: 92 files, 1063 tests passed. `npx tsc --noEmit -p .` is clean.
- **Mutants.** [evidence/mutants.txt](evidence/mutants.txt).

| V / condition | Assertion | Test nodes (`test_site_scene_layout.py` unless named) | Status |
|---|---|---|---|
| V39 | Raster and GeoJSON dimensions, and every vertex to 1 cm | `test_raster_north_up_*`, `test_raster_rotation_anchor_*`, `test_raster_half_turn_*`, `test_raster_rotation_30_*`, `test_geojson_rectangle_*`, `test_geojson_at_60_north_*` | passed |
| V39 | Absent scale/CRS named; DXF/IFC/PDF/SVG not accepted | `test_missing_scale_is_named`, `test_missing_placement_*`, `test_a_projected_crs_is_named`, `test_projected_coordinates_without_*`, `test_cad_and_vector_formats_*`, `test_raster_media_types_*`; `layoutFile.test.ts` | passed |
| V40 | Remote references, oversized inputs and out-of-image pixels refused before geometry (spy) | `test_remote_references_*`, `test_a_nested_remote_reference_*`, `test_an_image_over_50_*`, `test_an_image_side_*`, `test_too_many_*_before_geometry`, `test_pixels_outside_*`, `test_route_body_over_the_cap_*`; `layoutFile.test.ts` (header-only size, 25 MB) | passed |
| V06 / L4 | m/mm/ft agree; unknown or missing unit refused; zero, negative or non-finite distance, equal points and non-finite rotation each named | `test_scale_units_agree`, `test_unknown_scale_unit_*`, `test_zero_or_negative_*`, `test_non_finite_scale_distance_*`, `test_coincident_scale_points_*`, `test_non_finite_rotation_*` | passed |
| V08 / L6 | tagged, levels and fallback evidence; text, negative and non-finite heights and bad levels refused | `test_height_in_metres_*`, `test_levels_use_*`, `test_no_height_*`, `test_a_text_height_*`, `test_a_negative_zero_*`, `test_bad_levels_*`, `test_route_preview_then_explicit_apply_then_retry` | passed |
| L5 | MultiPolygon parts, id fallback and collision refused | `test_multipolygon_parts_*`, `test_a_single_part_*`, `test_missing_ids_*`, `test_an_index_fallback_colliding_*`, `test_raster_footprint_ids_*` | passed |
| L7 | `native.generate` byte-equal; each admission part kills on `import_layout` | `test_native_generate_output_is_unchanged` (digest recorded on master), the `test_too_many_*` tests, `test_preview_bytes_limit_*`, the duplicate-id tests; master's `test_site_scene_proto_geometry.py` is unchanged and passes | passed |
| L8 | Only `process_layout` changed | `test_site_scene_proto_flow_verdicts.py::test_optional_engines_never_enable_*` and `::test_process_layout_is_native_and_gated_like_generate` | passed |
| V36 / V78 / L9 | Preview writes nothing; apply is explicit; retry returns the receipt; stale or cancelled changes nothing; merge by id | `test_route_preview_then_explicit_apply_then_retry`, `test_route_stale_*`, `test_route_cancelled_*`, `test_route_import_merges_by_id_*`, `test_route_manual_correction_*` | passed |
| V37 / L9 | Foreign lock refuses propose and apply; another org cannot propose | `test_route_foreign_lock_*`, `test_route_another_org_*`. The scene routes have no viewer role: read-only is the lock and the org boundary. | passed |
| V38 | `network.nc` byte-equal; layout properties (`p_nom`, `marginal_cost`) never stored | `test_route_preview_then_explicit_apply_then_retry`, `test_route_v55_*` | passed |
| V55 / L2 | Marked properties, foreign members and filename absent from the normalized input, the proposal record, the response and every project file | `test_normalize_keeps_only_the_used_fields`, `test_route_v55_no_marked_input_*`. No original bytes reach the server. | passed |
| V77 / V90 | Needs an enabled Site with a boundary; native/manual paths unaffected | flow verdict test; existing `test_site_scene_routes.py` passes | passed |
| V43 / V44 | No mesh in this unit | — | not applicable |

## GK B (49) review round 1 (NOT OK @bd1923b8)

Answered in one push. Each item has a test that turns red when its guard is mutated (hand mutants, all killed).

| Item | Change | Test |
|---|---|---|
| F1 | — | `test_route_import_merges_by_id_*`: hall + annex re-proposed gives `kept_layout_ids == []` |
| F2 | `chat.py`: the chat read, its preview list, apply (and so the card) and cancel treat an action outside `CHAT_ACTIONS` as not found | `test_route_only_import_layout_is_invisible_to_the_chat` |
| F3 | — | `test_non_finite_coordinates_are_refused` asserts `layout_coordinates` for ring, pixel and `anchor_local` |
| F4 | — | `test_a_feature_with_its_own_projected_crs_is_named` |
| F5 | — | `test_a_document_nested_too_deeply_is_named` (65 refused, 64 accepted) |
| F6 | — | `test_a_latitude_out_of_range_is_named`, `test_one_vertex_beyond_10_km_is_named`, `test_exactly_50_megapixels_is_admitted_and_one_pixel_more_is_413` |
| F7 | `layout.py`: a GeoJSON id over 128 characters is refused (`layout_document`), never renamed | `test_a_geojson_id_over_128_characters_*`, `test_a_closed_ring_at_exactly_the_vertex_limit_is_admitted` |
| F8 | — | `test_route_another_org_cannot_propose` asserts 404 |
| F9 | `layout.py` `_echo`: a quoted crs name or media type is a string of at most 60 characters | `test_echoed_client_text_is_bounded` |

Also in this push:
- `chat.building_label` names layout buildings, matching `SiteCompanion.buildingLabel`. The parity test `test_sc07_receipts.py` now reads the whole arrow function and checks the layout branch; before, it failed on the PR head.
- Tests for mutants.py survivors: input shapes, the building caps on both paths, a non-text kind, and an import with no layout.
- `_json_body` RecursionError: filed as `.scratch/site-scene-generation/issues/09-json-body-recursion-error.md`.

## Next

- Photo half: blocked on the engine choice (Q-SS-1) and on SC-00 P.
- The upload route and private store: a separate unit with D05 runtime proof, if the owner wants the drawing kept for later sessions.
