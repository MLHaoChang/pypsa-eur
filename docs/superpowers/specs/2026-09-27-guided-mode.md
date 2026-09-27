# Guided mode — implementation spec (P22.9, P23–P26)

**Date:** 2026-09-27
**Plan:** [`docs/superpowers/plans/2026-09-27-guided-mode.md`](../plans/2026-09-27-guided-mode.md) (decisions G1–G4 are fixed by the product owner)
**Parent:** [`2026-09-26-eh-templates-guide-assistant.md`](../plans/2026-09-26-eh-templates-guide-assistant.md) (P19–P22)
**QA input:** [`docs/superpowers/qa/2026-09-27-realapp-clickthrough-expert.md`](../qa/2026-09-27-realapp-clickthrough-expert.md)
**Base commit for baselines:** `2b78c83` on `claude/epic-allen-k2t1c4`.

This document is contract-level. An implementation agent should not need a design decision beyond it. Where the spec author had to decide something the plan left open, it is listed in §10.

**Model tiering.** Implementation of each phase: Opus-class or lower agents, one phase per agent, TDD. Planning, spec changes and the per-phase plan review / QA-gate verdict: Fable. An implementer who finds this spec wrong stops and reports; it does not redesign.

**Conventions used below.**

| Symbol | Meaning |
|---|---|
| `FE/` | `pypsa-gui/frontend/src/` |
| `BE/` | `pypsa-gui/backend/` |
| `PY` | `PYTHONPATH=/home/user/pypsa-eur:/home/user/pypsa-eur/pypsa-gui/backend /tmp/claude-0/venv/bin/python` |
| "Expert unchanged" | In `uiMode === 'expert'` every rendered DOM, request body and backend response is identical to the base commit, except where a P22.9 fix says otherwise. Tests named `*.expertUnchanged.*` pin this. |

All line numbers were verified on the base commit and are anchors, not contracts; grep the quoted symbol if a line moved.

---

## 1. Scope

| Phase | Delivers | Depends on |
|---|---|---|
| **Baseline** | Suite baselines on `2b78c83`; one-time route-inventory catch-up (§4.1). | — |
| **P22.9-BE** | Bug 3 (`preserve_bus_topology`, invariant tests), bug 4 backend (`p_set_peak`), the bug 2 backend test. | Baseline |
| **P22.9-FE** | Bugs 1, 2 (FE), 4 (FE), 7; obstacles 2, 3, 9; verdict next-step + chip colour; rename + finished cue; `smoke-guided.mjs`. | P22.9-BE |
| **P23** | `uiMode` store, default rule, new-project rule, header/palette switch, hiding rules, `hubDesign` panel slot (empty shell). | P22.9-FE |
| **P24-BE** | `GET /api/results/eh_review` (+ `stale`), readiness additions, catalogue additions, FE client types, the two hook lifts and `EhReferenceDesignPanel` exports (Expert behaviour unchanged). | P23 |
| **P24-FE** | Hub-design store, rail and five cards. | P24-BE |
| **P25** | `chatStore.sendRequest`, `ui_mode`/`guided_step` in the chat request (Guided only), guided prompt addendum, `suggest_eh_setup`, stub scripting (§6.6). | P24-FE |
| **P26** | Real-app click-through of the Guided flow on all three templates, independent QA gate, full suites. | P25 |

Each half-phase is shippable on its own (additive backend fields; lifted hooks with unchanged behaviour) and each runs the full §8 gate.

**Non-goals (all phases).** No new engine; no change to study, sweep, review or readiness *semantics* (additive fields only); no per-user server-side preference (mode is per browser); no Guided coverage for anything outside the EH hub-design flow; no change to the command palette beyond one new entry; no change to the assistant's tool tiers; no mobile layout; chat-created template projects (`create_project_from_template`) do not switch the mode (see §10).

**How the Guided flow answers the click-through obstacles.**

| Obstacle | Guided answer |
|---|---|
| 1 — the study is hard to find | P23: Guided opens the `hubDesign` panel by default when a project is open; the sidebar has one "Hub design" entry. P22.9 also renames the Expert section and adds a finished cue. |
| 4 — report below the fold, no finished cue | P24: the Results card is the fourth step; the rail moves to it automatically when the study finishes (§5.6). P22.9 adds the Expert cue. |
| 6 — jargon | P24: cards use only catalogue plain-language `fields` (§5.8); every term has a "?" hover; P25 addendum asks the assistant for plain language in Guided. |
| 7 — FMEA tab off-screen | P23: Guided shows two Results tabs (adequacy, fmea); P24 Improve card links straight to it with `requestResultsTab('fmea')`. |
| 8 — VOLL hard to find | P24: the Goal card shows VOLL as a read-only line with a "Let the assistant set it" button (`update_solver_config` via confirmation card); templates ship VOLL 5000 already. |

---

## 2. P22.9 — Expert-flow fixes

Every item is a bug fix or a small affordance in the Expert UI. Each has a red test first.

### 2.1 Bug 3 — the live network is mutated by the study / sweep (serious)

**Observed.** After an **FMEA sweep** on the data-center template, every bus went `control` PQ → Slack, `sub_network` blank → `0/1/2` and `generator` blank → `grid_supply`/`genset_1`/`__voll_it_load` in the Buses table (`GET /api/network/buses`, `BE/routers/network.py:239` → `services/network_crud.py:64 _get_component` → `_serialize_component`); `/links` differed too. **The EH study alone leaves both tables equal** — verified 2026-09-27 over HTTP by the spec review (appendix A of the review): the click-through ran the study and then the sweep before looking. The study copies under the lock (`eh_study.py:969–971`), readiness copies first (`eh_readiness.py:100`), `_detach_solver_model` only nulls `model.solver_model`, and `tests/test_energy_hub_study_isolation.py:92–113` already guards links / dispatch / `p_nom_opt`.

**Mechanism (verified).** `run_contingency_sweep` (`BE/services/adequacy/sweep.py:271`) solves the **live** network in place by design (`freeze_capacities` → contingency solves in a `try/finally: unfreeze()` at 309–360 → closing `_restore_base_guarded` at **375, outside that `finally`**). The only `.optimize(` is `services/solver_service.py:1000`; PyPSA 1.1.2 runs `determine_network_topology()` in optimize post-processing when `n.c.sub_networks.static.empty`, which writes `buses.control` (one Slack per sub-network), `buses.sub_network`, `buses.generator` and adds `SubNetwork` rows. The closing re-solve restores dispatch and capacities, not those columns; on a failed base solve (`raise RuntimeError` at 316) it never runs at all. `Network.copy` does not call it (checked).

