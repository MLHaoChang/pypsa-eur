# Guided mode: deferred items — implementation spec (P27a–P32, stubs P33–P34)

**Date:** 2026-09-28. **Plan:** [`../plans/2026-09-28-guided-mode-deferred.md`](../plans/2026-09-28-guided-mode-deferred.md) (item ids A1…D3 are the plan's). **Parent spec:** [`2026-09-27-guided-mode.md`](2026-09-27-guided-mode.md); its §8 gate applies to every phase here, with the row-2 superset of §0.2. **Reviews applied:** plan review [`../qa/2026-09-28-guided-mode-deferred-plan-review.md`](../qa/2026-09-28-guided-mode-deferred-plan-review.md); spec review [`../qa/2026-09-28-guided-mode-deferred-spec-review.md`](../qa/2026-09-28-guided-mode-deferred-spec-review.md) (12 conditions, §9). **D-8 = (a)** (owner, 2026-09-28): P32 is scheduled directly after P27a and is specified in full in §7.1.

`FE` = `pypsa-gui/frontend/src`, `BE` = `pypsa-gui/backend`. Every anchor was checked in the tree on 2026-09-28 (P26 fixes in progress in `ChatPanel.tsx`, the hub cards, `plainWords.ts`). No code was edited for this spec.

## 0. Conventions for every phase

### 0.1 Rules carried from the parent spec
- Test-first: every red test named below is written and seen failing before its fix; a changed assertion carries a one-line justification in the plan's phase note.
- Additive backend fields only; no new backend module (`smoke/check_bundle.py` is the gate for a new file); no change to `chat_tools_schema.TOOLS`, tool descriptions or the system block in P27a–P32 (`BE/tests/test_guided_mode_prompt.py::test_expert_block_is_byte_equal_to_no_mode` and its siblings stay green).
- Expert unchanged: every `uiMode` branch has an Expert arm rendering the pre-phase markup; the `*.expertUnchanged.*` snapshots (`FE/layout/AppHeader.expertUnchanged.test.tsx`, `Sidebar.expertUnchanged.test.tsx`, `FE/pages/Results.expertUnchanged.test.tsx`, plus P29's new `FmeaTab.expertUnchanged.test.tsx`) pass byte-for-byte against the phase base.
- Wording: sentences, no engine ids on a Guided surface, no "0" for a missing number.
- Every gate row in a phase note states the exact command and cwd (plan C13).

### 0.2 Integration gate (every phase)

| # | Command (cwd) | Pass |
|---|---|---|
| 1 | `PYTHONPATH=/home/user/pypsa-eur:/home/user/pypsa-eur/pypsa-gui/backend /tmp/claude-0/venv/bin/python -m pytest tests/ -m "not slow" -p no:cacheprovider -W ignore -q -o addopts=""` (`BE/`) | zero failures not in `docs/superpowers/qa/2026-09-27-guided-mode-baseline.md` |
| 2 | `… -m pytest tests/test_energy_hub_templates_e2e.py tests/test_energy_hub_review.py tests/test_guides.py tests/test_energy_hub_study_isolation.py tests/test_live_network_untouched.py tests/test_chat_tools_endpoint_map.py tests/test_chat_tools_dispatch.py tests/test_eh_review_route.py tests/test_energy_hub_tagging.py tests/test_golden_coverage.py tests/test_results_range.py tests/test_chat_tools_schema_panels.py tests/test_guided_write_confirmation.py tests/test_guided_mode_prompt.py` (the 14-file set the P25 re-gate and P26 ran — `scratchpad/qa25r/row2.log`; P26 gate "14 files: 613 passed") **plus the phase's additions** (each phase §x.5) `-p no:cacheprovider -W ignore -q -o addopts=""` (`BE/`) | all pass |
| 3 | `npx tsc --noEmit -p .` (`pypsa-gui/frontend`) | zero errors |
| 4 | `npx vitest run` (`pypsa-gui/frontend`) | zero failures not in the baseline |
| 4s | stress: `for i in $(seq 10); do npx vitest run <touched suites>; done` when the phase changes polling, a store or chat | 10 / 10 green |
| 5 | `PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers node scripts/smoke-guided.mjs --phase <phase>` (`pypsa-gui/frontend`) | exit 0; the phase's assertions in §x.5 |
| 6 | independent QA-gate review → `docs/superpowers/qa/2026-09-28-guided-mode-gate-<phase>.md` | GO |
| 7 | `git diff <phase-base> -- 'pypsa-gui/frontend/src/**' \| grep -c uiMode` reviewed by hand; snapshots pass | reviewer signs |

`smoke-guided.mjs`: `PHASES` (`:96`) gains `P27a`, `P27b`, `P28`, `P29`, `P30`, `P31`, `P32`; the dispatcher (`:1885-1890`) maps each to a function that **calls its base phase's function first**, then runs the extension steps. Each phase names its base in §x.5.

---

## 1. P27a — Backend data integrity (A1, A8)

### 1.1 A1 — the topology / capacity restore runs under the runner's lock; live-network studies refuse edits

**Anchors.** `BE/services/adequacy/sweep.py:87-125` (`preserve_bus_topology`), `:188-196` (`freeze_capacities` `_undo`), `:279-286` (`_solve_once(cfg, n, lock, …)`), `:400-401` (call site). Other call sites: `frontier.py:211`, `coupling_loop_runner.py:625`, `margin_loop_runner.py:962`, `dtc.py:396` (a copy; no lock). Lock: `PyPSAService.get_lock()` → `threading.RLock` (`pypsa_service.py:632`), captured at request time by every runner (`fmea_sweep_runner.py:56`, `coupling_loop_runner.py:285`, `margin_loop_runner.py:208`, `frontier_loop_runner.py:72`, `eh_study_runner.py:296`). Study keys: `project_context.py:256` `STUDY_KEYS`, `:325` `running_study_key`. Refusal shape: `routers/projects.py:1515-1541` `_study_in_flight_detail(state, doing)`. Middleware: `main.py:628-800` (`is_write` `:642`, solver gate `:769-800`, `_SOLVER_BLOCKING_PREFIXES` `:267`, exemptions `:276,285`). Edit handlers: `services/network_crud.py:204` `_create_component`, `:375` `_update_component`, `:439` `_delete_component` (imported by `routers/network.py:206-212`; the chat tools reach them through `_route`, `chat_tools.py:2115-2147`, never through the middleware). Undo: already refused over all `STUDY_KEYS`, `services/network_undo.py:105` `PyPSAService.refuse_if_study_running("undo")` — but with a **string** detail (`study_swap_refusal`, `project_context.py:343`), not the `study_in_flight` dict (`test_tool_error_kind_manifest.py:80`: only `routers/projects.py` builds the dict). Request context: bound at `main.py:733` (`bind_request_context`) before the solver gate, so `PyPSAService.get_solver_state()` resolves the request's project there (as `projects.py:2377` does); the chat worker copies contextvars (`chat_service.py:4589-4592`), so a check inside `network_crud` sees the acting ctx. Chat error path: a tool's `HTTPException` with a dict detail → `tool_error{error_kind, message}` (`chat_service.py:4619-4645`).

**Contract.**

| Item | Rule |
|---|---|
| Signatures | `preserve_bus_topology(n, lock=None)`, `freeze_capacities(n, lock=None)`. When `lock` is given, the `finally` write-back (all five tables, the `SubNetwork` removal, and each `_undo`) runs inside `with lock:`. `lock=None` keeps today's unlocked behaviour (copies only). |
| Call sites | `sweep.py:400-401` → `preserve_bus_topology(network, lock)`, `freeze_capacities(network, lock)`; `frontier.py:211`, `coupling_loop_runner.py:625`, `margin_loop_runner.py:962` pass their captured `lock`; `dtc.py:396` passes nothing (copy). Never `PyPSAService.get_lock()` inside a worker. |
| Live-network keys | `LIVE_NETWORK_STUDIES = frozenset({"fmea_sweep", "frontier", "coupling_loop", "margin_loop"})` in `services/project_context.py` beside `STUDY_KEYS`. Evidence for the exclusions is a comment: `mc` snapshots under the lock and never mutates (`mc_loop_runner.py:66`); `eh_study` solves `network.copy()` (`eh_study.py:445,971`). |
| Detail helper | `_study_in_flight_detail` moves from `routers/projects.py:1515` to `services/study_state.py` as `study_in_flight_detail(state, doing, *, keys=STUDY_KEYS)`; `projects.py` re-imports it (its two callers `:1547`, `:2377` keep the default `keys`). Return shape unchanged: `{"error_kind": "study_in_flight", "study": <key>, "message": <sentence>}`. |
| Edit sentence | `doing="edit the network"` → message `"Cannot edit the network while <label> is running — it re-solves the in-memory network between its own iterates (…). Wait for it to finish, or abort it, and retry."` (the existing template, `:1535-1540`). |
| HTTP chokepoint | in `main.py`'s solver-in-flight branch (`:769-800`), after the `_solver_in_flight()` check, under the same `is_write` and exemption conditions but **only for the prefixes `/api/network/` and `/api/io/`** (a separate tuple `_STUDY_EDIT_PREFIXES = ("/api/network/", "/api/io/")`; **never `/api/projects/`**, which `_SOLVER_BLOCKING_PREFIXES` `:267` also covers): `detail = study_in_flight_detail(PyPSAService.get_solver_state(), "edit the network", keys=LIVE_NETWORK_STUDIES)` (no `ctx` variable exists in the middleware; the request ctx is already bound at `:733`); if set → `JSONResponse(409, {"detail": detail, "code": "study_in_flight"})`. `/api/projects/*` writes keep their endpoint-level guards unchanged, so `POST /api/projects/<name>` still says "Cannot save the project …" (`projects.py:1547`) and rename / delete / layout / snapshots of other projects are not refused with a sentence about editing the network. Over HTTP, `/api/network/undo` now meets this dict first (the prefix covers it) while the chat path's `undo_last` keeps the swap string — acceptable, stated. |
| Chat chokepoint | first statement of `_create_component`, `_update_component`, `_delete_component` (`network_crud.py:204,375,439`): `_refuse_edit_during_live_study()` → raises `HTTPException(409, detail)` with the same dict. Covers `create_component`, `update_component`, `bulk_update_components`, `delete_component`, `cascade_delete_bus`, `batch_delete_components` and every router that calls these three. |
| Not covered on purpose | profiles / time-axis / cluster / vintage HTTP routes are covered by the middleware prefix `/api/network/`; the chat tools for those (`import_*`, `cluster_network`, `delete_timeseries`, …) are destructive-tier and already go through `refuse_if_study_running` where they swap; a P27a test enumerates the chat tools that reach a network write and asserts each is refused during a sweep (see tests). |
| Chat-test harness | the chat-tool red tests follow `BE/tests/test_chat_tools_dispatch.py:83-216` exactly: `install_network(n, name=None)` plus the acting-user fixture, the fake study record installed **on the ctx the chat tool resolves** (assert `PyPSAService.get_solver_state()` shows it from inside the tool before asserting the refusal), and the bus asserted unchanged through that same ctx. Reason: the spec-review probe showed the `client` fixture and a bare `chat_tools.*` call can resolve different contexts (404 / "Nothing to undo"). |
| FE toast | `FE/api/client.ts` `QUIET_TOAST_CODES` gains `"study_in_flight"` only if the smoke shows a double toast; otherwise the existing one toast with `detail.message`. |

**Red tests** (`BE/tests/test_live_network_untouched.py` unless noted).

| Test | Red today because |
|---|---|
| `test_preserve_bus_topology_restores_under_the_passed_lock` — a `threading.RLock` subclass that records `__enter__`/`__exit__`; the write-back must run between them; `lock=None` records nothing | no `lock` parameter |
| `test_freeze_capacities_undo_runs_under_the_passed_lock` — same spy on the `_undo` path | same |
| `test_edit_during_a_sweep_is_refused_over_http` — install a sweep record with `record_is_running` true (as `test_adequacy_study_swap_guard.py` does), `PUT /api/network/buses/<name>` → 409, `detail.error_kind == "study_in_flight"`, `detail.study == "fmea_sweep"`; the bus is unchanged | no guard |
| `test_edit_during_a_sweep_is_refused_from_the_chat_tool` — `chat_tools.update_component(...)` raises `HTTPException` 409 with the same detail | no guard |
| `test_every_chat_network_write_is_refused_during_a_sweep` — parametrised over `create_component`, `update_component`, `bulk_update_components`, `delete_component`, `cascade_delete_bus`, `batch_delete_components` (409 **and** `detail.error_kind == "study_in_flight"`) and `undo_last` (**409 only**; its detail is the swap sentence string) | undo already refused, with the swap sentence, over all `STUDY_KEYS`; the six CRUD tools red |
| `test_save_during_a_sweep_still_gets_the_save_sentence` — `POST /api/projects/<name>` with a live `fmea_sweep` record → 409 whose `detail.message` starts with `Cannot save the project` (not `Cannot edit the network`) | green today; kills the mutant that widens the middleware prefixes to `/api/projects/` (`test_save_activate_during_study.py::_assert_study_refusal` checks only the label and "abort", so it would not) |
| `test_edit_during_an_eh_study_is_allowed` — an `eh_study` running record, `PUT /api/network/buses/<name>` → 200 | green today; pins the key scope (kills the "all `STUDY_KEYS`" mutant) |
| `test_edit_during_an_mc_study_is_allowed` | same |
| `test_edit_after_a_sweep_is_kept` — run a real sweep on the template (as `:92`), then edit `control` of one bus, assert it persists and `dispatch` reads as before | green today; guards against a restore that runs late |
| existing `:92,104,201,225,243,276` invariant tests | must stay green with the lock in place |
| `BE/tests/test_chat_tool_dispatch_loop_seam.py::test_a_refused_edit_reaches_the_model_as_study_in_flight` — fake dispatcher raises the dict-detail 409; frame `tool_error.error_kind == "study_in_flight"` and `message` is the sentence | green today (pins the surfacing path, kills a mutant that stringifies the dict) |

**Mutation targets.** `with lock:` removed → spy tests; `LIVE_NETWORK_STUDIES` → `STUDY_KEYS` → EH/MC allowed tests; middleware check dropped → HTTP test; `_STUDY_EDIT_PREFIXES` widened to `/api/projects/` → the save-sentence test; `network_crud` check dropped → chat test; `dtc.py` passing the lock → `test_dtc_*` copy tests (the copy has no lock: passing one is a signature error caught by the unit test that calls `preserve_bus_topology(copy)` with no lock).

### 1.2 A8 — the creating tools emit `project_rebound`

**Anchors.** `chat_service.py:125-131` `PROJECT_REBINDING_TOOLS`; `:3331-3345` emission (`new_bound != turn_project_holder[0]`); template route binds `routers/projects.py:1342` and moves the pointer `:1351` (declares `session`, `:1264`; `_route` injects it, `chat_tools.py:2129-2130`); bundle route binds `:1059`, pointer `:1068` / `:1139` (chat tool `chat_tools.py:2354-2371`); `save_project` binds an unbound draft in `_save_context` (`:1929`) and moves the pointer when `was_unbound` (`:1475,1500-1502`). FE handler `FE/components/ChatPanel.tsx:2322-2345` already covers `from: null`.

**Contract.** `PROJECT_REBINDING_TOOLS` = the five today + `create_project_from_template`, `import_project_bundle`, `save_project`. Emission rule unchanged (only on an actual move), so Save-a-Copy (`rebind=False`, binding unchanged) and a save of the already-bound project emit nothing. Frame shape unchanged: `{"from": <old|null>, "to": <new>, "via_tool": <name>}`.

**Red tests** (`BE/tests/test_chat_tool_dispatch_loop_seam.py`, modelled on `:150-190` — `_tu`, `_drive`, `holder`).

| Test | Red today because |
|---|---|
| `test_create_project_from_template_announces_the_rebind` — fake dispatcher moves `loaded_project` `"the-old-one" → "probe-a8"`; frame `{"from":"the-old-one","to":"probe-a8","via_tool":"create_project_from_template"}`; holder follows | not in the set |
| `test_import_project_bundle_announces_the_rebind` | same |
| `test_save_project_of_an_unbound_draft_announces_the_rebind` — holder `[None]` → `"draft-1"`, frame `from: None` | same |
| `test_a_second_tool_in_the_same_turn_still_dispatches_after_a_template_create` — `[_tu("t1", name="create_project_from_template"), _tu("t2")]`, driven with the **real** closure `switched=lambda: PyPSAService.get_active_context().loaded_project != holder[0]` (never `_drive`'s default `lambda: False`, `:47-48`, which makes the case green) and a fake dispatcher that moves `loaded_project` **during** `t1`; after the fix both dispatch, `outcome.switched_mid_turn is False`, no `project_switched_mid_turn` frame | today `t2` is refused with `project_switched_mid_turn` (plan-review probe `scratchpad/qadef/test_probe_a8.py`) |
| `test_save_a_copy_emits_no_rebind` — binding unchanged → no frame | green (pins the "only on a move" rule) |

**Mutation targets.** each name removed from the set → its test; emission condition `!=` → `is not` on equal strings → no test (equivalent); frame emitted unconditionally → `test_save_a_copy_emits_no_rebind`.

### 1.3 Stub scripting (deliverable)
`BE/smoke/stub_openai_endpoint.py` branch 7: last user text `"Create a project from the <template id> template called <name>"` → **two `tool_use` blocks in one response**: `create_project_from_template {"template_id": …, "new_name": …}` (`call_stub_7a`) then `list_components {}` (`call_stub_7b`) — the second call is what shows the same-turn dispatch works — and only after both results the closing sentence of branch 2. Pinned by `BE/tests/test_stub_openai_endpoint.py`: the exact regex and **both** calls, in that order.

### 1.4 Files
`sweep.py`, `frontier.py`, `coupling_loop_runner.py`, `margin_loop_runner.py`, `dtc.py`, `services/project_context.py`, `services/study_state.py`, `routers/projects.py` (import only), `main.py`, `services/network_crud.py`, `services/chat_service.py` (frozenset), `smoke/stub_openai_endpoint.py`, tests above, `tests/test_stub_openai_endpoint.py`.

### 1.5 Gate
- Row 2 additions: `tests/test_chat*.py`, `tests/test_chat_tool_dispatch_loop_seam.py`, `tests/test_save_guards_seam.py`, `tests/test_adequacy_study_swap_guard.py`, `tests/test_adequacy_swap_guard_callsites.py`, `tests/test_stub_openai_endpoint.py`.
- Smoke `--phase P27a` = **P22.9** (all its steps) + (a) start a B/C sweep from the FMEA tab, `PUT /api/network/buses/<bus>` via `api()` while `fmea_sweep.status === 'running'` → 409 with `detail.error_kind === 'study_in_flight'`, then in the browser edit the same bus from the Properties panel → exactly one toast whose text contains "is running", the bus row unchanged; wait for the sweep; the same edit succeeds; (b) **P25** stub branch 7 from the dock (Expert context, since P27a is backend only): the transcript shows `Active project: <name>` (`ChatPanel.tsx:2340` toast), `GET /api/network/meta.loaded_project === <name>`, one **manual save** (Ctrl+S, `POST /api/projects/<name>`) → 200, no 409 in the console; the stub's second scripted tool in the same response (`call_stub_7b` `list_components`) returns a `tool_result`, not `project_switched_mid_turn`.
- Row 4s not needed (no FE change beyond none). Row 7: no FE diff.

### 1.6 Risks
| Risk | Mitigation |
|---|---|
| The lock is held while a study's `finally` runs during a foreground request → the request waits ≤ one write-back (ms) | `RLock`, write-back is a few `loc[]` assignments; measured in the smoke log |
| A study exception path skips the lock | the `finally` wraps `with lock:` inside a `try/except` that logs, as today (`sweep.py:119-125`) |
| Expert users hit the new 409 mid-sweep | one plain sentence; the header's study indicator already shows the sweep; abort is one click |

---

## 2. P27b — Frontend integrity and coverage (A2, A5, A6, A1-FE)

### 2.1 A2 — bound-project mismatch after a restart

**Anchors.** `GET /api/network/meta` returns `loaded_project` (`BE/services/network_crud.py:124-141`; route `routers/network.py:601-604`) — the FE type omits it (`FE/api/types.ts:178`). Recovery effect `FE/App.tsx:446-481` (keys on `bus_count === 0`). Autosave `FE/layout/Sidebar.tsx:730` `guardProjectMutation`, `:751-779`, identity 409 handling `:846-861`. Request interceptor `FE/api/client.ts:130-136`. Greeting reads the same `meta` key (`ChatLaunchGreeting.tsx:96-101`, `nk(project,'meta')`).

**Contract.**

| Item | Rule |
|---|---|
| Type | `NetworkMeta` gains `loaded_project: string \| null` (no backend change). |
| Store | `uiStore`: `projectMismatch: { tab: string; backend: string } \| null` (in-memory), `setProjectMismatch(v)`. |
| Detection | the recovery effect (and the same effect on every `meta` refetch) sets `projectMismatch` only when **all** of: `useUIStore.getState().projectSwitchInProgress === false` (`uiStore.ts:408`; set by the four switch entry points, e.g. `Sidebar.tsx:954/1031`), the sample is settled (`isFetching` false), **and** `meta.loaded_project != null && meta.loaded_project !== currentProject` has held on **two consecutive settled samples** (the effect keeps the previous sample in a ref; one disagreeing sample is ignored). It clears the mismatch on any agreeing sample or when `loaded_project` is null. Reason: the open path (`Sidebar.tsx:984` `await projectsApi.load(name)` → `setCurrentProject` later), `switchToProject` (`projectActions.ts:407` activate → `:433`) and the `project_rebound` handler all have a window where the backend has moved and `currentProject` has not; a `meta` refetch there (Sidebar `refetchInterval: 30_000`, greeting `staleTime: 5_000`) must not raise the banner or block the switch's own follow-up writes. The `bus_count === 0` reload path is unchanged and runs only when there is no mismatch. |
| Write block (axios) | `client.interceptors.request` (`client.ts:130`): while `useUIStore.getState().projectMismatch` is set and `method ∈ {post,put,delete,patch}` and the URL does not match `MISMATCH_ALLOWED` = `{ POST /projects/<x>/activate (the Switch button and switchToProject, projectActions.ts:407), /chat/* (see the chat gate row), /local-settings/*, /auth/* }`, reject with an `AxiosError`-shaped `{ response: { status: 409, data: { detail: { error_kind: 'project_mismatch', message } } } }`. Reads are never blocked: `projectsApi.load` is `GET /projects/{name}` (`api/projects.ts:284`, backend `projects.py:2476`), so the block cannot deadlock recovery. `QUIET_TOAST_CODES` gains `project_mismatch` (the banner is the surface). |
| Writes that bypass axios | the block is **not** one place every write passes. (a) **Chat:** `/api/chat/stream` is a raw `fetch` (`api/chat.ts:104`) and the assistant's tools write into the backend's project — so while `projectMismatch` is set, `ChatPanel`'s Send is disabled and the existing `chat-send-gate` element (`ChatPanel.tsx:3195`) shows the banner sentence; the card queue holds as it does for `notReady`. (b) **Unload keepalive:** the raw `fetch` DELETE on `/api/network/lines|links/<name>` (`pages/TopologyCanvas.tsx:2106`) and `flushPendingEdgeDeletes` (`utils/pendingEdgeDeletes.ts`, called from the save path `Sidebar.tsx:765`) check `projectMismatch` and drop the request with `appLog('WARN', …)`. (c) Allowed on purpose: `api/uploads.ts:81-136` raw fetches are project-scoped by name in the URL; `topologyLayoutStore`'s `PUT /projects/<tab>/layout` targets the tab's own project. |
| Banner | `FE/components/ProjectMismatchBanner.tsx`, `data-testid="project-mismatch"`, `role="alert"`, mounted in `App.tsx` above the workbench: text `"This tab shows <tab>, but the app is now on <backend>. Changes from this tab are paused."`; buttons `project-mismatch-reload` ("Reload <tab>" → `projectsApi.load(tab)`, a GET, then clear) and `project-mismatch-switch` ("Switch to <backend>" → `switchToProject(backend, qc)`, `POST …/activate`, then clear). Reload can be refused with `study_in_flight` while a study runs on the backend's project (`projects.py:2377`): the banner then shows that 409's `detail.message` under the button (`project-mismatch-reload-error`) and keeps Switch enabled. |
| Autosave | `guardProjectMutation` returns false (silent for `auto`) while `projectMismatch` is set; the manual Save button shows the banner's sentence as its `title`. |
| Sidebar 409 branch | `Sidebar.tsx:846-861` does `String(detail)` on a dict → `"[object Object]"`, so a manual save refused with the `study_in_flight` dict shows "Cannot save: network is empty …" today. Fix in the same handler: `const msg = typeof detail === 'object' && detail ? detail.message : String(detail)`; the `/bound to project/` test runs on `msg`; a `study_in_flight` dict shows its own `message` as the toast. |
| Expert | the banner and the block apply in both modes (a data-integrity guard, not a Guided feature). |

**Red tests.**

| File | Case |
|---|---|
| `FE/App.recovery.test.tsx` (new) | two settled samples `meta.loaded_project='Y'`, `currentProject='X'` → banner text, `projectsApi.load` **not** called; **one** disagreeing sample → no banner; one disagreeing sample while `projectSwitchInProgress` is true, then agreement → no banner; Reload → `load('X')` and the banner clears; Reload refused with a `study_in_flight` 409 → its `message` under the button, Switch still enabled; Switch → `switchToProject('Y')`; `loaded_project=null, bus_count=0` → the old reload path |
| `FE/api/client.mismatch.test.ts` (new) | while mismatched: `PUT /network/buses/a` rejects client-side with `error_kind 'project_mismatch'` and no request leaves (adapter spy); `GET /projects/X` passes (reads are never blocked); `POST /projects/Y/activate` passes; `PUT /projects/X/layout` passes |
| `FE/layout/Sidebar.autosave.test.tsx` (new) | mismatched → the autosave tick posts nothing and logs one WARN; a 409 whose detail matches `/bound to project/` sets `projectMismatch` from the detail's names; a manual save refused with the `study_in_flight` **dict** toasts `detail.message`, not "[object Object]" or the empty-network sentence |
| `FE/components/ChatPanel.sendGate.test.tsx` | "mismatch → Send disabled, `chat-send-gate` shows the banner sentence, `createChatStream` not called on Enter" |
| `FE/utils/pendingEdgeDeletes.test.ts` (existing) | "mismatch → the keepalive DELETE is dropped with one WARN" |

**Mutation targets.** detection `!==` dropped → App test; two-sample rule dropped → the one-sample case; `projectSwitchInProgress` ignored → the switch case; allowlist emptied → the activate case; interceptor bypass for `put` → client test; chat gate dropped → sendGate test.

### 2.2 A5 — FMEA cache after a sweep; state after a project switch

**Anchors.** `FE/pages/hubDesign/cards/ImproveCard.tsx:103-115`; `FE/pages/results/FmeaTab.tsx:60-66`; `FE/hooks/useStudyFinishedInvalidation.ts:21-31` (its test file `FE/hooks/useStudyFinishedInvalidation.test.ts` is **new**); smoke settle loop `scripts/smoke-guided.mjs:1748-1762`.

**Contract.**
- `FE/hooks/useStartFmeaSweep.ts` exports `fmeaModesRefetchInterval(q)`: `2000` while `sweep_status === 'running'`, `2000` **once more** on the first sample after leaving `running` (a `WeakMap<query, boolean>` "extra tick" latch keyed by the query object), then `false`. Both `FmeaTab` and `ImproveCard` use it.
- `ImproveCard`: `useEffect(() => sweep.reset(), [project])`.
- `useStudyFinishedInvalidation(status)`: `prev` is reset to `undefined` when `currentProject` changes (read from `useUIStore`), so a switch never sees a false `running → done` transition.
- Fallback if the ×10 stress still shows a stale first read: backend `fmea_modes.sweep_status = 'finalising'` between the last contingency and the rows landing (additive value; the FE treats it as running). Decided at the P27b gate, recorded in the phase note.
- Smoke: the settle loop is replaced by "wait until `sweep_status !== 'running'` on `/api/results/fmea_modes`, then one more 2.5 s, then one read equal to the template's true count (8 / 4 / 6)" **only once** the counts are stable ten runs in a row with the fix; until then the loop stays.

**Red tests.** `ImproveCard.test.tsx` "one extra poll after the sweep ends" (fake timers: 3 fetches for running → done, not 2); "a project switch resets the sweep state" (`enabled` false after the switch, no `fmea_modes` fetch for the new project); `FmeaTab.test.tsx` uses the shared helper (import identity); `FE/hooks/useStudyFinishedInvalidation.test.ts` (new) "no invalidation when the project changes between samples". The `WeakMap<Query, boolean>` latch is keyed by the `Query` object React Query passes to `refetchInterval`; a query GC (`gcTime`) makes a fresh object, which is the intended reset.

### 2.3 A6 — mid-study project switch, end to end
Smoke only (§2.5) plus `FE/utils/projectActions.switch.test.ts`: after `switchToProject`, the hub queries `nk(old, 'results', 'eh_study'|'eh_review'|'fmea_modes')` are removed/inactive and the new project's are fetched once.

### 2.4 A1-FE — Guided buttons while a sweep runs
- `FE/hooks/useLiveStudyRunning.ts` (new): reads `nk(project,'results','fmea_modes')` with `fmeaModesRefetchInterval`; returns `sweep_status === 'running'` (the only live-network study reachable from Guided).
- `DelegateButton` (`FE/pages/hubDesign/shared/CardShell.tsx:33`) gains `disabled?: boolean; disabledTitle?: string`; Site "Fix with the assistant" (`SiteCard.tsx:76`), Goal VOLL (`GoalCard.tsx:124-130`) and Improve "Let the assistant do this" pass `disabled={liveStudy}` with `LIVE_STUDY_EDIT = 'A risk check is running — wait for it to finish or abort it before changing the network.'` (exported from `useLiveStudyRunning.ts`; the same sentence the toast shows for the 409).
- The 409's `detail.message` from P27a is rendered as-is by the existing toast path; `blockerMessage` (`FE/utils/blockerMessage.ts:14`) already extracts `detail.message`.
- Red tests: `SiteCard.test.tsx`, `GoalCard.test.tsx`, `ImproveCard.test.tsx` "buttons disabled with the sentence while the sweep runs; enabled after".

### 2.5 Gate
- Row 2: as P27a (no backend change; the superset re-run).
- Row 4s: `App*`, `hubDesign`, `FmeaTab*`, `useStudyFinishedInvalidation*`, `client*`, `Sidebar.autosave*`.
- Smoke `--phase P27b` = **P22.9** + **P26** (all steps) + (a) **restart:** `smoke-guided.mjs` gains `stop(name)` (kill that one process group with SIGTERM → SIGKILL as `stopAll` does, then splice it out of `procs`; `stopAll` `:154-160` stops everything and reverses `procs`, so it cannot be used). With the data-center project open in the tab: `await stop('backend')`, `start('backend', …)` re-issued with the same `RUN` dirs and env, poll `GET /api/health` until 200, then `POST /api/projects/<other>/activate` via `api()` (activate, not load, so the study-refusal path is not in play) so the backend binds another project; the Vite server and the page stay up and the tab is **not** reloaded; wait ≤ 10 s for `project-mismatch` (two settled `meta` samples); assert the banner text names both projects, a Properties-panel edit produces no `PUT` (request counter) and one `project_mismatch` refusal in the console log, `POST /api/projects/<tab>` autosave never fires (counter over 10 s); type in the dock and press Enter → `chat-send-gate` shown and no `/api/chat/stream` request leaves; click `project-mismatch-reload` → banner gone, `meta.loaded_project === <tab>`, the edit now succeeds, Send enabled; (b) **mid-study switch:** start a study on template 1, click template 2 on the Start card → the line equals `STUDY_RUNNING_SWITCH` (`useCreateFromTemplate.ts:19`), no project change; wait for `done`; click again → project 2, greeting "No study has run yet — …" (P28 wording; until P28 lands, assert the P26 greeting) and `hub-improve-list` absent; `GET /api/results/fmea_modes` rows belong to project 2 (names differ from project 1's); (c) FMEA counts read once after the extra tick equal 8 / 4 / 6 (or the settle loop, per §2.2).

### 2.6 Risks
| Risk | Mitigation |
|---|---|
| The banner shows during a legitimate switch | gated on `projectSwitchInProgress === false` and two consecutive settled disagreeing samples; `loaded_project === null` never sets it |
| Two tabs of one session on X and Y after a restart alternate the banner as each reloads its own project | correct for one backend binding; not a bug — a gate reviewer should expect it |
| Reload is refused while a study runs on the backend's project | the banner shows the 409's sentence; Switch stays available |
| The write block catches a legitimate switch | `load` is a GET (never blocked); `activate` is allowlisted; tested |
| Extra poll masks a real backend ordering bug | the `finalising` fallback is specified and decided at the gate |

---

## 3. P28 — Honest state (A3, A4, C6, C10)

### 3.1 A3 — per-profile readiness and the bound profile

**Anchors.** `BE/routers/chat.py:122-176` `/health` (byte-stable; docstring `:150-153` rules out a profiles list), `:648-667` `/profiles` (member-level, `{profiles:[{id,label,wire}], active_profile_id}`), `:703-800` `/history` (resolves `resolved_profile`, returns `last_session_id`, `bound_project`, `history_gap`, `pending_turn`). Readiness rule `:158-165` (bearer ⇒ `key_env` set; else ready). FE: `FE/hooks/useChatProfiles.ts:18` `CHAT_PROFILES_QUERY_KEY = ['chat','chat-profiles']`; `ChatPanel.tsx:1505-1512` gate; `:2748-2751` dropdown; `ChatLaunchGreeting.tsx:142-153` key offer; `FE/api/chat.ts:193-215` `ChatHealth`.

**Contract.**

| Endpoint / field | Rule |
|---|---|
| `GET /api/chat/profiles` | each profile gains `chat_ready: bool` computed by the `/health` rule applied to that profile (`os.environ` membership only; never a network call). `active_profile_id` unchanged. |
| `GET /api/chat/history` | gains `bound_profile_id: string \| null` = `resolved_profile.id` when a last session exists and the session is bound, else null. Read-only w.r.t. the session binding (the `:792-800` rule holds). |
| `GET /api/chat/health` | **unchanged byte for byte**: the existing `test_chat_sse.py::test_chat_health_reports_api_key_presence` / `test_chat_health_when_no_api_key` stay green, and a new `test_chat_profiles_readiness.py::test_health_key_set_is_unchanged` pins the exact key set. |
| FE effective profile | `effective = profileId ?? boundProfileId ?? active_profile_id`; `readiness(effective)` = `profiles.find(p => p.id === effective)?.chat_ready`, falling back to `chatHealth.chat_ready` when `effective === active` and the list is not loaded, else `undefined`. **Local mode:** `GET /api/chat/profiles` raises 401 when `optional_user` yields `None` (`chat.py:658`), so `useChatProfiles` is in error there; readiness then falls back to `chatHealth.chat_ready` for the active profile and `undefined` (fail-open) for a bound non-active profile — exactly today's behaviour. |
| Send gate | `notReady = readiness(effective) === false`; `undefined` → enabled (fail-open kept). |
| Key offer | `effectiveReady = readiness(effective) === true`; the offer hides when true (replaces `ChatLaunchGreeting.tsx:152-153`). |
| Store | `chatStore.boundProfileId: string \| null`, set from `/history` on hydrate (`ChatPanel.tsx:1672`), cleared on `resetForProjectSwitch` and on New chat. |

**Red tests.** BE `tests/test_chat_profiles_readiness.py` (new): bearer profile with its env set → `chat_ready true`; without → false; `auth: none` → true; `/health` key set unchanged; `/profiles` in local mode (no user) → 401 as today (`chat.py:658`). `tests/test_chat_profile_binding.py` (the existing `/history` tests live here, with `test_chat_e2e.py` and `test_chat_pending_turn_wal.py`): `bound_profile_id` equals the recorded profile; null with no turns. FE `ChatPanel.sendGate.test.tsx`: "after a reload with `profileId` null, `/history` bound to a ready non-active profile while the active profile is not ready → Send enabled"; "bound to a keyless profile, active ready → gated" ; the existing cases unchanged. `ChatLaunchGreeting.test.tsx`: "picked ready non-active profile → no key offer" (closes P26 note 5). The three `getChatHealth` mocks are untouched.

**Mutation targets.** `boundProfileId` ignored → the reload case; readiness list ignored → the picked case; `/health` gains a key → `test_health_key_set_is_unchanged`.

### 3.2 A4 — greeting: stale review and the `fresh` fallback

**Anchors.** `FE/components/ChatLaunchGreeting.tsx:56-88` `solveLine`; `:64-67` hub sentence; `:78` Guided `fresh` fallback; `:88` `'Not solved yet.'`; `useHubReview` `FE/pages/hubDesign/useHubData.ts:50-61`; `EhReview.stale` `FE/api/simulation.ts:787`.

**Contract** (Guided only; Expert branches untouched; the review query is `enabled: guided && hubStudy?.status === 'done'`, key `nk(project,'results','eh_review')`, no `refetchInterval`).

| State (Guided) | Sentence |
|---|---|
| `status.running` | `A solve is running right now.` (unchanged) |
| hub `running` | `The hub study is running — follow it in Hub design.` (unchanged) |
| hub `failed` / `aborted` | `The last hub study did not finish — see Hub design.` (unchanged) |
| hub `done`, review `stale === true` | `A study has run, but the network changed since — run it again in Hub design.` |
| hub `done`, review not stale (or not yet loaded) | `A study has run on this network — its results are in Hub design.` |
| no hub study, `dispatch 'fresh'` with a foreground condition | `Solved — the results match the network as it stands.` (unchanged) |
| no hub study, `dispatch 'fresh'` without a foreground condition | **new:** `A calculation has updated this network — see Results.` (replaces the `:78` Hub-design sentence) |
| `dispatch 'stale'` | unchanged |
| otherwise | **C6:** `No study has run yet — start in Hub design.` (Expert keeps `Not solved yet.`) |

**Red tests** (`ChatLaunchGreeting.solvedState.test.tsx`): stale review → the stale sentence; `fresh`, no foreground, `getEhStudy` 204 → the new sentence and not "Hub design"; Guided never-solved → C6 sentence; Expert never-solved → `Not solved yet.`; Expert never fetches `eh_review` (spy).

### 3.3 C10 — multi-tab `storage` listener
`uiStore.ts`: on `window 'storage'` with key `network-diagram:ui-mode-explicit` or `network-diagram:ui-mode`, when the other tab's explicit flag is set and this tab's is not → `setUiMode(stored, { explicit: true })`. Test `uiStore.uiMode.test.ts` "a storage event adopts the other tab's explicit choice; an implicit change is ignored".

### 3.4 Files
`BE/routers/chat.py`; `FE/api/chat.ts`, `FE/hooks/useChatProfiles.ts`, `FE/store/chatStore.ts`, `FE/components/ChatPanel.tsx`, `FE/components/ChatLaunchGreeting.tsx`, `FE/store/uiStore.ts`; tests above.

### 3.5 Gate
- Row 2 additions: `tests/test_chat*.py`.
- Row 4s: `ChatPanel*`, `ChatLaunchGreeting*`, `chatStore*`, `uiStore*`.
- Smoke `--phase P28` = **P26** + the **P22.9 send-gate step** extended (the `GET /api/chat/profiles` assertions run in the context the P22.9 stub-profile `PUT` already establishes, where the route answers): after the stub profile is active, `PUT` a second `auth: none` profile `smoke-stub-2` on the same stub port, send one turn with `profile_id: 'smoke-stub-2'` (the dropdown), then `POST /api/chat/settings/llm/active {profile_id: <default anthropic>}` (not ready, no key), reload the page: `chat-send` enabled with text typed, no `chat-send-gate`, `GET /api/chat/history.bound_profile_id === 'smoke-stub-2'`, `GET /api/chat/profiles` shows `chat_ready:false` for the anthropic profile and `true` for both stubs; the greeting shows no key offer. Then in Guided: edit one bus after a finished study → the greeting's stale sentence within 5 s (`chat-launch-greeting` text); on a fresh project the C6 sentence.

### 3.6 Risks
| Risk | Mitigation |
|---|---|
| `/profiles` now reveals which profiles have keys | it reveals `chat_ready` (a boolean), never a key name; the route stays member-level; `/health` is unchanged |
| The bound profile was deleted | `/history` returns null (C-4 path, `chat.py:757-773`); the FE falls back to active |

---

## 4. P29 — Guided chat and risk table (B1, B2, B3)

### 4.1 B1 — plain tool progress lines in Guided

**Anchors.** `FE/components/ChatPanel.tsx:2196-2216` (`tool_preparing` → `… preparing X`; `tool_request` → `→ X`), `:2239-2260` (`tool_result` → `✓ X`), `tool_error` → `✗ X` (same block), `guidedToolLine` `:468-472`, render `:3097-3138` (`chat-tool-label`, `chat-tool-details`), `GUIDED_CARD_SUMMARY` `:339-455`.

**Contract** (render-only; transcript and Expert unchanged).

| Stored line | Guided render |
|---|---|
| `… preparing X` | hidden |
| `→ X` | label `Working: <phrase(X)>…` |
| `✓ X` | label `Done: <phrase(X)>` |
| `✗ X` (and `tool_error` lines) | label `Could not: <phrase(X)>`; the error's own `message` stays as the next line |
| `denied: X` / `confirmation_denied` | as P26 |

`phrase(X)` = `GUIDED_TOOL_PHRASE[X]` (new table beside `GUIDED_CARD_SUMMARY`, e.g. `run_eh_study → 'run the reliability study'`, `update_component → 'change a setting'`, `suggest_eh_setup → 'look at how the site is set up'`, `get_adequacy_results → 'read the study results'`, `list_components → 'list what is in the network'`, `run_fmea_sweep → 'check what happens when equipment fails'`, `update_solver_config → 'change a study setting'`, `put_stress_scenarios → 'save the stress scenarios'`), fallback `use <tool words>`. The raw line sits under the existing `chat-tool-details`.

**Red tests** (`ChatPanel.sendRequest.test.tsx` "P29: Guided tool lines"): three frames for `run_eh_study` render `Working: run the reliability study…` then `Done: run the reliability study`, no `chat-message` text matches `/→ |preparing/` outside `details`; Expert renders the raw three lines (snapshot).

### 4.2 B2 — the FMEA tab in Guided

**Anchors.** `FE/pages/Results.tsx:526-531` (`PageHeader` eyebrow `SIMULATION · RESULTS`, title `Optimization results`, Guided subtitle already branches); `FE/pages/results/FmeaTab.tsx:161-171` (title `FMEA worksheet`, prose), `:237` "Class", `:257` `{r.failure_class}`, `:258-259` occurrence basis, `:274` `EngineBadge`, `:42-52` badge; `FE/pages/hubDesign/shared/Term.tsx:59` `Term`; catalogue `BE/data/guides/eh_fmea_guide.json` (`fields`), tests `BE/tests/test_guides.py:118-127` (`_JARGON`, ≤ 30 words), `:133-139` (`_HUB_JARGON`).

**Contract.**

| Surface | Expert (unchanged) | Guided |
|---|---|---|
| `Results.tsx` header | eyebrow `SIMULATION · RESULTS`, title `Optimization results` | eyebrow `HUB DESIGN · RESULTS`, title `Reliability results` |
| `FmeaTab` title | `FMEA worksheet` | `What could fail, and what it would cost` |
| header prose | `:166-171` | `Each row is one way the site can lose power. Occurrence is how often it happens; severity is what one event costs; the last column is the yearly risk. Your own rows and notes are kept with the project.` |
| Class cell | `A` … | `Term k="fmea_class_<a|b|c|d>"` → `Generator outage` / `Link outage` / `Stress scenario` / `Your own row` |
| Occurrence basis `FOR` | as is | `outage rate` (`Term k="fmea_occurrence"`) |
| Engine badge | `copt` / `lp_proxy` / `expert` | no badge; the row's `title` = `Term` text of `fmea_engine` + the fidelity tip |
| Severity `€0.0` | see B3 | see B3 |
| `data-testid` | `fmea-table` etc. unchanged | same ids; plus `data-guided="1"` on the table |

Catalogue keys added (`fields`): `fmea_class_a`, `fmea_class_b`, `fmea_class_c`, `fmea_class_d`, `fmea_engine`, `fmea_severity`, `fmea_occurrence`, `fmea_criticality`; each a statement ≤ 30 words, none of `_JARGON` / `_HUB_JARGON`; `test_hub_fields_present` (`test_guides.py:110`) lists them.

**Red tests.** `FE/pages/results/FmeaTab.guided.test.tsx` (new): Guided renders the plain labels, no cell text matches `/^(copt|lp_proxy|A|B|C|D)$/`, the hover keys resolve (import the JSON as `Term.test.tsx` does). `FmeaTab.expertUnchanged.test.tsx` (new, snapshot **taken on the P28-GO commit before any P29 edit**, per the P23 method: render on a scratch worktree, commit the `.snap`; B2 must leave it byte-identical; only B3's `title` updates it, justified). `Results.expertUnchanged.test.tsx` unchanged. `BE/tests/test_guides.py::test_hub_fields_present` red until the keys exist.

### 4.3 B3 — `zero_reason` on every FMEA row

**Anchors.** `BE/services/adequacy/copt.py:1145-1151` (class A: `crit = delta_eue × max(voll,0)`, `occ` from `mttr`, `severity = crit/occ if occ>0 else 0`), `:1275-1280` (merge recompute); `sweep.py:604-620` (class B; `in_scope False` → 0; VOLL ≤ 0 refused `:380-384`); `stress.py:644-655` (class C: `severity = delta × voll`); `BE/services/adequacy/copt_endpoint.py:86-91` (`per_mode` spreads `r["failure_mode"]`, so a key set inside `failure_mode` is forwarded with **no change there**); `BE/services/adequacy/worksheet.py` (existing module; helper lives here); FE `pages/results/fmea.ts:44-61` `WorksheetRow`, `:74` `mergeWorksheet`; `FmeaTab.tsx:260`; `FE/pages/hubDesign/cards/ResultsCard.tsx:86-91` (`no measurable cost`); `FE/pages/results/EhReferenceDesignPanel.tsx:359` `fmeaTopRows`.

**Contract.**

| Item | Rule |
|---|---|
| Helper | `worksheet.zero_reason(*, severity_eur, delta_eue_mwh, occurrence_per_year, in_metric_scope, voll) -> str \| None`: `None` when `severity_eur > 0`; else `'out_of_scope'` if not `in_metric_scope`; `'no_outage_data'` if `occurrence_per_year <= 0` (or not finite); `'unpriced'` if `voll <= 0` (copt path only — the sweep refuses VOLL ≤ 0, E2); `'no_shortfall'` if `delta_eue_mwh == 0`; else `None`. |
| Rows | `failure_mode["zero_reason"]` set by copt (`:1152`), sweep (`:621`), stress (`:656`) and re-derived in the copt merge (`:1280`); class-D manual rows carry `None`. `per_mode` forwards it unchanged (the spread at `copt_endpoint.py:86-91`). Helper docstring states: `out_of_scope` is tested before `no_outage_data` because `sweep.py:604-620` computes `occ` in both branches; a class-C row with `freq == 0` and `delta > 0` has `severity > 0` and correctly gets `None`. |
| FE type | `WorksheetRow.zero_reason?: 'no_shortfall' \| 'no_outage_data' \| 'unpriced' \| 'out_of_scope' \| null`. |
| FE text | `ZERO_REASON_TEXT` in `fmea.ts`: `no_shortfall → 'no shortfall — the site copes without it'`, `no_outage_data → 'no outage data'`, `unpriced → 'no price set for undelivered energy'`, `out_of_scope → 'not counted (outside the electricity metric)'`. |
| FmeaTab | severity cell: Guided shows the text; Expert shows `€0.0` with the text as `title` (Expert markup otherwise unchanged; the `title` attribute is the one accepted Expert deviation). Snapshot order: `FmeaTab.expertUnchanged` is taken on the P28-GO commit, B2 lands against it byte-for-byte, then B3's `title` is the **one justified snapshot update** in P29 (one-line justification in the plan note). |
| Hub | `ResultsCard` risks line reads `zero_reason` when `criticality_eur_per_year === 0`: the same `ZERO_REASON_TEXT`, falling back to `no measurable cost` when the field is absent. `fmeaTopRows` forwards the field. |
| Exports | `worksheetCsvRows` and every `downloadCSV/JSON` unchanged (byte-stable). |

**Red tests.** `BE/tests/test_fmea_zero_reason.py` (new): the helper's five outcomes; one row per engine on a constructed network (copt: a redundant genset → `no_shortfall`; a unit with `mttr_hours = 0` → `no_outage_data`; VOLL 0 → `unpriced`; sweep: an out-of-scope Link → `out_of_scope`; stress: a scenario with `delta 0` → `no_shortfall`); `per_mode` carries the key; the golden / range tests unchanged. FE `FmeaTab.formatting.test.tsx` "a €0 row shows its reason in Guided and as a title in Expert"; `ResultsCard.test.tsx` "a zero-cost risk says why".

**Mutation targets.** helper order swapped (`no_shortfall` before `no_outage_data`) → the mttr-0 case; field dropped from `per_mode` → the FE tests (mocked payload without the key → fallback text asserts differ).

### 4.4 Files
`ChatPanel.tsx`, `Results.tsx`, `FmeaTab.tsx`, `fmea.ts`, `ResultsCard.tsx`, `EhReferenceDesignPanel.tsx` (forward only), `Term.tsx` (`TermKey` union), `eh_fmea_guide.json`, `copt.py`, `sweep.py`, `stress.py`, `worksheet.py`, `copt_endpoint.py`, `api/simulation.ts` (type), tests above.

### 4.5 Gate
- Row 2 additions: `tests/test_fmea_zero_reason.py`, `tests/test_energy_hub_frontier_fmea.py`, `tests/test_energy_hub_class_c_authoring.py`, `tests/test_energy_hub_class_c_profiles.py`.
- Row 4s: `ChatPanel*`, `FmeaTab*`, `hubDesign`.
- Smoke `--phase P29` = **P26** + on the data-center FMEA step: `page-header` title text `Reliability results`; no `fmea-table` cell matches `/^(copt|lp_proxy|A|B|C|D)$/`; the `genset_1` row contains `no shortfall`; the Guided transcript after the Improve "Let the assistant do this" step has no `chat-message` text matching `/→ |preparing/` outside `details`; exports: `GET /api/results/fmea_modes` and the CSV download bytes equal the pre-phase fixture except the new key (assert the CSV header is unchanged).

### 4.6 Risks
| Risk | Mitigation |
|---|---|
| `zero_reason` disagrees with the hub's "no measurable cost" | one field, one text table, `ResultsCard` reads it |
| The Guided FMEA header hides that the tab is Results | the eyebrow keeps `RESULTS`; the sidebar item is unchanged |
| Tool-result body grows | one short key per row; within the per-turn result budget (no prompt change) |

---

## 5. P30 — Tours, templates, wizard (B4, B7, B5, B6, B8, B9, B10, C11, C12)

### 5.1 B4 — popover placement
**Anchors.** `FE/components/GuidedTour.tsx:194-206` (`W=320`, `below + 180 < vh`, `rect.top - 192`), `:211-217` highlight, `:216-218` dialog.
**Contract.** A `ref` on the dialog; after mount and on `ResizeObserver` / `resize` / `scroll`, compute `{w,h}`; candidates in order below, above, right, left (12 px gap); pick the first that fits inside `[8, vw-8] × [8, vh-8]` and does not intersect the target rect (`guide-highlight` box); if none fits, place at the largest free side and scroll the target into view; dialog style `maxHeight: calc(100vh - 16px)`, `overflowY: auto`. `data-placement="below|above|right|left|free"` on `guide-tour`.
**Red tests** (`GuidedTour.test.tsx`, mocked `getBoundingClientRect` and `innerWidth/Height`): target at the bottom with a tall popover → `above`, no intersection; target at the right edge → `left`; tiny viewport → `free`, inside the viewport; every case `top ≥ 8`, `top + h ≤ vh - 8`.

### 5.2 B7 — optional steps evaluated when reached
**Anchors.** `GuidedTour.tsx:104-106` `visibleSteps`, `:125-127`, `next` `:160-163`, counter `:221`.
**Contract.** Keep `tour.steps`; `next()` / `back()` skip an `optional` step whose target is absent at that moment; the counter shows `position among currently visible steps / count of currently visible steps` (recomputed on each move). `after_run` behaviour unchanged.
**Red tests.** "an optional step whose target appears after the tour started is shown"; "the counter counts only visible steps"; P24-FE pre-study tour walks 6 steps (unchanged).

### 5.3 B5 — `gen_zero_costs` exemptions
**Anchors.** `BE/services/validation_service.py:1749-1764`; `_warn` `:99`; templates `BE/project_templates/eh_templates.py:112-114` (`grid_supply` + `generators_t.marginal_cost`), `:140-141` (`rooftop_pv`, fixed, marginal 0; `p_max_pu` series present — assert in the test).
**Contract.** The zero-cost mask excludes a generator when (i) `n.generators_t.marginal_cost` has a column for it, or (ii) `n.generators_t.p_max_pu` has a column for it. Nothing else changes: a fixed dispatchable unit with all three static costs 0 and no series **still warns**. Message unchanged.
**Red tests** (`BE/tests/test_validation_gen_costs.py`, new): series marginal cost → no warning; profiled `p_max_pu` → no warning; fixed dispatchable all-zero, no series → warning; extendable all-zero → warning; `test_energy_hub_templates.py::test_templates_validate_without_gen_zero_costs` — all three builders → no `gen_zero_costs` in `validate_for_run(n, config)` output (`validation_service.py:2412`, the function `POST /api/simulation/preflight` calls, `simulation.py:404/505`); and the companion `test_gen_zero_costs_exemptions_cover_exactly_the_template_generators` asserts the exact set exempted is the seven the spec-review probe found (`scratchpad/qaspec2/probe_b5.py`): datacenter `grid_supply` (marginal-cost series), `rooftop_pv` (`p_max_pu`); H₂ hub `grid_supply`, `wind_farm`, `solar_park`; microgrid `pv_plant`, `wind_turbines` — so a narrowed exemption is caught. After the two exemptions **no** template generator warns.

### 5.4 B6 — the real projects root
**Anchors.** `BE/routers/local_settings.py:58-70` `_state()`, `:106` `GET ""`; `BE/app_paths.py:67-70`; `FE/api/localSettings.ts:30` `LocalSettingsState`; `FE/layout/NewProjectWizard.tsx:209-211`.
**Contract.** `_state()` gains `"projects_root": str(get_settings().flat_projects_root)` (`BE/settings.py:58-59`, default `app_paths.default_flat_projects_root()` `:73`; the value `routers/projects.py:69` `PROJECTS_DIR` resolves from); FE type gains `projects_root: string`; the wizard line reads `Saved to <projects_root>/<name>/` when the local-settings query resolved, and renders nothing (no line) otherwise. Hosted mode: the router-level `reject_unless_local_mode` (`local_settings.py:45`, `local_mode.py`) answers **404**, so `NewProjectWizard.templates.test.tsx` mocks a 404 for the "no line" case.
**Red tests.** `BE/tests/test_local_settings*.py` "`projects_root` equals `PROJECTS_DIR`"; `NewProjectWizard.templates.test.tsx` "shows the backend's root; no line when the query fails".

### 5.5 B8 — hub load errors
**Anchors.** `FE/pages/hubDesign/HubDesignPanel.tsx:112-116` (`hub-load-error`, `hub-load-retry`); `GoalCard.tsx:28-31`; `FE/api/client.ts:202-210`.
**Contract.** The line reads `This project's <study state | template | readiness> could not be read from the server, so the steps below may be incomplete.` from whichever query is in error (first of study, template, readiness); Goal's Run study is disabled while the template query is in error, `title="The template could not be read — retry above."`; `client.ts`: a `skipErrorToast` failure still calls `appLog('INFO', …)` (never ERROR, never a toast).
**Red tests.** `HubDesignPanel.flow.test.tsx` "template 500 → the line names the template, `hub-goal-run` disabled"; `client.quietToast.test.ts` "quiet failure logs INFO".

### 5.6 B9 — switch accessibility
**Anchors.** `FE/layout/AppHeader.tsx:1040-1075`, `UI_MODE_TITLES` (`FE/utils/uiMode.ts`).
**Contract.** Each button `aria-describedby="ui-mode-<m>-desc"`; a visually hidden `<span id=… className="sr-only">` with `UI_MODE_TITLES[m]`; `title` kept. Snapshot `AppHeader.expertUnchanged` updated with a one-line justification (accessibility attributes only).
**Red test.** `AppHeader.uiMode.test.tsx` "each mode button has an accessible description equal to its title".

### 5.7 B10 — scoped edit request
**Anchors.** `FE/store/uiStore.ts:426,595,776-777`; consumers `FE/layout/PropertiesPanel.tsx:1195-1199,1644-1648`; `FE/pages/results/prepareTaggingTour.ts`; pinned by `FE/layout/PropertiesPanel.editRequest.test.tsx`.
**Contract.** `propertiesEditRequest: { type: 'Bus' | 'Link'; name: string } | null`; `requestPropertiesEdit({type,name})`; consumed only by the panel whose selected component matches `{type,name}`; cleared on selection change (`setSelectedComponent`). `prepareTaggingTour` passes the bus it selected.
**Red test.** `PropertiesPanel.editRequest.test.tsx` "a request for bus A is not consumed by bus B and is cleared when the selection changes" (the existing test 3 is rewritten with a one-line justification).

### 5.8 C11 / C12 — test-only and two wordings
- `FE/pages/hubDesign/plainWords.test.ts`: a lone `dtc_planning` effect → "a plan for running without the grid" (R8).
- `AppHeader.uiMode.test.tsx`: Guided with `status.running` or a queued job → the Run/Abort control is rendered (R15).
- `FE/api/resultsApi.test.ts` (new): `getEhStudy()` / `getEhTemplate()` pass no `skipErrorToast`; `{quiet:true}` passes it (Q3–Q5).
- `plainWords.ts`: `\b(\d+(?:\.\d+)?) MWh\b` → `$1 megawatt-hours` in effects; `GoalCard.tsx:125`: `<Term k="mwh">` on the unit; catalogue key `mwh` (plain).

### 5.9 Gate
- Row 2 additions: `tests/test_validation*.py`, `tests/test_local_settings*.py`, `tests/test_energy_hub_templates.py`.
- Row 4s: `GuidedTour*`, `hubDesign`, `PropertiesPanel*`, `AppHeader*`.
- Smoke `--phase P30` = **P24** + **P22.9** + on every tour step screenshot (hub tour before / during / after a study; tagging tour): `guide-tour` box within the viewport and `guide-tour ∩ guide-highlight = ∅`; tagging tour started from a Bus, then open a Link's Edit form → the counter reads `2/2` and the Link step shows; `POST /api/simulation/preflight` (`BE/routers/simulation.py:403`, which runs `validate_for_run`, `:505`) on each template → no issue with `code === 'gen_zero_costs'`; the New-project dialog's `Saved to` line contains the smoke's scratch `PYPSAGUI_PROJECTS_ROOT`.

### 5.10 Risks
| Risk | Mitigation |
|---|---|
| Placement thrash on scroll | measurements are batched per frame (`requestAnimationFrame`), and the placement is recomputed only when the target rect or size changes |
| B10 breaks the tagging tour prepare path | the P22.9 smoke's tagging step asserts the Bus Edit form opens |
| B5 hides a real warning on an unusual network | the exemption is exactly the two series cases; the warning text is unchanged; recorded in the phase note |

---

## 6. P31 — Cosmetics (C2, C3, C4, C5)

| Item | Anchor | Contract | Red test |
|---|---|---|---|
| C2 toast over Send | `FE/main.tsx:47-49` (`position="bottom-right"`) | `containerStyle={{ bottom: dockOpen ? DOCK_HEIGHT + 16 : 16 }}` from a small `ToasterHost` that reads `assistantDockOpen`; `DOCK_HEIGHT` exported by the dock | `ToasterHost.test.tsx` (new): bottom offset follows the store. Smoke: after "created from template", the toast box ∩ `chat-send` box = ∅ |
| C3 `fmtEnergy` | `FE/pages/results/shared.tsx:743-749` | `< 1 MWh` → `${mwh.toFixed(3)} MWh`; `0` → `0 MWh`; the FMEA/ΔEUE column header gets `(MWh)` back where it was dropped | `shared.test.ts` cases; `EhReferenceDesignPanel.formatting.test.tsx` header |
| C4 vacuous smoke check | `scripts/smoke-guided.mjs:1162-1164` | count `page.on('request')` + `page.on('response')` with `url.includes('/api/results/eh_study') && status >= 500` after recovery; a **new** `--self-test` flag (none exists today) re-installs one 500 route **after** recovery and expects the run to print `FAIL` and exit 1 by design | the self-test run is part of gate row 5; the gate note records it as "expected FAIL" |
| C5 docstring | `BE/routers/results.py:1523-1526` | "204 when there is no stored report and no study record **with a result** (a study that failed with an exception has a record and still returns 204; an aborted study returns 200 `ok` with `summary.verdict: null`)" | none (docs) |

Gate: rows 1–7 as §0.2; row 2 = the superset; smoke `--phase P31` = **P26** + the toast assertion + `--self-test`.

---

## 7. P32 (scheduled) and the optional stubs P33–P34

### 7.1 P32 — chat-created projects start Guided (D1; D-8 = (a), owner 2026-09-28) — scheduled directly after P27a

FE-only; depends on P27a's `project_rebound` frame for the creating tools (A8).

**Anchors.** Parent spec §1 non-goal sentence (`2026-09-27-guided-mode.md:41` "chat-created template projects (`create_project_from_template`) do not switch the mode") and §3.4 last paragraph (`:224` "Chat-created projects (`create_project_from_template`, `save_project_as`) are a v1 non-goal (§10)"); §3.7 (`:263-273`); `FE/components/ChatPanel.tsx:2322-2345` (`project_rebound` handler: `setCurrentProject`, `setProjectName`, `touchTab`, invalidations, toast); `FE/store/uiStore.ts:647-660` (`noteNewProjectCreated` re-reads `ui-mode-explicit` and no-ops when explicit), `:640-642` (§3.7 pruning inside `setUiMode`); `FE/App.tsx:197-208` (P23 auto-open).

**(a) §10 addendum to the parent spec** (written by P32 as one table row, "§10 addendum: D-8 (2026-09-28, product owner)"): §1's non-goal sentence (`:41`) and §3.4's last paragraph (`:224`) are replaced by: "G4 extends literally to projects the assistant **creates**: a `project_rebound` frame whose `via_tool` is `create_project_from_template` or `import_project_bundle` counts as a new project (`noteNewProjectCreated('template' | 'file')`). A save (`save_project`, `save_project_as`) is not a new project and never changes the mode. Only an explicit mode choice blocks the switch."

**(b) Trigger.** The `project_rebound` frame — not the tool result (which does not say whether the binding moved) and not `save_project` / `save_project_as` (a save is not a new project). In the handler at `ChatPanel.tsx:2322-2345`, **after** `setCurrentProject(d.to)` (so §3.7 sees the project): `if (d.via_tool === 'create_project_from_template') noteNewProjectCreated('template'); else if (d.via_tool === 'import_project_bundle') noteNewProjectCreated('file')`. `NewProjectKind` already has `'template'` and `'file'`.

**(c) Explicit-choice rule.** No second rule: `noteNewProjectCreated` re-reads `network-diagram:ui-mode-explicit` from storage and no-ops when it is set (`uiStore.ts:652-658`). An explicit-Expert user is never flipped.

**(d) §3.7 on the flip, mid-conversation.** `setUiMode('guided')` prunes as today: `activeSlidePanel ∉ {hubDesign, results, null}` → `hubDesign` (`uiStore.ts:640-642`); the Results tab is coerced in `Results.tsx` (not written). Stated for this case: the assistant dock stays open and the transcript keeps scrolling (the dock is not in the §3.7 hidden list); `paletteMode` and the compare rail are untouched; the P23 auto-open then opens `hubDesign` for the new project unless a panel is already open for it (`App.tsx:197-208`).

**(e) Expert unchanged.** The change is store-side; no `uiMode` branch gains a new Expert arm; an explicit-Expert user keeps Expert and their open panels.

**(f) Red tests.**

| File | Case |
|---|---|
| `ChatPanel.sendRequest.test.tsx` "P32" | frame `via_tool: 'create_project_from_template'` → `noteNewProjectCreated('template')` called once, `uiMode === 'guided'`, `activeSlidePanel === 'hubDesign'` (implicit store) |
| same | `via_tool: 'import_project_bundle'` → `noteNewProjectCreated('file')` |
| same | `via_tool: 'save_project_as'` and `'save_project'` → not called, mode unchanged |
| same | `uiModeExplicit: true`, mode `expert` → not flipped, panels unchanged |
| same | the dock stays open and the frame's toast still fires |
| `uiStore.uiMode.test.ts` | unchanged (the explicit rule is already pinned there) |
| backend | none; `test_guided_mode_prompt.py` stays green (no prompt change) |

**(g) Smoke `--phase P32`** = **P25** + stub branch 7 from the dock in an **implicit-Expert** context (fresh context seeded with `network-diagram:current-project` and the P22.9 keys, `network-diagram:ui-mode = expert`, **no** `network-diagram:ui-mode-explicit`): after the turn `ui-mode-switch` reads Guided (`ui-mode-guided[aria-pressed="true"]`), `hub-card-site` visible, transcript shows `Active project: <name>`; then the same in an **explicit-Expert** context (`ui-mode-explicit = 1`) → stays Expert, no `hub-card-site`.

**(h) Gate.** Rows 1–7 as §0.2 (row 2 = the 14-file superset, no additions); row 4s stress `ChatPanel*`, `uiStore*`; row 7 signed (store-side change only). **(i) Prompt cache:** none (no `TOOLS`, description or system-block change). **Packaging:** none (FE only).

**Risks.** An implicit-Expert user mid-conversation is flipped and a non-Guided panel is replaced by `hubDesign` (the owner accepted this); the `Active project` toast and the mode toast (`uiModeToast`) show together — acceptable, stated.

### 7.2 P33 — a second Guided workflow (D2, needs D-9) — stub
Needs its own spec (cards, catalogue keys, delegate texts, stub branches, smoke phase). **Not scheduled.**

### 7.3 P34 — server-side mode preference (D3, needs D-10) — stub
Sketch: `GET/PUT /api/settings/ui-mode` (hosted mode, per user), the browser value as the fallback, migration of the implicit default. Smoke: P23 with two contexts for the same user. **Not scheduled.**

---

## 8. Decisions taken by this spec's author

| Question | Decision |
|---|---|
| Where `study_in_flight_detail` lives | `services/study_state.py` (already imports `STUDY_LABELS`, `running_study_key`); `routers/projects.py` re-imports; no new module |
| Which chokepoints for A1 | the `main.py` solver branch (HTTP) **and** the three `network_crud` handlers (chat); undo is already covered |
| A2 route | none — `loaded_project` is already on `/network/meta`; only the FE type changes |
| A2 write block location | the axios request interceptor for every axios write, plus the three bypasses named in §2.1 (chat Send gate, unload keepalive DELETE, `flushPendingEdgeDeletes`); uploads and the tab's own layout `PUT` stay allowed |
| A3 carrier | `chat_ready` per profile on `/chat/profiles`; `bound_profile_id` on `/history`; `/health` byte-stable |
| B2 Expert deviation | none in markup; B3 adds a `title` attribute on the Expert severity cell (recorded) |
| B3 helper home | `services/adequacy/worksheet.py` |
| Snapshot timing for `FmeaTab.expertUnchanged` | taken on the P28-GO commit before any P29 edit, committed with P29 |
| Smoke composition | a new phase calls its base phase's function, then its extension; `PHASES` gains `P27a`, `P27b`, `P28`, `P29`, `P30`, `P31` |
| A1 helper duplication (`study_swap_refusal` string vs the `study_in_flight` dict) | not for P27a; a later `structured=True` path on `refuse_if_study_running` could carry `error_kind` on undo / load / import refusals |

## 9. Spec review conditions applied

| # | Condition | Applied where |
|---|---|---|
| 1 | A1 undo shape (string detail; 409-only for `undo_last`) and the chat-test harness | §1.1 anchors, "Chat-test harness" row, red-test table |
| 2 | A1 middleware scope `/api/network/` + `/api/io/` only; `PyPSAService.get_solver_state()`; save-sentence test | §1.1 "HTTP chokepoint", red tests, mutation targets |
| 3 | A2 allowlist (activate, chat, local-settings, auth; `load` is a GET); Reload refused mid-study | §2.1 "Write block (axios)", "Banner", `App.recovery` / `client.mismatch` cases |
| 4 | A2 chat Send gate and keepalive-DELETE / `flushPendingEdgeDeletes` gating; uploads and layout allowed | §2.1 "Writes that bypass axios", tests, smoke (a) |
| 5 | A2 false positive: `projectSwitchInProgress` fence + two settled samples | §2.1 "Detection", `App.recovery` cases, risks |
| 6 | A8 same-turn test with the real `switched` closure; stub branch 7 = two `tool_use` blocks | §1.2 red tests, §1.3, §1.5 |
| 7 | P27b restart mechanics: `stop(name)`, re-`start`, `/api/health` poll, `activate`, tab not reloaded | §2.5 (a) |
| 8 | Row-2 superset of 14 files | §0.2 row 2 |
| 9 | Anchor and name corrections (`copt_endpoint`, `EhReferenceDesignPanel`, health tests, `test_chat_profile_binding.py`, `plainWords.test.ts` path, new `useStudyFinishedInvalidation.test.ts`, `check_bundle.py:172`, prompt-test name, B3 snapshot order) | §0.1, §2.2, §3.1, §4.2, §4.3, §5.8, §6; plan §5 |
| 10 | B5: the seven exempted names pinned; no template generator warns | §5.3 |
| 11 | Local-mode status codes: `/chat/profiles` 401 (FE fallback), `/local-settings` 404 in hosted mode; P28 smoke context | §3.1, §3.5, §5.4 |
| 12 | P32 fully specified (a–i); title and headers updated; plan §0 line fixed | §7.1; plan §0 |

**Non-binding suggestions adopted:** Sidebar `String(detail)` "[object Object]" fix folded into P27b (§2.1) with its `Sidebar.autosave.test.tsx` case; A2 multi-tab alternating banner stated in the risk table; P27a smoke wording "manual save"; A5 `WeakMap` GC note; B3 helper docstring on `out_of_scope` ordering and `freq == 0`; C4 `--self-test` is a new flag that exits 1 by design; A1 helper duplication recorded in §8 as later work. **Rejected:** none.
