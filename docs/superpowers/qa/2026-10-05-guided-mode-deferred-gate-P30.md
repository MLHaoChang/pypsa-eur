# QA gate: P30 — Tours, templates, wizard (B4, B7, B5, B6, B8, B9, B10, C11, C12)

**Date:** 2026-10-05. **Reviewer:** independent QA gate (did not write the code).
**Scope:** `git diff bcb6a671f..ec0cc082b` on `claude/epic-allen-k2t1c4` (9 commits). The backend has not changed since `5a9052599`, the commit the implementer's row-1 log ran on. `git diff 5a9052599 ec0cc082b` touches only the plan.
**Contract:** deferred spec `2026-09-28-guided-mode-deferred.md`, §0 and §5 (P30), plus the plan's "P30 phase note" (claims, 9 deviations, probe list).
**Scratch:** `/tmp/claude-0/-home-user-pypsa-eur/93e65137-63f8-5e19-b114-b55788c10af8/scratchpad/qa30/` (written `qa30/` below).
- No repo source or test was edited.
- Mutations and probes ran in two scratch worktrees, `qa30/wt` and `qa30/wt2` (HEAD). Both are removed now.
- `wt2` held an instrumented copy of `GuidedTour.tsx` (render, frame and refresh counters) and of the smoke (a churn sample on every tour step, plus a 360×640 walk).

## Verdict: **NO-GO**: one blocker (B6-1), a false "Saved to" path

The engineering is sound, and every gate row is green:
- Row 2: 779 passed.
- Row 3: tsc clean.
- Row 4: 2820 passed.
- Row 4s: 3 of 3 green.
- Row 5: smoke PASS.
- The four Expert snapshots are untouched, and so are the prompt and schema.

Results by item:
- **B4 placement** is correct live. 29 tour boxes were inside the viewport and apart from the highlight.
- **B4 churn:** the MutationObserver costs nothing measurable. Even during a running study, a 3 s sample shows 0–2 refreshes.
- **B7:** the union rule (deviation 2) is correct and needed. The late Link step reads `1/2` and then `2/2` live.
- **B5:** the tests pin it except for one widening, noted below.
- **B8 / B9 / C11 / C12** are correct.

I killed 29 of my 34 own mutants. The survivors are listed under S-1 and N-6.

The one blocker is the same class as the P28 and P29 blockers: a new user-facing sentence that is false.
- The New-project dialog says `Saved to <root>/<name>/`.
- The backend's allocator saves to a *different* folder whenever the name is sanitised or collides case-insensitively.
- In the case-collision case, the path shown is **another project's folder** on the two target platforms, which are case-insensitive.

The spec prescribes this wording, so the fix needs a one-line spec deviation, as P29's did. The verdict is GO once B6-1 is fixed.

## Gate rows

