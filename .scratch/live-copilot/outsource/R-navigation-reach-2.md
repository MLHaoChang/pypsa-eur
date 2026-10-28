<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

Status: ready-for-agent once PR #812 (LC-18, `claude/lc18-sensitivity-compare`) has merged — written 2026-10-28; GK B reviews the plan and the harness half, UX the frontend navigation

# Outsourced task R: chat reaches a Guided card and a section of the grid-study and GridSpine panels

Branch: `outsource/navigation-reach-2` · one PR against `master` · PR body starts with `Outsourced task R`

Source issue: `.scratch/live-copilot/issues/14-navigation-reach.md` (LC-14), items 4 and 5 only. Items 1, 2, 3 and 6 were task I (`.scratch/live-copilot/outsource/I-navigation-reach.md`), merged as #700 (`6cbdc8cb`). The issue's line numbers are from baseline `d006f4be`. Every pointer below was checked against `master` at `c86f572f`; #812 moves some lines in `catalogue.py`, `chat_tools.py` and `uiNavigate.ts`, so find each by its name after you merge `master`.

## Read first

1. `AGENTS.md` (repo root). Release notes and PR rules are there.
2. The issue (above), items 4 and 5.
3. **Task I's brief and what it built.** R follows the same pattern, so read I's brief, then these on `master`:
   - `pypsa-gui/frontend/src/components/uiNavigate.ts` (232 lines): the `NavigationRequest` type (l.65-97), `closePanelWithReturn` (l.121), the `navigate` branch (l.166-231), and how Scenarios opens with "Back to <origin>" (l.203-205, LC-17).
   - `pypsa-gui/backend/services/chat_tools.py`, `ui_open_panel` (l.5722-5822): enum checks raise `invalid_navigation` before any event (l.5750-5771), the `canvas_view` pairing check (l.5773-5780) and the optional keys copied into the event (l.5800-5822).
   - `pypsa-gui/backend/harness/catalogue.py`: `SAFETY_PANEL_ENUM` (l.57-93), the `ui_open_panel` declaration (l.2376-2414, schema l.2399-2412), and `TOOL_ROUTES` (`"ui_open_panel": _UI_EVENT`, l.5238).
   - `pypsa-gui/backend/harness/loop.py:3266-3300`, the compact acknowledgement the model sees (task I added `canvas_view` at l.3288).
   - Task I's tests: `pypsa-gui/backend/tests/test_navigation_reach.py` (`test_canvas_view_pairs` l.103, `test_schema_matches_dispatchers` l.111), `tests/test_lc14_navigation_reach_journey.py` (Tier A) and `pypsa-gui/frontend/src/components/ChatPanel.navigationReach.test.tsx`.
4. `pypsa-gui/backend/harness/README.md`: "The contract", and "Measuring parity", which says invalid navigation is refused before a UI event emits.
5. Item 4's code: `pypsa-gui/frontend/src/pages/hubDesign/hubDesignStore.ts` (84 lines; `HubStep` l.11-12, `STEP_SECTION` l.18-20, `setStep` l.74-77), `pages/hubDesign/HubDesignPanel.tsx` (`resetFor` l.74, auto-advance l.98-112, `pickSection` l.125-133), `pages/hubDesign/chapters.ts` (`ChapterOpen` l.17-20) and `pages/hubDesign/ChaptersSection.tsx:74`. The chat context already reports the card as `guided_step` (`utils/uiContext.ts:51, 98`), and the backend allow-lists the same five ids from the `hub-design` workflow (`harness/compose.py:164`, `_GUIDED_STEPS`).
6. Item 5's code: `pypsa-gui/frontend/src/pages/CampusElectricalPanel.tsx` (855 lines; the three parts `CampusPart` l.105 and their exports l.142-146; the sections each part renders l.243-276; `ResultsSection`'s `show` parts l.632-645), `pages/CampusIslandSection.tsx:210`, `pages/CampusGridCodeSection.tsx:179` and `pages/GridspinePanel.tsx` (1159 lines).
7. The request-then-scroll pattern to copy: `store/uiStore.ts` `settingsSectionRequest` (l.551, l.810, l.1088) and its consumer `components/AssistantModelSettings.tsx:443-464`, which waits for a mounted node before it scrolls and clears.
8. `docs/agents/review-ux.md`. The UX reviewer reviews the frontend half against it.

