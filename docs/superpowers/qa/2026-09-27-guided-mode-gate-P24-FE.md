# Guided mode: QA gate for P24-FE (hub-design step cards)

- **Reviewer:** an independent QA gate agent. It did not write this code.
- **Date:** 2026-09-27.
- **Scope:** `git diff ecb3b70..HEAD` (the three "WIP P24-FE" commits), branch `claude/epic-allen-k2t1c4`.
- **Contract:**
  - spec §5 (including the §5.5 row "decided at P24-FE"), §8, §10 and the addenda;
  - the P24-FE section of the plan;
  - the P24-BE gate notes N1–N8.
- **Scratch evidence:** `/tmp/claude-0/-home-user-pypsa-eur/93e65137-63f8-5e19-b114-b55788c10af8/scratchpad/qa24fe/`, referred to below as `qa24fe/`.

## Verdict: **NO-GO**

The mechanics are solid:
- rows 2–5 are green;
- the state machine, the headline and the delegate texts are correct and well tested (17 of 17 mutants were killed);
- the cache is shared correctly and nothing refetches in a loop;
- the Expert view is unchanged.

Three problems stop the phase. All three are in the part of the product the owner cares about most: what a first-time user reads and trusts.
- One card makes a false claim about the plan (B1).
- The tour dead-ends for the user who is most likely to open it (B2).
- The Improve card breaks the explicit §5.8 text rule on the main path (B3).

---

## Blockers

### B1: "Cost at your goal" claims the plan meets a goal it does not meet (honesty)

**Where:**
- `pypsa-gui/frontend/src/pages/hubDesign/cards/ResultsCard.tsx:46,74` (label `Cost at your goal`);
- `pypsa-gui/frontend/src/pages/hubDesign/shared/Term.tsx:36`;
- `pypsa-gui/backend/data/guides/eh_fmea_guide.json:273`. The hover reads "The yearly cost of the plan that meets your reliability goal…".

**The problem:** `report.cost_at_target_eur` is the cost of the plan at the **energy (ENS) target**, without load shedding. The Expert view labels it honestly: `Cost@target €37.5m excl. shed`, next to `ENS cap 10‱`. It is not the cost of meeting the LOLE goal.

**Repro:** run `smoke-guided.mjs --phase P24` and look at the screenshots.
- `p24/06-p24-dc-results.png`, data center: the headline says "Not certified: about 12 h/yr of shortfall vs a 3 h/yr goal", and the next line says "Cost at your goal: €37.54 M per year". The hover says this is the cost of a plan that *meets* the goal.
- `p24/15-p24-h2-results.png`, H₂ hub: "No reliability goal is set for this site…" followed by "Cost at your goal: €10.88 M per year". The card quotes a cost at a goal that does not exist.
- `p24/19-p24-mg-results.png`, microgrid: the verdict is inconclusive, and the same label appears.

**Why it blocks:** a first-time user reads this as "meeting my goal costs €37.5 M/yr". That is the kind of certified-looking claim without evidence that the gate must reject.

**Fix direction:**
- Label the number as the cost of the planned design, not the goal. For example: "Yearly cost of this design (before shortfall)".
- Correct the `cost_at_target` catalogue text and its `TERM_FALLBACK` twin together; `Term.test` pins them together.
- Optionally show the cost line only as "at your goal" when `verdict === 'pass'`.
- Add a ResultsCard test for the fail and no-target cases.

### B2: The `hub_design` tour dead-ends before a first study, which is when a new user opens it

**Where:**
- `pypsa-gui/backend/data/guides/eh_fmea_guide.json:218` (last step: `hub-improve-fmea`, reveal `hub-rail-step-improve`, not optional);
- `pypsa-gui/frontend/src/pages/hubDesign/flow.ts` `blockedReason` (Improve is blocked in `no_study`, so the reveal button is disabled);
- `pypsa-gui/frontend/src/components/GuidedTour.tsx:137–143` (clicking a disabled reveal does nothing).

**Repro:** run `qa24fe/qa-probe.mjs --phase QA`. It is a copy of the smoke with one extra probe phase; the log is `qa24fe/qa-probe.txt`. The probe opens a fresh Guided profile, creates the data-center project and clicks **Guide** on the Site card before running anything.
- The tour shows 7 steps. The two optional steps drop, which is correct.
- Step 7/7 "9 · Check what breaks" then shows the warning: "This control is not on screen right now — open the panel or select the component it belongs to, then continue."
- Screenshot: `qa/01-qa-tour-prerun-missing-hub-improve-fmea.png`.

