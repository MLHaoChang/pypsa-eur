# Guided mode: P26 gate (final)

**Reviewer:** independent QA gate. **Date:** 2026-09-28. **Branch:** `claude/epic-allen-k2t1c4` at `745e912`. **Diff reviewed:** `7bc2233..HEAD`.
No source or test file was edited. `git status` is clean. No processes are left running.

## Verdict: NO-GO

All automated rows are green and every mutation I tried is killed. But two defects break the product owner's honesty and confirmation requirements. Both are small fixes.

## Blockers

### B1. The Guided greeting says "The hub study is running" for as long as the chat is empty, after the study has finished

- **Where:** `pypsa-gui/frontend/src/components/ChatLaunchGreeting.tsx:126-130`. The study query has no `refetchInterval`. `useHubStudy` (`pages/hubDesign/useHubData.ts:26`) does poll, but only while the hub panel is mounted.
- **Repro:**
  1. Guided, Data Center template.
  2. Site → Goal → Run study.
  3. While it runs, click "Close" on the Hub design panel.
  4. Wait for the study to finish (the API says `done` after about 20 s).
  5. The greeting still reads "The hub study is running — follow it in Hub design." At +6 s and at +16 s after the study finished, it was unchanged.
  6. The greeting is on an empty transcript. Nothing refetches it: `refetchOnWindowFocus` is off and its 5 s `staleTime` only matters on remount.
- **Why it blocks:** the owner asked for the greeting to be truthful in every state, and "study running" is one of them. The greeting states a running study that is done.
- **Suggested fix:** give the greeting query `refetchInterval: ehStudyRefetchInterval` (already exported from `EhReferenceDesignPanel`). It is the same key and options as the hub, so no double poll.
- **Test gap:** no test covers this. `ChatLaunchGreeting.solvedState.test.tsx` only checks a static "running" record.
- **Probe:** `qa26/qa4.mjs`, log `qa26/qa4.log`.

### B2. A Guided confirmation card for a destructive call hides what is being deleted or replaced

- **Where:** `pypsa-gui/frontend/src/components/ChatPanel.tsx:339-381`. `GUIDED_CARD_SUMMARY` has no entry for the destructive tools. The fallback `The assistant wants to use <tool in words>` is shown, and the tool id and arguments sit in a collapsed Details (~line 600).
- **Repro:** in Guided, ask the assistant to run `delete_component` with `{"component_class":"Generator","name":"genset_1"}`. The card reads exactly:
  - "Confirm / The assistant wants to use delete component / Details / Approve / Deny".
  - `import_network_nc` (which replaces the whole network) reads "The assistant wants to use import network nc".
  - Screenshots: `qa26/qa5out/*qa-card-delete.png` and `*qa-card-import.png`.
- **Why it blocks:** before P26 the raw JSON was visible, so the target was on the card. Now, for the highest-risk tools, the user is asked to approve without seeing which component, project or file. That is weaker than P25 and against "the assistant does the work and the user confirms". Edits and runs are fine: "Change the settings of genset_1" shows the name.
- **Suggested fix:**
  - Add summaries for the destructive tools: `delete_component`, `cascade_delete_bus`, `import_network_nc`, `restore_project_snapshot`, `delete_project` and the like.
  - Make the generic fallback include the first identifying argument (`name`, `names`, `path`, `project_id`), or keep the JSON expanded for the destructive tier.

## Non-blocking notes

1. **Transient stale FMEA tab after a sweep.**
   - `ImproveCard` now shares the `fmea_modes` cache. If the user opens the FMEA tab within about 2 s of the sweep ending, the tab shows "Sweeping…" and only the partial rows (4 of 8 on the data center).
   - It self-heals within about 2.3 s. I measured this at 250 ms sampling (`qa26/qa3.log`); with a 3 s wait there was no transient (`qa26/qa2.log`).
   - The smoke reads its "FMEA rows" at that moment, so it prints 4 / 4 / 3. The true counts are 8 / 4 / 6, as in the close-out note. The smoke's row assertion is only `> 0`.