## Goal in plain words

A user can pick any card of the Guided hub design, and can scroll to any part of the grid-study and GridSpine panels. The assistant can open those panels but not a card or a part of them:

4. **A Guided card.** `ui_open_panel('hubDesign')` opens the hub design on whatever card it last showed. There is no way to say "the Goal card" (`hubDesignStore.ts:11`, `HubStep`).
5. **A section of the grid study or GridSpine.** Since LC-14 was written, the campus panel was split into three panels (`campusElectrical`, `campusStudy`, `campusResults`), and chat reaches each one. It still cannot reach a section inside one, such as Island in the study panel or Grid codes in the grid connection, or a section of `gridspine`.

Both are navigation fixes. Neither adds authority: `ui_open_panel` stays `Safety: read`, and nothing writes to the network or the project.

## User-facing outcome

- "Take me to the Goal card" opens the hub design on the Goal card, in Guided and in Expert.
- "Show me the island study" opens the grid study's settings panel scrolled to Island. "Open the grid codes" opens the grid connection scrolled to Grid codes. "Show the PowerFactory read-back" opens GridSpine scrolled to it.
- A step or section that does not exist, or that belongs to another panel, is refused, and nothing on screen changes.
- When the jump leaves a view, the existing "Back to <origin>" button appears and returns to it.
- **No new buttons, labels, panels, settings or UI copy.** The only text this task adds is the refusal message the assistant reads. The dense 9-11px text and Inter are deliberate (owner decision DENSITY-1); restyle nothing.

## Exact behaviour

### Item 4: `guided_step` on `ui_open_panel`

**Catalogue** (`catalogue.py`, the `ui_open_panel` schema): add a `guided_step` property, `{"type": "string", "enum": [...]}`, whose enum is **built from** `harness.workflows.get("hub-design").steps` (the step ids, in order), as `panel_section`'s enum is built from `_PANEL_SECTIONS` below. Never write the five ids as a literal (gatekeeper B's condition on #852). Add one clause to the description: "With panel_id hubDesign, guided_step opens that card: start, site, goal, results or improve." Keep `Safety: read.` and keep `panel_id` the only required key.

**Dispatcher** (`chat_tools.ui_open_panel`): add a keyword argument `guided_step: str | None = None`.

- Valid only with `panel_id` in `{"HubDesign", "hubDesign"}` and a value in the five steps. Read the five steps from the `hub-design` workflow, as `compose.py:164` does (`harness.workflows.get("hub-design").steps`); do not import compose's private `_GUIDED_STEPS`.
- Any other value, a non-string, or another panel raises, before any event, `HTTPException(400, detail={"error_kind": "invalid_navigation", "field": "guided_step", "message": "Choose a Guided card: start, site, goal, results or improve. Cards exist only in the hub design."})`. Build the list of ids in the message from the workflow too..
- On success, copy it into the event as `event["guided_step"]`, next to `canvas_view` (l.5819-5820).

**Frontend** (`uiNavigate.ts`): add `guided_step?: string` to `NavigationRequest` and to the `ui_event` frame type in `ChatPanel.tsx` (next to `canvas_view?: string`, l.2389).

In the `navigate` branch, when `panel === 'hubDesign'` and `d.guided_step` is one of `HUB_STEPS` (import it from `pages/hubDesign/hubDesignStore`), run the jump through `withReturnFrom`, the way Scenarios does (l.204):

```ts
withReturnFrom(() => {
  useUIStore.getState().setSlidePanel('hubDesign')
  useHubDesignStore.getState().setStep(d.guided_step as HubStep, { user: true })
})
```

- `{ user: true }` is what a rail click passes, so the auto-advance (`HubDesignPanel.tsx:98-112`) does not move the card away right after.
- `setStep` also sets `activeSection` (`hubDesignStore.ts:74-77`). That is what makes the card survive the panel's first `resetFor` for a project (`HubDesignPanel.tsx:74`), which resets `step` but not `activeSection`. A vitest below proves it for an unready store. If it fails for `improve` (which shares section `results` with `results`), stop and say so in the PR. Do not add store state without the gatekeeper's OK.
- An unknown `guided_step` from an old or new backend is ignored: the panel opens as today, with no Back button.
- Without `guided_step`, `hubDesign` behaves exactly as today.

