# Guided mode: deferred items — assessment and plan (P27+)

**Date:** 2026-09-28. **Base:** branch `claude/epic-allen-k2t1c4` at `00a35a5` plus the P26 final-gate fixes in the working tree (chips, GoalCard hint, ImproveCard aim, "Set a goal", `outage-driven`).
**Spec:** [`../specs/2026-09-27-guided-mode.md`](../specs/2026-09-27-guided-mode.md). **Plan it follows:** [`2026-09-27-guided-mode.md`](2026-09-27-guided-mode.md).
**Sources read:** the close-out note, every gate file (P22.9-BE/FE, P23, P24-BE/FE, P25, P26), the spec review, the Expert click-through, spec §1 / §8 / §10.

Every file:line below was checked in the tree on 2026-09-28. `FE` = `pypsa-gui/frontend/src`, `BE` = `pypsa-gui/backend`.

## 0. Summary

| Bucket | Open | Already fixed (verified) |
|---|---|---|
| Safety and data integrity | A1–A6, A8 (7) | A7 |
| First-time-user friction | B1–B10 (10) | B11–B16 |
| Cosmetic | C1–C6 (6) | C7–C9 |
| Non-goals (owner decision) | D1–D3 (3) | — |

26 open items in 8 phases (P27–P31 committed, P32–P34 optional). 11 owner decisions, §4.

## 1. Already fixed — no work (verified in code)

| # | Item | Where it came from | Evidence now |
|---|---|---|---|
| A7 | Unit test for the topology restore did not pin `generators.control` | P22.9-BE minor | `BE/tests/test_live_network_untouched.py:185,196` asserts `n.generators["control"]` |
| B11 | Greeting stuck on "study running" after the hub closes | P26 gate B1 | `FE/components/ChatLaunchGreeting.tsx:135` `refetchInterval: ehStudyRefetchInterval` (shared module `pages/results/ehStudyPoll.ts`) |
| B12 | Destructive cards hid their target | P26 gate B2 | `FE/components/ChatPanel.tsx:376-455` summaries for every destructive / execution tool; fallback names the first identifying arg; Details open for `destructive` (`:684`) |
| B13 | Greeting chips "Check adequacy" / "Summarize this solve" | P26 gate | working tree `ChatPanel.tsx:1337-1345` "Explain my results" / "What should I improve?" |
| B14 | "outage-driven"; Improve text vs. action; H₂ hub no way to Goal | P26 gate | working tree `plainWords.ts:11`, `ImproveCard.tsx:56-60`, `ResultsCard.tsx:56-62` (`hub-results-set-goal`), `GoalCard.tsx:93-99` |
| B15 | Failed / aborted study read "Not solved yet." | P26 gate note 2 (half) | `ChatLaunchGreeting.tsx:61-63` |
| B16 | P23 N-R2: a project switch during a tour consumed the new project's auto-open | P23 re-gate | `FE/App.tsx:201` returns before marking (skip without marking) |
| C7 | VOLL / frontier / fmea_top effect not translated | P24-FE re-gate | `plainWords.ts:23-26` |
| C8 | P24-BE N6 (hook imports a page), N7 (`import_p_nom_mw` non-finite) | P24-BE | `FE/hooks/useStartFmeaSweep.ts:6-10` imports no page; `BE/services/adequacy/eh_readiness.py:307` `_finite_or_none` |
| C9 | P25 notes: queue ignores `notReady`, deny keeps sending the group, sanitiser one-pass, stub "applied" for a read; P22.9-FE notes 1, 2, 6 | P25 / P22.9-FE | spec §10 P25 addenda; `ChatPanel.tsx:2521-2535`; gate re-checks |
| — | Smoke "exits 0 on FAIL" (P26 note 6) | P26 | not reproduced: `scripts/smoke-guided.mjs:133` `check` throws, `:1892-1901` sets `code = 1` and `process.exit(code)`. Probe-copy artefact, as the reviewer suspected. |

## 2. Open items

Legend: **Hurts** = first-time user (FTU) / expert / data integrity (DI) / safety. **Size** S ≤ ½ day, M ≤ 2 days, L > 2 days. **Risk** to Expert / system.

### A. Safety and data integrity

