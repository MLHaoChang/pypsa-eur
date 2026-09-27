# Guided mode — QA gate P23 (Guided / Expert mode)

**Date:** 2026-09-27
**Reviewer:** independent QA-gate agent (did not write the code)
**Branch / range:** `claude/epic-allen-k2t1c4`, `git diff 0302a5c..HEAD` (7db38a9, 3fb0e6f, b8ac655 — "WIP P23")
**Contract:** spec §3, §8, §9, §10; plan P23 section; decisions G1–G4 (G4 read literally)
**Scratch evidence:** `/tmp/claude-0/-home-user-pypsa-eur/93e65137-63f8-5e19-b114-b55788c10af8/scratchpad/qa23/`

## Verdict: **NO-GO**

Rows 2–5 are green, and every new test killed its mutants. The Expert snapshots match what the base commit renders. But the review found three system-level integration bugs. Each has a failing repro test. Spec §8.3 says "zero system-level bugs; a bug found during the gate blocks the gate; it is fixed or explicitly deferred by the product owner in the plan". Every blocker is small to fix. B2 and B3 are gaps in the spec itself (the implementation follows §3.6 and §3.5/§3.7 literally), so they need a spec decision (Fable or the owner) before the fix.

---

## Blockers

### B1 — G4 is not literal: two creation paths never call `noteNewProjectCreated`

G4 (literal) says **every** new project starts in Guided unless the user made an explicit choice. The spec §3.4 table lists five call sites. The app has more creation paths than that:

