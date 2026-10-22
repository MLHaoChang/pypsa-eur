<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Outsourced task A: a tool the assistant just discovered is offered on its next request

Branch: `outsource/discovered-tools-offered` · one PR against `master` · PR body starts with `Outsourced task A`

## Read first

1. `AGENTS.md` (repo root). Release notes and PR rules are there.
2. `pypsa-gui/backend/harness/README.md`, especially "The contract" (layering: nothing outside `harness/providers/` names a wire), "Measuring parity" (the 128-tool OpenAI offer) and the `find_capabilities` paragraph under "Durable assistant tasks".
3. `pypsa-gui/backend/tests/test_openai_tool_selection.py`. Your tests go here and follow its style.

## Goal in plain words

The app's assistant has 287 tools. On the official OpenAI profile the provider accepts at most 128 per request, so `harness/providers/wiring.py::_bounded_tools` chooses 128 from:

- the harness controls;
- the plan's pending tools;
- tool names written in the user's message;
- tools called recently;
- word overlap.

When the model calls `find_capabilities(goal=…)` to look for a tool, the result names up to 8 tools. Those names are not fed back into the selection, and the offer is not refreshed after `find_capabilities` (`harness/loop.py:2288` refreshes only after project rebinding, `use_toolset` or task tools). So the model can discover the right tool and then be refused with `tool_not_offered` when it calls it. Fix that: after a discovery, the tools it returned are offered on the next request, in the same turn and on later turns, until the next discovery replaces them.

## User-facing outcome

- Invisible. The assistant stops failing with "that tool is not available" right after finding the tool.
- **No new buttons, modes, settings, frames or copy.**
- Anthropic, local and other OpenAI-compatible endpoints are unchanged: they already receive the whole eligible catalogue.

## Files to touch

- `pypsa-gui/backend/harness/session.py`: add one dataclass field to `ChatSession`, `discovered_tools: tuple[str, ...] = ()`, next to `task_id`. Add a one-line comment saying it is selection-only and grants nothing.
- `pypsa-gui/backend/services/assistant_tools.py`: in `find_capabilities` (goal mode only, not `overview=True`), after `items` is final (after the 3800-char clip), set `session.discovered_tools = tuple(item["name"] for item in items)` when `session` is not None. It replaces the previous value; do not accumulate.
- `pypsa-gui/backend/harness/providers/wiring.py`: in `_bounded_tools`, read `getattr(session, "discovered_tools", ())` from the `session` it already resolves. Add `int(name in discovered)` to the `rank` tuple directly after `int(name in task_tools)`, so the order is controls, plan tools, discovered, explicit, recent, score, original index. Keep the 128 limit, the catalogue-order output and the "controls always survive" property.
- `pypsa-gui/backend/harness/loop.py`: line ~2288 only. Add `"find_capabilities"` to the literal set `{"use_toolset", "start_task", "resume_task", "resolve_task_step"}`, so the next request in the same turn is re-selected. Change nothing else in `loop.py`.
- `pypsa-gui/backend/tests/test_openai_tool_selection.py`: new tests (below).
- `pypsa-gui/backend/harness/README.md`: append one short paragraph after the "Official OpenAI requests offer at most 128 tools per turn…" paragraph (section "Measuring parity").
- `doc/release-notes.d/outsource-discovered-tools-offered.md`: the release-note fragment.

## Files NOT to touch

- `harness/catalogue.py` and `harness/toolsets.py`. Do not change `find_capabilities`' description or schema. Open PR #623 edits `catalogue.py`.
- `harness/confirm.py`, `harness/restrictions.py`, `harness/access.py` and the dispatch path (`dispatch_tool_call`). Discovery must not change admission.
- Any frontend file, `routers/*`, `scripts/coord/*`, `services/result_views.py`, `services/profile_gen/*`.
- `pypsa-gui/tool-error-kinds.json` (no new error kinds).

## Contract

