<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

Status: ready-for-agent once PR #812 has merged (it edits `harness/providers/wiring.py`). Written 2026-10-28. Gatekeeper B reviews the plan in the PR body and the PR.

# Outsourced task Q: Core read tools always survive OpenAI's 128-tool cut, and three small pins

Branch: `outsource/tool-selection-pins` · one PR against `master` · PR body starts with `Outsourced task Q`

## Read first

1. `AGENTS.md` (repo root), especially "The chat harness". Then `pypsa-gui/backend/harness/README.md`, which is the harness contract.
2. `docs/agents/review-gk.md`. The gatekeeper applies it.
3. `pypsa-gui/backend/harness/providers/wiring.py`: `_OPENAI_TOOL_LIMIT`, `_SELECTION_INTENTS`, `_GRIDSPINE_ALWAYS` and `_bounded_tools` (about l.250–400 on master `700539f9`).
4. `pypsa-gui/backend/tests/test_openai_tool_selection.py` and `tests/test_loadflow_navigation.py`.

Start from current `master` after #812 has merged.

## Why

The app has about 340 tools, and OpenAI accepts 128 per request. `_bounded_tools` ranks the catalogue by explicit names, then `_SELECTION_INTENTS`, recent calls and description-word overlap. The ranking is fragile. When LC-19 (#802) added the word "open" to a shared rerun note, `get_component` fell out of the selection in `test_loadflow_navigation` journey[openai]. Any description edit to a solve tool can do this again. Three core read tools should never drop out: `get_component`, `list_components` and `validate_network`.

## What to build

1. **Always-offered core reads.** In `wiring.py`, add a small frozenset of core read tools: `get_component`, `list_components`, `validate_network`. `_bounded_tools` always keeps them, the same way `_GRIDSPINE_ALWAYS` and the harness controls are kept, whatever the query. They count toward the 128 limit. Keep the original catalogue order, for prefix caching, as the function already does. Every name must be a read tool in the catalogue: add an assertion or test for that, as `_SELECTION_INTENTS` has.
2. **Selection test.** In `test_openai_tool_selection.py`, the three tools are in the bounded list for:
   - an empty query;
   - a query full of words that match other tools' descriptions (build it from the descriptions of the top-ranked non-core tools);
   - a long history of other tool calls.
   Write the test first, against current master, and show it failing for at least one case (or, if it already passes, say so in the PR body).
3. **`pypsa.Network.remove` signature pin.** `pypsa-gui/backend/services/component_identity.py` (about l.119–136) wraps `pypsa.Network.remove` with `wrapper(self, class_name, name, suffix="")` so that removing a component drops its stored uid. If a PyPSA upgrade changes `remove`'s signature, the wrapper silently breaks. Add a test in `pypsa-gui/backend/tests/` that pins the parameters of the unwrapped `remove` (`inspect.signature`; the wrapper keeps `__wrapped__` via `functools.wraps`) to `(self, class_name, name, suffix="")`, with a message telling the reader to update `_drop_on_remove`. Also test that the wrapper still drops the uid for a scalar name and for a list of names.
4. **`chat_tools.validate_network` return annotation.** `pypsa-gui/backend/services/chat_tools.py` (about l.3543) declares `def validate_network() -> list[dict]:`, but it returns preflight's dict. Read what it returns and fix the annotation only. Change nothing else in `chat_tools.py`: it is shared with several in-flight PRs.
5. **The `results_current` error kind names the load flow precisely.** `pypsa-gui/tool-error-kinds.d/claude-lc19-run-lifecycle.json`, `why`: it says "(the load flow included)". Name the mechanism instead: `run_lifecycle.loadflow_current` refuses `run_ac_pf_stage` when the load flow already matches its inputs. Read `pypsa-gui/backend/harness/run_lifecycle.py` to confirm the names. Keep `surface` unchanged and keep the text one paragraph.

## Done

- One PR from `outsource/tool-selection-pins`, body starting `Outsourced task Q`, with a short plan section at the top for the gatekeeper and the failing-then-passing evidence for item 2.
- Green: `tests/test_openai_tool_selection.py`, `tests/test_loadflow_navigation.py`, `tests/test_json_fragments.py`, the tool-error manifest tests, and `python -m smoke.assistant_inventory --check` and `python -m smoke.assistant_route_parity --check` (both from `pypsa-gui/backend`). The repo's CI is green.
- A release-note fragment under `doc/release-notes.d/` if the release-notes guard asks for one.
- Stay out of every file not named above. Never edit the base `pypsa-gui/tool-error-kinds.json`.
