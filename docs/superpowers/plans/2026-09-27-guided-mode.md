# Guided mode — a simple, assistant-driven path for first-time users (P22.9, P23–P26)

**Status:** planned; spec written 2026-09-27 and revised the same day after an independent review (GO-with-conditions, all twelve conditions applied — see "Review conditions applied"). Nothing implemented yet.
**Spec (contract-level, read before any task):** [`docs/superpowers/specs/2026-09-27-guided-mode.md`](../specs/2026-09-27-guided-mode.md). Section numbers below (§) refer to it.
**Review:** [`docs/superpowers/qa/2026-09-27-guided-mode-spec-review.md`](../qa/2026-09-27-guided-mode-spec-review.md).
**QA input:** [`docs/superpowers/qa/2026-09-27-realapp-clickthrough-expert.md`](../qa/2026-09-27-realapp-clickthrough-expert.md) — the Expert click-through on the data-center template, commit f934560.
**Base commit for baselines:** `2b78c83` on `claude/epic-allen-k2t1c4`.
**Requested:** 2026-09-27. The UI should be easy for a non-consultant. It should hide what an expert does not need up front, guide a first-time user, offer hover help everywhere, and let the assistant do the engineering steps and answer questions.

**Decided (product owner, 2026-09-27; each is the recommended option):**