2. **Greeting and a stale study.** After the user edits the network, the greeting still says "A study has run on this network — its results are in Hub design", although the review is `stale`. A failed or aborted study reads "Not solved yet." That is not false, but it is not informative.
3. **Bug 5 (`€0.0` severity on class-A rows)** is deferred and documented. It is the nearest thing to "a missing number shown as zero" that Guided links to. The hub cards themselves say "no measurable cost".
4. **Leftover state in `ImproveCard` after a project switch.** `sweep.isSuccess` stays true after a project switch while the card stays mounted, so the new project's `fmea_modes` is fetched needlessly. Harmless; not reproduced.
5. **Send-gate rule for bound profiles** is recorded in the note. With `profileId` set to a non-active but ready profile, the key offer still shows. This is by design.
6. **Smoke exit code.** `smoke-guided.mjs` exits 0 on a FAIL in my probe copy. The real phases printed PASS, so I take it as a probe-harness artefact.

## First-time-user friction list

Screenshots: `qa26/run/P26/`, 37 images, all three templates.

- **Greeting words.**
  - "Not solved yet.", "Solved…", and the chips "Check adequacy" and "Summarize this solve" are engine words in Guided, before any study.
  - After the study, the line "A study has run… results are in Hub design" is plain and correct.
- **Tool progress lines** "… preparing run_eh_study" and "→ run_eh_study" stay raw above the card (known limitation 2). "Understood — run_eh_study was not applied." is from the stub.
- **Improve wording:** the "IMPORTANT"/"WORTH DOING" finding titles still carry "outage-driven" and "accounts for 93% of the outage risk".
- **The Improve description does not match the action.** The data-center finding says "the assistant would size the local capacity…", but its action opens a "Run the reliability study" card. A first-timer may not connect the two.
- **H₂ hub.**
  - Results say "set an allowed shortfall in step 3 (Goal)". The Goal box is a greyed "no goal", and there is no button to jump there.
  - The user sees a headline that says nothing was decided. This is honest but a mild dead end.
- **FMEA tab from Guided** still shows "SIMULATION · RESULTS", "Optimization results", class letters, `copt`, and `€0.0` rows (known limitation 1).
- **Toast:** "Created … from template" briefly covers Send.
- **Good:**
  - "Next: Goal" and "Next: Improve" work.
  - The card headers are by purpose.
  - The declined line is plain: "You declined — nothing was changed."
  - The summaries "Run the reliability study for this site (about 30 calculation steps)" and "Change the settings of genset_1" read well.

## Honesty and safety checks

- **Certified without evidence:** none.
  - The data center reads "Not certified: about 12 h/yr … vs a 3 h/yr goal".
  - The microgrid reads "Not decided: 3–6 h/yr straddles the goal".
  - The H₂ hub reads "No reliability goal is set… did not certify it".
  - The smoke asserts the headline against the review for every template.
- **Missing numbers:** "no measurable cost", not `0`. Exception: see note 3.
- **Every Guided change goes through a confirmation card:**
  - `_confirm_tiers(guided)` adds `write` (`chat_service.py:105-110`).
  - `AUTO_APPROVE_TIERS` is intersected with the destructive tiers, so it can never contain `write`.
  - `test_guided_write_confirmation` passes.
  - Caveat: see B2 on what the card shows.
- **Live network untouched:** the smoke checks buses, links and generators equal after the study and after the sweep, on all three templates. Only the accepted `*_nom_opt` outputs change (8 / 7 / 8 after the sweep, 0 after the study).
- **Expert unchanged** apart from the recorded deviations:
  - the key-offer rule;
  - the reloaded Improve label;
  - `plainWords`.
  - Mutation M9, which makes Expert render the Guided card, is killed by 8 tests. The Expert greeting is unchanged: "Solved…" after a foreground solve.
- **Greeting states**, probed on the live app (`qa26/qa2.log`):

| State | Greeting |
|---|---|
| never solved | "Not solved yet." |
| study running, hub open | "The hub study is running…" |
| study running, hub closed and finished | STALE "running" (B1) |
| study done | "A study has run on this network — its results are in Hub design." |
| sweep done | same line, still truthful |
| foreground solve | "Solved — the results match the network as it stands." |
| Expert | "Solved…" |
| Guided again | "Solved…" |