| # | Command (cwd) | Result |
|---|---|---|
| 1 | not rerun. The implementer's `scratchpad/p30/row1.log` ran on `5a9052599`, and `git diff 5a9052599 HEAD -- pypsa-gui/backend` is empty. The log says `6733 passed, 31 skipped, 11 deselected`. `pytest tests/ -m "not slow" --collect-only` on HEAD (`pypsa-gui/backend`) gives `6764/6775 collected (11 deselected)`, and 6733 + 31 = 6764 | accepted (log usable, counts match) |
| 2 | `PYTHONPATH=/home/user/pypsa-eur:/home/user/pypsa-eur/pypsa-gui/backend /tmp/claude-0/venv/bin/python -m pytest <14-file set> tests/test_validation*.py tests/test_local_settings*.py tests/test_energy_hub_templates.py -p no:cacheprovider -W ignore -q -o addopts=""` (`pypsa-gui/backend`, HEAD) | **779 passed, 17 skipped** (`qa30/row2.log`). This includes `test_guided_mode_prompt.py` |
| 3 | `npx tsc --noEmit -p .` (`pypsa-gui/frontend`) | **0 errors** |
| 4 | `npx vitest run` (`pypsa-gui/frontend`) | **244 files / 2820 passed** (`qa30/row4.log`) |
| 4s | `for i in 1 2 3; do npx vitest run src/components/GuidedTour src/pages/hubDesign src/layout/PropertiesPanel src/layout/AppHeader; done` (`pypsa-gui/frontend`) | **3 / 3 green, 219 tests each** (`qa30/row4s-{1,2,3}.log`) |
| 5 | `PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers node scripts/smoke-guided.mjs --phase P30 --out qa30/smoke30 --keep` (`pypsa-gui/frontend`, main checkout) | **PASS, 61 screenshots, exit 0** (`qa30/smoke30.log`). The two probe runs from `wt2` (`qa30/probe.log`, `qa30/probe2.log`) also PASS |
| 7 | `git diff bcb6a671f -- 'pypsa-gui/frontend/src/**' \| grep -c uiMode` → 6 | 3 of the 6 lines are the `diff/---/+++` header of `AppHeader.uiMode.test.tsx`. The other 3 are test lines: an import, a `setState({uiMode:'guided'})`, and one context line. No product line branches on `uiMode`. **Signed.** |
| E | Expert unchanged | `git diff --stat bcb6a671f..HEAD` on the four `*.expertUnchanged*` tests and their `__snapshots__` is empty, and they pass in row 4. There is no change to `chat_tools_schema`, the prompt files or tool descriptions, and `test_guided_mode_prompt.py` is green in row 2. Deviation 5 is confirmed: `AppHeader.expertUnchanged.test.tsx:61` removes `ui-mode-switch` before comparing, and the new spans are inside it |

## Spec contract, row by row (code + live)

| Item | Contract row | Code | Live (smoke screenshots) |
|---|---|---|---|
| B4 | `ref`; re-measure on resize, scroll and ResizeObserver; candidates below, above, right, left with a 12 px gap; fit inside `[8,vw-8]×[8,vh-8]`; apart from the highlight | `GuidedTour.tsx:124-156` (`placePopover`), `:249-266` | 29 / 29 boxes inside and apart. Hub steps place `below`; the tagging steps place `above` (`qa30/smoke30/11-…`, `12-…`, `14…53-p30-tour-*`) |
| B4 | none fits → `free`, the largest side, target scrolled into view | `:146-155`, `:314-319` | 360×640 probe: steps 2 and 7 are `free`, inside the viewport, and overlap a 0-px-wide target (allowed). See N-3 |
| B4 | `maxHeight: calc(100vh-16px)`, `overflowY:auto`, `data-placement` | `:349-350`, `:362` | ✓ |
| B4 (dev. 3) | an absent target docks bottom-left | `:343-347` | not hit in the smoke. Unit-pinned |
| B7 | keep `tour.steps`; Next / Back skip an absent optional step; counter = visible position / visible count | `:110-113`, `:279-291`, `:334-339` | tagging tour: `1/1` → open the Link Edit form → `1/2` with Next → `2/2` on `eh-link-role` (`12-p30-tour-tagging-link.png`). Hub walks of 6 / 7 / 8 / 6 steps are unchanged |
| B5 | exempt the `generators_t.marginal_cost` and `generators_t.p_max_pu` columns; the fixed all-zero unit still warns; message unchanged | `validation_service.py:1698-1717`, `:1770-1775` | preflight on all 3 templates: `ok=true`, 0 warnings, no `gen_zero_costs` |
| B6 | `projects_root` in `_state()`; FE type; line `Saved to <root>/<name>/`; no line on 404 or failure | `local_settings.py:72-76`, `NewProjectWizard.tsx:166-170,215-219` | the line names `<RUN>/projects/new_project/` (`61-p30-new-project-saved-to.png`), and the projects are on disk at `<RUN>/projects/<name>` (`--keep`). **The sentence is false for some names: B6-1** |
| B8 | the line names the first read in error (study state, template, readiness); Run disabled with a title; `skipErrorToast` → `appLog('INFO')` | `HubDesignPanel.tsx:30-33,79-86,119-123`; `GoalCard.tsx:84,146`; `client.ts:242-246` | the P24 B4 500 step shows "study state" and recovers |
| B9 | `aria-describedby`; an `sr-only` span carrying the title; `title` kept | `AppHeader.tsx:1062,1073-1076` | — |
| B10 | `{type,name}`; consumed only by the matching card; cleared by `setSelectedComponent`; prepare passes the bus | `uiStore.ts:713-720`; `PropertiesPanel.tsx:1200,1649`; `prepareTaggingTour.ts:44` | the tagging tour's Bus Edit form opens (P22.9 step) |
| C11 | `dtc_planning`; R15; Q3–Q5 | tests. Q3–Q5 are already pinned by `ehQuiet.test.ts:24-59` (deviation 6, accepted) | — |
| C12 | `N MWh` → `N megawatt-hours`; `<Term k="mwh">` on the Goal line; catalogue key | `plainWords.ts:35-37`, `GoalCard.tsx:136-138`, `Term.tsx:67-69`, `eh_fmea_guide.json:290` | Goal line: "€5,000 per MWh" with the term ⓘ |

