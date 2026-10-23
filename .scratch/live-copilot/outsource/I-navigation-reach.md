<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

Status: ready-for-agent — GK triage 2026-10-23: G1–G7 (a); UX 2026-10-23: G2 (a), G3 (a), G6 (a)

# Outsourced task I: chat reaches the snapshot, the Properties panel, the edit card and every canvas view

Branch: `outsource/navigation-reach` · one PR against `master` · PR body starts with `Outsourced task I`

Source issue: `.scratch/live-copilot/issues/14-navigation-reach.md` (LC-14), items 1, 2, 3 and 6 only. The issue's line numbers are from baseline `d006f4be`. Every pointer below was checked against `master` at `32f188eb`.

## Read first

1. `AGENTS.md` (repo root). Release notes and PR rules are there.
2. The issue (above). Items 4 and 5 are not part of this task (see "Out of scope").
3. `pypsa-gui/backend/harness/README.md`. Read "The contract", the `loop.py` / `navigation.py` lines of the module map (l.23, l.29) and "Measuring parity" (~l.652-663). The last one covers the inventory and says that invalid navigation is refused before a UI event emits.
4. `pypsa-gui/frontend/src/components/uiNavigate.ts`, all 184 lines. It is the one function the chat stream (`ChatPanel.tsx:2366-2416`) and the live companion (`liveWalk.ts`) call to apply a `ui_event`.
5. `pypsa-gui/backend/services/chat_tools.py:4945-5015`, the three UI-control dispatchers. `ui_open_panel` shows how a refusal raises `HTTPException(400, detail={"error_kind": "invalid_navigation", ...})` before any event is returned.
6. `pypsa-gui/backend/harness/loop.py:3152-3221`, where a `_ui_event` result becomes a `ui_event` frame and a compact acknowledgement for the model.
7. `pypsa-gui/backend/tests/test_oj05_selected_line_edit_journey.py` and `tests/journey_wire.py`. Your Tier A test follows them. Tier A is defined in `.scratch/live-copilot/owner-journeys.md` l.18-26.
8. `pypsa-gui/frontend/src/components/ChatPanel.applicationNavigation.test.tsx`. Your vitest file follows its shape: `APPLY_UI_NAVIGATE_FOR_TEST`, then assertions on `useUIStore` state.
9. `docs/agents/review-ux.md`. The UX reviewer reviews this PR against it.
10. `pypsa-gui/frontend/src/layout/flowNavigation.ts:1-107` (`currentOrigin`, `withReturn`, `returnToOrigin`) and `layout/flowReturn.test.tsx`. Every panel the assistant closes must leave a "Back to <origin>" behind (UX condition).

## Goal in plain words

A user can open four things in the app that the assistant cannot open for them:

1. **A snapshot.** `ui_set_snapshot` says it drives the snapshot picker (`harness/catalogue.py:2202-2211`, "Drive SnapshotPicker selection from chat"). It does not: the frontend opens Results and drops the timestamp (`uiNavigate.ts:127-132`). Opening Results makes it worse, because the picker sits on the canvas and hides behind every slide panel (`SnapshotPicker.tsx:137`, `if (activeSlidePanel) return null`).
2. **A selected component's Properties.** `ui_select_component` selects the component and opens the right panel (`uiNavigate.ts:98-102`). When a slide panel is open, the Properties panel is not rendered at all: `App.tsx:792` renders `{!activeSlidePanel && <PropertiesPanel />}`. The one exception is Issues (`App.tsx:793-797`). So the selection happens out of sight.
3. **A component's edit card.** Only the canvas double-click (`pages/TopologyCanvas.tsx:2807-2819`), the tagging tour (`pages/results/prepareTaggingTour.ts:43-46`) and the availability editor (`components/series/AvailabilityEditor.tsx:60`) call `requestPropertiesEdit` (`store/uiStore.ts:1048`). No chat path does.
4. **The Hybrid and Site 3D canvas views.** `ui_open_panel` reaches `topology` (sets `blank`) and `map` (sets `satellite`) only (`uiNavigate.ts:135-141`). `CanvasView` has four values (`uiStore.ts:76`), and a user reaches all four from `components/MapModeSwitcher.tsx`.

Each fix is a navigation fix. None adds authority: every tool stays `Safety: read` and nothing writes to the network.

## User-facing outcome

- "Show me 12:00 on the canvas" closes the open slide panel. The snapshot picker then stands at that hour with the results overlay on. On a multi-period network the picker's period selector moves to the snapshot's period. A timestamp that is not on the time axis is refused, and the assistant can name the first and last snapshot.
- "Select L2" brings the Properties panel into view (G3).
- "Open the edit form for L2" selects L2, shows Properties and opens its Edit Parameters form. Nothing is saved; the user saves it as usual.
- "Show the hybrid map" or "open the 3D site view" switches the canvas the same way the map-mode switcher does.
- Whenever one of these actions closes a panel, the existing "Back to <origin>" button appears and reopens it. In Guided it also restores the drawer.
- **No new buttons, labels, panels, settings or UI copy.** The only visible text this task may add is an error message the assistant reads; app-visible strings are unchanged. Any wording you do write follows `docs/agents/review-ux.md` ("Wording and layout"). The app's 9-11px text and Inter font are deliberate (owner decision DENSITY-1), so do not restyle anything.

## Exact behaviour per item

### Item 1: `ui_set_snapshot` sets the snapshot or refuses (G1, G2)

