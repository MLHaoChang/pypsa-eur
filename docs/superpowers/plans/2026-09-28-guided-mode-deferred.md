# Guided mode: deferred items — assessment and plan (P27+)

**Date:** 2026-09-28 (rev 2, after the plan review). **Base:** branch `claude/epic-allen-k2t1c4` at `00a35a5` plus the P26 final-gate fixes in the working tree.
**Spec (parent):** [`../specs/2026-09-27-guided-mode.md`](../specs/2026-09-27-guided-mode.md). **Spec (this work):** [`../specs/2026-09-28-guided-mode-deferred.md`](../specs/2026-09-28-guided-mode-deferred.md). **Review:** [`../qa/2026-09-28-guided-mode-deferred-plan-review.md`](../qa/2026-09-28-guided-mode-deferred-plan-review.md) (GO-with-conditions).
**Sources read:** the close-out note, every gate file (P22.9-BE/FE, P23, P24-BE/FE, P25, P26), the spec review, the Expert click-through, spec §1 / §8 / §10, the plan review.

Every file:line below was checked in the tree on 2026-09-28. `FE` = `pypsa-gui/frontend/src`, `BE` = `pypsa-gui/backend`.

## 0. Summary

| Bucket | Open | Already fixed (verified) |
|---|---|---|
| Safety and data integrity | A1–A6, A8 (7) | A7 |
| First-time-user friction | B1–B10 (10) | B11–B16 |
| Cosmetic and test-only | C1–C6, C10–C13 (10) | C7–C9 |
| Accepted, not fixed | E1–E2 (2) | — |
| Non-goals (owner decision) | D1–D3 (3) | — |

27 open items in 9 phases (P27a, P27b, P28–P31 committed; P32 scheduled after P27a per D-8 = (a); P33–P34 optional, not scheduled). 11 owner decisions, §4.

## 1. Already fixed — no work (verified in code)

| # | Item | Where it came from | Evidence now |
|---|---|---|---|
| A7 | Unit test for the topology restore did not pin `generators.control` | P22.9-BE minor | `BE/tests/test_live_network_untouched.py:185,196` |
| B11 | Greeting stuck on "study running" after the hub closes | P26 gate B1 | `FE/components/ChatLaunchGreeting.tsx:135` `refetchInterval: ehStudyRefetchInterval` (`pages/results/ehStudyPoll.ts`) |
| B12 | Destructive cards hid their target | P26 gate B2 | `FE/components/ChatPanel.tsx:376-455`; Details open for `destructive` (`:684`) |
| B13 | Greeting chips "Check adequacy" / "Summarize this solve" | P26 gate | working tree `ChatPanel.tsx:1337-1345` |
| B14 | "outage-driven"; Improve text vs. action; H₂ hub no way to Goal; Goal placeholder | P26 gate | working tree `plainWords.ts:11`, `ImproveCard.tsx:56-60`, `ResultsCard.tsx:56-62` (`hub-results-set-goal`), `GoalCard.tsx:93-99` |
| B15 | Failed / aborted study read "Not solved yet." | P26 gate note 2 (half) | `ChatLaunchGreeting.tsx:61-63` |
| B16 | P23 N-R2: a project switch during a tour consumed the new project's auto-open | P23 re-gate | `FE/App.tsx:201` returns before marking |
| C7 | VOLL / frontier / fmea_top effect not translated | P24-FE re-gate | `plainWords.ts:23-26` |
| C8 | P24-BE N6 (hook imports a page), N7 (`import_p_nom_mw` non-finite) | P24-BE | `FE/hooks/useStartFmeaSweep.ts:11` imports `utils/blockerMessage`; `BE/services/adequacy/eh_readiness.py:307` `_finite_or_none` |
| C9 | P25 notes 1–4; P22.9-FE notes 1, 2, 6 | P25 / P22.9-FE | `ChatPanel.tsx:2521-2535` (queue), `:619-620` (deny drops group), `:571-572` (expiry drops group); `chat_service.py:2292-2293` (sanitiser loops); `stub_openai_endpoint.py:120,175` ("finished" for a read) |
| — | P22.9-BE note 3 "margin loop has no HTTP invariant test" (review §2 item 1) | P22.9-BE / review | **already present:** `test_live_network_untouched.py:243` `test_margin_loop_leaves_the_live_tables_equal`, judged non-vacuous at the P22.9-BE re-gate. A1 keeps it green and adds the lock spy to the same file. |
| — | Smoke "exits 0 on FAIL" (P26 note 6) | P26 | not reproduced: `scripts/smoke-guided.mjs:133` `check` throws; `:1892-1901` `code = 1`, `process.exit(code)`. |

## 2. Open items

Legend: **Hurts** = first-time user (FTU) / expert / data integrity (DI) / safety. **Size** S ≤ ½ day, M ≤ 2 days, L > 2 days. Contracts, test ids and wording are in the spec; this file carries the assessment.

### A. Safety and data integrity

#### A1. Edits made during a live-network study are reverted; the restore writes outside the lock
- **Problem.** A user edit to `control` / `sub_network` / `generator` (Bus, Generator, passive branches) or to `*_nom_min/max` made while a sweep / frontier / coupling / margin loop runs on the live network is silently overwritten when the study ends, and the write-back can interleave with an edit. **Hurts:** DI, expert.
- **Evidence.** `BE/services/adequacy/sweep.py:87-125` `preserve_bus_topology` snapshots on entry and writes back in `finally` with no lock; `freeze_capacities`' `_undo` (`:188-196`) is the same shape. The runners pass `lock` to `_solve_once` (`:279-286` → `run_simulation`), which holds it per solve only, so the `finally` runs unlocked. Call sites: `sweep.py:400-401`, `frontier.py:211`, `coupling_loop_runner.py:625`, `margin_loop_runner.py:962`, `dtc.py:396` (on a copy, no lock). Edit routes take the lock (`BE/services/network_crud.py:211,387,441`) but are never refused during a study; the chat tools reach the same handlers through `_route` (`chat_tools.py:2115-2147`), bypassing `main.py`'s middleware. `/api/network/undo` **is** already refused (`network_undo.py:105` `refuse_if_study_running("undo")`).
- **Root cause.** Snapshot/put-back outside the lock; no edit refusal during a live-network study.
- **Options.** (1) Diff-restore under the lock, keeping user edits. (2) Restore under the lock **and** refuse edits during a live-network study with the existing `study_in_flight` 409 shape (`projects.py:1515-1541`).
- **Recommendation:** 2 (review D-1 agrees). Mechanics per review conditions 2, 3, 7:
  - `preserve_bus_topology(n, lock)` / `freeze_capacities(n, lock)` acquire the lock the runner **passed in** (the workers run under a copied context: `fmea_sweep_runner.py:56`, `coupling_loop_runner.py:285`, `margin_loop_runner.py:208`, `frontier_loop_runner.py:72`; `PyPSAService.get_lock()` is an `RLock`, `pypsa_service.py:632`, not held when `finally` runs). `dtc.py:396` passes `None` (copy).
  - Live-network study keys: `fmea_sweep`, `frontier`, `coupling_loop`, `margin_loop`. Not `mc` (`mc_loop_runner.py:66` "never mutates the network"; it snapshots under the lock) and not `eh_study` (`eh_study.py:445,971` `network.copy()`), so Guided tagging edits during a 30-solve EH study stay allowed.
  - Chokepoints: (i) `main.py:769-800` solver-in-flight branch gains a second check, `_study_in_flight_detail(state, "edit the network")` restricted to the live keys, same prefixes / exemptions; (ii) `network_crud._create_component` / `_update_component` / `_delete_component` (`:204,375,439`) raise the same detail so the chat path is covered; undo is already covered.
  - Assistant exposure: a routed tool's `HTTPException` with a dict detail reaches the model as `tool_error{error_kind:'study_in_flight', message}` (`chat_service.py:4619-4645`) — reuse. Guided: the Site card's "Fix with the assistant", the Goal card's VOLL button and the Improve "Let the assistant do this" are disabled while an FMEA sweep runs, with one plain title.
- **Files.** `sweep.py`, `frontier.py`, `coupling_loop_runner.py`, `margin_loop_runner.py`, `dtc.py`, `main.py`, `network_crud.py`, `routers/projects.py` (export `_study_in_flight_detail` or move it to `services/study_state.py`), `FE/pages/hubDesign/shared/CardShell.tsx`, `FE/hooks/useLiveStudyRunning.ts` (new).
- **Tests.** `BE/tests/test_live_network_untouched.py`: lock spy on the passed lock; edit-during-sweep refused (HTTP and chat tool); edit-during-EH-study allowed; edit after the study kept; the five invariant tests (sweep, abort, frontier, coupling, margin) stay green. FE: `CardShell.test.tsx` / `SiteCard.test.tsx` buttons disabled while the sweep runs.
- **Size:** M. **Risk:** Expert medium (edits during a sweep now 409 with a plain sentence; one toast). System low.
- **Owner decision:** D-1.

#### A2. Bug 6: one-off 409 on resume after a backend restart with a stale tab open
- **Problem.** After a backend restart, a tab still holding `currentProject = X` keeps editing and autosaving while the backend is bound to Y (another tab, resume-on-start). Autosave hits the identity guard (409); **edits from the tab land in Y's live network** (the real hazard, review condition 4). **Hurts:** DI, FTU.
- **Evidence.** Guard: `BE/routers/projects.py:1686-1694`. Autosave handling: `FE/layout/Sidebar.tsx:846-861`. Recovery: `FE/App.tsx:446-481` reloads only when `bus_count === 0`. `GET /api/network/meta` **already returns `loaded_project`** (`BE/services/network_crud.py:124-141`, route `routers/network.py:601-604`), but the FE type omits it (`FE/api/types.ts:178`) and the effect never compares it. The review's proposed `GET /api/projects/active` is dropped: `GET /{name}` (`projects.py:2476`) would shadow it.
- **Root cause.** Identity is checked on save only; nothing compares the tab's project with the backend's binding after a reconnect.
- **Recommendation (D-2, review-amended).** No backend change. The recovery effect reads `meta.loaded_project`; when it is non-null and ≠ `currentProject`, the store enters `projectMismatch`, a blocking banner offers "Reload X" (load X) or "Switch to Y" (adopt), the axios request interceptor refuses every write from the tab with a client-side 409 `{error_kind:'project_mismatch'}` until resolved, and autosave is suspended through `guardProjectMutation`.
- **Files.** `FE/api/types.ts`, `FE/store/uiStore.ts`, `FE/api/client.ts:130` (request interceptor), `FE/App.tsx`, `FE/layout/Sidebar.tsx`, a new `FE/components/ProjectMismatchBanner.tsx`.
- **Tests.** New `FE/App.recovery.test.tsx` (mismatch → banner, no load, no autosave; reload / switch paths); `FE/api/client.mismatch.test.ts` (PUT refused client-side while mismatched, GET allowed); `Sidebar.autosave.test.tsx` (new: identity 409 → autosave suspended, one WARN). Smoke: restart uvicorn with the tab open (P27b), assert the banner **and** that a PUT from the stale tab is refused.
- **Size:** M. **Risk:** Expert: the banner is visible in Expert too (by design). System low.
- **Owner decision:** D-2.

#### A3. Send gate: the session's bound profile is not exposed to the client
- **Evidence.** `FE/components/ChatPanel.tsx:1511-1512`; `GET /api/chat/history` (`BE/routers/chat.py:703-800`) resolves `resolved_profile` but returns only `last_session_id` / `bound_project`; `session_init` carries `profile_id` (`chat_service.py:1417-1424`) only once a turn starts. `/chat/health` (`chat.py:122-176`) deliberately excludes a profiles list; `/chat/profiles` (`chat.py:648-667`) is member-level and lists every profile.
- **Recommendation (D-3, review-amended).** Per-profile `chat_ready` on `GET /chat/profiles` (env-membership only, cheap), `bound_profile_id` on `/history`; `/chat/health` stays byte-stable. FE gate on the effective profile `profileId ?? bound ?? active` with that profile's readiness; the key offer follows the same rule (closes P26 note 5).
- **Files.** `BE/routers/chat.py`, `FE/api/chat.ts`, `FE/hooks/useChatProfiles.ts`, `ChatPanel.tsx`, `ChatLaunchGreeting.tsx`.
- **Tests.** BE `test_chat_profiles_readiness.py` (new), `test_history_reports_bound_profile`. FE `ChatPanel.sendGate.test.tsx` "bound to a ready non-active profile after reload → Send enabled"; `ChatLaunchGreeting.test.tsx` "picked ready non-active profile → no key offer"; the three `getChatHealth` mocks are untouched.
- **Size:** M. **Risk:** Expert low. System low.

#### A4. The greeting ignores study staleness, and the `fresh` fallback sends non-hub re-solves to Hub design
- **Evidence.** `ChatLaunchGreeting.tsx:64-67` reads `hubStudy === 'done'` and `status.dispatch`; the review's boolean `stale` (`useHubData.ts:50-53`, `EhReview.stale`, `api/simulation.ts:787`) is never read. `:78` sends any `fresh`-no-foreground state to Hub design in Guided, including an Expert-run sweep (P24-FE re-gate 2 note 5; review condition 8).
- **Fix.** Guided reads `eh_review` on the hub's key (no poll); `stale` → "A study has run, but the network changed since — run it again in Hub design."; the "results are in Hub design" sentence is keyed on the hub study record only; the `fresh`-no-foreground case gets its own Guided sentence ("A calculation has updated this network — see Results.").
- **Tests.** `ChatLaunchGreeting.solvedState.test.tsx`: stale → stale sentence; `fresh` without a hub study → the new sentence, not "Hub design"; Expert unchanged. **Size:** S.

#### A5. Transient stale FMEA tab after a sweep; leftover state after a project switch
- **Evidence.** `ImproveCard.tsx:103-115` (poll stops at the first non-`running` sample; `enabled: sweep.isSuccess` is mutation state per mount); `FmeaTab.tsx:60-66` same interval; `useStudyFinishedInvalidation.ts:21` keeps `prev` across a project switch (P22.9-FE note 7, review §2 item 4); smoke settle loop `smoke-guided.mjs:1748-1762`.
- **Fix.** Shared `fmeaModesRefetchInterval` that polls one extra tick after leaving `running`; `sweep.reset()` on project change; `useStudyFinishedInvalidation` resets `prev` when `currentProject` changes. Fallback (review suggestion): backend `sweep_status: 'finalising'` if the ×10 stress still flakes; the smoke settle loop is reverted only once the counts are stable ten runs in a row.
- **Tests.** `ImproveCard.test.tsx`, `FmeaTab.test.tsx`, `useStudyFinishedInvalidation.test.ts` (new case). **Size:** S.