## Blocker

### B6-1: "Saved to `<root>/<name>/`" names a folder the project is not saved in

- **Where:** `pypsa-gui/frontend/src/layout/NewProjectWizard.tsx:215-219` renders `Saved to {root}/{trimmed}/`.
- **What the backend actually does:** `POST /api/projects/{name}` → `project_registry.create_root` → `storage_paths.allocate_storage_path` → `safe_names.unique_dir_name`. That allocator changes the name in four cases:
  - it strips trailing dots and spaces;
  - it suffixes Windows reserved stems with `_`;
  - it appends ` (2)` when the folded name (NFC + casefold) collides with an existing row's folder;
  - it does the same for an orphan directory.

  `_PROJECT_NAME_RE` (`routers/projects.py:119`) admits all of these names: dots, spaces, `CON`.
- **Repro (backend, real allocator, through the API):** a scratch test in `qa30/wt` used the `local_client` fixture from `test_local_settings_api.py` and created projects through `POST /api/projects/{name}`, one after another:

  | Typed name | Line says | Saved at |
  |---|---|---|
  | `grid` | `<root>/grid/` | `<root>/grid` ✓ |
  | `Grid` | `<root>/Grid/` | `<root>/Grid (2)` ✗ |
  | `Study.` | `<root>/Study./` | `<root>/Study` ✗ |
  | `CON` | `<root>/CON/` | `<root>/CON_` ✗ |

  On disk: `['CON_', 'Grid (2)', 'Study', 'grid']`.
- **Why it blocks:**
  - It is a new sentence about where the user's data goes, and it is false.
  - In the `Grid` / `grid` case on macOS or Windows, which are case-insensitive and are the desktop targets, `<root>/Grid/` *is* the other project's folder. A user who opens that path in Finder or Explorer finds the wrong project.
  - The implementer flagged exactly this in the probe list ("is `<root>/<name>/` true for every Blank-tab create?"). It is not.
  - The default case (`new_project`, any plain unique name) is true, which is why the smoke passes.
- **Fix (cheap). Say only what is always true, and record a one-line spec deviation for §5.4's wording.**
  - Option (a): `Saved in a folder named after the project, under <root>/`.
  - Option (b): `Saved in <root>/` followed by the name only when it is provably exact. "Provably exact" means:
    - `safe_dir_name(name) === name`, mirrored in the frontend: no trailing dot or space, and no reserved stem;
    - and no case-insensitive match among `existingProjects`.

    Orphan directories stay invisible to the frontend, so (b) can still be wrong. I recommend (a).
  - Add a red test: `NewProjectWizard.templates.test.tsx` with `existingProjects=[{name:'grid'}]` and the typed name `Grid` must not show `…/Grid/`.

## Should-fix