**Tiles.** A Guided report tile already opens a target chat can reach: a Results tab, `campusStudy`/`campusResults`, or the Goal card (`chapters.ts:17-20`). So `guided_step` covers tiles. Add no tile argument; say so in the PR plan for the gatekeeper.

### Item 5: `panel_section` on `ui_open_panel`

**Name.** Call the argument `panel_section`. `section` is already taken by `open_run_result` (`NavigationRequest.section`, `loop.py` acknowledgement).

**The closed set.** Valid pairs (panel id → section ids). Each id names one existing heading:

| `panel_id` (either spelling) | `panel_section` | The section it scrolls to |
|---|---|---|
| `campusElectrical` | `campus_file` | `CampusFileSection` (`CampusElectricalPanel.tsx:246`, defined l.290) |
| | `grid_connection` | "Grid code and PCC" (`GridConnectionSection`, l.362) |
| | `connection_limits` | `ConnectionLimits` (l.248) |
| | `grid_codes` | "Grid codes" (`CampusGridCodeSection.tsx:179`) |
| | `library` | `CampusLibrarySection` (l.253) |
| `campusStudy` | `study_settings` | "Study settings" (`RunSection`, l.440, or the empty one at l.260) |
| | `island` | "Island" (`CampusIslandSection.tsx:210`) |
| `campusResults` | `grid_code_results` | the grid-code part of `ResultsSection` (`show` `grid_code`, l.632-645) |
| | `electrical_design` | the electrical-design part (`show` `electrical_design`) |
| `gridspine` | `dispatch_source` | "Dispatch source" (`GridspinePanel.tsx:400`) |
| | `configuration` | "Configuration" (l.540) |
| | `ranked_snapshots` | "Ranked snapshots" (l.195) |
| | `assumptions_ledger` | "Assumptions ledger" (l.224) |
| | `readback` | "PowerFactory read-back" (l.612) |
| | `connection_capacity` | "Connection capacity" (l.854) |
| | `connection_assessment` | "Connection-point assessment" (l.1052) |

Put the table in the PR plan. The gatekeeper and UX may trim it; build what they approve.

**Catalogue.** Add `"panel_section": {"type": "string", "enum": [<every id above>]}` and one clause: "With campusElectrical, campusStudy, campusResults or gridspine, panel_section scrolls to one section of that panel (for example island, grid_codes, readback)." Keep the backend's pair table in one constant in `chat_tools.py` (for example `_PANEL_SECTIONS: dict[str, frozenset[str]]`, keyed by the lower-camel panel id) and build the enum from it, so the two cannot drift.

**Dispatcher.** Add `panel_section: str | None = None`. Normalise `CampusElectrical` / `CampusStudy` / `CampusResults` / `GridSpine` to their lower-camel id first. A value that is not a string, is unknown, or does not belong to that panel raises, before any event, `invalid_navigation` with `field: "panel_section"` and a message that names the panel by the title the user sees and lists its valid ids, e.g. "Choose a section of the grid study: island or study_settings." Copy it into the event as `event["panel_section"]`.

