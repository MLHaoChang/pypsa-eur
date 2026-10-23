<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

Status: ready-for-agent after PR #694 (Codex G-a) merges — owner decision Q5 = A; gatekeeper reviews the plan first (a harness change)

# Outsourced task K: The assistant checks whether solved results make sense (LC-20, first rules)

Branch: `outsource/results-plausibility` · one PR against `master` · PR body starts with `Outsourced task K`

**Do not start until PR #694 (G-a, `outsource/profile-tuning-backend`) has merged.** G-a and this task both add to `harness/catalogue.py` and to the capability inventory. Start from `master` after #694. When you finish, merge `master` again and regenerate the inventory with `--write`; never hand-merge it.

**Gated.** Before any code, post your plan to the gatekeeper as a draft PR with the plan in the body (or as a PR comment on it). Wait for `GK OK` on the plan. See "Plan to send first".

## Read first

1. `AGENTS.md` (repo root), and `docs/agents/review-gk.md`.
2. `.scratch/live-copilot/issues/20-results-plausibility-check.md`, the issue (LC-20). Owner decision Q5 = A: this is the first item after LC-05.
3. `.scratch/live-copilot/owner-journeys.md`, lines 136-142 (the `oj-grid-bad` fixture) and 235-262 (OJ-18 and OJ-19, your pass and fail wording). Tier A is defined at lines 18-26.
4. `pypsa-gui/backend/harness/README.md`: "The contract" (l.113-168; rule 2 "Tools are data" is the one that matters) and "Measuring parity" (l.652-663, the inventory).
5. `pypsa-gui/backend/harness/catalogue.py`: the `_t(...)` helper (l.196), a read tool as a model (`get_run_evidence`, l.4199), `_SERVICE_CALL` (l.4251), `TOOL_ROUTES` (l.4298; `get_run_evidence` at l.4300), `safety_tier_for` (l.4797).
6. `pypsa-gui/backend/services/chat_tools.py`: `DISPATCHERS` (l.7678) and `diagnose_network` (l.302), a short pre-solve check that returns one dict. Your handler follows it.
7. `pypsa-gui/backend/smoke/qa_e2e.py`, lines 245-258: check S2.5 fails if any of `diagnose_results`, `solve_overview`, `sanity_check_results`, `generate_run_report`, `submit_plan`, `plan_what_if`, `undo_my_last_chat_action` is registered. **Do not use any of those names.** Do not edit the list.
8. `pypsa-gui/backend/services/assistant_tools.py`, l.109-110: the "checks" family note says "rules for whether results are plausible do not exist yet". It becomes false when you land. Update that note and name the new tool in the family's `tools`.
9. `pypsa-gui/backend/tests/test_tool_error_kind_manifest.py` (l.1-50 for why it exists, `_REFUSE_HELPER_MODULES` at l.48) and `pypsa-gui/tool-error-kinds.json`.
10. `pypsa-gui/backend/tests/oj_grid.py` (`build_oj_grid`, l.41), `tests/journey_wire.py` and `tests/test_oj05_selected_line_edit_journey.py`. Your Tier A test follows them.

## Goal in plain words

Before a solve, real checks exist. After a solve, nothing checks the results; any verdict is the model's own guess. This task adds a native rule set that reads a solved network and a read tool that returns each rule's verdict. The model only narrates what the rules found. It adds no verdicts of its own.

## Scope: the first rules only

The five rules that OJ-19 seeds in `oj-grid-bad`, and no others. They come from the issue's list (`20-results-plausibility-check.md`, Scope item 1):

| Rule id | Reads | Flags |
|---|---|---|
| `lost_load` | lost load per bus (`services/results/lost_load.py`) | lost load above 0, or a price at the VoLL slack |
| `branch_overload` | branch flow against `s_nom` / `s_nom_opt` | utilisation above 100 %, labelled an active-power proxy |
| `curtailment_share` | `services.results.curtailment.curtailment_frame` | curtailment share per carrier above a stated threshold |
| `storage_simultaneous` | StorageUnit charge and discharge by snapshot | both above 0 in the same hour |
| `build_at_bound` | extendable `p_nom_opt` against `p_nom_max` | build ends at the bound |