Backend: `chat_tools.ui_set_snapshot` (`chat_tools.py:5013-5015`).

- Read the active network's snapshots (`PyPSAService.get_network().snapshots`).
- Resolve the target:
  - Parse `snapshot_iso` with `pd.Timestamp`.
  - On a flat index, the target is the position of the equal timestamp.
  - On a MultiIndex (period, timestep), `period` picks the level-0 value. For backward compatibility, also accept the form that the old description told models to use: `snapshot_iso="<period>|<iso>"`. Split on the first `|`. If that period disagrees with a `period` argument, refuse.
  - With no period on a multi-period network, a timestep that occurs in exactly one period resolves. One that occurs in more than one period is refused, and the message lists the periods.
- **Time zones (GK condition 2).** The time-axis route's own snapshot keys do not convert time zones. `update_snapshot_weightings` (`PATCH /api/network/snapshots/weightings`, `routers/network_time_axis.py:376-486`) builds `ts.isoformat()` keys from the axis (l.424-436) and matches the caller's key as an exact string. A key with `Z` or an offset never matches a tz-naive axis, so the route answers 400 "Unknown snapshot key". Follow that rule and refuse, but with a reason of its own rather than "not on the axis":
  - If the parsed timestamp's awareness (`ts.tzinfo is None`) differs from the axis's, refuse. Read the axis's awareness from `n.snapshots.tz`, or from the timestep level's `.tz` on a MultiIndex.
  - The message for a tz-naive axis (the usual case) reads: "The time axis has no time zone. Send the time without an offset, between 2026-06-01T00:00:00 … 2026-06-01T23:00:00." It uses the network's real first and last timesteps.
  - Never convert, and never compare naive with aware. A pandas comparison across the two either raises or silently mismatches.
  - Two aware sides compare as instants (pandas equality).
- Refuse with `HTTPException(400, detail={"error_kind": "invalid_navigation", "field": "snapshot_iso", "message": ...})` (G1, G7) in these cases:
  - the timestamp cannot be parsed;
  - a time-zone mismatch (above);
  - it is not on the axis;
  - it is ambiguous;
  - the network has no snapshots.

  No event is emitted.
- **Message wording (UX).**
  - Every message starts with a capital letter.
  - A range is written as "first … last" with the network's real ISO timesteps, e.g. "2026-06-01T12:30:00 is not on the time axis (2026-06-01T00:00:00 … 2026-06-01T23:00:00)."
  - Multi-period messages also list the periods, e.g. "Periods: 2030, 2040."
  - The model relays these words, so they are user-facing.
- On success return `{"_ui_event": True, "kind": "set_snapshot", "snapshot_iso": <the matched timestep, ISO>, "period": <int or None>, "snapshot_index": <int>}`.
  - `snapshot_index` is the position in `n.snapshots`. It is only right if that order is the order of the `snapshots` array the picker indexes (GK condition 1). That array comes from `get_snapshots` (`GET /api/network/snapshots`, `routers/network_time_axis.py:140-185`):
    - flat: `[s.isoformat() for s in sns]`;
    - MultiIndex: `get_level_values(1)` with a parallel `periods` array.

    Both walk `n.snapshots` in order. Tests 3 and 8 prove it by reading that route.

Catalogue: rewrite the `ui_set_snapshot` description (`catalogue.py:2202-2211`).

- Say what it now does: it moves the canvas snapshot picker and shows the results overlay at that snapshot, closing any open panel. On a multi-period network, pass `period`. A timestamp not on the axis is refused.
- Keep `Safety: read.` and keep the schema (`snapshot_iso` required, `period` integer).

Frontend, `uiNavigate.ts:127-132`: replace the branch. When `d.kind === 'set_snapshot'` and `typeof d.snapshot_index === 'number'`:

- close any open slide panel through `closePanelWithReturn` (see "Back to the closed panel" below);
- call `ui.setResultsSnapshotIdx(d.snapshot_index)`;
- call `ui.setResultsOverlay(true)` (G2).

An event without a numeric `snapshot_index`, from an old backend, is a no-op: no panel closes and no state changes (GK condition 7). All new event fields are optional. Add `snapshot_index?: number` to `NavigationRequest` (`uiNavigate.ts:64-87`) and to the `ui_event` frame type in `ChatPanel.tsx:2367-2392`.

Frontend, `components/SnapshotPicker.tsx`. Today the picker's period is local state (l.83-91). The clamp effect (l.104-110) pulls any index outside the selected period back into it, so an index in the second period would be undone. Change the clamp effect:

- On a multi-period network, when `resultsSnapshotIdx` lies inside another period's `[start, end]`, call `setSelectedPeriodKey` for that period instead of clamping.
- Clamp only an index that lies in no period.

The period `<select>` (l.209-224) and playback keep their current behaviour.

### Item 2: a selection is never hidden behind a slide panel (G3)

Frontend only, `uiNavigate.ts:98-102`. In the `select_component` branch, before `setSelectedComponent`, close any open slide panel except Issues through `closePanelWithReturn` (below):

```ts
if (ui.activeSlidePanel && ui.activeSlidePanel !== 'issues') closePanelWithReturn()
```

This is what a user's click on the canvas does: the click-outside listener closes the slide panel (`App.tsx:653-674`). Issues stays open because Properties already renders beside it (`App.tsx:793-797`). In Guided the hub-design auto-open fires once per project (`App.tsx:276-287`), so closing it here is respected and it does not reopen.