**Refusal wording (UX condition, #852).** Tool results the chat shows are covered by the wording rules in `docs/agents/review-ux.md`. In every refusal message:

- name the panel by its on-screen title (`App.tsx:142-146`), never by a `panel_id`: `campusElectrical` → "the grid connection", `campusStudy` → "the grid study", `campusResults` → "the grid study results", `gridspine` → "Planning → dynamics";
- the ids the model must retry with stay, as plain words: no repr quotes, no backticks;
- a colon, not an em dash, before the list; join the last two ids with "or".

**Frontend: one request, read by the panel.** Follow `settingsSectionRequest`:

- `store/uiStore.ts`: add `panelSectionRequest: { panel: SlidePanel; section: string } | null` (initial `null`), `requestPanelSection(panel, section)` and `clearPanelSectionRequest()`.
- `uiNavigate.ts`: when `panel` is one of the four panels and `d.panel_section` is a string, run the jump through `withReturnFrom`, setting the slide panel and then `requestPanelSection(panel, d.panel_section)`. Without `panel_section`, these panels open as today.
- Mark each section with a wrapper attribute and nothing else: `data-panel-section="<id>"` on the existing element or a plain `<div>` around it. No new class, style, text or element that shows.
- Consume it once per panel, in `CampusView` (the three campus parts) and in `GridspinePanel`: when the request's `panel` is this panel, wait until the panel's data has loaded, then look up `[data-panel-section="<id>"]` inside the panel, `scrollIntoView({ block: 'start' })` it if present, and clear the request either way. A section that is not rendered for this project (for example `study_settings` before a campus file exists, l.256-265) leaves the panel at its top and clears the request; it must not linger and scroll later. Put the shared lookup in one small hook in the same folder rather than copying it.

### Guided stays Guided (UX)

`guided_step` and `panel_section` open the panel the same way `ui_open_panel` already opens that panel in the current mode. Neither ever switches Guided to Expert (or Expert to Guided). Vitest case 12 asserts the mode is unchanged.

### No self-return (UX)

When the target is the view already showing (the same Guided card with `hubDesign` open, or the same panel with the same section requested), do not call `withReturnFrom`: scroll to the section, or do nothing for a card already showing. A "Back to <origin>" must never point at the view it would return from. Vitest case 13 covers both.

### Back to the view the jump left (UX)

Both jumps go through `withReturnFrom` (`layout/flowNavigation.ts:103`), so `flowReturn` records the panel, the section, and in Guided the drawer (`currentOrigin`, l.65-92). "Back to <origin>" then restores them. This also covers moving between cards while `hubDesign` is already open: the origin's `activeSection` is the previous card. Change nothing in `flowNavigation.ts` or `FlowReturnButton.tsx`.

### The model's acknowledgement

In `loop.py` (the dict at l.3271-3300), add `"guided_step": ui_event_payload.get("guided_step")` and `"panel_section": ui_event_payload.get("panel_section")` next to `canvas_view`. Add nothing else and change nothing else in `loop.py`.

### The navigation ACK (decision for the plan)

`harness/navigation.py::issue` (l.133-147) issues an acknowledgement only for a navigate with no keys outside `kind`, `panel_id`, `results_tab` and the load-flow keys. So `hubDesign` with `guided_step`, or a campus panel with `panel_section`, gets no ACK record, as `map` with `canvas_view` gets none today. Recommended: leave `navigation.py` unchanged (option a). Name this in the PR plan so the gatekeeper can choose instead (b): extend `issue` to carry the new key in its target, with the frontend ACK check that goes with it. Build (b) only if the gatekeeper asks.

## Files it may touch

- `pypsa-gui/backend/harness/catalogue.py`: the `ui_open_panel` schema and description only. No `TOOL_ROUTES` change; `ui_open_panel` stays `_UI_EVENT`. No new tool, no new panel id in `SAFETY_PANEL_ENUM`.
- `pypsa-gui/backend/services/chat_tools.py`: `ui_open_panel` and the one new `_PANEL_SECTIONS` constant next to it, only.
- `pypsa-gui/backend/harness/loop.py`: the two acknowledgement keys only.
- `pypsa-gui/backend/harness/README.md`: one sentence in "Measuring parity", next to task I's sentence, saying the assistant can also open a Guided card and a section of the grid-study and GridSpine panels.
- `pypsa-gui/frontend/src/components/uiNavigate.ts`, and the `ui_event` frame type in `components/ChatPanel.tsx` only.
- `pypsa-gui/frontend/src/store/uiStore.ts`: the `panelSectionRequest` state and its two actions only.
- `pypsa-gui/frontend/src/pages/CampusElectricalPanel.tsx`, `CampusIslandSection.tsx`, `CampusGridCodeSection.tsx`, `GridspinePanel.tsx`: the `data-panel-section` markers and the request consumer only. One new hook file next to them.
- Tests (below), adding cases to task I's test files and changing none of their existing cases.
- `doc/release-notes.d/outsource-navigation-reach-2.md`: the release-note fragment.

## Files it must NOT touch

- **Task Q's lines.** Q (`.scratch/harness/outsource/Q-tool-selection-pins.md`) also starts after #812 and edits `harness/providers/wiring.py` and the `validate_network` return annotation in `chat_tools.py` (`def validate_network() -> list[dict]:`, l.3607 on `c86f572f`; Q's brief says about l.3543). R touches neither. R and Q share no line; whichever merges second merges `master` without conflict.
- `harness/navigation.py` (unless the gatekeeper picks (b)), `harness/compose.py`, `harness/toolsets.py`, `harness/providers/*`, `harness/prompts/*`, every catalogue entry other than `ui_open_panel`.
- `pypsa-gui/tool-error-kinds.json` and `tool-error-kinds.d/`: `invalid_navigation` already exists and its `why` already names `ui_open_panel`. Add no kind.
- `.scratch/live-voice/*.json`: the inventory, control matrix and route parity are no longer committed (their `--write` writes a local file for reading only). Never commit them.
- `layout/flowNavigation.ts`, `layout/FlowReturnButton.tsx`, `pages/hubDesign/*` other than the import of `HUB_STEPS`/`useHubDesignStore` from `uiNavigate.ts` (no edits there), `chapters.ts`, `ChaptersSection.tsx`, any CSS or styling.
- `.scratch/live-copilot/issues/14-navigation-reach.md`: task S adds its `Progress:` line, and the coordinator sets `Status:` when R merges.
- `docs/agents/*`, `scripts/coord/*`, `doc/release_notes.md`.

