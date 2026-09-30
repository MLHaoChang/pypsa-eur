# QA gate: P29 — Guided chat and risk table (B1, B2, B3, plus the out-of-phase `d95357335`)

**Date:** 2026-09-30. **Reviewer:** independent QA gate (did not write the code).
**Scope:** `git diff e7f139b84..f66fc1165` on `claude/epic-allen-k2t1c4` (10 commits, backend and frontend), including the out-of-phase `d95357335` (`dispatch_status` ignores transient rows).
**Contract:** deferred spec `2026-09-28-guided-mode-deferred.md` §0 and §4 (P29); the plan's "P29 phase note".
**Scratch:** `/tmp/claude-0/-home-user-pypsa-eur/93e65137-63f8-5e19-b114-b55788c10af8/scratchpad/qa29/` (written `qa29/` below). No source or test in the repo was edited. Mutations ran in a scratch worktree `qa29/wt` (HEAD). The base snapshot check ran in `qa29/wtbase` (`e7f139b84`). A live B1 probe ran in `qa29/wt2`, which holds a scratch-only copy of the smoke with an extra step.

## Verdict: **NO-GO** — one blocker (B1-1), a phrase-table fix

The engineering is sound, and every gate row is green:
- the `zero_reason` rule and its order;
- all five engine paths, and `per_mode` forwarding;
- the Guided FMEA tab, with Expert unchanged byte for byte apart from the two justified `title` attributes;
- the settled-call rule for "Working" lines;
- the transient-row fix, which works across threads in the real request context.

I killed 24 of 26 mutants. The two survivors are equivalent.

The one blocker is a false statement on a Guided surface, the same class as the P28 blocker. A tool that only *starts* a long study is labelled **"Done: …"** as soon as the study has started. I reproduced this live: "Done: check what happens when equipment fails" showed while `GET /api/results/fmea_sweep` said `running`. The spec's own table and red test prescribe this wording, so the fix needs a one-line spec deviation.

The verdict is GO once B1-1 is fixed. The fix is a change to the phrase table plus the red test's strings.

## Blocker

### B1-1: "Done: …" is shown for a tool that only started a study that is still running

- **Where:** `pypsa-gui/frontend/src/components/ChatPanel.tsx:473-482`, in `GUIDED_TOOL_PHRASE`:
  - `run_eh_study: 'run the reliability study'`
  - `run_fmea_sweep: 'check what happens when equipment fails'`

  `:523` renders `✓ X` as `Done: <phrase>`.
- **Why it is false:** the tool descriptions say these tools *start* work and return at once:
  - `chat_tools_schema.py:995-997`, `run_fmea_sweep`: "Start the contingency sweep … returns {status:'running'} immediately — poll get_adequacy_results";
  - `run_eh_study` at `:1076-1077`: "Start the Energy Hub reference-design study…".

  The `✓` line means the start request succeeded, not that the study finished. "Done: run the reliability study" reads as "the study is finished".
- **Repro (live):** run `qa29/wt2`'s smoke copy with `QA29_PROBE=1 … --phase P29 --template eh_datacenter`. The log is `qa29/probe.log`, lines 142-157. Send "Run the tool run_fmea_sweep with exactly these arguments: {}", then Approve. After the turn ends:
  - the log records `QA29 sweep status right after "Done": running`;
  - the transcript reads "Done: check what happens when equipment fails";
  - screenshot `qa29/probe/15-qa29-dc-d-sweep-done-label.png`.
- **Scope:** the same applies to the other start-tools, which reach the fallback: `run_simulation`, `run_frontier_study`, `run_mc_study`, `run_coupling_loop` and `run_margin_loop`. "Done: use run frontier study" is both jargon and false.
- **Fix (cheap):** name the start in the phrase, so that both labels are true:
  - `run_eh_study → 'start the reliability study'` and `run_fmea_sweep → 'start the equipment-failure check'`;
  - add entries for the other five start-tools in the same form ("start the …").

  Then update the "P29: Guided tool lines" test strings and record a one-line deviation in the plan note. Spec §4.1's example phrase is the source of the error, so the justification is the tool description quoted above.

## Should-fix