Catalogue: extend the `ui_select_component` description (`catalogue.py:2153-2162`) with one clause: it closes an open panel (except Issues) so Properties is visible. Keep `Safety: read.`

### Item 3: chat opens a component's edit card (G4, G5)

Backend (G4 option a): add an optional `open_editor` to `ui_select_component`. Update its catalogue schema (`catalogue.py:2158-2161`) and its dispatcher (`chat_tools.py:4950-4952`).

- **Strict boolean (GK condition 3).** The schema declares it as `{"type": "boolean"}`, so a non-bool (`"true"`, `1`) fails schema validation. The dispatcher also checks `type(open_editor) is bool` when it is not `None`, as `ui_open_panel` does for `compare_rail` (`chat_tools.py:~4989-4999`). On failure it raises `invalid_navigation` with `field: "open_editor"`.

With `open_editor` true:

- Refuse with `invalid_navigation` (field `component_class`) unless the class is one of the cards that take an edit request (G5 option a): `Bus`, `Line`, `Transformer`, `Link`, `Generator`, `StorageUnit`, `Store`, `Load`.
- **Existence check (GK condition 4).** Use the accessor `chat_tools.get_component` already uses (`chat_tools.py:379-393`): `attr = _GENERIC_CRUD_ATTRS.get(component_class)`, then `df = getattr(PyPSAService.get_network(), attr)`, then `name in df.index`. When the name is missing, raise `HTTPException(404, detail={"error_kind": "component_not_found", "component_class": ..., "name": ..., "message": "Line 'L9' is not in the network."})`. The detail names the class and the name, and the message starts with a capital.
- Return `{"_ui_event": True, "kind": "open_properties", "component_class": ..., "name": ...}`.

Without `open_editor`, or with it `False`, the dispatcher returns today's `select_component` event byte for byte: the same keys, in the same order, with no `open_editor` key (GK condition 3). Plain `ui_select_component` still gets no existence check. That gap is recorded as a follow-up in the LC-14 issue file; do not fix it here.

Description: add one sentence: with `open_editor=true` it also opens the component's Edit form in Properties for the user to change and save; nothing is saved by this tool. Keep `Safety: read.`

Frontend, `uiNavigate.ts`. Add an `open_properties` branch next to `select_component`. It does the same slide-panel close as item 2, through `closePanelWithReturn`, then:

1. `ui.setSelectedComponent({ type, name })`;
2. `ui.openRightPanel()`;
3. `ui.requestPropertiesEdit({ type, name })`.

Keep this order. `setSelectedComponent` drops an edit request for any other component (`uiStore.ts:907-916`), so the request must come after it, as `TopologyCanvas.tsx:2813-2815` does.

Frontend, `layout/PropertiesPanel.tsx`. Six cards already consume the request:

| Card | Line |
|---|---|
| `GeneratorCard` | 202-208 |
| `StorageUnitCard` | 376-382 |
| `StoreCard` | 542-548 |
| `LoadCard` | 693-699 |
| `LinkCard` | 907-913 |
| `BusPanel` | 1308-1314 |

`LinePanel` (l.1486, `startEdit` l.1549) and `TransformerPanel` (l.1681, `startEdit` l.1729) have an Edit Parameters form but do not consume the request. Add the same effect to each, modelled on `BusPanel`'s:

- wait for the row to load;
- if not editing, call `startEdit()`;
- call `clearPropertiesEditRequest()`.

Change nothing else in that file.

### Item 6: the Hybrid and Site 3D views are reachable (G6)

Backend (G6 option a): add an optional `canvas_view` to `ui_open_panel`.

- Catalogue: add it to the schema (`catalogue.py:2176-2184`) as `{"type": "string", "enum": ["blank", "satellite", "hybrid", "site"]}`.
- Dispatcher (`chat_tools.py:4955-5010`): validate it like the other enums. It is valid only with these pairings:
  - `panel_id` in `{"Topology", "topology"}` with `canvas_view == "blank"`;
  - `panel_id` in `{"MapCanvas", "map"}` with `canvas_view` in `{"satellite", "hybrid", "site"}`.

  Any other combination raises `invalid_navigation` with `field: "canvas_view"`. Copy it into the event when given.
- Description: add one clause: map with `canvas_view` hybrid (satellite with labels) or site (the 3D site view).
- `canvas_view` is a `_UI_EVENT` argument only (GK condition 5):
  - No `TOOL_ROUTES` entry changes (`catalogue.py:4253`, `4526-4528`).
  - No `SERVED_BY_TOOL` entry changes in `tests/test_route_chat_disposition.py` (l.34).
  - No route is added.

Frontend, `uiNavigate.ts:135-141`. For `topology` and `map`:

- use `d.canvas_view` when it is one of the four `CanvasView` values; otherwise keep today's default (`blank`, `satellite`);
- replace today's bare `ui.setSlidePanel(null)` with `closePanelWithReturn()`.

Add `canvas_view?: string` to `NavigationRequest` and to the `ChatPanel.tsx` frame type. The UX ruling names map/topology with `canvas_view`. Both go through the one branch, so plain `topology`/`map` also get the Back button when they close a panel; say so in the PR body.

### Back to the closed panel (UX condition, items 1, 2, 3 and 6)

Every panel the assistant closes must be closed through `layout/flowNavigation.ts`'s `withReturn`, so the existing "Back to <origin>" button (`layout/FlowReturnButton.tsx`, mounted at `App.tsx:820`) restores it. That covers:

- the `set_snapshot` close (G2);
- the `select_component` close (G3);
- the `open_properties` close;
- the `topology`/`map` close.

`withReturn(target, jump)` is module-private today (`flowNavigation.ts:78-82`). It records `currentOrigin(null)` (l.56-75): the mode, section, panel, Guided drawer, selection and scroll. It runs the jump, and then sets `flowReturn`. `returnToOrigin` (l.85-101) puts it back:

- Guided: the slide panel (`o.panel ?? 'hubDesign'`), the selection and the drawer;
- Expert: the panel.

The work:

- In `flowNavigation.ts`, add one exported wrapper and change nothing else in that file:

  ```ts
  /** Run a programmatic jump (the assistant's) and remember the view it left. */
  export function withReturnFrom(jump: () => void): void { withReturn(null, jump) }
  ```

- In `uiNavigate.ts`, add a local helper:

  ```ts
  const closePanelWithReturn = () => withReturnFrom(() => useUIStore.getState().setSlidePanel(null))
  ```

  Call it only when `activeSlidePanel` is non-null and is being closed. With no panel open, call nothing, so no Back button appears for a jump that left nothing.
- `applyUiNavigate` runs the jump inside `withAssistantNavigation` (`uiNavigate.ts:89-94`). So `setSelectedComponent` and `setCanvasView` do not fire `navigationChanged` (`utils/navigationEpoch.ts:10-12`), and `flowReturn` survives the rest of the branch. A later manual navigation clears it, as today (`flowNavigation.ts:104-107`).
- `flowNavigation.ts` imports `GridReadinessScorecard`. If importing it from `uiNavigate.ts` creates an import cycle that `tsc -b` or vitest reports, stop and say so in the PR. Do not restructure either module.

- `site` without a chosen site behaves exactly as the switcher's Site 3D button: `SiteCanvas` picks the site itself (`pages/SiteCanvas.tsx:681`).
- `harness/navigation.py::issue` only acknowledges `kind == "navigate"` for panels in its `PANELS` set (l.12-32, l.63-70), and `topology` and `map` are not in it. So no acknowledgement is issued for these, as today. Do not change `navigation.py`.

### The model's acknowledgement (all items)

`loop.py:3184-3209` builds the compact result the model sees. Today it drops `component_class`, `name` and the snapshot fields, so the model cannot say what is now shown. Add these keys to that dict, taken from the event and `None` when absent:

- `component_class`
- `name`
- `snapshot_iso`
- `period`
- `snapshot_index`
- `canvas_view`

Add nothing else, and change nothing else in `loop.py`.

## Files to touch

- `pypsa-gui/backend/services/chat_tools.py`: `ui_select_component`, `ui_open_panel`, `ui_set_snapshot` (l.4950-5015) only.
- `pypsa-gui/backend/harness/catalogue.py`: the three UI-control declarations (l.2153-2211) only, as descriptions and schema. No `TOOL_ROUTES` change; all three stay `_UI_EVENT` (l.4526-4528). No new tool.
- `pypsa-gui/backend/harness/loop.py`: the acknowledgement keys (l.3184-3209) only.
- `pypsa-gui/backend/harness/README.md`: one or two sentences in "Measuring parity" after l.660-662 ("The assistant can navigate all current Results tabs and panel targets ..."). Add that it can also:
  - set the canvas snapshot (refusing one not on the axis);
  - open a component's Edit form (`open_editor`, read tier);
  - switch to every canvas view.
- `pypsa-gui/frontend/src/components/uiNavigate.ts`.
- `pypsa-gui/frontend/src/layout/flowNavigation.ts`: the one exported `withReturnFrom` wrapper only.
- `pypsa-gui/frontend/src/components/ChatPanel.tsx`: the `ui_event` frame type (l.2367-2392) only.
- `pypsa-gui/frontend/src/components/SnapshotPicker.tsx`: the period-follows-index change (l.83-110) only.
- `pypsa-gui/frontend/src/layout/PropertiesPanel.tsx`: the edit-request effect in `LinePanel` and `TransformerPanel` only.
- `pypsa-gui/tool-error-kinds.json`: only the `why` text of `invalid_navigation` (l.750-753) and `component_not_found` (l.838-841), widened to name these tools (G7, GK condition 6). The file keeps exactly its current set of kinds, with no kind added, removed or renamed and no `surface` changed. `tests/test_tool_error_kind_manifest.py` and the frontend manifest test (`src/components/ChatPanel.manifest.test.tsx`) pass unchanged.
- `pypsa-gui/backend/tests/test_chat_tools_dispatch.py`: `test_ui_set_snapshot_returns_event_marker` (l.701-706) only. It pins the old, unchecked contract (an ISO on no network). Rewrite it to install a network with known snapshots and assert `snapshot_index`. This is the one existing test you may change; say so in the PR body.
- New tests (below): `pypsa-gui/backend/tests/test_navigation_reach.py`, `pypsa-gui/backend/tests/test_lc14_navigation_reach_journey.py`, `pypsa-gui/frontend/src/components/ChatPanel.navigationReach.test.tsx`, plus new cases in `pypsa-gui/frontend/src/layout/PropertiesPanel.editRequest.test.tsx` and `pypsa-gui/frontend/src/components/SnapshotPicker.a11y.test.tsx` (add cases; change none).
- `.scratch/live-voice/capability-inventory.json` and, only if its check says so, `.scratch/live-voice/capability-matrix.json`: **regenerated**, never hand-edited (below).
- `doc/release-notes.d/outsource-navigation-reach.md`: the release-note fragment.