**Why it blocks:**
- The user cannot do what the warning says, because the Improve step is disabled until a study has run.
- The smoke walks the tour only after a study, so it never exercises this path.
- The task asks for "the tour works on screen". It does not, on the first-time path. (Spec §4.3 marks this step as not optional, which was a spec gap. Implementing it literally produces the dead end.)

**Fix direction:** either works.
- Mark `hub-improve-fmea` as `optional` + `after_run`. `test_hub_design_tour_matches_the_spec` must change in the same commit, with a one-line justification in the plan.
- Or render `hub-improve-fmea` (Check risks) on a card that is reachable before a study.

Add a smoke or unit step that walks the tour in `no_study`.

A related finding for the same tour:
- Every reveal click goes through `onPick` → `setStep(s, {user:true})` (`HubDesignPanel.tsx:84`). So walking the tour sets `userMovedRail`. If the study finishes after the tour, the auto-advance to Results is silently suppressed.
- The first step's body also says "a greyed step opens once the one before it is done" (`eh_fmea_guide.json:168`). That is not how the rail works: Site and Goal are always open, and Results and Improve open only after a study.

### B3: The Improve card shows engine jargon, stage ids and ‱ outside the "Why" list (§5.8)

**Where:** `pypsa-gui/frontend/src/pages/hubDesign/cards/ImproveCard.tsx:38` (`f.recommendation`) and `:40` (`What it would do: {f.actions[0].effect}`). Both render the review prose verbatim.

**The rule:** spec §5.8 says: "No `stage` ids, `‱`, or engine names appear except inside the 'Why' evidence list".