## Mutations

Run on scratch copies of `src` (`qa26/mut`, `qa26/mutate.sh`). The real tree is unchanged.

| # | Change mutated | Result |
|---|---|---|
| M1 | card summary count dropped | killed (1 test) |
| M2 / M2b | declined regex broken / `confirmation_denied` line no longer hidden | killed (2 / 1) |
| M3 / M3b | greeting study query disabled / "running" branch removed | killed (3 / 1) |
| M3d / M3e | Expert also reads the study | killed (1) |
| M3c | `guided &&` dropped inside `solveLine` only | survives. It is an equivalent mutant, because the query is disabled in Expert. |
| M4 / M4c | ImproveCard: no invalidation on sweep end / query disabled | killed (1 / 1) |
| M4b | ImproveCard: poll interval removed | killed (1) |
| M5 / M5b / M5c | key offer: profile clause dropped / ready check dropped / `!== false` | killed (1 / 3 / 3) |
| M6 / M6b / M6c | purpose headers: snapshot / tier fallback / load | killed (1 / 4 / 2) |
| M7 | Next button no-op | killed (2) |
| M8 | plain-words rule dropped | killed (1) |
| M9 | Expert shows the Guided card | killed (8) |

**Gap:** no test covers the greeting's polling (B1).

## System level

- **No polling storms.**
  - During the study, `eh_study` was requested 14 times in 25 s (one 2 s poll), `simulation/status` once and `eh_review` once.
  - During the sweep, `fmea_modes` was requested 8 times in 17.5 s (one poll) and there were no extra `simulation/status` calls.
  - There were no study or status requests in 10 s idle.
- **Project switch:** the greeting and ImproveCard queries are keyed by project (`nk(project, …)`), so a switch does not show stale data. Backend isolation tests pass. I did not run an in-app switch end to end (see note 4).

## Close-out note versus reality

- **Accurate:**
  - vitest 233 files and 2481 tests, and tsc 0. Both matched my runs.
  - The verdicts and the `*_nom_opt` counts.
  - The smokes: P26 has 37 screenshots, P25 has 12 and P22.9 has 10.
- **Missing limitations:**
  - B1 (greeting stuck on "running") and B2 (destructive cards hide their target). These are defects, not limitations.
  - The transient stale FMEA tab (note 1).
  - The greeting ignores study staleness (note 2).
  - Failed and aborted studies read "Not solved yet." (note 2).
- **Overstated:**
  - "Every Guided card leads with one plain sentence" holds, but for the destructive tools that sentence is only "The assistant wants to use delete component" (B2).
  - "The greeting explains this state" holds only while the greeting is fresh (B1).
- **Smoke row counts:** the note's FMEA row counts (8 / 4 / 6) differ from what the smoke prints (4 / 4 / 3), for the reason in note 1.

## Evidence

- **Row 1** (full backend suite): run by the orchestrator, not by me.
- **Row 2**, 14 files: 613 passed in 491 s (`qa26/row2_mine.txt`).
- **Row 3:** `npx tsc --noEmit -p .` exit 0. Full vitest: 233 files, 2481 tests, all passed (`qa26/vitest_mine.txt`).
  - An earlier run under CPU load had 2 `BottomPanel` timeouts, in a file P26 did not touch. They passed in isolation on the clean re-run.
- **Row 4:** stress ×10 of App*, hubDesign, ChatPanel*, ChatLaunchGreeting*, chatStore*, uiContext*. 36 files and 485 tests passed each time, 10 of 10 (`qa26/stress_mine.txt`).
- **Row 5:** P26, P25, P24, P23, P24-BE and P22.9 all PASS (`qa26/run/*.log`).
  - P26 verdicts: data center `fail`, H₂ hub none (no goal), microgrid `inconclusive`.
  - The screenshots were read as a first-time user.
- **Probes:** `qa26/qa2.mjs`, `qa3.mjs`, `qa4.mjs` and `qa5.mjs`, with logs and screenshots in `qa26/qa*out`.
- **Repo state:** `git status` clean.