### S-1: three contract rows are not pinned by a test (my mutants survived)
- **Q-B4f:** the `free` → `scrollIntoView({block:'center'})` effect (`GuidedTour.tsx:314-319`) was deleted, and all 15 GuidedTour tests pass. The spec row reads "scroll the target into view".
- **Q-B10c:** `setSelectedComponent` was changed to clear only on a *type* mismatch (`uiStore.ts:718`, dropping the name half). All 6 editRequest tests pass. Moving from Bus A to Bus B keeps the request for A, so it replays when A is selected again.
  - The spec's own red-test title is "a request for bus A is not consumed by bus B **and is cleared when the selection changes**".
  - But the test (`PropertiesPanel.editRequest.test.tsx`, the "bus A / bus B" case) moves the selection to a *Link*, so the name half is untested.
- **Q-B5c:** the exemption was widened to `generators_t.p_min_pu` columns as well, and all 13 B5 tests pass.
  - Spec §5.10 says "the exemption is exactly the two series cases".
  - The fixed-dispatchable guard has no series at all, so a widening to other series is uncaught.
  - Add a fixed all-zero unit that has a `p_min_pu` series, and assert it still warns.

### S-2: a pending edit request survives a project switch and an asset-detail jump
- **Where:**
  - `uiStore.ts:836-870` `setCurrentProject` sets `selectedComponent: null` but leaves `propertiesEditRequest`;
  - `requestAssetDetail` (`:809-814`) moves the selection the same way.
- **Repro (scratch vitest in `qa30/wt`):** `setSelectedComponent({Bus,'grid'})` → `requestPropertiesEdit({Bus,'grid'})` → `setCurrentProject('Other')` → the request is still `{"type":"Bus","name":"grid"}`. It is still there after `requestAssetDetail({Generator,'g'})`.
  - The next `setSelectedComponent({Bus,'grid'})` keeps it, because it matches. Project B's same-named bus therefore opens in Edit.
  - The request is only pending when no card consumed it, so the impact is small.
- **Why:** the store comment at `uiStore.ts:438-439` promises "a request never replays on a later card". The spec scopes the clear to `setSelectedComponent`, so this is out of contract, but it is a two-line fix: clear the request in both setters.

## Notes

- **N-1, the deviations:**

  | # | Deviation | Ruling |
  |---|---|---|
  | 1 | `projects_root` instead of `flat_projects_root` | **Correct, live-confirmed.** `--keep` left `<RUN>/projects/{Data Center Energy Hub, Industrial Hydrogen Hub, Island Microgrid, P30 Data Center}`. `PROJECTS_DIR` (`routers/projects.py:69`) is the auth-disabled flat store; local mode is auth mode with `use_org_segment() == False` (`storage_paths.py:35-58`). My mutants Q-B6a (flat root) and Q-B6b (app-data) are killed |
  | 2 | union rule (at start ∪ when reached) | Correct and necessary. Q-B7a and Q-B7b (each half dropped) are killed. The counter counts an at-start optional step whose target is momentarily absent, which is acceptable because its `reveal` brings it back |
  | 3 | absent target docks bottom-left | Acceptable. The smoke's centred-covers-Edit evidence is real. Q-B4d and Q-B4e are killed |
  | 4 | "readiness check"; Run disabled only on `templateError && !template` | Acceptable. A cached template runs correctly. The line can name "study state" while Run's title names the template; both are true |
  | 5 | no `AppHeader.expertUnchanged` update | Confirmed (row E) |
  | 6 | no new `resultsApi.test.ts` | Accepted. `ehQuiet.test.ts` pins Q3–Q5 |
  | 7 | smoke order P22.9 → rename → P24 | Accepted |
  | 8, 9 | the "Changed assertions" and "Mutants" bookkeeping | Each changed assertion carries a justification. The two equivalents (B4e, C11-R15) are argued correctly: GAP 12 > RING 4 |
- **N-2, MutationObserver churn (probe item):** `qa30/probe.log` holds a 3 s sample on every tour step of the instrumented run. It counts DOM `childList` batches, the tour's `refresh` calls, its frames and its renders.
  - Idle steps: 0–2 batches, 0–2 refreshes, 0–4 renders.
  - **During the running H2 study** (`hub-22…27`): 0–1 refresh and ≤ 4 renders per 3 s.
  - Renders happen without a refresh, so they come from the parent, not from the observer.
  - The per-frame batching and the "state only on change" rule hold. No churn concern.
  - The FMEA-during-sweep page was not sampled: no tour is offered there.
