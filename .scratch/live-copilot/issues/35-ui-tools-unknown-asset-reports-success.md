<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-35 — UI tools report success for an unknown asset or metric

Status: done
Type: bug

Found by LC-32 (D24, `LC-D24.unknown_asset_event`); reviewed by GK B (12).

`ui_select_component` without `open_editor` and `ui_open_asset_detail` do not check that
the asset exists. With name `"Nope"` both emit a `ui_event` and report success, while
`ui_select_component` with `open_editor=true` correctly refuses `component_not_found`.

- `ui_select_component`: the frontend (`uiNavigate.ts:127`) sets `selectedComponent` to
  the non-existent asset and opens the right panel. This is a defect: the screen lands on
  nothing while the assistant says it worked.
- `ui_open_asset_detail`: `AssetDetail.tsx:67–77` ignores an unknown asset. Harmless on
  screen, but the tool still reports success.
- An unknown metric in `ui_open_asset_detail` is likewise accepted and reported as done.

Expected: both tools refuse an unknown asset as `component_not_found`, and an unknown
metric as `invalid_navigation`, with no `ui_event` frame, as the editor path already does.
Then extend `test_lc06_d24_ui_events.py::test_a_wrong_target_is_refused_without_moving_the_screen`
and move `LC-D24.unknown_asset_event` to covered.

## Follow-ups (CAPACITY-FILL-1, option c)

- Unknown asset (both tools) and unknown metric were already refused on master; the unknown
  `category` and `mode` of `ui_open_asset_detail` now are too (`invalid_navigation`, no `ui_event`).
- `LC-D24.panels_e2e` is covered by `frontend/scripts/smoke-copilot-lc06-d24.mjs`, run in the CI browser job.