**Repro:** `p24/09-p24-dc-improve.png` (data center, the gate's main path). The main text on the first finding includes:
- "achieved ENS 0 ‱";
- "a higher p_nom_min";
- "(dtc_planning)";
- "What it would do: size the local capacity that islanded operation needs (dtc_stress + dtc_planning)";
- the title "LOLE 12.38 h/yr exceeds…".

The second finding includes "The redundancy stage prices GENERIC N+1…" and "price generic N / N+1 / storage scenarios".

**Why it blocks:** this is the explicit text rule of the phase, broken on the headline path. It is also the card where the first-time user decides what to do next.

**Fix direction:**
- Show the plain title and effect, and move `recommendation` into the Why block, labelled "technical".
- Or have the FE strip or replace stage ids and ‱ (for example `‱` → "parts per 10 000", as in the Goal card).
- Or add a plain `summary` to the review findings (backend, additive).

Add a card test that fails when any of `‱`, `dtc_`, `p_nom`, `stage` appear outside `hub-improve-evidence-*`.

---

## Non-blocking notes

### System integration (checked, no bug found)

- **Shared keys:**
  - `eh_study` has the same key and the same `refetchInterval` export (`ehStudyRefetchInterval`) in the cards and in the Expert panel.
  - The readiness keys cannot collide. The cards use `[…eh_readiness, archetype, 'hub-design', overrides]`; the Expert panel uses `[…, archetype, dtc, budget, preview]`.
  - `solverConfig` and `eh_template` use the same keys and functions as their existing readers.
  - In the probe:
    - with only the hub mounted, the hub polled `eh_study` about every 2 s;
    - with the Expert panel mounted instead, the rate was 2 GETs in 4 s, which is the Expert panel's own polling;
    - once finished and idle, there were 0 `eh_*` requests in 10 s, so there is no refetch loop.
  - `useStudyFinishedInvalidation` is called once per mounted poller and only fires on a transition out of `running`, as before.
- **The Expert panel shows the same study:** in the probe, a study started from the Goal card showed as `Studying…` on `eh-run` in Results → Adequacy (`qa/02-qa-expert-midrun.png`).
- **Project switch mid-run:** clicking another template on the Start card while a study runs gets a backend **409**. The project does not change, and the cards keep showing the running project's study. There is no cross-project contamination (`qa/03`, `qa/04`).
  - UX gap: the template buttons stay enabled while a study runs. The only feedback is the wizard hook's generic toast "Template import failed: Request failed with status code 409" (`useCreateFromTemplate.ts:68`), which does not use `blockerMessage`.
  - Suggestion: disable the Start card's template buttons while `running`, with a title, or show `blockerMessage`.
- **Tour holds:** `GuidedTour` still holds and releases the P23 auto-open. The P23 smoke passes, and the `hub_design` tour runs inside the hub panel.
- **`ehReportRequest`:**
  - It is consumed and cleared on the panel's first effect. Mutant M11 is killed.
  - `scrollToReport` stays armed if no report exists yet. That cannot happen from the card, because Open report needs a report. Low risk.
- **Expert unchanged:**
  - No `*.snap` or `*expertUnchanged*` file changed, and all three snapshot suites pass.
  - `git diff ecb3b70..HEAD -- src | grep -c uiMode` = 1: a test comment only, with no new `uiMode` branch.
  - The Expert panel's changes are:
    - the refactors `ehStudyRefetchInterval` and `ehStudyQueryKeys`, with the same keys and order plus the existing `eh_review`;
    - a request-only effect.
  - `blockerMessage` moved to `utils/` and is re-exported from `McPanel` (N6).
  - The P22.9 and P24-BE Expert smokes pass.
- **P23 Guided hiding:** intact. The P23 smoke passes: two tabs in Guided, and everything back in Expert.
- **Packaging:**
  - The catalogue JSON is imported only by `shared/Term.test.tsx`, through a relative path.
  - `vite build --outDir qa24fe/build` succeeds.
  - The bundle contains only the `TERM_FALLBACK` strings. It does not contain the catalogue: no tour texts, no `renewable_availability_multiplier`, no `hub_design` steps.
  - `tsc` is clean.

### Contract checks

- **Card tree and test ids:** as in §5.1. The five rail ids are literal (accepted deviation 2). All nine tour targets are literal in JSX, and `test_guides` pins them.
- **State machine:** `no_project`, `no_study`, `running`, `done` and `stale`. Failed and aborted are read from the study record (N2). An aborted study with a report counts as done (deviation 4). A failed study never fetches the review (M7 and M8 killed). `stale` comes from the boolean (N8, M2 and M9 killed).
- **Headline (§5.5):** all six rows are implemented. The smoke matched the verdicts:
  - data center: fail;
  - H₂ hub: the new "no goal" row;
  - microgrid: inconclusive, 3–6 h/yr.
- **Delegation texts (§5.7):** verbatim, except the P24 grid text, which omits the `suggest_eh_setup` clause as the spec requires.
- **Off-grid wording:** correct (N4). The tie is shown as "normally open", with no MW and no outage gap (`p24/16`).
- **Term hovers:** every `Term k` is typed as a `TermKey`. `Term.test` pins all 20 fallbacks to the catalogue word for word.
- **Missing numbers:** no missing number is shown as zero, with one small exception. `GoalCard.tsx:74` renders `solves_consumed ?? 0`, so it would say "0 of N solves" if the pipeline had a budget but no count. In practice, the running text was "Studying… (up to 30 solves)".

### UX list from the P24 screenshots (first-time user)

1. **Start card:**
   - The provenance reads "synthetic illustrative data (**P19 template**)…". The internal phase id reaches the user (`p24/02`).
   - The template blurbs use unexplained jargon: "gensets", "UPS", "PV", "H2 … offtake", "subsea tie".
2. **Header:** a large red **Run LOPF** button stays in the header in Guided mode. It competes with "Run study", and "LOPF" is jargon. This comes from P23 scope, but it is the most visible first-time confusion.
3. **Goal card:**
   - "Price of undelivered energy ⓘ : €5,000 per MWh" has a stray space before the colon.
   - "MWh" has no hover, although the catalogue text avoids it.
4. **Running text:** "Studying… (up to 30 solves)". "solves" is jargon for this audience.
5. **Results headline:** "…more **Monte-Carlo draws** would settle it" (spec-literal, but jargon). The verdict "driven by site_transformer" uses a raw component name, which is acceptable.
6. **Biggest risks:** the list includes "electrolyser — about €0.00 per year" and "fuel_cell — about €0.00 per year" (H₂ hub). Listing €0 items as "biggest risks" reads oddly. Consider hiding rows with zero criticality.
7. **Why evidence:** raw floats such as `12.383928571428568` and `[7.68316774385459, 17.084689399002553]`. They are labelled "technical", which is allowed, but rounding would help.
8. **Improve card:** "About stress scenarios ⓘ" is a stand-alone hover with nothing next to it. It looks like a control but does nothing.
9. **Rail:**
   - On an own network, Start shows no tick until a study runs.
   - Walking the tour leaves the rail on the last revealed step (Goal, `site=todo`).
   - Both are fine, but combined with B2 they are confusing.
10. **Dock greeting:** "Not solved yet." stays after a finished hub study. This is pre-existing (spec §10: the study does not record a foreground solve), but the hub makes it more visible.

### Vacuity: 17 of 17 mutants killed

The mutants were run on a scratch copy (`qa24fe/mut`, driven by `qa24fe/mutate.py`; the log is `qa24fe/mutants.txt`). The real files were not touched, and `git status` is clean. Each mutant below was killed:

| # | Mutant | Killed |
|---|---|---|
| M1 | A failed study counts as having results | ✓ |
| M2 | Stale is ignored | ✓ |
| M3 | Running does not block Results | ✓ |
| M4 | The fail headline uses `.toFixed(1)` | ✓ |
| M5 | The no-goal/no-number row is removed | ✓ |
| M6 | The pack value overwrites the user's typing | ✓ |
| M7 | The review is fetched regardless of the study | ✓ |
| M8 | The review hook ignores its `enabled` flag | ✓ |
| M9 | The stale banner is removed | ✓ |
| M10 | Auto-advance ignores `userMovedRail` | ✓ |
| M11 | `ehReportRequest` is not cleared | ✓ |
| M12 | `eh_review` is dropped from the invalidation set | ✓ |
| M13 | The off-grid tie filter is removed | ✓ |
| M14 | Delegate uses the last action instead of the first | ✓ |
| M15 | The Goal body ignores the template form | ✓ |
| M16 | VOLL ≤ 0 does not disable Run | ✓ |
| M17 | The initial step ignores the template | ✓ |

The test gaps are the three blockers:
- no test asserts the cost label against the verdict;
- no test walks the tour in `no_study`;
- no test checks for jargon on the Improve card.

### N1–N8 carried from P24-BE

| Note | How it was handled |
|---|---|
| N1 | The order is now pinned: `nav:` is recorded in `calls` (`useCreateFromTemplate.test.tsx`). |
| N2 | Failed and aborted come from the study record (`flow.ts`). |
| N3 | The running prose is never rendered (`useHubReview`, which returns only `ok`). |
| N4 | Off-grid wording is done. Slack, sink, dump and spill are excluded from `missing`, with a backend test. |
| N5 | The catalogue is reworded (no MVA, MWh or inverter; no questions in the `hub_*` intros; `verdict` includes "no goal"), with new tests. |
| N6 | `blockerMessage` is in `utils/`. |
| N7 | `_finite_or_none`, with a route test. |
| N8 | Stale is read from the boolean. |

All eight are handled as claimed.

---

## Evidence

| Row | Command | Result |
|---|---|---|
| 2 | Targeted EH set (the ten files listed in the brief) | `495 passed in 469.43s`, exit 0 (`qa24fe/row2.txt`) |
| 3 | `npx tsc --noEmit -p .` | exit 0, no output (`qa24fe/row3.txt`) |
| 4 | `npx vitest run` | `Test Files 227 passed (227)`, `Tests 2336 passed (2336)`, exit 0 (`qa24fe/row4.txt`) |
| 5 | `smoke-guided.mjs --phase P24 --out qa24fe/p24` | `PASS — 19 screenshots`, exit 0. Verdicts: data center fail ("Not certified: about 12 h/yr … driven by site_transformer"), H₂ hub no target (the decided sentence), microgrid inconclusive (3–6 h/yr). Buses, links and generators equal around all three studies and the sweep. |
| 5 | `--phase P23`, `--phase P24-BE`, `--phase P22.9` | All PASS, exit 0 (`qa24fe/smoke-*.txt`) |
| 5 | Extra probe (`qa24fe/qa-probe.mjs --phase QA`) | Reproduces B2. Covers the shared study, the mid-run switch, polling and idle refetch. |
| — | Processes after all runs | 0 left over (uvicorn, vite, chromium, stub) |
| — | `vite build --outDir qa24fe/build` | OK. No backend catalogue in the bundle. |

Row 1 (the full backend suite) is run by the orchestrator and was not re-run here.
