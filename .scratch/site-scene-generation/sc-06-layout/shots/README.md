# SC-06-layout shots

Real Chromium (Playwright 1440x900, deviceScaleFactor 2) against a local
backend + Vite (no reload) on branch `claude/site-scene-06-layout` at
cf402f95 (UX conditions + shot findings). Synthetic data only: saved
projects with one Site "Demo Campus" (rectangular boundary, origin
11.58 E / 48.14 N) adopted as a 3D scene through the API; a 1000x800
synthetic floor plan (4 dark rectangles, 20 m scale bar). Light theme,
Guided mode unless named. Project `layout-light` already held two accepted
layout buildings (Hall A, Office), so the import below is a re-import.

| Shot | Shows |
|---|---|
| 01-import-button.png | Generate part with 2 accepted buildings: "Import layout..." on one line, format hint below it. |
| 02-trace-dialog-empty.png | Dialog just opened: "Distance (m)", Preview disabled with the grey reason "Mark the two ends of a known distance with the Scale tool". |
| 03-trace-dialog-calibrated-traced.png | Scale marked (20 m), anchor at Workshop's lower-left (East 0, North -20), Workshop finished (7 m), a second outline at 2 corners; reason "Finish or undo the outline you are tracing". |
| 03-dark.png | Same as 03, dark theme: anchor ring and crosshair now visible thanks to the halo. |
| 08-dialog-name-error.png | Second building renamed "Workshop": red "Name: another building already has this name" under both rows; reason "Give every building its own name". |
| 09-dialog-refusal.png | Anchor East 20000, Preview: red refusal "Layout building Workshop is more than 10 km from this site: check the layout is for this site and where it is placed". |
| 09-dark.png | Same as 09, dark theme. |
| 10-discard-guard.png | Cancel with outlines traced: "Discard the traced buildings?" with Cancel / Discard. |
| 04-preview-readback.png | After Preview: "Preview: 2 buildings", read-back "Drawing at 0.1 m per pixel · spans 66 x 20 m (east x north) · 2 earlier layout buildings stay"; camera panned (right-drag) so the red preview is visible. |
| 05-applied-corner-table.png | After Apply: `layout:Workshop` selected, Correct: title "Layout building Workshop", Height 7, corner table with left-aligned "East (m)" / "North (m)" headers. |
| 06-refused-dxf.png | `plan.dxf` chosen: named refusal naming GeoJSON / PNG / JPEG / WebP. |
| 07-geojson-summary.png | `site-buildings.geojson` chosen: "site-buildings.geojson · 2 features · WGS84 longitude/latitude" with Preview / Cancel. |

## What still looks off

- **Stale refusal** (10): after the far_from_site refusal, setting East back
  to 0 leaves the red refusal line in the dialog until the next Preview. It
  should clear when a calibration field or outline changes.
- **Duplicate name flagged on both rows** (08): the first, original "Workshop"
  row also turns red. Defensible, but flagging only the later duplicate would
  point at the field the user just changed.
- Known and out of scope: the default camera puts the preview behind the
  Generate panel (04 needed a manual pan).
- Environment only: the "Site context unavailable" toast and status line come
  from the sandbox's blocked map service.

The building selection in 05 was set through the store
(`setSitePartSelection`); Correct was a real click.