Each rule has: an id, the facts it read, a threshold with its basis (a constant with a comment, not a magic number), a severity, and a one-sentence explanation template. Each needs its own fixture. Call `curtailment_frame`; do not edit `services/results/curtailment.py` or `carrier_kpis.py` (task H owns them).

Leave the other six rules in the issue's list (negative prices, dispatch bounds, energy balance, CO2 cap, objective band, staleness) and Scope items 3 and 5 (show the tab; sensitivities) for follow-ups. Say so in the PR body and in the issue's `Progress:` line.

## What to change

1. **Rules module.** New `pypsa-gui/backend/services/results/plausibility.py`. One function per rule, each returns a finding or "passed", or "not evaluated" with a reason (no solve, no storage, no lost-load frame). Pure over a network; no HTTP.
2. **One read tool.** A new name, for example `check_result_plausibility` (not one on the S2.5 list). It returns, per rule, `{rule_id, severity, verdict, facts[], explanation}`, plus the rules not evaluated and why. A network with no solved results returns a typed refusal, not "no findings".
3. **Wire it the way the harness README says (rule 2).** Each of these is required, and a parity test fails without it:
   - a `_t(...)` entry in `harness/catalogue.py` with `Safety: read.` in the description, and a `TOOL_ROUTES` entry (`_SERVICE_CALL`);
   - a handler in `services/chat_tools.py` and its `DISPATCHERS` entry;
   - any new `error_kind` the handler raises in `pypsa-gui/tool-error-kinds.json`, with a `surface` and a `why`. If you raise it with `_refuse(...)` in a module that `_REFUSE_HELPER_MODULES` does not list, add the module there;
   - the capability inventory regenerated: `python pypsa-gui/backend/smoke/assistant_inventory.py --write`, then `--check`;
   - the family note in `services/assistant_tools.py` (read-first item 8).