| # | Question | Decision |
|---|---|---|
| G1 | Shape | A **Guided / Expert toggle** in one app. Guided hides advanced panels and shows a step-card flow; Expert is today's UI. |
| G2 | Scope v1 | **Hub design end to end:** template or own network → site → study → results in plain language → FMEA risks → improve with the assistant. Everything else stays Expert. |
| G3 | Assistant | **It does the steps and you confirm.** "Let the assistant do this" sends a specific request. Every change goes through the normal confirmation card, and questions can be asked at any time. |
| G4 | Default | **New users and new projects start Guided.** One click switches, and an explicit choice is remembered and never overridden. Read literally (review B10, owner's choice): **every** new project — blank, template, from file, clone; from the wizard, `Sidebar.newProjectMut` or `ProjectsHomePage.createBlank` — starts Guided unless the user has chosen a mode explicitly. |

**Rules:** the same as the parent plans: TDD, a QA gate, honesty over completeness, and build on existing engines. Guided mode adds **no new engine**. It is a presentation over the P10–P22 surfaces (readiness, EH study, review, FMEA sweep, guide catalogue, assistant tools). Expert mode stays behaviourally unchanged except for the P22.9 fixes (spec §1 "Expert unchanged").

**Model tiering.** Implementation: Opus-class or lower, one (half-)phase per agent. Plan and spec changes, phase-plan review and the QA-gate verdict: Fable. An implementer who finds the spec wrong stops and reports.

**Order and gating.** Baseline → P22.9-BE → P22.9-FE → P23 → P24-BE → P24-FE → P25 → P26. **After every (half-)phase the integration gate (spec §8) runs end to end across the whole system, and the next one starts only on a GO.** The gate checklist is repeated under each phase; tick it in this file.

---

## Review conditions applied (B1–B12)

| # | Condition | Where applied |
|---|---|---|
| B1 | The EH study does not mutate the live network; rewrite so nobody hunts a non-existent cause | spec §2.1 (Observed / Mechanism rewritten; study test kept as a guard), §9 row removed, §10 row "Bug 3 study path" |
| B2 | Restore point and column list: `preserve_bus_topology` incl. `generator` + `SubNetwork` rows, wrapping the whole sweep body incl. `_restore_base_guarded`; same wrapper for the other live-network studies; twelve call sites recorded; foreground solve still works | spec §2.1 fix contract items 1–4 and the invariant test list |
| B3 | Tour `target`/`reveal` ids must be literal `data-testid` strings | spec §5.1 "Literal test-id rule" |
| B4 | Three existing `getChatHealth` mocks change to `chat_ready: true`; same `['chat','health']` key as `ApiKeySetup` / `AssistantModelSettings` | spec §2.8, §8.3 |
| B5 | Playwright loaded by absolute path (`createRequire` / `index.mjs`), `shot-authed.mjs` is CDP not Playwright, smoke self-check step | spec §8.4 |
| B6 | Send-gate smoke step runs **before** the stub profile is activated | spec §2.11, §8.4 step 2, §10 |
| B7 | Stub scripted tool-call branch is a specified deliverable (`§6.6`), smoke asserts the addendum in the recorded request | spec §6.6, §6.4 files, §8.4 step 5; plan P25 row 4 |
| B8 | Route-inventory catch-up (252 → 282) done once in the Baseline step; P24-BE's fixture diff is exactly one row | spec §4.1, §8.3; plan Baseline |
| B9 | `ui_mode`/`guided_step` sent only in Guided; Expert wire byte-identical; cold-start `null` pin kept | spec §6.2, §6.5 |
| B10 | G4 literal: `noteNewProjectCreated(kind)` from every creation success handler | spec §3.1, §3.4, §3.8, §3.9, §5.3, §10; this plan's G4 row |
| B11 | Anchors: `SelectedComponent.type`; `_serialize_component` in `services/network_crud.py`; `_build_system_prompt`; `_gen_category` from `services/profile_shapes.py`; readiness tests in `test_energy_hub_tagging.py`; `generator` column | spec §2.7, §2.3, §6.5, §4.2, §6.3 |
| B12 | Split P22.9 → BE/FE and P24 → BE/FE, each with a full gate | spec §1 scope table, §2.11; this plan's phase sections |

Non-binding suggestions 1–9: all adopted (spec §10 last row); none rejected.

---

## Baseline (once, before P22.9-BE)

**Done 2026-09-27 on `09045a0`:** the backend has 6033 passed, 0 failed; tsc is clean; vitest has 2033/2033; the inventory went from 252 to 282 routes. See `docs/superpowers/qa/2026-09-27-guided-mode-baseline.md`.

- [x] Full backend suite on `2b78c83` (spec §8.1 command, cwd `pypsa-gui/backend`, ~40 min), output kept in the scratchpad. Gate row 2 at this point runs **without** `tests/test_live_network_untouched.py` (it is created in P22.9-BE).
- [x] `npx vitest run` and `npx tsc --noEmit -p .` on `2b78c83` (cwd `pypsa-gui/frontend`).
- [x] Route-inventory catch-up (review B8): `PY tools/openapi_diff.py --phase0-fixture` from `pypsa-gui/backend`, a housekeeping change with no code change; the 49-line diff is pasted into the baseline note; `test_chat_tools_endpoint_map.py` stays green.
- [x] `docs/superpowers/qa/2026-09-27-guided-mode-baseline.md` lists every failing id with the summary line as evidence, plus the reviewer's 2026-09-27 pre-baseline observation (`tsc` exit 0; vitest 181 files / 2033 passed on `8e04e54`) marked as an observation, not the phase's baseline. Only ids in that file count as pre-existing.

---

## P22.9-BE — Expert-flow fixes, backend half

Source: the click-through report. Spec §2.

| Item | Report ref | Contract | Files | Tests |
|---|---|---|---|---|
| Live network mutated after the **FMEA sweep** (the study alone leaves it equal — verified by the review) | Bug 3 | §2.1 — `preserve_bus_topology(n)` saves/restores `control`, `sub_network`, `generator` and removes new `SubNetwork` rows; wraps the **entire** `run_contingency_sweep` body incl. `_restore_base_guarded`; same wrapper at the entry of `run_frontier_sweep` and the loop runners' live-network paths; the twelve `run_simulation(` sites classified (live vs copy) in the phase note | `BE/services/adequacy/sweep.py`, `frontier.py`, `coupling_loop_runner.py`, `margin_loop_runner.py` | `BE/tests/test_live_network_untouched.py` (4 tests: sweep equal, sweep-abort equal, study equal as a guard, foreground solve still works after a sweep) |
| Contradictory solved state — backend evidence | Bug 2 | §2.2 — one test documenting `dispatch: fresh` with `condition: null` after a sweep (the FE sentence in P22.9-FE describes it) | — | `test_live_network_untouched.py::test_after_a_sweep_status_reports_dispatch_fresh_without_a_foreground_condition` |
| Loaded bus shows 0 MW — backend half | Bug 4 | §2.3 — additive `p_set_peak` inside `_serialize_component` under `attr == "loads"` (both routes agree) | `BE/services/network_crud.py` | `test_network_loads_peak.py` (static, time-series, none; both routes) |

**P22.9-BE integration gate (spec §8): GO on `e0dc3f7`, 2026-09-27.** (The first pass was NO-GO because `Generator.control` was not restored; that is fixed.) Evidence:
- Row 1: full backend `6049 passed, 31 skipped, 11 deselected in 2176.48s`. The baseline was 6033, 16 tests are new, and there are 0 failures.
- Row 2 (the reviewer's run): 560 passed.
- Rows 3–4: tsc exit 0; vitest 2033/2033 (no FE change).
- Row 5: a live uvicorn reproduction over nine tables. After the sweep and after the study, only `*_nom_opt` differs.
- The independent gate file is `docs/superpowers/qa/2026-09-27-guided-mode-gate-P22.9-BE.md`.
- After the gate, the unit test also asserts `generators.control`. Its mutation check (Generator skipped in `_topology_tables`) turns the test red.

- [x] 1 full backend suite — zero new failures vs. baseline
- [x] 2 targeted EH set green (`test_energy_hub_templates_e2e.py`, `test_energy_hub_review.py`, `test_guides.py`, `test_energy_hub_study_isolation.py`, `test_live_network_untouched.py`, `test_chat_tools_endpoint_map.py`, `test_chat_tools_dispatch.py`)
- [x] 3 `npx tsc --noEmit -p .` clean
- [x] 4 `npx vitest run` — zero new failures
- [x] 5 browser smoke: not yet available (the script is a P22.9-FE deliverable); instead the HTTP reproduction of §2.1 is run by hand against a live uvicorn (buses/links equal after study and sweep) and the transcript is attached
- [x] 6 independent QA-gate review: GO (`docs/superpowers/qa/2026-09-27-guided-mode-gate-P22.9-BE.md`)
- [x] 7 Expert-unchanged review signed (additive field + restore only)

**Phase note — the twelve `run_simulation(` sites (spec §2.1 fix 3).** Line numbers are the spec's (pre-change). Several of the listed sites are `_solve_once` calls or the call into `_restore_base`; `_solve_once` is a thin wrapper over `run_simulation`, so they are classified the same way.

| # | Site | What it solves | Live / copy | Action |
|---|---|---|---|---|
| 1 | `frontier.py:145` (`_restore_base` → `run_simulation`) | closing re-solve | **live** from `POST /api/results/frontier` (`frontier_loop_runner.py`, `PyPSAService.get_network()`); the EH study passes a copy with `restore_base=False` and never reaches it | covered by `preserve_bus_topology` around the whole `run_frontier_sweep` body |
| 2 | `frontier.py:229` (`_solve_once` per point) | each ε point | **live** from the route; copy from the EH study | same wrapper |
| 3 | `frontier.py:267` (the `_restore_base` call in the `finally`) | closing re-solve | **live** from the route | same wrapper (the restore runs inside it) |
| 4 | `coupling_loop_runner.py:319` (`solve_at` → `_solve_once`) | each iterate | **live** (`n = PyPSAService.get_network()`) | the worker runs inside `preserve_bus_topology(n)`; closed explicitly after `_restore_closing`, before the record flips |
| 5 | `coupling_loop_runner.py:464` (`_restore_closing`) | closing re-solve | **live** | same wrapper |
| 6 | `margin_loop_runner.py:453` (`solve_at` → `_solve_once`) | each iterate (probe included) | **live** | same pattern as the coupling loop |
| 7 | `margin_loop_runner.py:772` (`_restore_closing`) | closing re-solve | **live** | same wrapper |
| 8 | `dtc.py:408` | islanding contingency | copy (`nn = network.copy()`) | left alone |
| 9 | `dtc.py:608` | planning contingency | copy (`nn = network.copy()`) | left alone |
| 10 | `levers.py:326` | lever scenario | copy (`nn = network.copy()`) | left alone |
| 11 | `redundancy.py:593` | redundancy scenario | copy (`nn = network.copy()` at 554) | left alone |
| 12 | `eh_study.py:219` (`ens_solve`) | ENS solve | copy (the study copies under the lock, `eh_study.py:969–971`) | left alone |

Outside the twelve: `sweep.py` `_solve_once` / `_restore_base_guarded` (the FMEA sweep; **live** from `POST /api/results/fmea_sweep` for class B and class C, copy from the EH study's `fmea_top`) are the bug-3 sites and are wrapped in `run_contingency_sweep` itself; `routers/simulation.py` (the foreground solve) and `solve_queue.py` (a queued user solve) are user solves and out of scope (spec §2.1 fix 4).

**Topology columns restored (coordinator decisions after deviation 2 and gate NO-GO B1).** PyPSA 1.1.2's topology pass (`determine_network_topology` → `find_bus_controls` → `find_slack_bus`) writes, checked against a real optimize: Bus `control` / `sub_network` / `generator`, Generator `control` (the sub-network's slack generator → Slack, extra ones → PV), and `sub_network` on every passive branch (Line, Transformer). `preserve_bus_topology` now saves and restores every one of `control`, `sub_network`, `generator` on **every** component whose static table carries it, found through `n.components` (Bus, Generator, StorageUnit, Line, Transformer on 1.1.2), plus the `SubNetwork` rows; a test double without a component registry is still a no-op. Tests: `test_contingency_sweep_restores_branch_sub_network` (Line + Transformer; red before: `('Line', [''], ['0'])`); the FMEA sweep and abort tests now also compare `/api/network/generators` (red before: `generators/genset_1 changed: control PQ→Slack`). Mutation checks: sweep wrapper → null context turns the sweep, abort and branch tests red; skipping Generator in the lookup turns the sweep and abort tests red on `genset_1.control`; the margin-loop wrapper removed turns `test_margin_loop_leaves_the_live_tables_equal` red. Not restored, recorded: `x_pu`/`r_pu`/`*_pu_eff` on branches (dependent values PyPSA recomputes before every solve, not user inputs) and a blank `carrier` filled from the bus's carrier (PyPSA only fills blanks, never overwrites an entered value; the foreground solve does the same; not seen on the EH templates) — `carrier` is an optional input, so this is a known limitation, not a derived value. Known limitation too: an edit made to a topology column during a live-network study is reverted when the study ends (the restore runs outside the network lock; same class as `freeze_capacities`' undo; not a regression).

**`p_set_peak` sign (gate note 6).** For a time-series load the peak is the value with the largest magnitude, sign kept (a negative, generation-like load reports its most negative value; plain `max` under-reported it). Docstring in `_add_load_peak`; test `test_network_loads_peak.py::test_negative_load_peak_is_the_largest_magnitude`.

**`*_nom_opt` difference, characterised (accepted deviation).** After the sweep and the fix, `/buses` rows are equal. `/links` and `/generators` differ only in `p_nom_opt` (0 → nameplate: links `grid_import`, `site_transformer`; generators `grid_supply`, `genset_1..4`, `rooftop_pv`), and the same holds for every `*_nom_opt` output (`s_nom_opt`, `e_nom_opt`) on the other tables. They are written by the sweep's closing base re-solve, which solves the user's own config on purpose — the same solve that leaves `dispatch: fresh` (bug 2). They are optimisation outputs, not user inputs, and restoring them would leave `fresh` dispatch next to zero capacities. The invariant tests therefore compare whole rows of `/buses`, `/links` and `/generators` with every `*_nom_opt` key excluded on the sweep and loop paths only, and pin it instead: on every fixed link and generator `p_nom_opt == p_nom`. The study guard compares whole rows with no exclusion. **Accepted by the coordinator.**

## P22.9-FE — Expert-flow fixes, frontend half

| Item | Report ref | Contract | Files | Tests |
|---|---|---|---|---|
| Contradictory solved state | Bug 2 | §2.2 — greeting says "Solved" only with `condition` + `solve_time`; otherwise the study-re-solve sentence | `FE/components/ChatLaunchGreeting.tsx` | `ChatLaunchGreeting.solvedState.test.tsx` |
| Loaded bus shows 0 MW — frontend half | Bug 4 | §2.3 — "Peak load" row from `p_set_peak ?? p_set` | `FE/layout/PropertiesPanel.tsx`, `FE/api/types.ts` | `PropertiesPanel.peakLoad.test.tsx` |
| Raw numbers | Bug 1 | §2.4 — `fmtEnergy`/`fmtCurrency` in the DtC tables and FMEA € columns | `EhReferenceDesignPanel.tsx`, `FmeaTab.tsx` | `…formatting.test.tsx` ×2 |
| `/api/projects/unclaimed` console noise | Bug 7 | §2.5 — `enabled: authEnabled` | `FE/pages/ProjectsHomePage.tsx` | `ProjectsHomePage.unclaimed.test.tsx` |
| Template does not open the workbench | Obstacle 2 | §2.6 — `addTab` + navigate to `/app?project=` from `/projects` (template, file and clone tabs) | `FE/layout/NewProjectWizard.tsx` | `NewProjectWizard.templates.test.tsx` (+2) |
| Tagging tour dead-ends | Obstacle 3 | §2.7 — `GuideButton.prepare`, select a bus (`{ type: 'Bus', name }`), open Properties, `propertiesEditRequest` → Edit; `reveal` ids `props-edit-bus/link`; Guide button on the Bus card; Link step optional with the absent-`reveal` guard | `GuidedTour.tsx`, `EhReferenceDesignPanel.tsx`, `prepareTaggingTour.ts`, `uiStore.ts`, `PropertiesPanel.tsx`, `cardKit.tsx`, catalogue | `GuidedTour.prepare.test.tsx`, `EhReferenceDesignPanel.taggingTour.test.tsx`, `PropertiesPanel.editRequest.test.tsx`, `test_guides.py` |
| Send enabled without an API key | Obstacle 9 | §2.8 — disable on `chat_ready === false`, fail-open on unknown, inline `ApiKeySetup`, key `['chat','health']`. **Justified test edits:** `ChatPanel.test.tsx:44`, `ChatPanel.actions.test.tsx:52`, `ChatPanel.profile.test.tsx:59` mocks → `chat_ready: true` (they model a working assistant; no assertion depends on `false`) | `FE/components/ChatPanel.tsx`, `ApiKeySetup.tsx` | `ChatPanel.sendGate.test.tsx` |
| Failed verdict has no next step; "ok" chips red | Obstacle 5 (part) | §2.9 — `certificationHeadline.next` per verdict, `eh-certification-next`; `statusTone('ok')` → success token | `EhReferenceDesignPanel.tsx`, `index.css`, catalogue `enter` line | `EhReferenceDesignPanel.verdictNext.test.tsx` |
| Quick win: name + finished cue | Obstacles 1, 4 | §2.10 — header "Energy Hub reference design"; `eh-study-finished-cue` scrolls to `eh-report`; banner stays while running | `EhReferenceDesignPanel.tsx` | `…finishedCue.test.tsx`, `…banner.test.tsx` |
| Smoke script | — | §8.4 — `scripts/smoke-guided.mjs`: absolute-path Playwright load, self-check, send-gate check **before** stub-profile activation, `--phase P22.9` path (§2.11) | `FE/scripts/smoke-guided.mjs` | runs green |

**Further justified test edit (§8.3):** `EhReferenceDesignPanel.test.tsx` "tones ok / skipped / not_established differently" asserted `statusTone('ok')` contains `accent`; §2.9 moves "ok" to the success token (`text-success`), so that one assertion now reads `success` (the skipped/not_established assertions are unchanged).

**Not in P22.9 (deferred, recorded):** bug 5 (FMEA severity-0 rows without explanation), bug 6 (one-off 409 on resume), obstacles 10–12 (popover placement, `gen_zero_costs` warning, misleading save path). Obstacles 1, 4, 6, 7, 8 are answered by the Guided flow (mapping in spec §1) and, for 1/4, partly by the quick win above.

**Known limitation (Send gate, recorded at the P22.9-FE re-gate):**
- **The case.** A session was bound server-side to profile X on an earlier turn. The store's `profileId` is null again, for example after a reload. Send is then gated on the active profile's readiness, while the backend keeps running turns on X. If X is ready and the active profile is not, Send is wrongly disabled.
- **Why it is acceptable.**
  - The dropdown shows `profileId ?? active`, so what the user sees matches the gate.
  - Picking X lifts it in one click.
  - It needs an admin to switch the active profile away from a working one mid-session.
- **Follow-up.** Expose the session's bound profile id to the client and compare against that.

**P22.9-FE integration gate (spec §8): GO on `28774ad`, 2026-09-27.** The first pass was NO-GO: the Send gate ignored the session's picked profile. That is fixed. Evidence:
- Row 1: full backend suite `6049 passed, 31 skipped, 11 deselected in 2203.30s`, 0 failures. The run started at `9bd0590`. The only backend change since is one catalogue text line, and `test_guides.py` passes on it (6 passed).
- Row 2: 400 passed.
- Row 3: tsc exit 0.
- Row 4: vitest 195 files / 2094 passed. The baseline was 2033, so 61 tests are new. Four existing test edits are justified above.
- Row 5: smoke `--phase P22.9` PASS, 10 screenshots.
- Rows 6–7: the independent gate file is `docs/superpowers/qa/2026-09-27-guided-mode-gate-P22.9-FE.md`, with 10 mutation checks plus the re-gate mutations.

- [x] 1 full backend suite — zero new failures
- [x] 2 targeted EH set green
- [x] 3 `tsc` clean
- [x] 4 `vitest` — zero new failures (the three justified mock edits recorded above)
- [x] 5 browser smoke `--phase P22.9` (spec §2.11 order: send gate with no key → stub profile → template opens the workbench → study → cue → verdict next → buses/links equal → sweep → equal → tagging tour lands on `eh-bus-fields` in Edit), screenshots kept
- [x] 6 QA-gate review GO (`…-gate-P22.9-FE.md`)
- [x] 7 Expert-unchanged review signed (only the fixes above differ)

---

## P23 — Guided / Expert mode

Spec §3.

- **State.** `uiStore.uiMode: 'guided' | 'expert'` under `network-diagram:ui-mode`, `uiModeExplicit` under `network-diagram:ui-mode-explicit`, density pattern (§3.1).
- **Default rule** (§3.2 truth table). A stored explicit choice always wins; otherwise a first-time user (no `network-diagram:*` key other than `theme-schema`, no `pypsa-guide-seen:*`, no `results:active-tab`) starts `guided` and that is persisted once; an existing user starts `expert`. `FIRST_RUN` is a module-level constant evaluated **before** `create()` because `storedTheme()` writes `theme-schema` during store creation; the order test covers both writes.
- **New-project rule (G4 literal, §3.4).** `noteNewProjectCreated(kind)` from the wizard's three tabs, `Sidebar.newProjectMut` and `ProjectsHomePage.createBlank`: sets `guided` unless the choice was explicit. Opening a project never changes the mode; chat-created projects are a v1 non-goal.
- **Switch.** Segmented control in `AppHeader` (`ui-mode-switch`), palette entry `act-ui-mode` (§3.3).
- **Hiding in Guided** (§3.5 table): sidebar keeps Assistant, **Hub design**, and Save / Recent / Projects home; DATA and SIMULATION sections, the `ModeSwitcher` and (through SIMULATION) the Solve Queue row are hidden; Results shows `adequacy` and `fmea` (`expertOnly` on the rest, filtered like `multiOnly`). Hidden panels stay reachable through the palette and `ui_open_panel`. Switching to Guided prunes a hidden slide panel to `hubDesign`/null and coerces a hidden results tab to `adequacy` without writing storage (§3.7).
- **`hubDesign` panel slot** (§3.6): `SlidePanel` member, `PANEL_META`, `FULL_SCREEN_TABS`, placeholder component, `_normalizePanelId` alias, `SAFETY_PANEL_ENUM`; auto-opens once per project in Guided.

| Task | Files | Tests |
|---|---|---|
| Store fields, keys, `FIRST_RUN`, actions, switch-time pruning | `FE/store/uiStore.ts` | `uiStore.uiMode.test.ts`, `uiStore.firstRunOrder.test.ts` |
| New-project rule at five call sites | `NewProjectWizard.tsx` (3 tabs), `Sidebar.tsx`, `ProjectsHomePage.tsx` | `Sidebar.newProject.test.tsx`, `ProjectsHomePage.createBlank.test.tsx`, wizard tests |
| Header switch + palette entry | `AppHeader.tsx`, `CommandPalette.tsx` | `AppHeader.uiMode.test.tsx`, `CommandPalette.uiMode.test.tsx` |
| Sidebar test ids, Guided render conditions, Hub design row | `layout/Sidebar.tsx` | `Sidebar.uiMode.test.tsx`, `Sidebar.expertUnchanged.test.tsx` (snapshot from base) |
| Results `expertOnly`, `effectiveTab`, tab ids | `pages/Results.tsx` | `Results.uiMode.test.tsx` |
| Panel slot, auto-open | `App.tsx`, `pages/hubDesign/HubDesignPanel.tsx` (placeholder), `ChatPanel.tsx`, `BE/services/chat_tools_schema.py` | `App.hubDesignAutoOpen.test.tsx`, `BE/tests/test_chat_tools_schema_panels.py` |

**P23 integration gate (spec §8):**
- [x] 1 full backend suite — zero new failures
- [x] 2 targeted EH set green
- [x] 3 `tsc` clean
- [x] 4 `vitest` — zero new failures
- [x] 5 browser smoke `--phase P23`: fresh context → Guided on; sidebar shows Assistant / Hub design / Project basics only; Results shows two tabs; switch to Expert → everything back; reload keeps Expert; existing-user context (seeded `network-diagram:current-project`) → Expert; creating a blank project and a template project with implicit Expert → Guided; with explicit Expert → stays Expert
- [x] 6 QA-gate review GO (`…-gate-P23.md`)
- [x] 7 Expert-unchanged review signed

---

**P23 result: GO, 2026-09-27.** The first gate was NO-GO with three blockers (B1–B3); they were fixed following the spec's "§10 addendum: P23 gate decisions". The re-gate was GO, and its notes N-R1 and N-R2 were closed afterwards.

| Check | Result |
|---|---|
| Row 1, full backend suite | `6054 passed, 31 skipped, 11 deselected`, 0 failures; the backend is unchanged since |
| Row 2 | 405 passed |
| Row 3, tsc | exit 0 |
| Row 4, vitest (orchestrator's run after N-R1/N-R2) | 212 files / 2222 passed |
| Row 5, smokes | P23 (17 steps) and P22.9 both PASS |
| Row 7, Expert snapshots | byte-identical to the base render |

Gate file: `docs/superpowers/qa/2026-09-27-guided-mode-gate-P23.md`.

## P24-BE — Review route, readiness additions, catalogue, hook lifts

Spec §4 plus the Expert-side lifts from §5.9.

| Task | Files | Tests |
|---|---|---|
| `review_latest` lift (+ boolean `stale`); `GET /api/results/eh_review` (200 / 204); endpoint map → the route; fixture gains exactly one row | `BE/services/adequacy/eh_review.py`, `chat_tools.py`, `routers/results.py`, `chat_tools_schema.py`, `tests/fixtures/route_inventory_phase0.txt` | `BE/tests/test_eh_review_route.py` (route == tool output; 204; running; `stale`; `TOOL_ROUTES` pin) |
| Readiness additive keys `pack_defaults`, `outage_units` (thermal-like via `_gen_category`), `import_p_nom_mw` | `BE/services/adequacy/eh_readiness.py` | `test_energy_hub_tagging.py` (+3; count via `resolve_outage_params`; existing keys pinned) |
| Catalogue: the 20 `fields` keys only (the `hub_design` tour lands in **P24-FE** with the components that render its literal target ids, so `test_every_tour_target_is_a_rendered_test_id` never goes red between halves) | `BE/data/guides/eh_fmea_guide.json` | `test_guides.py` (+`test_hub_fields_present`, `test_field_text_is_plain`) |
| FE client types | `FE/api/simulation.ts` (`getEhReview`, `EhReview`, `EhReadiness` fields) | type-checked |
| Lifted hooks (behaviour unchanged) | `FE/hooks/useCreateFromTemplate.ts` (keeps `noteNewProjectCreated('template')` + navigation), `FE/hooks/useStartFmeaSweep.ts`; `NewProjectWizard.tsx`, `FmeaTab.tsx` consume | hook tests; existing wizard / FMEA tests stay green |
| Expert panel exports + `eh_review` invalidation | `EhReferenceDesignPanel.tsx` | existing panel tests |

**P24-BE integration gate (spec §8): GO on `fcf88d8`, 2026-09-27.** Evidence:

| Row | Result |
|---|---|
| 1 | full backend `6083 passed, 31 skipped, 11 deselected`, 0 failures |
| 2 | 536 passed |
| 3–4 | tsc exit 0; vitest 215 files / 2234 passed |
| 5 | smokes P24-BE / P23 / P22.9 PASS; curl 204 → running → ok with `stale:false` |

Mutation checks: 16 of 17 killed. The survivor is the addTab/navigate order: the code is unchanged, but the test does not pin that order. It is carried into P24-FE as N1.

The gate file is `docs/superpowers/qa/2026-09-27-guided-mode-gate-P24-BE.md`. Notes N1–N8 are carried into P24-FE.

- [x] 1 full backend suite — zero new failures
- [x] 2 targeted EH set green (incl. `test_chat_tools_endpoint_map` with the one-row fixture diff)
- [x] 3 `tsc` clean
- [x] 4 `vitest` — zero new failures
- [x] 5 browser smoke `--phase P22.9` re-run (Expert path unchanged after the lifts) plus `curl` of `GET /api/results/eh_review` before / during / after a study (204 / running / ok with `stale:false`)
- [x] 6 QA-gate review GO (`…-gate-P24-BE.md`)
- [x] 7 Expert-unchanged review signed (lifted hooks byte-equivalent in behaviour)

## P24-FE — The hub-design step cards

Spec §5. A full-width panel `hubDesign`, opened by default in Guided mode when a project is open. Five steps sit in a progress rail, each a **card** with at most three decisions, plain-language text, a "?" hover on every term (guide catalogue), **Ask about this** (prefills a question) and **Let the assistant do this** (sends a request, P25).

1. **Start.** Pick a template (the three EH templates, with one-line purposes) or "use my network". It shows the template's provenance ("synthetic example data").
2. **Site.** Readiness in words: grid connection (`import.links`, `import_p_nom_mw`), critical load, grid-strength data present/missing, outage data (`outage_units.count`). Each gap has a fix button that calls the assistant. The one editable choice is the site type (archetype) in plain words.
3. **Goal.** Allowed shortfall per year (LOLE, default from the template / `pack_defaults`), "how strict on energy" (ENS, advanced), VOLL read-only with an assistant fix. Budget and stages hidden (template / pack defaults). Then **Run study** — the same `buildEhStudyBody` as the Expert panel.
4. **Results.** Plain-language cards from `GET /api/results/eh_review`: headline verdict (§5.5), cost, top risks, "what this study could not establish". A link opens the full Expert report.
5. **Improve.** High/medium findings as cards with **Why**, **Let the assistant do this** (the finding's action) or **Ask**. **Check risks (FMEA)** runs the B/C sweep with the stress registry (lifted `useStartFmeaSweep`), "Add a stress scenario" via the assistant.

State machine `no_project / no_study / running / done / stale` (§5.4, `stale` from the boolean), auto-advance on `running → done` (§5.6), delegation texts (§5.7), text sourcing rules (§5.8), literal test ids for tour anchors (§5.1).

| Task | Files | Tests |
|---|---|---|
| Panel, rail (literal `RAIL_IDS`), five cards, `Term`, `CardShell`, store, `delegate.ts` (= `ask` until P25) | `FE/pages/hubDesign/**` | `HubDesignPanel.flow`, `StartCard`, `SiteCard`, `GoalCard`, `ResultsCard`, `ImproveCard`, `Term` (imports the catalogue JSON) tests (§5.10) |
| `hub_design` tour in the catalogue (targets/reveals now rendered) | `BE/data/guides/eh_fmea_guide.json` | `test_guides.py` target pin |

Changed assertions (spec §8.3):
- `test_guides.py::_HUB_TOUR`: `hub-improve-fmea` is now optional. Justification: P24-FE gate B2. Improve is blocked until a study has finished, so a non-optional step dead-ends the tour before a first study (spec §10, P24-FE gate decisions).
- `App.hubDesignAutoOpen.test.tsx`: asserts `hub-rail`, not the P23 placeholder text. Justification: P24 replaces the placeholder (spec §3.6).

**P24-FE integration gate (spec §8):**
- [x] 1 full backend suite — zero new failures
- [x] 2 targeted EH set green
- [x] 3 `tsc` clean
- [x] 4 `vitest` — zero new failures
- [x] 5 browser smoke `--phase P24` on the data-center template: Start (template banner) → Site rows populated → Goal default 3 h/yr → Run → running → done → rail at Results, headline "Not certified…" → Improve lists findings → Check risks → FMEA tab; Open full report scrolls `eh-report` into view; buses/links equal before/after; the `hub_design` tour walks all steps
- [x] 6 QA-gate review GO (`…-gate-P24-FE.md`)
- [x] 7 Expert-unchanged review signed

---

**P24-FE result: GO at Re-gate 2 on `81f7bad`, 2026-09-27.** The gate took three rounds.
- **First gate: NO-GO**, three blockers:
  - B1: the cost label claimed the plan met the goal.
  - B2: the tour dead-ended before a first study.
  - B3: engine jargon on the Improve card.
- **Re-gate: NO-GO.** New blocker B4: a failed first read left the hub panel loading forever while it re-requested about twice a second. This was also the real cause of the intermittent vitest hang.
- **Re-gate 2: GO.**

| Row | Evidence |
|---|---|
| 1 | Full backend `6111 passed` at `37a1693`, plus one flaky failure (`test_chat_sse::test_invalid_decision…`). Its root cause was a TTL race, reproduced with a 0.5 s stall and fixed in `40eda09`. `e3ded59` fixed another flaky test (`id()` reuse on freed connections). The backend is otherwise unchanged. |
| 2 | 516 passed |
| 3 | tsc clean |
| 4 | vitest 229 files / 2365 passed |
| Stress | App plus hub tests ×30, 0 failures (9/25 before the fix) |
| 5 | All four smokes pass (P24 has 21 screenshots, including the B4 step). A B4 probe covered 500/404/403 on both reads: no loop, no toasts, Retry recovers, and Expert toasts are unaffected. |

Mutation checks killed 24 of 27. The three survivors are all in the API-layer quiet mapping; those tests are carried into P25 step 0.

The gate file is `docs/superpowers/qa/2026-09-27-guided-mode-gate-P24-FE.md`.

## P25 — The assistant does the steps

Spec §6.

- **Send from a card.** `chatStore.sendRequest(text, {source})` queues; ChatPanel's effect dispatches through `dispatchSend` (the typed-message path) when not streaming and no confirmation card is pending; dedupe on the queue's `lastRequest` within 2 s; attachments never included; write confirmation cards untouched (§6.1).
- **Guided prompt awareness.** `ui_context.ui_mode` and `guided_step` from `buildUiContext` **only in Guided** (Expert wire byte-identical, cold-start `null` pin kept); backend allow-lists them and appends `_GUIDED_MODE_ADDENDUM` to the per-turn user content — the system prompt is untouched (§6.2).
- **`suggest_eh_setup` (read).** `services/adequacy/eh_setup.py`; heuristics for import Link, PoC bus, critical buses, missing outage data (thermal-like via `_gen_category`); ready `update_component` / `bulk_update_components` actions that validate against their schemas; never applies anything (§6.3). One sentence added to `_EH_GUIDE_CHAINING`.
- **Stub scripting (§6.6).** Branch 2 of `smoke/stub_openai_endpoint.py` parses the §5.7 delegate text into the named tool call so the smoke sees a confirmation card.

| Task | Files | Tests |
|---|---|---|
| Queue + `sendRequest` | `FE/store/chatStore.ts`, `FE/components/ChatPanel.tsx`, `FE/pages/hubDesign/delegate.ts` | `chatStore.sendRequest.test.ts`, `ChatPanel.sendRequest.test.tsx`, `delegate.test.ts` |
| `ui_mode` / `guided_step` (Guided only) | `FE/utils/uiContext.ts`, `BE/services/chat_service.py` | `uiContext.uiMode.test.ts` (expert → no key, cold start `null`), `BE/tests/test_guided_mode_prompt.py` (`_build_system_prompt` pinned; Expert block byte-equal) |
| `suggest_eh_setup` | `BE/services/adequacy/eh_setup.py`, `chat_tools.py`, `chat_tools_schema.py`, `BE/tests/_tool_actions.py` (shared `_validate_action`) | `BE/tests/test_eh_setup_suggest.py` (three templates with tags stripped recover the builder's tags; actions validate; network untouched; bulk collapse) |
| Stub scripted tool call (§6.6) | `BE/smoke/stub_openai_endpoint.py` | `BE/tests/test_stub_openai_endpoint.py`; smoke |

**P25 integration gate (spec §8): GO at Re-gate 2 on `e19c04e`, 2026-09-28.**

The gate took three rounds:
- **First gate: NO-GO.**
  - **B1:** write-tier tools applied with no confirmation card, although every Guided sentence promised one. Decision: in Guided, the write tier goes through the card. Expert is unchanged.
  - **B2:** the request bubble overflowed.
- **Re-gate 1: NO-GO.**
  - **R1:** the next card was wiped by the previous card's `/confirm`, a race that became reachable only once several writes could go through cards. Fixed with a token guard.
- **Re-gate 2: GO.**

Evidence at Re-gate 2:

| Check | Result |
|---|---|
| Row 1, full backend suite | `6235 passed, 31 skipped, 11 deselected`, 0 failures |
| Row 2 | 613 passed |
| Chat tests | 1155 passed |
| tsc | clean |
| vitest | 2431 passed |
| Stress | ×10, 0 failures |
| Smokes | all five pass |
| R1 browser probe | 12/12 rounds |
| Mutations | 8/9 killed; the survivor is the expiry guard, harmless defence judged not to need a test |
| Expert wire and prompt | byte-identical to `0689df3` except for the one reworded sentence and the added tool |

Gate file: `docs/superpowers/qa/2026-09-27-guided-mode-gate-P25.md`. Items carried to P26: confirmation friction and wording for non-editing write-tier tools (export, snapshot, load project).

- [x] 1 full backend suite — zero new failures
- [x] 2 targeted EH set green (+ `test_eh_setup_suggest.py`, `test_guided_mode_prompt.py`, `test_stub_openai_endpoint.py`)
- [x] 3 `tsc` clean
- [x] 4 `vitest` — zero new failures
- [x] 5 browser smoke `--phase P25`: Improve → "Let the assistant do this" → request appears as a user message, the stub's recorded last user text contains `Guided mode is on`, a **confirmation card** renders, decline; a second click while streaming is queued and sent after; in Expert the recorded text has no addendum and the request body has no `ui_mode`
- [x] 6 QA-gate review GO (`…-gate-P25.md`)
- [x] 7 Expert-unchanged review signed (only the `_EH_GUIDE_CHAINING` sentence differs)

**P25 edited assertions (spec §8.3, one line each):**
- `delegate.test.ts` — "delegate behaves like ask until P25" becomes "delegate calls `sendRequest` with `source:'hub-design'`"; the Site-fix grid text gains the `suggest_eh_setup` clause (spec §5.7: both are the P25 change).
- `ImproveCard.test.tsx`, `SiteCard.test.tsx`, `GoalCard.test.tsx` — the Do / fix / VOLL / stress buttons now assert the queued request instead of the composer seed (§5.7 `delegate` → `sendRequest`); Ask still asserts the seed.
- `test_energy_hub_review.py` — `_validate_action` moved to `tests/_tool_actions.py` (§6.3, shared) and gained an enum check; `test_prompt_carries_the_eh_workflow_both_modes` extended for `suggest_eh_setup` (§6.3).
- `smoke-guided.mjs --phase P24` — the Improve step's "composer seeded, nothing sent" check becomes "sent as a user message → confirmation card → decline" (§5.7 P25 behaviour).
- P25 gate (spec §10 "P25 gate decisions"): `test_guided_mode_prompt.py::SUGGEST_SENTENCE` follows the reworded `_EH_GUIDE_CHAINING` sentence (B1: true in both modes); `delegate.test.ts` "the button title…" now checks the per-mode title (`delegateTitle`); `ImproveCard.test.tsx` "two actions" checks "one at a time" instead of "each confirmed separately" plus the group and labels (B1, B2); `ChatPanel.sendRequest.test.tsx` fixture `CARD` (update_component, tier write) gains a comment — it is now a card the backend emits in Guided (B1); `test_eh_setup_suggest.py` checks the reworded description; the P25 smoke waits for "Done — suggest_eh_setup finished." (note 4).

---

## P26 — Verification

Spec §7.

- [ ] Real-app click-through of the Guided flow on all three templates (`smoke-guided.mjs --phase P26`): first-time user → Guided → template → site → goal → run → results → improve via the assistant (confirmation shown) → FMEA check; mode persistence both ways.
- [ ] Any new finding fixed test-first in the owning phase's files; recorded here.
- [ ] Verdicts observed per template recorded (expected from P20: data center fail, H₂ hub no target, microgrid inconclusive; differences are recorded, not forced).
- [ ] Independent QA gate GO (`…-gate-P26.md`), full backend and frontend suites green vs. baseline, `tsc` clean.
- [ ] Close-out note: what Guided still does not cover (spec §1 non-goals) and the deferred P22.9 items.
