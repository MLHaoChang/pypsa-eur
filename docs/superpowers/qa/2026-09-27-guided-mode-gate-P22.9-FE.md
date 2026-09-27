# Guided mode — independent QA gate, P22.9-FE

- **Reviewer:** independent QA gate; I did not write this code.
- **Date:** 2026-09-27.
- **Branch:** `claude/epic-allen-k2t1c4`.
- **Diff:** `1336d59..9bd0590`. That is the two "WIP P22.9-FE" commits: 41 files, +2161/−55.
- **Contract:** spec §2.2, §2.4–§2.11, §8 and §10, and the P22.9-FE section of the plan.
- **What I changed:** no source or test file was edited. Mutations and repro tests ran in a scratch copy of `src/`: `scratchpad/qa229fe/mut/`, with `node_modules` symlinked.
- **Row 1:** the full backend suite was running in the background and is the orchestrator's to report.

## Verdict: **NO-GO**, one blocker

Everything the spec lists for P22.9-FE is implemented. Rows 2–5 are green, the key tests are not vacuous (15 mutations, all caught), and the browser smoke passes end to end.

There is one blocker. The new send gate falsely locks the assistant when the chat session uses a model profile other than the instance-active one. That is an Expert regression, and it is exactly the "must not block the stub/OpenAI profile" case. The fix is one line.

---

## Blockers

### B1 — Send gate locks the dock when the session's picked profile differs from the instance-active profile