4. **Prompt wording (the interim rung's replacement).** `harness/prompts/results_walk.md`, l.43, says plainly that result-plausibility rules do not exist yet. Change that sentence to point at the new tool. Check `harness/prompts/` and `harness/workflows/` for the same claim. Do not add a verdict anywhere. `tests/test_vs2_results_walk_journey.py` pins the old sentence (l.164, l.174). Update those two lines to the new wording; this is the one existing test you may change, and the PR body says so.

## Plan to send first (the gatekeeper's questions)

State, in the draft PR body:
- the facts each rule reads and where it gets them (which call returns the solved network, and what "solved" means for a network from a loaded run);
- each threshold and its basis (curtailment share, utilisation);
- the tool name, its arguments (none, or a `run_id`?) and its error kinds;
- how `curtailment_frame` behaves for a carrier you have not seen (do not re-implement the carrier rule);
- the list of tests (below).

## Acceptance tests (write first; each fails before the code)

All under `pypsa-gui/backend/tests/`.

1. `test_results_plausibility.py` (new): one fixture per rule that flags it, with the exact `rule_id`, severity and facts; one clean fixture that returns no warning; one fixture per "not evaluated" reason. Thresholds are read from the module's constants, not repeated in the test.
2. `oj_grid_bad.py` (new helper beside `oj_grid.py`; do not edit `oj_grid.py`): `build_oj_grid_bad()` per `owner-journeys.md` l.138-141: lost load in hour 19, `L2` at 160 % utilisation, `Solar` curtailment of 60 %, `Battery` charging and discharging in the same hour, a build at `p_nom_max`. A real HiGHS solve may not reproduce each seed; write the result frames directly if a solve cannot (the Tier A rules say a "small fixture" and real handlers).
3. `test_oj18_oj19_plausibility_journey.py` (new, Tier A): OJ-19 on `oj-grid-bad` returns exactly the five seeded findings, ranked by severity, each with its facts. OJ-18 on `oj-grid` returns no warnings and names the rules that could not run. Parametrise over `tests.test_workflow_provider_parity.PROVIDERS` as `test_oj05_*` does. A scripted answer that adds a claim the rules did not return is withheld (LC-T19 extra-claim injection).
4. Parity: `tests/test_chat_tools_schema_match.py`, `test_chat_tools_endpoint_map.py`, `test_tool_error_kind_manifest.py`, `test_assistant_inventory.py`, `test_assistant_control_matrix.py` pass unchanged.
5. A guard that the tool name is not on the S2.5 removed list (read it from `smoke/qa_e2e.py`).

Frontend: `pypsa-gui/frontend/src/components/ChatPanel.manifest.test.tsx` reads `tool-error-kinds.json`. If you add a kind, make it pass; add `KIND_COPY` only if the manifest says the kind must route.

## Files it may touch

- `pypsa-gui/backend/services/results/plausibility.py` (new).
- `pypsa-gui/backend/harness/catalogue.py`: the new `_t` entry and `TOOL_ROUTES` line only.
- `pypsa-gui/backend/services/chat_tools.py`: the new handler and its `DISPATCHERS` entry only.
- `pypsa-gui/backend/services/assistant_tools.py`: the `checks` family note and tools (l.109-110).
- `pypsa-gui/tool-error-kinds.json`: new kinds only.
- `pypsa-gui/backend/tests/test_tool_error_kind_manifest.py`: `_REFUSE_HELPER_MODULES` only, if needed.
- `.scratch/live-voice/capability-inventory.json`: regenerated, never by hand. Same for `capability-matrix.json` if its check says so.
- The prompt sentence in `harness/prompts/results_walk.md` (l.43), and any other file that says the same.
- `pypsa-gui/backend/tests/test_vs2_results_walk_journey.py`: the two lines that pin the old sentence only.
- The new tests above, `doc/release-notes.d/outsource-results-plausibility.md`, and the `Progress:` line of the LC-20 issue.

## Files it must NOT touch

- `harness/loop.py`, `harness/toolsets.py`, `harness/providers/*`, `harness/navigation.py`.
- `smoke/qa_e2e.py` (including the S2.5 list).
- `services/results/carrier_kpis.py` and `services/results/curtailment.py` (task H). `services/profile_shapes.py` and the profile files (G-a).
- `services/adequacy/grid_readiness.py`, `gridspine/**`, `data/grid_codes.yaml`, campus files.
- Frontend files beyond the manifest test.
- `doc/release_notes.md`.

## Review

The engine gatekeeper reviews. First the plan (no code yet), then the PR with `GK OK @<sha>`. No UX review: there is no UI change.

## Repo rules

- Branch `outsource/results-plausibility` from `master` after #694 merged. One PR. Never force-push. Do not merge it; the coordinator merges.
- PR body starts with `Outsourced task K`, then follows `.github/pull_request_template.md`. Mark it AI-generated.
- Release-note fragment `doc/release-notes.d/outsource-results-plausibility.md`: `* Feat(pypsa-gui): The assistant can check solved results against a set of plausibility rules (lost load, branch overload, curtailment, storage cycling, builds at their bound) and reports which rule fired and which could not run.`
- Do not skip, loosen or delete any existing test.

Commands (repo root; `pixi install --frozen -e default -e test` once):

```bash
pixi run gui-tests tests/test_results_plausibility.py tests/test_oj18_oj19_plausibility_journey.py
pixi run gui-tests tests/test_chat_tools_schema_match.py tests/test_chat_tools_endpoint_map.py tests/test_tool_error_kind_manifest.py tests/test_assistant_inventory.py tests/test_assistant_control_matrix.py tests/test_route_chat_disposition.py tests/test_harness_layout.py tests/test_vs2_results_walk_journey.py
python pypsa-gui/backend/smoke/assistant_inventory.py --write
python pypsa-gui/backend/smoke/assistant_inventory.py --check
pixi run gui-tests   # full backend suite before opening the PR
```

## Done when

- [ ] `GK OK` on the plan is in the draft PR before the first code commit.
- [ ] The five rules each have a flagging fixture, and the clean fixture raises none.
- [ ] OJ-18 and OJ-19 pass in Tier A on `oj-grid` and `oj-grid-bad`, with every finding traced to a fact.
- [ ] The inventory is regenerated with `--write` after merging the latest `master`, and `--check` passes.
- [ ] The S2.5 list is untouched and the new tool is not on it.
- [ ] The full backend suite is green, or the PR lists each failure that also fails on `master`.
- [ ] The PR names the six rules and Scope items 3 and 5 as left for follow-ups.