| Path | Where | Effect |
|---|---|---|
| Project-tab strip **"+" → New project** (quick-create a blank project) | `pypsa-gui/frontend/src/layout/ProjectTabs.tsx:210-247` (`createProject`) | creates, seeds, `addTab`, `setCurrentProject`; mode untouched |
| Sidebar **Open project → from file** (`.pypsaproj.zip` imported "as a fresh project", the same `importBundle` call as the wizard's From-file tab) | `pypsa-gui/frontend/src/layout/Sidebar.tsx:1035-1060` (`handleOpenFromFile`) | new project registered and opened; mode untouched |
| (secondary) `ImportZone` bundle import with no current project | `pypsa-gui/frontend/src/pages/ImportExport.tsx:98-130` | new project opened; mode untouched |

**Repro (failing test):** `qa23/repro/ProjectTabs.qaG4Repro.test.tsx` is `ProjectTabs.test.tsx` plus one case. It sets `uiMode:'expert', uiModeExplicit:false`, then "+" → New project tab → type `delta⏎`, and waits for `currentProject === 'delta'`. The case fails with `expected 'expert' to be 'guided'`.
Manual: seed `network-diagram:current-project` (existing user, implicit Expert), open `/app`, click "+" on the tab strip → New project → Enter. The new project opens in Expert.

**Fix:** call `useUIStore.getState().noteNewProjectCreated('blank')` in `ProjectTabs.createProject` before `addTab`, and `noteNewProjectCreated('file')` in `Sidebar.handleOpenFromFile`. The `ImportZone` no-current-project branch should get `'file'` too. Add one handler test each, like `Sidebar.newProject.test.tsx`. Record the extra call sites in spec §3.4. If the owner decides "Open from file" counts as opening an existing project, record that in the plan instead.

### B2 — Guided auto-open hijacks the EH tagging tour (P22.9 Obstacle 3 comes back in Guided)

`App.tsx:188-197` opens `hubDesign` the first time the project has **no panel** in Guided. The spec §3.6 rule does not check why the panel became null. The P22.9 tour prepare step (`pages/results/prepareTaggingTour.ts:41`) calls `setSlidePanel(null)` on purpose, so that the canvas and the Properties Bus card (the tour's target `eh-bus-fields`) can render. If the auto-open has not fired yet for this project, the effect sees `activeSlidePanel == null` and opens the full-screen `hubDesign`. `App.tsx:661` renders `PropertiesPanel` only when `!activeSlidePanel`, so the tour's target never mounts, and the tour dead-ends on "target missing".

**When the auto-open has not fired yet:** whenever the project got a panel before its first null moment in Guided. The ordinary case: an Expert user has Results open and clicks **Guided**. §3.7 keeps `results` open, so the auto-open does not fire. The user then goes to Adequacy → Energy Hub reference design → "How to tag the network". The same thing happens whenever a panel is opened before the effect runs, for example by the assistant's `ui_open_panel` or by `requestAssetDetail`. P24's Site card will reuse this tour.

**Repro (failing test):** `qa23/repro/App.qaTourRepro.test.tsx` is `App.hubDesignAutoOpen.test.tsx` plus one case. It sets `uiMode:'expert', activeSlidePanel:'results'`, renders App, calls `setUiMode('guided',{explicit:true})` (results stays), then awaits the real `prepareTaggingTour(qc,'Demo')`. It fails with `expected 'hubDesign' to be null`, and `properties-stub` is absent.

**Fix (needs a spec decision for §3.6):** the auto-open should fire only when the user *enters* a project, or switches to Guided, with nothing open. For example, mark `hubDesignAutoOpenedFor.current = currentProject` whenever Guided has a project and a non-null panel, and not only when the effect opens `hubDesign` itself. Alternatively, skip the auto-open while a tour prepare is pending. Add the repro above as a regression test.

### B3 — In Guided, visible entry points silently land on the wrong Results tab

Guided keeps the canvas, BottomPanel, PropertiesPanel and the assistant unchanged (§3.5). Several of their controls target Results tabs that Guided coerces to `adequacy` (§3.7 `effectiveTab`):

- **Asset detail.** These all call `requestAssetDetail`, which sets `activeSlidePanel:'results'` and `resultsTabRequest:'asset'`, and the user sees **Adequacy** with no asset:
  - Properties "View results"/Asset-detail button: `layout/PropertiesPanel.tsx:2625`;
  - BottomPanel row action: `layout/BottomPanel.tsx:1245`;
  - canvas context menus: `pages/TopologyCanvas.tsx:3172`, `pages/MapCanvas.tsx:1260`;
  - the assistant's `ui_open_asset_detail`: `components/ChatPanel.tsx:179`.
- **Assistant `ui_open_panel(results, results_tab=economics|dispatch|…)`** (`ChatPanel.tsx:233`). The assistant says it opened Economics, and the user sees Adequacy. The Guided greeting still offers the chip **"Open Economics"** (`ChatPanel.tsx:1097`; it is visible in smoke shot `s23/03`). That chip is a dead end in Guided.
- These contradict the product copy the phase itself ships. The switch title says "advanced panels are hidden but reachable through the assistant", and the toast says "ask the assistant for any of them" (`utils/uiMode.ts`). For Results tabs that is false in Guided.

**Repro (failing tests):** `qa23/repro/Results.qaHiddenTabRepro.test.tsx`, run with the `Results.expertUnchanged` mocks.
- (a) `uiMode:'guided'` + `requestAssetDetail({Generator,g1})`: `results:active-tab` is `asset`, but `asset-stub` is absent.
- (b) `uiMode:'guided'` + `requestResultsTab('economics')`: `economics-stub` is absent.

Both fail.

**Fix (needs a spec decision):** pick one of three.
- (i) An explicit request (`resultsTabRequest`, asset detail) shows the requested tab even in Guided. The coercion then applies only to a *stored* tab, which matches §3.7's "Panels opened later by the assistant render in Guided regardless of the hidden list".
- (ii) Hide or disable those entry points in Guided, and drop the Guided greeting chips that target hidden tabs.
- (iii) Change the copy and have the assistant told (P25 `ui_mode`).

Option (i) is the smallest and keeps the copy true.

---

## Non-blocking notes

1. **Multi-tab can override an explicit choice.** There is no `storage` listener, and `noteNewProjectCreated` trusts the in-memory `uiModeExplicit` (`store/uiStore.ts:634-642`).
   - Tab A clicks Expert, which writes `ui-mode=expert` and `explicit=1`.
   - Tab B, loaded earlier with an implicit mode, creates a project. It writes `ui-mode=guided` and leaves `explicit=1` in place.
   - On the next load the user is in **explicit Guided**, a choice they never made.

   One-line fix: re-read `network-diagram:ui-mode-explicit` from storage inside `noteNewProjectCreated`. A `storage` listener would sync the tabs completely. This does not happen in a single tab.
2. **Guided copy.**
   - The Results header still reads "Capacity expansion, dispatch, load flow, prices, and emissions from the last solve" when only Adequacy and FMEA are shown (shot `s23/03`).
   - The Guided greeting chips offer "Compare two scenarios" and "Open Economics" (see B3). These are candidates for P24 or P25.
3. **Accessibility of the switch.**
   - Good: `role="group"` + `aria-label="Interface mode"`, real `<button type="button">`s with visible text and `aria-pressed`, and the browser focus outline is kept (nothing in `index.css` suppresses it for buttons).
   - Weak: the explanation is in `title` only, so keyboard and screen-reader users never get it (consider `aria-describedby`), and the label is 10px text.
4. **Results expertUnchanged scope.** It snapshots only the tab strip; the body is checked by a single `prices-stub` assertion. This is acceptable, because the body selection is also killed by mutation M5b.
5. **`SAFETY_PANEL_ENUM` vs the frontend aliases.** The backend adds `HubDesign`/`hubDesign`. The frontend also accepts `hub_design`, which the backend enum does not list. The frontend is the wider side, so this is harmless. `ui_open_panel` in `chat_tools.py:2773` passes the id through unchanged. The new backend test uses a hand-rolled enum validator and carries its own non-vacuity case.
6. **Global test seed (`vitest.setup.ts:141-155`).** This is an accepted deviation, and it does not mask the mode tests:
   - `uiStore.uiMode.test.ts` and `uiStore.firstRunOrder.test.ts` call `localStorage.clear()` before each fresh `import()`;
   - vitest isolation is on (default `isolate`), so `resetModules` cannot leak a Guided store into other files.

   What it does mean: no component or App test ever runs a real first-run. That path is covered only by the store tests and the P23 smoke (fresh context), and both pass.
7. **Opening a project never changes the mode.** Checked: only the six creation handlers call `noteNewProjectCreated`, and `switchToProject`, Recent, Projects-home open and the "+" → Open existing path do not. A first-time (implicit Guided) user who opens an existing project stays Guided (smoke steps 04–05, 11–13).
8. **The command palette in Guided is unchanged apart from `act-ui-mode`.** Hidden panels are reachable through it (smoke step 07, shot `s23/04`). `ui_open_panel` of a hidden panel works in Guided (`ChatPanel.hubDesignPanel.test.tsx`). API-key settings stay reachable from the assistant's own link.
9. **What the Guided v1 flow needs is still visible:** Results Adequacy and FMEA, the Assistant dock and nav row, the header Run button, the Properties panel and canvas (for tagging) and the `hubDesign` slot. P24 must fix B2 before its Site card reuses the tagging tour.
10. **Row 7 (Expert unchanged).**
    - The non-test diff has 32 `uiMode` hits. Every `guided` branch has an Expert arm that renders the pre-phase markup. The only Expert-visible changes are the allowed ones: new `data-testid`s, the header switch, and the palette entry.
    - The three `*.expertUnchanged` snapshots are **byte-identical** to what the base commit `0302a5c` renders. I checked this by rendering the same three test files on a scratch `git worktree` of `0302a5c` (fresh `--update`, then `cmp`): all three `.snap` files are IDENTICAL. The base snapshots are kept in `qa23/base-snaps/`. The snapshots were committed in 7db38a9 together with the source, but their content equals the base render, so they are genuine.

---

## Evidence

### Row 2 — targeted EH / chat-tools set (`pypsa-gui/backend`, the brief's command incl. `test_chat_tools_schema_panels.py`)
```
405 passed in 371.19s (0:06:11)      EXIT=0        (qa23/row2.txt)
```

### Row 3 — `npx tsc --noEmit -p .`
```
EXIT=0, no output                                    (qa23/tsc.txt)
```

### Row 4 — `npx vitest run`
```
Test Files  209 passed (209)
     Tests  2189 passed (2189)                       EXIT=0  (qa23/vitest.txt)
```

### Row 5 — browser smokes
- `--phase P23 --out qa23/s23`: **PASS**, exit 0, 11 screenshots, 14 steps. Log: `qa23/smoke-p23.log`.
  - Screenshots checked by eye:
    - 01: `/projects` on a first-time user.
    - 02: Guided workbench with `hub-design-panel` full-screen; sidebar shows Assistant, Hub design and PROJECT (card, Save, Projects home); Guided pressed.
    - 03: Results shows exactly the Adequacy and FMEA tabs.
    - 04: Solver settings opened through the palette in Guided.
    - 05: Expert brings back every Project row, the MODE switcher and the full tab strip; the stored tab `dispatch` is restored.
    - 06: after a reload, still Expert.
    - 07: explicit Expert, a new template project stays Expert.
    - 08: an existing user starts in Expert.
    - 09: implicit Expert, a new blank project flips to Guided with hubDesign open.
    - 10: the same for the template project.
    - 11: after pruning, a closed hubDesign stays closed.
  - Every shot matches its step.
- `--phase P22.9 --out qa23/s229`: **PASS**, exit 0, 10 screenshots. The script seeds explicit Expert (`smoke-guided.mjs:214-219`). Log: `qa23/smoke-p229.log`.
  - Shot 03: the Expert workbench opens.
  - Shot 10: the tagging tour's coach mark sits on `eh-bus-fields` in the Bus card's Edit mode.
- Processes: both runs logged "stopped stub / vite / uvicorn". Afterwards `ps` shows no uvicorn, vite, stub or chromium process left.

### Vacuity — mutation testing (scratch `git worktree` of HEAD; real files never edited)
Harness `qa23/mutate.py`, results `qa23/mutations.txt`. **19 of 19 mutants killed:**

| Mutant | Killed by |
|---|---|
| M1a FIRST_RUN computed inside the `create()` initializer (after `storedTheme`) | `firstRunOrder` (probe order) |
| M1b theme-schema no longer excluded + late detection | `firstRunOrder`, `uiMode` (12 fails) |
| M1c first-time branch does not persist | `uiMode`, `firstRunOrder` |
| M2a `noteNewProjectCreated` ignores the explicit flag | `uiMode` (5 kinds), Sidebar/ProjectsHome/Wizard handler tests |
| M2b implicit set clears the explicit flag | `uiMode` |
| M3a–c Sidebar Guided hiding (all / ModeSwitcher / icon-strip Data+Sim) | `Sidebar.uiMode` |
| M3d Results `expertOnly` filter removed; M3e `effectiveTab` not coerced | `Results.uiMode` |
| M4a auto-open not once-per-project; M4b auto-open in Expert | `App.hubDesignAutoOpen` |
| M5a Hub design row in Expert; M5d one class changed in `SectionHdr` | `Sidebar.expertUnchanged` |
| M5b Expert Results active tab changed | `Results.expertUnchanged` |
| M5c header switch wrapped in an extra element | `AppHeader.expertUnchanged` |
| M6 Sidebar `newProjectMut` drops the note call | `Sidebar.newProject` |
| M7 palette title not next-state | `CommandPalette.uiMode` |
| M8 header click made implicit | `AppHeader.uiMode` |

Reading each new test: none is vacuous.
- The store tests re-import fresh per case and assert storage as well as state.
- The handler tests assert both the call order (note before navigate or set) and the real mode outcome.
- The backend panel test includes a non-vacuity case for its validator.

### Blocker repro tests (scratch only, all fail on HEAD)
`qa23/repro/ProjectTabs.qaG4Repro.test.tsx` (B1), `qa23/repro/App.qaTourRepro.test.tsx` (B2), `qa23/repro/Results.qaHiddenTabRepro.test.tsx` (B3, two cases). To run them, drop each into the matching `src/` directory.

### Row 1
The orchestrator runs the full backend suite. It is not re-run here.