- **N-3, 360×640 (probe item):** `qa30/probe2.log` and `probe2/45…52-qa30-360-*.png` show the hub tour opened at 1440 and then resized.
  - All 8 boxes are inside the viewport.
  - 6 steps are `below`. 2 are `free` and overlap the target, which at that width is a 0-px-wide column: the workbench itself does not lay out at 360 px, and the main panel collapses.
  - The spec allows the overlap. Opening the tour *at* 360 failed because `hub-guide-button` was not clickable, which is a layout issue that predates P30.
- **N-4, B8 INFO logging (probe item):**
  - Hosted 404: `fetchLocalSettings` passes `skipErrorToast`, so the INFO line `GET /local-settings — … [no toast]` is written once per session (`useLocalSettings` has `staleTime: Infinity`). Fine.
  - Side effect: all 37 `skipErrorToast` callers now log INFO. The export path then logs twice (INFO, then its own ERROR). The comment at `pages/OverviewPanel.tsx:185` ("`skipErrorToast` suppresses the interceptor's appLog line") is now stale. Update the comment.
  - The log is capped at 2000 lines (`simulationStore.ts:129`).
- **N-5, B8 readiness wording:**
  - A readiness **422** is one of the engine's typed refusals (`routers/results.py:1507-1509`), for example a network the pack cannot use. It renders as "This project's readiness check could not be read from the server", but the server answered.
  - SiteCard already said "could not be read" before P30, and the spec prescribes this line. A later phase could use the refusal text.
- **N-6, my other two survivors:**
  - Q-B7e: the intro is gated on `idx === 0` instead of `isFirst`. That only matters when the first step is optional and absent; no real tour has one.
  - Q-B5e: the `overnight_cost` leg of the mask was dropped. This behaviour predates P30, and the refactor kept it, but it is unpinned.

  Both are test gaps only.
- **N-7, the "optional step whose reveal control is visible but target not" probe:** with a Link selected but not in Edit, the Link step is not counted (`1/1`, Done). The Bus step's sentence says the step is added "as soon as a Link's Edit form is open", which matches. The rule is true.
- **N-8, the B7 tagging sentence:** "the tour adds that step as soon as a Link's Edit form is open" is true live: `1/1` → `1/2` → `2/2`. The Link step's own body, "(the tour opens a bus first)", is also still true.
- **N-9, the B9 descriptions:** the spans carry `UI_MODE_TITLES` verbatim, and both are true of the product. Expert's "Every panel and tab, as today." has an odd "as today" when read aloud. That wording predates P30.
- **N-10, the readiness observer:** `useHubReadiness(…, {observeOnly:true})` shares the cards' key and sends no request of its own (Q-B8c is killed). As the implementer notes, an Expert-panel readiness error with a different key does not show on the hub line. Accepted.

## Mutation table (mine; scripts `qa30/mut_fe.py`, `qa30/mut_be.py`; logs `qa30/mut_fe.log`, `qa30/mut_be.log`)

