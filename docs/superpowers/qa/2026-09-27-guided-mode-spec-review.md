# Independent review: Guided-mode spec and plan (2026-09-27)

**Reviewed:** `docs/superpowers/specs/2026-09-27-guided-mode.md` (spec), `docs/superpowers/plans/2026-09-27-guided-mode.md` (plan), against `docs/superpowers/qa/2026-09-27-realapp-clickthrough-expert.md` (QA input) and the code at `8e04e54` (working tree clean).
**Reviewer role:** independent; nothing implemented, nothing committed. One temporary probe test was created under `pypsa-gui/backend/tests/` to reproduce bug 3 over HTTP and deleted afterwards (output in the appendix).
**Owner requirements checked:** G1 toggle, G2 EH end to end, G3 assistant does / user confirms via existing cards, G4 default rule; one phase per Opus-or-lower agent; a full system gate after each phase.

## Verdict: **GO-with-conditions**

The spec is unusually concrete and most of its anchors are correct (a verified list is in §3). It cannot be handed to an implementer as-is because several load-bearing claims are wrong in ways that would make the implementer either chase a bug that does not exist (bug 3, study path), put a fix where it is undone (bug 3, restore location), or ship tests that fail for tooling reasons the spec did not anticipate (guide test-id scanner, ChatPanel health mocks, Playwright module resolution, the route-inventory drift, a frontend test that pins `buildUiContext() === null`). One product-owner decision (G4 "new projects") is silently narrowed. The conditions below fix these; none changes the design.

---

## 1. Binding conditions

Each condition names the evidence (file:line, verified on `8e04e54`) and the exact change to the spec or plan. The phase does not start until its conditions are edited into the documents.

### B1. Bug 3: the EH study does not mutate the live network — rewrite §2.1 so the implementer does not hunt for a cause that is not there

**Evidence.**
- Reproduced over HTTP with the e2e helpers (appendix A): after `POST /api/results/eh_study` on the data-center template, `GET /api/network/buses` and `/links` are **equal** before/after; after `POST /api/results/fmea_sweep` they **differ**. The click-through ran the study and then the sweep before looking, so "after an EH study or an FMEA sweep" was never observed for the study alone.
- `services/adequacy/eh_study.py:969-971` copies the network under the lock; `services/adequacy/eh_readiness.py:100` copies (`S._private_copy(network)`) before anything else; `_detach_solver_model` (`redundancy.py:487-496`) only nulls `model.solver_model`. The only `.optimize(` in the backend is `services/solver_service.py:1000`; `pypsa 1.1.2` calls `determine_network_topology()` inside optimize post-processing only when `n.c.sub_networks.static.empty` (checked in the installed package), and `Network.copy` does not call it (checked).
- Existing guard: `tests/test_energy_hub_study_isolation.py:92-113` already asserts the study leaves `links`, `generators_t.p` and `p_nom_opt` alone (not the bus columns).

**Change (spec §2.1, plan P22.9 row 1).** Replace "After an EH study or an FMEA sweep …" with "After an FMEA sweep (the study alone leaves the table equal — verified 2026-09-27 over HTTP)". Delete the sentence starting "for the study path the implementer must find which stage or preflight touches the live object (candidates: …)". Keep `test_eh_study_leaves_the_live_buses_table_equal` as a **guard** and say so. Delete §9 row 1 ("Bug 3's study-path cause is not found quickly") — it is moot.

### B2. Bug 3: the restore point named in §2.1 is wrong, and the column list is incomplete