## Tests

Every new test must fail on `master`'s code; say why for each in the PR body. Use the mocked wire only. Make no paid model calls.

### Backend unit, add to `tests/test_navigation_reach.py`

1. `test_guided_step_pairs`: `ui_open_panel("hubDesign", guided_step=s)` carries `guided_step == s` for each of the five; `("HubDesign", guided_step="goal")` too. `("hubDesign", guided_step="nope")`, `("results", guided_step="goal")` and `("hubDesign", guided_step=3)` raise 400 `invalid_navigation` with `field == "guided_step"`. Without it, the `hubDesign` event is today's, byte for byte (`json.dumps` equal to `{"_ui_event": True, "kind": "navigate", "panel_id": "hubDesign"}`).
2. `test_guided_steps_match_the_workflow`: the schema enum equals the `hub-design` workflow's step ids, in order.
3. `test_panel_section_pairs`: one valid id per panel is carried (`campusStudy`/`island`, `campusElectrical`/`grid_codes`, `campusResults`/`electrical_design`, `gridspine`/`readback`, and `CampusStudy`/`island`). `("campusStudy", panel_section="grid_codes")` (wrong panel), `("results", panel_section="island")`, `("gridspine", panel_section="nope")` and a non-string raise `invalid_navigation` with `field == "panel_section"`, and the message starts with a capital, names the panel by its on-screen title (no `panel_id` such as `campusStudy` in it), and lists the panel's ids.
4. Extend the pattern of `test_schema_matches_dispatchers` in a new test: both arguments validate against the catalogue schema (`tests.test_chat_tools_schema_panels._errors`), the `guided_step` enum equals the step ids built from the `hub-design` workflow (not a literal) and every one of them validates, the `panel_section` enum equals the union of `_PANEL_SECTIONS`, `safety_tier_for("ui_open_panel") == "read"`, and `TOOL_ROUTES["ui_open_panel"]` is still `_UI_EVENT`.

### Tier A journey, add to `tests/test_lc14_navigation_reach_journey.py`

Inside `TestNavigationJourney` (all providers × `expert`/`guided`, `oj_grid` fixture, `run_journey`):