| Id | Mutant | Suites | Result |
|---|---|---|---|
| Q-B4a | fit ignores the bottom edge | GuidedTour | killed (5) |
| Q-B4b | fit ignores the right edge | GuidedTour | killed (2) |
| Q-B4c | `above` tried before `below` | GuidedTour | killed (1) |
| Q-B4d | off-screen dock at the top-left | GuidedTour | killed (1) |
| Q-B4e | off-screen dock at the right | GuidedTour | killed (1) |
| Q-B4f | `free` does not scroll the target into view | GuidedTour | **survived** (S-1) |
| Q-B7a | the "when reached" half dropped | GuidedTour + taggingTour | killed |
| Q-B7b | the "at start" half dropped | GuidedTour + HubDesignPanel.flow | killed |
| Q-B7c | Back ignores availability | GuidedTour | killed (2) |
| Q-B7d | counter position = idx + 1 | GuidedTour | killed |
| Q-B7e | intro on index 0 only | GuidedTour | survived (N-6) |
| Q-B5a | `p_max_pu` exemption dropped | B5 tests | killed |
| Q-B5b | marginal-cost-series exemption dropped | B5 tests | killed |
| Q-B5c | exemption widened to `p_min_pu` series | B5 tests | **survived** (S-1) |
| Q-B5d | exempt any static `p_max_pu < 1` | B5 tests | killed |
| Q-B5e | `overnight_cost` ignored | B5 tests | survived (N-6, predates P30) |
| Q-B6a | `flat_projects_root` | local-settings test | killed |
| Q-B6b | app-data `/projects` | local-settings test | killed |
| Q-B6c | FE line shown with the old fallback root | NewProjectWizard | killed (3) |
| Q-B6d | FE line omits the name | NewProjectWizard | killed |
| Q-B8a | template named before study | HubDesignPanel.flow | killed |
| Q-B8b | readiness never named | HubDesignPanel.flow | killed |
| Q-B8c | observer fetches on its own | flow + useHubData | killed |
| Q-B8d | quiet failure logged at ERROR | client.quietToast | killed |
| Q-B8e | Run title dropped | GoalCard + flow | killed |
| Q-B8f | Retry skips readiness | flow | killed |
| Q-B9a | `aria-describedby` dropped | AppHeader.uiMode | killed |
| Q-B9b | descriptions cross-wired | AppHeader.uiMode | killed |
| Q-B9c | description not `sr-only` | uiMode + expertUnchanged | killed |
| Q-B10a | selection change keeps the request | editRequest | killed |
| Q-B10b | selection change always clears (prepare breaks) | editRequest + taggingTour | killed |
| Q-B10c | the clear compares type only | editRequest | **survived** (S-1) |
| Q-B10d | prepare passes no name | taggingTour | killed |
| Q-C12a | MWh rule also rewrites `€/MWh` | plainWords | killed |