**Evidence.**
- `services/adequacy/sweep.py:309-360`: the `try/finally: unfreeze()` closes at 359-360; the closing base re-solve `_restore_base_guarded(...)` is at **375, outside** that `finally`. A restore placed "in the `finally` that already calls `unfreeze()` … after `_restore_base_guarded`" is self-contradictory: inside that `finally` it runs **before** the closing solve, which (because `sub_networks` is empty again) re-runs `determine_network_topology` and re-applies Slack/`sub_network`. On a failed base solve (`raise RuntimeError` at 316) the closing solve never runs at all.
- Probe (appendix B): `determine_network_topology()` on the template changes **three** bus columns, `control`, `sub_network` and `generator` (`'' → 'grid_supply'/'genset_1'`), plus adds 3 `SubNetwork` rows. The spec's "`buses[["control","sub_network"]]`" misses `generator`, so the whole-row assertion in step 5 would fail after the fix. `n.remove("SubNetwork", list(n.sub_networks.index))` returns the component to 0 rows; the restored `buses[cols]` equals the saved frame; `n.sub_networks.equals(saved)` is `False` on an empty frame (dtype/index metadata), so the test must compare row counts / `empty`, not `.equals`.
- Live-network solve callers besides `_solve_once`: `frontier.py:145,229,267`, `coupling_loop_runner.py:319,464`, `margin_loop_runner.py:453,772`, `dtc.py:408,608`, `levers.py:326`, `redundancy.py:593`, `eh_study.py:219`. The spec's "the one helper they share" does not exist as a single choke point for the live network.

**Change (spec §2.1 fix contract).**
1. Define `preserve_bus_topology(n)` (context manager in `sweep.py`) that saves `n.buses[["control","sub_network","generator"]]` and the `SubNetwork` index, and on exit writes the columns back and removes any `SubNetwork` rows that were not there before. Restore compares `sorted(n.sub_networks.index)` and `n.buses[cols].equals(saved)`.
2. Wrap the **entire body** of `run_contingency_sweep` from `freeze_capacities` through the `_restore_base_guarded` call (i.e. an outermost `with preserve_bus_topology(network):` that encloses lines 309-381), not the `unfreeze` `finally`. State explicitly that the restore runs after the closing re-solve and on every exception path.
3. Apply the same wrapper at the entry of every study function that receives the **live** network and solves it: `run_frontier_sweep` (`frontier.py`, whole body including `_restore_base`), and the loop runners' live-network paths (`coupling_loop_runner.py`, `margin_loop_runner.py`). The implementer records, in the phase note, for each of the twelve `run_simulation(` call sites above whether it runs on the live network or a private copy; sites on a private copy are left alone.
4. The invariant tests assert whole rows of `/buses` and `/links` (already in the spec) **and** that a foreground `POST /api/simulation/run` afterwards still solves (the restore must not break the next user solve).

### B3. Guide-catalogue test-id scanner sees literal ids only — the hub tour's `reveal` ids as specified would fail `test_guides.py`