#### A6. In-app project switch mid-study not covered end to end
- **Fix.** P27b smoke step (start a study, attempt a template switch → the plain 409 line, wait, switch, assert the new project's greeting / cards, no cross-project rows) plus a `projectActions.switch.test.ts` case for the hub queries. **Size:** S.

#### A8. Chat-created projects rebind the backend without a `project_rebound` frame
- **Problem.** `create_project_from_template` and `import_project_bundle` run by the assistant swap and bind the backend, but no `project_rebound` frame is emitted: the tab's `currentProject` stays on the old name (autosave `expect` → identity 409), and **every further tool in the same turn is refused with `project_switched_mid_turn`** (review probe `scratchpad/qadef/test_probe_a8.py`, `switched_mid_turn=True`). **Hurts:** DI.
- **Evidence.** Template: `BE/routers/projects.py:1264` (declares `session`), `:1342` (bind), `:1351` (pointer); bundle: `chat_tools.py:2354-2371` → `import_bundle`, binds `projects.py:1059`, persists `:1068` / `:1139`; `PROJECT_REBINDING_TOOLS` (`chat_service.py:125-131`) lacks both; `_dispatch_tool_uses` (`:3331-3345`) emits the frame only for that set. `save_project` from an unbound draft also binds (`_save_context`, `:1929`; pointer moved when `was_unbound`, `:1475,1500-1502`) and is not in the set either.
- **Fix.** Add `create_project_from_template`, `import_project_bundle` **and `save_project`** to `PROJECT_REBINDING_TOOLS`. The frame is emitted only when the binding actually moved (`new_bound != holder`), so a Save-a-Copy (`rebind=False`) emits nothing; the FE handler (`ChatPanel.tsx:2322-2345`) already covers "unbound → open".
- **Tests.** `BE/tests/test_chat_tool_dispatch_loop_seam.py`: modelled on `:150-190`, for each of the three names: the fake dispatcher moves `loaded_project`, the frame is asserted with `via_tool`, the holder follows, and a **second tool in the same turn dispatches normally** (red today: `project_switched_mid_turn`). Smoke P27a: stub branch "create a template from chat", then one autosave → no 409.
- **Size:** S. **Risk:** none to Expert.

### B. First-time-user friction

#### B1. Raw tool progress lines in Guided
- **Evidence.** `ChatPanel.tsx:2196-2216` (`tool_preparing`, `tool_request`), `:2239-2260` (`tool_result` "✓ tool"); `guidedToolLine` `:468-472` covers only the declined lines; render `:3097-3138`.
- **Fix.** Render-only: Guided maps `… preparing X` → hidden, `→ X` → "Working: <verb phrase>…", `✓ X` → "Done: <verb phrase>", `✗ X` → "Could not: <verb phrase>", raw line under Details; a per-tool verb table beside `GUIDED_CARD_SUMMARY`. Expert unchanged. **Tests.** `ChatPanel.sendRequest.test.tsx`; Expert snapshot. Smoke P26: no Guided `chat-message` contains `→ ` or `preparing`. **Size:** S.

#### B2. The FMEA tab reached from Guided is Expert-styled
- **Evidence.** `FE/pages/Results.tsx:526-531` (eyebrow / title in both modes); `FmeaTab.tsx:166-171` header prose, `:237,257` class letters, `:258-259` "FOR", `:274` engine badge. `Results.expertUnchanged.test.tsx` snapshots only the tab strip (P23 note 4).
- **Fix (D-4).** Guided variant of the same tab: header "HUB DESIGN · RESULTS" / "Reliability results"; plain class labels with the letter in a hover; engine as a hover; plain header sentences; "outage rate" for FOR; catalogue keys `fmea_class_a|b|c|d`, `fmea_engine`, `fmea_severity`, `fmea_occurrence` in `eh_fmea_guide.json` (plain, ≤ 30 words, `test_guides.py` `_JARGON`). Expert byte-identical: `FmeaTab.expertUnchanged.test.tsx` snapshot taken on the P28-GO commit **before** any P29 edit. **Size:** M.

#### B3. Bug 5: FMEA rows with severity 0 show `€0.0` with no explanation
- **Evidence.** `copt.py:1147-1151` (`occ == 0` → 0; `delta_eue == 0` → 0; VOLL clamped), `sweep.py:604-620` (`in_scope == False` → 0 by design; the sweep refuses VOLL ≤ 0 at `:380-384`), `stress.py:644-646`; `copt_endpoint.py:87-90` forwards `delta_eue_mwh` / `note`; `FmeaTab.tsx:260` prints `fmtCurrency` only; FE `WorksheetRow` (`pages/results/fmea.ts:44-61`) has `delta_eue_mwh?` and no reason.
- **Fix (D-5).** Additive `zero_reason: 'no_shortfall' | 'no_outage_data' | 'unpriced' | 'out_of_scope' | null` on every `per_mode` row, computed by one helper in `BE/services/adequacy/worksheet.py` (existing module; `unpriced` reachable on the copt path only); FE shows the reason instead of `€0.0`; `ResultsCard`'s "no measurable cost" (`ResultsCard.tsx:90`) reads the same field so hub and tab cannot disagree. Exports unchanged. **Size:** M.

#### B4. Obstacle 10: the tour popover covers its target or overflows the viewport
- **Evidence.** `GuidedTour.tsx:194-206` guessed height (180 / 192 px), fixed width 320, no re-measure. **Fix.** Measure the popover (`ResizeObserver`), place below / above / right / left by fit, clamp to the viewport, never intersect the target, `max-height: calc(100vh - 16px)`. **Tests.** `GuidedTour.test.tsx` with mocked rects. **Size:** S.

#### B5. Obstacle 11: `gen_zero_costs` on the data-center template
- **Evidence.** `validation_service.py:1749-1764` reads static columns only; `eh_templates.py:112-114` `grid_supply` (static 0 + `generators_t.marginal_cost`), `:140-141` `rooftop_pv` (fixed, marginal 0) — exactly the two flagged. No test pins `gen_zero_costs`.
- **Fix (D-6, review condition 9).** Exempt (i) generators with a `generators_t.marginal_cost` column and (ii) generators with a `generators_t.p_max_pu` column (variable renewables). Keep the warning for everything else, including fixed dispatchable units at zero marginal cost (still an indeterminate dispatch). **Tests.** `test_validation_gen_costs.py` (new): series marginal cost → no warning; profiled PV → no warning; fixed dispatchable all-zero, no series → warning stays; all three templates → no `gen_zero_costs`. **Size:** S.

#### B6. Obstacle 12: the New-project dialog shows a save path local mode does not use
- **Evidence.** `NewProjectWizard.tsx:209-211` hard-coded; real root `app_paths.py:67-70`; `GET /api/local-settings` (`local_settings.py:106`, `_state()` `:58-70`) already returns `log_path` and can carry `projects_root`. **Fix (D-7).** `projects_root` on `/local-settings` (local mode only); the wizard shows it, nothing in hosted mode. **Size:** S.

#### B7. The tour drops optional steps for good
- **Evidence.** `GuidedTour.tsx:104-106,125-127`. **Fix.** Keep all steps; skip an optional step whose target is absent at the moment it would show; "n of m" from currently visible steps. **Size:** S.

#### B8. Hub load-error line names the wrong failure; Run with the default pack; quiet failures unlogged
- **Evidence.** `HubDesignPanel.tsx:112-116`; `client.ts:202-210` (`skipErrorToast` skips `appLog` too). **Fix.** Name what failed, disable Run study while the template read is in error, log quiet failures at INFO. **Size:** S.

#### B9. Mode switch accessibility — `AppHeader.tsx:1040-1075`, `title` only. **Fix.** `aria-describedby` to a visually hidden sentence per button. **Size:** S.

#### B10. `propertiesEditRequest` is unscoped — `uiStore.ts:426,595,776-777`; consumers `FE/layout/PropertiesPanel.tsx:1195,1644`; pinned in `FE/layout/PropertiesPanel.editRequest.test.tsx`. **Fix.** `{type, name}` and clear on selection change. **Size:** S.

### C. Cosmetic and test-only

| # | Item | Evidence | Fix | Phase | Size |
|---|---|---|---|---|---|
| C1 | A reloaded Improve request loses "(step i of N)" | `ChatPanel.tsx:1363-1368` | **Accept** (D-11): recorded as a known cosmetic | — | — |
| C2 | The "created from template" toast covers Send | `FE/main.tsx:47` bottom-right | `containerStyle` offset while the assistant dock is open | P31 | S |
| C3 | `fmtEnergy` renders `0.00 kWh` under 1 MWh | `FE/pages/results/shared.tsx:743-749` | below 1 MWh keep MWh (3 decimals), zero → "0 MWh"; unit back in the header | P31 | S |
| C4 | Vacuous smoke check "no further failing requests" | `smoke-guided.mjs:1162-1164` | `page.on('request')` counter on `/api/results/eh_study` ≥ 500; `--self-test` injects one 500 **after** recovery and expects FAIL | P31 | S |
| C5 | `/eh_review` docstring 204 wording | `BE/routers/results.py:1523-1526` | correct the docstring | P31 | S |
| C6 | Guided greeting opens with "Not solved yet." | `ChatLaunchGreeting.tsx:88-89` | Guided: "No study has run yet — start in Hub design."; Expert unchanged | P28 | S |
| C10 | Multi-tab: no `storage` listener | `uiStore.ts:647-660` | `storage` event → adopt the other tab's explicit choice | P28 | S |
| C11 | Survived mutants R8 (`dtc_planning` rule) and R15 (header Run/Abort shown in Guided while queued / running); Q3–Q5 (`resultsApi` quiet flag) | P24-FE re-gates | test-only: `plainWords.test.ts`, `AppHeader.uiMode.test.tsx`, `resultsApi.test.ts` (new) | P30 | S |
| C12 | "Critical demand unserved … keeps MWh"; Goal VOLL line spacing / no hover on "MWh" | P24-FE re-gate | `plainWords` rule for `MWh` in effects; `Term k="mwh"` on the Goal line | P30 | S |
| C13 | Gate reporting hygiene: the implementer's test-count report named no command (P25 note 5) | P25 | every gate row in every phase note states the exact command and cwd | all | — |

### E. Accepted, not fixed (recorded per §8.3)

| # | Item | Evidence | Why accepted |
|---|---|---|---|
| E1 | Same-name re-create latch: a deleted and re-created project keeps the derived store | P24-FE re-gate 2 note 7 | pre-existing guard condition (`storeProject === project`), no Guided path reaches it |
| E2 | `unpriced` zero reason only on the copt path | B3 | the sweep refuses VOLL ≤ 0 (`sweep.py:380-384`); the helper documents it |

### D. Non-goals (spec §1) — D1 decided and scheduled (P32); D2–D3 optional, not scheduled

#### D1. Chat-created projects do not switch the mode
- **Evidence.** §10: "the assistant is already the Guided surface". A8 delivers the `project_rebound` frame for the creating tools either way; D1 is only the FE `noteNewProjectCreated('template')` on that frame.
- **What choosing "yes" means (review condition 11).** (a) Spec §1 (non-goal list) and §3.4 are amended — a §10 addendum is **required**. (b) An implicit-Expert user (an existing user with app keys, §3.2) who asks the assistant for a template is flipped to Guided mid-conversation and loses their open panels (§3.7 hiding). Only an explicit choice is respected.
- **Options.** (a) yes, via the A8 frame + `noteNewProjectCreated` when `via_tool ∈ {create_project_from_template, import_project_bundle}`; (b) keep the non-goal.
- **Owner decision:** D-8 — **decided (a), 2026-09-28** (product owner: "Yes"). Projects the assistant creates or imports in chat switch to Guided unless the user made an explicit mode choice. This needs a spec §1/§3.4 amendment (§10 addendum in the deferred spec). P32 is scheduled directly after P27a, because it depends on A8's `project_rebound` frame.

#### D2. Non-EH workflows are Expert-only — keep; a second flow needs its own spec (D-9).

#### D3. Mode stored per browser, not per user — keep for local mode (D-10).

## 3. Phases

Every phase runs the parent spec §8 gate unchanged: row 1 full backend suite (`-m "not slow"`), **row 2 the superset** used by earlier gates (the seven spec files plus `test_eh_review_route.py`, `test_energy_hub_tagging.py`, `test_golden_coverage.py`, `test_results_range.py`, `test_chat_tools_schema_panels.py`, and the phase's own additions named in the spec), row 3 `tsc`, row 4 full vitest, stress ×10 of the touched suites where the phase changes polling / store / chat, row 5 the browser smoke phase named below (each new `--phase` **extends** its base and keeps the base steps), row 6 an independent QA-gate review with verdict GO before the next phase, row 7 the Expert-unchanged check. Every gate row in a phase note states the exact command and cwd (C13).

| Phase | Items | Why this order | Smoke extends | Size |
|---|---|---|---|---|
| **P27a — Backend data integrity** | A1 (lock, live-study edit refusal, chat cover), A8 (rebinding tools) | The only phase that changes what the backend writes or refuses | `P27a` = P22.9 + edit-during-sweep (HTTP 409, plain toast) + P25 stub branch "template from chat" then autosave (no 409, second tool in the turn runs) | M |
| **P27b — Frontend integrity and coverage** | A2 (mismatch banner, client-side write block), A5 (FMEA cache, switch resets), A6 (mid-study switch), A1's Guided button gating | Builds on P27a's refusal shape | `P27b` = P22.9 restart step (banner, stale PUT refused) + P26 mid-study switch + settled FMEA counts | M |
| **P28 — Honest state** | A3 (profiles readiness, bound profile), A4 (stale review, `fresh` fallback), C6, C10 | The owner's honesty rule; touches the send gate | `P28` = P26 + P22.9 send-gate step (second ready profile, bind by one turn, reload → Send enabled) | M |
| **P29 — Guided chat and risk table** | B1, B2, B3 | The two Expert surfaces Guided links to | `P29` = P26 (Guided FMEA title, no engine badges, `genset_1` reason text; no raw `→` lines) | M |
| **P30 — Tours, templates, wizard** | B4, B7, B5, B6, B8, B9, B10, C11, C12 | Friction outside the main loop; all S. If P30 runs long, B5 / B6 / B8 / B9 / B10 move to P31 | `P30` = P24 (tour box ∩ target = ∅, in viewport; templates validate clean) + P22.9 tagging tour (late Link step) | M |
| **P31 — Cosmetics** | C2, C3, C4, C5 | Lowest value, zero risk | `P31` = P26 (toast ∩ Send = ∅) + `--self-test` | S |
| **P32 (scheduled after P27a)** | D1 | D-8 = (a); needs the deferred spec's P32 section in full and a §10 addendum | `P32` = P25 | S |
| **P33 (optional)** | D2 | Needs D-9 and a spec | new | L |
| **P33b — study honesty after an edit (owner-approved, to schedule)** | O1 follow-up (P28 gate): a backend network-revision marker on the EH study record — e.g. `network_changed: bool` computed from a revision counter bumped on every live-network write after the study finished — read by both the Guided greeting and the Hub design Results card, so "the network changed since the study" is said only when it is true | O1 (owner, 2026-09-30): P28 softens the wording now ("The last study’s results are in Hub design."; live `dispatch === 'stale'` → the stale sentence); real tracking is this phase. Additive backend field; one source for both surfaces | `P33b` = P26 + edit one bus after a study → the greeting and the Results card both say the network changed | M |
| **P34 (optional)** | D3 | Needs D-10 | `P34` = P23 | M |

Test-first in every phase: each item's red test is written and seen failing before the fix; changed assertions carry a one-line justification here.

### P27a phase note (implementation, 2026-09-29, base `4d7cfbb86`)

**Anchor drift** (spec §1 anchors were taken before the PR #60 merge; re-verified on `4d7cfbb86`):

| Spec anchor | On `4d7cfbb86` |
|---|---|
| `sweep.py:87-125, 188-196, 279-286, 400-401`; `frontier.py:211`; `coupling_loop_runner.py:625` (lock `:285`); `margin_loop_runner.py:962` (lock `:208`); `dtc.py:396`; `pypsa_service.py:632`; `project_context.py:256, 325, 343`; `network_crud.py:204, 375, 439`; `network_undo.py:105`; `chat_service.py:125-131` | unchanged |
| `routers/projects.py:1515` `_study_in_flight_detail`, callers `:1547`, `:2377` | `:1554`, `:1586`, `:2416` |
| `main.py:267` `_SOLVER_BLOCKING_PREFIXES`, `:642` `is_write`, `:733` bind, `:769-800` solver gate | `:293`, `:668`, `:759`, `:794-826` |
| `chat_service.py:3331-3345` emission; `:4589-4592` contextvars copy; `:4619-4645` dict → `tool_error` | `:3471-3485`; `:4753`; `:4786-4806` |
| `chat_tools.py:2115-2147` `_route`; `:2354-2371` bundle / template tools | `:2082`; `import_project_bundle` `:2322-2339`, `create_project_from_template` `:2342-2347` |
| `projects.py` template `:1264/1342/1351`; bundle `:1059/1068/1139`; save `:1475, 1500-1502`; `_save_context` `:1929` | `create_from_template` `:1298` (session `:1303`, pointer `:1390/:1437`); `import_bundle` `:941` (pointer `:1081/:1174`); `save_project` `:1460`, `was_unbound` `:1514`, pointer `:1540-1541`; `_save_context` `:1798` |
| `eh_study.py:445, 971` (`network.copy()`) | `:554`, `:1189` |
| `ChatPanel.tsx:2340` rebind toast | `:2342` (handler `:2322-2350`) |

**Mechanism drift found while implementing.** (1) `cascade_delete_bus`, `bulk_update_components`, the Bus rename and the GlobalConstraint handlers do **not** route through the three `network_crud` handlers (`network_buses.py` `apply_delete_bus_cascade` / `apply_rename_bus`, `network_bulk.py` `apply_bulk_update`, `network_global_constraints.py`), so each calls `study_state.refuse_edit_during_live_study()` itself — without that, the spec's parametrised chat test stays red for those two tools. (2) `list_components` requires `component_class`, so stub branch 7's second call is `list_components {"component_class": "Bus"}` (the spec's `{}` would come back as a `tool_error`). (3) `QUIET_TOAST_CODES` in `FE/api/client.ts` **already** contains `study_in_flight` on master, so the interceptor never toasts the sentence; the Properties card's own `onError` toasts `Update failed: ${e.message}` (axios' "Request failed with status code 409"). The smoke therefore shows exactly one toast, but not the "is running" sentence — a frontend change, handed to P27b (A1-FE). (4) The `tool_error` frame's `message` is `str(detail)` (it contains the sentence); the model-facing `tool_result` content is `study_in_flight` + the fenced sentence (`_error_result_content`). The seam test asserts both. (5) `dtc.py` has a `lock` in scope, so "passing the lock" is no signature error and no DTC test sees it: pinned structurally instead (`test_dtc_freezes_its_private_copy_without_the_lock`).

**Changed assertions (one line each).** `test_adequacy_study_swap_guard.py::test_the_refusal_names_the_study_and_only_offers_a_real_remedy`: for the four live-network keys `POST /api/network/reset` now meets the middleware's `study_in_flight` dict before the swap guard's string (spec §1.1 "HTTP chokepoint", stated for undo; reset is the same prefix) — the test reads `detail.message` when the detail is a dict. `test_edit_after_a_sweep_is_kept`: "dispatch reads as before" is asserted as "every other bus / link / generator row reads as right after the sweep"; `/simulation/status.dispatch` goes `fresh` → `none` after any `/api/network/*` write (the undo middleware invalidates it), with or without a sweep. `test_adequacy_abort.py::test_F1b*` (two cases): their `freeze_capacities` stub takes `(n, lock=None)`, the new signature the sweep now calls it with.

**Gate NO-GO fixes (`docs/superpowers/qa/2026-09-29-guided-mode-deferred-gate-P27a.md`).** *B1:* the chat tools call their handlers in process, so the three CRUD chokepoints covered only part of the write surface; `update_meta`, `set_snapshots`, `set_snapshot_weightings`, `set_multi_period_snapshots`, `set_investment_periods`, `upload_timeseries`, `upload_{load,generator,link}_profile`, `generate_exemplary_timeseries` and `delete_timeseries` changed the live network mid-sweep. Fixed at the dispatch seam: `chat_tools._study_gated_tool_names()` is derived from the foreign-lock gate's set (`_lock_gated_tool_names()`), narrowed to `/api/network/` + `/api/io/` write routes plus the routeless mutators, minus the tools that swap the whole network (the four imports and `cluster_network`, which keep the swap guard's sentence over every study); `_study_gated` calls `refuse_edit_during_live_study()` before the handler. **Deviation from the gate note:** `delete_timeseries` is gated, not left out as a "swap tool" — it drops `_t` columns in place and has no swap guard (the derived test showed it changing the network mid-sweep). `undo_last` now meets the dict before the swap string on the chat path too. `_CHAT_WRITES` is replaced by `_derived_chat_writes()` (same rule, computed in the test) with the reviewer's probe arguments per tool; `test_the_seam_study_gate_is_exactly_the_derived_set`, a read-tools-stay-allowed test and a swap-tools-keep-their-guard test (`import_network_nc` during a sweep → 409 with the swap *string*) prove both sides. Red before the fix: 21 of 29 derived cases (11 changed the network, counting `set_snapshot_weightings` with ISO keys; the rest were not refused or answered with another error). *Finding 1:* AST pin `test_the_live_study_call_sites_pass_the_runners_lock` (sweep ×2, frontier, coupling, margin). *Finding 2:* `test_the_handler_chokepoints_refuse_a_bare_call_during_a_sweep` (GC create / update / delete, bus rename, cascade, bulk — bare calls that bypass `DISPATCHERS`). *Finding 3:* the four network imports join `PROJECT_REBINDING_TOOLS`; they unbind (`to: null`), announced only on an actual move; the FE handler (`ChatPanel.tsx:2322`) ignores a null `to` for the store and only appends the tool line — whether the tab should also drop its `currentProject` is FE work for P27b/P32. *Finding 5:* the smoke's success line now reads "the browser save was sent while the sweep ran, and was refused". Findings 4 (check-then-act window, same shape as the solver gate) and 6 (a swallowed bind falls back to the foreground ctx) are recorded, not changed.

**Smoke P27a** (`scripts/smoke-guided.mjs --phase P27a`, base P22.9): after P22.9, a second Expert context puts the bus in Edit from the canvas, starts the B/C sweep from the FMEA tab, `PUT /api/network/buses/<bus>` → 409 `study_in_flight`; Save in the Properties card → exactly one toast (text: see drift 3), the user's value does not land (mid-sweep the row shows the solve's own `control`, P22.9 bug 3), and after the sweep the row equals the pre-sweep row and the same edit succeeds ("Bus updated"). Branch 7: confirmation card → approve → toast `Active project: <name>`, `meta.loaded_project === <name>`, `call_stub_7b` returned a result, no `project_switched_mid_turn`, Ctrl+S → `POST /api/projects/<name>` 200, no 409 in the console. The dock follows the rebind to the new project's own (empty) chat, so the rebind is read from the toast, not the transcript.

## 4. Owner decisions

| # | Question | Options | Recommendation (author) | Reviewer |
|---|---|---|---|---|
| D-1 | A1: edits during a live-network study | (a) refuse with `study_in_flight` 409 + restore under the passed lock; (b) merge-restore | **(a)**, scope = `fmea_sweep`, `frontier`, `coupling_loop`, `margin_loop`; HTTP + chat chokepoints; Guided buttons gated | agrees |
| D-2 | A2: bound-project mismatch after a restart | (a) blocking banner, client-side write block, reload / adopt; (b) silent auto-reload | **(a)**, `loaded_project` from `/network/meta` (no new route) | agrees |
| D-3 | A3: bound-profile transport | (a) `bound_profile_id` on `/history`; (b) per-profile `chat_ready` on `/chat/profiles` + (a); `/health` byte-stable | **(b)** | agrees (carrier changed) |
| D-4 | B2: Guided FMEA tab | (a) Guided variant, Expert snapshot-identical; (b) separate table | **(a)** | agrees |
| D-5 | B3: zero-severity explanation | (a) backend `zero_reason` + FE text, exports byte-stable; (b) FE inference | **(a)** | agrees |
| D-6 | B5: `gen_zero_costs` | (a) exempt series marginal cost and profiled renewables only; (b) template costs | **(a)** | agrees (rule narrowed) |
| D-7 | B6: save path | (a) real root from `/local-settings` in local mode; (b) drop | **(a)** | agrees |
| D-8 | D1: chat-created projects → Guided | (a) yes (spec §1/§3.4 addendum; implicit-Expert users flip mid-conversation); (b) keep the non-goal | **owner: (a), 2026-09-28** | leaned (b); owner overrode |
| D-9 | D2: second Guided workflow | (a) not now; (b) spec one | **(a)** | agrees |
| D-10 | D3: server-side mode preference | (a) keep per-browser; (b) hosted preference | **(a)** | agrees |
| D-11 | C1: "(step i of N)" after reload | (a) accept; (b) persist `display` | **(a)** | agrees |

## 5. Risks

| Risk | Mitigation |
|---|---|
| P27a's edit refusal surprises an Expert user mid-sweep (HTTP or assistant) | one plain sentence ("A risk check is running — wait for it to finish or abort it before changing the network."); `QUIET_TOAST_CODES` keeps it to one toast; the assistant gets the same sentence in `tool_error.message`; Guided buttons are disabled with the same title |
| P27b's mismatch banner blocks a tab that is right after all | the banner reads `loaded_project` live and clears itself when the backend rebinds to X; "Reload X" is one click; visible in Expert too |
| P28 loosens the send gate on a wrong readiness answer | readiness comes from the backend per profile; fail-open only when unknown |
| P29 changes FMEA markup Expert users rely on | `FmeaTab.expertUnchanged` snapshot taken on the P28-GO commit, byte-compared as in the P23 gate |
| B3 touches three engines | additive field only; invariant suites and golden tests stay green; no number changes |
| **Prompt cache** | no phase P27–P31 touches `chat_tools_schema.TOOLS`, any tool description, or the system block (`test_guided_mode_prompt.py::test_expert_block_is_byte_equal_to_no_mode` and its siblings stay green). B3 changes tool-result **bodies** only (one additive key, within the per-turn result budget). A8 changes a frozenset in `chat_service.py`, not a prompt. If P32 ever adds a description sentence it costs one cache miss and must say so. |
| **Packaging** | no new backend module in any phase: B3's helper goes into `services/adequacy/worksheet.py`, B2's keys into the already-rooted `data/guides/eh_fmea_guide.json` (`smoke/check_bundle.py:172` `ROOTED`), B6 reads `app_paths` (packaged), A1's detail helper moves within existing modules. `check_bundle.py` is the gate for any new file. |
| Smoke growth | phases stay independent (`--phase P27a` runs base + extension); run time recorded per gate |

## 6. Review conditions applied

| # | Condition | Applied where |
|---|---|---|
| 1 | A8: `import_project_bundle`, corrected pointers, same-turn red test, `save_project` decision | A8 (all three tools added; frame only on an actual move); spec §1.2 |
| 2 | A1 lock: the runner's passed lock, five call sites | A1; spec §1.1 |
| 3 | A1 chokepoint: HTTP + chat, live keys named, undo | A1 (undo already guarded at `network_undo.py:105`); spec §1.1 |
| 4 | A2: block writes; `loaded_project` on `/network/meta`, no `/projects/active` | A2 (no backend change needed — the field exists); spec §2.1 |
| 5 | Split P27 → P27a / P27b, row-2 additions | §3; spec §1.5, §2.5 |
| 6 | Register completeness | C11, C12, C13, E1, A4, A5; §1 last rows (margin-loop test already present) |
| 7 | A1 assistant and Guided exposure | A1; spec §1.1, §2.4 |
| 8 | A4 `fresh` fallback | A4; spec §3.2 |
| 9 | B5 exemption rule | B5; spec §5.3 |
| 10 | Prompt-cache and packaging rows | §5 |
| 11 | D-8 presentation | D1 / D-8 |

**Non-binding suggestions:** all adopted (A3 carrier `/chat/profiles`; A2 smoke asserts the refused PUT; A5 `finalising` fallback; B3 helper in `worksheet.py` + hub reads the field; B2 snapshot timing; C4 self-test after recovery; corrected test paths; row-2 superset). **Rejected:** none. **Pushback:** review §2 item 1 (missing margin-loop HTTP test) is already satisfied by `test_live_network_untouched.py:243`.

## P27a result: GO at re-gate on `5751e2b82`, 2026-09-29

- **First gate: NO-GO (B1).** Eight write-tier chat tools changed the live network during a sweep, even though the same HTTP routes returned 409.
- **B1 fix.** The chat tools now pass through a derived seam gate, `_study_gated_tool_names()`. It gates 29 tools and is derived from the foreign-lock seam, so tools added later are covered automatically. `delete_timeseries` is in the gate; the four imports and `cluster_network` keep their swap guard. Findings 1, 2, 3 and 5 are also fixed; finding 3 adds the four network imports to `PROJECT_REBINDING_TOOLS`.
- **Evidence at the re-gate:**

  | Check | Result |
  |---|---|
  | Full backend suite (`-m "not slow"`) | 6660 passed, 31 skipped, 11 deselected, 0 failed; `--collect-only` matches HEAD |
  | Row 2 | 1626 passed |
  | `tsc` | clean |
  | Smoke P27a | PASS |
  | Smoke P26 | PASS |
  | Reviewer's probes | 21/21 get 409 and leave the network unchanged |
  | Mutants | 14/14 killed |

- **Gate file:** `docs/superpowers/qa/2026-09-29-guided-mode-deferred-gate-P27a.md`.
- **Carried to P27b:**
  - The Properties card's refusal toast should show the backend sentence instead of "Request failed with status code 409".
  - ChatPanel should handle a `project_rebound` frame with `to: null` (a network import unbinds the tab), with a vitest.
- **Still on record:** the small race before a sweep publishes its study record, and the middleware's fallback when request binding fails.


### P27b phase note (implementation, 2026-09-29, base `88aabcfe0`)

**Anchor drift** (spec §2 anchors were taken before the master merge and P27a; re-verified on `88aabcfe0`):

| Spec anchor | On `88aabcfe0` |
|---|---|
| `network_crud.py:124-141` `_meta_payload`, `routers/network.py:601-604`; `types.ts:178`; `client.ts:130-136`; `uiStore.ts:408`; `Sidebar.tsx:730, 765, 846-861, 954, 984, 1031`; `api/projects.ts:284`; `api/chat.ts:104`; `ChatPanel.tsx:2322` (rebound), `:3195` (`chat-send-gate`); `TopologyCanvas.tsx:2106`; `uploads.ts:81-136`; `CardShell.tsx:33`; `SiteCard.tsx:76`; `GoalCard.tsx:124-130`; `blockerMessage.ts:14`; `useCreateFromTemplate.ts:19`; `FmeaTab.tsx:60-66` | unchanged (±1: `_meta_payload` `:125`) |
| `App.tsx:446-481` recovery effect | `:447-485` |
| `ChatLaunchGreeting.tsx:96-101` meta key | `:98-103` |
| `projectActions.ts:407` activate, `:433` `setCurrentProject` | `:463`, `:489` |
| `ImproveCard.tsx:103-115` | `:102-111` |
| `useStudyFinishedInvalidation.ts:21-31` | `:19-31`; its test file **already exists** as `useStudyFinishedInvalidation.test.tsx` (JSX wrapper) — the new cases go there, not into a new `.test.ts` |
| `projects.py:2476` `GET /{name}`, `:2377` load refusal "with `study_in_flight`" | `:2493`; the load route refuses with `refuse_if_study_running("load a project")` (`:2521`), a **string** detail (the swap sentence), not the `study_in_flight` dict (`:2416` is `activate`). The banner shows either shape through `blockerMessage`; `App.recovery.test.tsx` pins both. |
| `smoke-guided.mjs:96` `PHASES`, `:154-160` `stopAll`, `:1748-1762` settle loop, `:1885-1890` dispatcher | `:107`, `:165-176`, `:1760-1772`, `:2131-2137`; the backend process is named `uvicorn`, so the restart is `stop('uvicorn')` |
| `QUIET_TOAST_CODES` (A1-FE) | already holds `study_in_flight` (`client.ts:83`); the card's `onError` was the one toast |

**What was built** (commits below). A2: `NetworkMeta.loaded_project` (optional in the type — existing mocks omit it; the route always sends it); `uiStore.projectMismatch`; `hooks/useProjectMismatchDetection.ts` (App) — two consecutive settled disagreeing samples, none during `projectSwitchInProgress`, counted by the query's `dataUpdateCount` and with `structuralSharing: false` so two samples in one millisecond are still two; meta polled every 5 s, 1 s while a first disagreeing sample waits (`mismatchPoll`, a test seam); a study tab (planning → dynamics) and a tab with no project never raise it; the `bus_count === 0` reload no longer runs over another binding. `components/ProjectMismatchBanner.tsx` (Reload / Switch; the Reload refusal under the button; Switch sets the `projectSwitchInProgress` fence like the other entry points). `utils/projectMismatch.ts` (sentence, allowlist); the axios request interceptor (`client.ts`) rejects with an `AxiosError` whose `response` is the 409 shape (it then passes the response interceptor, which logs it quietly; one `console.warn('[project_mismatch] …')` per refusal is what the smoke counts). Chat Send gate; card requests are **dropped** with a toast while mismatched (as for `notReady` — "holds" would fire them into whichever project a later Switch lands on). `pendingEdgeDeletes`: `flushPendingEdgeDeletes` and the pagehide keepalive (moved out of `TopologyCanvas` into `keepaliveFlushPendingEdgeDeletes` so it is testable) drop with one WARN. Sidebar: `guardProjectMutation`, the Save title, the identity 409 raising the mismatch, and the `String(detail)` fix. **`project_rebound {to: null}`** (P27a finding 3): the tab clears `currentProject` (the unbound state — Save asks for a name) with one toast. Reason: the save identity guard lets an unbound network through (`expect != loaded` only when `loaded is not None`), so keeping the old name would let the next autosave write the import over the old project's folder. A5: `fmeaModesRefetchInterval` (a `WeakMap<Query, {running, extraAt}>` latch — per **sample** (`dataUpdateCount`), not per call: React Query re-evaluates `refetchInterval` on every observer render, and a second evaluation returning `false` would cancel the pending extra tick); the Improve card's follow-up is bound to the project its success belongs to (a ref, so the first render after a switch already stops following) and `sweep.reset()` runs on a project change; `useStudyFinishedInvalidation` keeps its previous sample per project. A1-FE: `hooks/useLiveStudyRunning.ts`; `DelegateButton` `disabled` / `disabledTitle`; Site fixes, Goal VOLL fix, Improve finding buttons and the Site / Improve footer delegates (their requests edit the network) are disabled with `LIVE_STUDY_EDIT`, which also shows as a one-line note (`hub-live-study-note`) on the Site, Goal and Improve cards; every PropertiesPanel error toast goes through `blockerMessage`. A6: `projectActions.switch.test.ts` hub-query case.

**Deviations.** (1) The spec's ImproveCard switch case says "no `fmea_modes` fetch for the new project"; with A1-FE the card reads the project's modes once through `useLiveStudyRunning`, so the case asserts "at most one read, never a poll" (and the reset, and the "Started" line gone). (2) Changed assertion, `ImproveCard.test.tsx` "no sweep started from here → the modes are not polled": `not.toHaveBeenCalled()` → at most one read over 10 s of fake time (same reason). (3) The ImproveCard test's `useStartFmeaSweep` mock now spreads the real module (`importOriginal`) so the shared interval is real. (4) `useStudyFinishedInvalidation.test.ts` is not new: the cases went into the existing `.test.tsx`. (5) `stop('uvicorn')`, not `stop('backend')` — the process name the script already used. (6) (c) FMEA counts: the settle loop stays the smoke's assertion (spec §2.2: replaced only after ten stable runs); P27b logs a one-shot read after the extra tick for the gate's `finalising` decision. (7) No backend change.

**Smoke findings fixed during the phase.** (1) The first P27b run showed the banner after 9.9 s: React Query computes `refetchInterval` when a sample lands, before the hook's effect records it as pending, so the 1 s confirmation never applied. The interval now reads the sample itself (banner after 6.3 s; `App.recovery.test.tsx` "re-checked on the short confirm interval" pins it, red without the fix). (2) P22.9 and P26 both create the templates under their default names (409 "already exists" for the second): P27b renames the P22.9 data center (`P27b Data Center`, the tab's project) before P26, and P26's H2 hub before (b) re-creates it. (3) Editing `scripts/smoke-guided.mjs` during a run makes Vite reload the page (`[vite] page reload scripts/smoke-guided.mjs`) — one aborted run; not a product defect.

**Red → green and mutants.** Each test below was seen failing for the stated reason before its fix: `client.mismatch.test.ts` (7 of 15 red: writes left the tab), `App.recovery.test.tsx` (7 of 9 red: no detection), `Sidebar.autosave.test.tsx` (4 of 5 red: save posted, no mismatch from the 409, no title, "[object Object]" path), `ChatPanel.sendGate.test.tsx` A2 (3 red), `pendingEdgeDeletes.test.ts` A2 (2 + 1 red), `ChatPanel.rebound.test.tsx` (1 red: `currentProject` kept), `useStudyFinishedInvalidation.test.tsx` A5 (2 red), `ImproveCard.test.tsx` A5 (2 red: 2 reads not 3; no reset), `SiteCard` / `GoalCard` / `ImproveCard` A1-FE (4 red), `PropertiesPanel.save.test.tsx` (1 red: "Save failed: Request failed with status code 409"). `FmeaTab.test.tsx` A5 and the A6 switch case are coverage (green on arrival; their mutants below are killed). Mutation run on the final HEAD (`scratchpad/p27b/mutate.py`, log `mutations-final.log`; each mutant applied to the source, the named suites run, the source restored; the Properties-toast mutant was applied by hand, its anchor is not unique): **29 / 29 killed** — detection `===` dropped; two-sample rule dropped; `projectSwitchInProgress` ignored; allowlist emptied; `put` not a write; interceptor not applied; chat gate dropped; Sidebar `String(detail)` back; autosave guard ignores the mismatch; identity 409 does not raise it; pending deletes ignore it; keepalive path bypasses it; extra-tick latch removed; latch per call instead of per sample; FmeaTab inline interval (import identity); ImproveCard follow not project-scoped; ImproveCard no reset; `useStudyFinishedInvalidation` ignores the project; live study never running; Site / Goal / Improve buttons not disabled (3); Properties toast back to `e.message`; null rebound ignored; hub study key not project-scoped (A6); Reload error not shown; recovery reloads over another binding; study tab not exempt; confirm interval from the pending ref.

**Gate rows** (all from `pypsa-gui/frontend` unless stated):

| Row | Command | Result |
|---|---|---|
| 2 | `PYTHONPATH=/home/user/pypsa-eur:/home/user/pypsa-eur/pypsa-gui/backend /tmp/claude-0/venv/bin/python -m pytest <the 14-file set + tests/test_chat*.py + test_chat_tool_dispatch_loop_seam.py test_save_guards_seam.py test_adequacy_study_swap_guard.py test_adequacy_swap_guard_callsites.py test_stub_openai_endpoint.py; 70 files> -p no:cacheprovider -W ignore -q -o addopts=""` (`pypsa-gui/backend`; no backend change) | 1598 passed, 19 skipped, exit 0 (`scratchpad/p27b/row2.log`) |
| 3 | `npx tsc --noEmit -p .` | 0 errors |
| 4 | `npx vitest run` | 238 files, 2668 passed (baseline on `88aabcfe0`: 234 files, 2612 passed) |
| 4s | `for i in $(seq 10); do npx vitest run src/App src/pages/hubDesign src/pages/results/FmeaTab src/hooks/useStudyFinishedInvalidation src/api/client src/layout/Sidebar src/components/ChatPanel src/utils/pendingEdgeDeletes src/utils/projectActions.switch src/layout/PropertiesPanel.save; done` | 10 / 10 green (51 files, 646 tests each) |
| 5 | `PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers node scripts/smoke-guided.mjs --phase P27b --out …/scratchpad/smoke27b` | PASS, 54 screenshots; banner after 6.3 s; (c) one-shot reads 8 / 4 / 6 = settled 8 / 4 / 6 |
| 5 | same, `--phase P27a` / `P26` / `P25` / `P22.9` | PASS (14 / 38 / 12 / 10 screenshots). P27a now asserts the refused-save toast carries "is running" (the P27a deviation 3 closed) |
| 7 | `git diff 88aabcfe0 -- 'pypsa-gui/frontend/src/**' \| grep -c uiMode` | 3: two test-store setups and one unchanged context line (`DelegateButton`'s `const mode`); no new `uiMode` branch; `*.expertUnchanged.*` snapshots green in row 4 |

**For the gate (spec §2.2 fallback).** One run of one-shot reads after the extra tick matched the settled counts on all three templates; the settle loop stays until ten runs in a row agree, so the backend `finalising` value is not needed on this evidence. The (b) `fmea_modes` check: project 1 had 4 rows, project 2 (fresh H2 hub) has none of them (0 rows, no class B/C).

**Gate NO-GO fixes** (`docs/superpowers/qa/2026-09-29-guided-mode-deferred-gate-P27b.md`):

- **B1, the edit lock in auth mode.**
  - `mismatchAllows` lets `POST|DELETE /projects/<x>/lock` and `POST /projects/<x>/lock/heartbeat` through. They move lease metadata, not the live network. Longer lock-like paths stay refused.
  - Reload re-acquires the lock the tab last held. `projectActions.lastHeldLockProject()` records it: it is set on acquire, survives a lost heartbeat, and is cleared on release.
  - The gate's `probeLock` / `probeHeartbeat` are adopted as `components/ProjectMismatchBanner.lock.test.tsx`. Its cases are: Switch lands writable, the heartbeat keeps the lock across a mismatch and Reload, Reload re-acquires a lost lock, and Reload claims no lock the tab never held. They were red before the fix.
  - `client.mismatch.test.ts` gains the three lock routes and a refused longer path.
- **Note 6, fixed.** A confirmation card's Approve is disabled while mismatched, with the banner sentence on the card (`chat-confirm-mismatch`) and as its title; `onApprove` also returns early. Deny stays available.
- **Note 1, fixed.** The 1 s interval applies only to a disagreeing sample that can still raise the mismatch. A raised mismatch, a study tab and a switch in flight poll at the idle 5 s. Pinned counts (`hooks/useProjectMismatchDetection.test.tsx`, fake timers, real defaults):

  | Tab state | Reads | Before the fix |
  |---|---|---|
  | Agreeing, 60 s | 13 | 13 |
  | Mismatched, 60 s | 14 (0, 1, 2 and then every 5 s) | 14 |
  | Study tab, 59 s | 12 | 61 in 60 s |
  | Switch stuck in flight, 59 s | 12 | 61 in 60 s |

- **Note 2, fixed.**
  - The distinct-sample guard (`p.at !== seq`) is pinned by a failed refetch between two samples: the effect re-runs on the same sample and must not treat it as the second.
  - `structuralSharing: false` is **dropped**. The effect keys on `dataUpdateCount`, and the option only re-rendered every meta observer. `App.recovery` and the hook test pass ×10 without it.
- **Note 8, fixed.** `projectSwitchInProgress` mirrors a depth counter (`projectSwitchDepth`, never below 0), so overlapping switches keep the fence until the last one finishes (`store/uiStore.switchFence.test.ts`).
- **Mutants for these fixes: 11 of 11 killed** (`scratchpad/p27b/mutate2.py`, log `mutations-regate.log`):
  - N1: the lock routes are not allowlisted.
  - N2: the heartbeat is not allowlisted.
  - N3: Reload does not re-acquire the lock.
  - N4: Reload re-acquires unconditionally.
  - N5: Approve is not gated.
  - N6: a study tab keeps the fast poll.
  - N7: a switch in flight keeps the fast poll.
  - N7b: a raised mismatch keeps the fast poll.
  - N8: the sample guard is dropped.
  - N9: the fence is back to a boolean.
  - N10: there is no confirm re-check.
- **Auth-mode smoke: not added.** The harness starts uvicorn in local mode (`PYPSAGUI_LOCAL_MODE=1`), where there is no user, no login and no edit lock. An auth-mode step would need a hosted backend configuration, a database with a seeded user and a scripted login in each context: a new harness mode, not a step. The lock path is covered at the axios/adapter level by `ProjectMismatchBanner.lock.test.tsx`, with the real client, the real `switchToProject` and the real lock code.

**Accepted limitations (gate notes 4, 5, 7).**
- **The ~6 s detection window.** Until two samples agree on the disagreement (5 s idle + 1 s confirm; the smokes measured 5.5–6.3 s), edits from the stale tab still land in the backend's project. Saves are caught by the backend identity guard.
- **A mismatched tab shows the backend's data under its own project name.** Reads are never blocked. The banner says the tab is paused.
- **Guided buttons learn about an external sweep on the next `fmea_modes` fetch.** An example is a sweep started by the assistant; the fetch happens on mount or focus. Until then the buttons stay enabled, and P27a's 409 refuses the edit.

**Re-gate evidence** (`pypsa-gui/frontend`):

| Check | Result |
|---|---|
| `npx tsc --noEmit -p .` | 0 errors |
| `npx vitest run` | 241 files, 2686 passed |
| Stress ×10 (`src/App src/pages/hubDesign src/pages/results/FmeaTab src/hooks/useStudyFinishedInvalidation src/hooks/useProjectMismatchDetection src/api/client src/layout/Sidebar src/components/ChatPanel src/components/ProjectMismatchBanner src/utils/pendingEdgeDeletes src/utils/projectActions src/layout/PropertiesPanel.save src/store/uiStore`) | 10 of 10 green, 59 files, 736 tests each |
| Smoke `--phase P27b` | PASS, 54 screenshots; banner after 6.0 s; (c) 8 / 4 / 6 = settled |
| Smoke `--phase P27a` | PASS, 14 screenshots; the toast carries "is running" |
| Smoke `--phase P26` | PASS, 38 screenshots |

No processes are left running. There is no backend change, so row 2 stands at 1598 passed.

## P27b result: GO at re-gate on `1edf4209c`, 2026-09-29

- **First gate: NO-GO, blocker B1 (auth mode).** The mismatch write block also refused the edit-lock routes, so Switch or a heartbeat left the tab read-only as "locked-by-user".
- **Fixed:**
  - The three lock routes are allowlisted.
  - Reload re-acquires a lock the tab held.
  - Chat Approve is gated while the tab is mismatched.
  - Meta polls fast only inside the confirm window.
  - The distinct-sample guard is pinned by a test.
  - The switch fence is a counter.
- **Evidence at the re-gate:**
  - `tsc` clean, vitest 241 files / 2686 passed, stress ×10 green.
  - Smokes P27b, P27a and P26 PASS.
  - Mutations: 15 of 16 killed. The one survivor is harmless (it lets any verb on `/lock` through, and the backend answers 405).
  - The reviewer's steal probe shows Reload cannot take a lock another user holds.
  - The backend is unchanged since P27a (6660 passed).
- **Recommended, not gating:** a two-user, two-tab auth harness alongside P28's multi-tab work (C10).
- **Accepted limitations:**
  - An edit can land during the ~6 s window before a mismatch is detected.
  - A mismatched tab shows the backend's data under its own name.
  - An external sweep reaches the Guided buttons only on their next fetch.
- **Gate file:** `docs/superpowers/qa/2026-09-29-guided-mode-deferred-gate-P27b.md`.

### P32 phase note (implementation, 2026-09-29, base `6135c7601`)

D-8 = (a), deferred spec §7.1. FE only; no backend, prompt, `TOOLS` or packaging change.

**Anchor drift** (spec §7.1 anchors predate the master merge, P27a and P27b; re-verified on `6135c7601`):

| Spec anchor | On `6135c7601` |
|---|---|
| parent spec `2026-09-27-guided-mode.md:41` (§1 non-goal), `:224` (§3.4 last paragraph), `:263-273` (§3.7) | `:41`, `:225`, `:263-273`; the §10 "G4 new projects" row (`:718`) says the same thing and is marked superseded too |
| `ChatPanel.tsx:2322-2345` `project_rebound` handler | `:2338-2383` (P27b added the `to: null` branch); `setCurrentProject(d.to)` `:2352`, toast `:2358` |
| `uiStore.ts:647-660` `noteNewProjectCreated`, `:652-658` explicit re-read | `:662-676`, `:667-673` |
| `uiStore.ts:640-642` §3.7 pruning in `setUiMode` | `:655-658` (`setUiMode` `:644`) |
| `App.tsx:197-208` P23 auto-open | `:200-211` |
| `smoke-guided.mjs:96` `PHASES`, `:1885-1890` dispatcher | `:124`, `:2445-2452` |
| red tests in `ChatPanel.sendRequest.test.tsx` | the rebound handler's tests live in `ChatPanel.rebound.test.tsx` (P27b), whose harness already mocks the toast: the P32 cases went there |

**What was built.** `ChatPanel.tsx` rebound handler, in the named-rebind branch, after `setCurrentProject(d.to)` / `setProjectName` / `touchTab`: `create_project_from_template` → `noteNewProjectCreated('template')`, `import_project_bundle` → `noteNewProjectCreated('file')`. No second explicit-choice rule (the one in `noteNewProjectCreated` re-reads `ui-mode-explicit` from storage). The §10 addendum "D-8 (2026-09-28, product owner)" is in the parent spec, with pointers at §1 (`:41`), §3.4 (`:225`) and the §10 G4 row. Smoke `--phase P32` = P25, then stub branch 7 from the dock in two fresh contexts seeded with the P25 project as `network-diagram:current-project` and `ui-mode = expert` (implicit: no explicit flag → Guided, `hub-card-site`, "Active project: <name>" toast, dock still open, no `project-mismatch` banner 8 s later and no `[project_mismatch]` refusal; explicit: `ui-mode-explicit = 1` → stays Expert, no `hub-card-site`).

**Network imports do not flip (decision, kept to the spec).** P27a added `import_network_nc`, `import_csv_bundle`, `import_excel`, `import_matpower` to the rebinding set; they announce `to: null` and P27b clears `currentProject`. They are not new projects: nothing is created, the tab is unbound, and Guided has nothing to show without a project (the auto-open and the §3.7 swap both need `currentProject`; a flip would close the user's panel and leave an empty Guided shell). It also matches the UI: `ImportExport.tsx:130` calls `noteNewProjectCreated('file')` only for a bundle imported as a NEW project, never for a raw network import. A later Save As of the unbound draft is a save, which the addendum excludes. Pinned by four `it.each` cases.

**The mismatch fence during the rebind.** The flip changes no binding and sends no write; `setCurrentProject(d.to)` runs first, so the detection's meta key is the new project's and its first sample agrees. Pinned in the unit test (`projectMismatch` stays null) and the smoke (no banner 8 s after, beyond the two-sample window). The first P32 smoke run showed the fence doing its job: the explicit context opened the P25 project while the backend was still bound to the implicit context's new project, and the confirmation card's Approve was disabled with the mismatch sentence. The smoke now activates the P25 project through the API before each context (as a user's backend would be); not a product defect.

**Red → green and mutants.** `ChatPanel.rebound.test.tsx` "P32" (15 cases): 6 red before the fix (template, bundle, after-`setCurrentProject`, explicit — the spy was never called —, other-tab explicit, dock + toast), 9 guards green on arrival (saves / opens and the four imports not flipping, no mismatch). Mutations (`scratchpad/p32/mutate.py`, log `mutations.log`): **7 / 7 killed** — call removed (6 fail); kinds swapped (3); bundle branch dropped (2); `save_project_as` also flips (1); explicit rule bypassed with a direct `setUiMode('guided')` (4); flip before `setCurrentProject` (1: the panel became null, not `hubDesign`); a `to: null` import flips (4). `uiStore.uiMode.test.ts` unchanged.

**Deviations.** (1) Tests in `ChatPanel.rebound.test.tsx`, not `ChatPanel.sendRequest.test.tsx` (above). (2) Extra cases beyond §7.1(f): `activate_project` / `load_project` do not flip; an explicit choice made in another tab (storage only) is adopted; the four network imports; no mismatch. (3) The spec's smoke says the transcript shows `Active project: <name>`: that text is the rebind **toast** (the transcript tool line reads `🔀 active project: a → b`, and the dock follows the rebind to the new project's own chat, as P27a noted), so the smoke reads it from the toast, as P27a does. (4) §7.1 Risks says the rebind toast and the mode toast (`uiModeToast`) show together: `uiModeToast` is shown only by the header switch and the command palette; `noteNewProjectCreated` shows none, so only the rebind toast appears. (5) The implementation landed in the orchestrator's snapshot commit `e7f07b37b` (a container restart); this note and the gate evidence are committed on top of it.

**Gate rows** (cwd `pypsa-gui/frontend` unless stated):

| Row | Command | Result |
|---|---|---|
| 1 / 2 | backend | not re-run: no backend change since P27b (`git diff 6135c7601 -- pypsa-gui/backend` is empty); the brief's files `tests/test_guided_mode_prompt.py tests/test_stub_openai_endpoint.py tests/test_chat_tool_dispatch_loop_seam.py` (`PYTHONPATH=/home/user/pypsa-eur:/home/user/pypsa-eur/pypsa-gui/backend /tmp/claude-0/venv/bin/python -m pytest <files> -p no:cacheprovider -W ignore -q -o addopts=""`, cwd `pypsa-gui/backend`): 87 passed |
| 3 | `npx tsc --noEmit -p .` | 0 errors |
| 4 | `npx vitest run` | 241 files / 2701 passed (P27b 2686 + 15 P32 cases) |
| 4s | `for i in $(seq 10); do npx vitest run src/components/ChatPanel src/App. src/store/uiStore src/pages/hubDesign src/api/client.mismatch src/components/ProjectMismatchBanner src/hooks/useProjectMismatch; done` | 10 / 10 green (40 files / 618 tests each) |
| 5 | `PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers node scripts/smoke-guided.mjs --phase P32 --out <scratchpad>/smoke32…` | PASS, 14 screenshots (twice: before and after the container restart) |
| 5 | same, `--phase P27b` / `P27a` / `P26` / `P25` / `P23` | PASS 54 (banner ~6.5 s; FMEA 8 / 4 / 6 settled) / PASS 14 ("is running" toast) / PASS 38 / PASS 12 / PASS 13 |
| 7 | `git diff 6135c7601 -- 'pypsa-gui/frontend/src/**' \| grep -c uiMode` | 13, all in `ChatPanel.rebound.test.tsx`; no product `uiMode` branch changed; the `*.expertUnchanged.*` snapshots pass in row 4 |

No processes are left running.

## P32 result: GO on `266c14a92`, 2026-09-30

- **No blockers.** The implementer's evidence:

  | Check | Result |
  |---|---|
  | vitest | 241 files / 2701 passed |
  | Stress run | ×10, all green |
  | Smokes | P32, P27b, P26 and P25 PASS |
  | Mutations | 7/7 killed |
  | Backend | unchanged since P27a |

- **Reviewer's mutations:** 7 of 9 killed. The two survivors: `rename_project` and `restore_project_snapshot` are not listed in the "not a new project" cases, so they are untested (N1). That goes into P28.
- **Chat project switch in auth mode (N6).** Switching project from chat does not acquire the new project's edit lock in auth mode. This predates P32; it is investigated test-first in P28.
- **Recorded, not changed:**
  - N2: the unit mismatch test is weak; the smoke is the real guard.
  - N3: open Results stays open, and Hub design does not auto-open over it.
  - N4: an optional "Switched to Guided" toast is left to the owner.
  - N5: the transcript follows the dock to the new project's chat, which is P27a behaviour.
- **Gate file:** `docs/superpowers/qa/2026-09-29-guided-mode-deferred-gate-P32.md`.

### P28 phase note (implementation, 2026-09-30, base `531ffe1da`)

Deferred spec §3 (A3, A4, C6, C10), plus the items carried in: P32 N1, P32 N6 and the P27b auth-harness recommendation.

**Anchor drift.** The spec §3 anchors predate the master merge and P27a–P32. They were re-verified on `531ffe1da`.

| Spec anchor | On `531ffe1da` |
|---|---|
| `chat.py:122-176` `/health`, docstring `:150-153`, readiness rule `:158-165` | unchanged (`:122`, `:153`, `:160`) |
| `chat.py:648-667` `/profiles`, 401 at `:658` | `:648-667`, 401 at `:660` |
| `chat.py:703-800` `/history`, C-4 `:757-773`, read-only rule `:792-800` | `:703-851`; C-4 `except` at `:762`; the freshly-minted binding at `:811` |
| `useChatProfiles.ts:18` key | unchanged |
| `ChatPanel.tsx:1505-1512` gate, `:1672` hydrate, `:2748-2751` dropdown | `:1501-1522`; `setMessages(seeded)` `:1687`; `selectedProfileId` `:2805`, `<select value>` `:2906` |
| `ChatLaunchGreeting.tsx:142-153` key offer; `:56-88` `solveLine`, `:78` fallback, `:88` "Not solved yet." | `:138-150`; `:56-90`, `:78`, `:86` |
| `api/chat.ts:193-215` `ChatHealth` | unchanged. The profiles type (`ChatProfilesPayload`) lives in `api/llmSettings.ts:129-132`, not in `api/chat.ts`. |
| `useHubData.ts:50-61`; `simulation.ts:787` `EhReview.stale` | `:50-61`; `:982` |
| `uiStore.ts:647-660` (C10) | `setUiMode` `:644`, `noteNewProjectCreated` `:662-676`; the listener is new, at the end of the file |
| rebound handler (N6) | `ChatPanel.tsx:2338`; `switchToProject` step 7 at `projectActions.ts:512-524` |

**Contract drift found while implementing:**

1. **`/chat/profiles` answers 200 in local mode, not 401** (spec §3.1 "Local mode", review condition 11).
   - `main.lifespan` runs `local_mode.ensure_local_identity`, and the middleware injects that user on every request. This holds even when the database's identity was removed beforehand.
   - The 401 path is the anonymous hosted caller.
   - Both are pinned in `test_chat_profiles_readiness.py`: `test_profiles_refuse_a_caller_with_no_user` and `test_local_mode_seeds_its_identity_at_startup_so_profiles_answer`.
   - The FE fallback is kept for any error on the list: `/health` decides for the active profile, and a non-active profile fails open.
   - The smoke reads `/chat/profiles` in local mode directly.
2. **The review's `stale` does not flag an edit** (spec §3.5 smoke: "edit one bus → the stale sentence").
   - `eh_review.review_latest` sets `stale: true` only when the stored report was cleared, and only a foreground `/simulation/run` clears it (`routers/simulation.py:834`). A bus edit leaves `stale: false`.
   - The smoke logs this (after the edit: `stale=false`, greeting unchanged), then runs a foreground solve and asserts the stale sentence. On reload it appears in 0.0 s.
   - A greeting that also notices edits would need a backend signal, such as a network revision on the study record. That is not in the P28 contract; it is recorded for the owner.
3. **Re-activating a project does not bring back its hub study record.** The P26 H2 project re-opened at Site, with no study. The smoke's part (C) therefore runs its own study from the Goal card.

**What was built** (the file list is spec §3.4, plus `ApiKeySetup.tsx`, `api/llmSettings.ts` and `utils/projectActions.ts`):

- **A3 backend.**
  - `_profile_chat_ready(profile)` is shared by `/health` and `/profiles`. `/health`'s body is unchanged and its key set is now pinned.
  - Each profile on `/profiles` carries `chat_ready`.
  - `/history` carries `bound_profile_id`:
    - a freshly minted session reports the profile it adopted, or null on the C-4 path;
    - an already-live session reports the binding `/stream` gave it, while that binding is still configured;
    - it is null with no turns.
- **A3 frontend.**
  - `chatStore.boundProfileId` is set from `/history` on hydrate and from `session_init.profile_id`. It is cleared by `resetForProjectSwitch` and `startNewChat`, and it is never sent with a turn.
  - `hooks/useChatProfiles.ts` gains `chatProfileReadiness` and `useChatReadiness`. The effective profile is `profileId ?? boundProfileId ?? active`. Its readiness comes from the list, then `/health` for the active profile, then unknown.
  - The Send gate and the key offer read it.
  - `ApiKeySetup` also invalidates the profile list.
- **A4 + C6.**
  - Guided with the hub study done: the record decides the sentence, dispatch does not. `eh_review.stale` gives the stale sentence. The review is read on the hub's key, only while the study is done, with no poll.
  - The fresh-dispatch fallback with no hub study says "see Results".
  - Guided never-solved says the C6 sentence. Expert is unchanged and never reads the review.
- **C10.** A `storage` listener adopts another tab's explicit choice through `setUiMode(stored, {explicit: true})`, only while this tab has no explicit choice. An implicit change is ignored. There is one listener per window.
- **N6.**
  - `projectActions.moveProjectLock(from, to)`, used by `switchToProject` step 7 (same behaviour) and by the rebound handler.
  - A named rebind to another project releases the old lock and acquires the new one. If another user holds it, the tab goes read-only (`locked-by-user`) and the project stays open for viewing.
  - `to: null` releases the old lock. The call is a no-op without auth.
- **N1.** `rename_project` and `restore_project_snapshot` join the "is not a new project" `it.each`.

**Decisions and deviations:**

1. **The dropdown shows the bound profile** (`profileId ?? boundProfileId ?? active`).
   - The spec names the dropdown's anchor but gives it no rule.
   - Showing the active profile while Send follows the bound one would be dishonest.
   - It would also make the cross-wire check compare against the wrong wire: the backend refuses a cross-wire rebind of a bound session.
2. **`session_init.profile_id` updates `boundProfileId`.** This goes beyond the spec, which names only `/history`. Without it, a session that bound to the active profile keeps following later active-profile changes in the gate until the next reload. `profileId` is still never pinned from the frame.
3. **When the list does not say, the active profile falls back to `/health`.**
   - Examples: a list entry without `chat_ready` (an older backend, or every existing test mock), or a list that failed to load.
   - The spec's rule falls back only "when the list is not loaded".
   - This keeps the three existing `getChatHealth` mocks and their cases untouched, as §3.1 requires.
4. **The smoke's part (A) keeps the spec's order.** The turn is sent while the stub is active, then the Anthropic profile is made active, then the page reloads. The key offer is checked in a separate part (B), on a project with an empty transcript: after (A)'s turn the greeting is hidden, so a key-offer check there would be vacuous.
5. **The two-user, two-tab auth harness is deferred.**
   - It is not cheap. The harness would need a hosted-mode uvicorn: non-local, with an SQLite `DATABASE_URL` and `SECRET_KEY`.
   - It would also need a super-admin via `tools/bootstrap_super_admin.py`, and a second user created through the admin API whose password is set from the outbox token.
   - Each context would need a scripted login with CSRF cookies, and the stub turn would need to run in auth mode. That is a new harness mode, not a step (the same finding as P27b).
   - N6 is covered at the adapter level by `ChatPanel.reboundLock.test.tsx`, with the real client, the real rebound handler and the real lock code. The backend's side (foreign holder refused, TTL steal) is covered by `test_project_locks.py`.
   - C10 is covered by `uiStore.uiMode.test.ts`.

**Changed assertions (one line each):**
- `test_llm_settings_api.py::test_profiles_route_allows_any_authenticated_member`: the profile key set gains `chat_ready` (a boolean), per D-3.
- `ChatLaunchGreeting.solvedState.test.tsx`, Guided re-solve case: → "A calculation has updated this network — see Results." (spec §3.2 replaces the `:78` fallback).
- The same file, Guided "no hub study yet": → the C6 sentence.
- `smoke-guided.mjs` P27b (b), project 2's greeting: → the C6 sentence. The step's own comment anticipated this.

**Red → green.** Every test below was seen failing for the stated reason before its fix.

| Test | Before the fix |
|---|---|
| `test_chat_profiles_readiness.py` | 7 of 9 red: 6 with `KeyError: 'chat_ready'`, and the spec's "local mode → 401" case (it answered 200: the spec's claim is wrong, so that case became the drift pin above). The `/health` key-set pin and the anonymous 401 were green on arrival; mutant B1 kills the pin. |
| `test_chat_profile_binding.py` P28 | 4 of 4 red (no `bound_profile_id`) |
| `ChatPanel.sendGate.test.tsx` P28 | 6 of 7 red. "A key saved re-reads the list" was green on arrival; it guards the `ApiKeySetup` invalidation, and mutant F8 kills it. |
| `ChatLaunchGreeting.test.tsx` P28 | 2 of 3 red (the list was never read); the control was green |
| `chatStore.test.ts` P28 | 3 of 3 red |
| `ChatLaunchGreeting.solvedState.test.tsx` P28 | 7 red (the two changed cases plus 5 new ones); the Expert and "not until done" guards were green |
| `uiStore.uiMode.test.ts` C10 | 3 of 5 red; the two "never overridden / unrelated keys" guards were green |
| `ChatPanel.reboundLock.test.tsx` (N6 reproduction) | 8 of 10 red: no `DELETE /projects/X/lock`, no `POST /projects/Y/lock`, and no read-only when Bob holds Y. The same-project and auth-off guards were green. |
| N1 cases | coverage, green on arrival; mutants N1a and N1b are killed |

**Mutants** (`scratchpad/p28/mutate.py`; logs `mutations-fe.log`, `mutations-be.log`):
- **Frontend: 28 of 29 killed.**
  - F1–F10: bound ignored; list ignored; `/health` fallback dropped; hydrate, reset, New chat and `session_init` not wired; `ApiKeySetup` not invalidating the list; dropdown ignores the bound profile; greeting health-only.
  - A1–A7: stale ignored; the fallback sentence back to Hub design; C6 reverted; C6 applied to Expert; review read in Expert; review read before the study is done; dispatch overrides a done study.
  - C1–C5: implicit change adopted; own explicit choice overridden; adopted implicitly; listener not registered; `setUiMode` bypassed (no §3.7 pruning).
  - N6a–e: no lock move; no release; `to: null` keeps the lock; auth ignored; release of the new project instead of the old.
  - N1a–b: `rename_project` or `restore_project_snapshot` flips.
  - **Survivor N6d** ("`moveProjectLock` ignores `authEnabled`") is equivalent: `releaseProjectLock` and `acquireProjectLock` each check `authEnabled` themselves, so no lock request leaves either way.
- **Backend: 5 of 5 killed.** B1 `/health` gains a key; B2 `chat_ready` always true; B3 a deleted (C-4) profile still reported; B4 a live session reports the transcript's profile; B5 `bound_profile_id` always null.

**Gate rows** (cwd `pypsa-gui/frontend` unless stated):

| Row | Command | Result |
|---|---|---|
| 1 | `PYTHONPATH=/home/user/pypsa-eur:/home/user/pypsa-eur/pypsa-gui/backend /tmp/claude-0/venv/bin/python -m pytest tests/ -m "not slow" -p no:cacheprovider -W ignore -q -o addopts=""` (`pypsa-gui/backend`) | 6673 passed, 31 skipped, 11 deselected, 0 failed (P27a: 6660 + 13 new) |
| 2 | same interpreter, `-m pytest <the 14-file set> tests/test_chat*.py tests/test_llm_settings_api.py -p no:cacheprovider -W ignore -q -o addopts=""` (`pypsa-gui/backend`; 56 chat files including the new readiness file) | 1608 passed, 19 skipped (`scratchpad/p28/row2.log`) |
| 3 | `npx tsc --noEmit -p .` | 0 errors |
| 4 | `npx vitest run` | 242 files / 2738 passed (P32: 241 / 2701) |
| 4s | `for i in $(seq 10); do npx vitest run src/components/ChatPanel src/components/ChatLaunchGreeting src/store/chatStore src/store/uiStore src/App. src/hooks/useChatProfiles src/components/ApiKeySetup src/components/ProjectMismatchBanner src/utils/projectActions; done` | 10 / 10 green (34 files / 594 tests each) |
| 5 | `PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers node scripts/smoke-guided.mjs --phase P28 --out <scratchpad>/smoke28` | PASS, 43 screenshots. (A) bound profile after reload: Send enabled, dropdown `smoke-stub-2`, `bound_profile_id=smoke-stub-2`, `chat_ready` anthropic false / stubs true. (B) offer shown, then hidden on pick. (C) stale sentence 0.0 s after reload. (D) C6 in Guided, "Not solved yet." in Expert. |
| 5 | same, `--phase P32` / `P27b` / `P26` / `P25` / `P22.9` | PASS 14 / PASS 54 (after the C6 assertion update; banner 6.4 s; FMEA 8 / 4 / 6 settled) / PASS 38 / PASS 12 / PASS 10 |
| 7 | `git diff 531ffe1da -- 'pypsa-gui/frontend/src/**' \| grep -c uiMode` | 28: test-store setups and assertions, plus the C10 listener. No new product `uiMode` branch; the greeting's Guided arms use its existing `guided` flag. The `*.expertUnchanged.*` snapshots pass in row 4. |

No processes are left running.

**P28 gate follow-up, O1 (owner, 2026-09-30): soften now, track later.** The Guided "done" greeting no longer claims the results describe the current network. It now reads "The last study’s results are in Hub design.". With the hub study done and live `dispatch === 'stale'`, the stale sentence shows again; this is the pre-P28 precedence and gate probe Q16, now pinned. The Hub design Results card makes no such claim ("These results are from an earlier study; the network was solved since…" appears only when `stale` is set), so it is unchanged. Real edit tracking is **P33b** in §3 (owner-approved, to schedule) and OPEN-ITEMS 10b.

## P28 result: GO at re-gate on `ac6960ebd` + R-1 fix, 2026-09-30
- **First gate:** NO-GO on B1 (the stale sentence claimed the network changed when it had been solved). The re-gate also found N-a/N-b (lock replies racing a later move) and N-c (`/history` reported the wrong fallback binding).
- **Re-gate fixes:** all fixed test-first. Rows 1–5 pass (6674 passed + 31 skipped backend; row 2 1609 passed; tsc 0; vitest 2745; smoke P28 PASS). Mutations killed 9 of 12. The survivors are R3 (equivalent), R4 and R7 (narrow lock races with correct guards and no test; recorded in OPEN-ITEMS).
- **O1 (owner):** softer done-sentence now; real edit-after-study tracking is P33b.
- **R-1:** after a study, stale live dispatch falls through to "…the network changed since." (see the gate file).

### P29 phase note (implementation, 2026-09-30, base `e7f139b84`)

Deferred spec §4 (B1, B2, B3). Commits: `363942269` (the Expert snapshot, before any P29 edit), `e5af7aa4b` (B1), `401dec86e` (B2), `1fa3bbf65` (B3 backend), `d3f288074` (B3 frontend), `8ec11f7aa` (B1 follow-up), `7456d6c44` (smoke), `a994da65d` (B3 merge tests). Out-of-phase: `d95357335` (coordinator's cherry-pick: `services/dispatch_status.py` ignores a solve's transient `__voll_*` slack rows, so a live-network FMEA sweep no longer reports a false `stale` dispatch; `tests/test_dispatch_status_transient.py`). It is not P29 work. It is in the tree that rows 1, 2 and the second smoke ran on.

**Anchor drift.** Re-checked on `e7f139b84`:

| Spec anchor | On `e7f139b84` |
|---|---|
| `ChatPanel.tsx:2196-2216` `tool_preparing` / `tool_request`; `:2239-2260` `tool_result`; render `:3097-3138` | `:2211`, `:2254`; `chat-tool-label` `:3196` |
| `guidedToolLine` `:468-472` | unchanged |
| `FmeaTab.tsx:161-171`, `:237`, `:257`, `:258-259`, `:260`, `:274` | `:162` title, `:237` Class, `:257` class cell, `:260` severity cell, `:274` badge |
| `copt.py:1145-1151`, merge `:1275-1280` | `:1148`, `:1279` |
| `sweep.py:604-620` | `:628-640` (the `in_scope` branch); row dict ends `:655` |
| `stress.py:644-655` | `:670` |
| `api/simulation.ts` (type) | `getFmeaModes` is untyped (`per_mode: Array<Record<string, unknown>>` in `results/adequacy.tsx:161`). The row type lives in `fmea.ts` (`WorksheetRow`) and `EhReferenceDesignPanel.tsx` (`FmeaRow`), so the field was added there instead. `api/simulation.ts` is unchanged. |

**What was built:**

- **B1 (`ChatPanel.tsx`).**
  - `GUIDED_TOOL_PHRASE` sits beside `GUIDED_CARD_SUMMARY` and holds the spec's eight entries. `guidedToolPhrase(X)` falls back to "use <tool words>".
  - `guidedToolLine` hides `… preparing X`. It reads `→ X` as "Working: <phrase>…", `✓ X` as "Done: <phrase>" and `✗ X[ — kind[: message]]` as "Could not: <phrase>".
  - A failed call's own message is shown as the next line (`chat-tool-message`). The raw line stays under the existing Details, collapsed.
  - The P26 declined rules come first and are unchanged. The transcript and Expert are unchanged.
- **B2.**
  - `Results.tsx`: in Guided the header reads eyebrow `HUB DESIGN · RESULTS` and title `Reliability results`.
  - `FmeaTab.tsx` gets a Guided branch; the Expert JSX is untouched:
    - the spec's title and prose;
    - a class cell `Term k="fmea_class_a|b|c|d"` reading Generator outage / Link outage / Stress scenario / Your own row;
    - the occurrence basis `Term k="fmea_occurrence"` reading "outage rate";
    - no badge: the row `title` is `useTerm('fmea_engine')` plus a plain fidelity sentence;
    - `Term` hovers on the Severity and Yearly-risk headers (`fmea_severity`, `fmea_criticality`);
    - `data-guided="1"`; every test id is kept.
  - Eight keys were added to `eh_fmea_guide.json` and to `TERM_FALLBACK`, word for word the same. Each is at most 27 words, with no `_JARGON` or `_HUB_JARGON`.
- **B3.**
  - `worksheet.zero_reason(...)` holds the spec's rule and its order, with the docstring notes.
  - It is set on `failure_mode["zero_reason"]` in these places:
    - COPT class A (`attribute_criticality`);
    - the COPT block merge (re-derived);
    - the class-B sweep;
    - the class-C sweep;
    - the Energy Hub zonal screen merge (see decision 3).
  - `per_mode` forwards the key through the existing spread, with no edit to `copt_endpoint.py`. No tool schema, description or prompt changed; `test_guided_mode_prompt.py` is green in row 2.
  - `fmea.ts`: `ZeroReason`, `ZERO_REASON_TEXT` and `zeroReasonText`. `mergeWorksheet` forwards the key; manual rows get `null`.
  - `FmeaTab`: in Guided, a €0 row with a reason shows the text. In Expert it shows `€0.0` with the text as `title`.
  - `ResultsCard` reads the same text and falls back to "no measurable cost". `FmeaRow` gains the field, and `fmeaTopRows` forwards it unchanged.
  - The CSV and JSON exports are unchanged.

**The one justified snapshot update.** `FmeaTab.expertUnchanged.test.tsx.snap` was recorded on `e7f139b84` and committed first (`363942269`). B2 (`401dec86e`) passed it byte for byte. B3 (`d3f288074`) added exactly two attributes, `title="no shortfall — the site copes without it"` and `title="not counted (outside the electricity metric)"`, on the two €0 severity cells. *Justification:* the spec's accepted Expert deviation; the Expert `€0.0` gains its reason as a hover and nothing else changes. `Results.expertUnchanged`, `AppHeader.expertUnchanged` and `Sidebar.expertUnchanged` are unchanged.

**Decisions and deviations:**

1. **A finished or declined call's "Working" line gives way to its outcome** (`8ec11f7aa`, `guidedSettledToolIds`).
   - The backend sends `tool_request` before the confirmation gate (`chat_service.py:4620`). As first built, a declined call therefore read "Working: run the reliability study…" above "You declined — nothing was changed."
   - That is a claim that is no longer true. Guided now hides the `→ X` line once a `✓`, `✗` or `denied:` line with the same `tool_use_id` exists, so each call is one line.
   - The spec's "Working… then Done" holds in time. The red test asserts both states. An extra case covers a declined call.
2. **The rebound line reads in words in Guided.** `🔀 active project: A → B` becomes "The assistant is now working in the project B." (or "The assistant replaced the network; it is not saved to a project yet."), with the raw line under Details. The spec's table does not list it, but it is a Guided `chat-message` with a raw `→ `, which is what the smoke forbids.
3. **The Energy Hub zonal screen merge (`eh_stages._screen`) also re-derives `zero_reason`.** It is a second block merge, beyond the spec's anchors, and it holds the common-mode row. Without it, its rows would carry a per-block reason, or none. The pin is `test_the_zonal_screen_merge_rederives_the_reason_too`: red without the change.
4. **Guided wording beyond the contract table**, on the same tab:
   - the sweep button reads "Check equipment failures and stress scenarios" / "Checking…";
   - the aborted notice, the empty state, the form title ("Add your own row") and its footnote are plain;
   - "(out of scope)" reads "(not counted)";
   - Mode reads "What fails", and Mitigability reads "Notes", with the delete button inside that cell;
   - Yearly risk is the last column, so the spec's prose ("the last column is the yearly risk") is true.
   - The Guided fidelity sentence replaces the Expert tip, which says "COPT" / "LP proxy" (engine ids).
5. **The smoke has no `page-header` test id to read.** `PageHeader` has none, and adding one would change every page's Expert markup. The smoke reads the Results `h1` ("Reliability results", and not "Optimization results").
6. **The export check has no stored pre-phase fixture.** Its two parts:
   - **API.** Every `GET /api/results/fmea_modes` row carries `zero_reason` from the allowed set, and no other key is new (checked against the pre-P29 key set). The payload is saved as `p29-fmea_modes.json`.
   - **CSV.** The download is taken twice on the same page: with the live rows, and with the rows served without the key through a Playwright route (the pre-phase payload). The bytes are equal, and the header is the pre-phase header.
   - The number side is pinned by the golden and range suites, which are green in row 2.
7. **Class-C frequency 0 is unreachable.** `stress._validate` refuses `frequency_per_year` outside (0, 365]. The "freq 0 with a shortfall" note is kept in the helper's docstring; the test uses a positive frequency.

**Changed assertions (one line each):**
- `test_guides.py::test_hub_fields_present`: `len(set(HUB_FIELDS)) == 20` → `28`. The eight B2 keys join the list, as the spec requires.
- `Term.test.tsx` "covers the twenty hub-design fields" → also the eight FMEA-tab fields. Same reason.
- `smoke-guided.mjs`:
  - P24 FMEA step: a class-B row is `B` **or** `Link outage` (B2's Guided label).
  - P26 FMEA settle loop: `Sweeping…` **or** `Checking…` (B2's Guided button).
  - P26 declined step: the declined label is **one of** the tool labels, not necessarily the first (B1 labels every tool line).

**Red → green:**

| Test | Before the fix |
|---|---|
| `ChatPanel.sendRequest.test.tsx` "P29: Guided tool lines" | 9 of 10 red (no labels; the preparing line shown). The Expert raw-lines snapshot was written against the unchanged code. Later additions: rebound (2 red), declined-not-working (red before `8ec11f7aa`). |
| `FmeaTab.guided.test.tsx` + `Results.uiMode.test.tsx` P29 | 11 red; the two Expert controls were green |
| `test_guides.py` | 17 red (the eight keys missing) |
| `test_fmea_zero_reason.py` | ImportError, then 8 of 19 red with the helper in place (every engine and forwarding case; the helper cases pass with the helper). Added later: the zonal case (red without its change) and the block-merge override (kills M6). |
| `FmeaTab.formatting` / `ResultsCard` / `fmea.test.ts` P29 | 4 red. "Leaves the CSV unchanged" was green on arrival (a guard); mutant B3b does not affect it, because `worksheetCsvRows` never read the field. |

**Mutants** (`scratchpad/p29/mutate_fe.py`, `mutate_be.py`; logs `mutations-fe.log`, `mutations-be.log`):
- **Frontend: 15 of 15 killed.**
  - B1a–f: phrase entry dropped; preparing shown; error message dropped; settled rule off; labels in Expert; rebound raw.
  - B2a–e: header not branched; class letter shown; basis raw; engine tip in the row title; Guided always on (kills the Expert snapshot).
  - B3a–d: Expert title dropped; merge drops the field; hub ignores the field; Guided always `€0.0`.
- **Backend: 7 of 7 killed.**
  - M1 is the spec's mutation target (`no_shortfall` before `no_outage_data`), killed by the mttr-0 cases. M2 is the other (field dropped from `per_mode`).
  - M3 `out_of_scope` after `no_outage_data`; M4/M5 class B/C not set; M6 the COPT merge not re-derived; M7 the `unpriced` check dropped.
  - M6 first survived: both merge tests ran the single-block path. They now carry a constant `capacity_series`.

**Gate rows** (cwd `pypsa-gui/frontend` unless stated; logs in `scratchpad/p29/`):

| Row | Command | Result |
|---|---|---|
| 1 | `PYTHONPATH=/home/user/pypsa-eur:/home/user/pypsa-eur/pypsa-gui/backend /tmp/claude-0/venv/bin/python -m pytest tests/ -m "not slow" -p no:cacheprovider -W ignore -q -o addopts=""` (`pypsa-gui/backend`, HEAD `a994da65d`) | 6716 passed, 31 skipped, 11 deselected, 0 failed in 57 min (`row1.log`; P28: 6674, plus the 21 in `test_fmea_zero_reason.py` and the cherry-picked `test_dispatch_status_transient.py`) |
| 2 | same interpreter, `-m pytest <the 14-file set> tests/test_fmea_zero_reason.py tests/test_energy_hub_frontier_fmea.py tests/test_energy_hub_class_c_authoring.py tests/test_energy_hub_class_c_profiles.py -p no:cacheprovider -W ignore -q -o addopts=""` (`pypsa-gui/backend`, HEAD `a994da65d`) | 771 passed, 17 skipped (`row2.log`) |
| 2+ | same, `tests/test_adequacy_*.py tests/test_energy_hub_*.py tests/test_golden*.py tests/test_copt*.py tests/test_fmea*.py tests/test_results_range.py tests/test_guided_mode_prompt.py -m "not slow"` (`pypsa-gui/backend`, on `1fa3bbf65`) | 1440 passed, 17 skipped (`adequacy_pre.log`) |
| 3 | `npx tsc --noEmit -p .` | 0 errors |
| 4 | `npx vitest run` | 244 files / 2777 passed (P28: 2745) |
| 4s | `for i in $(seq 10); do npx vitest run src/components/ChatPanel src/pages/results/FmeaTab src/pages/hubDesign; done` | 10 / 10 green (34 files / 573 tests each) |
| 5 | `PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers node scripts/smoke-guided.mjs --phase P29 --out <scratchpad>/p29/smoke29b` (HEAD `a994da65d`; the first run on `7456d6c44` also passed) | PASS, 39 screenshots. The transcript after Improve and after the delete/export turns has no raw progress line (3 and 9 rows; labels are the three declines). h1 "Reliability results"; `data-guided="1"`; 48 cells, no letter or engine id; `genset_1` "…no shortfall — the site copes without it"; every `per_mode` row carries the key (`site_transformer`/`grid_import` null, four gensets and both scenarios `no_shortfall`); no other new key; CSV 1212 bytes equal with and without the key; header unchanged. |
| 5 | same, `--phase P24` / `P28` | PASS 21 / PASS 43 (the changed P24 and P26 steps hold in both) |
| 7 | `git diff e7f139b84 -- 'pypsa-gui/frontend/src/**' \| grep -c uiMode` | 16. Test-store setups, plus two product lines: the `Results.tsx` eyebrow and title, and the one `guided` read in `FmeaTab`. ChatPanel uses its existing `uiMode === 'guided'` gate. The `*.expertUnchanged.*` snapshots pass in row 4; the FmeaTab one carries the justified update above. |

**Known limitations:**
- Rows from a sweep record written before P29 carry no key. The tab then shows `€0.0` in both modes, and the hub shows "no measurable cost". Re-running the sweep adds the key.
- `StressScenarioEditor` (under the FMEA tab) keeps its Expert wording in Guided. It is outside the B2 contract.
- The model-fallback system line (`from → to`) is not a tool line and keeps its arrow in Guided. The stub never produces one.
- On the data-center template every stress scenario and genset reads `no_shortfall`, so the smoke does not exercise `unpriced`, `no_outage_data` or `out_of_scope` in the browser. Those are covered in `test_fmea_zero_reason.py` and `FmeaTab.formatting.test.tsx`.

No processes are left running.

**P29 gate fixes** (gate record `docs/superpowers/qa/2026-09-30-guided-mode-deferred-gate-P29.md`: NO-GO on B1-1, plus S-1 and S-2; fixed test-first on `235c15fdd`):

- **B1-1** (`4b2f17c60`). A tool that only *starts* background work answers at once, so its `✓` line means "started". Its Guided phrase now begins "start …":
  - `run_eh_study` → "start the reliability study", and `run_fmea_sweep` → "start the equipment-failure check";
  - explicit entries for `run_simulation`, `run_ac_pf_stage`, `run_frontier_study`, `run_mc_study`, `run_coupling_loop`, `run_margin_loop` and `gridspine_run_pipeline`;
  - `solve_queue_enqueue` → "add the project to the solve queue", and `abort_adequacy_study` → "ask the running study to stop".

  The other six phrases were audited and describe work the call completes. *Deviation from spec §4.1:* the spec's example phrases `run the reliability study` and `check what happens when equipment fails` were the source of the false "Done: …". The tool descriptions (`chat_tools_schema.py`: "Start the …", "returns {status:'running'} immediately") decide.

  The red tests read the start-only set from the schema's own "Start …" descriptions, plus the worker and queue tools. They check that each has an explicit "start …" phrase (no "use …" fallback), and that a Guided `✓ run_fmea_sweep` reads "Done: start the equipment-failure check". 14 were red. The existing P29 strings were updated to the new phrases.
- **S-1** (`5fcd1fd6a`). The Guided FMEA tab's stress editor heading reads "Stress scenarios · n/10", with no "(class C)". The Guided add-row placeholders read "cost per event (€)" and "notes (optional)". Expert is unchanged, and `FmeaTab.expertUnchanged` did not change. 2 were red.
- **S-2** (`5fcd1fd6a`). `ZERO_REASON_TEXT.no_outage_data` now reads "no outage rate set (or it is zero)". The rule also fires for an explicit rate of 0, so the text must be true in both cases. The backend rule is the spec's and is unchanged. 2 were red.
- **Mutants:** 11 of 11 killed (`scratchpad/p29/mutations-gate.log`).
  - G1 and G2: the spec phrases restored.
  - G3–G6: start-tool, queue and abort entries dropped.
  - S1a–d: heading not branched, heading always Guided, placeholder not branched, Guided placeholder in Expert.
  - S2: the old text.
- **Rows** (frontend only; the backend is unchanged since `a994da65d`, so rows 1–2 stand):

  | Row | Result |
  |---|---|
  | 3 `tsc` | 0 errors |
  | 4 `vitest` | 244 files / 2793 passed |
  | 4s | 10 / 10 green, 34 files / 587 tests each |
  | 5 `--phase P29` on `5fcd1fd6a` | PASS, 39 screenshots (`smoke29c`) |

  The smoke asserted none of the changed strings.

## P29 result: GO at re-gate on `c066bb7fa` + R-1 wording, 2026-09-30
- **First gate:** NO-GO on B1-1 — "Done: run the reliability study" showed while the study was still running, because the spec's example phrases named the work rather than the start of it. Fixed: every start-only tool (10, derived from the schema descriptions) says "start …".
- **Should-fixes:** S-1 (Guided stress heading and placeholders) and S-2 (zero-rate text) fixed.
- **Re-gate:** rows 3–5 pass (tsc 0; vitest 2793; smoke P29 PASS). Rows 1–2 stand (no backend change since `a994da65d`: 6716 passed + 31 skipped; row 2 776 passed). Mutations: 24 of 26 killed at the first gate (2 equivalent), 12 of 12 at the re-gate.
- **R-1 (re-gate should-fix):** the zero-frequency text now also covers a missing or zero repair time.
- **Out of phase:** `d95357335` (dispatch_status ignores a running solve's transient rows) was reviewed in this gate and holds.

### P30 phase note (implementation, 2026-09-30, base `bcb6a671f`)

Deferred spec §5 (B4, B7, B5, B6, B8, B9, B10, C11, C12). All nine items are done in P30; none moved to P31. Commits:
- `ea312f851` B5, B6 backend;
- `87309cff1` B4, B7;
- `4e893431a` B6 (frontend), B8, B9, B10, C11, C12;
- `4c9559755` smoke `--phase P30`;
- `7a97688f0` B7 follow-up: the tagging tour's sentence, plus a smoke locator fix;
- `c90556d2f` B4 follow-up: a step whose target is not on screen docks bottom-left;
- `2787a2530` B7 follow-up: an optional step on screen when the tour starts keeps its place;
- `5a9052599` tighter tests added after the mutation pass;
- plus this note.

The three follow-ups came from smoke runs. Each was test-first.

**Anchor drift.** Re-checked on `bcb6a671f`:
- `GuidedTour.tsx` placement is at `:193-206`, and the counter at `:221`.
- `validation_service.py` `gen_zero_costs` is at `:1745-1764`.
- `local_settings.py` `_state()` is at `:58-70`.
- `NewProjectWizard.tsx` "Saved to" is at `:209-211`.
- `HubDesignPanel.tsx` error line is at `:112-116`.
- `client.ts` quiet branch is at `:232`.
- `AppHeader.tsx` switch is at `:1040-1075`.
- `uiStore.ts` request is at `:438,543,797`.
- `PropertiesPanel.tsx` consumers are at `:1198,1647`.
- `GoalCard.tsx` VOLL line is at `:136`.

**What was built:**

- **B4 (`GuidedTour.tsx`).** `placePopover(target, size, vw, vh)` is exported and pure.
  - It tries below, above, right and left, in that order, with a 12 px gap. It takes the first one that fits inside an 8 px viewport margin and does not touch the highlight box (the target grown by 4 px).
  - If none fits, the result is `free`: the side with the most room for the popover's own size, clamped into the viewport. The target is then scrolled into view (`block: 'center'`) once per step.
  - The popover is measured through a `ref`, with a layout effect after each commit. It is re-measured on `resize`, on `scroll` (capture), through a `ResizeObserver` on itself, and through a `MutationObserver` on `document.body` (`childList`, `subtree`).
  - All re-measuring is batched per animation frame. State changes only when the target box, the popover size or the set of available steps actually changed.
  - Style: width 320, `maxWidth: calc(100vw - 16px)`, `maxHeight: calc(100vh - 16px)`, `overflowY: auto`. `data-placement` is set on `guide-tour`.
- **B7.** All of `tour.steps` is kept.
  - `next()` / `back()` look for the next or previous *available* step at the moment of the click. An optional step is available if its target is on screen now, or was on screen when the tour started (see deviation 2).
  - The counter is "position among the steps shown now / their count". The Next / Done label and Back's disabled state come from the same set, which the observers keep current. So the label is true when an optional target appears or goes while a step is open.
  - The intro shows on the first shown step.
  - The `eh_tagging` "What to enter" sentence said "the tour includes that step only when it is started with a Link's Edit form open". That is no longer true. It now reads "the tour adds that step as soon as a Link's Edit form is open." (`test_guides.py::test_tagging_tour_says_when_the_link_step_appears`, red first.)
- **B5 (`validation_service.py`).** A new helper `_zero_cost_generators(n)` builds the zero-cost mask. It excludes a generator with a `generators_t.marginal_cost` column or a `generators_t.p_max_pu` column. The warning's code, name and message are unchanged. After the fix no template generator warns. The seven exempted are exactly the probe's seven.
- **B6.**
  - `GET /api/local-settings` gains `projects_root`. The value is `settings.projects_root`, not `flat_projects_root` (see deviation 1).
  - `LocalSettingsState.projects_root: string`.
  - The Blank tab reads `useLocalSettings()` (the shared `['localSettings']` query). It shows `Saved to <root>/<name>/` (`data-testid="new-project-saved-to"`, with a `\` separator for a Windows root).
  - It shows no line at all in hosted mode (404 → `null`) or when the read fails.
- **B8.**
  - `HubDesignPanel` names the first read in error: "study state", "template" or "readiness check". The line reads "This project's <x> could not be read from the server, so the steps below may be incomplete."
  - Readiness is observed with `useHubReadiness(…, { observeOnly: true })`. That is the same key with `enabled: false`, so the panel sends no request of its own. Retry also re-reads readiness.
  - Goal's disabled Run carries `title="The template could not be read — retry above."` The panel's Retry is above the card.
  - `client.ts`: a `skipErrorToast` failure (not a quiet poll) now logs `appLog('INFO', '<METHOD> <url> — <msg> [no toast]')`. It never logs ERROR and never toasts.
- **B9.** Each mode button has `aria-describedby="ui-mode-<m>-desc"`. A `<span id=… className="sr-only">` carries `UI_MODE_TITLES[m]` inside the switch group. `title` and the button names are unchanged.
- **B10.**
  - `propertiesEditRequest: { type: 'Bus' | 'Link'; name: string } | null` (`PropertiesEditRequest` is exported).
  - Each card consumes the request only when type and name match its own component.
  - `setSelectedComponent` clears the request unless the new selection *is* the requested component, because `prepareTaggingTour` selects and then asks.
  - `prepareTaggingTour` passes the bus it selected.
- **C11.**
  - `plainWords.test.ts` gains a lone `dtc_planning` case.
  - `AppHeader.uiMode.test.tsx` gains "a running queue job still shows Abort" (R15). The queued and store-running cases already existed.
  - Q3–Q5: see deviation 6.
- **C12.**
  - `plainWords`: `/\b(\d+(?:\.\d+)?) MWh\b/g → '$1 megawatt-hours'`. It applies to titles and effects alike, through the one function. A price `€/MWh` is untouched.
  - GoalCard reads `€X per <Term k="mwh">MWh</Term>`.
  - Catalogue key `mwh`: "A megawatt-hour: the energy of one megawatt of power delivered for one hour, the same as 1 000 kilowatt-hours." It is in `eh_fmea_guide.json` and `TERM_FALLBACK`, word for word the same.

**Deviations (with reasons):**
1. **B6 root.**
   - The spec names `get_settings().flat_projects_root` ("the value `PROJECTS_DIR` resolves from"). That is the auth-disabled legacy flat store, under app-data.
   - `/local-settings` only exists in local mode, and local mode is auth mode with a local identity. There a project is saved at `settings.projects_root / <unique_dir_name(name)>`: `storage_paths.use_org_segment()` is false locally, and `project_registry.project_dir` rejoins the row with `projects_root`.
   - The spec's own smoke assertion (the line contains `PYPSAGUI_PROJECTS_ROOT`) only holds for `projects_root`. A line with the flat root would be a false sentence.
   - The red test pins `projects_root`, checks that it is *not* the flat root, and checks that `storage_path_for(…, org_segment=use_org_segment())` lands at `<root>/<name>`.
2. **B7: "judged when reached" alone broke the P24 smoke** (the spec requires P30 ⊇ P24).
   - In the after-study hub tour started on Results, the step before `hub-results-verdict` reveals the Goal card. When reached, the verdict was absent, so it was skipped (6 steps instead of 7).
   - The rule is now the union: an optional step counts if it was on screen when the tour started (the pre-P30 rule; its `reveal` brings it back) or is on screen when reached (B7).
   - This keeps every P24 walk unchanged (6 / 7 / 8 / 6 steps in the smoke) and the tagging tour's late Link step.
   - Pinned by the unit test "an optional step on screen at the start is kept; its reveal brings it back". It was red before `2787a2530`.
3. **B4: a step whose target is not on screen docks bottom-left (left 8, bottom 8), not centred.**
   - The spec does not cover this case. The P30 smoke found that the centred popover covered the Link card's "Edit" button, which is exactly what the tagging tour asks the user to click. Playwright: "guide-tour intercepts pointer events".
   - Unit test "a target not on screen → docked bottom-left". `placement` reads `free` there.
4. **B8 wording and condition.**
   - The third name reads "readiness check", not the bare "readiness" ("This project's readiness could not be read" is not a plain sentence).
   - Run is disabled on `templateError && !template`, the existing `templateUnknown`. It is not disabled on every `templateError`. With a cached template, a failed re-read still runs with the correct settings, so disabling it would be a refusal with no reason behind it. The spec's red case (a first-load 500) is disabled with the title.
5. **B9: `AppHeader.expertUnchanged` needed no update.** That test removes `ui-mode-switch` from the clone before comparing, and the new spans live inside the switch group. The spec expected a one-line update; none was needed. All four `*.expertUnchanged` snapshots are unchanged.
6. **C11 Q3–Q5: no new `resultsApi.test.ts`.** `src/api/ehQuiet.test.ts` (P25 step 0) already pins exactly this. It covers `getEhStudy()` / `getEhTemplate()` with no argument, with `{quiet:false}` and with `{quiet:true}`. A duplicate file would only add upkeep. The three Q mutants are killed by it (below).
7. **Smoke order.** `phaseP30` runs P22.9 first, renames its project "P30 Data Center" (as P27b does), then runs P24. P22.9's first step needs a backend with no stub profile, and P24 activates one. The spec lists them as "P24 + P22.9", with no order.

**Changed assertions (one line each):**
- `test_guides.py::test_hub_fields_present`: `== 28` → `29`. `mwh` joins `HUB_FIELDS` (C12).
- `Term.test.tsx`: the key list gains `mwh` (C12).
- `PropertiesPanel.editRequest.test.tsx` tests 1–3: requests are `{type, name}`. Test 3 now says the pending Bus request is kept by a Link card and named in full (B10, spec-mandated rewrite).
- `EhReferenceDesignPanel.taggingTour.test.tsx`: the request equals `{ type: 'Bus', name: 'grid' }`, the bus prepare selected (B10).
- `useHubData.test.tsx`: the readiness hook's named fields gain `refetch`, for the panel's Retry (B8). It is still named, never spread.
- Local-settings fixtures (`localSettings.test.ts`, `CommandPalette.test.tsx`, `Sidebar.settingsNav.test.tsx`, `LocalSettings.test.tsx`) gain `projects_root`, which is required by the type (B6).

**Red → green:**

| Test | Before the fix |
|---|---|
| `test_validation_gen_costs.py` (new, 10) + `test_energy_hub_templates.py::test_templates_validate_without_gen_zero_costs` (3) | 9 red. The baseline, fixed-unit, extendable and static-`p_max_pu` guards were green. |
| `test_local_settings_api.py::test_get_reports_the_projects_root_a_new_project_lands_in` | red (KeyError) |
| `test_guides.py` (`mwh` ×3; the tagging sentence) | 4 red |
| `GuidedTour.test.tsx` P30 (B4 ×5, then the docked case, then the resize-follow case; B7 ×2, then the start-kept case) | 6 red at first. The docked case and the start-kept case were each red before their follow-up. "Counts only visible steps" was green (a guard). |
| `PropertiesPanel.editRequest.test.tsx` | 4 red |
| `AppHeader.uiMode.test.tsx` (B9) | red. The R15 queue-running case was green: the header already handles it. |
| `HubDesignPanel.flow.test.tsx` (template, readiness) / `GoalCard.test.tsx` (title; `term-mwh`) / `client.quietToast.test.ts` (INFO) / `plainWords.test.ts` (MWh) | 6 red. The study-state and both-fail ordering cases were green (guards). |
| `NewProjectWizard.templates.test.tsx` B6 ×3 | 3 red |

**Mutants** (scripts and logs in `scratchpad/p30/`: `mutate_fe.py`, `mutate_be.py`, `mutations-fe-final.log`, `mutations-be.log`):
- **Backend: 7 of 7 killed.**
  - M1 / M2: either exemption dropped.
  - M3: widened to every non-extendable unit.
  - M4: widened to a static `p_max_pu < 1`.
  - M5: `flat_projects_root`.
  - M6: key dropped.
  - M7: the `mwh` key renamed.
- **Frontend: 33 of 35 killed.**
  - B4a–d, f;
  - B7a–e;
  - B10a–d;
  - B9a–b;
  - B8a–g;
  - B6a–b;
  - C12a–c;
  - C11 R8, R15b, Q3–Q5.

  B4c (the resize listener dropped) and B10c (Link card ignores the name) first survived. Two tests were added in `5a9052599`. The resize test silences the tour's `MutationObserver`, which in jsdom re-read the target too. Both are now killed.
- **Two equivalent mutants:**
  - B4e (the "apart from the highlight" check dropped) is equivalent by geometry. Every candidate is offset from the target by the 12 px gap, which is greater than the 4 px ring, so a candidate inside the viewport never touches the highlight. The check stays as documentation of the contract.
  - C11-R15 (`jobRunning` dropped from `amber`) is equivalent in the rendered header. The header attaches to a running queue job and sets the simulation store to `running`, so `isRunning` already covers it. The test observed that (`useSimulationStore.status === 'running'` after mount).

**Gate rows** (logs in `scratchpad/p30/`):

| Row | Command (cwd) | Result |
|---|---|---|
| 1 | `PYTHONPATH=/home/user/pypsa-eur:/home/user/pypsa-eur/pypsa-gui/backend /tmp/claude-0/venv/bin/python -m pytest tests/ -m "not slow" -p no:cacheprovider -W ignore -q -o addopts=""` (`pypsa-gui/backend`, HEAD `5a9052599`; the backend is unchanged after `7a97688f0`) | 6733 passed, 31 skipped, 11 deselected, 0 failed in 53 min (`row1.log`; P29: 6716) |
| 2 | same interpreter, `-m pytest <the 14-file set> tests/test_validation*.py tests/test_local_settings*.py tests/test_energy_hub_templates.py -p no:cacheprovider -W ignore -q -o addopts=""` (`pypsa-gui/backend`, HEAD `5a9052599`) | 779 passed, 17 skipped (`row2.log`; P29: 776 + 17) |
| 3 | `npx tsc --noEmit -p .` (`pypsa-gui/frontend`) | 0 errors |
| 4 | `npx vitest run` (`pypsa-gui/frontend`) | 244 files / 2820 passed (`row4.log`; P29: 2793). An earlier run made while the backend suite shared the CPU had one 5 s timeout in `BottomPanel.test.tsx`, plus an "after teardown" `document` error from `prepareTaggingTour`'s 3 s poll. Both files pass alone, and the clean full run passed. |
| 4s | `for i in $(seq 10); do npx vitest run src/components/GuidedTour src/pages/hubDesign src/layout/PropertiesPanel src/layout/AppHeader; done` (`pypsa-gui/frontend`) | 10 / 10 green, 24 files / 219 tests each (`row4s-*.log`) |
| 5 | `PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers node scripts/smoke-guided.mjs --phase P30 --out <scratchpad>/p30/smoke30f` (`pypsa-gui/frontend`, HEAD `5a9052599`) | PASS, 61 screenshots (the first PASS was `smoke30e` on `2787a2530`). Details below this table. |
| 5 | same, `--phase P29 --out <scratchpad>/p30/smoke29` | PASS, 39 screenshots |
| 7 | `git diff bcb6a671f -- 'pypsa-gui/frontend/src/**' \| grep -c uiMode` | 6, all in `AppHeader.uiMode.test.tsx` (store setups and one import). No product line branches on `uiMode`. |

What the P30 smoke checked:
- The tour box was checked 29 times, all inside the viewport and apart from the highlight: the hub tour before a study (6 steps), during the H2 study (6), after it from Results (7) and from Improve (8), and the tagging tour (2).
- Tagging tour: the counter read `1/1` on the Bus step. With `grid_import` opened in Edit from the header search it read `1/2` with Next, then `2/2` on the Link step.
- Preflight on all three templates: `ok`, 0 warnings, no `gen_zero_costs`.
- `/local-settings.projects_root` and the Blank tab line both name `<RUN>/projects`.

Row 7 also covers the behaviour changes that reach Expert, by design of the items. The spec scopes none of them to Guided:
- tour placement and steps (every tour);
- the B9 description;
- the B10 scoping;
- the B6 line;
- the B8 INFO log.

The hub-design changes (B8 line and title, C12) are Guided surfaces.

**Known limitations / probe list:**
- **"Saved to" for a name that already exists.** The allocator (`storage_paths.allocate_storage_path`) may add a suffix (`Name (2)`) for a new row. The Blank tab's overwrite path writes into the existing project. Probe: is `<root>/<name>/` true for every Blank-tab create in local mode?
- **The tour now watches `document.body` (`childList`, `subtree`) while it is open.** Every mutation batch costs one frame callback, which only sets state on a change. Probe for render churn on a page with live polling (the FMEA tab during a sweep).
- **An optional step whose target is absent, but whose `reveal` control is on screen, is still skipped.** An example is a Link selected but not in Edit. The spec rule is "target absent". Including reveal controls would make the pre-study hub tour reach the blocked Results / Improve rail steps.
- **The Bus step after the user selects a Link.** Its target is gone, so the popover docks bottom-left with the existing "This control is not on screen right now…" note. That text is true.
- **`free` placement.** In very small viewports the popover may still overlap the target; the spec allows this ("if none fits"). Probe: a 360×640 viewport on the hub tour.
- **Readiness in the error line.** It is observed through the hub's own key (archetype plus template overrides). A readiness error from the Expert panel's differently keyed query does not show there.
- **The B8 INFO line logs `skipErrorToast` failures from every caller,** including `fetchLocalSettings`'s hosted 404 (once per session) and the hub's quiet reads.

No processes are left running.

**P30 gate fixes.** The gate record is `docs/superpowers/qa/2026-10-05-guided-mode-deferred-gate-P30.md`: NO-GO on B6-1, plus S-1 and S-2. Fixed on `091bf899a`.

- **B6-1** (`44e685888`). The line said `Saved to <root>/<name>/`. That was false for any name the backend's allocator changes (`safe_names.unique_dir_name`): `Grid` beside `grid` is saved in `Grid (2)`, `Study.` in `Study`, and `CON` in `CON_`. On a case-insensitive disk, the folder named was another project's.
  - The line now reads `Saved in a folder named after the project, under <root>/`. It names the root only, so it is true for every name.
  - *Spec deviation (§5.4):* the spec's `Saved to <projects_root>/<name>/` wording is replaced, because the frontend cannot predict the allocated folder.
  - The red test is `NewProjectWizard.templates.test.tsx`: "never names a folder the allocator may change (Grid beside grid)". The test "shows the backend's root" was updated to the new wording. The smoke's B6 assertion now expects that exact sentence with the scratch root.
- **S-1** (`c8ee7c552`). Three spec rows that the gate's mutants found unpinned now have tests. All three were green on arrival, because the code already met each row; each test kills its mutant:
  - (a) free placement scrolls the target into view with `{block:'center'}`, and a placement that fits does not;
  - (b) moving from bus A to bus B clears the request, and reselecting A does not replay it;
  - (c) a fixed all-zero unit with a `p_min_pu` series still warns `gen_zero_costs`.
- **S-2** (`c8ee7c552`). `setCurrentProject` and `requestAssetDetail` now clear `propertiesEditRequest`. Both tests were red first. A same-named bus in another project can no longer open in Edit.
- **Note N-4.** The stale `OverviewPanel.tsx` comment now says that `skipErrorToast` leaves an INFO line since B8.
- **Mutants: 7 of 7 killed** (`scratchpad/p30/mutate_gate.py`, `mutations-gate.log`):
  - G-B6a: the folder name appended again;
  - G-S1a: the free scroll dropped;
  - G-S1a2: centre-scroll on every placement;
  - G-S1b: clear only on a type change;
  - G-S2a: the project switch keeps the request;
  - G-S2b: the asset jump keeps the request;
  - G-S1c: `p_min_pu` also exempts.
- **Rows** (logs in `scratchpad/p30/`):

  | Row | Command (cwd) | Result |
  |---|---|---|
  | 1 | not rerun. The only backend change is one new test, run in row 2 | — |
  | 2 | the row-2 command above (`pypsa-gui/backend`, HEAD `c8ee7c552`) | 780 passed, 17 skipped (`row2-gate.log`) |
  | 3 | `npx tsc --noEmit -p .` (`pypsa-gui/frontend`) | 0 errors |
  | 4 | `npx vitest run` (`pypsa-gui/frontend`) | 244 files / 2826 passed (`row4-gate.log`). See the note after this table |
  | 4s | `for i in $(seq 10); do npx vitest run src/components/GuidedTour src/pages/hubDesign src/layout/PropertiesPanel src/layout/AppHeader src/layout/NewProjectWizard; done` (`pypsa-gui/frontend`) | 10 / 10 green, 28 files / 248 tests each |
  | 5 | `PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers node scripts/smoke-guided.mjs --phase P30 --out <scratchpad>/p30/smoke30g` (`pypsa-gui/frontend`, HEAD `c8ee7c552`) | PASS, 61 screenshots. 29 tour boxes, all inside the viewport and apart from the highlight. The line reads "Saved in a folder named after the project, under `<RUN>/projects/`" |

  Row 4 note: a first run made while row 2 and the smoke shared the CPU had one 5 s timeout in `BottomPanel.test.tsx` ("select-all past the cap"). The rerun on a quiet machine passed in full.

## P30 result: GO at re-gate on `37d446c21`, 2026-10-05
- **First gate:** NO-GO on B6-1: the New-project dialog said "Saved to <root>/<name>/" but the allocator renames some folders (`Grid` beside `grid` → `Grid (2)`, `Study.` → `Study`, `CON` → `CON_`). The line now reads "Saved in a folder named after the project, under <root>/" (spec §5.4 deviation, recorded).
- **Should-fixes:** S-1 (three unpinned spec rows now tested) and S-2 (a pending edit request is cleared on a project switch and an asset-detail jump) fixed.
- **Rows:** row 1 stands (6733 passed + 31 skipped on `5a9052599`; the only later backend change is one test); row 2 780 passed; tsc 0; vitest 2826; smoke P30 PASS (61 screenshots) and P29 PASS. Mutations: 29 of 34 killed at the first gate (survivors pinned since), 9 of 9 at the re-gate; implementer 40 of 42 + 7 of 7.
- **Note:** a pre-migration project row with an absolute path can sit outside the root; the existing overwrite warning covers it.

### P31 phase note (implementation, 2026-10-05, base `4d37e92c5`)

Deferred spec §6 (C2, C3, C4, C5). All four items are done. Commits:
- `8783395c9` C3: `fmtEnergy` below 1 MWh, the ΔEUE header;
- `10d3958c6` C5: the `/eh_review` docstring, with four route tests;
- `2611069ca` C2: `ToasterHost`;
- `1a2fff1c2` smoke `--phase P31`, the real B4 counter and `--self-test` (C4);
- `4fe51e08c` C2 follow-up: `/projects` mounts the dock too (found by the first P31 smoke);
- `1a2b91c75` smoke: the toast check also needs frames with the whole toast on screen;
- plus this note.

**Anchor drift.** Re-checked on `4d37e92c5`:
- `main.tsx` `<Toaster>` is at `:47-57`; the dock is `components/AssistantDock.tsx`, mounted by `App.tsx:703` **and** `pages/ProjectsHomePage.tsx:780`.
- `shared.tsx` `fmtEnergy` is at `:743-750` (unchanged).
- The only dropped ΔEUE header is the class-B "top outages" table in `EhReferenceDesignPanel.tsx:1887` (dropped in `9bd059033`, P22.9-FE). `FmeaTab.tsx` has no ΔEUE column.
- `smoke-guided.mjs`'s vacuous check is at `:1292` (inside `phaseP24`).
- `routers/results.py` `/eh_review` docstring is at `:1534-1544`.

**What was built:**

- **C2 (`components/ToasterHost.tsx`, new; `main.tsx`).** The app's one `<Toaster>` moved out of `main.tsx` into a small `ToasterHost` that reads `assistantDockOpen` and `assistantDockWidth`. While the dock is open on a page that mounts it (`/app`, `/projects`), `containerStyle.right` is the dock width + 16 px (`TOAST_GAP`); otherwise (dock collapsed; login, admin) 16 px. Position, toast style and every toast call are unchanged.
- **C3 (`pages/results/shared.tsx`, `EhReferenceDesignPanel.tsx`).**
  - `fmtEnergy`: below 1 MWh → `${mwh.toFixed(3)} MWh`; zero (and `-0`) → `0 MWh`. From 1 MWh up, and for a missing value, nothing changes. `digits` is not used below 1 MWh (always three decimals, as the spec says).
  - The class-B ΔEUE header reads `ΔEUE (MWh)` again. Its cells are bare MWh numbers from a new `fmtMwhNumber` (grouped, two decimals from 1 MWh, three below, `0` for zero, `—` when missing). See deviation 2.
  - Callers checked: every `fmtEnergy` import (`AggregatedOverview`, `Dispatch`, `CapacityExpansion`, `Economics`, `EhReferenceDesignPanel`). Only values below 1 MWh render differently (KPI cards, table cells, chart ticks and tooltips): `0.00 kWh` → `0 MWh`, `250.00 kWh` → `0.250 MWh`. The local `fmtEnergy` copies in `Curtailment.tsx`, `LostLoadTab.tsx` and `TimeSeriesManager.tsx` are separate functions and untouched. No CSV / JSON export goes through `fmtEnergy` (the export helpers format raw values), so exports are byte-identical.
- **C4 (`scripts/smoke-guided.mjs`).**
  - The re-gate B4 path moved from `phaseP24` into `b4StudyReadFailure(browser, project, consoleLines, { selfTest })`; `phaseP24` calls it unchanged, so P24 / P30 get the real counter too.
  - After recovery is confirmed, `page.on('request')` counts `/api/results/eh_study` requests and `page.on('response')` records each such response with status ≥ 500. The check is "no failing eh_study response after recovery (N request(s) seen, M with status ≥ 500)". The old check compared a counter that only the already-removed route handler incremented, so it could never fail.
  - `--self-test` (new; P31 only, otherwise usage exit 2): after recovery one 500 route is installed again and one read is driven through it with a page `fetch` (the app sends no `eh_study` read while no study runs: it polls only a running study). The run must print `FAIL` and exit 1.
  - `--phase P31` = P26 (every step) + on each template the toast check + the B4 path on the P26 data-center project. The toast check: an init script samples, every animation frame, the "… from template" toast's bar and `chat-send` while both are rendered, and records their intersection. It asserts the toast was shown, that some shared frames had the whole toast on screen, and that the overlap was 0 in every shared frame.
- **C5 (`routers/results.py`).** The docstring now reads: 200 `running` while the study runs; otherwise 200 `ok` (plus `stale`) when a report exists — the stored one, or the study record's copy (`stale: true`); 204 when there is no stored report and no study record with a report. So a study whose worker raised (record, no report) returns 204, since a study clears the stored report when it starts; an aborted or stage-failed study keeps its partial report and returns 200 `ok`, with `summary.verdict` null unless MC certification finished before the stop. Each sentence is pinned by a test in `tests/test_eh_review_route.py` (below).

**Deviations (with reasons):**
1. **C2: a right offset, not `bottom: DOCK_HEIGHT + 16`.** The dock is a right-hand column (`AssistantDock`, `border-l`, its width from the store), not a bottom drawer; its composer and Send are at the bottom of that column. Raising the toast by a "dock height" would not clear Send, so the toaster's right inset is the dock width + 16. No `DOCK_HEIGHT` exists or was added; the width is the store's `assistantDockWidth`, which is what the dock renders (`style={{ width }}`, `shrink-0`). The route condition includes `/projects`: the first P31 smoke failed with a 1121 px² overlap because the template toast is raised on `/projects`, which mounts the same dock (`smoke31a`); red test "on /projects … sit left of it", fixed in `4fe51e08c`.
2. **C3: the ΔEUE cells are bare MWh numbers, not `fmtEnergy`.** A header `ΔEUE (MWh)` above a `fmtEnergy` cell would be false for any value from 1 000 MWh (`315.61 GWh`) and was the reason P22.9-FE dropped the unit. With the unit in the header, the cells must be in MWh. The P22.9 Bug 1 intent (no raw floats such as `315605.35`) holds: the cell is `315,605.35`. Only the ΔEUE header regains its unit (the spec's scope); the frontier / redundancy ENS and "Built" headers keep their unit-less headers with a unit in each cell, which is true as it is.
3. **C4: the self-test drives its one failing read with a page `fetch`.** A 500 route alone produces no response while no study runs, so the self-test would PASS vacuously. The fetch goes through the same routing and response events as the app's reads.
4. **C5: the spec's proposed text was not true as written.** "An aborted study returns 200 `ok` with `summary.verdict: null`" fails when the stop comes after `mc_certify` (it runs before `fmea_top`, `redundancy`, `levers`, `dtc_*`): the record's report keeps the verdict. A study whose pipeline marked a stage `failed` is also 200 `ok` (partial report). The docstring says both, and each case has a test.

**Changed assertions (one line each):**
- `EhReferenceDesignPanel.formatting.test.tsx` "class-B ΔEUE, frontier ENS, …": the ΔEUE value is no longer expected as `fmtEnergy(315605.35, 2)` (the cell is now the bare MWh number, asserted by the new header test); the `0.25` comment reads `0.250 MWh` and the panel must not contain `kWh` (C3).

**Red → green:**

| Test | Before the fix |
|---|---|
| `shared.test.ts` "fmtEnergy (P31 C3)" ×3, "fmtMwhNumber (P31 C3)" ×1 | 3 red (below 1 MWh, zero, `fmtMwhNumber` missing); "unchanged from 1 MWh up" green (a guard) |
| `EhReferenceDesignPanel.formatting.test.tsx` header test + the changed case | 2 red |
| `ToasterHost.test.tsx` (new, 5) | red (no module); the `/projects` case red again before `4fe51e08c` |
| `test_eh_review_route.py` C5 ×4 (worker raised → 204; aborted → 200 `ok`, verdict null; aborted after MC → verdict kept; stage-failed → 200 `ok`) | green on arrival: docs item, the tests pin the docstring's sentences against the code (each kills a mutant below) |
| smoke `--phase P31` toast check | FAIL before `4fe51e08c` (overlap 1121 px² on `/projects`), PASS after |

**Mutants** (scripts and logs in `scratchpad/p31/`: `mutate_fe.py`, `mutate_be.py`, `mutations-fe.log`, `mutations-fe-c2f.log`, `mutations-be.log`):
- **Frontend: 13 of 13 killed.** C3a zero not special; C3b back to kWh; C3c `digits` honoured below 1; C3d header unit dropped; C3e ΔEUE cell through `fmtEnergy`; C3f `fmtMwhNumber` scales to kWh; C3g `fmtMwhNumber` zero as `0.000`; C2a offset ignores the dock; C2b ignores the route; C2c ignores open / collapsed; C2d ignores the width (420 fixed); C2e bottom instead of right; C2f `/projects` dropped from the dock routes.
- **Backend: 5 of 5 killed.** B1 no fallback to the record's report; B2 a raised worker keeps a report; B3 verdict always null; B4 a `failed` record is `no_data`; B5 an `aborted` record is `no_data`.
- **Smoke: the vacuous counter.** A copy of the script with `watching = true` removed, run with `--phase P31 --self-test`: **PASS** (exit 0, "0 eh_study request(s) seen") — so the self-test tells a counting check from a vacuous one; the real script's self-test FAILs (below).

**Gate rows** (logs in `scratchpad/p31/`):

| Row | Command (cwd) | Result |
|---|---|---|
| 1 | `PYTHONPATH=/home/user/pypsa-eur:/home/user/pypsa-eur/pypsa-gui/backend /tmp/claude-0/venv/bin/python -m pytest tests/ -m "not slow" -p no:cacheprovider -W ignore -q -o addopts=""` (`pypsa-gui/backend`, HEAD `1a2b91c75`) | 6738 passed, 31 skipped, 11 deselected, 0 failed in 53 min (`row1.log`; P30: 6733 + 1 gate test; +4 C5 tests) |
| 2 | same interpreter, `-m pytest <the 14-file set> tests/test_validation*.py tests/test_local_settings*.py tests/test_energy_hub_templates.py -p no:cacheprovider -W ignore -q -o addopts=""` (`pypsa-gui/backend`, HEAD `1a2fff1c2`; the backend is unchanged after `10d3958c6`) | 784 passed, 17 skipped (`row2.log`; P30: 780 + 17; +4 C5 tests) |
| 3 | `npx tsc --noEmit -p .` (`pypsa-gui/frontend`, HEAD `1a2b91c75`) | 0 errors |
| 4 | `npx vitest run` (`pypsa-gui/frontend`, HEAD `1a2b91c75`) | 245 files / 2836 passed (`row4.log`; P30: 244 / 2826). The four `*.expertUnchanged` snapshots pass unchanged |
| 4s | not required: no store, polling or chat change (`ToasterHost` only reads the store) | — |
| 5 | `PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers node scripts/smoke-guided.mjs --phase P31 --out <scratchpad>/p31/smoke-final-P31` (`pypsa-gui/frontend`, HEAD `1a2b91c75`) | PASS, 43 screenshots. Toast ∩ Send = ∅ on all three templates: 139 / 149 / 154 shared frames, 73 / 81 / 88 with the whole toast on screen (left of the dock, e.g. toast x 654–1004, Send x 1378–1432). B4: "no failing eh_study response after recovery (0 request(s) seen, 0 with status ≥ 500)" |
| 5 | same, `--phase P31 --self-test --out <scratchpad>/p31/smoke31-selftest` | **expected FAIL**, exit 1: "--self-test: the driven eh_study read answered 500" → `FAIL — assertion failed: no failing eh_study response after recovery (1 eh_study request(s) seen, 1 with status ≥ 500: 500)` (every P26 and toast step before it passed) |
| 5 | same, `--phase P30 --out <scratchpad>/p31/smoke-final-P30` (regression) | PASS, 61 screenshots (P24's B4 path now with the real counter: 0 failing responses) |
| 5 | same, `--phase P29 --out <scratchpad>/p31/smoke-final-P29` (regression) | PASS, 39 screenshots |
| 7 | `git diff 4d37e92c5 -- 'pypsa-gui/frontend/src/**' \| grep -c uiMode` | 0. No `uiMode` branch was added or changed |

Row 7 also covers the behaviour changes that reach Expert, by design of the items (the spec scopes none of them to Guided):
- the toast position (every mode, every toast) while the dock is open;
- `fmtEnergy` below 1 MWh on every Expert results tab that uses it;
- the ΔEUE column of the EH reference-design panel (an Expert surface).

No `chat_tools_schema.TOOLS`, tool description or system-prompt change (row 2's `test_guided_mode_prompt.py` is green).

**Known limitations / probe list:**
- **A tiny non-zero energy reads as zero.** Below 0.0005 MWh (0.5 kWh), `fmtEnergy` shows `0.000 MWh` and `fmtMwhNumber` `0.000` (the spec's three decimals). Probe: is any results value in that range meaningful (e.g. a near-zero curtailment KPI)?
- **Chart axis ticks below 1 MWh** (`fmtEnergy(v, 0)` in `AggregatedOverview`) now read `0.500 MWh` (three decimals) where they read `500 kWh`. Probe: a small network whose energy axis spans less than 1 MWh.
- **The toast now sits over the main area.** On `/app` it lands at the bottom-right of the canvas / Properties panel / full-screen tab, beside the dock. Probe: a toast over the Properties panel's bottom buttons.
- **Narrow windows.** With a 420 px dock the toaster has `viewport − 452 px` of width; at about 800 px or less, long toasts wrap into a narrow column. Probe: a 1024×768 window and a 360-px-wide one.
- **Dock resizing** writes the store on every mouse move, so an open toast follows the dock edge during a drag (intended).
- **C5 edge.** "A study clears the stored report when it starts" is true for `run_eh_study` (it pops the store before copying the network). An exception raised before that line (stage validation, already done at request time) would leave an earlier stored report, so `/eh_review` would then answer 200 with that earlier report. Probe: is any exception reachable there?
- **The self-test's failing read is a page `fetch`, not an app read** (deviation 3). It proves the counter; it does not prove the app would re-read after recovery.

No processes are left running.