- `ChatSession.discovered_tools: tuple[str, ...]` has at most 8 names, all of them catalogue names returned by the last goal-mode `find_capabilities` call in this session. The default is `()`.
- `find_capabilities(goal, limit=3, overview=False) -> dict`: signature and result unchanged. Its only new side effect is the assignment above.
- `_bounded_tools(tools, query="", history=None) -> list[dict]`: signature unchanged. Discovered names outrank explicit/recent/score but not controls or plan tools. Output is still ≤128 and in catalogue order. With ≤128 input tools the input is returned unchanged.
- The refresh after a `find_capabilities` batch happens only after that batch has been checked against the offer it received (the existing comment at `loop.py:2285-2287` must stay true).
- Discovery grants no authority:
  - a discovered tool hidden by the current toolset (`filter_tools`) stays hidden;
  - a tool not eligible for the project (`_tools_payload`) stays absent;
  - personal restrictions, confirmation cards and the dispatch allowlist apply unchanged.

## Acceptance tests to add (in `tests/test_openai_tool_selection.py`)

1. `test_discovered_tools_outrank_generic_matches`:
   - pick a tool that `_bounded_tools(TOOLS, "hello")` does not select (compute it in the test; do not hard-code it);
   - set a `ChatSession` with `discovered_tools=(that_name,)` via `chat_tools.set_chat_session`;
   - assert it is now selected, that `len == 128`, that controls ⊆ selected, and that the order is catalogue order.
2. `test_discovery_replaces_not_accumulates`: two goal-mode `find_capabilities` calls leave only the second call's names in `session.discovered_tools`. `overview=True` leaves it unchanged.
3. `test_a_discovered_tool_is_callable_in_the_same_turn` (turn-level, offline, modelled on `test_actual_selected_set_drives_metadata_and_dispatch_allowlist`):
   - official profile;
   - monkeypatch a dispatcher for a tool that is outside the first 128 for the message "Hello";
   - FakeProvider round 1: `find_capabilities` with a goal containing that tool's name;
   - round 2: call that tool;
   - round 3: text;
   - assert no `tool_error` with `tool_not_offered`, that the patched handler ran once, and that `fake.requests[1].tools` contains it.
   - If the tool's tier needs a confirmation card, choose a read-tier tool instead, so the test needs no approval.
4. `test_discovery_never_bypasses_the_toolset`: with `session.toolset` set to a narrowed domain that hides the discovered tool, `_tools_payload_for_profile(official)` still omits it.
5. Existing tests in the file must still pass unchanged.

Commands (from the repo root; install once with `pixi install --frozen -e default -e test`):

```bash
pixi run gui-tests tests/test_openai_tool_selection.py tests/test_assistant_capabilities.py tests/test_capability_overview.py
pixi run gui-tests tests/test_harness_layout.py tests/test_chat_turn_frame_contract.py tests/test_harness_eventlog_truth.py
pixi run gui-tests   # full backend suite before opening the PR
```

`gui-tests` runs `python -m pytest` in `pypsa-gui/backend`, so paths are relative to that folder.

## Repo rules

- Branch `outsource/discovered-tools-offered` from current `master`. Open exactly one PR. Never force-push. Do not merge it; our coordinator merges.
- PR body starts with the line `Outsourced task A`, then follows `.github/pull_request_template.md`. Mark the body as AI-generated.
- Add one release-note fragment, `doc/release-notes.d/outsource-discovered-tools-offered.md`, containing one bullet in this form:

  ```
  * Fix(pypsa-gui): On the OpenAI profile, a tool the assistant finds with find_capabilities is offered on its next request, so it can call it in the same turn.
  ```

  Never edit `doc/release_notes.md`. See `doc/release-notes.d/README.md`.
- Tests and docs go in the same PR. Do not skip, loosen or delete any existing test.
- Do not commit credentials or `.env` files. Make no paid model calls; FakeProvider only.
- Follow the README layering rule: no wire/SDK names outside `harness/providers/`.

## Done when

- [ ] The four new tests pass, and every existing test in `test_openai_tool_selection.py` passes unchanged.
- [ ] `tests/test_harness_layout.py`, `tests/test_chat_turn_frame_contract.py` and `tests/test_harness_eventlog_truth.py` pass.
- [ ] The full `pixi run gui-tests` is green locally, or the PR lists each failure that also fails on `master`.
- [ ] The diff touches only the files listed under "Files to touch".
- [ ] The README paragraph and the release-note fragment are present.
- [ ] One PR on `outsource/discovered-tools-offered`, with a body starting `Outsourced task A`.
