<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Outsourced task E: the assistant names what it cannot do from chat, and every route has a chat disposition

Branch: `outsource/not-via-chat-list` · one PR against `master` · PR body starts with `Outsourced task E`

Source issue: `.scratch/live-copilot/issues/22-unsupported-by-chat.md` (LC-22), journey OJ-46. This brief is written against current `master`, not the issue's baseline (see "Drift since the issue" below).

## Read first

1. `AGENTS.md` (repo root). Release notes and PR rules are there.
2. The issue (above), and OJ-46 in `.scratch/live-copilot/owner-journeys.md` (~l.519): the user says "Create a user account for my colleague."; pass = a refusal with the reason, naming or opening the Admin page; fail = a guessed endpoint or a bare "I can't".
3. `pypsa-gui/backend/harness/README.md`: "The contract" (rule 2, "Tools are data"), the `find_capabilities` paragraph under "Durable assistant tasks and file delivery" (~l.776-780) and the discovered-tools paragraph under "Measuring parity" (~l.675).
4. `pypsa-gui/backend/services/assistant_tools.py::find_capabilities` (l.163-203). Outsourced task A (PR #660) added `session.discovered_tools` at l.201-202.
5. `pypsa-gui/backend/tests/test_write_surface_lock_policy.py`: `ROUTE_POLICY` (l.195), `KNOWN_GAPS` (l.328), `_walk_routes` (l.334) and `test_the_route_enumeration_is_complete` (l.441). Your route test copies this file's shape.
6. `pypsa-gui/backend/tests/test_oj06_ambiguous_storage_cost_journey.py` and `tests/journey_wire.py`. Your OJ-46 test follows them.

## Goal in plain words

Some things in the app must not be done from chat (admin, sign-in, API keys). Some are done by hand on purpose (map layout, file uploads). One is not built yet (project sharing). Today the assistant cannot tell these apart from things it simply has not found, so it improvises.

Two parts:

1. **A declared "not via chat" list.** Each entry has a title, a disposition, a reason and the page that does it. `find_capabilities(goal)` returns the matching entries next to its tools. The assistant can then say "that's on the Admin page; I can't do it from chat because …", and open the panel when there is one.
2. **A route test.** Every backend route (method + path) has exactly one chat disposition:
   - a chat tool reaches it;
   - it is on the "not via chat" list;
   - it is UI-internal;
   - it is a recorded gap.

   A new route with none of these fails the test, and the failure says how to classify it.

## User-facing outcome

- Asked for an admin action, a password change, an API key, project sharing or a map edit, the assistant gives the reason and names the page (and opens the panel when the entry has one) instead of guessing.
- **No new buttons, modes, settings, frames, tools or UI copy.** Nothing is callable that was not callable before.
- `find_capabilities(overview=True)` is unchanged.

## Drift since the issue (baseline `ae95a94a`, 1314 commits behind `master`)

Router endpoints: 442 in 32 modules then; now 491 in 39 modules, plus 2 in `main.py`. The route walker counts 493 method+path pairs; OpenAPI lists 492 of them, all but the SPA catch-all. New router modules: `flow.py`, `profiles.py`, `library_references.py`, `network_document_values.py`, `network_equipment_table.py`, `network_library_reference.py`.

- "Clear finished" in the issue's queue row already has a tool, `solve_queue_clear_finished` (`TOOL_ROUTES`, `POST /api/simulation/queue/clear_finished`). It does **not** go on the list.
- `cancel_queued` sits inside the issue's line range (`solve_queue.py:1087-1164`). It is per-caller, not super-admin (see its docstring, l.1120), so it is a recorded gap, not "out of chat".
- Every route the issue's table names still exists.

## Files to touch

### `pypsa-gui/backend/services/not_via_chat.py` (new)

Data plus one function. Do not put this list in `harness/catalogue.py`: another session is editing that file.

```python
DISPOSITIONS = ("out of chat", "manual", "unbuilt", "unbuilt; owner to decide later")

NOT_VIA_CHAT: tuple[dict, ...] = (
    {"id": "admin_users_and_orgs", "title": ..., "disposition": "out of chat",
     "reason": ..., "page": ..., "panel": None,
     "keywords": frozenset({...}), "routes": frozenset({("GET", "/api/admin/users"), ...})},
    ...
)
NOTE: str  # one sentence, at most 240 characters (text below)

def matches(words: set[str], limit: int = 2) -> list[dict]:
    """Entries with at least one keyword in `words`: most hits first, then declaration order, at most `limit`.
    Each returned dict has only title, disposition, reason, page and panel."""
```

- The result never carries `id`, `keywords` or `routes`. A snake_case id in the model's context looks like a tool name the model might try to call. Paths would invite the "guessed endpoint" that OJ-46 counts as a failure.
- `panel` is `None` or a value from `harness.catalogue.SAFETY_PANEL_ENUM` (l.57), so the model can pass it to `ui_open_panel`.
- `NOTE` text: "not_via_chat lists features you cannot do from chat. Tell the user the reason and the page; if panel is set you may open it with ui_open_panel. Do not look for another tool or an API for them."

The entries are below. Keep the ids, dispositions, panels and routes exactly. You may reword titles, reasons and pages within the limits the tests set (title ≤ 80 characters, reason 40–200, page ≤ 80). The one exception is `project_members`: its reason is pinned. Keywords are lowercase `[a-z0-9]+` tokens. Never use these generic words as keywords: `create get list run project projects result results network component settings data file change set show load flow user key keys token code pdf address coordinates`. (GK: the last eight collide with goals chat can do, e.g. a PDF datasheet's values, bus coordinates, a "user-defined" carrier, "key results".)

| id | disposition | page / panel | keywords (proposal) | routes |
|---|---|---|---|---|
| `admin_users_and_orgs` | out of chat | "Admin page (/admin), super-admins only" / `None` | users account accounts colleague colleagues organisation organisations organization organizations org admin invite smtp | all 9 routes of `routers/admin.py` (`/api/admin/...`) |
| `legacy_project_import` ‡ | out of chat | "Projects page (/projects)" / `None` | legacy unclaimed folder claim | `GET /api/projects/unclaimed`, `POST /api/projects/unclaimed/{legacy_name}/import`, `POST /api/projects/import-folder` |
| `sign_in_and_password` | out of chat | "Sign-in page" / `None` | password passwords login logout signin sign | the 6 routes of `routers/auth.py` other than `org-members` |
| `project_members` | unbuilt; owner to decide later | "Workspace panel, project members" / `"workspace"` | share sharing shared member members collaborator collaborators colleague colleagues | `GET` and `PUT /api/projects/{name}/members`, `GET /api/auth/org-members` |
| `assistant_keys_and_profiles` | out of chat | "Settings panel" / `"settings"` | api apikey secret secrets anthropic openai reveal | the 3 routes of `routers/local_settings.py`, and every `/api/chat/settings/...` route **except** `POST /api/chat/settings/llm/active` (9 routes) |
| `assistant_access_rule` ‡ | out of chat | "Chat panel, access settings" / `"chat"` | permission permissions restriction restrictions readonly autoapprove | `PUT /api/chat/restrictions` |
| `solve_queue_instance_controls` | out of chat | "Solve queue panel" / `"solveQueue"` | pause paused unpause halt | `POST /api/simulation/queue/pause`, `POST /api/simulation/queue/resume` |
| `map_layout_and_sites` | manual | "Map" / `"map"` | layout geocode geocoding osm openstreetmap thumbnail thumbnails | the 13 routes under `/api/projects/{name}/map_layout…` and `/api/projects/{name}/sites…`, plus `GET /api/geocode` and `GET /api/geocode/status` |
| `library_uploads_and_tariff_edit` | manual | "Commercial library" / `"library"` | meter metering tariff upload uploads | the 6 routes of `routers/library.py` that are not in `TOOL_ROUTES` (`PUT /api/library/items/{kind}/{name}`, `POST /api/library/meter_data`, `GET`/`POST /api/library/series`, `POST /api/library/series/upload`, `GET /api/library/series/{name}`) |
| `series_file_upload` ‡ | manual | "Time series panel" / `"timeseries"` | upload uploads | `POST /api/network/timeseries/upload`, `POST /api/projects/{name}/profiles/loads/upload`, `POST /api/projects/{name}/profiles/series/upload` |
| `grid_code_publish` | manual | "Campus electrical, Grid codes" / `"campusElectrical"` | publish published | the 10 `/api/campus-electrical/{name}/grid-codes/...` routes other than `GET …/grid-codes` and `POST …/documents/{document_id}/extract` (those two are reached by `campus_list_grid_codes` and `campus_extract_grid_code`) |

‡ = not in the issue's table. Each one is needed so that every route gets a disposition. The gatekeeper may move them; see Open questions.

Reasons:

- Write each reason from the issue's table: admin = security; sign-in and keys = secrets; queue = instance-wide and super-admin.
- For the `manual` rows, the reason says what chat *can* do, e.g. "From chat the assistant can list library items, import a URDB tariff and attach a tariff."
- `project_members` uses exactly this text (GK: the issue's wording was internal, and the assistant relays the reason to the user; the disposition still records that the owner decides later): `"Sharing a project is not available from chat yet. Add or remove its members in the Workspace panel."`

The issue's "Dictation" row ("Input path, LC-07") becomes UI-internal (below), not an entry: nobody asks chat to dictate.

### `pypsa-gui/backend/services/assistant_tools.py`

In `find_capabilities`, goal mode only. Add the new lines after l.202 (the `discovered_tools` assignment) and before `return out` (l.203):

```python
    from services import not_via_chat
    refused = not_via_chat.matches(words)
    if refused:
        out["not_via_chat"], out["not_via_chat_note"] = refused, not_via_chat.NOTE
        while len(json.dumps(out)) > 3800 and out["not_via_chat"]:
            out["not_via_chat"].pop()
        if not out["not_via_chat"]:
            del out["not_via_chat"], out["not_via_chat_note"]
```

Why this order keeps the tools whole:

- `words` is the goal's token set after stop-words and aliases (l.172-177). Reuse it; do not tokenise again.
- The tools are chosen and clipped first, exactly as today. `limit` (1–8) counts tools only, and the 3800-character clip (l.199-200) runs on the tools-only result.
- `session.discovered_tools` is still set from `items` (l.201-202), before any entry is added. So entries never become selection hints, and they are never offered or dispatchable.
- Entries are added afterwards, only into the room left under 3800 characters, and dropped first. A result full of tools therefore has no `not_via_chat` key. Measured on `master`: the OJ-46 goal with `limit=8` already fills 3777 characters with 7 tools. With the default `limit=3` it is 1612 characters, which leaves room.
- The keys are absent when nothing matches, so results for ordinary goals do not change.

Change nothing else: not the signature, the `items` fields, the `note`, `overview=True` (it returns at l.169, before all this), or the refusals.

### `pypsa-gui/backend/tests/test_not_via_chat.py` (new)

The list and `find_capabilities` (tests 1–8 below).

### `pypsa-gui/backend/tests/test_route_chat_disposition.py` (new)

The route test (tests 9–15). Model it on `test_write_surface_lock_policy.py`:

- **Enumerate** with `from tests.test_write_surface_lock_policy import _walk_routes` over `main.app.routes`. Do not copy it. `app.routes` alone yields lazy `_IncludedRouter` wrappers and almost no routes. Drop `HEAD` and `OPTIONS`.
- **Reached by a tool over HTTP:** every `(METHOD, PATH)` tuple in `harness.catalogue.TOOL_ROUTES` (l.4297). This is the same source `smoke/assistant_inventory.py` writes into `.scratch/live-voice/capability-inventory.json` as each tool's `route`. Today that is 259 pairs, all live. Read it; do not edit it.
- **`SERVED_BY_TOOL: dict[tuple[str, str], str]`**, in this test file. It maps a route to the tool that reaches the same handler or service function without calling the route. `TOOL_ROUTES` records those tools as `"_service_call_"` with no path (see the comment at `catalogue.py` ~l.4612-4616), so this map is the one new mapping. A later catalogue PR may move it into `TOOL_ROUTES`. Add an entry only after reading the tool's dispatcher in `services/chat_tools.py` and confirming it calls the route's handler or the same service function. When unsure, the route goes to `KNOWN_GAPS`. Expected groups, each to verify:
  - the 16 GridSpine routes with a matching `gridspine_*` tool, e.g. `GET …/connection` → `gridspine_get_connection_assessments` (not the two file uploads, `dispatch-source/external` and `readback/{hour}`);
  - about 18 `/api/campus-electrical/{name}` routes with a `campus_*` tool (e.g. `GET …` → `campus_get_study`, `POST …/run` → `campus_run_study`, `PUT …/island` → `campus_set_island_config`);
  - `GET /api/results/energy_flows` → `get_energy_flows`, `GET /api/results/load_shape` → `get_load_shape`, `GET /api/results/asset/{component_class}/{name}` → `get_asset_results`, `…/export.xlsx` → `export_asset_results`;
  - the document-values and equipment-table previews → `preview_document_values`, `preview_commercial_document_values`, `preview_equipment_mapping`;
  - `POST /api/chat/settings/llm/active` → `set_active_profile`;
  - `GET /api/projects/{name}/uploads/{file_id}/blob` → `get_file_delivery` (it returns that link).
- **`UI_INTERNAL: dict[str, dict]`**, in this test file. Keyed by a short name; each value is `{"reason": <one line>, "routes": frozenset(...)}` with the routes written out, not prefixes. These 9 entries (51 routes):
  - `spa`: `GET /{full_path:path}`, "serves the built frontend's pages";
  - `health`: `GET /api/health`, "liveness probe for the desktop shell and CI";
  - `chat_panel`: the 31 routes of `routers/chat.py` and `routers/assistant_activity.py` other than `/api/chat/settings/...` and `PUT /api/chat/restrictions`, "the chat panel itself: stream, cards, history, checked-answer records, Activity";
  - `durable_conversations`: the 5 `/api/durable-conversations…` routes, "the chat panel's own thread persistence and rebinding";
  - `mcp`: the 3 `/api/mcp` routes, "the MCP protocol endpoint; external agents reach the same catalogue through it";
  - `editor_lock`: `POST`/`DELETE /api/projects/{project_id}/lock`, `POST …/lock/heartbeat`, "the browser tab's editing lock; the assistant acts under the tab's lock";
  - `log_feeds`: `GET /api/simulation/log_stream`, `GET /api/simulation/queue/{job_id}/log_stream`, `GET …/log_history`, "solver log feeds for the log panel; the assistant follows jobs with wait_for_job";
  - `dictation`: the 2 `/api/dictation/...` routes, "the composer's voice input; the words arrive as the user's message (LC-07)";
  - `browser_upload`: `POST /api/projects/{name}/uploads`, `GET …/uploads/{file_id}/signature`, "the browser's file upload and its signature; the assistant uses uploads by file ID".

  You may add at most 2 more entries if a route is plainly only for a screen (e.g. a form schema). Name each one in the PR body.
- **`KNOWN_GAPS: dict[tuple[str, str], str]`**, in this test file. Routes that no tool reaches yet and that are not refusals (e.g. `PATCH /api/projects/{name}/settings`, studies `PATCH`/`DELETE`, `POST /api/network/reset`, `POST /api/simulation/queue/cancel_queued`). The value names the page where the user does it today. It is a ratchet:
  - a gap that gains a tool fails the exclusivity test until it is removed;
  - `_KNOWN_GAPS_CEILING` is set to the count at merge, so the list cannot grow.

  These stay out of `find_capabilities`: a route without a tool does not mean the capability is missing (another tool may cover it).

Expected totals on today's `master`: 493 = 259 (`TOOL_ROUTES`) + 68 (`NOT_VIA_CHAT`) + 51 (`UI_INTERNAL`) + 115 (`SERVED_BY_TOOL` + `KNOWN_GAPS`, about 44 + 71). If outsourced task D merges first, `POST /api/network/library_reference/preview` becomes `POST /api/library_reference/preview`. Classify whichever path is live; the stale-entry test catches the other. Either path goes in `SERVED_BY_TOOL` as `apply_library_reference`: its confirmation card comes from the same `edit_card.preview` (`_library_reference_preview`).

### `pypsa-gui/backend/tests/test_oj46_feature_chat_must_refuse_journey.py` (new)

OJ-46 in Tier A (test 16).

### `pypsa-gui/backend/harness/README.md`

Append one short paragraph after the `find_capabilities` paragraph (~l.776-780). It says:

- goal-mode results carry matching `not_via_chat` entries (title, disposition, reason, page, panel) from `services/not_via_chat.py`;
- the entries are never offered, dispatched or added to `discovered_tools`, and they only use room the tools left under 3800 characters;
- `tests/test_route_chat_disposition.py` gives every route one chat disposition.

### `doc/release-notes.d/outsource-not-via-chat-list.md`

The release-note fragment (below).

## Tests

All three new files import `services.not_via_chat`, which does not exist on `master`, so every new test fails there. Tests 3 and 16 would also fail on `master`'s `find_capabilities` on their own.

In `tests/test_not_via_chat.py`. Use the `client` and `helpers_state` fixtures and `chat_tools.set_chat_session(chat_service.ChatSession())` for the `find_capabilities` tests, as `tests/test_assistant_tool_reachability.py::test_unbound_discovery_labels_study_tools_ineligible` does.

1. `test_every_entry_is_well_formed`:
   - ids are unique;
   - no id is a tool name (`harness.catalogue.TOOLS`) or a key of `chat_tools.DISPATCHERS`;
   - `disposition` is in `DISPOSITIONS`;
   - the length limits above hold;
   - `panel` is `None` or in `SAFETY_PANEL_ENUM`;
   - keywords are non-empty `[a-z0-9]+` tokens, none of them a banned generic word;
   - `routes` is non-empty and holds `(METHOD, PATH)` tuples.
2. `test_the_issue_rows_are_declared`:
   - each id in the table exists with its disposition and panel;
   - `project_members` has disposition `"unbuilt; owner to decide later"` and the pinned reason;
   - `solve_queue_instance_controls.routes` does not contain `clear_finished`.
3. `test_oj46_goal_returns_the_admin_entry`: `find_capabilities("Create a user account for my colleague.")` (default limit):
   - `out["not_via_chat"][0]["title"]` is the admin entry's title;
   - every returned entry has exactly the keys `{title, disposition, reason, page, panel}`;
   - `"/api/"` does not appear in `json.dumps(out["not_via_chat"])`;
   - `out["not_via_chat_note"] == NOTE`;
   - `len(json.dumps(out)) <= 3800`.
4. `test_refusal_goals_find_their_entry` (parametrised). The first entry is:
   - `"Share this project with Anna"` → `project_members`;
   - `"Change my password"` → `sign_in_and_password`;
   - `"Set my Anthropic API key"` → `assistant_keys_and_profiles`;
   - `"Pause the solve queue"` → `solve_queue_instance_controls`;
   - `"Geocode the site address"` → `map_layout_and_sites`.
5. `test_ordinary_goals_get_no_refusal` (parametrised): `"get_component_schema"`, `"run the simulation"`, `"show the load flow results"`, `"parameter changes chart delivery"` and `"Change the storage cost to 150"`, plus the collision guards `"Read the capex from this PDF datasheet"`, `"Set the coordinates of bus 3"`, `"What are the key cost drivers"` and `"Add a user-defined carrier"`, return no `not_via_chat` key.
6. `test_entries_never_become_discovered_tools`: after the OJ-46 goal,
   - `session.discovered_tools == tuple(i["name"] for i in out["items"])`;
   - every name is in `TOOLS`;
   - no entry title or id is in it.
7. `test_entries_never_displace_a_tool`: run the OJ-46 goal with `limit=8` and with `limit=3`. Monkeypatch `not_via_chat.NOT_VIA_CHAT = ()` and run both again. `out["items"]` is identical with and without the list, and every result is ≤ 3800 characters. With `limit=3` the `not_via_chat` key is present. (With `limit=8` it is absent on today's catalogue, as there is no room; do not assert that, since it depends on description lengths.)
8. `test_overview_is_unchanged`: `find_capabilities(overview=True)` has no `not_via_chat` key.

In `tests/test_route_chat_disposition.py`:

9. `test_the_route_enumeration_is_complete`: every OpenAPI operation (`main.app.openapi()`) is enumerated, and there are more than 400 pairs. This guards the guard, as l.441 of the lock-policy test does.
10. `test_every_route_has_exactly_one_chat_disposition`: the main test. Build each route's set of buckets (`tool_routes`, `served_by_tool`, `not_via_chat:<id>`, `ui_internal:<name>`, `known_gap`). Fail on routes with none, listing method, path and `endpoint.__module__.__name__`, with this message:

    ```
    Give each route exactly one chat disposition:
      a chat tool calls it over HTTP      -> its tool's entry in harness/catalogue.py TOOL_ROUTES
      a _service_call_ tool reaches it    -> SERVED_BY_TOOL in tests/test_route_chat_disposition.py
      chat must refuse or point elsewhere -> an entry's routes in services/not_via_chat.py (reason + page)
      only the app's own screens call it  -> UI_INTERNAL in tests/test_route_chat_disposition.py (one-line reason)
    KNOWN_GAPS only shrinks: a new route without a tool is declared in services/not_via_chat.py as "unbuilt".
    ```

    Also fail, with its own message, on a route that has more than one bucket.
11. `test_no_disposition_names_a_missing_route`: every route in `NOT_VIA_CHAT`, `SERVED_BY_TOOL`, `UI_INTERNAL` and `KNOWN_GAPS` is live.
12. `test_served_by_names_real_tools`: every value of `SERVED_BY_TOOL` is in `TOOLS`.
13. `test_ui_internal_stays_small`: at most 11 entries, each reason 20–160 characters.
14. `test_known_gaps_only_shrink`: `len(KNOWN_GAPS) <= _KNOWN_GAPS_CEILING`.
15. `test_a_planted_route_is_reported`: pass the live routes plus `("POST", "/api/planted/route")` to the same pure helper that test 10 uses (e.g. `_unclassified(routes) -> list`). It reports exactly the planted route, and the message names `services/not_via_chat.py` and `UI_INTERNAL`.

In `tests/test_oj46_feature_chat_must_refuse_journey.py`, modelled on OJ-06:

16. `test_a_feature_chat_must_refuse_names_the_reason_and_the_page`, parametrised over `tests.test_workflow_provider_parity.PROVIDERS` and `mode` in `expert`/`guided`:
    - `MESSAGE = "Create a user account for my colleague."`;
    - steps: `find_capabilities({"goal": MESSAGE})`, then end. `answer` is built from `results[0]["not_via_chat"][0]`: its reason and page;
    - `calls == ["find_capabilities", None]`;
    - the first entry is the admin entry;
    - no `tool_error`, `error` or `tool_pending_confirmation` frame;
    - no `ui_event` frame (the admin entry has no panel);
    - the final text contains the entry's page;
    - `session.discovered_tools` holds only `TOOLS` names.

    Use the `client` and `helpers_state` fixtures; add a project fixture only if the turn needs one.

Commands (from the repo root; install once with `pixi install --frozen -e default -e test`):

```bash
pixi run gui-tests tests/test_not_via_chat.py tests/test_route_chat_disposition.py tests/test_oj46_feature_chat_must_refuse_journey.py
pixi run gui-tests tests/test_assistant_capabilities.py tests/test_capability_overview.py tests/test_assistant_tool_reachability.py tests/test_openai_tool_selection.py tests/test_harness_user_skills.py
pixi run gui-tests tests/test_tool_error_kind_manifest.py tests/test_harness_layout.py tests/test_write_surface_lock_policy.py tests/test_chat_tools_endpoint_map.py
python pypsa-gui/backend/smoke/assistant_inventory.py --check
pixi run gui-tests   # full backend suite before opening the PR
```

`gui-tests` runs `python -m pytest` in `pypsa-gui/backend`, so test paths are relative to that folder.

**Inventory:** `smoke/assistant_inventory.py` reads only `harness/catalogue.py`, `harness/toolsets.py`, `Results.tsx` and `uiStore.ts`, plus literals from `models/energy_hub.py` and `services/adequacy/mc.py`. This task changes none of them, so `--check` must still print "Inventory and GUI navigation schemas match: 287 tools …" (it does on `master` today). Do **not** run `--write`, and do not commit `.scratch/live-voice/capability-inventory.json`. If `--check` reports stale, you touched a file outside this brief.

**Error kinds:** no new `error_kind`. Entries are data in a successful result, not a refusal. `pypsa-gui/tool-error-kinds.json` stays untouched, and `test_tool_error_kind_manifest.py` must pass unchanged.

## Out of scope / don't touch

- `harness/catalogue.py` (another session edits it): no new tool, no `TOOL_ROUTES` edit, no change to `find_capabilities`' description or schema. Also `harness/toolsets.py`, `harness/providers/*`, `harness/loop.py`, `harness/session.py`.
- Campus files: `routers/campus_electrical.py`, `services/campus_*.py`, their frontend and their tests. Naming campus routes in the new test's tables is fine; editing campus files is not.
- Any frontend file. Any router. `main.py`.
- LC-01 discovery metadata beyond what `find_capabilities` already returns: no new item fields, no overview change, no prompt or workflow text.
- `tests/fixtures/route_inventory_phase0.txt`, `tests/test_chat_tools_endpoint_map.py`, `tests/test_write_surface_lock_policy.py` (import from it; do not edit it).
- `pypsa-gui/tool-error-kinds.json`, `.scratch/live-voice/capability-inventory.json`, `scripts/coord/*`, `docs/agents/*`, `doc/release_notes.md`.

## Repo rules

- Branch `outsource/not-via-chat-list` from current `master`, after outsourced task A (PR #660, merged as `e8cb3f037`). Open exactly one PR. Never force-push. Do not merge it; our coordinator merges.
- PR body starts with the line `Outsourced task E`, then follows `.github/pull_request_template.md`. Mark the body as AI-generated. List any UI-internal entries you added, and the final `SERVED_BY_TOOL` / `KNOWN_GAPS` counts.
- If `master` gains routes before you open the PR, merge `master` and classify them. That is the guard working.
- Add one release-note fragment, `doc/release-notes.d/outsource-not-via-chat-list.md`, containing one bullet in this form:

  ```
  * Feat(pypsa-gui): Asked for something it cannot do from chat (admin, sign-in, API keys, project sharing, map layout, file uploads), the assistant gives the reason and names the page; a backend test now fails when a new route has no chat disposition.
  ```

  Never edit `doc/release_notes.md`. See `doc/release-notes.d/README.md`.
- Tests and docs go in the same PR. Do not skip, loosen or delete any existing test.
- Do not commit credentials or `.env` files. Make no paid model calls; the journey test uses the mocked wire only.
- Follow the README layering rule: no wire/SDK names outside `harness/providers/`.

## Done when

- [ ] The 16 new tests pass, and all of them fail on `master`'s code.
- [ ] `test_assistant_capabilities.py`, `test_capability_overview.py`, `test_assistant_tool_reachability.py`, `test_openai_tool_selection.py`, `test_harness_user_skills.py`, `test_tool_error_kind_manifest.py`, `test_harness_layout.py`, `test_write_surface_lock_policy.py` and `test_chat_tools_endpoint_map.py` pass unchanged.
- [ ] `python pypsa-gui/backend/smoke/assistant_inventory.py --check` passes without `--write`.
- [ ] The full `pixi run gui-tests` is green locally, or the PR lists each failure that also fails on `master`.
- [ ] The diff touches only: `services/not_via_chat.py`, `services/assistant_tools.py`, the three new test files, `harness/README.md` and the release-note fragment.
- [ ] One PR on `outsource/not-via-chat-list`, with a body starting `Outsourced task E`.