- **Where:** `pypsa-gui/frontend/src/components/ChatPanel.tsx:1207–1213`:
  ```ts
  const { data: chatHealth } = useQuery<ChatHealth>({ queryKey: ['chat','health'], ... })
  const notReady = chatHealth?.chat_ready === false
  ```
  `notReady` is then used at `:2166` (`onSend`), `:2837` (the inline key form) and `:2971` (the button's `disabled` and `title`).
- **Mechanism.** `GET /api/chat/health`'s `chat_ready` is computed from `llm_config.resolve_active()`, the instance-wide active profile (`routers/chat.py:158–176`). ChatPanel, however, sends with the session's own pick:
  - `chatStore.profileId` is set from the model dropdown (`ChatPanel.tsx:2416–2518`).
  - It is sent as `profile_id` on every stream request (`:2138`).
  - Picking a profile there does **not** change the server's active profile.

  The ErrorBanner already avoids this trap: see the C-12 comment at `ChatPanel.tsx:729–733`, "a member's session may legitimately differ from" the `/chat/health` active profile. The gate reintroduces it.
- **Effect.** Take a desktop user with no Anthropic key and a working local or OpenAI profile (`auth: none`, or a bearer profile whose key is set) that they picked in the dock dropdown rather than activating globally.
  - Before P22.9-FE, their messages went through.
  - Now Send and Enter are disabled.
  - The inline form says "needs an API key for the active model" and offers the Anthropic key.
  - A server-mode member cannot change the active profile at all, so their only way out is an admin.
- **Repro.** Scratch-only test `scratchpad/qa229fe/mut/src/components/QA.sessionProfileGate.test.tsx`:
  - Health: `active_profile=anthropic-sonnet, chat_ready:false`.
  - Profiles: `[anthropic-sonnet, local-llm]`.
  - `chatStore.profileId='local-llm'`; the dropdown shows `local-llm`.
  - Observed: `SEND DISABLED = true GATE SHOWN = true`, so the test fails (`expected true to be false`).
- **Spec note.** Spec §2.8 says "`chat_ready` comes from the active profile" and does not mention the per-session pick. The spec has a gap here, and the implementation followed it literally. It still blocks under §8.3 ("a bug in another surface found during the gate blocks the gate") and under row 7 (Expert unchanged beyond the listed fixes).
- **Suggested fix** (gate only when the session is on the active profile; everything else stays fail-open, as the spec intends):
  ```ts
  const notReady = chatHealth?.chat_ready === false
    && (profileId === null || profileId === chatHealth.active_profile?.id)
  ```
  Also add the repro above to `ChatPanel.sendGate.test.tsx`: a picked non-active profile gives an enabled Send. I did not check whether the backend binds a session to its first profile. If it does, a session whose `profileId` is null but which is bound to another profile would still be gated, so the orchestrator may want to pin that case too.

---

## Non-blocking notes

1. **Double tour and tour across a project switch.** `GuidedTourHost` (`GuidedTour.tsx:258–269`) is a global zustand slot. Nothing closes it on a project switch, so after a switch its step shows the "not on screen" note. The Bus Edit card also renders its own `eh-bus-guide-button` (no `prepare`, so it opens a local `GuidedTour`). Clicking it while the host tour is open stacks two identical overlays. Relaunching from the EH panel is safe, because `seq` remounts a single host tour. Suggestions: close the host tour on `currentProject` change, and have the host-less button no-op while the host tour is open.
2. **Tagging tour is always 1/1.** `visibleSteps` is computed once at tour start (`GuidedTour.tsx:112`). The optional Link step is therefore always dropped when the tour starts from a Bus, and never appears later, even if the user opens a Link's Edit form. The new catalogue text "(step 2 shows when a Link's Edit form is open)" (`eh_fmea_guide.json`, `eh_tagging` step 1 `enter`) only holds if the tour is *started* from a Link. That is what spec §2.7(5) allows, but the wording oversells it. Smoke screenshot 10 shows "1/1".
3. **Tour popover overflows the viewport** (screenshot 10: the "What to enter" text is cut off at the bottom). This is obstacle 10, which is deferred.
4. **`propertiesEditRequest` stickiness.** The request is consumed only by a mounted `BusPanel` or `LinkCard(detail)`, and a 'Bus' request survives a Link card (this is pinned in `PropertiesPanel.editRequest.test.tsx` test 3). I found no reachable path that leaves it set: `prepareTaggingTour` always selects a Bus and opens the right panel before requesting, and throws before requesting when there is no bus. It is still an unscoped global flag. Clearing it on selection change, or storing `{type, name}`, would make it robust. It is not a blocker.
5. **`fmtEnergy` below 1 MWh renders kWh.** A zero ENS or ΔEUE cell now reads `0.00 kWh` (and 0.5 MWh reads `500.00 kWh`) in a column whose unit was dropped from the header. This is cosmetic. Exports are unchanged: every `downloadCSV`/`downloadJSON` call site in `EhReferenceDesignPanel.tsx` (1235, 1361, 1423, 1544, 1600, 1681, 1742) and `FmeaTab.tsx:178` still goes through the unchanged `*CsvRows` helpers on raw values. The FMEA table has no client-side sort on the formatted strings.
6. **The greeting keeps "Add an Anthropic API key to talk to me"** while the stub profile is active and `chat_ready:true` (screenshots 03, 05, 09). This is pre-existing and not in P22.9 scope, but it now contradicts the enabled Send.
7. **`useStudyFinishedInvalidation`** fires only on a `running → !running` edge. The invalidated key (`simulationStatus`) is not an input to any of the six polled study queries, so it cannot loop. It keeps `prev` across a project switch. The worst case is one extra `simulationStatus` refetch for the new project, which is harmless.
8. **Wizard.** The blank path (`onConfirm` → Sidebar and ProjectsHome `createBlank`) is untouched. Template, From-file and Clone now call `addTab`, which is deduplicated (`uiStore.ts:722`), and navigate only off `/app` and only inside a router. That matches ProjectsHome's `createBlank` (`addTab` + `navigate(getPostLoginPath(name))`). The Sidebar (inside `/app`) does not navigate.
9. **Catalogue, bundle, Expert.** `test_guides.py` is green in row 2, so every new `reveal` id is a literal rendered test id. The `testId` prop name on `DetailFooter` was chosen for the literal scan. No requirements, spec or bundle-manifest file is touched. `smoke-guided.mjs` lives in `frontend/scripts` and is not part of the build.
10. **Smoke step 10 screenshot is taken too early.** `smoke-guided.mjs` waits for `fmea-table`, which already exists before the sweep. It should wait for `fmea-sweep` to be re-enabled or for the class-B rows, then shoot. The assertions themselves are API-based, so the step is still valid.

---

## Evidence

### Row 2 — targeted backend set (cwd `pypsa-gui/backend`)

The command was exactly the one in the brief: 7 files.

```
400 passed in 366.20s (0:06:06)   EXIT 0
```

This is consistent with the BE gate: 432 with the two extra files, 560 with the extended set.

### Row 3 — `npx tsc --noEmit -p .`

```
EXIT 0
```

### Row 4 — `npx vitest run`

```
Test Files  195 passed (195)
     Tests  2090 passed (2090)      EXIT 0
```

The baseline was 181 files and 2033 tests. The difference is +14 files and +57 tests, with 0 failures. The only noise is jsdom's "Not implemented: navigation to another Document", which is pre-existing.

### Row 5 — browser smoke (reviewer's own run)

The command was `PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers node scripts/smoke-guided.mjs --phase P22.9 --out scratchpad/qa229fe/smoke`. The result was **PASS**, with 10 screenshots, EXIT 0, and a total path time of about 53 s.

The script logged `stopped stub / vite / uvicorn`. Afterwards no uvicorn, vite, stub or chrome process remained, and ports 8000, 5173 and 11999 were free.

Key log lines:

```
health: active_profile=anthropic-sonnet chat_ready=false → chat-send disabled, hint, chat-send-gate visible
stub profile → health chat_ready=true, chat-send enabled, no inline form
navigated to /app?project=Data Center Energy Hub; wizard closed; no Resume; no /api/projects/unclaimed request
header "Energy Hub reference design"; banner stays while running; eh-readiness-paused
study done (weak_flexible); cue text; eh-report scrolled into view; cue hides
verdict fail → "Next: tighten the energy target or add firm capacity — …"; ok chip text-success
tables equal after study (0 *_nom_opt) and after sweep (8 *_nom_opt only); sweep base_restored=true
status dispatch=fresh condition=null → greeting "The network carries dispatch from a study re-solve, …"
tagging tour: Results closed, Bus 'grid' in Edit, eh-bus-fields highlighted, no missing note
```

I looked at each screenshot to check that it shows what its step claims:

| Screenshot | What it shows | Matches the claim |
|---|---|---|
| 01-send-gate-disabled | /projects; dock "Claude Sonnet"; the gate text with the inline Anthropic key form; Send greyed out with "hello" typed | Yes |
| 02-send-enabled-stub | Dock on "Smoke stub", no gate form | Yes |
| 03-workbench-opened | Workbench with the "Data Center Energy Hub" tab and the toast "Created … from template" | Yes |
| 04-study-running | Banner present with "Use recommended settings" disabled; "Readiness is paused while the study runs"; "Studying…" and Abort | Yes |
| 05-finished-cue | Green "Study finished — view report" pill at the top of the "ENERGY HUB REFERENCE DESIGN" section; greeting "Not solved yet." (the study runs on a copy, which is correct) | Yes |
| 06-report-in-view | Report scrolled up: MC LOLE and verdict line under the filter bar, pipeline chips visible. It is pixel-identical to 07, and the report's own header is hidden under the sticky horizon bar, so the `r.top >= -2` check is lenient | Yes (weakly) |
| 07-verdict-next | "Not certified (fail vs 3 h/yr)", then the "Next: tighten …" line; `ok` chips in green, `skipped` muted; ΔEUE shown as "315.61 GWh" / "58.96 GWh"; levers show "€37.5m" | Yes |
| 08-fmea-after-sweep | **No.** It shows the button still at "Sweeping…" and only the four class-A genset rows. The script shoots as soon as `fmea-table` exists (it already did before the sweep); the API said `done`, but the 2 s UI poll had not caught up. The post-sweep table is visible in 09 | **No** (see note 10) |
| 09-greeting-after-sweep | Dock greeting shows the study-re-solve sentence; FMEA severity/criticality read "€13.0 M", "€7.9 M", "€589.6 k" | Yes |
| 10-tagging-tour-bus-fields | Bus "grid" card in Edit; the Energy Hub block (PoC ticked, SCL 250, IBR 30) ringed by the coach mark; popover "1/1 · 1 · Bus tags" (see notes 2 and 3) | Yes |

One cosmetic point in 09: the genset rows read `€0.0`, which is bug 5 (deferred).

### Vacuity — mutation checks

Each mutation was applied to a scratch copy and the named test file was run. All 15 mutations turned at least one test red. The real files were untouched throughout: `git status` is clean.

| # | Mutation (scratch copy) | Test run | Result |
|---|---|---|---|
| 1 | `notReady = false` (gate removed) | `ChatPanel.sendGate.test.tsx` | **3 failed** |
| 2 | `notReady = chat_ready !== true` (fail-closed) | same | **2 failed** (undefined, probe failure) |
| 3 | `onSend` without the `notReady` check (button-only gate) | same | **1 failed** (the Enter path) |
| 4 | `ApiKeySetup` no longer invalidates `['chat','health']` | same | **1 failed** |
| 5 | `solveLine` fresh → always "Solved" | `ChatLaunchGreeting.solvedState.test.tsx` | **2 failed** |
| 6 | Template success without `openInWorkbench` | `NewProjectWizard.templates.test.tsx` | **2 failed** |
| 7 | Navigation guard `pathname !== '/app'` removed | same | **1 failed** |
| 8 | `prepareTaggingTour` without `requestPropertiesEdit('Bus')` | `EhReferenceDesignPanel.taggingTour.test.tsx` | **1 failed** |
| 9 | EH tagging button without `prepare` | same | **1 failed** |
| 10 | `GuideButton` does not await `prepare` | `GuidedTour.prepare.test.tsx` | **1 failed** |

Based on reading the code rather than mutating it:
- `PropertiesPanel.editRequest.test.tsx` asserts `eh-bus-fields` appears only after the request.
- `studyStatusRefresh.test.tsx` counts `simulationStatus` invalidations per panel.
- `EhReferenceDesignPanel.finishedCue.test.tsx` covers a fresh mount on a done study as a negative case.
- `ProjectsHomePage.unclaimed.test.tsx` asserts the query is not called when auth is disabled.
- The formatting tests compare against the formatter output.