### S-1: the Guided FMEA tab still shows "CLASS C" and "mitigability"
- **Where:**
  - `StressScenarioEditor.tsx:216`, rendered at the foot of `FmeaTab`, has the heading "STRESS SCENARIOS (CLASS C) · 2/10";
  - the Guided "Add your own row" form keeps the Expert placeholders `mitigability (optional)` and `severity €` (`FmeaTab.tsx:405-418`, which are not branched).
- **Repro:** smoke screenshot `qa29/smoke29/11-p29-dc-fmea-guided.png`.
- **Why:** B2 removes the class letter from the table, but the same tab shows it one panel lower. Spec §0.1 says "no engine ids on a Guided surface". The implementer records the editor as a known limitation, "outside the B2 contract". I accept that it is out of contract, but it is cheap to fix: one heading and two placeholders.

### S-2: `no_outage_data` is shown for a unit whose outage rate is explicitly 0
- **Where:** `services/adequacy/worksheet.py:161-167`. `occurrence_per_year <= 0` gives `no_outage_data`. `copt.py:1147-1148` sets `occ = 0` both when MTTR is 0 and when the unit's rate is 0.
- **Repro:** `_copt_network(grid=True)` from `test_fmea_zero_reason.py`. The `grid` generator has `outage_rate_value=0.0`, `mttr_hours=24`, and `rate_source="asset"`. It gets `zero_reason = "no_outage_data"`, which renders as "no outage data" (my probe, run inline).
- **Why:** the data exists and says the unit does not fail. "no outage data" would send a Guided user to enter data they already entered. The rule is the spec's own, so the implementation is compliant. The wording, or a split on `rate_source == "missing"`, should change in a later phase, for example "no outages expected".

## Notes (no action required for GO)