#### A1. Edits made during a live-network study are reverted; the restore writes outside the lock
- **Problem.** A user edit to `control` / `sub_network` / `generator` (Bus, Generator, passive branches) made while a sweep / coupling / margin loop runs on the live network is silently overwritten when the study ends. **Hurts:** DI, expert (Guided users cannot reach these columns).
- **Evidence.** `BE/services/adequacy/sweep.py:87-125` `preserve_bus_topology` copies the columns on entry and writes them back in `finally` with no lock. Edit routes take the lock (`BE/services/network_crud.py:211,387,441`) but are never refused during a study. `freeze_capacities`' undo has the same shape (P22.9-BE gate, known limitation 1).
- **Root cause.** The restore is a snapshot/put-back, not a diff; it runs after the study's last solve, outside `PyPSAService.get_lock()`, so an interleaved edit is lost, and a concurrent edit can even interleave mid-write.
- **Options.**
  1. Take the lock around the restore, and restore only the cells the topology pass changed (compare against the post-solve values, write back the pre-study value only where the current value equals the solver's value). Keeps edits; complexity in the diff logic.
  2. Refuse network edits with the existing structured 409 while a live-network study runs (`BE/routers/projects.py:1516` "the structured 409 for an action a live study forbids" already exists for saves), plus take the lock around the restore. Simple, honest, and the UI already renders `study_in_flight` 409s.
- **Recommendation:** 2, then 1 only if users hit the refusal. The study's solves already hold the lock in bursts; edits during a study were never supported.
- **Files.** `BE/services/adequacy/sweep.py` (lock in `preserve_bus_topology`, also the `freeze_capacities` undo), `BE/services/network_crud.py` or the route edge (`study_in_flight` refusal), `FE/api/client.ts` (`QUIET_TOAST_CODES` if needed), `FE` edit toasts.
- **Tests (red first).** `BE/tests/test_live_network_untouched.py`: (a) `test_restore_runs_under_the_network_lock` — a lock spy asserts `__enter__` around the write-back; (b) `test_edit_during_live_study_is_refused_409` via the HTTP edit route with a fake running study; (c) `test_edit_after_study_is_kept`. FE: `client.quietToast.test.ts` for the new code if quiet.
- **Size:** M. **Risk:** Expert medium (edits during a sweep now 409; message must be plain). System low.
- **Owner decision:** D-1 (refuse vs. merge).

#### A2. Bug 6: one-off 409 on resume after a backend restart with a stale tab open
- **Problem.** After a backend restart, a tab that still holds `currentProject = X` autosaves with `expect=X`; if the backend has meanwhile bound another project (another tab, resume-on-start), the identity guard refuses. The app recovers, but the user sees a 409 / "reload to resync". **Hurts:** DI (the guard is right), FTU (opaque error).
- **Evidence.** Guard: `BE/routers/projects.py:1686-1694`. Autosave handling: `FE/layout/Sidebar.tsx:846-861` (auto → app log only; manual → toast). Restart recovery: `FE/App.tsx:446-481` reloads the project only when the backend network is **empty** (`bus_count === 0`); it never compares the backend's `bound_project` (available on `GET /api/chat/history`, `BE/routers/chat.py:734`) with the tab's project.
- **Root cause.** The recovery effect keys on "empty network", not on "bound to a different project", so a backend that resumed project Y while the tab shows X is never resynced.
- **Options.**
  1. Extend the recovery effect: read the backend's bound project (a small `GET /api/projects/active` or reuse `history.bound_project`); if it differs from `currentProject` and the user's tab is idle, show one line "The app is now on Y — reload X?" with a button; autosave is suspended until resolved. Explicit, safe.
  2. Auto-reload X into the backend. Simple but it swaps the network out from under the other tab (the 2026-05-28 hazard the guard exists for).
- **Recommendation:** 1.
- **Files.** `BE/routers/projects.py` (`GET /api/projects/active` → `{bound_project}`; add to the route inventory fixture), `FE/api/projects.ts`, `FE/App.tsx` recovery effect, `FE/layout/Sidebar.tsx` (suspend autosave while mismatched).
- **Tests.** BE: `test_projects_active_route` (+ `test_chat_tools_endpoint_map` row). FE: `App.recovery.test.tsx` "bound to another project → banner, no autosave, no load"; `Sidebar.autosave.test.tsx` "identity 409 → autosave suspended, one WARN". Smoke: a P22.9 step that restarts uvicorn with the tab open, then asserts no 409 in the console and the banner.
- **Size:** M. **Risk:** Expert low (banner only). System low.
- **Owner decision:** D-2.

#### A3. Send gate: the session's bound profile is not exposed to the client
- **Problem.** Send is gated on the **active** profile's readiness. A session bound to another, ready profile after a reload (`profileId` null in the store) is gated wrongly; the escape is to re-pick the profile. **Hurts:** FTU (locked out), expert.
- **Evidence.** `FE/components/ChatPanel.tsx:1511-1512` `notReady = chat_ready === false && (profileId == null || profileId === active.id)`. `GET /api/chat/history` (`BE/routers/chat.py:703-800`) resolves `resolved_profile` from the last turn but returns only `last_session_id` / `bound_project`; `session_init` carries `profile_id` (`BE/services/chat_service.py:1417-1424`) but only once a turn starts.
- **Root cause.** The readiness rule needs the session's binding before any turn; no read endpoint reports it.
- **Options.**
  1. Add `bound_profile_id` + `bound_profile_ready` to the `/history` payload (it already resolves the profile), and let the FE gate on `profileId ?? bound ?? active`. One field, one consumer.
  2. Per-profile readiness in `/chat/health` (`profiles: [{id, chat_ready}]`) and the FE picks. More general; also fixes the key-offer rule for a non-active picked profile (P26 note 5).
- **Recommendation:** 2 (it removes both known caveats at once), with 1's `bound_profile_id` on `/history`.
- **Files.** `BE/routers/chat.py` (`chat_health`, `chat_history`), `FE/api/chat.ts` types, `FE/components/ChatPanel.tsx` (gate), `FE/components/ChatLaunchGreeting.tsx` (key offer).
- **Tests.** BE: `test_chat_health_per_profile_readiness`, `test_history_reports_bound_profile`. FE: `ChatPanel.sendGate.test.tsx` "bound to a ready non-active profile after reload → Send enabled"; `ChatLaunchGreeting.test.tsx` "picked ready non-active profile → no key offer".
- **Size:** M. **Risk:** Expert low (gate loosens only when the backend says ready). System low.
- **Owner decision:** D-3.

#### A4. The greeting ignores study staleness
- **Problem.** After the user edits the network, the Guided greeting still says "A study has run on this network — its results are in Hub design", although the review reports `stale: true`. **Hurts:** FTU (the owner's honesty rule).
- **Evidence.** `ChatLaunchGreeting.tsx:64-67` reads the study record and `status.dispatch`; the EH study solves a copy so `dispatch` never becomes `stale`. The review's boolean `stale` (`useHubReview`, `FE/pages/hubDesign/useHubData.ts:50-53`) is not read.
- **Root cause.** Two staleness signals (live dispatch vs. review) and the greeting reads the wrong one for the hub path.
- **Options.**
  1. Greeting reads `eh_review` on the hub's key (`nk(project,'results','eh_review')`, Guided only, no poll) and says "A study has run, but the network changed since — run it again in Hub design." when `stale`.
  2. Backend: `GET /eh_study` gains `stale` too. Additive, but a second source of the same flag.
- **Recommendation:** 1.
- **Files.** `ChatLaunchGreeting.tsx`. **Tests.** `ChatLaunchGreeting.solvedState.test.tsx` "stale review → stale sentence"; Expert unchanged test (query disabled). **Size:** S. **Risk:** none to Expert; system: one more cached read, same key as the hub.

#### A5. Transient stale FMEA tab after a sweep; leftover `sweep.isSuccess` after a project switch
- **Problem.** (a) Opening the FMEA tab within ~2 s of a sweep ending shows "Sweeping…" and partial rows until the shared query refetches. (b) `ImproveCard`'s `enabled: sweep.isSuccess` stays true after a project switch, so the next project's `fmea_modes` is fetched needlessly. **Hurts:** FTU (a), system hygiene (b).
- **Evidence.** `FE/pages/hubDesign/cards/ImproveCard.tsx:106-115` (same key and 2 s poll as `FmeaTab.tsx:60-66`); `useStartFmeaSweep` mutation state is per mount, not per project. The smoke now waits for the count to settle (`smoke-guided.mjs:1748-1762`), which hides (a).
- **Root cause.** (a) The poll stops on the first `sweep_status !== 'running'` sample, but the backend's rows land after the closing re-solve; the last poll can precede the final rows. (b) `useMutation` state is not keyed by project.
- **Options.**
  1. Poll once more after the transition (`refetchInterval` returns 2000 for one extra tick after leaving `running`), and reset the mutation on project change (`sweep.reset()` in an effect keyed on `project`). Small.
  2. Backend: `fmea_modes` reports `sweep_status: 'finalising'` until the rows are final. More truthful, two files.
- **Recommendation:** 1, and 2 only if the transient persists.
- **Files.** `ImproveCard.tsx`, `FmeaTab.tsx` (shared helper `fmeaModesRefetchInterval` in `hooks/useStartFmeaSweep.ts`). **Tests.** `ImproveCard.test.tsx` "one extra poll after the sweep ends" and "project switch resets the sweep state"; `FmeaTab.test.tsx` same interval helper. Smoke: revert the settle loop to a single read after the extra tick (keep the true counts 8 / 4 / 6). **Size:** S. **Risk:** none to Expert beyond one extra request per sweep.

#### A6. In-app project switch mid-study is not covered end to end
- **Problem.** The P26 gate did not run a project switch while a study runs in the real app. Backend isolation tests pass and the queries are project-keyed, but the browser path (Start-card 409 message, greeting, ImproveCard) is unproven. **Hurts:** DI (unknown), FTU.
- **Evidence.** P24-FE note (409 with the plain message, template buttons disabled while running — `spec §10 P24-FE addendum`), P26 "System level". No smoke step switches project mid-study (`smoke-guided.mjs` phases P24–P26).
- **Root cause.** Coverage gap, not a known defect.
- **Recommendation.** Add a P27 smoke step: start a study on template 1, open Projects home, click template 2 → assert the 409 line, then wait for the study, switch, assert the greeting / cards show project 2's state and no cross-project rows. Plus a vitest `projectActions.switch.test.ts` case for the hub queries.
- **Size:** S. **Risk:** none (tests only).

#### A8. A template project created from chat rebinds the backend without a `project_rebound` frame
- **Problem.** `create_project_from_template` run by the assistant swaps and binds the backend to the new project, but the frontend is not told, so `currentProject` stays on the old name and the next autosave sends `expect=<old>` → identity-guard 409 (the 2026-06-08 incident class). **Hurts:** DI.
- **Evidence.** `BE/routers/projects.py:1332-1346` (bind atomically with the swap, "mirroring activate_project"); `BE/services/chat_service.py:125-131` `PROJECT_REBINDING_TOOLS` lists `activate_project`, `load_project`, `save_project_as`, `rename_project`, `restore_project_snapshot` only; `chat_tools.py:2374-2379` calls the same route as the wizard.
- **Root cause.** The rebinding-tools set was not extended when the template tool was added (C9).
- **Fix.** Add `create_project_from_template` (and `import_project_bundle` if it binds — check `:1475`) to `PROJECT_REBINDING_TOOLS`. The FE handler (`ChatPanel.tsx:2322-2345`) already mirrors the switch. D1 (mode switch) builds on this frame.
- **Tests.** BE `test_chat_project_rebound.py`: a scripted `create_project_from_template` turn yields `project_rebound{via_tool}` (red: no frame). FE: none needed beyond the existing handler test. Smoke P25: stub branch that creates a template from chat, then autosave once → no 409 in the console.
- **Size:** S. **Risk:** none to Expert (a frame that was missing).

### B. First-time-user friction

#### B1. Raw tool progress lines in Guided ("… preparing run_eh_study", "→ run_eh_study", "✓ run_eh_study")
- **Evidence.** `ChatPanel.tsx:2196-2216` (`tool_preparing`, `tool_request`), `:2239-2260` (`tool_result` "✓ tool"); Guided render hook `guidedToolLine` `:468-472` handles only the declined lines; `:3097-3138` renders the rest raw in monospace.
- **Root cause.** The three lifecycle frames append three separate `tool` messages; the Guided renderer has no vocabulary for them.
- **Options.**
  1. Render-only (like P26 item 6): in Guided, `guidedToolLine` maps `… preparing X` → hidden, `→ X` → "Working: <guidedCardSummary(X) in progress form>…", `✓ X` → "Done: <summary>", `✗ X` → "Could not: <summary>", with the raw line under Details. The transcript and Expert are unchanged.
  2. Collapse the three into one message updated in place (store change). Cleaner but touches the persisted transcript shape.
- **Recommendation:** 1. Needs `guidedCardSummary` to accept the tool name alone (args are not on these frames): add a short per-tool verb table (`run_eh_study` → "the reliability study", etc.) and the "on <target>" fallback.
- **Files.** `ChatPanel.tsx` (`guidedToolLine`, summary table). **Tests.** `ChatPanel.sendRequest.test.tsx` "P27: Guided tool progress lines are plain (three frames → Working / Done, raw under Details)", Expert snapshot unchanged. Smoke P26: assert no `chat-message` in Guided contains `→ ` or `preparing`. **Size:** S. **Risk:** none to Expert (branch on `uiMode`).

#### B2. The FMEA tab reached from Guided is Expert-styled
- **Evidence.** `FE/pages/Results.tsx:526-531` eyebrow "SIMULATION · RESULTS", title "Optimization results" in both modes (only the subtitle branches). `FmeaTab.tsx:237` column "Class" shows `A/B/C/D` letters (`:257`), `:274` `EngineBadge` shows `copt` / `lp_proxy` / `expert`, `:258-259` occurrence basis "FOR", header prose "€/yr criticality … fidelity" (`:166-171`). Catalogue hovers exist in Hub design (`Term`) but not here.
- **Root cause.** Spec §1 obstacle 7 only made the tab reachable; the tab body was never in Guided scope.
- **Options.**
  1. Guided variant of the same tab: in Guided, `PageHeader` reads eyebrow "HUB DESIGN · RESULTS", title "Reliability results"; the Class column shows a plain label ("Generator outage" / "Link outage" / "Stress scenario" / "Your own row") with the letter in a hover; the engine badge becomes a hover on the row ("estimated by …"); the header prose becomes two plain sentences; "FOR" → "outage rate". Expert renders byte-identical (snapshot).
  2. A separate Guided risk table component fed by the same query. No conditional markup, but duplicated table logic and exports.
- **Recommendation:** 1, with a `fmeaColumnLegend` helper and a `Term` hover per column; reuse the catalogue keys (`eh_fmea_guide.json` gains `fmea_class_*`, `fmea_engine`, `fmea_severity`, plain, ≤ 30 words, no jargon — `test_guides.py` plainness check).
- **Files.** `Results.tsx`, `FmeaTab.tsx`, `BE/data/guides/eh_fmea_guide.json`, `FE/pages/hubDesign/shared/Term.tsx` (import). **Tests.** `FmeaTab.guided.test.tsx` (plain labels, no letters / badges in Guided), `FmeaTab.expertUnchanged` snapshot, `Results.expertUnchanged` snapshot, `test_guides.py` catalogue keys. Smoke P26: the FMEA screenshot step asserts the Guided title and that no cell text matches `^(copt|lp_proxy)$`. **Size:** M. **Risk:** Expert none (snapshot). System none.
- **Owner decision:** D-4.

#### B3. Bug 5: FMEA rows with severity 0 show `€0.0` with no explanation
- **Evidence.** Class A: `BE/services/adequacy/copt.py:1147-1151` `severity = crit/occ if occ > 0 else 0.0`, `crit = delta_eue * voll`; so `€0.0` means **either** the outage costs nothing (`delta_eue == 0`: the site copes) **or** no outage data (`occ == 0`) **or** VOLL 0. Class B: `sweep.py:604-620` same, plus `in_scope == False` → 0 by design. Class C: `stress.py:644-655` `severity = delta * voll`. The payload carries `delta_eue_mwh` and sometimes `note` (`copt_endpoint.py:87-90`); `FmeaTab.tsx:260` prints `fmtCurrency(r.severity_eur, 1)` and reads neither.
- **Root cause.** Three different zero reasons are collapsed into one number; the UI has no field to explain it.
- **Options.**
  1. Backend additive `zero_reason: "no_shortfall" | "no_outage_data" | "unpriced" | "out_of_scope" | null` on every `per_mode` row (one helper used by copt / sweep / stress), FE renders "no shortfall — the site copes" (Guided) / "0 (no shortfall)" (Expert hover) instead of `€0.0`. One source of truth, matches the hub cards' "no measurable cost".
  2. FE-only inference from `delta_eue_mwh`, `occurrence_per_year`, `in_metric_scope`. No backend change, but the "unpriced" case needs VOLL, which the row does not carry.
- **Recommendation:** 1.
- **Files.** `BE/services/adequacy/{copt,sweep,stress}.py` (+ a `zero_reason()` helper in `worksheet.py` or `metrics.py`), `copt_endpoint.py`, `FE/api/simulation.ts` type, `FmeaTab.tsx`, `FE/pages/results/EhReferenceDesignPanel.tsx` top-risks (already "no measurable cost"; align wording). **Tests.** BE: `test_fmea_zero_reason.py` — one case per reason per engine (red: field absent). FE: `FmeaTab.formatting.test.tsx` "€0 rows show the reason". Smoke P26: the data-center FMEA screenshot asserts `genset_1` row text contains "no shortfall". **Size:** M. **Risk:** Expert: a new column/hover only (additive); exports unchanged (`*CsvRows` helpers untouched) unless the owner wants the reason exported (D-5).
- **Owner decision:** D-5.

#### B4. Obstacle 10: the tour popover covers its target or overflows the viewport
- **Evidence.** `FE/components/GuidedTour.tsx:194-206`: fixed width 320; placed below the target if `below + 180 < vh` else at `rect.top − 192` — the popover's real height is unknown (intro + enter text), so a tall step clips at the bottom (P22.9-FE screenshot 10) or overlaps the target when placed above.
- **Root cause.** Placement uses a guessed height and never re-measures.
- **Options.**
  1. Measure the popover with a ref after render (`useLayoutEffect` + `ResizeObserver`), then choose below / above / right / left by fit, clamp to `[8, vh − h − 8]`, and never intersect the target rect; fall back to centred with the target scrolled into view. Also `max-height: calc(100vh − 16px); overflow: auto`.
  2. Use a positioning library. Adds a dependency for one component; the spec forbids new deps casually.
- **Recommendation:** 1.
- **Files.** `GuidedTour.tsx`. **Tests.** `GuidedTour.test.tsx` with mocked `getBoundingClientRect` and `innerHeight`: "a tall popover near the bottom goes above and does not overlap the target"; "never outside the viewport". Smoke P24: the tour step screenshots assert `guide-tour` bounding box ∩ target box = ∅ and box within viewport. **Size:** S. **Risk:** none.

#### B5. Obstacle 11: the data-center template raises a `gen_zero_costs` warning on 2 generators
- **Evidence.** Validator `BE/services/validation_service.py:1749-1764` flags generators with static `capital_cost == marginal_cost == overnight_cost == 0`. Template `BE/project_templates/eh_templates.py:112-114` `grid_supply` has `marginal_cost=0.0` static **and** a time-varying `generators_t.marginal_cost`; `:140-141` `rooftop_pv` is fixed (`p_nom_extendable=False`) with zero marginal cost, which is legitimate. The templates test only checks readiness warnings (`test_energy_hub_templates.py:55`).
- **Root cause.** The validator ignores time-varying marginal cost and treats fixed zero-marginal renewables as an error.
- **Options.**
  1. Validator: skip generators with a `generators_t.marginal_cost` column; for non-extendable generators, downgrade to `info` (or skip) — capital cost is irrelevant to a fixed asset. Templates untouched.
  2. Add nominal costs to the templates. Hides a validator false positive and changes the template's dispatch.
- **Recommendation:** 1.
- **Files.** `validation_service.py`. **Tests.** `BE/tests/test_validation_gen_costs.py`: time-varying marginal cost → no warning; fixed PV with zero costs → no warning; extendable all-zero → warning stays. `test_energy_hub_templates.py`: all three templates validate with no `gen_zero_costs`. **Size:** S. **Risk:** Expert: one fewer warning on some networks (documented in the plan note).
- **Owner decision:** D-6.

#### B6. Obstacle 12: the New-project dialog shows a save path local mode does not use
- **Evidence.** `FE/layout/NewProjectWizard.tsx:209-211` hard-codes "Saved to `pypsa-gui/backend/projects/<name>/`". The real root is `PYPSAGUI_PROJECTS_ROOT` or `~/Documents/…/Projects` (`BE/app_paths.py:67-70`) and the flat store in app-data; nothing exposes it to the client (`BE/routers/local_settings.py:106` `GET` returns settings only).
- **Root cause.** A dev-era literal.
- **Options.**
  1. `GET /api/local-settings` gains `projects_root` (local mode only); the wizard shows the real path in local mode and no path in hosted mode.
  2. Drop the line. Loses the one thing a local user wants to know.
- **Recommendation:** 1.
- **Files.** `local_settings.py`, `FE/api/localSettings.ts`, `NewProjectWizard.tsx`. **Tests.** BE `test_local_settings_projects_root`; FE `NewProjectWizard.templates.test.tsx` "shows the backend's projects root / hides it when unknown". **Size:** S. **Risk:** none.
- **Owner decision:** D-7.

#### B7. The tour drops optional steps for good (tagging tour always 1/1)
- **Evidence.** `GuidedTour.tsx:104-106,125-127` `visibleSteps` filters optional steps once when the tour mounts; a Link step never appears if its target shows up later. The catalogue wording was corrected (P22.9-FE re-gate) but the behaviour stays.
- **Root cause.** Static filtering at mount.
- **Options.**
  1. Keep all steps; skip an optional step whose target is absent **at the moment it would show** (advance past it), and show "N of M" from the steps that are currently visible. Small change in `next()`.
  2. Re-evaluate `visibleSteps` on every DOM mutation. Overkill.
- **Recommendation:** 1.
- **Files.** `GuidedTour.tsx`. **Tests.** `GuidedTour.test.tsx` "an optional step whose target appears later is shown"; the P24-FE pre-study tour test still walks 6 steps. **Size:** S. **Risk:** none.

#### B8. Hub load-error line names the wrong failure; Run study proceeds with the default pack; a quiet failure leaves no log line
- **Evidence.** P24-FE re-gate notes 2 and 6. `FE/api/client.ts:202-210` `skipErrorToast` also skips `appLog`. The hub error line says "study state could not be read" for a template read failure; the Goal card then runs with `strong_grid`.
- **Recommendation.** Name what failed (`study` / `template`), disable Run study while the template read is in error (a one-line reason), and log quiet failures at INFO in `client.ts`.
- **Files.** `FE/pages/hubDesign/HubDesignPanel.tsx`, `cards/GoalCard.tsx`, `client.ts`. **Tests.** `HubDesignPanel.flow.test.tsx` "template 500 → line names the template, Run disabled"; `client.quietToast.test.ts` "quiet failure still logs INFO". **Size:** S. **Risk:** none.

#### B9. Mode switch accessibility
- **Evidence.** `FE/layout/AppHeader.tsx:1051` `aria-label="Interface mode"`; the explanation is in `title` only (P23 note 3).
- **Recommendation.** `aria-describedby` to a visually hidden sentence per button; keep the 10 px label.
- **Files/Tests.** `AppHeader.tsx`, `AppHeader.uiMode.test.tsx` "each mode button has an accessible description". **Size:** S. **Risk:** none.

#### B10. `propertiesEditRequest` is an unscoped global flag
- **Evidence.** `FE/store/uiStore.ts:426,595,776-777` stores `'Bus' | 'Link' | null`, consumed only by a mounted panel; pinned in `PropertiesPanel.editRequest.test.tsx` (P22.9-FE note 4).
- **Recommendation.** Store `{type, name}` and clear it on selection change. **Files/Tests.** `uiStore.ts`, `PropertiesPanel.editRequest.test.tsx` "a request for bus A is not consumed by bus B". **Size:** S. **Risk:** low (tagging tour prepare path; the P22.9 smoke covers it).

### C. Cosmetic

| # | Item | Evidence | Fix | Tests | Size |
|---|---|---|---|---|---|
| C1 | A reloaded Improve request loses "(step i of N)" | `ChatPanel.tsx:1363-1368` `userMessageLabel` rebuilds the label from the §5.7 sentence, which has no step index; the live label comes from `QueuedRequest.display` (not persisted) | Backend-free: the delegate text already carries the tool and args; add the index to the sent sentence? No — §5.7 text is pinned by the stub regex. Instead persist `display` as a render hint in the transcript's client-side metadata (`chat.jsonl` `ui` field is already allow-listed per turn) **or** accept the loss. Recommend: accept; write it in the plan as a known cosmetic | — | S |
| C2 | The "created from template" toast covers Send | `FE/main.tsx:47-49` `Toaster position="bottom-right"`; the dock's Send sits bottom-right | Toast container offset above the dock when the assistant dock is open (`containerStyle` bound to `assistantDockOpen`), or `position="top-right"` in Guided | `main`-level test is awkward; assert via smoke P26 screenshot: toast box ∩ `chat-send` box = ∅ | S |
| C3 | `fmtEnergy` renders `0.00 kWh` / `500.00 kWh` under 1 MWh in a column whose header lost its unit | `FE/pages/results/shared.ts:743-749` | Below 1 MWh keep MWh with 3 decimals; zero → "0 MWh"; restore the unit in the header | `shared.test.ts` cases | S |
| C4 | Vacuous smoke check "no further failing requests after recovery" | `smoke-guided.mjs:1162-1164` counts the removed route's fulfils | Count via `page.on('request')` filtered on `/api/results/eh_study` and status ≥ 500 | smoke self-test (the check must be able to fail: inject one 500 and expect FAIL in a `--self-test` run) | S |
| C5 | `GET /eh_review` docstring: 204 wording (a failed study has a record and still returns 204) | `BE/routers/results.py:1523-1526` | Correct the docstring (P24-BE N2) | none (docs) | S |
| C6 | Guided greeting still opens with the engine words "Not solved yet." before any study | `ChatLaunchGreeting.tsx:86-90` `default` branch (in-progress fixes changed the chips only) | Guided: "No study has run yet — start in Hub design."; Expert unchanged | `ChatLaunchGreeting.solvedState.test.tsx` Guided/Expert pair | S |
| C10 | Multi-tab: no `storage` listener (P23 note 1, partly fixed by the re-read) | `uiStore.ts:647-660` re-reads on `noteNewProjectCreated` only | `window.addEventListener('storage')` → `setUiMode(stored, {explicit:true})` | `uiStore.uiMode.test.ts` "storage event adopts the other tab's explicit choice" | S |

### D. Non-goals (spec §1) — optional phases pending an owner decision

#### D1. Chat-created projects do not switch the mode
- **Evidence.** `BE/services/chat_tools.py:2374-2379` `create_project_from_template` calls the same route as the wizard; `PROJECT_REBINDING_TOOLS` (`chat_service.py:125-131`) does **not** include it, so no `project_rebound` frame is emitted for it (it is for `save_project_as`). FE `ChatPanel.tsx:2322-2345` handles `project_rebound` without `noteNewProjectCreated`. §10: "the assistant is already the Guided surface".
- **Options.** (1) Keep the non-goal. (2) Add `create_project_from_template` to `PROJECT_REBINDING_TOOLS` (verified: `routers/projects.py:1332-1346` binds the new project atomically with the network swap and persists the active pointer, "mirroring activate_project" — so today the chat path rebinds the backend **without** a `project_rebound` frame, which is also the autosave `expect` hazard the frame exists for) and have the FE `project_rebound` handler call `noteNewProjectCreated('template')` when `via_tool` is a creating tool (`create_project_from_template`, `save_project_as`). G4 literal.
- **Recommendation:** 2, size S, because an Expert user who asks the assistant for a template is exactly the first-time path, and the G4 rule was chosen "literally". Tests: BE `test_chat_project_rebound_on_template` (red: no frame), FE `ChatPanel.sendRequest.test.tsx` "project_rebound via a creating tool → Guided unless explicit". Risk: Expert with an explicit choice is unchanged (`uiModeExplicit`).
- **Owner decision:** D-8.

#### D2. Non-EH workflows are Expert-only
- **Evidence.** Spec §3.5 hiding rules; everything else reachable via palette / `ui_open_panel`.
- **Options.** (1) Keep, gather feedback from real Guided users first. (2) A second Guided flow (candidates: "Run a dispatch and read the cost", or GridSpine hand-off) — needs its own spec (§ cards, catalogue, stub branches, smoke).
- **Recommendation:** 1 for now; if 2, write a spec first (L).
- **Owner decision:** D-9.

#### D3. Mode stored per browser, not per user
- **Evidence.** `uiStore.ts` localStorage keys (spec §3.1).
- **Options.** (1) Keep. (2) Server-side preference under the org/user settings (hosted mode only), with the browser value as the fallback.
- **Recommendation:** 1 for local mode; 2 only when hosted multi-user is in scope (M, backend settings + FE sync + migration of the implicit default).
- **Owner decision:** D-10.

## 3. Phases

Every phase runs the spec §8 gate unchanged: row 1 full backend suite (`-m "not slow"`), row 2 targeted EH set (`test_energy_hub_templates_e2e.py test_energy_hub_review.py test_guides.py test_energy_hub_study_isolation.py test_live_network_untouched.py test_chat_tools_endpoint_map.py test_chat_tools_dispatch.py`), row 3 `tsc`, row 4 full vitest, stress ×10 of the touched suites where the phase changes polling / store / chat, row 5 the browser smoke phase named below, row 6 an independent QA-gate review with verdict GO before the next phase starts, row 7 the Expert-unchanged check (`uiMode` diff review + `*.expertUnchanged` snapshots). Each phase adds its steps to `smoke-guided.mjs` under a new `--phase` that **extends** the named base phase (the base steps keep running).

| Phase | Items | Why this order | Smoke extends | Size |
|---|---|---|---|---|
| **P27 — Data integrity** | A1 (restore under the lock, edits refused during a live study), A2 (bound-project resync after a restart), A8 (`project_rebound` for chat-created template projects), A5 (FMEA cache: extra tick, per-project reset), A6 (mid-study switch smoke) | Only phase that changes backend write behaviour; do it before anything builds on the study loop | `P27` = P22.9 (restart step, edit-during-sweep step) + P25 (chat template + autosave, no 409) + P26 (mid-study switch, settled FMEA counts) | M |
| **P28 — Honest state** | A3 (per-profile readiness, bound profile), A4 (stale review in the greeting), C6 (greeting first line), C10 (storage listener) | The owner's honesty rule; touches the send gate, so it needs its own gate | `P28` = P26 + P22.9 send-gate step (bound-profile case: PUT a second ready profile, bind by one turn, reload, assert Send enabled) | M |
| **P29 — Guided chat and risk table** | B1 (plain progress lines), B2 (Guided FMEA tab), B3 (bug 5 zero reasons) | The two remaining Expert surfaces a Guided user is sent to | `P29` = P26 (FMEA screenshot asserts Guided title, no engine badges, `genset_1` reason text; chat has no raw `→` lines) | M |
| **P30 — Tours, templates, wizard** | B4 (popover placement), B7 (late optional steps), B5 (`gen_zero_costs`), B6 (real save path), B8 (hub load errors), B9 (switch a11y), B10 (scoped edit request) | First-time friction outside the main loop; all S | `P30` = P24 (tour boxes never overlap targets / viewport; validation of each template returns no `gen_zero_costs`) + P22.9 tagging tour (Link step appears when a Link's Edit opens) | M (7 × S) |
| **P31 — Cosmetics** | C1 (record as accepted or persist `display`), C2 (toast offset), C3 (`fmtEnergy`), C4 (real "no further requests" check), C5 (docstring) | Lowest value, zero risk | `P31` = P26 (toast ∩ Send = ∅) + the smoke `--self-test` | S |
| **P32 (optional)** | D1 chat-created projects switch mode | Needs D-8 | `P32` = P25 (stub branch: `create_project_from_template` from chat in a fresh Expert-implicit context → Guided) | S |
| **P33 (optional)** | D2 a second Guided workflow | Needs D-9 and a spec | new phase | L |
| **P34 (optional)** | D3 server-side mode preference | Needs D-10 | `P34` = P23 (mode persistence across two contexts with the same user) | M |

Test-first in every phase: each item's "red" test is written and seen failing before the fix; changed assertions carry a one-line justification here, as in the P26 section of the parent plan.

## 4. Owner decisions

| # | Question | Options | Recommendation |
|---|---|---|---|
| D-1 | A1: edits during a live-network study | (a) refuse with the existing `study_in_flight` 409 and restore under the lock; (b) merge: restore only the solver's cells, keep user edits | **(a)** — simple, honest, consistent with the save refusal; (b) only if users complain |
| D-2 | A2: on a bound-project mismatch after a restart | (a) banner + suspended autosave + one-click reload; (b) silent auto-reload of the tab's project | **(a)** — (b) re-creates the cross-tab hazard the guard exists for |
| D-3 | A3: transport for the bound profile | (a) `bound_profile_id` on `/history` only; (b) per-profile `chat_ready` on `/health` plus (a) | **(b)** — also closes the key-offer caveat (P26 note 5) |
| D-4 | B2: Guided FMEA tab | (a) Guided variant of the same tab (plain labels, hovers, Expert snapshot-identical); (b) a separate Guided risk table | **(a)** |
| D-5 | B3: zero-severity explanation | (a) backend additive `zero_reason` + FE text; (b) FE inference only | **(a)**; do not add it to the CSV/JSON exports unless asked (exports stay byte-stable) |
| D-6 | B5: `gen_zero_costs` | (a) validator exempts time-varying marginal cost and fixed generators; (b) give the templates nominal costs | **(a)** |
| D-7 | B6: save path | (a) show the real root from `GET /api/local-settings` in local mode, nothing in hosted; (b) drop the line | **(a)** |
| D-8 | D1: chat-created projects switch to Guided (G4 literal) | (a) yes, via `project_rebound` + `noteNewProjectCreated`; (b) keep the non-goal | **(a)** as optional P32 (S) — an Expert user's explicit choice is still respected |
| D-9 | D2: a second Guided workflow | (a) not now, collect feedback; (b) spec one (dispatch-and-cost or GridSpine) | **(a)** |
| D-10 | D3: server-side mode preference | (a) keep per-browser; (b) add a hosted-mode user preference | **(a)** for local mode; revisit with multi-user hosting |
| D-11 | C1: "(step i of N)" after reload | (a) accept and record as a known cosmetic; (b) persist a client `display` hint per turn | **(a)** — the §5.7 sentence is pinned by the stub regex and the history stays byte-stable |

## 5. Risks specific to this plan

| Risk | Mitigation |
|---|---|
| P27's edit refusal surprises an Expert user mid-sweep | plain 409 sentence ("A study is still running — wait for it to finish or abort it before editing."), `QUIET_TOAST_CODES` keeps it to one toast; documented in the P27 note |
| P28 loosens the send gate on a wrong readiness answer | readiness comes from the backend per profile; the FE only follows; `chat_ready` fail-open stays for unknown |
| P29 changes the FMEA tab markup Expert users rely on | `FmeaTab.expertUnchanged` and `Results.expertUnchanged` snapshots taken **before** the change on the base commit, compared byte-for-byte as in the P23 gate |
| B3 changes three engines | additive field only; the invariant tests (`test_live_network_untouched.py`, sweep / stress golden tests) stay green; no number changes |
| Smoke growth (each phase extends a base) | phases stay independent (`--phase P27` runs base + extension); total run time is recorded in each gate file |
