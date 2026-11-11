# SC-06-layout shots

Real Chromium (Playwright 1440x900, deviceScaleFactor 2) against a local
backend + Vite on branch `claude/site-scene-06-layout`. Synthetic data only:
an empty saved project with one Site "Demo Campus" (rectangular boundary,
origin 11.58 E / 48.14 N), adopted as a 3D scene through the API; a
1000x800 synthetic floor plan (4 dark rectangles, 20 m scale bar at
x 100-300 px, y 700). Base set is the light theme, Guided mode.
`*.small.png` are the `scripts/coord/shots.sh` downscales.

| Shot | Shows |
|---|---|
| 01-import-button.png | Generate part of an enabled scene with no buildings: Add building / Generate buildings, then "Import layout..." with its format hint. |
| 02-trace-dialog-empty.png | "Trace a layout drawing" just opened: drawing at left, Scale tool selected, "mark 2 points (0/2)" / "mark the anchor" hints, Preview disabled. |
| 03-trace-dialog-calibrated-traced.png | Scale marked on the 20 m bar (distance 20 m), anchor at Hall A's lower-left (East -40, North 20), two outlines finished (Hall A 9 m, Office no height), a third outline in progress ("Outline: 2 corners"). |
| 03-dark.png | Same state as 03 in the dark theme. |
| 04-preview-readback.png | Panel after Preview: "Preview: 2 buildings", read-back "Drawing at 0.1 m per pixel · spans 70 x 21 m (east x north)", Apply / Discard; red preview footprints in the scene. |
| 04-expert.png | Same preview in Expert mode (light), with the expert sidebar and bottom table. This project already had 2 accepted layout buildings, so the preview replaces them. |
| 05-applied-corner-table.png | After Apply: building `layout:Hall A` selected, Correct pressed: Height field and the corner table (4 corners, East/North in metres from the site origin), Preview change / Cancel. |
| 06-refused-dxf.png | `plan.dxf` ("0\nSECTION...") chosen: named refusal "DXF files are not accepted: export the layout as GeoJSON, or as a PNG, JPEG or WebP image and trace it". |
| 07-geojson-summary.png | `site-buildings.geojson` chosen: summary row "site-buildings.geojson · 2 features · WGS84 longitude/latitude" with Preview / Cancel. |

## What looked wrong

- **"Import layout..." button wraps onto two lines** (01, 06, 07): the button
  shares a row with the helper text and gets squeezed to "Import / layout...".
  Needs `whitespace-nowrap`/`shrink-0`, or the hint on its own line.
- **Dark theme: anchor ring and crosshair vanish on the drawing** (03-dark):
  both use `stroke-text`, which is near-white in dark mode, drawn over a white
  drawing. The anchor marker at Hall A's corner and the cursor crosshair are
  barely visible; in the light theme they are clearly black. They should use a
  fixed high-contrast colour (or a dark+light double stroke) independent of theme.
- **Corner table columns misaligned** (05): the "East" and "North" headers sit
  centred over wide columns while the inputs are left-aligned, so "North" floats
  far right of its inputs and the gap between the East and North input columns is large.
- **Accepted layout building is titled "Proposed building"** (05), and the chip
  reads "Explain Proposed building", although the traced name was "Hall A" (id
  `layout:Hall A`). The user's own name is lost in the correction header.
- **Preview footprints hidden under the overlays** (04, 04-expert): with the
  default camera, the red preview buildings sit at the top-left of the
  boundary, behind the Generate panel and the "Library dimensions are
  estimates..." note; only a sliver is visible. The scene does not frame the preview.
- Expert mode has no visible "Open 3D site"/"Resume site setup" entry on the
  landing canvas; for 04-expert the Site 3D view was opened with the app's own
  `openSite3D('generate')` from the browser console.
- Environment only: the "Site context unavailable: the map data service could
  not be reached" toast and status line come from the sandbox's blocked map
  service, not from this feature.

How the selection in 05 was made: `setSitePartSelection({kind:'building', id:'layout:Hall A'})`
via the store (clicking a mesh in the WebGL scene by pixel was not attempted);
Correct was a real click.