- **N-1, deviation 1 is correct and live-checked:**
  - Probe `c-card-pending`: while the confirmation card waits, no "Working" row shows. The `→` line is hidden until a `tool_request` arrives, and the backend emits it with the dispatch.
  - A read tool reads "Done: list what is in the network" (`a-read`).
  - A failing tool reads "Could not: read the study results", with the message on the next line (`b-error`).
  - A declined call reads only "You declined — nothing was changed." (the smoke's two transcript checks).
  - Parallel calls are keyed by `tool_use_id`. Mutant F6 ("any settled call hides every Working line") is killed.
  - Replayed history (probe `e-reloaded`, a reload mid-transcript) carries no tool rows at all, only user and assistant turns, so no raw line can appear.
  - A stream that dies between `tool_request` and its outcome would leave "Working: …" in place. The backend's abort is checked between loop iterations, not mid-tool (`chat_service.py:4265-4267`), so only a transport error hits this. It is rare, and the P26 behaviour was the same.
- **N-2:** the Guided "Could not" message is the backend's raw text. Live it read "Unknown adequacy kind: 'bogus_kind'. Known: adequacy, copt, coupling_loop, …", which contains engine ids. Spec §4.1 says "the error's own message stays", so this is compliant. It is worth a plain-words pass later.
- **N-3:** the slide-panel breadcrumb above the page header still reads "SIMULATION / Results" in Guided (screenshot `11-…`). The spec's contract is the `PageHeader` eyebrow, which is correct ("HUB DESIGN · RESULTS").
- **N-4, the smoke's CSV check is weaker than it reads:** `worksheetCsvRows` never reads `zero_reason` (the `fmea.ts` diff leaves it untouched), so "bytes equal with and without the key" is true by construction. I checked the substance by diff instead: `worksheetCsvRows`, `WORKSHEET_CSV_HEADER` and every `downloadCSV` call are unchanged, and `EhReferenceDesignPanel`'s CSVs use fixed columns. The report **JSON** export (`EhReferenceDesignPanel.tsx:1100`) is a raw dump of the backend report, so it gains `zero_reason` in the `fmea_top` rows. That is the spec's "except the new key", as an additive backend field.
- **N-5, sweeps saved before P29:** class-A rows regenerate on every view, so they get the key at once. Stored B and C sweep rows and stored EH reports have no key:
  - the tab shows `€0.0` in both modes, since `zeroReasonText(undefined)` is null and the Guided cell falls back to `fmtCurrency`;
  - the hub shows "no measurable cost" (mutants F10 and F11 pin the fallback).

  This is sensible. It is recorded as a known limitation in the plan.
- **N-6, deviations 2-8:**

  | Deviation | Judgement |
  |---|---|
  | 2, rebound line in words | acceptable; unit-tested; not reached by the smoke |
  | 3, zonal merge re-derives the key | correct and necessary; mutant B9 is killed |
  | 4, extra Guided wording | acceptable; checked on the screenshot. "Yearly risk" is the last column, so the prose is true. |
  | 5, `h1` instead of a `page-header` test id | acceptable, since it avoids Expert markup churn |
  | 6, export check | see N-4 |
  | 7, class-C frequency 0 unreachable | confirmed by `stress._validate` |
  | 8, changed assertions | the three smoke tweaks and the two count bumps are justified |
- **N-7, `d95357335` in other callers:** `ac_pf_service.py:320` (the Stage-2 gate) now also ignores transient rows. After a normal run the slacks are removed and unmarked, so this is a no-op there.

## Gate rows

| Row | Command (cwd) | Result |
|---|---|---|
| 1 | Implementer's `scratchpad/p29/row1.log`: 6716 passed, 31 skipped, 11 deselected, 0 failed. It ran on `a994da65d` from about 06:56 to 07:53, after the last backend commit, and `git diff a994da65d..f66fc1165 -- pypsa-gui/backend` is empty. Checked with `pytest tests/ -m "not slow" --collect-only -q …` (`pypsa-gui/backend`), which gives **6747/6758 collected (11 deselected)** = 6716 + 31 (`qa29/collect.log`). | accepted |
| 2 | `PYTHONPATH=/home/user/pypsa-eur:/home/user/pypsa-eur/pypsa-gui/backend /tmp/claude-0/venv/bin/python -m pytest <14-file set> tests/test_fmea_zero_reason.py tests/test_energy_hub_frontier_fmea.py tests/test_energy_hub_class_c_authoring.py tests/test_energy_hub_class_c_profiles.py tests/test_dispatch_status_transient.py -p no:cacheprovider -W ignore -q -o addopts=""` (`pypsa-gui/backend`) | **776 passed, 17 skipped**, exit 0 (`qa29/row2.log`). This includes `test_guided_mode_prompt.py`. |
| 3 | `npx tsc --noEmit -p .` (`pypsa-gui/frontend`) | exit 0, 0 errors (`qa29/tsc.log`) |
| 4 | `npx vitest run` (`pypsa-gui/frontend`) | **244 files / 2777 tests passed**, exit 0 (`qa29/vitest.log`) |
| 4s | 3× sample: `npx vitest run src/components/ChatPanel src/pages/results/FmeaTab src/pages/hubDesign` (`pypsa-gui/frontend`) | **3/3 green**, 573 tests each (`qa29/stress-{1,2,3}.log`). The implementer's 10× log is `p29/row4s.log`. |
| 5 | `PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers node scripts/smoke-guided.mjs --phase P29 --out qa29/smoke29` (`pypsa-gui/frontend`) | **PASS**, 39 screenshots (`qa29/smoke29.log`). Results: h1 "Reliability results"; `data-guided="1"`; 48 cells with no letter or engine id; `genset_1` reads "…no shortfall — the site copes without it"; `per_mode` has 8 rows, each with the key; no other new key; the CSV is 1212 bytes, equal, with an unchanged header. |
| 5+ | live B1 probe (scratch smoke copy, `qa29/probe.log`, `qa29/probe/12…16-qa29-*.png`) | read / error / card-pending / reload as in N-1; **B1-1 reproduced**. The run then fails by design after my reload step. |
| 7 | `git diff e7f139b84 -- 'pypsa-gui/frontend/src/**' \| grep -c uiMode` | **16**. Product lines: the `Results.tsx` eyebrow and title (two ternaries, with an Expert arm equal to the base) and `FmeaTab`'s `guided` flag. `ChatPanel` reuses its existing `uiMode === 'guided'` gate, and the new settled-call check sits behind `toolLine &&`, which is null in Expert. The rest are test-store setups. **Signed**, subject to B1-1. |

No chat schema, tool description or system prompt changed: `git diff e7f139b84..HEAD -- pypsa-gui/backend` touches no chat, schema or prompt file. After the smokes no uvicorn, vite, stub or chromium process is running, and ports 8000, 5173 and 11999 are free.

## Contract rows checked

| Item | Evidence | Status |
|---|---|---|
| B1: preparing hidden, `→` becomes "Working", `✓` becomes "Done", `✗` becomes "Could not" plus the message, raw text under Details | code `ChatPanel.tsx:473-537`, `:3222-3268`; unit tests; live probe | pass, but see **B1-1** for the wording of the start-tools |
| B1: phrase table and "use <words>" fallback | the eight entries match the spec; mutants F1 and F2 are killed | pass |
| B1: Expert shows the raw lines | snapshot, plus `13-p26-dc-10-expert.png` (raw `… preparing` / `→` / `✗` / `denied:` lines) | pass |
| B2: Results header | Guided reads `HUB DESIGN · RESULTS` / "Reliability results"; Expert reads `SIMULATION · RESULTS` / "Optimization results" (screenshots 11 and 13) | pass |
| B2: title, prose, class `Term`, "outage rate", no badge and the row `title`, `data-guided` | code, `FmeaTab.guided.test.tsx`, screenshot; mutants F7, F8, F9 and F16 are killed | pass (see S-1 for the editor below the table) |
| B2: 8 catalogue keys, at most 30 words, no jargon | `test_guides.py` green in row 2; `TERM_FALLBACK` is identical to the JSON | pass |
| B3: helper order | `worksheet.py:129-176` matches the spec's order exactly; mutants B1, B2, B3 and B10 are killed | pass |
| B3: engine paths | copt class A (`copt.py:1169`), copt merge (`:1286`), class-B sweep (`sweep.py:659`), class-C sweep (`stress.py:689`), zonal merge (`eh_stages.py:1053`); manual rows are `null` (`fmea.ts:134`) | pass (mutants B8, B9) |
| B3: `per_mode` forwarding | the spread at `copt_endpoint.py:86-90` is unchanged; the sweep rows' spread is at `:175`; mutant B4 is killed | pass |
| B3: FE text, Expert `title`, hub fallback | `fmea.ts:44-56`, `FmeaTab.tsx:309-310, 370-372`, `ResultsCard.tsx:92`; mutants F10–F13 are killed | pass (see S-2 on the wording) |
| Expert snapshot | `363942269`'s `.snap` + test copied into `qa29/wtbase` (`e7f139b84`) → `vitest run … --update=false` **1 passed**, so it was recorded on base code. `363942269..HEAD` changes only the `.snap`, and a character diff shows exactly two inserts: ` title="no shortfall — the site copes without it"` and ` title="not counted (outside the electricity metric)"`. The test file is unchanged. `Results`, `AppHeader` and `Sidebar` expertUnchanged are untouched. | pass |
| Exports | see N-4 | pass |

## `d95357335`: `dispatch_status` ignores transient rows

- **Correctness in the real contexts:**
  - `mark_transient` from the sweep thread resolves `_transient_target(ensure=True)`. That is the thread-local solving ctx, else `_ensure_active()`, which is the request ctx the runner copied with `contextvars.copy_context()` (`fmea_sweep_runner.py:112`).
  - `/status` resolves `_request_ctx.get()` for its own request.
  - Both are the same `ProjectContext` object.
- **Probe:** `qa29/probe_transient.py`. A worker in a copied request context marks `__voll_l` and adds it. A *separate* request context bound to the same ctx then reads **fresh**. With no request ctx the read is **stale**, so the check depends on the context. After the remove and unmark it reads fresh, and a real `g2` add reads **stale**.
- **Order:** marks happen *before* `n.add` (`assumptions.py:846-848`); unmarks happen *after* `n.remove` (`:966-969`), so no unmarked window exists.
- **No masking:** only names in the registry are ignored, on both sides. A real edit is still stale (test and probe). The only masking case is a user component that reuses a live `__voll_*` name during a solve. That is not reasonable to fear.
- **Mutants B5, B6 and B7:** all killed.

## Mutations

Scripts: `qa29/mutate.py` (one mutation at a time in `qa29/wt`, restored after each). Frontend suites: `src/components/ChatPanel`, `src/pages/results/FmeaTab`, `src/pages/results/fmea`, `src/pages/hubDesign/cards/ResultsCard`, `src/pages/Results.uiMode`. Backend suites: `test_fmea_zero_reason.py`, `test_dispatch_status_transient.py`, `test_energy_hub_common_mode.py`. Logs: `qa29/mut-fe.log`, `qa29/mut-be.log`.

| # | Mutation | Result |
|---|---|---|
| F1 | phrase fallback returns the raw tool id | killed |
| F2 | fallback keeps underscores | killed |
| F3 | Working→outcome rule removed | killed (3 tests) |
| F4 | settled set ignores `denied:` lines | **survived, equivalent**: a decline also gets the backend's `✗ X — confirmation_denied` line, which settles the call |
| F5 | settled set ignores `✗` lines | killed |
| F6 | any settled call hides every Working line (id ignored) | killed |
| F7 | class B label becomes "Generator outage" | killed |
| F8 | class A uses the class-C hover key | killed |
| F9 | class D label is the letter | killed |
| F10 | `ResultsCard` fallback is empty | killed |
| F11 | `ResultsCard` ignores `zero_reason` | killed |
| F12 | `mergeWorksheet` drops the key | killed |
| F13 | `no_shortfall` text changed | killed |
| F14 | the Guided cell shows the reason even when severity > 0 | **survived, equivalent**: the backend never sets a reason when severity > 0 |
| F15 | the error message line is dropped | killed |
| F16 | the Guided row title carries the engine id | killed |
| B1 | helper: `no_shortfall` before `no_outage_data` (the spec's target) | killed |
| B2 | helper: `out_of_scope` after `no_outage_data` | killed |
| B3 | helper: `no_shortfall` before `unpriced` | killed |
| B4 | `per_mode` drops `zero_reason` (the spec's target) | killed |
| B5 | `dispatch_status` transient filter off | killed |
| B6 | the default does not read the registry | killed |
| B7 | the filter applies to the static side only | killed |
| B8 | copt class A passes `in_metric_scope=False` | killed |
| B9 | the zonal merge does not re-derive the key | killed |
| B10 | the `unpriced` check is dropped | killed |

**24 of 26 killed; the two survivors are equivalent.** No mutant exercises B1-1's wording, because the tests pin the spec's phrase.

## Screenshots read
- `qa29/smoke29/11-p29-dc-fmea-guided.png`: Guided with "HUB DESIGN · RESULTS" / "Reliability results".
  - The title and prose are as specified.
  - The Kind column reads Link outage / Generator outage, with occurrence "outage rate" and `Term` hovers.
  - `genset_1` and `genset_2` read "no shortfall — the site copes without it"; "Yearly risk" is the last column.
  - The editor below still reads "STRESS SCENARIOS (CLASS C)" (S-1), and the panel breadcrumb reads "SIMULATION / Results" (N-3).
- `qa29/smoke29/13-p26-dc-10-expert.png`: Expert shows "Optimization results", "FMEA worksheet", the class letters, `copt` / `lp_proxy` badges and the raw chat lines. It is unchanged.
- `qa29/smoke29/15-p26-dc-12-guided-reloaded.png`: Guided after a reload; the Hub design page is loading and the chat shows the greeting.
- `qa29/probe/14-qa29-dc-c-card-pending.png`: "Done: list what is in the network", "Could not: read the study results" with the raw message (N-2), and the confirmation card with no "Working" row.
- `qa29/probe/15-qa29-dc-d-sweep-done-label.png`: "Done: check what happens when equipment fails" while the sweep is running (**B1-1**).

---

## Re-gate (2026-09-30, HEAD `c066bb7fa`)

**Scope:** `git diff 235c15fdd..c066bb7fa`. There are three commits:
- `4b2f17c60` fixes B1-1;
- `5fcd1fd6a` fixes S-1 and S-2;
- `c066bb7fa` is the phase note.

All three are frontend and docs only. `git diff a994da65d..c066bb7fa -- pypsa-gui/backend` is empty, so rows 1 and 2 above still stand. The scratch area is `qa29/rg/`. Mutations ran in a scratch worktree at `c066bb7fa`. The live probe ran in a second worktree, using a scratch-only copy of the smoke. Both worktrees are removed.

### Verdict: **GO**

B1-1 is fixed and I checked the fix live. S-1 is fixed, and Expert is still byte-identical. S-2 is fixed for the two cases it was about: the rate is missing, or the rate is 0. One residual case of the S-2 text is still false (R-1 below). It is a should-fix and does not block.

### B1-1: fixed
- **Live check.** I re-ran the probe (`qa29/rg/probe.log` lines 93-103, screenshot `qa29/rg/probe/12-qa29-dc-sweep-started-label.png`). I approved `run_fmea_sweep`. The line then read **"Done: start the equipment-failure check"** while `GET /api/results/fmea_sweep` returned `running`. The line is now true. The probe smoke passed, with 40 screenshots.
- **Which tools start work in the background.** I worked this out from `chat_tools_schema.TOOLS` itself, not from the implementer's test. I scanned all 155 descriptions for "Start…", "returns {status:'running'}", "worker thread", "Long-running" and "queue". The tools that start work and return are:

  | Tool | Why it counts |
  |---|---|
  | `run_simulation` | description says "Start" |
  | `run_fmea_sweep` | description says "Start" |
  | `run_frontier_study` | description says "Start" |
  | `run_mc_study` | description says "Start" |
  | `run_coupling_loop` | description says "Start" |
  | `run_margin_loop` | description says "Start" |
  | `run_eh_study` | description says "Start" |
  | `run_ac_pf_stage` | description says "Long-running" |
  | `solve_queue_enqueue` | background queue |
  | `gridspine_run_pipeline` | runs "through the solve queue" |

  Every one of them now has its own phrase. The eight "start" tools read "start …", `solve_queue_enqueue` reads "add the project to the solve queue", and `abort_adequacy_study` reads "ask the running study to stop". None falls back to "use <tool words>".
- **Tools I checked and left alone.** `start_campaign` ("Open a reliability CAMPAIGN") only opens a budget and computes nothing, so its fallback "use start campaign" is not a false claim. `solve_queue_abort` and `solve_queue_list` are not start tools.
- **Wording audit of all 19 entries.** Every entry is true and in plain words.
  - `run_coupling_loop`, "start the planning loop that tightens the shortfall limit": the loop adjusts the ENS cap until LOLE meets the target. In practice that means tightening, so this is acceptable. "…that adjusts the shortfall limit" would be exact. This is a note only.
  - `run_margin_loop`, "start the planning loop that adds backup capacity": the loop raises the reserve margin, and the plan builds more firm capacity. True.
  - `solve_queue_enqueue` keeps "solve queue". That is a GUI term, not an engine id. Acceptable.

### S-1: fixed
- **Guided.** The stress editor heading reads "Stress scenarios · 2/10". The add-row placeholders read "cost per event (€)" and "notes (optional)". See the probe screenshot above.
- **Note.** The placeholder "cost per event (€)" is cut to "cost per ever" in the `w-28` input. This is cosmetic.
- **Expert.**
  - `FmeaTab.expertUnchanged.test.tsx.snap` is unchanged since `f66fc1165`: `git diff --stat f66fc1165..HEAD` over snapshots and `*expertUnchanged*` is empty.
  - The Expert heading "Stress scenarios (class C)" is pinned by the new `StressScenarioEditor.test.tsx` case.
  - The Expert placeholders are pinned by that snapshot too. My mutant G10 also broke `renders the base header and worksheet`.

### S-2: fixed for the two cases asked about
The text is now "no outage rate set (or it is zero)". It is true when the rate is missing, and true when the rate is 0 (the `grid` case in my S-2 repro).

### R-1 (should-fix, new residual of S-2): the text is false when a rate is set but the repair time is missing or 0
- **Where:** `fmea.ts:49` text, for `no_outage_data`. The helper at `worksheet.py:161-167` returns that reason for any `occurrence_per_year <= 0`. `copt.py:1147-1148` sets `occ = 0` whenever `mttr_hours` is NaN or ≤ 0, even when the rate is set.
- **Repro (inline, printed by the probe):**
  - Setup: carrier `mystery`, which has no library default; `g1` has `outage_rate_value=0.05`, `outage_rate_basis="FOR"` and no `mttr_hours`.
  - Result: `g1 occ=0.0 severity=0.0 criticality=24000.0 zero_reason=no_outage_data rate_source=asset`.
  - The Guided cell would say "no outage rate set (or it is zero)" next to the user's 5 % rate. It would also show a €24.0 k yearly risk.
- **Reach:** the spec's own mutation-target case, `mttr_hours = 0` in `test_fmea_zero_reason.py::test_copt_a_unit_with_mttr_zero_has_no_outage_data`, is this case too. `validate_outage_params` warns about it, so it is an unusual input. That is why this does not block.
- **Fix (one string plus its three test constants):** e.g. "no outage frequency (outage rate or repair time missing or zero)". Alternatively, split the reason on `rate_source` and the MTTR in a later phase.

### Rows

| Row | Command (cwd) | Result |
|---|---|---|
| 1, 2 | unchanged: no backend diff since `a994da65d` | stand (6716 + 31 at HEAD; 776 passed, 17 skipped) |
| 3 | `npx tsc --noEmit -p .` (`pypsa-gui/frontend`) | exit 0, 0 errors (`qa29/rg/tsc.log`) |
| 4 | `npx vitest run` (`pypsa-gui/frontend`) | **244 files / 2793 tests passed** (`qa29/rg/vitest.log`) |
| 4s | 3× `npx vitest run src/components/ChatPanel src/pages/results/FmeaTab src/pages/results/StressScenarioEditor src/pages/hubDesign` | **3/3 green**, 615 tests each (`qa29/rg/stress-{1,2,3}.log`) |
| 5 | `PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers node scripts/smoke-guided.mjs --phase P29 --out qa29/rg/smoke29` (`pypsa-gui/frontend`, run alone) | **PASS**, 39 screenshots (`qa29/rg/smoke29.log`) |
| 5+ | live B1-1 probe (`qa29/rg/probe.log`) | PASS, 40 screenshots; see above |
| 7 | the new product `uiMode` read is the `guided` flag in `StressScenarioEditor`, and the `FmeaTab` placeholders use the existing flag | Expert arms equal to base; signed |

After the runs no uvicorn, vite, stub or chromium process is running, and the working tree is clean apart from this file.

### Mutations (`qa29/rg/mutate.py`, log `qa29/rg/mut.log`)

| # | Mutation | Result |
|---|---|---|
| G1 | old `run_fmea_sweep` phrase restored | killed (3 tests) |
| G2 | old `run_eh_study` phrase restored | killed |
| G3 | `run_margin_loop` entry dropped | killed |
| G4 | `run_ac_pf_stage` entry dropped (not caught by the schema "Start" scan) | killed (the test's explicit list) |
| G5 | `gridspine_run_pipeline` entry dropped | killed |
| G6 | `solve_queue_enqueue` entry dropped | killed |
| G7 | `run_simulation` reads "run a solve" | killed |
| G8 | S-1 heading: Guided and Expert branches swapped | killed |
| G9 | S-1 heading: always plain (Expert changes) | killed |
| G10 | S-1 severity placeholder: branches swapped | killed (the Guided test and the Expert snapshot) |
| G11 | S-1 old "mitigability" placeholder in Guided | killed |
| G12 | S-2 old text "no outage data" | killed |

**12 of 12 killed.**

### R-1 resolution (coordinator, 2026-09-30)
The `no_outage_data` text now reads "no outage frequency (outage rate or repair time missing or zero)", which is true for a missing rate, a zero rate, and a missing or zero MTTR. Also addressed from the notes: the Guided severity placeholder is shortened to "€ per event" (the old text was cut off), and `run_coupling_loop` reads "…adjusts the shortfall limit". tsc is clean; the results and ChatPanel suites pass (1109 tests).