**Evidence.** `tests/test_guides.py:22-28` `_fe_test_ids()` uses `(?:data-testid|testId)=["'{`]+([a-z0-9-]+)["'`}]`; a templated `data-testid={`hub-rail-step-${id}`}` stops at `$` and is not captured. §5.1 specifies `"hub-rail-step-{start|site|goal|results|improve}"`, `"hub-start-template-{id}"`, `"hub-improve-{finding.id}"`; §4.3 makes `hub-rail-step-*` the `reveal` of eight tour steps, and `test_every_tour_target_is_a_rendered_test_id` (line 39-46) checks `reveal`s too.

**Change (spec §5.1).** Add the rule: "every id used as a tour `target` or `reveal` is a **literal** `data-testid="…"` string in a `.tsx` file (the catalogue test scans literals only). `StepRail` renders the five step buttons from a literal table `{ start: 'hub-rail-step-start', … }` written out as string literals, not a template." Non-tour ids (`hub-improve-{id}`, `term-{k}`) may stay templated.

### B4. Obstacle 9 (send gate) breaks three existing ChatPanel test files unless their health mocks are updated — list the edits

**Evidence.** `components/ChatPanel.test.tsx:44`, `ChatPanel.actions.test.tsx:52`, `ChatPanel.profile.test.tsx:59` mock `getChatHealth` resolving `chat_ready: false`; `ChatPanel.test.tsx:93` and `:311` then click `chat-send` and assert a real send. Today ChatPanel does not query health (only `ApiKeySetup.tsx:98` and `AssistantModelSettings.tsx:469` touch `['chat','health']`), so the mocks are inert; after §2.8 they disable Send.

**Change (spec §2.8, §8.3).** Add: "Expected test edits: the three `getChatHealth` mocks above change to `chat_ready: true` (they model a working assistant); no assertion in those files depends on `false`. Justification line in the plan per §8.3." Also state that ChatPanel's new health query uses the **same** `['chat','health']` key `ApiKeySetup` already uses, so the invalidation at `AssistantModelSettings.tsx:469` covers profile switches too.

### B5. The browser smoke cannot load Playwright the way §8.4 says, and `shot-authed.mjs` is not a Playwright script

**Evidence.** Verified on this machine: `NODE_PATH=/opt/node22/lib/node_modules node --input-type=module -e "import('playwright')"` → `ERR_MODULE_NOT_FOUND` (Node's ESM resolver ignores `NODE_PATH`); `createRequire`/CJS `require('playwright')` with `NODE_PATH` → ok; `import('/opt/node22/lib/node_modules/playwright/index.mjs')` → ok. `frontend/scripts/shot-authed.mjs:15-45` spawns `google-chrome` and drives it over CDP with `ws`; `which google-chrome chromium chromium-browser` finds nothing. Playwright 1.56.1 and `chromium-1194` exist where the spec says.

**Change (spec §8.4).** Replace the run line and "pattern from `scripts/shot-authed.mjs`" with: the script is `.mjs`, loads Playwright with `const { chromium } = createRequire(import.meta.url)('/opt/node22/lib/node_modules/playwright')` (or `await import('/opt/node22/lib/node_modules/playwright/index.mjs')`), sets `PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers`, and takes only process management (spawn / wait-for-port / cleanup) from `shot-authed.mjs`. Add a first step "smoke self-check: launch, open `about:blank`, close" so a tooling failure is distinguishable from an app failure.

### B6. The P22.9 smoke step "Send disabled without an API key" contradicts the stub profile the same script configures

**Evidence.** `routers/chat.py:158-165`: `chat_ready` is computed from the **active profile**; a bearer profile is ready iff its `key_env` is set; an `auth: none` profile (the stub, `stub_openai_endpoint.py:41-46`) is always ready. §8.4 step 2 configures and activates the stub profile before walking the path; §2.11 then expects Send disabled.

**Change (spec §2.11 and §8.4).** Order the P22.9 path: (a) start the backend with `ANTHROPIC_API_KEY` unset and the default Anthropic profile active; assert `GET /api/chat/health` → `chat_ready:false` and `chat-send` disabled; (b) only then `PUT` and activate the stub profile (`POST /api/chat/settings/llm/active`) and continue. State that §10's "verify in P22.9's gate" is resolved by this ordering.

### B7. The stub model cannot script the P25/P26 tool call today — make the extension a specified deliverable, not a "verify"

**Evidence.** `smoke/stub_openai_endpoint.py:48-51` ("Only P1 (`save_project`) is scripted, deliberately") and `:121-133`: one branch keyed on `"Save the current network"` emits `save_project`; every other prompt gets `"Saved."`. `_last_user_text` (line 68) returns the last user text, which in P25 carries the ui block and the guided addendum appended.

**Change (spec §6 new §6.6 "Stub scripting", plan P25 row 4).** Specify: a second branch that matches the §5.7 delegate text with an unanchored regex `Run the tool (\w+) with exactly these arguments: (\{.*?\})` and emits that tool call with those arguments (id `call_stub_2`); after a `role: tool` message it closes with one plain sentence. A third branch for Site fixes is **not** required in v1 (P26 exercises Improve → do). Add a smoke assertion that the recorded request's last user text contains `Guided mode is on` in Guided and not in Expert (the stub already records payloads). Keep the module's "what it proves" docstring honest: add the new branch to it.

### B8. Regenerating the route inventory pulls in 30 unrelated routes — do it once, before P24

**Evidence.** `PY tools/openapi_diff.py --out <scratch>` on `8e04e54` writes 282 routes; the committed `tests/fixtures/route_inventory_phase0.txt` has 252 (diff 49 lines: `/api/chat/profiles`, seven `/api/chat/settings/llm/*`, `/api/chat/{session_id}/rewind`, nine `/api/gridspine/*`, …). `test_every_http_route_resolves_in_inventory` (`test_chat_tools_endpoint_map.py:134`) is a subset check, so the stale fixture passes today and a regenerated one will too.

**Change (plan Baseline section, spec §4.1).** Move the regeneration to the Baseline step (a housekeeping commit with no code change, the diff pasted into the baseline note) so P24's fixture diff is exactly the one `GET /api/results/eh_review` row. §8.3 bullet 3 then reads "the fixture gains exactly the new route".

### B9. `ui_mode` "always" in `buildUiContext` breaks a pinned test and the "Expert wire unchanged" goal — send it only in Guided

**Evidence.** `utils/uiContext.test.ts:100-105` pins `buildUiContext()` → `null` at cold start; §6.2 adds `ui_mode` unconditionally, so the function would never return `null` and ChatPanel would attach `ui_context` to every Expert request. The backend allow-list (`chat_service.py:2271-2315`) reads named keys only and `routers/chat.py:82` types `ui_context` as `dict[str, Any] | None`, so nothing 422s, but the Expert request body changes for no benefit.

**Change (spec §6.2, §6.5).** `ui_mode` and `guided_step` are added **only when `uiMode === 'guided'`**; Expert requests are byte-identical to today (the "one deliberate Expert wire diff" sentence is deleted). `uiContext.uiMode.test.ts` asserts: expert → no `ui_mode` key and cold-start still `null`; guided → `ui_mode:'guided'`. Backend tests keep the byte-equality assertion for an explicit `ui_mode:'expert'` (defence in depth).

### B10. G4 "new projects start Guided" is narrowed to EH-template projects without saying so — decide and record

**Evidence.** Plan G4: "New users and **new projects** start Guided … an explicit choice is remembered and never overridden." Spec §3.1 `noteTemplateProjectCreated` flips only when `templateId.startsWith('eh_')`, hooked only into the wizard's template mutation (§3.4); blank, from-file and clone projects and chat-created projects never switch. §10 records the chat case but not the narrowing to `eh_` templates. The own-network path exists in Guided (§5.4 `no_study` own network → Start card, "Use my network"), so a blank project in Guided is meaningful.

**Change (spec §3.1/§3.4 and §10; plan G4 row).** Either (a) implement G4 literally — every wizard success handler (`NewProjectWizard.tsx:258-270`, `:362`, `:482`) and `Sidebar.newProjectMut` (`Sidebar.tsx:1721`) calls `noteNewProjectCreated()` which sets `guided` when `!uiModeExplicit` — or (b) keep the EH-only rule and add a §10 row "G4 'new projects' is read as 'new EH-template projects' in v1" with the product owner's initials. The reviewer recommends (a): it is two more call sites and it is what the owner wrote.

### B11. Anchor and naming corrections (all verified)

| Spec | Says | Is |
|---|---|---|
| §2.7 step 2 | `setSelectedComponent({ class: 'Bus', name })` | `SelectedComponent` is `{ type: string; name: string }` (`store/uiStore.ts:9`; `utils/uiContext.ts:56` reads `sel.type`) |
| §2.3 | rows built "in the one place `_get_component("Load", …)` builds rows (find it via `BE/routers/network.py:239`)" | `_get_component` is `services/network_crud.py:64` and delegates to `_serialize_component(n, attr, transient)`, which the B6 path-scoped route also uses. Spec must say: add `p_set_peak` inside `_serialize_component` under `attr == "loads"` so both routes agree, and test both. |
| §6.5 | `build_system_prompt(...)` | `_build_system_prompt` (`services/chat_service.py:2631`) |
| §4.2, §6.3 | "renewable set used by the frontier (`services.adequacy` renewable carriers helper — find and reuse)" | No such helper in `services/adequacy` (`frontier.py` has no carrier classifier). The only classifier is `services/profile_shapes.py:_gen_category` / `_RENEWABLE_KW` (lines 217, 232-236). Spec must name it: "thermal-like" := `_gen_category(carrier) == 'conventional'`, imported from `services.profile_shapes`. |
| §4.2 | "extend `tests/test_energy_hub_readiness*.py`" | No such file. Readiness tests live in `tests/test_energy_hub_tagging.py` (25 references) and `tests/test_energy_hub_p18.py`. Name `test_energy_hub_tagging.py`. |
| §2.1 | restore columns `control`, `sub_network` | plus `generator` (B2) |
| §8.4 | "pattern from `scripts/shot-authed.mjs`" | CDP + `google-chrome`, not Playwright (B5) |

### B12. Phase sizing: split P22.9 and P24

**Evidence.** P22.9 touches 10 items across ≥ 15 files in both stacks plus a Playwright harness written from nothing (plan rows 1-11); P24 is a backend route + readiness + catalogue, two hook lifts in Expert code, a store, a rail and five cards with ≥ 10 new test files (§5.9-5.10). The owner's rule is one phase per Opus-or-lower agent under TDD, with the whole system green at the end.

**Change (plan).** Split: **P22.9-BE** (bug 3, bug 4 backend, bug 2 backend test) and **P22.9-FE** (everything else incl. `smoke-guided.mjs`); **P24-BE** (§4 + FE client types + the two hook lifts + `EhReferenceDesignPanel` exports) and **P24-FE** (§5). Each half runs the full §8 gate (the 40-min suite runs in the background per §9; that is the owner's rule and this review does not weaken it). The intermediate halves are shippable on their own (additive backend fields; lifted hooks with unchanged behaviour).

---

## 2. Findings that are correct (verified, no change needed)

- **Bug 3 sweep mechanism**: `run_contingency_sweep` solves the live network (`sweep.py:271, 309-375`); the closing re-solve leaves dispatch (`dispatch: 'fresh'` after the sweep, `'none'` after the study — appendix A), which is exactly bug 2's contradiction (`ChatLaunchGreeting.tsx:54`, `SnapshotPicker.tsx:146,202`, `TopologyCanvas.tsx:1851`, `routers/simulation.py:984-1006`).
- **First-run race**: `storedTheme()` (`uiStore.ts:148-152`) writes `theme-schema` inside `create()`'s initial state; a module-level `const FIRST_RUN` above `create(...)` runs first. Storage-key inventory (all `network-diagram:*`, `results:active-tab`, `results:dispatch-viewmode`, `compare:*`, `assetDetail:*`, `pypsa-guide-seen:*`, `chat:*` event names) supports §10's claim that the uncounted keys cannot exist without a counted one; `App.tsx:257` and `shared.tsx:1199` write inside effects/handlers.
- **`TOOL_ROUTES`**: `review_eh_study: _DERIVED` at `chat_tools_schema.py:2320`; `_SERVICE_CALL`/`_DERIVED` sentinels at 2165-2166; `test_non_http_sentinels_are_documented` only requires known sentinels; `tools/openapi_diff.py --phase0-fixture` exists (line 103-119) and runs offline (verified).
- **`chat_ready`**: `routers/chat.py:158-176`, from the active profile's `key_env` membership in `os.environ`; FE type `api/chat.ts:212` optional — the fail-open rule in §2.8 is right.
- **`ui_context` allow-list**: `chat_service.py:2271-2315` reads named keys through `_sanitise_ui_value`; `test_chat_ui_context.py:78` pins that unknown keys are dropped; the block is user-turn content, never the system block (tests at 158, 221). Adding `ui_mode`/`guided_step` there keeps prompt caching intact.
- **`review_eh_study` lift**: `chat_tools.py:1606-1633` matches the description; `eh_review.review_report` at `eh_review.py:89`; `_ADEQUACY_NO_DATA_HINTS["eh_reference_design"]` at 1438/1462.
- **Packaging**: `smoke/check_bundle.py:172-176` `ROOTED` already lists `data/guides/eh_fmea_guide.json`; P24 edits that file, adds no data file. `eh_setup.py` is a normal module (PyInstaller follows in-function imports; no `hiddenimports` entry needed, `pypsa-gui.spec:139-180`).
- **`_t` helper, `TOOLS` registry, tier test**: `chat_tools_schema.py:166-183`, `:184`; `test_chat_tools_dispatch.py:611` parametrises over tools.
- **Frontend anchors**: all line references in §2.4-2.10, §3.3-3.6, §5.3, §6.1-6.2 resolve (`EhReferenceDesignPanel.tsx:52,106,194,206,220,421,800,807,1109,1614-1634`; `FmeaTab.tsx:92-116,280`; `ProjectsHomePage.tsx:218`; `projects.ts:114-125`; `NewProjectWizard.tsx:258-270,362,482`; `Sidebar.tsx:1699,1721`; `GuidedTour.tsx:124-132` (`reveal` click) and `:101-103` (`visibleSteps` drops optional steps with no target); `ChatPanel.tsx:120,220-226,795,2147`; `App.tsx:99,119,645`; `Results.tsx:63-76` (thirteen tabs, `multiOnly` filter at 514), `:663`; `AppHeader.tsx:~1000`; `CommandPalette.tsx:466`; `uiContext.ts:45`; `cardKit.tsx:56`; `Sidebar` components `SectionHdr:214`, `IconStripBtn:1600`, `ModeSwitcher:1427`, `ProjectSectionContent:667`, `PreferencesFooter:1522`, `AssistantNavButton:1375`, `sidebar-assistant:1391/1409`; `requestResultsTab` consumed at `Results.tsx:124-147`; `getEhReadiness` signature `api/simulation.ts:1295-1298` matches §5.3's call; `EhReadiness` type at 738 has `import.links`, `critical_buses`, `scr`).
- **Baselines actually measured now** (for the plan's Baseline step, not a substitute for it): `npx tsc --noEmit -p .` exit 0 (19 s); `npx vitest run` → 181 files, 2033 tests, all passed (exit 0). The backend full suite was **not** run by this review (~40 min); the plan's Baseline step remains mandatory and no backend number is claimed here.

## 3. Non-binding suggestions

1. **§2.1 restore and the next foreground solve.** After the wrapper empties `sub_networks`, the next `optimize` recomputes them; that is today's behaviour on a fresh network. Consider a one-line note that a *foreground* solve still writes `control`/`sub_network`/`generator` (Expert-visible today, out of scope), so the reviewer does not read it as a regression.
2. **§3.2 persistence of the implicit default.** Writing `ui-mode=guided` during module evaluation makes `uiStore.ts` the second import-time writer. Fine, but the `firstRunOrder` test should assert the write order of *both* keys relative to `detectFirstRun`.
3. **§4.2 `outage_units.count`** — the data-center builder makes 6 `_outage` calls (`project_templates/eh_templates.py:139-173`: gensets loop + `genset_new` + `grid_import` + `site_transformer`); the spec's "computed from the builder, not a literal" is right — say `len(re.findall(r'_outage\(', builder_source))` is *not* acceptable either; count via `resolve_outage_params` on the built network.
4. **§5.3 Term test importing `BE/data/guides/eh_fmea_guide.json`** from the frontend tree: vitest resolves JSON outside the Vite root, but add `resolveJsonModule` awareness to the test's import (`import guide from '../../../../backend/data/guides/eh_fmea_guide.json'`) and a comment naming `check_bundle.ROOTED` as the packaging pin.
5. **§5.4 `stale`** relies on `review.source` text starting with `"study record"` (`chat_tools.py:1621`). Make `review_latest` return a boolean `stale` alongside `source` so the FE does not parse prose.
6. **§6.1 dedupe by "most recent user message in the last 2000 ms"** needs a timestamp on messages; check `chatStore` messages carry one, else dedupe on the queue only.
7. **§8.2 row 2** lists `tests/test_live_network_untouched.py`, which does not exist until P22.9 lands; for the Baseline run use the row without it.
8. **Plan Baseline**: record the vitest/tsc numbers above as the 2026-09-27 pre-baseline observation, clearly marked as the reviewer's run, not the phase's.
9. **§2.7 step 5** (Link step optional): also give the step a `reveal` of `null` when no Link is selected to avoid `GuidedTour` clicking `props-edit-link` on a Bus card; today `reveal` is only clicked when the target is missing, and `visibleSteps` already drops the optional step, so this is defensive.

## 4. Integration-gate assessment

- Commands in §8.1-8.2 are runnable as written (cwd, `PYTHONPATH`, `-o addopts=""` with `pytest.ini`'s `addopts = -q`; `live_solve` tests run under `-m "not slow"`, as the spec assumes).
- Baseline handling ("only ids in the baseline file are pre-existing") is sound; B8 adds the fixture regeneration to it.
- Browser smoke: feasible only after B5-B7; the fresh-context / first-time-user design is right.
- Row 7 (Expert-unchanged review) is a hand review plus snapshot tests; adequate once B9 removes the only wire diff.
- Honesty: with B1 the only "verified" claim that was not (the study path) is corrected; every gate output is a number from a command, none is defaulted.

---

## Appendix A — bug-3 reproduction over HTTP (reviewer probe, temporary test, deleted)

Data-center template via `_project_from_template`; `budget_solves: 8`; `GET /api/network/buses` and `/links` compared as whole rows.

```
STUDY done 15.8 s
AFTER STUDY buses_equal,links_equal = (True, True)
STATUS after study {'dispatch': 'none', 'condition': None, 'solve_time': None}
SWEEP done 15.9 s
AFTER SWEEP buses_equal,links_equal = (False, False)
  bus diff grid   {'control': ('PQ','Slack'), 'generator': ('', 'grid_supply'),   'sub_network': ('', '0')}
  bus diff dc_mv  {'control': ('PQ','Slack'), 'generator': ('', 'genset_1'),      'sub_network': ('', '1')}
  bus diff it_bus {'control': ('PQ','Slack'), 'generator': ('', '__voll_it_load'), 'sub_network': ('', '2')}
STATUS after sweep {'dispatch': 'fresh', 'condition': None, 'solve_time': None}
```

(`/links` also differed after the sweep; the probe printed bus diffs only. The `links` difference must be characterised by the P22.9 implementer — the spec's invariant already covers it.)

## Appendix B — `determine_network_topology` side effects and restore (reviewer probe)

```
before:     sub_networks rows 0 | control ['PQ','PQ','PQ'] sub_network ['','',''] generator ['','','']
after topo: sub_networks rows 3 | control ['Slack','Slack','Slack'] sub_network ['0','1','2'] generator ['grid_supply','genset_1','']
n.buses.loc[:, cols] = saved; n.remove("SubNetwork", list(n.sub_networks.index)) -> rows 0
restored buses equal: True | sub_networks.equals(saved_empty): False (compare emptiness / index, not .equals)
n.copy() -> sub_networks rows 0
```

## Appendix C — tooling checks

- `NODE_PATH=… node --input-type=module -e "import('playwright')"` → `ERR_MODULE_NOT_FOUND`; CJS `require` with `NODE_PATH` → ok; absolute-path ESM import → ok. No `google-chrome`/`chromium` on `PATH`; `/opt/pw-browsers/chromium-1194` present.
- `tools/openapi_diff.py --out <scratch>`: 282 routes vs 252 in the fixture (49 diff lines).
- `npx tsc --noEmit -p .` → exit 0. `npx vitest run` → 181 files / 2033 tests passed.