5. `test_chat_opens_a_guided_card`: steps `ui_open_panel({"panel_id": "hubDesign", "guided_step": "goal"})`, then end. `calls == ["ui_open_panel", None]`; one `ui_event` frame with `panel_id == "hubDesign"` and `guided_step == "goal"`; `results[0]["guided_step"] == "goal"`; no `tool_error` and no `tool_pending_confirmation` frame.
6. `test_chat_opens_a_panel_section`: steps `ui_open_panel({"panel_id": "campusStudy", "panel_section": "island"})`, then end. One `ui_event` frame carrying `panel_section == "island"`; `results[0]["panel_section"] == "island"`; no `tool_error` or `tool_pending_confirmation`.
7. `test_chat_refuses_a_section_of_another_panel`: steps `ui_open_panel({"panel_id": "campusStudy", "panel_section": "grid_codes"})`, then end. One `tool_error` with `error_kind == "invalid_navigation"`; no `ui_event` frame.

### Frontend vitest

Add to `src/components/ChatPanel.navigationReach.test.tsx`, inside its `describe.each(['expert', 'guided'])` (reset `useHubDesignStore` with `HUB_DESIGN_INITIAL` in `beforeEach` as well):

8. **Guided card.** With `activeSlidePanel: 'timeseries'`, applying `{kind: 'navigate', panel_id: 'hubDesign', guided_step: 'goal'}` gives `activeSlidePanel === 'hubDesign'`, hub `step === 'goal'`, `userMovedRail === true`, `activeSection === 'scenarios'` and `flowReturn.panel === 'timeseries'`. `returnToOrigin()` reopens `'timeseries'`. An unknown `guided_step` opens `hubDesign` and leaves `step` and `flowReturn` unchanged. On `master` the step stays `'start'`.
9. **Card survives the first reset.** Add a case to `src/pages/hubDesign/HubDesignPanel.flow.test.tsx` (add; change none): apply `guided_step: 'site'` before the panel has run `resetFor` for the project, mount it, and assert the Site card shows. Do the same for `'improve'` (see item 4's note).
10. **Panel section request.** With no panel open, `{kind: 'navigate', panel_id: 'campusStudy', panel_section: 'island'}` gives `activeSlidePanel === 'campusStudy'` and `panelSectionRequest` equal to `{panel: 'campusStudy', section: 'island'}`; with `'overview'` open beforehand, `flowReturn.panel === 'overview'`. Without `panel_section`, `panelSectionRequest` stays `null` and `flowReturn` is unchanged from today.
11. **Scroll and clear.** In `src/pages/CampusElectricalPanel.test.tsx` and `src/pages/GridspinePanel.test.tsx` (add cases, following their mocks): with a request for `island` (and for `readback`), after the data loads, `scrollIntoView` was called on the `[data-panel-section="island"]` node and `panelSectionRequest` is `null`. A request for a section that is not rendered (e.g. `study_settings` with no campus file) clears without a scroll.
12. **Guided stays Guided.** In the `guided` pass of case 8 and case 10, the UI mode after the jump is still Guided; in the `expert` pass it is still Expert.
13. **No self-return.** With `hubDesign` open on the Goal card, applying `guided_step: 'goal'` leaves `flowReturn` unchanged (no "Back to" pointing at the same card). With `campusStudy` open, applying `panel_section: 'island'` sets the scroll request but leaves `flowReturn` unchanged.

### Inventory, control matrix and route parity

R changes `ui_open_panel`'s input schema. Run the checks; never hand-edit or commit the JSON:

```bash
cd pypsa-gui/backend
python -m smoke.assistant_inventory --check
python -m smoke.assistant_control_matrix --check
python -m smoke.assistant_route_parity --check
```

Only `ui_open_panel`'s `input_keys` and `schema_sha256` should change in the in-memory inventory (`--write` to read it locally; do not commit it). The tool count is unchanged. No route parity row changes: `panel_section` and `guided_step` are `_UI_EVENT` arguments, and `tests/test_route_chat_disposition.py` passes with no `SERVED_BY_TOOL` change.

### Commands

From the repo root. Install once with `pixi install --frozen -e default -e test`.

```bash
pixi run gui-tests tests/test_navigation_reach.py tests/test_lc14_navigation_reach_journey.py tests/test_chat_tools_schema_panels.py tests/test_assistant_navigation_coverage.py tests/test_assistant_navigation_provider_parity.py tests/test_navigation_join.py tests/test_assistant_inventory.py tests/test_assistant_control_matrix.py tests/test_assistant_route_parity.py tests/test_tool_error_kind_manifest.py tests/test_route_chat_disposition.py tests/test_guided_mode_prompt.py
pixi run gui-tests   # full backend suite before opening the PR
cd pypsa-gui/frontend && npx vitest run src/components/ChatPanel.navigationReach.test.tsx src/components/ChatPanel.applicationNavigation.test.tsx src/components/ChatPanel.hubDesignPanel.test.tsx src/pages/hubDesign/HubDesignPanel.flow.test.tsx src/pages/CampusElectricalPanel.test.tsx src/pages/GridspinePanel.test.tsx src/layout/flowReturn.test.tsx && npx tsc -b
cd pypsa-gui/frontend && npm test   # full vitest suite before opening the PR
```

`gui-tests` runs `python -m pytest` in `pypsa-gui/backend`. Node and npm come from `.pixi/envs/default`; if `npx` is not on `PATH`, prefix with `pixi run -e default`.

## Review

- **Gatekeeper B** reviews the plan (open the PR as a draft with the plan at the top: the section table, the ACK choice (a)/(b), the tile note) and then the harness half: `catalogue.py`, `chat_tools.py`, `loop.py`, the tests, against `docs/agents/review-gk.md`.
- **UX** reviews the frontend navigation against `docs/agents/review-ux.md`: Back to <origin> in Guided and Expert, no visible change in the panels, and the refusal wording. **Evidence in the PR (UX condition):** screenshots in Guided and in Expert of: the Goal card reached from another card, with the Back pill; Island in the grid study; the PowerFactory read-back in GridSpine (Planning → dynamics); Back returning to the origin; one refusal as the chat shows it.

## Repo rules

- **Ordering.** Start only after PR #812 (LC-18, `claude/lc18-sensitivity-compare`) has merged. It edits `harness/catalogue.py`, `services/chat_tools.py`, `harness/loop.py` and `uiNavigate.ts`. Branch from the new `master`. If `master` moves again before you open the PR, merge it and rerun the three `--check` commands.
- Branch `outsource/navigation-reach-2`. One PR. Never force-push. Do not merge it; the coordinator merges.
- The PR body starts with `Outsourced task R`, then follows `.github/pull_request_template.md`. Mark it AI-generated. Include the plan, the in-memory inventory diff summary, and for each new test why it fails on `master`.
- Add one release-note fragment, `doc/release-notes.d/outsource-navigation-reach-2.md`, with one bullet:

  ```
  * Fix(pypsa-gui): The assistant can now open a named Guided card, and scroll the grid connection, grid study and GridSpine panels to a named section.
  ```

  Never edit `doc/release_notes.md`.
- Do not skip, loosen or delete an existing test. Do not commit credentials or `.env` files.
- Follow the README layering rule: no wire or SDK names outside `harness/providers/`.

## Done when

- [ ] The 13 new tests pass, and each fails against `master`'s code.
- [ ] Every invalid `guided_step` or `panel_section` (unknown, wrong type, wrong panel) is refused with `invalid_navigation` before any `ui_event`; refusal messages start with a capital and name panels by their on-screen title, never by `panel_id`.
- [ ] Without the new arguments, `ui_open_panel` events and the frontend behave exactly as on `master`.
- [ ] Each jump that leaves a view sets `flowReturn` through `withReturnFrom`, in Expert and Guided, and "Back to <origin>" restores it; a jump to the view already showing sets none; the mode never changes.
- [ ] The PR carries the UX screenshots (Guided and Expert) listed under Review.
- [ ] No app-visible string, style or element changed; only `data-panel-section` attributes were added.
- [ ] `ui_open_panel` is still `Safety: read` and `_UI_EVENT`; no tool, panel id or `error_kind` was added; `navigation.py` is unchanged unless the gatekeeper chose (b).
- [ ] The three `--check` commands, the named backend and frontend files, `npx tsc -b`, the full `gui-tests` and `npm test` are green, or the PR lists each failure that also fails on `master`.
- [ ] The diff touches only the files under "Files it may touch", and none of task Q's lines.
- [ ] One PR on `outsource/navigation-reach-2`, with a body starting `Outsourced task R`.