29 / 34 killed. The survivors are Q-B4f, Q-B10c and Q-B5c (S-1), and Q-B7e and Q-B5e (N-6). (Q-B6c / Q-B6d are the frontend harness's Q-B6a / Q-B6b.)

No processes are left running, and both scratch worktrees are removed. This file is the only repo write, and it is uncommitted.

---

## Re-gate (2026-10-05, HEAD `37d446c21`)

**Scope:** `git diff ec0cc082b..37d446c21`. That is `44e685888` (B6-1), `c8ee7c552` (S-1, S-2, N-4 comment) and `37d446c21` (phase note), on top of the gate record `091bf899a`.
**Scratch:** `qa30/rg/`. Mutations ran in `qa30/wt` and the live probe in `qa30/wt2`, both at HEAD and both removed now. No repo source or test was edited.

### Verdict: **GO**

The blocker is fixed: the line is now true for every name I tried, on the live dialog. S-1 and S-2 are fixed and pinned. Every gate row is green, and all 9 re-gate mutants are killed.

### B6-1: the sentence is now true (live)
- **The fix:** `NewProjectWizard.tsx:218-221` now reads `Saved in a folder named after the project, under <root>/`. It never names the folder.
- **Live probe:** a scratch copy of the smoke in `wt2` opened the real dialog, typed each name, read the line, clicked *Create blank project*, and then listed `<root>` (`qa30/rg/probe.log`, screenshots `rg/probe/62…65-qa30rg-line-*.png`):

  | Typed | Line | Folder created |
  |---|---|---|
  | `grid` | `Saved in a folder named after the project, under <root>/` | `grid` |
  | `Grid` | same | `Grid (2)` |
  | `Study.` | same | `Study` |
  | `CON` | same | `CON_` |

  Every folder is under `<root>` and named after the project, so the sentence is true in each case. Row 5's own assertion was tightened to match the line exactly.
- **N-11 (note, out of B6's scope):** a pre-migration row that stores an *absolute* path is resolved as-is (`project_registry.project_dir:184-187`), and such a row can point outside `projects_root` by design. Typing that row's name on the Blank tab takes the overwrite path into that directory. The blank-tab line then does not hold, but the existing `willOverwrite` warning is the surface for that case. This needs a legacy row plus a deliberate overwrite, so it is not a blocker.

### S-1 and S-2
- **The S-1 survivors are now killed** (`qa30/rg/mut_fe.log`, `qa30/rg/mut_be.log`):
  - Q-B4f: killed by "free placement scrolls the target into view (centred)".
  - Q-B10c: killed by "a request for bus A is cleared when the selection moves to bus B".
  - Q-B5c: killed by `test_a_p_min_pu_series_does_not_exempt`.
- **S-2:**
  - **The fix:** `uiStore.ts:813-814` (`requestAssetDetail`) and `:863-865` (`setCurrentProject`) now clear `propertiesEditRequest`.
  - **My scratch test passes.** It covers three cases: after a switch the request is `null`; after an asset-detail jump it is `null`; and the normal path (select → open the panel → request → re-select the same component) **keeps** the request.
  - **Normal Edit path:**
    - `PropertiesPanel.editRequest.test.tsx` (9 tests) and `EhReferenceDesignPanel.taggingTour.test.tsx` are green.
    - Live, the P22.9 tagging step opens the Bus Edit form, and the Link step reaches `2/2` (row 5).
    - `prepareTaggingTour` calls neither cleared setter after it requests (`prepareTaggingTour.ts:41-45`).
- **N-4 comment:** `OverviewPanel.tsx:185-188` now states the INFO + ERROR behaviour correctly.

### Rows

| # | Command (cwd) | Result |
|---|---|---|
| 1 | `git diff --stat 5a9052599..37d446c21 -- pypsa-gui/backend` → only `tests/test_validation_gen_costs.py` (+11, one new test, which row 2 runs) | the row-1 log stands |
| 2 | the row-2 command from the first gate (`pypsa-gui/backend`, HEAD) | **780 passed, 17 skipped** (`qa30/rg/row2.log`; previously 779, +1 for the new test) |
| 3 | `npx tsc --noEmit -p .` (`pypsa-gui/frontend`) | **0 errors** |
| 4 | `npx vitest run` (`pypsa-gui/frontend`) | **244 files / 2826 passed** (`qa30/rg/row4.log`; previously 2820, +6 tests) |
| 4s | `npx vitest run src/components/GuidedTour src/pages/hubDesign src/layout/PropertiesPanel src/layout/AppHeader` ×3 | **3 / 3 green, 224 each** |
| 5 | `PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers node scripts/smoke-guided.mjs --phase P30 --out qa30/rg/smoke30` (`pypsa-gui/frontend`, main checkout) | **PASS, 61 screenshots, exit 0**. Its B6 check is now `line === "Saved in a folder named after the project, under <RUN>/projects/"`. The `wt2` probe run also passes |
| 7 / E | `git diff bcb6a671f -- 'pypsa-gui/frontend/src/**' \| grep -c uiMode` → 6, unchanged. The four `*.expertUnchanged` tests and snapshots and `chat_tools_schema` have no diff since base | signed |

### Re-gate mutation table (`qa30/mut_fe_rg.py`, `qa30/mut_be_rg.py`)

| Id | Mutant | Result |
|---|---|---|
| Q-B4f | `free` does not scroll the target into view (previous survivor) | killed |
| Q-B10c | the selection-change clear compares type only (previous survivor) | killed |
| Q-B5c | exemption widened to `p_min_pu` series (previous survivor) | killed |
| R-B6a | the line names the folder again (`{root}/{name}/`) | killed (2) |
| R-B6b | the line shows with no root | killed (3) |
| R-S2a | the project switch keeps the request | killed |
| R-S2b | the asset-detail jump keeps the request | killed |
| R-S1a | free placement scrolls with `block:'nearest'` | killed |
| R-S1b | centre-scroll on every placement, not only `free` | killed |

9 / 9 killed. The remaining notes from the first gate (N-1 … N-10) stand, and none of them blocks.
