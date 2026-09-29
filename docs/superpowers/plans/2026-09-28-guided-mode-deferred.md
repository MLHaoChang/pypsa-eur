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