**Reproduction (HTTP, in a pytest).**
1. `client.post("/api/projects/from_template/eh_datacenter")`, activate it (the e2e test's `_project_from_template` helper in `tests/test_energy_hub_templates_e2e.py:34`).
2. `before = client.get("/api/network/buses").json()`, same for `/links`.
3. `POST /api/results/fmea_sweep` with the template's stress registry; poll `GET /api/results/fmea_sweep` to `done`.
4. `after = …`; `assert after == before` fails today (whole rows, every column).

**Fix contract.**
1. `preserve_bus_topology(n)` — a context manager in `sweep.py` — saves `n.buses[["control", "sub_network", "generator"]]` (copy) and `sorted(n.sub_networks.index)`; on exit (every path, including exceptions and the stop event) it writes the three columns back (`n.buses.loc[:, cols] = saved`) and removes every `SubNetwork` row that was not there before (`n.remove("SubNetwork", new_rows)`). It verifies `n.buses[cols].equals(saved)` and `sorted(n.sub_networks.index) == saved_index` and logs (never raises) on mismatch. Do **not** compare `n.sub_networks.equals(...)` on an empty frame (dtype/index metadata differ).
2. `run_contingency_sweep`: an outermost `with preserve_bus_topology(network):` encloses the **entire body from `freeze_capacities` through the `_restore_base_guarded` call** (lines 309–381), so the restore runs **after** the closing re-solve and on every exception path. Placing it inside the `unfreeze` `finally` would run before the closing solve, which would re-apply the columns.
3. The same wrapper goes at the entry of every study function that receives the **live** network and solves it: `run_frontier_sweep` (`frontier.py`, whole body incl. `_restore_base`) and the live-network paths of `coupling_loop_runner.py` and `margin_loop_runner.py`. For each of the twelve `run_simulation(` call sites (`frontier.py:145,229,267`, `coupling_loop_runner.py:319,464`, `margin_loop_runner.py:453,772`, `dtc.py:408,608`, `levers.py:326`, `redundancy.py:593`, `eh_study.py:219`) the implementer records in the phase note whether it runs on the live network or a private copy; private-copy sites are left alone.
4. Note (not a regression): a *foreground* `POST /api/simulation/run` still writes `control`/`sub_network`/`generator` on the live network, as it does today in Expert; that is out of scope.
- **Invariant tests** (new file `BE/tests/test_live_network_untouched.py`, created in **P22.9-BE** before gate row 2 lists it):
  - `test_fmea_sweep_leaves_the_live_tables_equal` — whole rows of `/buses` and `/links` equal before/after.
  - `test_fmea_sweep_restores_topology_columns_after_abort` (start, abort, assert equal).
  - `test_eh_study_leaves_the_live_tables_equal` — a **guard** (passes today; kept so a future stage that solves the live object fails CI). Data-center template; if it exceeds 60 s locally, mark `slow` and add a fast variant on the `_feeder_hub` fixture from `tests/test_energy_hub_frontier_fmea.py`.
  - `test_a_foreground_solve_still_works_after_a_sweep` — after the sweep + restore, `POST /api/simulation/run` completes with an optimal condition (the restore must not break the next user solve).

### 2.2 Bug 2 — contradictory solved state

**Observed.** After the study + sweep the dock greeting says "Solved — the results match the network as it stands." (`FE/components/ChatLaunchGreeting.tsx:54`, from `status.dispatch === 'fresh'`) while the canvas footer (`FE/pages/TopologyCanvas.tsx:1851`) and `SnapshotPicker.tsx:202` say "Run a simulation to enable" (`hasResults = !!status.condition && status.solve_time != null`, `SnapshotPicker.tsx:146`).

**Mechanism.** The sweep's closing base re-solve leaves dispatch on the live network, so `_dispatch_status` (`BE/routers/simulation.py:984–1006`) reports `fresh`; no foreground solve was recorded, so `condition`/`solve_time` are null. Two readers, two signals.

**Fix contract (frontend only, one signal).** `solveLine` treats `dispatch === 'fresh'` as "Solved" **only when** `status.condition != null && status.solve_time != null`; otherwise, with `dispatch === 'fresh'`, it says: `'The network carries dispatch from a study re-solve, but no foreground solve is recorded — run a simulation for results you can read here.'`. Backend untouched.
**Tests.** `FE/components/ChatLaunchGreeting.solvedState.test.tsx`: (fresh, condition present) → "Solved —"; (fresh, condition null) → the study-re-solve sentence; (stale) unchanged. Backend: extend `test_live_network_untouched.py` with `test_after_a_sweep_status_reports_dispatch_fresh_without_a_foreground_condition` documenting the backend state the FE sentence describes (so a later backend change that records the re-solve as a foreground solve fails a test and the FE copy gets revisited).

### 2.3 Bug 4 — a loaded bus shows "Total load 0 MW"

**Observed.** `it_bus` has a Load whose `p_set` lives in `loads_t.p_set`; `FE/layout/PropertiesPanel.tsx:1613` sums static `l.p_set`.

**Fix contract.**
- Backend, additive: the Load rows from `GET /api/network/loads` gain `p_set_peak: float | null` = `max(loads_t.p_set[name])` when the column exists, else the static `p_set`, else `null`. Implemented inside `_serialize_component(n, attr, transient)` (`BE/services/network_crud.py`, reached from `_get_component` at line 64) under `attr == "loads"`, so the generic route and the path-scoped route that also uses it agree; no other class changes. Tests cover both routes.
- Frontend: `totalLoad = Σ |l.p_set_peak ?? l.p_set ?? 0|`; the row label becomes **"Peak load"** with title "Largest hourly load across the horizon (time-series aware)". `FE/api/types.ts` `Load` gains `p_set_peak?: number | null`.
- **Tests.** BE `tests/test_network_loads_peak.py`: static-only load → `p_set_peak == p_set`; time-series load → max of the series; no p_set → null. FE `layout/PropertiesPanel.peakLoad.test.tsx`: a bus with a ts load renders the peak, not 0.

### 2.4 Bug 1 — raw numbers

| Where | Now | Fix |
|---|---|---|
| `FE/pages/results/EhReferenceDesignPanel.tsx:52` `cell()` used by the DtC stress / planning tables (~1614–1634) | `String(v)` | numeric → `fmtEnergy(v, 2)` (MWh) for `*_unserved_mwh`, `fmtCurrency(v, 2)` for `*_eur`, else `String(v)`. Add `cellNum(v, kind)`; keep `cell` for strings. |
| `FE/pages/results/FmeaTab.tsx:~280` severity / criticality | `toFixed(0)` | `fmtCurrency(v, 1)` for both €-columns; the sort keys are untouched. |

**Tests.** `EhReferenceDesignPanel.formatting.test.tsx` (a DtC row with `59558.514428810326` renders `59.56 GWh`? — no: `fmtEnergy` scales; assert the rendered string equals `fmtEnergy(59558.514428810326, 2)`), `FmeaTab.formatting.test.tsx` (a row with `criticality_eur_per_year: 590000` renders `€590.0 k`).

### 2.5 Bug 7 — console noise from `/api/projects/unclaimed` in local mode

`FE/pages/ProjectsHomePage.tsx:218` queries `projectsApi.listUnclaimed` unconditionally; `FE/api/projects.ts:114–125` swallows the 404, but the axios client logs it first. **Fix:** `enabled: authEnabled` (from `FE/auth/config`) on that query; the local-mode page never issues the request. **Test:** `ProjectsHomePage.unclaimed.test.tsx` — with `authEnabled=false` the mocked `listUnclaimed` is not called; with `true` it is.

### 2.6 Obstacle 2 — a template project does not open the workbench

`FE/layout/NewProjectWizard.tsx:258–270` (`importMut.onSuccess`) sets the current project but neither adds a tab nor navigates; from `/projects` the user stays there.
**Fix contract.** In `onSuccess`: `addTab(res.imported)` (parity with `Sidebar.newProjectMut`, `Sidebar.tsx:1721`), then if `window.location.pathname !== '/app'` → `navigate(getPostLoginPath(res.imported))` (`FE/auth/resume.ts`). `useNavigate()` is available: both mounts are inside the router (`FE/routes.tsx`). Apply the same two lines to the From-file and Clone tabs' success handlers (`NewProjectWizard.tsx:362`, `:482`) — same defect, same fix. P23 hooks its template rule into this same `onSuccess` (§3.4).
**Tests.** `NewProjectWizard.templates.test.tsx` gains: from `/projects`, success navigates to `/app?project=<name>` and `addTab` was called; from `/app`, no navigation.

### 2.7 Obstacle 3 — the tagging tour dead-ends

The `eh_tagging` tour's targets `eh-bus-fields` / `eh-link-role` render only in the Properties panel's Bus/Link card in **Edit** mode (`FE/layout/properties/cardKit.tsx` EH section; `PropertiesPanel.tsx` `editing` state at 1592/1803/2138); it is launched from Results, where the Properties panel is not rendered (`App.tsx`: `{!activeSlidePanel && <PropertiesPanel />}`).

**Fix contract.**
1. `GuideButton` gains an optional `prepare?: () => void | Promise<void>` prop, run and awaited before the tour mounts.
2. The EH panel's tagging `GuideButton` (`EhReferenceDesignPanel.tsx:~807`) passes `prepare` = `prepareTaggingTour()` (new, `FE/pages/results/prepareTaggingTour.ts`):
   - pick a bus: the first `eh_poc` bus, else the first bus (from the `buses` query already in the panel's query cache; fetch via `networkApi` if absent);
   - `useUIStore.getState().setSlidePanel(null)`; `setSelectedComponent({ type: 'Bus', name })` (`SelectedComponent` is `{ type: string; name: string }`, `uiStore.ts:9`; action at `~549`); `openRightPanel()`;
   - `useUIStore.getState().requestPropertiesEdit('Bus')` — **new** uiStore field `propertiesEditRequest: 'Bus' | 'Link' | null` + `requestPropertiesEdit(c)` / `clearPropertiesEditRequest()`; the Bus and Link cards consume it in an effect (`setEditing(true)` then clear).
3. Test ids: the Bus card's Edit button gets `data-testid="props-edit-bus"`, the Link card's `props-edit-link`. The catalogue's two `eh_tagging` steps gain `reveal: "props-edit-bus"` / `"props-edit-link"` so re-entering the tour from a non-edit state also works (`GuidedTour` clicks `reveal` when the target is missing, `GuidedTour.tsx:~128`).
4. Launch from the canvas: the Bus card's EH section header gets `<GuideButton tourId="eh_tagging" testId="eh-bus-guide-button" label="How to tag" />` (no `prepare`: the card is already open).
5. Step 2 (Link role): `prepare` is not enough for a Link target, because the tour selects a bus. The step's `body` tells the user to select the import Link; it is marked `optional: true` so the tour does not stall (existing `visibleSteps` semantics). Its `reveal` is `props-edit-link`, and `GuidedTour` only clicks a `reveal` whose element exists — so on a Bus card (no `props-edit-link` rendered) nothing is clicked; the implementer keeps that guard explicit (`findTarget(step.reveal)?.click()` already does) and adds a test that a `reveal` absent from the DOM is not clicked.
**Tests.** `GuidedTour.prepare.test.tsx` (prepare runs before the first step renders; a rejected prepare still opens the tour with the missing-target note); `EhReferenceDesignPanel.taggingTour.test.tsx` (click → slide panel null, selected Bus set, right panel open, `propertiesEditRequest === 'Bus'`); `PropertiesPanel.editRequest.test.tsx` (request → Bus card enters edit, request cleared); `test_guides.py` already pins that every `reveal` is a rendered test id — the new ids make it pass.

### 2.8 Obstacle 9 — Send stays enabled without an API key

`FE/components/ChatPanel.tsx` disables Send only on `streaming`. `ChatHealth.chat_ready?: boolean` exists (`FE/api/chat.ts:~213`).
**Fix contract.** ChatPanel reads `useQuery(['chat','health'], getChatHealth, { staleTime: 30_000, retry: false })` — the **same** `['chat','health']` key `ApiKeySetup.tsx:98` and `AssistantModelSettings.tsx:469` already use, so a key save and a profile switch both invalidate it. When `chat_ready === false`: Send and Enter-to-send are disabled, `title="Add an API key first (Settings → Assistant)"`, and the composer shows the existing `<ApiKeySetup>` inline (the `missing_api_key` branch at `ChatPanel.tsx:795` already renders it — reuse). `chat_ready` undefined (probe failed / older backend) → **enabled** (fail-open: a probe outage must not lock the assistant). `chat_ready` comes from the **active profile** (`routers/chat.py:158–176`): a bearer profile is ready iff its `key_env` is set; an `auth: none` profile (the smoke stub) is always ready.
**Expected edits to existing tests** (justification: they model a working assistant; no assertion depends on `false`): `components/ChatPanel.test.tsx:44`, `ChatPanel.actions.test.tsx:52`, `ChatPanel.profile.test.tsx:59` mock `getChatHealth` with `chat_ready: false` — change to `true`. Today those mocks are inert because ChatPanel does not query health; after this fix they would disable Send and break `ChatPanel.test.tsx:93/311`. Record the one-line justification in the plan per §8.3.
**Tests.** `ChatPanel.sendGate.test.tsx`: `chat_ready:false` → Send disabled and `createChatStream` not called on Enter; `chat_ready:true` and `undefined` → enabled; invalidating `['chat','health']` re-enables.

### 2.9 Obstacle 5 (part) — verdict next step and chip colour

- `certificationHeadline` (`EhReferenceDesignPanel.tsx:220`, rendered at `~1109`, `data-testid="eh-certification-verdict"`) gains `next: string | null`:

| verdict | `next` |
|---|---|
| `fail` | "Next: tighten the energy target or add firm capacity — press **Ask the assistant** for a reviewed recommendation." |
| `inconclusive` | "Next: raise MC draws (Pack settings → Draws) or tighten the plan; an inconclusive verdict is not a failure." |
| `pass` | null |
| no target / `none` metric | "Next: set a shortfall target (Pack settings → LOLE target) to certify." |

  Rendered under the verdict as `data-testid="eh-certification-next"`. The catalogue step `eh-certification-verdict.enter` is rewritten to cover fail and inconclusive (P24 owns catalogue text; this one line moves with P22.9).
- `statusTone('ok')` (`:194`) returns `text-accent` (brand red). Change to the theme's success token; if `index.css` has no `--color-ok`/`--color-success`, add `--color-ok` (light `#1a7f37`, dark `#3fb950`) and a `text-ok` utility. `not_established` keeps `text-warn`; `skipped` keeps `text-muted`.
**Tests.** `EhReferenceDesignPanel.verdictNext.test.tsx` (four verdict cases) and an assertion that `statusTone('ok') !== statusTone('not_established')` and does not contain `accent`.

### 2.10 Quick win — section name and finished cue (Expert)

- The collapsible header (`EhReferenceDesignPanel.tsx:800`) reads **"Energy Hub reference design"**; the tour step and catalogue mention of "Reference design" follow.
- **Finished cue.** When `study.status` transitions `running → done` while the panel is mounted (ref of the previous status), render a sticky pill at the top of the panel body, `data-testid="eh-study-finished-cue"`: "Study finished — view report", `onClick` → `document.querySelector('[data-testid="eh-report"]')?.scrollIntoView({ block: 'start', behavior: 'smooth' })`. The pill hides on click, on a new run, and on project switch. On `failed`/`aborted` the existing `eh-error`/`eh-aborted` blocks are scrolled into view once.
- The template banner (`eh-template-banner`) stays visible while `running`; readiness shows "Readiness is paused while the study runs" instead of unmounting.
**Tests.** `EhReferenceDesignPanel.finishedCue.test.tsx` (transition renders the cue; a fresh mount with `done` does not; click scrolls — `scrollIntoView` mocked), `…banner.test.tsx` (banner present with `status:'running'`).

### 2.11 P22.9 integration gate

See §8 for the common gate. P22.9 is split into **P22.9-BE** (bug 3, bug 4 backend, the bug 2 backend test, `test_live_network_untouched.py`) and **P22.9-FE** (everything else incl. `smoke-guided.mjs`); each half runs the full gate. Phase-specific browser smoke path (P22.9-FE), **in this order**: (a) backend started with `ANTHROPIC_API_KEY` unset and the default Anthropic profile active → `GET /api/chat/health` reports `chat_ready:false` and `chat-send` is disabled; (b) only then the script `PUT`s and activates the stub profile (`POST /api/chat/settings/llm/active`) → Send enabled; (c) data-center template from `/projects` → **workbench opens without "Resume"** → Results → Adequacy → "Energy Hub reference design" → Run (recommended) → finished cue appears → click → report in view; verdict shows a `next` line; `/buses` and `/links` rows identical before/after the study and after the FMEA sweep; "How to tag the network" → Properties opens on a bus in Edit with the coach mark on `eh-bus-fields`.

---

## 3. P23 — Guided / Expert mode

### 3.1 State (`FE/store/uiStore.ts`)

| Field | Type | Persistence |
|---|---|---|
| `uiMode` | `'guided' \| 'expert'` | `network-diagram:ui-mode` (value `guided`/`expert`) |
| `uiModeExplicit` | `boolean` | `network-diagram:ui-mode-explicit` (`'1'` when true; key absent otherwise) |
| `setUiMode(mode, opts?: { explicit?: boolean })` | action | writes `ui-mode`; writes `ui-mode-explicit='1'` only when `opts.explicit === true`; never clears an existing explicit flag |
| `noteNewProjectCreated(kind)` | action | `kind ∈ {'blank','template','file','clone'}` (informational, logged). If `!uiModeExplicit` → `setUiMode('guided')` (implicit, persisted). If explicit → no-op. G4 literally: **every** new project starts Guided unless the user chose a mode explicitly. |
| `readStoredUiMode()` | test helper | mirrors `readStoredDockOpen` |

Follows the density pattern (`DENSITY_KEY` at 77, `storedDensity` at 163, `setDensity` at 524). All reads and writes are `try/catch`.

### 3.2 Default rule and the startup race

**Truth table** (evaluated once at module load):

| stored `ui-mode` | stored `explicit` | first-time user? | initial `uiMode` | `uiModeExplicit` |
|---|---|---|---|---|
| present | `1` | — | stored | true |
| present | absent | — | stored | false |
| absent | — | yes | `guided` | false |
| absent | — | no | `expert` | false |
| unreadable storage (throws) | — | — | `expert` | false |

**First-time user** := no localStorage key `k` such that `isLegacyKey(k)`, where
```
isLegacyKey(k) = k !== 'network-diagram:theme-schema'
              && (k.startsWith('network-diagram:') || k.startsWith('pypsa-guide-seen:') || k === 'results:active-tab')
```
Enumerate with `localStorage.length` / `localStorage.key(i)`.

**Race.** `storedTheme()` (`uiStore.ts:148–152`) **writes** `network-diagram:theme-schema` during `create()`'s initial-state evaluation. It is the only import-time writer in the app (audited: every other `setItem` is inside an action, effect or handler — `ChatPanel.writePref`, `GuidedTour.markSeen`, `BottomPanel`, `extrasStore`, `topologyLayoutStore`, `MapCanvas`, `Results.setTab`, `selectionMemory`, `shared.tsx`, `CarrierFilter`, `TimeSeriesManager`, `CompareView`, `projectActions`). Two defences, both required:
1. `const FIRST_RUN = detectFirstRun()` is a **module-level constant declared above `create(...)`** in `uiStore.ts`, so it runs before `storedTheme()`.
2. `isLegacyKey` excludes `theme-schema` anyway.

The initial `uiMode` is computed by `initialUiMode(FIRST_RUN)`. If the store did not persist the implicit default, a first-time user who never touches the switch would become an "existing user" as soon as any legacy key appeared and flip to Expert on the next load. So `initialUiMode` **persists** `ui-mode=guided` (implicit) when it resolved via the first-time branch — this makes `uiStore.ts` the second import-time writer, which is why the `firstRunOrder` test asserts the write order of **both** `theme-schema` and `ui-mode` relative to `detectFirstRun`. Net effect: the first-ever load decides `guided` and writes it; nothing later overrides it except the user or the new-project rule (§3.4).

### 3.3 Switch

- `AppHeader.tsx`: a segmented control `data-testid="ui-mode-switch"` with two buttons `ui-mode-guided` / `ui-mode-expert`, placed immediately left of the status pill (before the block at `AppHeader.tsx:~1000`). `aria-pressed` reflects the mode. Hover titles: Guided — "A step-by-step hub design with the assistant doing the engineering; advanced panels are hidden but reachable through the assistant."; Expert — "Every panel and tab, as today." Click → `setUiMode(m, { explicit: true })`; a toast "Guided mode on — advanced panels hidden, ask the assistant for any of them" / "Expert mode on".
- `CommandPalette.tsx`: entry `id: 'act-ui-mode'`, title "Switch to Guided mode" / "Switch to Expert mode" (next state, like `act-toggle-theme` at 466), same action.

### 3.4 New-project rule (G4, literal)

`useUIStore.getState().noteNewProjectCreated(kind)` is called from **every** project-creation success handler, before any navigation:

| Call site | kind |
|---|---|
| `NewProjectWizard.tsx` Templates tab `importMut.onSuccess` (`:258–270`, after the P22.9 fix; the P24 lifted hook keeps the call) | `'template'` |
| `NewProjectWizard.tsx` From-file tab (`:362`) | `'file'` |
| `NewProjectWizard.tsx` Clone tab (`:482`) | `'clone'` |
| `Sidebar.newProjectMut.onSuccess` (`Sidebar.tsx:1721`) | `'blank'` |
| `ProjectsHomePage.createBlank.onSuccess` (`ProjectsHomePage.tsx:311`) | `'blank'` |

Opening an existing project never changes the mode. Chat-created projects (`create_project_from_template`, `save_project_as`) are a v1 non-goal (§10).

### 3.5 What Guided hides

New test ids first (Expert DOM otherwise unchanged; adding `data-testid` attributes is the only Expert-visible diff and is allowed):

| Element | test id |
|---|---|
| PROJECT / DATA / SIMULATION `SectionHdr` (expanded) and `IconStripBtn` (icon strip) | `sidebar-section-project`, `sidebar-section-data`, `sidebar-section-simulation` |
| `ModeSwitcher` (Select/Connect, both sizes) | `sidebar-mode-switcher` |
| New Guided-only row | `sidebar-hub-design` |
| Results tab buttons | `results-tab-<id>` |

Guided rules:

| Surface | Guided | Stays reachable via |
|---|---|---|
| Sidebar `AssistantNavButton` | shown | — |
| Sidebar `sidebar-hub-design` ("Hub design", `Compass` icon) → `setSlidePanel('hubDesign')` | shown, above PROJECT | — |
| PROJECT section | header card (name, autosave), **Save**, **Recent**, **Projects home** only. Hidden rows: Project info, Snapshots, Scenarios, Duplicate project, Export bundle, Workspace panel | palette, assistant (`ui_open_panel`) |
| DATA section, SIMULATION section (whole sections incl. icon-strip buttons) | hidden | palette, assistant |
| `ModeSwitcher` | hidden | keyboard V / C still work (unchanged handler) |
| `PreferencesFooter` | shown | — |
| AppHeader Run / status / properties toggle / user menu | unchanged | — |
| Results tabs | only `adequacy`, `fmea` (`expertOnly: true` on every other `TABS` row; filter `.filter(t => (!t.multiOnly \|\| multi) && (!t.expertOnly \|\| uiMode === 'expert'))`) | Expert |
| Results compare-rail toggle | unchanged | — |
| Solve queue | sidebar row hidden (SIMULATION is hidden); header Run button still queues | palette `act-solvequeue`, assistant |
| Canvas, BottomPanel, PropertiesPanel, MapModeSwitcher | unchanged (visible only when no full-screen panel is open) | — |
| Command palette | unchanged + `act-ui-mode` | — |

The `Sidebar` `sections` state (`Sidebar.tsx:1699`) is untouched; Guided is a render condition (`uiMode === 'guided'`) around the section headers and contents, and a `guided` prop on `ProjectSectionContent`.

### 3.6 `hubDesign` panel slot (shell in P23, content in P24)

- `SlidePanel` union (`uiStore.ts:42`) += `'hubDesign'`. `PANEL_META.hubDesign = { eyebrow: 'GUIDED', title: 'Hub design' }` (`App.tsx:99`); `FULL_SCREEN_TABS` += `'hubDesign'` (`App.tsx:119`); `fullPageContent` renders `<HubDesignPanel />` (P23 ships a placeholder that renders `data-testid="hub-design-panel"` and "Coming in the next step").
- `ChatPanel._normalizePanelId` (`ChatPanel.tsx:120`) aliases `HubDesign`, `hubDesign`, `hub_design` → `'hubDesign'` and the `setSlidePanel` allow-list (`:~222`) accepts it. Backend `SAFETY_PANEL_ENUM` (`chat_tools_schema.py:51`) += `"HubDesign", "hubDesign"`; `ui_open_panel`'s description mentions it. (`test_chat_tools_endpoint_map` is unaffected; the schema snapshot tests, if any pin the enum, are updated.)
- **Default open.** `App.tsx` effect: when `uiMode === 'guided' && currentProject && activeSlidePanel == null && autoOpenedFor.current !== currentProject` → `setSlidePanel('hubDesign')`, `autoOpenedFor.current = currentProject` (a `useRef<string | null>`). Once per project per session; closing it is respected.

### 3.7 Behaviour when a surface becomes hidden on switching

On `setUiMode('guided')` (inside the action, synchronous):
- if `activeSlidePanel ∉ {'hubDesign', 'results', null}` → `activeSlidePanel = currentProject ? 'hubDesign' : null`.
- Results tab: `Results.tsx` derives `effectiveTab = (uiMode === 'guided' && !GUIDED_TABS.has(tab)) ? 'adequacy' : tab` at the point where `overview → dispatch` is already coerced (`Results.tsx:663`) and in the strip's active check; it does **not** write `results:active-tab`, so switching back to Expert restores the user's tab.
- `paletteMode`, dock, compare rail: untouched.

On `setUiMode('expert')`: nothing is closed or opened; `hubDesign` may stay open (it has Close).

Panels opened later by the assistant (`ui_open_panel`) render in Guided regardless of the hidden list: the list governs navigation chrome, not the panel host.

### 3.8 P23 files

| File | Change |
|---|---|
| `FE/store/uiStore.ts` | fields, keys, `FIRST_RUN`, actions, switch-time pruning |
| `FE/layout/AppHeader.tsx` | switch |
| `FE/components/CommandPalette.tsx` | `act-ui-mode` |
| `FE/layout/Sidebar.tsx` | test ids, Guided render conditions, `sidebar-hub-design`, `guided` prop |
| `FE/pages/Results.tsx` | `expertOnly`, filter, `effectiveTab`, tab test ids |
| `FE/App.tsx` | `PANEL_META`, `FULL_SCREEN_TABS`, `fullPageContent`, auto-open effect |
| `FE/pages/hubDesign/HubDesignPanel.tsx` | placeholder |
| `FE/layout/NewProjectWizard.tsx` (three tabs), `FE/layout/Sidebar.tsx` (`newProjectMut`), `FE/pages/ProjectsHomePage.tsx` (`createBlank`) | `noteNewProjectCreated(kind)` |
| `FE/components/ChatPanel.tsx` | panel alias |
| `BE/services/chat_tools_schema.py` | `SAFETY_PANEL_ENUM` |

### 3.9 P23 tests

| File | Asserts |
|---|---|
| `FE/store/uiStore.uiMode.test.ts` | the five truth-table rows (use `vi.resetModules()` + dynamic `import('./uiStore')` per case so `FIRST_RUN` re-evaluates; seed storage before import); a storage containing only `network-diagram:theme-schema` → guided; containing `network-diagram:current-project` → expert; `pypsa-guide-seen:x` → expert; the first-time branch persisted `ui-mode=guided`; explicit set persists both keys; `noteNewProjectCreated(kind)` for each of the four kinds flips implicit expert → guided (persisted, implicit) and never flips an explicit expert; `setUiMode('guided')` with `activeSlidePanel='simparams'` → `hubDesign` when a project is open, `null` otherwise; `'results'` stays. |
| `FE/store/uiStore.firstRunOrder.test.ts` | spy `Storage.prototype.setItem`; fresh import; every `setItem` call (`theme-schema` **and** `ui-mode`) is **after** `detectFirstRun` ran (assert via a module-level call-order probe exported for tests, `__firstRunProbe`), and with an empty store the resolved mode is guided even though `theme-schema` gets written during the same import. |
| `FE/layout/Sidebar.newProject.test.tsx`, `FE/pages/ProjectsHomePage.createBlank.test.tsx`, `NewProjectWizard.*.test.tsx` | each success handler calls `noteNewProjectCreated` with the right kind. |
| `FE/layout/Sidebar.uiMode.test.tsx` | guided: `sidebar-section-data`/`-simulation`/`sidebar-mode-switcher` absent, `sidebar-hub-design` and `sidebar-assistant` present, PROJECT shows only the four allowed rows; expert: all present and the DOM (`container.innerHTML`) equals a snapshot taken from the base commit's Sidebar with the same mocks (`Sidebar.expertUnchanged.test.tsx`, snapshot committed). Both sidebar modes (expanded, icon). |
| `FE/pages/Results.uiMode.test.tsx` | guided: exactly `results-tab-adequacy`, `results-tab-fmea`; stored tab `dispatch` → adequacy content renders and `results:active-tab` still reads `dispatch`; expert: all thirteen (minus `multiOnly` rule). |
| `FE/layout/AppHeader.uiMode.test.tsx` | the switch renders, `aria-pressed`, click calls `setUiMode(m, {explicit:true})`. |
| `FE/App.hubDesignAutoOpen.test.tsx` | guided + project → `hubDesign` opens once; closing it does not reopen; expert never auto-opens. |
| `FE/components/CommandPalette.uiMode.test.tsx` | entry present with the next-state title. |
| `BE/tests/test_chat_tools_schema_panels.py` | `HubDesign`/`hubDesign` in `SAFETY_PANEL_ENUM`; `ui_open_panel` schema validates `{"panel_id": "hubDesign"}`. |

---

## 4. P24 — Backend contracts

### 4.1 `GET /api/results/eh_review`

One source with the chat tool. Refactor first, then add the route.

- **Lift** the body of `chat_tools.review_eh_study` (`BE/services/chat_tools.py:1606–1633`) into `BE/services/adequacy/eh_review.py`:
  ```python
  def review_latest(store: dict, record: dict | None) -> dict:
      """{'status': 'running'|'no_data'|'ok', ...} — exactly what the chat tool returns."""
  ```
  containing the running check, `eh_reference_design_http_payload(store)` fallback to `record["report"]`, `review_report(body, record)`, `out["source"]`, **`out["stale"]: bool`** (true when the report came from the study record because the stored report was cleared — the FE reads this boolean, never the `source` prose), and `out["status"] = "ok"` on success (new keys; additive for the tool). The tool becomes `return review_latest(R._state, R.get_eh_study() if dict else None)` and keeps its `no_data` message text (`_ADEQUACY_NO_DATA_HINTS["eh_reference_design"]`) via a parameter `no_data_message`.
- **Route** (`BE/routers/results.py`, next to `get_eh_reference_design` at 1499):

| | |
|---|---|
| Path | `GET /api/results/eh_review` |
| Query | none (v1). |
| 200 | `review_latest(...)` body when `status ∈ {'ok','running'}`. Running body: `{"status":"running","message":...}`. |
| 204 | when `status == 'no_data'` (no study record and no stored report). Same convention as `/eh_reference_design`. |
| 404 | never (session-scoped state, like the sibling routes). |
| Auth / lock | same dependencies as `get_eh_reference_design` (none beyond the router's). |

  Response schema (200, ok):
  ```json
  {"status":"ok","source":"stored report"|"study record (…)","stale":false|true,
   "summary":{...as review_report...},
   "findings":[{"id":str,"severity":"high"|"medium"|"low"|"info","title":str,
                "evidence":{...},"recommendation":str,
                "actions":[{"tool":str,"args":{...},"effect":str}]}],
   "next_steps":[...]}
  ```
- **Endpoint map:** `TOOL_ROUTES["review_eh_study"]` changes from `_DERIVED` to `[("GET", "/api/results/eh_review")]`; regenerate `tests/fixtures/route_inventory_phase0.txt` with `PY tools/openapi_diff.py --phase0-fixture` (from `BE/`). Because the committed fixture is already 30 routes behind the app (252 vs 282 on the base commit; `test_every_http_route_resolves_in_inventory` is a subset check), the catch-up regeneration is done **once in the Baseline step** as a housekeeping change with the diff pasted into the baseline note; P24-BE's fixture diff is then exactly the one `GET /api/results/eh_review` row.
- **FE client:** `resultsApi.getEhReview()` in `FE/api/simulation.ts` (204 → `null`, like `getEhReferenceDesign`), type `EhReview`.

**Tests** (`BE/tests/test_eh_review_route.py`): 204 with no study; running → 200 `status: running`; done on the `_feeder_hub` fixture → body `==` `chat_tools.review_eh_study()` called on the same state (deep equality); `TOOL_ROUTES["review_eh_study"]` pinned; the route is in the inventory (`test_chat_tools_endpoint_map` covers it once the fixture is regenerated).

### 4.2 Readiness additions (additive, `BE/services/adequacy/eh_readiness.py:88`)

| New key | Value |
|---|---|
| `pack_defaults` | `{"target_lole_h": float\|null, "ens_cap_permyriad": float\|null, "certification_metric": str}` read from the factory pack **after** overrides are applied (the pack the study would run). |
| `outage_units` | `{"count": int, "by_class": {"Generator": int, "Link": int, "StorageUnit": int}, "missing": [{"class": str, "name": str}][:20]}` — units whose `occurrence.resolve_outage_params(n, cls)` yields finite `outage_rate_value` and `mttr_hours`; `missing` lists thermal-like units without them. **Thermal-like** := Generators with `_gen_category(carrier) == 'conventional'` (`services/profile_shapes.py:217`, the only carrier classifier in the backend — there is no renewable helper in `services/adequacy`), plus import Links. |
| `import_p_nom_mw` | `float\|null`: Σ `p_nom` of `import.links` (for "Grid connection: grid_import (40 MW)"). |

`EhReadiness` TS type gains the three optional fields. Expert panel ignores them. **Tests:** extend `tests/test_energy_hub_tagging.py` (where the readiness tests live; also `test_energy_hub_p18.py`) with one test per key on the data-center template. The expected `outage_units.count` is computed by running `resolve_outage_params` over the built network's classes and counting finite pairs — not a literal, and not a regex count of `_outage(` calls in the builder source. Pin that every pre-existing key is unchanged.

### 4.3 Catalogue additions (`BE/data/guides/eh_fmea_guide.json`)

New `fields` keys (plain language, ≤ 30 words each, no stage ids, no "P19"/"decision 6"):
`hub_start, hub_site, hub_goal, hub_results, hub_improve, site_type, grid_connection, critical_load, grid_strength, outage_data, shortfall_hours, energy_strictness, verdict, cost_at_target, top_risks, not_established, stress_scenario, fmea_check, template_provenance, voll_plain`.

New tour `hub_design` (title "Design a hub in five steps") — added in **P24-FE** together with the components that render its ids (P24-BE adds only the `fields` keys, so the catalogue target pin never goes red between halves). Targets in order with `reveal` = the rail step that shows the card; every target and reveal is a literal `data-testid` (§5.1):

| step target | reveal | optional |
|---|---|---|
| `hub-rail` | — | no |
| `hub-start-templates` | `hub-rail-step-start` | no |
| `hub-site-readiness` | `hub-rail-step-site` | no |
| `hub-site-type` | `hub-rail-step-site` | no |
| `hub-goal-lole` | `hub-rail-step-goal` | no |
| `hub-goal-run` | `hub-rail-step-goal` | no |
| `hub-results-verdict` | `hub-rail-step-results` | yes (after a run) |
| `hub-improve-list` | `hub-rail-step-improve` | yes |
| `hub-improve-fmea` | `hub-rail-step-improve` | no |

`test_guides.py::test_every_tour_target_is_a_rendered_test_id` pins them; add `test_hub_fields_present` listing the twenty keys, and `test_field_text_is_plain` (no key's text contains `P19`, `decision 6`, `Class-B`, `‱`, `lp_proxy`, `copt`).

---

## 5. P24 — Hub-design step cards (frontend)

### 5.1 Component tree and test ids

```
pages/hubDesign/
  HubDesignPanel.tsx        data-testid="hub-design-panel"
    StepRail.tsx            "hub-rail", buttons "hub-rail-step-{start|site|goal|results|improve}"
                            each with data-state="todo|current|done|blocked"
    cards/StartCard.tsx     "hub-card-start", "hub-start-templates", "hub-start-template-{id}", "hub-start-own-network", "hub-start-provenance"
    cards/SiteCard.tsx      "hub-card-site", "hub-site-readiness", rows "hub-site-{grid|critical|strength|outage}", fix buttons "hub-site-fix-{grid|critical|strength|outage}", "hub-site-type" (select)
    cards/GoalCard.tsx      "hub-card-goal", "hub-goal-lole" (number input), "hub-goal-advanced" (toggle), "hub-goal-ens", "hub-goal-voll", "hub-goal-run", "hub-goal-blocked"
    cards/ResultsCard.tsx   "hub-card-results", "hub-results-verdict", "hub-results-cost", "hub-results-risks" (+ "-{i}"), "hub-results-gaps", "hub-results-open-report", "hub-results-stale"
    cards/ImproveCard.tsx   "hub-card-improve", "hub-improve-list", items "hub-improve-{finding.id}" with "hub-improve-why-{id}", "hub-improve-do-{id}", "hub-improve-ask-{id}"; "hub-improve-fmea", "hub-improve-add-stress", "hub-improve-open-fmea"
  shared/CardShell.tsx      title, ≤3 decision slots, footer with AskButton ("hub-ask-{step}") and DelegateButton ("hub-delegate-{step}")
  shared/Term.tsx           <Term k="shortfall_hours">…</Term> → InfoTip with useGuideField(k, fallback); renders "?" glyph, data-testid="term-{k}"
  hubDesignStore.ts         zustand
  delegate.ts               ask(text) / delegate(text)
```
`InfoTip` is `FE/layout/properties/cardKit.tsx:56`.

**Literal test-id rule.** `test_guides.py::_fe_test_ids` scans `.tsx` files for **literal** `data-testid="…"` / `testId="…"` strings (regex stops at `$`), so every id used as a tour `target` or `reveal` must appear as a string literal in a component. `StepRail` therefore renders its five buttons from a literal table — `const RAIL_IDS = { start: 'hub-rail-step-start', site: 'hub-rail-step-site', goal: 'hub-rail-step-goal', results: 'hub-rail-step-results', improve: 'hub-rail-step-improve' } as const` — never a template string. `hub-start-templates`, `hub-site-readiness`, `hub-site-type`, `hub-goal-lole`, `hub-goal-run`, `hub-results-verdict`, `hub-improve-list`, `hub-improve-fmea`, `hub-rail` are literals in JSX. Ids that are not tour targets/reveals (`hub-start-template-{id}`, `hub-improve-{finding.id}`, `term-{k}`, `hub-site-fix-*` if templated) may stay templated.

### 5.2 `hubDesignStore` (`FE/pages/hubDesign/hubDesignStore.ts`)

| Field | Default | Notes |
|---|---|---|
| `project: string \| null` | null | reset guard: when `uiStore.currentProject` changes, `resetFor(project)` |
| `step: 'start'\|'site'\|'goal'\|'results'\|'improve'` | derived on reset (§5.4) | user clicks change it |
| `archetype: EhArchetype` | template `recommended_archetype` else `'strong_grid'` | Site card |
| `loleTarget: string` | template `pack_overrides.target_lole_h` else readiness `pack_defaults.target_lole_h` else `''` | Goal card; string like `PackForm` |
| `ensCap: string` | template override else `''` (pack default) | advanced |
| `userMovedRail: boolean` | false | suppresses auto-advance after a manual rail click, until the next transition |

Not persisted. `guided_step` for the chat context reads `step` (P25).

### 5.3 Data per card (existing hooks/APIs only)

| Card | Reads | Writes / actions |
|---|---|---|
| Start | `TEMPLATES` exported from `NewProjectWizard.tsx` filtered `id.startsWith('eh_')`; `resultsApi.getEhTemplate(currentProject)` (banner: name + `provenance` + `study_notes[0]`) | create: the same mutation as the wizard's Templates tab, lifted to `FE/hooks/useCreateFromTemplate.ts` (returns `{mutate, isPending, variables}`; wizard uses it too — behaviour unchanged incl. P22.9 navigation and the P23 `noteNewProjectCreated('template')` call). "Use my network" → `setStep('site')`. |
| Site | `resultsApi.getEhReadiness(archetype, undefined, undefined, { stages: undefined, pack_overrides })` with the same query key family as the Expert panel (`nk(project,'results','eh_readiness')`, extra key parts), `enabled: !running` | rows: grid = `import.links` + `import_p_nom_mw`; critical = `critical_buses`; strength = `scr.status` (`ok`→"present", else "missing" with `scr.note`); outage = `outage_units.count` (+ `missing.length`). Fix buttons → `delegate(text)` (§5.7). Site type select → `archetype`. |
| Goal | readiness `pack_defaults`; `simulationApi.getSolverConfig()` (existing client for `/simulation/config` — verify the name in `FE/api/simulation.ts`) for VOLL | Run → `resultsApi.startEhStudy(buildEhStudyBody(archetype, form).body!)` where `form = { ...(template ? formFromTemplate(template) : EMPTY_PACK_FORM), loleTarget, ensCap }` (both exported from `EhReferenceDesignPanel.tsx:106/421`); `built.error` → `hub-goal-blocked`; 409/422 → `blockerMessage`. VOLL ≤ 0 → Run disabled + "Let the assistant set it" → `delegate("Set VOLL to 5000 €/MWh so the study can price shortfall.")`. |
| Results | `resultsApi.getEhStudy()` (shared key `nk(project,'results','eh_study')`, `refetchInterval` 2 s while running — identical options to the Expert panel so the cache is shared); `resultsApi.getEhReview()` key `nk(project,'results','eh_review')`, `enabled: status==='done'`, invalidated together with the panel's `invalidateAll` set (add the key there) | headline = `summary`-derived sentence (§5.5); cost = `report.cost_at_target_eur` via `fmtCurrency` (from `getEhReferenceDesign` — reuse the panel's `reportKey` query); risks = `fmeaTopRows(report).slice(0,3)` with `Term k="top_risks"`; gaps = `notEstablishedNotes(report)` (`EhReferenceDesignPanel.tsx:~206`, export it); "Open full report" → `setSlidePanel('results')`, `requestResultsTab('adequacy')`, then `scrollIntoView` on `eh-report` after a frame. |
| Improve | same review query; `findings.filter(f => f.severity ∈ {'high','medium'})` | "Why" toggles `evidence` as a definition list; "Let the assistant do this" → `delegate(actionText(f))` (§5.7); "Ask" → `ask(askText(f))`; **Check risks (FMEA)** → `useStartFmeaSweep()` (lifted from `FmeaTab.tsx:92–116` into `FE/hooks/useStartFmeaSweep.ts`; FmeaTab uses it — unchanged behaviour) then `hub-improve-open-fmea` → results + `requestResultsTab('fmea')`; "Add a stress scenario" → `delegate("Add a stress scenario to this project's registry for <one-line context>; read the current registry first and send the whole list back with put_stress_scenarios.")`. |

### 5.4 State machine

`flowState` is derived, never stored:

| State | Condition | Rail | Cards |
|---|---|---|---|
| `no_project` | `currentProject == null` | Start only enabled | Start shows templates; the other rail steps `blocked` with title "Open or create a project first". |
| `no_study` | project, `eh_study` 204/null or `status ∈ {failed, aborted}` with no report | Start ✓ (if template) → Site current | Site, Goal enabled; Results/Improve `blocked` ("Run the study first"). Failed/aborted: Goal shows the `error` text and Run again. |
| `running` | `status === 'running'` | Goal current, spinner | Goal shows "Studying… n of budget solves" from `pipeline` if present, Abort (`resultsApi.abortEhStudy`); Site readiness paused; Results/Improve blocked. |
| `done` | `status === 'done'` and review `status === 'ok'` | Results current (auto-advance once, unless `userMovedRail`) | all enabled |
| `stale` | `done` and `review.stale === true` (the boolean from §4.1; the stored report was cleared by a later solve and the study record's copy was used) | as done + `hub-results-stale` banner: "These results are from an earlier study; the network was solved since. Run again to refresh." | all enabled |

Initial `step` on `resetFor(project)`: `no_project`→`start`; template project with `no_study`→`site`; own network `no_study`→`start`; `running`→`goal`; `done`/`stale`→`results`.

### 5.5 Plain-language headline

`headline(review, report)`:
- verdict `fail`: `Not certified: about {lole:.0f} h/yr of shortfall vs a {target:g} h/yr goal — driven by {topRisk}` where `topRisk` = first `fmea_top` row name, else "the plan's energy limit".
- `inconclusive`: `Not decided: the shortfall estimate ({lo:.0f}–{hi:.0f} h/yr) straddles the {target:g} h/yr goal — more Monte-Carlo draws would settle it.`
- `pass`: `Certified: about {lole:.1f} h/yr of shortfall, under the {target:g} h/yr goal.`
- no target: `No reliability goal was set — the study reports {lole:.1f} h/yr of shortfall. Set a goal to certify.`
- MC not run / `not_established`: `The study could not certify reliability: {note}`.
Numbers come from `review.findings[*].evidence` / `report.mc_lole_h` exactly as `review_report` reads them; never computed anew.

### 5.6 Auto-advance

On `eh_study.status` transition `running → done` (ref of previous status inside the panel): `setStep('results')` unless `userMovedRail`. On `done → running`: `setStep('goal')`. `userMovedRail` resets on every transition.

### 5.7 Delegation texts (`delegate.ts`)

`ask(text)` = `setAssistantDockOpen(true)` + `seedComposer(text)` (P22 behaviour; text shown, not sent).
`delegate(text)` = in P24: identical to `ask` (temporary); in P25: `useChatStore.getState().sendRequest(text, { source: 'hub-design' })`. One import site to change.

| Button | Text |
|---|---|
| Site fix grid | `On the Site card, the grid connection is missing. Run suggest_eh_setup, then tag the import Link with eh_role = grid_import and the grid-side bus eh_poc = true, explaining each choice before the confirmation.` (P24 text omits the `suggest_eh_setup` clause; P25 adds it.) |
| Site fix critical | `On the Site card, no critical load is tagged. Propose which buses must stay on (eh_critical = true) and tag them after I confirm.` |
| Site fix strength | `On the Site card, grid-strength data is missing. Ask me for the short-circuit level (MVA) at the point of connection and set eh_sk_mva on that bus after I confirm.` |
| Site fix outage | `On the Site card, {n} units have no outage data. List them and ask me for outage rate and repair time per unit, then set outage_rate_value and mttr_hours after I confirm.` |
| Goal VOLL | see §5.3 |
| Improve do(f) | `Apply this recommendation from the study review: "{f.title}". Run the tool {a.tool} with exactly these arguments: {JSON.stringify(a.args)}. Say in one sentence what will change, then proceed to the confirmation.` (first action only; multiple actions → one message per action, sent sequentially via the P25 queue) |
| Improve ask(f) | `Explain in plain language: "{f.title}". Evidence: {JSON.stringify(f.evidence)}. What are my options?` |
| Card footer Ask | `I am on the {Step} card of the hub design. {step question}` where step question is from `fields.hub_{step}` |
| Card footer Delegate | Start: `Pick the template that best fits a {archetype label} site and create the project.`; Site: `Review the Site card for this network and fix every gap you can, one confirmation at a time.`; Goal: `Run the Energy Hub study with the recommended settings for this project.`; Results: `Summarise the study results for a non-specialist and tell me the single most important next step.`; Improve: `Apply the highest-severity recommendation from review_eh_study, one confirmation at a time, then review again.` |

### 5.8 Text rules

Card body text comes only from: catalogue `fields` (via `Term`/`useGuideField` with an English fallback identical to the catalogue string — the same "offline fallback" discipline as the Bus card), review `title`/`recommendation`/`effect`, template `study_notes`, and the formatters in §5.5. No `stage` ids, `‱`, or engine names appear except inside the "Why" evidence list (raw keys are acceptable there, labelled "technical evidence").

### 5.9 P24 files

| File | Change |
|---|---|
| `BE/services/adequacy/eh_review.py`, `BE/services/chat_tools.py`, `BE/routers/results.py`, `BE/services/chat_tools_schema.py`, `BE/tests/fixtures/route_inventory_phase0.txt` | §4.1 |
| `BE/services/adequacy/eh_readiness.py` | §4.2 |
| `BE/data/guides/eh_fmea_guide.json` | §4.3 |
| `FE/api/simulation.ts` | `getEhReview`, `EhReview`, `EhReadiness` fields |
| `FE/pages/hubDesign/**` | §5.1 |
| `FE/hooks/useCreateFromTemplate.ts`, `FE/hooks/useStartFmeaSweep.ts` | lifted hooks; `NewProjectWizard.tsx`, `FmeaTab.tsx` consume them |
| `FE/pages/results/EhReferenceDesignPanel.tsx` | export `notEstablishedNotes`, `fmeaTopRows`, `EMPTY_PACK_FORM`; add `eh_review` key to `invalidateAll` |

### 5.10 P24 tests

| File | Asserts |
|---|---|
| `FE/pages/hubDesign/HubDesignPanel.flow.test.tsx` | the five `flowState`s render the right rail states and cards (mock `getEhStudy`, `getEhReview`, `getEhReadiness`, `getEhTemplate`); initial step per §5.4; auto-advance running→done; manual rail click suppresses it; `stale` banner from `source`. |
| `…/ResultsCard.test.tsx` | the five headlines of §5.5 from constructed review/report pairs; cost formatting; three risks; gaps list; Open report calls `setSlidePanel('results')` + `requestResultsTab('adequacy')`. |
| `…/ImproveCard.test.tsx` | only high/medium findings; Why toggles evidence; Do → `delegate` with the exact §5.7 text incl. `JSON.stringify(args)`; Ask → `ask`; FMEA button calls the lifted hook; open-fmea requests the tab. |
| `…/SiteCard.test.tsx` | four rows from a readiness fixture; each fix button's text; site type select updates the store and refetches readiness with the new archetype. |
| `…/GoalCard.test.tsx` | defaults from template then `pack_defaults`; Run posts `buildEhStudyBody` output (deep-equal to the Expert panel's body for the same inputs — computed by calling the same function); VOLL ≤ 0 disables Run. |
| `…/StartCard.test.tsx` | three templates, provenance line, create calls the lifted hook; "Use my network" → site. |
| `…/Term.test.tsx` | every `k` used by the cards resolves in the catalogue: the test imports the JSON directly (`import guide from '../../../../backend/data/guides/eh_fmea_guide.json'` — Vite/vitest resolve JSON outside the root; add `resolveJsonModule` to the test tsconfig if `tsc` complains) with a comment naming `smoke/check_bundle.ROOTED` as the packaging pin, so a missing key fails FE tests too. |
| `FE/hooks/useCreateFromTemplate.test.tsx`, `useStartFmeaSweep.test.tsx` | behaviour identical to the pre-lift code (registry error refuses; navigation + `noteNewProjectCreated('template')`). |
| `FE/pages/results/FmeaTab.test.tsx`, `NewProjectWizard.templates.test.tsx` | still green after the lift (no edits beyond imports). |
| Backend | §4.1, §4.2, §4.3 test lists. |

---

## 6. P25 — The assistant does the steps

### 6.1 `chatStore.sendRequest`

State (`FE/store/chatStore.ts`):
```ts
interface QueuedRequest { id: string; text: string; source?: string; queuedAt: number }
requestQueue: QueuedRequest[]           // FIFO
sendRequest(text: string, opts?: { source?: string }): string | null  // returns id or null when deduped
takeNextRequest(): QueuedRequest | null // ChatPanel only
```
Semantics:
- `sendRequest` trims; empty → null. **Dedupe (queue only — `ChatMessage` carries no timestamp):** the store keeps `lastRequest: { text, at } | null`, set on every accepted `sendRequest` and on every take; if `text === lastRequest.text && now - lastRequest.at < 2000` → return null. Otherwise push `{ id: crypto.randomUUID?.() ?? String(Date.now()+Math.random()), … }`, call `useUIStore.getState().setAssistantDockOpen(true)`, return id.
- `resetForProjectSwitch` clears `requestQueue`.
- Not persisted.

ChatPanel (`ChatPanel.tsx`, next to `onSend` at ~2149): an effect on `[requestQueue.length, streaming, pending, pendingSendText]`:
```
if (requestQueue.length === 0 || streaming || pending != null || pendingSendText != null) return
const req = takeNextRequest(); if (!req) return
dispatchSend(req.text, [])   // the same path typed messages use
```
Rules:
- **Queue while streaming**: nothing is dispatched until `streaming` is false; the effect re-runs on the flag.
- **Never while a confirmation card is pending** (`pending != null`): the queue waits; the card is answered by the user as today.
- **Attachment modal bypass:** card requests are dispatched with `attachIds = []`, so the first-send attachment modal (`onSend`, `chat:firstSendAck`) is not involved and currently attached files are **not** sent with a card request (documented in the button titles: "Sends this request to the assistant — your attached files are not included."). The user's `attachedFileIds` are left untouched.
- **Never bypass write confirmation cards**: `dispatchSend` is unchanged; cards arrive as frames as today. A test pins that no code path in `sendRequest`/the effect calls `confirmApi`/`/api/chat/confirm`.
- One dispatch per request id (the effect takes from the queue, so a re-render cannot double-send; pinned by test).
- `input` (the composer draft) is not touched.

### 6.2 `ui_mode` and `guided_step` in the chat request

Frontend `buildUiContext()` (`FE/utils/uiContext.ts:45`) adds, **only when `uiMode === 'guided'`**:
```ts
ui_mode: 'guided',
guided_step: activeSlidePanel === 'hubDesign' ? hubDesignStore.step : undefined
```
and reports `panel: 'hubDesign'` through the existing `panel` field. `UiContext` type gains both (optional). In Expert nothing is added: the cold-start `buildUiContext() === null` pin (`utils/uiContext.test.ts:100–105`) still holds and Expert request bodies are byte-identical to today.

Backend `_format_ui_context` (`BE/services/chat_service.py:2271`): allow-list `ui_mode ∈ {'guided','expert'}` and `guided_step ∈ {'start','site','goal','results','improve'}`; other values dropped (fail closed, as today). The ui block **omits** `ui_mode` and `guided_step` lines when `ui_mode == 'expert'` (byte-identical block to today). When `ui_mode == 'guided'` the block gains `mode: guided` and, if present, `guided step: <step>`, and the function appends `_GUIDED_MODE_ADDENDUM`:

```
Guided mode is on. Rules for this turn: answer in plain language a non-specialist can follow; keep it short (about 120 words unless the user asks for detail); gloss any technical term in a few words the first time; the user is on the "<Step>" card of the hub design — refer to it by name and say what to do there; when the user delegates a step, do it with the tools rather than explaining how, and before any write or run say in one sentence what will change and that a confirmation card follows; never apply a change the user has not asked for; questions are welcome at any time.
```
(One paragraph; `<Step>` substituted or the clause dropped when no step is known.) It lives in the **per-turn user content**, like the ui block, not in the system prompt — the system prompt (`_EH_GUIDE` etc.) is untouched, so prompt caching and Expert behaviour are unchanged. The addendum is persisted with the turn exactly as the ui block is.

`input_mode` stays as is.

### 6.3 `suggest_eh_setup` (read tool)

**Module:** `BE/services/adequacy/eh_setup.py` — `suggest_eh_setup(n: pypsa.Network) -> dict`. Pure, deterministic, no solve, no mutation (asserted by test: `n.buses`/`n.links` equal before/after).

**Input schema (chat):** `_t("suggest_eh_setup", "<desc> … Safety: read.", {"archetype": {"type": "string", "enum": ["strong_grid","weak_flexible","off_grid"]}}, [])` — `archetype` optional, only used to word reasons. `TOOL_ROUTES["suggest_eh_setup"] = _SERVICE_CALL`. Dispatcher `chat_tools.suggest_eh_setup(archetype=None)` reads `PyPSAService.get_network()`.

**Output schema:**
```json
{"status":"ok"|"no_network",
 "network":{"buses":int,"links":int,"loads":int,"generators":int},
 "already":{"import_links":[...],"poc_buses":[...],"critical_buses":[...]},
 "suggestions":[{"kind":"import_link"|"poc_bus"|"critical_bus"|"outage_data",
                 "component_class":"Link"|"Bus"|"Generator"|"StorageUnit",
                 "name":str,"current":{...},"proposed":{...}|null,
                 "reason":str,"confidence":"high"|"medium"|"low",
                 "action":{"tool":str,"args":{...},"effect":str}|null}],
 "actions":[{"tool":str,"args":{...},"effect":str}],
 "notes":[str]}
```
`actions` is the deduplicated, ready-to-run list: when ≥ 2 suggestions share `component_class` and identical `proposed`, they collapse into one `bulk_update_components {component_class, names, updates}`; otherwise `update_component {component_class, name, attrs}`. `outage_data` suggestions never have an action (values are the user's to give) and go to `notes` as one line each.

**Heuristics** (all sets are names; `peak(load)` = max of `loads_t.p_set[name]` if present else `p_set`):

| kind | rule | confidence |
|---|---|---|
| `import_link` | For each Link `l` with `eh_role` not `grid_import`: `supply(b)` = Σ `p_nom` of Generators at bus `b` with `marginal_cost ≤ median(marginal_cost of all generators)` (or any generator when < 3 generators); `demand_side(b)` = true if `b` reaches ≥ 1 Load through lines/links/transformers without crossing `l` (BFS on the undirected graph minus `l`). Candidate when one endpoint has `supply ≥ 0.8 × Σ peak(all loads)` and the other endpoint is `demand_side`. Score = supply ratio; +1 if the supply-side bus, a generator there or the link carrier name matches `/(grid|import|utility|mainland|tie|poc)/i`. Take the top-scored links whose score ≥ 0.9 × best. `proposed = {"eh_role": "grid_import"}`. | high if name-matched, else medium |
| `poc_bus` | The supply-side endpoint of each proposed/existing import link, not already `eh_poc`. If no import link: the bus with the largest `supply(b)` when it also holds no Load. `proposed = {"eh_poc": true}`. | high with an import link, low otherwise |
| `critical_bus` | Buses with a Load whose bus name, load name or load carrier matches `/(crit|hospital|clinic|it[_-]|data|process|essential|emerg)/i` → high. If none match: the bus with the largest `peak(load)` that is not a `poc_bus` → medium. Skip buses already `eh_critical`. `proposed = {"eh_critical": true}`. | as stated |
| `outage_data` | Generators with `_gen_category(carrier) == 'conventional'` (`services/profile_shapes.py:217` — the backend's only carrier classifier; do not hardcode a list) and every import Link (existing or proposed), where `resolve_outage_params` gives non-finite `outage_rate_value` or `mttr_hours`. `proposed = null`, reason names both fields. | — |

**Validation by construction:** every emitted action passes `tests/test_energy_hub_review.py::_validate_action` (lift that helper into `BE/tests/_tool_actions.py` so both test files share it) and its `attrs`/`updates` keys ⊆ `EH_CUSTOM_COLUMNS[class]` with the declared type.

**Prompt:** `_EH_GUIDE_CHAINING` gains one sentence: "For a network that is not tagged yet, call suggest_eh_setup, present each suggestion with its reason, and apply only the ones the user picks (update_component / bulk_update_components will ask for confirmation)." — this is a system-prompt change visible to Expert too; it is the one intended exception (the tool exists in both modes) and `test_prompt_carries_the_eh_workflow_both_modes` is extended accordingly.

### 6.4 P25 files

| File | Change |
|---|---|
| `FE/store/chatStore.ts` | queue + `sendRequest` + `takeNextRequest`; reset |
| `FE/components/ChatPanel.tsx` | queue-consuming effect |
| `FE/pages/hubDesign/delegate.ts` | `delegate` → `sendRequest`; Site-fix-grid text gains the `suggest_eh_setup` clause |
| `FE/utils/uiContext.ts` | `ui_mode`, `guided_step` |
| `BE/services/chat_service.py` | allow-list, `_GUIDED_MODE_ADDENDUM`, `_EH_GUIDE_CHAINING` sentence |
| `BE/services/adequacy/eh_setup.py` (new), `BE/services/chat_tools.py`, `BE/services/chat_tools_schema.py` | tool |
| `BE/tests/_tool_actions.py` | shared `_validate_action` |
| `BE/smoke/stub_openai_endpoint.py` | §6.6 scripted tool-call branch |

### 6.6 Stub scripting (smoke prerequisite — a deliverable, not a "verify")

`BE/smoke/stub_openai_endpoint.py` scripts one prompt today (`"Save the current network"` → `save_project`, lines 48–51 and 121–133); every other prompt gets `"Saved."`. `_last_user_text` (line 68) returns the last user text, which in Guided carries the ui block and the addendum. Add:
- **Branch 2** — unanchored regex over the last user text: `Run the tool (\w+) with exactly these arguments: (\{.*?\})` (DOTALL). Emits a tool call with that name and those parsed arguments, id `call_stub_2`. After the following `role: tool` message it answers with one plain sentence (`"Done — <tool> applied."`) and stops.
- The module docstring's "what it proves" list gains this branch. No branch for the Site fixes in v1 (P26 exercises Improve → do).
- The stub already records request payloads; the smoke asserts that the recorded request's **last user text contains `Guided mode is on`** in Guided and **does not** in Expert.
- Unit test `BE/tests/test_stub_openai_endpoint.py` (fast, in-process): branch 2 parses the §5.7 delegate text into the tool call; text without the marker falls through to the default reply.

### 6.5 P25 tests

| File | Asserts |
|---|---|
| `FE/store/chatStore.sendRequest.test.ts` | returns id; opens the dock; dedupe within 2 s (identical text → null; different text → queued; same text after 2.1 s → queued); `resetForProjectSwitch` clears. |
| `FE/components/ChatPanel.sendRequest.test.tsx` | (mock `createChatStream`) one `sendRequest` → exactly one `createChatStream` call with `message === text`, `attachment_file_ids` undefined, `ui_context.ui_mode` present; `streaming: true` → not dispatched until `setStreaming(false)`; `pending` card set → waits; two requests → two calls in order; `chat:firstSendAck` absent + attached files present → no modal, files not sent; re-render storm (force 5 re-renders) → still one call; the composer draft is untouched. |
| `FE/utils/uiContext.uiMode.test.ts` | expert → no `ui_mode` key and cold start still returns `null`; guided → `ui_mode:'guided'`; guided + hubDesign open → `panel:'hubDesign'`, `guided_step` from the store. |
| `FE/pages/hubDesign/delegate.test.ts` | `delegate` calls `sendRequest` with `source:'hub-design'`; `ask` seeds without sending. |
| `BE/tests/test_guided_mode_prompt.py` | `_format_ui_context({'ui_mode':'expert','panel':'results'})` == `_format_ui_context({'panel':'results'})` (byte-equal); guided → block contains `mode: guided` and the addendum; `guided_step:'site'` → `"Site" card` wording; unknown `ui_mode`/`guided_step` dropped; `_build_system_prompt(...)` (`chat_service.py:2631`) output byte-equal to a snapshot taken on the base commit **except** the one `_EH_GUIDE_CHAINING` sentence (assert by removing that sentence and comparing); a `run_turn` with a fake provider records the addendum in the persisted user turn only in guided mode. |
| `BE/tests/test_eh_setup_suggest.py` | for each of the three template builders: strip tags (`eh_role=''`, `eh_poc=False`, `eh_critical=False`) → `suggestions` recover `grid_import`/`grid`/`it_bus`, `grid_import`/`grid`/`hub`, `subsea_tie`/`mainland`/`hospital` respectively (compare against the builder's own tagging, read from the untouched network — no literals); every action validates (`_validate_action`) and its attrs ⊆ `EH_CUSTOM_COLUMNS`; bulk collapse when two critical buses are proposed (construct a 2-load network); network untouched; `no_network` when empty; `outage_data` lists the datacenter gensets after `outage_rate_value` is set to NaN; the tool is registered (`TOOLS`, `TOOL_ROUTES`, dispatcher — the existing name-alignment tests cover it), tier `read` (`test_every_tool_safety_marker_resolves_to_known_tier` parametrises automatically). |

---

## 7. P26 — Verification

1. **Real-app click-through (Guided), all three templates**, via the Playwright smoke in §8.4 with a fresh browser context each time (first-time user): `/projects` → `localStorage["network-diagram:ui-mode"] === "guided"` (the switch lives in `AppHeader`, which `/projects` does not render; corrected at the P23 gate) → after the workbench opens, `ui-mode-switch` has guided pressed → New project → template → workbench opens, `hub-design-panel` visible, rail at Site → Site rows show the template's grid link, critical bus, strength, outage count → Goal shows the pack default, Run → running state → done → rail at Results, headline text matches §5.5 for the known verdicts (data center: fail; H₂ hub: no target; microgrid: inconclusive — from the parent plan's P20 outcomes; if a verdict differs, record it, do not force it) → Improve lists ≥ 1 finding for the data center → "Let the assistant do this" → the assistant turn shows a **confirmation card** (stub provider script emits the tool call) → decline → "Check risks (FMEA)" → FMEA tab opens with rows. Switch to Expert → all sidebar sections and thirteen tabs back → reload → Expert persists → switch to Guided → reload → Guided persists.
2. Fold in any new finding as a test-first fix in the phase that owns it.
3. Independent QA gate (§8.5) and full suites (§8.1–8.3).

---

## 8. Integration gate (every phase)

The user's rule: after every phase, end-to-end QA across the whole system; proceed only when it integrates without system-level bugs. **Do not start the next phase until every box below is ticked in the plan.**

### 8.1 Baseline (once, before P22.9)

On the base commit `2b78c83`, in a clean worktree:
- `cd /home/user/pypsa-eur/pypsa-gui/backend && PYTHONPATH=/home/user/pypsa-eur:/home/user/pypsa-eur/pypsa-gui/backend /tmp/claude-0/venv/bin/python -m pytest tests/ -m "not slow" -p no:cacheprovider -W ignore -q -o addopts="" 2>&1 | tee /tmp/claude-0/-home-user-pypsa-eur/93e65137-63f8-5e19-b114-b55788c10af8/scratchpad/baseline-backend.txt` (~40 min; `pytest.ini` has `testpaths = tests`, so the cwd is `BE/`).
- `cd /home/user/pypsa-eur/pypsa-gui/frontend && npx vitest run 2>&1 | tee …/baseline-frontend.txt` and `npx tsc --noEmit -p .`.
- Record the failing test ids (backend node ids, frontend file::name) in `docs/superpowers/qa/2026-09-27-guided-mode-baseline.md` with the summary lines as evidence. A test may be called pre-existing **only** if it is in that file.

### 8.2 Commands per gate

| # | Command (cwd) | Pass |
|---|---|---|
| 1 | full backend: the §8.1 command (`BE/`) | zero failures not in the baseline file; zero errors; no new `slow` marks added to dodge the gate |
| 2 | targeted EH e2e: `… -m pytest tests/test_energy_hub_templates_e2e.py tests/test_energy_hub_review.py tests/test_guides.py tests/test_energy_hub_study_isolation.py tests/test_live_network_untouched.py tests/test_chat_tools_endpoint_map.py tests/test_chat_tools_dispatch.py -p no:cacheprovider -W ignore -q -o addopts=""` (`BE/`). `test_live_network_untouched.py` is created in P22.9-BE; the Baseline run uses the row without it. | all pass (these have no baseline failures; if one appears in the baseline it is fixed in P22.9 first) |
| 3 | `npx tsc --noEmit -p .` (`FE` root `pypsa-gui/frontend`) | zero errors |
| 4 | `npx vitest run` (`pypsa-gui/frontend`) | zero failures not in the baseline file |
| 5 | browser smoke §8.4 for the phase's path | every step's assertion passes; screenshots saved under the scratchpad |
| 6 | independent QA-gate review §8.5 | verdict GO |
| 7 | Expert-unchanged check: `git diff <phase-base> -- 'pypsa-gui/frontend/src/**' \| grep -c "uiMode"` is reviewed by hand: every `uiMode` branch has an `expert` arm that renders the pre-phase markup; the `*.expertUnchanged.*` snapshot tests pass | reviewer signs |

### 8.3 Pass criteria

- Zero new failures vs. baseline in 1 and 4; 2 and 3 fully green.
- No test deleted or skipped to pass; a changed assertion needs a one-line justification in the plan.
- The route inventory fixture was caught up in the Baseline step; in P24-BE it gains **exactly** the new `GET /api/results/eh_review` row and `test_chat_tools_endpoint_map` passes.
- Existing tests edited to keep passing (e.g. the three `getChatHealth` mocks in §2.8) carry a one-line justification in the plan.
- The phase's smoke script exits 0 and the QA reviewer's report lists zero blockers and zero system-level bugs (a bug in another surface found during the gate blocks the gate; it is fixed or explicitly deferred by the product owner in the plan).

### 8.4 Browser smoke

Tooling (verified on this machine): Playwright **1.56.1** at `/opt/node22/lib/node_modules/playwright`, browsers at `/opt/pw-browsers` (`chromium-1194`, `chromium_headless_shell-1194`). No Playwright in `frontend/node_modules` or the Python venv — do not add a dependency. No `google-chrome`/`chromium` on `PATH`: `scripts/shot-authed.mjs` (which spawns `google-chrome` and drives it over CDP with `ws`) is **not** a Playwright precedent; take only its process management (spawn / wait-for-port / cleanup) from it.

**Module loading.** Node's ESM resolver ignores `NODE_PATH`, so `import('playwright')` fails. The script is `.mjs` and loads Playwright by absolute path:
```js
import { createRequire } from 'node:module'
const { chromium } = createRequire(import.meta.url)('/opt/node22/lib/node_modules/playwright')
// or: const { chromium } = await import('/opt/node22/lib/node_modules/playwright/index.mjs')
process.env.PLAYWRIGHT_BROWSERS_PATH ??= '/opt/pw-browsers'
```
Script: `pypsa-gui/frontend/scripts/smoke-guided.mjs` (new in P22.9-FE, extended per phase). Run:
```
cd /home/user/pypsa-eur/pypsa-gui/frontend
PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers node scripts/smoke-guided.mjs --phase P23 [--template eh_datacenter] [--keep]
```
The script:
0. **self-check**: `chromium.launch({ headless: true })`, open `about:blank`, close — so a tooling failure is distinguishable from an app failure (exit code 3 vs 1);
1. starts uvicorn from `BE/` with `PYPSAGUI_LOCAL_MODE=1`, `ANTHROPIC_API_KEY` **unset**, `PYPSAGUI_APP_DATA_DIR` and `PYPSAGUI_PROJECTS_ROOT` under the scratchpad (the isolation the `smoke/qa_e2e.py` docstring requires), port 8000, and Vite on 5173 (`vite.config.ts` proxies `/api` to 8000);
2. for `--phase P22.9` first asserts the send gate with the default Anthropic profile active (`GET /api/chat/health` → `chat_ready:false`, `chat-send` disabled); **then**, for every phase, starts `BE/smoke/stub_openai_endpoint.py`, `PUT`s an OpenAI-wire `auth: none` profile pointing at it and activates it (`POST /api/chat/settings/llm/active`) — the stub is the deterministic model (§6.6 gives it the scripted tool call the P25/P26 paths need);
3. launches a **fresh context** (empty storage) and walks the phase path, asserting on test ids; on failure it saves a screenshot and the console log and exits 1;
4. `--phase P22.9 | P23 | P24 | P25 | P26` selects the path (§2.11, §3, §5, §6, §7); `P26` loops over the three templates;
5. always asserts the P22.9 invariants (whole rows of `GET /api/network/buses` and `/links` equal before/after) whenever it runs a study or sweep, and in P25/P26 asserts the stub's recorded last user text contains `Guided mode is on` in Guided and not in Expert.

### 8.5 Independent QA-gate review

A separate agent (not the implementer; Fable reviews the plan, an Opus-class reviewer runs the gate) receives: the phase's diff, this spec section, the baseline file, the outputs of §8.2 1–5. It re-runs 2–5 itself, reads every new test for vacuity (a test that cannot fail is a finding), checks the Expert-unchanged snapshots, and writes `docs/superpowers/qa/2026-09-27-guided-mode-gate-<phase>.md` with verdict GO / NO-GO, blockers, and the exact failing ids. NO-GO returns to the implementer; the phase is not done until GO.

---

## 9. Risks

| Risk | Mitigation |
|---|---|
| `FIRST_RUN` evaluated at import makes uiStore tests order-dependent | tests use `vi.resetModules()` + dynamic import; a test-only `__resetUiModeForTests()` is not offered (it would hide the race) |
| Hiding sections breaks Sidebar tests that query by label | the tests set `uiMode: 'expert'` in `beforeEach` via `useUIStore.setState`; new Guided tests are separate files |
| `expertOnly` filter changes tab order or ids | ids and order untouched; the filter is the only change (snapshot) |
| Cache sharing between the Expert panel and the cards (same query keys, different options) | options are copied verbatim; a test asserts the `refetchInterval` function is the same export |
| Auto-send from a card surprises the user | the button label says "Let the assistant do this", the title says it sends, the message is visible in the transcript, writes still confirm |
| Prompt-cache breakage | the addendum lives in the user content, not the system prompt; `test_guided_mode_prompt` pins the system prompt |
| The stub model's scripted branch (§6.6) drifts from the §5.7 delegate text | `test_stub_openai_endpoint.py` parses the exact §5.7 string; the delegate text and the regex are changed together |
| Full suite time (~40 min × 5 gates) | run it in the background while the smoke runs; never skip it |

---

## 10. Decisions taken by the spec author

| Question | Decision |
|---|---|
| Which keys define a "first-time user" | `network-diagram:*` minus `theme-schema`, plus `pypsa-guide-seen:*` and `results:active-tab` (§3.2); `chat:*` and `pypsa-gui:map:*` are not counted (they cannot exist without a `network-diagram:*` key). |
| Persist the implicit default? | Yes, on the first-time branch only, so the decision is stable across reloads. |
| Two storage keys or one JSON blob | Two plain-string keys, matching every other preference in the store. |
| "Canvas tool switcher" | The Sidebar `ModeSwitcher` (Select / Connect). `MapModeSwitcher` (blank / satellite / hybrid) is left alone. |
| Command palette in Guided | Unchanged (power-user surface) + one entry. |
| Which PROJECT rows survive in Guided | Save, Recent, Projects home, header card. |
| `hubDesign` full-screen or half | Full-screen (it replaces the canvas for the flow). |
| `ui_mode` transport | Inside `ui_context` (allow-listed), addendum in the per-turn user content, system prompt untouched. |
| G4 "new projects" | Implemented **literally** (review B10, owner's choice): every wizard tab, `Sidebar.newProjectMut` and `ProjectsHomePage.createBlank` call `noteNewProjectCreated`; only an explicit choice blocks it. Chat-created projects (`create_project_from_template`, `save_project_as`) are not switched in v1 — the assistant is already the Guided surface. |
| Stale detection | `review_latest` returns a boolean `stale` (review suggestion 5); the FE never parses the `source` prose. |
| `GET /eh_review` on `running` | 200 with `{status:'running'}` (the FE polls the study record anyway), 204 only for `no_data`. |
| `suggest_eh_setup` input | optional `archetype` only. |
| Where `_validate_action` lives | `BE/tests/_tool_actions.py`, shared by the review and setup tests. |
| Bug 2 fix location | Frontend sentence only; backend recording of the sweep's closing re-solve as a foreground solve is out of scope. |
| Bug 4 fix | Additive `p_set_peak` on Load rows; label "Peak load". |
| Obstacle 3 fix | `prepare` prop + `propertiesEditRequest` + `reveal` ids + a Guide button on the Bus card. |
| Obstacle 9 fix | Disable Send on `chat_ready === false`, fail-open when unknown. |
| Send gating and the stub provider | Resolved (review B6): `chat_ready` is computed from the active profile (`routers/chat.py:158–176`); an `auth: none` stub profile is always ready, so the P22.9 smoke asserts the disabled Send **before** activating the stub profile (§2.11, §8.4 step 2). |
| Bug 3 study path | Corrected by the review (B1): the study leaves the live tables equal; the study test is a guard, the sweep (and the other live-network studies) get the fix. |
| Baseline location | `docs/superpowers/qa/2026-09-27-guided-mode-baseline.md`; the reviewer's 2026-09-27 pre-baseline observation (`tsc` exit 0; vitest 181 files / 2033 tests passed on `8e04e54`) is recorded there as an observation, not as the phase's baseline. |
| Review suggestions (non-binding, 1–9) | **All nine adopted**: 1 foreground-solve note (§2.1 item 4); 2 both keys in `firstRunOrder` (§3.2, §3.9); 3 count via `resolve_outage_params` (§4.2); 4 JSON import path + `ROOTED` comment (§5.10); 5 boolean `stale` (§4.1, §5.4); 6 queue-only dedupe (§6.1); 7 gate row 2 without the new file at Baseline (§8.2); 8 pre-baseline observation (this table, plan Baseline); 9 `reveal` guard for the optional Link step (§2.7 item 5). None rejected. |