None of these can pass without the fix.

### The 14 accepted deviations and 2 follow-ups

I reviewed them all. The only one with a system-level defect is the send gate's profile scope (B1). The `text-success` token exists (`index.css:71`, `--color-success`). The `EhReferenceDesignPanel.test.tsx` `statusTone` edit is justified in the plan per §8.3.

---

## Re-gate at `28774ad` (diff `32a56d7..28774ad`)

### Verdict: **GO**

B1 is fixed and pinned by tests that fail without the fix. Notes 1–3 and note 10 are addressed. Rows 3–5 and `test_guides.py` are green. I edited no source or test file. Row 1, the full backend suite, is still the orchestrator's to report.

### B1: closed

`ChatPanel.tsx:1221–1222`:

```ts
notReady = chat_ready === false && (profileId == null || profileId === active_profile?.id)
```

I checked this against the backend's per-turn profile rule (`routers/chat.py:1120–1166`, `chat_service._resolve_turn_profile`):

- A named `profile_id` rebinds the session and the turn runs on it. The gate is off unless that profile is the active one, which is correct.
- `profile_id` absent on an unbound session means `resolve_legacy_model(None)`, which returns `resolve_active()`. The turn runs on the active profile, so gating on `chat_ready` is correct.
- Fail-open is kept: when `chatHealth` is undefined, `notReady` is false.

**The limitation.** It is not written in the repo; the commit and the code comment do not name it. I take it to be this case:

- The session was bound to profile X on an earlier turn, and the store's `profileId` is null again (after a reload, or because `session_init` deliberately never pins the pick).
- The backend then keeps X, because a bound session with no named target keeps its binding (C-8/C-9).
- The gate, however, reads the active profile's readiness.

If X is ready and the active profile is not, Send is gated although the turn would succeed.

**I accept it** as non-blocking, for three reasons:
- The dropdown shows `profileId ?? active`, so the UI is at least self-consistent with the gate.
- The escape is one click: pick X in the dropdown, which sets `profileId` and lifts the gate.
- It needs the admin to change the active profile away from a working one mid-session.

The other direction (X keyless, active ready) fails open into the existing `missing_api_key` banner, as before.

**Please record it in the plan's P22.9-FE section** per §8.3; the coordinator's message is currently the only record. A later fix could expose the session's bound profile id to the client, via `session_init` or history, and compare against that.

### Notes 1–3 and 10: addressed

1. **Tour across a project switch and duplicate tours.**
   - `GuidedTourHost` stores the launch project and clears the slot when `currentProject` changes. It also clears the slot on unmount.
   - `GuideButton.start` returns early when the host already shows the same tour.
   - A side effect: re-clicking "How to tag the network" while its own tour is open is now a no-op rather than a restart. This is harmless.
2. **Catalogue wording** now says the Link step appears "only when it is started with a Link's Edit form open", which is accurate.
3. **Popover overflow** is still obstacle 10, which remains deferred. It is unchanged, and screenshot 10 still clips the "What to enter" text.
10. **Screenshot 08 timing.** The smoke now waits until `fmea-sweep` is re-enabled with the text "Run B/C sweep" and the table has class-B rows. The new screenshot 08 shows exactly that: the "Run B/C sweep" button, `site_transformer` and `grid_import` as class B (€13.0 M / €7.9 M, €403.9 k / €589.6 k), then the class-A gensets. **It now matches its step.**

   The dock greeting in 08 still reads "Not solved yet." because the status refetch lands a moment later. Step 09 asserts and shows the study-re-solve sentence.

### Evidence

| Check | Result |
|---|---|
| Repro `QA.sessionProfileGate.test.tsx` (scratch), run with the new `ChatPanel.sendGate.test.tsx` and `GuidedTour.prepare.test.tsx` | 3 files, **15 passed** |
| Mutation: B1 clause removed | **2 failed**: the implementer's repro test and mine |
| Mutation: clause reduced to `profileId == null` | **1 failed** ("explicitly picked the active (not ready) profile → still gated") |
| Mutation: host ignores project staleness | **1 failed** ("closes the host tour when the project switches") |
| Mutation: dedupe guard removed | **1 failed** ("does not open a second copy") |
| `npx tsc --noEmit -p .` | EXIT 0 |
| `npx vitest run` | **195 files, 2094 passed**, 0 failed (+4 over the first gate) |
| `pytest tests/test_guides.py` | **6 passed** |
| Smoke `--phase P22.9 --out scratchpad/qa229fe/smoke2` | **PASS**, 10 screenshots, EXIT 0; the new check "FMEA tab shows the finished sweep (button re-enabled, class-B rows)" is ok; stub, vite and uvicorn stopped |

After the smoke, no uvicorn, vite, stub or chrome process remained. `git status` is clean apart from this file.