**Overlap with Codex task G-a.** G-a (branch `outsource/profile-tuning-backend`, `.scratch/renewable-profiles/outsource/G-a-profile-tuning-backend.md`) also edits `harness/catalogue.py` and the capability inventory. Whichever of G-a and I merges second must:

1. merge `master`;
2. keep both sides' catalogue edits;
3. re-run `python pypsa-gui/backend/smoke/assistant_inventory.py --write` (and the control matrix's `--write` if its `--check` fails).

Never hand-merge the inventory JSON.

## Tests

Every new test below must fail on `master`'s code; each says why. Use the mocked wire only. Make no paid model calls.

### Backend unit tests, `tests/test_navigation_reach.py` (new)

Use `install_network` from `tests/conftest.py`. Build two networks:

- a flat one with 24 hourly snapshots from `2026-06-01`;
- a multi-period one with periods 2030 and 2040, each with the same three hourly timesteps `2030-01-01T00:00` to `02:00`. Set them with `n.set_snapshots(pd.MultiIndex.from_product(...))` and set `n.investment_periods`.

1. `test_set_snapshot_returns_the_picker_index`:
   - flat: `ui_set_snapshot("2026-06-01T12:00:00")` gives `snapshot_index == 12`;
   - a date-only `"2026-06-01"` gives 0.

   On master there is no `snapshot_index`.
2. `test_set_snapshot_refuses_a_time_not_on_the_axis`:
   - `"2026-06-02T00:00:00"`, `"2026-06-01T12:30:00"` and `"not a date"` each raise `HTTPException` 400 with `error_kind == "invalid_navigation"` and `field == "snapshot_iso"`;
   - each message starts with a capital letter;
   - each message contains `"2026-06-01T00:00:00 … 2026-06-01T23:00:00"`.

   On master these return an event.
3. `test_set_snapshot_multi_period`:
   - `(iso="2030-01-01T01:00:00", period=2040)` gives index 4;
   - `iso="2040|2030-01-01T01:00:00"` gives 4;
   - `(iso="2030-01-01T01:00:00")` with no period is refused as ambiguous, and the message names 2030 and 2040;
   - `iso="2030|..."` with `period=2040` is refused.

   It also proves the order (GK condition 1). Call `GET /api/network/snapshots` through the `client` fixture on the same network, or call `routers.network_time_axis.get_snapshots()` directly. Then assert:
   - `body["snapshots"][4] == "2030-01-01T01:00:00"`;
   - `body["periods"][4] == 2040`.
4. `test_set_snapshot_time_zone_mismatch_is_refused` (GK condition 2): on the tz-naive flat network, `"2026-06-01T12:00:00Z"` and `"2026-06-01T14:00:00+02:00"` each raise `invalid_navigation` with:
   - a message that says the time axis has no time zone;
   - the range `"2026-06-01T00:00:00 … 2026-06-01T23:00:00"`;
   - no `snapshot_index` anywhere: no event, no wrong index and no "not on the time axis" wording.

   On master these return an event.
5. `test_open_editor_emits_open_properties`:
   - `ui_select_component("Line", "L1", open_editor=True)` returns kind `open_properties`;
   - `ui_select_component("Line", "L1")` and `ui_select_component("Line", "L1", open_editor=False)` each satisfy `json.dumps(event) == json.dumps({"_ui_event": True, "kind": "select_component", "component_class": "Line", "name": "L1"})`. This is today's event byte for byte, with no `open_editor` key (GK condition 3).

   On master `open_editor` is an unexpected argument.
6. `test_open_editor_refusals`:
   - `("Carrier", "AC", open_editor=True)` raises `invalid_navigation` (field `component_class`);
   - `("Line", "nope", open_editor=True)` raises 404 with `error_kind == "component_not_found"` and `detail["component_class"] == "Line"` and `detail["name"] == "nope"`, and a message that starts with a capital (GK condition 4);
   - `open_editor="true"` and `open_editor=1` raise `invalid_navigation` with field `open_editor`.
7. `test_canvas_view_pairs`:
   - `ui_open_panel("map", canvas_view="hybrid")` and `"site"` carry `canvas_view`;
   - `ui_open_panel("topology", canvas_view="blank")` is accepted;
   - `("topology", canvas_view="hybrid")`, `("results", canvas_view="site")` and `("map", canvas_view="nope")` raise `invalid_navigation` with `field == "canvas_view"`.
8. `test_schema_matches_dispatchers`. With `tests.test_chat_tools_schema_panels._errors`:
   - the new arguments validate against the catalogue schemas;
   - `canvas_view` is in `ui_open_panel`'s properties;
   - `_errors(schema, {"component_class": "Line", "name": "L1", "open_editor": "true"})` is non-empty (strict boolean, GK condition 3);
   - each of the three tools is still `safety_tier_for(name) == "read"`;
   - `TOOL_ROUTES` for all three is still `_UI_EVENT` (GK condition 5).

### Tier A journey, `tests/test_lc14_navigation_reach_journey.py` (new)

Model it on OJ-05:

- **Fixture.** Use the `oj-grid` fixture: `install_network(build_oj_grid())`, then `POST /api/projects/oj-grid`, then the context binding as in OJ-05's `oj_grid`. You need no `_solve_once`; no solve is needed.
- **Parametrisation.** Parametrise over `tests.test_workflow_provider_parity.PROVIDERS` and `mode` in `expert`/`guided`.
- **Running.** Drive each case through `tests.journey_wire.run_journey`.

9. `test_chat_sets_the_snapshot_it_names`. Steps `ui_set_snapshot({"snapshot_iso": "2026-06-01T12:00:00"})`, then end.
   - `calls == ["ui_set_snapshot", None]`.
   - Exactly one `ui_event` frame, with `kind == "set_snapshot"` and `snapshot_index == 12`.
   - `results[0]["snapshot_index"] == 12` (the model's acknowledgement).
   - No `tool_error` frame.
   - GK condition 1: `client.get("/api/network/snapshots").json()["snapshots"][frame["snapshot_index"]] == frame["snapshot_iso"]`.
10. `test_chat_refuses_a_snapshot_not_on_the_axis`. Steps `ui_set_snapshot({"snapshot_iso": "2026-06-02T00:00:00"})`, then end.
    - One `tool_error` frame with `error_kind == "invalid_navigation"`.
    - No `ui_event` frame.
11. `test_chat_opens_the_edit_card`. Steps `ui_select_component({"component_class": "Line", "name": "L2", "open_editor": True})`, then end.
    - One `ui_event` frame with `kind == "open_properties"`, `component_class == "Line"` and `name == "L2"`.
    - `results[0]["name"] == "L2"`.
    - No `tool_pending_confirmation` frame (read tier: no card).
    - The network is unchanged: `s_nom` of L2 is the same before and after.
12. `test_chat_switches_to_hybrid_and_site`. Two `ui_open_panel` steps (`map`/`hybrid`, then `map`/`site`), then end.
    - Two `ui_event` frames carrying those `canvas_view` values.
    - `results[i]["canvas_view"]` echoes them.

Item 2 changes no backend contract, so its proof is the vitest below. The `select_component` frame stays byte-identical, and test 5 pins that.

### Frontend vitest

`src/components/ChatPanel.navigationReach.test.tsx` (new). Use `APPLY_UI_NAVIGATE_FOR_TEST` and reset `useUIStore` in `beforeEach`, including `flowReturn: null`, as `ChatPanel.applicationNavigation.test.tsx` does. Wrap tests 13-16 in `describe.each(['expert', 'guided'])`, setting `uiMode` in each case.

13. **Snapshot.** With `activeSlidePanel: 'results'`, `resultsSnapshotIdx: 0` and `resultsOverlayEnabled: false`, applying `{kind: 'set_snapshot', snapshot_iso: '…', snapshot_index: 12}` gives:
    - `activeSlidePanel === null`;
    - `resultsSnapshotIdx === 12`;
    - `resultsOverlayEnabled === true`;
    - `flowReturn.panel === 'results'`.

    On master the panel stays `'results'` and the index stays 0.
14. **Old event is a no-op (GK condition 7).** With `'timeseries'` open, `{kind: 'set_snapshot', snapshot_iso: '2026-06-01T12:00:00'}` (no `snapshot_index`) leaves all of these unchanged:
    - `activeSlidePanel` (still `'timeseries'`);
    - `resultsSnapshotIdx`;
    - `resultsOverlayEnabled`;
    - `flowReturn` (still `null`).

    On master it switches the panel to `'results'`.
15. **Selection behind a panel.** With `activeSlidePanel: 'timeseries'`, `select_component` Line L2 gives:
    - `activeSlidePanel === null`;
    - the selection set;
    - `rightPanelOpen === true`;
    - `flowReturn.panel === 'timeseries'`.

    Calling `returnToOrigin()` then reopens `'timeseries'` in Expert. In Guided it reopens the recorded panel, with `fieldDrawer` restored when one was set before. With `'issues'` open, Issues stays open and `flowReturn` stays `null`. With no panel open, `flowReturn` stays `null`. On master `'timeseries'` stays open.
16. **Edit request.** With `'results'` open, `{kind: 'open_properties', component_class: 'Line', name: 'L2'}` gives:
    - the slide panel closed;
    - `flowReturn.panel === 'results'`;
    - `selectedComponent` set to `{type: 'Line', name: 'L2'}`;
    - `propertiesEditRequest` set to `{type: 'Line', name: 'L2'}`.

    On master the unknown kind does nothing.
17. **Canvas views.**
    - `{kind: 'navigate', panel_id: 'map', canvas_view: 'hybrid'}` gives `canvasView === 'hybrid'`, and `'site'` gives `'site'`.
    - With `'overview'` open beforehand, `flowReturn.panel === 'overview'`.
    - With no `canvas_view`, `map` still gives `'satellite'` and `topology` still gives `'blank'`.

    On master hybrid gives `'satellite'`.

`src/components/SnapshotPicker.a11y.test.tsx` (add one case):

18. Mock `getSnapshots` with a `periods` array of two periods, three snapshots each, and a solved status. Then `act(() => setResultsSnapshotIdx(4))`.
    - The period `<select>` (title "Investment period to display") shows the second period.
    - `resultsSnapshotIdx` stays 4.

    On master it is clamped back to 2.

`src/layout/PropertiesPanel.editRequest.test.tsx` (add cases, following its existing ones):

19. A `requestPropertiesEdit` for a selected Line opens `LinePanel`'s Edit Parameters form and clears the request. The same holds for a Transformer. On master the request is never consumed.

### Inventory and control matrix

`smoke/assistant_inventory.py` reads `harness/catalogue.py`, `harness/toolsets.py`, `Results.tsx` and `uiStore.ts`. This task changes the `ui_select_component` and `ui_open_panel` input schemas, so the inventory goes stale. Regenerate it; never hand-edit or hand-merge the JSON:

```bash
python pypsa-gui/backend/smoke/assistant_inventory.py --write
python pypsa-gui/backend/smoke/assistant_inventory.py --check        # "Inventory and GUI navigation schemas match: 287 tools, 21 result tabs, 23 panels ..."
python pypsa-gui/backend/smoke/assistant_control_matrix.py --check   # if it reports stale: --write, then --check again
```

The tool count stays 287 under G4 option a. Review the regenerated diff: only the two tools' `input_keys` and `schema_sha256` (and `generated_at`) should change. Every `route` stays `["_ui_event_"]`, and `tests/test_route_chat_disposition.py` passes with no `SERVED_BY_TOOL` change (GK condition 5). If G-a merged first, merge `master` and run `--write` again; never hand-merge the JSON.

### Commands

From the repo root. Install once with `pixi install --frozen -e default -e test`.

```bash
pixi run gui-tests tests/test_navigation_reach.py tests/test_lc14_navigation_reach_journey.py tests/test_chat_tools_dispatch.py
pixi run gui-tests tests/test_assistant_navigation_coverage.py tests/test_assistant_navigation_provider_parity.py tests/test_chat_tools_schema_panels.py tests/test_chat_compare_navigate.py tests/test_navigation_join.py tests/test_results_source_navigation.py tests/test_assistant_inventory.py tests/test_assistant_control_matrix.py tests/test_tool_error_kind_manifest.py tests/test_chat_profile_binding.py tests/test_project_route_acl_policy.py tests/test_route_chat_disposition.py tests/test_oj05_selected_line_edit_journey.py
pixi run gui-tests   # full backend suite before opening the PR
cd pypsa-gui/frontend && npx vitest run src/components/ChatPanel.navigationReach.test.tsx src/components/ChatPanel.applicationNavigation.test.tsx src/components/ChatPanel.hubDesignPanel.test.tsx src/components/SnapshotPicker.a11y.test.tsx src/layout/PropertiesPanel.editRequest.test.tsx src/layout/flowReturn.test.tsx src/components/ChatPanel.manifest.test.tsx src/components/AssistantDock.eviction.test.tsx src/components/MapModeSwitcher.test.tsx && npx tsc -b
cd pypsa-gui/frontend && npm test   # full vitest suite before opening the PR
```

`gui-tests` runs `python -m pytest` in `pypsa-gui/backend`, so test paths are relative to that folder. Node and npm come from `.pixi/envs/default`; if `npx` is not on `PATH`, prefix with `pixi run -e default`.

## Out of scope / don't touch

- **LC-14 items 4 and 5.** These are the `guided_step` targets and the campus section ids. PR #691 is changing those files. Do not touch `pages/hubDesign/*` (including `hubDesignStore.ts`), `chapters.ts` or `pages/CampusElectricalPanel.tsx`.
- **Campus and grid-code files:** `routers/campus_electrical.py`, `services/campus_*.py`, their frontend and their tests.
- **Other services:** `services/results/carrier_kpis.py`, `services/profile_shapes.py`, the renewable-profile files (`services/profile_gen/*`, `routers/profiles.py`, `pages/ProfileStudio*`, anything under `.scratch/renewable-profiles/`), and `gridspine/`.
- **Other harness files:** `harness/navigation.py`, `harness/toolsets.py`, `harness/providers/*`, `harness/compose.py`, `harness/prompts/*`. Also `TOOL_ROUTES` and every catalogue entry other than the three UI-control tools.
- **Other frontend files:** `components/MapModeSwitcher.tsx`, `pages/SiteCanvas.tsx`, `pages/MapCanvas.tsx`, `components/liveWalk.ts`, `layout/flowNavigation.ts`, the `KIND_COPY` / banner routing in `ChatPanel.tsx`, and any CSS or styling.
- **No new tool, no new `error_kind`, no new panel id in `SAFETY_PANEL_ENUM`, and no new UI copy.** The Back button and its "Back to <origin>" label already exist.
- `layout/flowNavigation.ts` beyond the one `withReturnFrom` export, and `layout/FlowReturnButton.tsx`.
- `docs/agents/*`, `scripts/coord/*` and `doc/release_notes.md`.

## Gatekeeper and UX decisions (2026-10-23)

The gatekeeper ruled option (a) on G1-G7 with conditions 1-8. The UX reviewer ruled (a) on G2, G3 and G6, with the Back-to-origin and wording conditions. All of them are folded into the sections above; this list is the record.

- **G1 (a).** A snapshot not on the axis is refused with `invalid_navigation` and nothing changes on screen. There is no nearest-snapshot fallback.
- **G2 (a).** `set_snapshot` closes the slide panel, sets the index and switches the results overlay on.
- **G3 (a).** `select_component` closes an open slide panel, except Issues, so Properties is visible.
- **G4 (a).** `open_editor` on `ui_select_component` emits kind `open_properties`, at read tier. There is no new tool, and the count stays 287.
- **G5 (a).** Eight classes get an edit card: Bus, Line, Transformer, Link, Generator, StorageUnit, Store and Load. `LinePanel` and `TransformerPanel` gain the edit-request effect, and other classes are refused.
- **G6 (a).** `canvas_view` on `ui_open_panel` is valid as `map` + satellite/hybrid/site or `topology` + blank. No panel id is added.
- **G7 (a).** `invalid_navigation` and `component_not_found` are reused; only their `why` text changes.

GK conditions:

1. **Index order.** `snapshot_index` must index the `GET /api/network/snapshots` array. Tests 3 and 9 prove it through that route.
2. **Time zones.** An ISO with `Z` or an offset against a tz-naive axis is refused with its own message, never converted. This follows `update_snapshot_weightings`' exact-string match. Test 4 covers it.
3. **`open_editor` is a strict boolean**, enforced by the schema and the dispatcher (tests 6 and 8). With it false or absent, the event is today's `select_component` event byte for byte (test 5).
4. **Existence check.** It uses the `get_component` accessor (`_GENERIC_CRUD_ATTRS` → `getattr(n, attr)` → `.index`). The 404 detail names the class and the name.
5. **No route changes for `canvas_view`.** No `TOOL_ROUTES` or `SERVED_BY_TOOL` change. `assistant_inventory.py --check` passes after `--write`. Whichever of G-a and I merges second regenerates; nobody hand-merges.
6. **Manifest.** `tool-error-kinds.json` keeps exactly its kinds, and the manifest tests pass unchanged.
7. **Optional event fields.** All new `ui_event` fields are optional. An old `set_snapshot` without `snapshot_index` is a no-op (test 14).
8. **Follow-up.** Plain `ui_select_component` still has no existence check. This is recorded as a follow-up in `.scratch/live-copilot/issues/14-navigation-reach.md`.

UX conditions:

- **Back to the closed panel.** Every panel the assistant closes goes through `withReturn`, via the new `withReturnFrom` export, so "Back to <origin>" restores it: in Guided the slide panel and drawer, in Expert the panel. Tests 13-17 cover Expert and Guided.
- **Wording.** No app-visible string changes. Refusal messages start with a capital letter and write ranges as "first … last" with real timestamps.
- **Screenshots.** None are needed; the vitest covers Guided and Expert.

## Repo rules

- Branch `outsource/navigation-reach` from current `master`. Open exactly one PR. Never force-push. Do not merge it; our coordinator merges.
- The PR body starts with the line `Outsourced task I`, then follows `.github/pull_request_template.md`. Mark the body as AI-generated. In the body:
  - name the one existing test you rewrote (`test_ui_set_snapshot_returns_event_marker`);
  - give the regenerated inventory diff summary;
  - say that plain `topology`/`map` also record a Back origin when they close a panel.
- The UX reviewer reviews the UI half against `docs/agents/review-ux.md`. The UX ruling waives screenshots for this PR; the Guided and Expert vitest cases replace them. Do not restyle anything: the dense 9-11px text and Inter are deliberate (DENSITY-1).
- If `master` moves before you open the PR (especially if G-a merges), merge `master` and regenerate the inventory as described under "Files to touch".
- Add one release-note fragment, `doc/release-notes.d/outsource-navigation-reach.md`, containing one bullet in this form:

  ```
  * Fix(pypsa-gui): The assistant can now set the canvas snapshot it names (or says it is not on the time axis), keeps a selected component's Properties in view, opens a component's edit form without saving, and switches the canvas to the Hybrid and Site 3D views.
  ```

  Never edit `doc/release_notes.md`. See `doc/release-notes.d/README.md`.
- Tests and docs go in the same PR. Do not skip, loosen or delete any existing test; the one rewrite named above is the only exception.
- Do not commit credentials or `.env` files.
- Follow the README layering rule: no wire or SDK names outside `harness/providers/`.

## Done when

- [ ] The 19 new tests pass, and each of them fails against `master`'s code.
- [ ] GK conditions 1-7 and the UX conditions under "Gatekeeper and UX decisions" each have their named test or check, and each holds.
- [ ] Every panel closed by `set_snapshot`, `select_component`, `open_properties` or `topology`/`map` sets `flowReturn` through `withReturnFrom`, in Expert and Guided. No close happens without one, and none happens when no panel was open.
- [ ] Refusal messages start with a capital letter and write ranges as "first … last" with real timestamps. No app-visible string changed.
- [ ] Every backend file in the second `gui-tests` command passes unchanged, and so do these frontend files: `ChatPanel.applicationNavigation.test.tsx`, `ChatPanel.hubDesignPanel.test.tsx`, `flowReturn.test.tsx`, `ChatPanel.manifest.test.tsx`, `AssistantDock.eviction.test.tsx` and `MapModeSwitcher.test.tsx`.
- [ ] `npx tsc -b` is clean.
- [ ] `assistant_inventory.py --check` and `assistant_control_matrix.py --check` pass after `--write`, with no hand edits to either JSON. No `TOOL_ROUTES` or `SERVED_BY_TOOL` entry changed.
- [ ] `tool-error-kinds.json` has exactly the kinds it had on `master`; only two `why` strings differ.
- [ ] The full `pixi run gui-tests` and `npm test` are green locally, or the PR lists each failure that also fails on `master`.
- [ ] `ui_select_component`, `ui_open_panel` and `ui_set_snapshot` all still classify as `Safety: read`. No tool, `error_kind` or panel id was added.
- [ ] The diff touches only the files under "Files to touch".
- [ ] One PR on `outsource/navigation-reach`, with a body starting `Outsourced task I`.
