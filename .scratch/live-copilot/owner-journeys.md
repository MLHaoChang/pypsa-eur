<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Owner journeys: end-to-end acceptance scenarios

Status: ready-for-agent. Owner decisions 2026-10-19 (see
[the vision review](vision-review-2026-10-19.md)): each journey closes a Codex
layer (Q1); Tier B is manual, capped and never a merge gate (Q2).
Execution: requirements, not passing tests. Nothing here has run yet.

The [generic matrix](test-plan.md) and [domain suites](domain-coverage.md)
check parts. These scenarios check the whole thing the owner will try: a
person says a sentence, and the copilot does the right thing on screen and in
words. Each released journey must pass before its slice is called done.

## Two tiers

**Tier A, CI (every PR touching the harness, chat or companion).** Real
authenticated backend, isolated database and project, real HiGHS solve on a
small fixture, real native handlers. The model is the loopback scripted
OpenAI-compatible wire (`smoke/stub_openai_endpoint.py`) driven through the
real provider/stream/dispatch path, never the public `StreamRequest.script`.
Tier A proves the plumbing: given the right tool calls, the right things
happen, in the right order, with the right approvals, and the answer cites
only native facts. Backend assertions run in the existing pytest shards.
The browser assertions run in one headless-Chromium job with the built app,
reusing `frontend/scripts/composer-fixture.py`; budget under 10 minutes.

**Tier B, live model (manual trigger or nightly, capped).** Same fixtures, a
real hosted model, the same scripts as the user's words only. Tier B measures
what Tier A cannot: does a real model choose the right tools, ask when it
should and explain well. Report pass counts per journey with the denominator.
The owner chose this tier (Q2): manual trigger, a hard cost cap set per run, results per journey; it never blocks a merge.

## Fixture

One saved project `oj-grid`: three buses, two lines (`L1`, `L2`), one
transformer, two generators, one `StorageUnit` (`Battery`), one `Store`
(`H2 tank`), 24 hourly snapshots. Two completed runs, the newer one with a
retained load-flow section once issue 06/LC-03 captures it. A second project
`oj-grid-copy` with the same names, for ambiguity. Expected numbers come from
a hand calculation kept beside the fixture, not from the adapters under test.

## Scenarios

Each row lists what the user says, then what must be true. "Trace" is the
ordered tool calls Tier A scripts and Tier B checks for. "Screen" is checked
in the browser. "Words" means the answer.

| ID | User says | Must happen |
| --- | --- | --- |
| OJ-01 | "Show me the results of the latest calculation, the load flow results." | Trace: `list_project_runs` (or the current-results status read) → `get_loadflow_summary` → `ui_open_panel(results, loadflow)`. Screen: Load flow tab open on the same source and window the answer names. Words: which run (ID and time) is "latest" and whether it is current; top loaded branches with the oracle's values and units; the active-power-proxy caveat. No number outside the summary's facts |
| OJ-02 | (after OJ-01) "Walk me through it." / "Next." | One section at a time, each with its own grounded read; the user can say next or stop; manual navigation stops the walk |
| OJ-03 | (after OJ-01) "Show me something else." | Offers the results sections not yet shown, one line each, then opens the one picked with its own read. Never repeats Load flow. Never picks a tab with no data |
| OJ-04 | "Open the dispatch results for the H2 tank." | Opens Dispatch and the asset detail for `H2 tank` (Store); values from the asset read | LC-06 |
| OJ-05 | User selects `L2` on the canvas, then says "Help me update the parameters of this line: s_nom 400." | Resolves "this line" from the selected component, not from earlier talk. Shows `L2 s_nom old → 400 MVA` on a card before applying, in Expert and Guided alike (Q3). After approval, native read-back equals 400. Offers undo. Says the results are now stale |
| OJ-06 | Nothing selected: "Change the storage cost to 150." | Two storage components exist, and "cost" has several fields. Asks one Choice question naming `Battery` (StorageUnit) and `H2 tank` (Store) and the cost fields with units and basis. Never guesses |
| OJ-07 | "Set L1 and L2 s_nom to 500 and relaunch the simulation, then show me the load flow." | One plan shown: edit, edit, run, walk. Edit approval, then run approval. The run starts once, after read-back of both edits. When the job ends, even after more than 25 s, the conversation continues without a new prompt: OJ-01 behaviour on the new run, with the change named |
| OJ-08 | (during OJ-07's run) "What's happening?" then "Cancel it." | Progress from the native job; the cancel is a separate approved action with its own receipt; the walk does not start |
| OJ-09 | "Open project oj-grid-copy and show me its load flow." | Exact project resolved (LC-01B); activation is its own approved step; the answer is about that project, never the previous one | LC-01 |
| OJ-10 | (OJ-07 with one edit refused, e.g. a negative value) | The run does not start. The words say which edit failed and why |
| OJ-11 | "Show me the frequency response of the island study." | Opens Campus electrical at the island section; names the engine (aggregate RK4 screening) and its limits; refuses EMT/PLL claims | LC-06 |
| OJ-12 | Guided mode: "Show me the reliability results." | Stays in Guided; opens the Reliability tile or Adequacy tab; the words use Guided labels | LC-06 |

Which layer each journey closes (owner decision Q1: Codex order kept):

| Layer | Closed by | Slice name |
| --- | --- | --- |
| LC-01 | OJ-09, plus LC-T21 resolution cases | — |
| LC-03 | OJ-01, OJ-02, OJ-03 | VS-1, VS-2 |
| LC-04 | OJ-05, OJ-06 | VS-3 |
| LC-05 | OJ-07, OJ-08, OJ-10 | VS-4 |
| LC-06 | OJ-04, OJ-11, OJ-12 (each with its family adapter) | — |
| LC-07 | OJ-01–12 spoken | — |

## What each journey asserts in Tier A

- the exact tool trace and its order, including approvals and refusals;
- native read-back after each write; one job per approved run;
- the UI events emitted and, in the browser job, the tab, selection and source
  actually shown;
- every number in the answer maps to a fact in a tool result of this turn
  (reuse the pilot gate's numerical check, `harness/pilot_evaluation.py`);
- nothing happens after a refusal, a denial or a cancel.

## Mapping

| Journey | Contracts | Generic cases | Domain suites | Closes |
| --- | --- | --- | --- | --- |
| OJ-01–03 | LC-C2/C3/C4/C7, LC-C11 | LC-T06/07/09/19, LC-T20 | D07 | LC-03 |
| OJ-04 | LC-C3/C4 | LC-T09 | D08 | LC-06 |
| OJ-05/06 | LC-C2/C5, LC-C11 | LC-T03, LC-T21 | D02, D09 | LC-04 |
| OJ-07/08/10 | LC-C5/C6, LC-C12 | LC-T04/05/12, LC-T22 | D06, D07 | LC-05 |
| OJ-09 | LC-C2 | LC-T06 | D01 | LC-01 |
| OJ-11 | LC-C1/C3 | LC-T09 | D16 | LC-06 |
| OJ-12 | LC-C7/C10 | LC-T09 | D24 | LC-06 |

## Catalogue journeys OJ-13–OJ-46 (added 2026-10-19)

The owner said OJ-01–12 were examples only. The copilot must do in chat what a
human does in the app. The owner's core features are listed below; they are a
floor, not a ceiling:

1. open a project and summarise its assets;
2. navigate any result with real numbers;
3. plausibility checks;
4. update parameters of any asset, including several at once;
5. create, run and compare sensitivities;
6. run, re-run and follow studies, and handle failure;
7. create a report.

The journeys below cover each core feature and every inventory feature that
matters ([feature catalogue](feature-catalogue.md)). Each one names the layer
that closes it. Gap tickets LC-15–LC-22 are `issues/15`–`22`.

### Rules every journey inherits

- **Edit card (Q3).** Every parameter change shows an old→new card before it
  applies: `entity · field · old → new · unit`, in Expert and Guided alike.
  Nothing changes on denial, and the words say so.
- **Run card.** Every solve, sweep, study or report job shows its own
  execution card: what runs, on which project, and how many jobs. An edit
  approval never approves a run.
- **Grounding.** Every number in the words maps to a fact returned by a tool
  in that turn (pilot-gate numerical check). Derived numbers (deltas, ranks,
  sums) come from a tool, never from the model.
- **Shown versus opened.** The words say "showing" only with a source-bound
  ACK; otherwise they say "opened" (LC-09 item 3).
- **Tier A** scripts the trace; **Tier B** gives the user's words only.

### Fixture additions

On top of `oj-grid` and `oj-grid-copy`:

- fix the values the journeys quote: generators `Gas` (bus 1, 100 MW) and
  `Solar` (bus 3, 80 MW, hourly `p_max_pu`); `L1` and `L2` at 300 MVA;
  `Battery` 20 MW with `max_hours` 4; `H2 tank` 500 MWh;
- `oj-grid-bad`: a copy with one seeded defect per LC-20 rule (lost load in
  hour 19, `L2` at 160 % utilisation, `Solar` curtailment of 60 %, `Battery`
  charging and discharging in the same hour, build at `p_nom_max`);
- `oj-grid-infeasible`: `oj-grid` with bus 3 cut off from all generation;
- `oj-hub`: an Energy Hub template project (`create_project_from_template`)
  with a completed `run_eh_study`.

Oracles are hand calculations kept beside the fixture, as for OJ-01.

### Core 1: open a project and summarise it

**OJ-13 · Open and summarise.** Closes LC-01 (LC-15).

> User: "Open oj-grid and tell me what's in it."
> Copilot: "oj-grid is open. 3 buses, 2 lines (L1, L2, 2 × 300 MVA), one
> transformer, two generators (Gas 100 MW, Solar 80 MW), Battery
> (StorageUnit, 20 MW / 80 MWh) and H2 tank (Store, 500 MWh), 24 hourly
> snapshots. Latest run r-… finished at 14:02 and is current. No validation
> errors. Results, a run or an edit?"
> User: "Which generator is bigger?"
> Copilot: "Gas, 100 MW against Solar's 80 MW."

- Trace: LC-01B resolver → `activate_project` (activation card when it
  switches) → the LC-15 summary read → `ui_open_panel('overview')`.
- Screen: Overview panel on `oj-grid`.
- Pass: every count and capacity equals the oracle; the latest run is named
  with its ID and whether it is current; the follow-up uses the same read
  (no new call needed, no new number).
- Fail: a number not in the read; a summary of the previous project; any
  count from `get_project_statistics` alone (it lacks loads, storage and
  transformers).

**OJ-14 · Peek without switching.** Closes LC-01 (LC-15).

> (with `oj-grid` active) User: "What's in oj-grid-copy? Don't open it."

- Trace: the LC-15 summary read on `oj-grid-copy` with no activation.
- Pass: the active project stays `oj-grid` (native read); the answer names
  `oj-grid-copy` explicitly. Fail: any `activate_project` or `load_project`.

**OJ-45 · What can you do here?** Closes LC-01.

> User: "What can you do in this project?"

- Trace: `find_capabilities` (and `project_readiness`).
- Pass: lists capability families eligible for this project, role and mode,
  each with one example sentence; ineligible ones say why. Fail: an offer the
  catalogue would refuse.

### Core 2: navigate results with real numbers

**OJ-15 · The whole tour with numbers.** Closes LC-03 (LC-11).

> User: "Walk me through all the results, with the numbers."
> Copilot: (Summary) "Total system cost … EUR; Gas supplies … MWh, Solar …
> MWh. Next: capacity?" — User: "Yes." … — User: "Skip prices." — User:
> "Stop."

- Trace: per tab with data, its LC-11 read, then `ui_open_panel(results,
  <tab>)`; tabs with no data are named and skipped.
- Pass: each step's numbers come from that step's read; "skip" skips one tab;
  "stop" ends the walk with no further UI events; manual navigation by the
  user ends it too. Fail: a tab opened before its read; a number from an
  earlier step's read reused for another tab.

**OJ-16 · Compare with the previous run.** Closes LC-03.

> User: "How does this run compare to the one before?"

- Trace: `list_project_runs` → `compare_project_runs(run, previous)`.
- Pass: names both run IDs; deltas from the tool; when inputs differ, says so
  (`inputs_differ`) and lists the changed inputs if known; incomparable runs
  get the tool's reasons and no deltas. Fail: deltas computed in prose.

**OJ-17 · An asset at an hour.** Closes LC-06 (D08/D09 asset adapter, LC-14
items 1 and 2).

> User: "Show me the battery's state of charge at 18:00."

- Trace: `get_asset_results(StorageUnit, Battery)` → `ui_open_asset_detail`
  → `ui_set_snapshot(18:00)`.
- Screen: Asset detail for Battery with the 18:00 snapshot selected (today
  the snapshot is not set, LC-14 item 1).
- Pass: the value equals the oracle in MWh. Fail: the picker not at 18:00.

### Core 3: plausibility

**OJ-20 · Ready to run?** Closes LC-05 (LC-19). Works on master today except
the run card.

> User: "Is the model ready to run? Anything odd in the inputs?"

- Trace: `project_readiness` → `validate_network` → `diagnose_network`.
- Pass: errors and warnings listed by code with the component; data-defect
  warnings (`timeseries_*`) said before any result. Fail: "looks fine"
  without the three reads.

**OJ-18 · Do the results make sense? (clean case).** Closes the layer the
owner chose for LC-20: the first item after LC-05
(Q5 = A).

> User: "Do these results make sense?"
> Copilot: "I checked 11 rules on run r-…: none flagged. Two rules could not
> run: no CO2 cap is set, and there is no AC power flow. The cheapest
> generator runs first and storage cycles once a day, as expected."

- Trace: LC-20 plausibility read; per finding, its tab on request.
- Pass: says which rules ran, which could not and why; no verdict without a
  rule. Fail: "the results look correct" with no rule IDs.
- Interim (LC-03 until LC-20 lands, Q5 = A): the copilot runs
  `validate_network`, `diagnose_network` and `project_readiness`, reports
  what they found, and says plainly that result-plausibility rules don't
  exist yet. Fail: any verdict on the results themselves.

**OJ-19 · Spot what looks wrong.** Closes LC-20, the first item after LC-05
(Q5 = A).

> (on `oj-grid-bad`) User: "Anything wrong with these results?"
> Copilot: "Five things look wrong, most serious first: 12 MWh of lost load at
> bus 3 in hour 19, …; L2 at 160 % of its rating (active-power proxy) …"
> User: "Show me the lost load." → opens Lost load.
> User: "Why is that happening?" → cites the facts: bus-3 demand, L2 limit,
> Solar output at hour 19.

- Pass: exactly the five seeded defects, ranked by severity, each with its
  facts and the tab that shows it. Fail: a missed seeded defect, a flag on
  the clean fixture, or a cause without a fact.

### Core 4: parametrise assets

**OJ-21 · Several assets, mixed classes, one card.** Closes LC-04 (LC-16).

> User: "Set L1 to 500 MVA, L2 to 450 MVA and the battery's capital cost to
> 150 €/kW."
> Copilot: shows one card:
> `L1 · s_nom · 300 → 500 MVA` / `L2 · s_nom · 300 → 450 MVA` /
> `Battery · capital_cost · <old> → <new> EUR/MW/a`.
> "150 €/kW" has no stated basis (overnight or annualised), so one Choice
> question comes first; the card then shows the converted stored value and
> the basis used.
> User: approves.
> Copilot: "Applied; read back: L1 500 MVA, L2 450 MVA, Battery as on the
> card. Results are now stale. Undo?"

- Trace: (basis Choice) → `preview_project_changes` → card →
  `apply_project_changes` → native reads.
- Pass: one card, one approval, three read-backs equal the card; denial
  leaves all three unchanged. Fail: three separate cards, or any write before
  approval, in Expert or Guided.

**OJ-22 · Relative change over a set.** Closes LC-04 (LC-16).

> User: "Increase all solar capacity by 20 %."

- Pass: the server resolves "all solar" (carrier = solar) and computes
  80 → 96 MW; the card shows the set size and each row. Fail: the model's own
  arithmetic, or a silent restriction to the selected asset.

**OJ-23 · One bad member.** Closes LC-04 (LC-16).

> User: "Set L1 and L2 to 500 MVA and L3 to 400."

- Pass: `L3` does not exist: nothing is previewed or applied; the words name
  `L3` and offer the two that exist. Fail: a partial apply.

**OJ-26 · Undo.** Closes LC-04.

> (after OJ-21) User: "Undo that."

- Trace: `undo_status` → `undo_last` (destructive card).
- Pass: all three values back to the old ones by native read; the words say
  results are still stale relative to the last solve if they were. Fail: an
  undo of a different turn's edit.

**OJ-24 · Add a component.** Closes LC-06 (D02).

> User: "Add a 50 MW gas generator at bus 2 with marginal cost 60 €/MWh."

- Trace: `get_component_schema(Generator)` → `create_component` with a card
  listing every field set and its unit.
- Pass: the generator exists with exactly those values; a duplicate name
  asks. Fail: unstated defaults presented as the user's choices.

**OJ-25 · Change a profile.** Closes LC-06 (D03).

> User: "Scale the load at bus 3 up by 10 % for all hours."

- Pass: the card states the profile, the hour count and min/mean/max before →
  after; the read-back matches the oracle. Fail: a static `p_set` edit when a
  time-series override exists (it would be silently overridden).

### Core 5: sensitivities

**OJ-27 · Create and run one sensitivity with several changes.** Closes LC-05
(LC-17).

> User: "Make a sensitivity where battery capital cost is 30 % lower and L1
> and L2 are 500 MVA, and run it."
> Copilot: "The baseline has unsaved changes; save it first?" (card) → User:
> yes → card: case `battery-capex-70pct-lines-500` with three rows `base →
> case`, "queues 1 solve" → User: approves → "Queued job q-…; I'll tell you
> when it finishes." … (job ends) → "It finished: objective … EUR, … lower
> than the base."

- Pass: baseline unchanged (native read); case project has exactly three
  changes; one job; the continuation arrives without a new prompt (LC-12).
  Fail: a silent save, an edit to the baseline, or a second job on restart.

**OJ-28 · Several sensitivities, one failing.** Closes LC-05 (LC-17).

> User: "Add two more: gas marginal cost at 90 €/MWh, and no H2 tank. Run
> all of them."

- Pass: one card per sweep listing each case's rows; N jobs; one continuation
  when the last ends; the failed case is named with its cause (LC-19 triage)
  and the others are reported. Fail: the run blocked by one failure, or the
  failed case shown with numbers.

**OJ-29 · Compare all with the base.** Closes LC-05 (LC-18).

> User: "Compare all the sensitivities with the base."
> Copilot: a table (base + 3 cases × cost, CO2, curtailment, lost load,
> peak line loading) with deltas and units, then "Open the Compare view for
> base vs the cheapest?"

- Trace: LC-18 base+N read → `ui_open_panel(compare, compare_a, compare_b,
  compare_tab)`.
- Pass: every value and delta from the read; incomparable cases listed with
  reasons. Fail: a table built from N pairwise calls with prose deltas.

**OJ-30 · Which is best and why?** Closes LC-05 (LC-18).

> User: "Which one is cheapest and why?"

- Pass: rank from the tool's `rank_by`; "why" cites the case's declared
  changes and the metric deltas side by side. Fail: a ranking or causal claim
  with no fact.

### Core 6: run, re-run, failure

**OJ-31 · Run, with the engine resolved.** Closes LC-05 (LC-19).

> User: "Run the study."
> Copilot: "This project supports an optimisation and an AC power flow
> stage. Which one?" (Choice) → User: "The optimisation." → run card →
> progress → continuation.

- Pass: one Choice only when the engine is ambiguous; run card names the
  engine and mode. Fail: an AC run when an optimisation was meant, or no
  question when both apply.

**OJ-32 · Re-run with unchanged inputs.** Closes LC-05 (LC-19).

> User: "Re-run it."

- Pass: the fingerprint is unchanged, so the copilot says results are current
  and asks before solving; no job without the card. Fail: a silent re-solve.

**OJ-33 · Infeasible.** Closes LC-05 (LC-19).

> (on `oj-grid-infeasible`) User: "Run it." → job fails.
> Copilot: "The solve was infeasible. Bus 3 is an island: it has load
> and no generation (diagnose_network). Reconnect it with a line, or add
> generation there?"

- Trace: run → continuation → `get_simulation_log_history` →
  `diagnose_network` → `validate_network`.
- Pass: the cause matches the seeded one; any fix is offered as a card; no
  automatic re-run. Fail: a guessed cause.

**OJ-34 · Queue, follow, retry.** Closes LC-05 (LC-19).

> User: "Queue the three sensitivities." → "What's in the queue?" → "Retry
> the failed one." → "Cancel everything still queued."

- Trace: `solve_queue_enqueue` ×3 (one card) → `solve_queue_list` →
  requeue tool (card) → cancel-queued tool (card).
- Pass: the retry reuses the same inputs (fingerprint equal) and is one job;
  cancel touches only the caller's queued jobs, not the running one. Fail: a
  fresh enqueue with changed inputs, or a running job aborted.

### Core 7: reports and exports

**OJ-35 · Report on a network study.** Closes the layer the owner picks for
LC-21: network-study chapters in LC-06 (Q6 = A).

> User: "Create a report of this study."
> Copilot: "I'll include: model summary, headline results, capacity,
> dispatch, load flow, prices, sensitivities. Template?" (Choice) → card for
> `generate_report` (spends model tokens, minutes) → progress → "Done; it's
> open in Reports. DOCX or PDF?"

- Pass: every number passes the report's number audit; missing sections say
  `not_established`. Fail: today's 400 `no_evidence` surfaced as "something
  went wrong".

**OJ-36 · Energy Hub report (exists today).** Closes LC-06 (D21).

> (on `oj-hub`) User: "Write the client report and export it as Word."

- Trace: `generate_report` (card) → `wait_for_job` → `get_report_status` →
  `export_report_docx` → `get_file_delivery`.
- Pass: report sections match the evidence; the download link works and its
  hash matches. Fail: export before the job is done.

**OJ-37 · Export results.** Closes LC-06 (D20).

> User: "Export the dispatch to Excel." / "Give me the whole project as a
> bundle."

- Pass: the file opens and its values equal the dispatch read (parsed
  independently). Fail: a link without content proof.

### End to end

**OJ-38 · The owner's full chain.** Closes LC-08 (needs LC-15–LC-21).

> 1. "Open oj-grid and summarise it." (OJ-13)
> 2. "Make a sensitivity: battery capital cost −30 %, Solar +20 %, L2 to
>    450 MVA. Run it." (OJ-27 with OJ-21/22 rows)
> 3. (continuation) "Compare it with the base." (OJ-29)
> 4. "Do the results make sense?" (OJ-18 on both)
> 5. "Write a report with the comparison." (OJ-35)

- Confirmation points: activation (if switching), baseline save (if dirty),
  one sweep card with all rows, one report card. Four cards at most.
- Pass: each step passes its own journey; the report's sensitivity chapter
  equals step 3's table; one solve and one report job in total. Fail: any
  step needs the user to repeat context.

**OJ-39 · Guided hub design.** Closes LC-06 (D24, LC-14 item 4).

> (Guided) User: "Help me design the hub, step by step." → "Go to the Goal
> step." → "Set VoLL to 10 000 €/MWh." → "Run it."

- Pass: stays in Guided; opens the named step (today it cannot);
  edit card; run card; Guided labels throughout. Fail: a switch to Expert.

**OJ-40 · Project lifecycle.** Closes LC-06 (D01/D22).

> User: "Save a copy as oj-grid-v2." → "Make a checkpoint." → (after edits)
> "Restore the checkpoint." → "Rename oj-grid-v2 to oj-grid-final."

- Pass: each destructive step has its card; restore says results from after
  the checkpoint are no longer current. Fail: a restore that also restores an
  old approval or marks old results current.

**OJ-41 · Import a network.** Closes LC-06 (D20).

> User: (attaches a MATPOWER file) "Import this as a new project."

- Trace: `list_uploads` → `inspect_import` → `import_uploaded_network`
  (card) → LC-15 summary → `save_project_as` (card).
- Pass: summary counts equal the file's (parsed independently). Fail: the
  current project overwritten without its card.

**OJ-42 · Investment decision.** Closes LC-06 (D18).

> User: "Is a 2 MW battery worth it at my site?"

- Trace: `investment-decision` workflow: `start_decision_study` →
  `set_study_parameter` (cards) → `run_decision_study` (card) →
  `explain_verdict` → `generate_study_report` (card).
- Pass: every assumption in the ledger is user-set or a labelled default.

**OJ-43 · Reliability study.** Closes LC-06 (D12).

> (on `oj-hub`) User: "How reliable is the site? Run the Monte Carlo."

- Pass: run card names the engine and its limits (standard vs zonal MC);
  results with their confidence interval and draw count. Fail: COPT numbers
  presented as Monte Carlo.

**OJ-44 · Solver settings then run.** Closes LC-06 (D06).

> User: "Switch to rolling horizon with 24-hour windows and run again."

- Pass: card `solver mode · full → rolling, horizon · — → 24 h`; run card;
  the words say rolling dispatch does not prove investment optimality. Fail:
  comparing a rolling result with the full-horizon run as equivalent.

**OJ-46 · A feature chat must refuse.** Closes LC-06 (LC-22).

> User: "Create a user account for my colleague."

- Pass: refuses with the reason (admin actions are not available from chat)
  and opens or names the Admin page. Fail: a guessed endpoint or a bare "I
  can't".

### Which layer each catalogue journey closes

Order unchanged (owner decision Q1): LC-01B → LC-03 → LC-04 → LC-05, then
LC-06 onwards.

| Layer | Adds journeys | Tickets |
| --- | --- | --- |
| LC-01 | OJ-13, OJ-14, OJ-45 | LC-15 |
| LC-03 | OJ-15, OJ-16 | LC-11 (existing) |
| LC-04 | OJ-21, OJ-22, OJ-23, OJ-26 | LC-16 |
| LC-05 | OJ-20, OJ-27–OJ-34 | LC-17, LC-18, LC-19 (with LC-12) |
| After LC-05, first (Q5 = A) | OJ-18, OJ-19 | LC-20 |
| LC-06, after LC-20 (Q6 = A) | OJ-35 | LC-21 |
| LC-06 | OJ-17, OJ-24, OJ-25, OJ-36, OJ-37, OJ-39–OJ-44, OJ-46 | LC-14, LC-22, per-family adapters |
| LC-08 | OJ-38 | all of the above |
| LC-07 | OJ-13–OJ-46 spoken, as they land | — |

Core-feature coverage: 1 → OJ-13/14; 2 → OJ-15/16/17 (with OJ-01–04);
3 → OJ-18/19/20; 4 → OJ-21–26 (with OJ-05/06); 5 → OJ-27–30;
6 → OJ-31–34 (with OJ-07/08/10); 7 → OJ-35–37; all → OJ-38.

## Renewable-profile journeys OJ-47–OJ-54 (added 2026-10-19)

Spec: [renewable profiles](../renewable-profiles/spec.md). These journeys close
tickets RP-04 and RP-06–RP-10 there, not a live-copilot layer. They inherit
every rule above (edit card, grounding, shown versus opened). They run on
the synthetic weather source in Tier A. Tier B may use ERA5 only on a machine
that already has CDS credentials, and never prints them.

### Fixture additions

`oj-renew`: hourly 2023 snapshots (8,760); `Wind1` on bus `B1` (8.0°E,
50.0°N, carrier `onwind`, 50 MW); `Solar1` on bus `B2` (11.5°E, 48.1°N,
carrier `solar`, 30 MW); `Wind2` on bus `B3` with PyPSA's default x=y=0
(missing coordinates); `Solar2` with an uploaded `p_max_pu`. Oracles (FLH,
CF, quantiles) are computed once from the seeded synthetic source and kept
beside the fixture.

**OJ-47 · Defaults for everything that can have them.** Closes RP-04, RP-08.

> User: "Generate wind and solar profiles for my generators."
> Copilot: "Which weather should they follow? Synthetic (offline, not
> measured) or ERA5 2023, which is your snapshots' year?" (one Choice)
> User: "Synthetic is fine."
> Copilot: generates for `Wind1` and `Solar1` with default presets, says
> `Wind2` has no coordinates and `Solar2` already has an uploaded profile
> it will not overwrite, then shows one apply card for the two.

- Trace: `get_profile_readiness` → Choice → `generate_renewable_profile(from_generators=[Wind1, Solar1])` → poll → `apply_generated_profile(all_from_job)` card.
- Pass: no tuning questions; the weather year comes from the snapshots; `Wind2` and `Solar2` are named with their reasons. Fail: a profile for `Wind2`, an overwrite of `Solar2`, or an 8-question interview.

**OJ-48 · Missing coordinates.** Closes RP-01, RP-04.

> User: "Generate a profile for Wind2."
> Copilot: "Wind2's bus B3 has no coordinates (0, 0). Set coordinates for
> B3, or upload a profile instead?"

- Pass: the `missing_coordinates` refusal is relayed with both remedies; with coordinates given, an edit card `B3 · x/y · 0/0 → 9.2/52.3` comes first, then generation. Fail: any profile generated at 0/0.

**OJ-49 · Horizon from the project.** Closes RP-03.

> User: "Make the solar profile match my simulation horizon."

- Pass: the words name 2023 and 8,760 hourly values, taken from the snapshots, and no resolution question is asked. With `oj-renew` changed to 3-hourly snapshots, the result has 2,920 values, each a 3 h mean. Fail: a resolution or year question when the snapshots already answer it.

**OJ-50 · Tune in the panel.** Closes RP-05, RP-08.

> User: "I want to tune the solar parameters myself."

- Trace: `ui_open_panel('timeseries')` with the Renewables tab and `Solar1`'s tuning panel open.
- Pass: the words say "opened" (or "showing" with an ACK) and name the knobs (tilt, azimuth, tracking, losses). Fail: a raw JSON prompt.

**OJ-51 · "The tail doesn't match."** Closes RP-06, RP-07, RP-08.

> User: "Peak time and full-load hours match my measured data, but the tail
> doesn't: there is too much low-wind generation. Dampen the tail."
> Copilot: proposes `tail_damp{low, beyond P20, factor 0.5, keep FLH}`,
> runs it, and shows before/after: FLH unchanged (± 0.5 %), P10 old → new,
> hour of peak unchanged. It asks before applying.

- Trace: `shape_generated_profile` → `compare_profiles` → (approve) `apply_generated_profile`.
- Pass: every number is from `compare_profiles`; FLH and peak timing are preserved; nothing is written before approval. Fail: a regenerated weather download, or numbers the model computed itself.

**OJ-52 · Hit a full-load-hours target.** Closes RP-06, RP-07.

> User: "Wind1 should have 2,400 full-load hours."

- Pass: `target_flh{2400}` result is within 0.5 %, values stay within [0, 1], and the words show old → new FLH from the compare read. Fail: plain scaling that leaves values above 1.

**OJ-53 · Match an observed series.** Closes RP-06.

> User: uploads `measured_wind1.csv`, then says "Fit Wind1's profile to
> this measured series."

- Pass: `quantile_map{observed_ref}` matches the measured duration curve and keeps the weather timing (rank order). The words say timing comes from the chosen weather year and the coordinates. Fail: the observed series copied over the profile.

**OJ-54 · Undo all.** Closes RP-09.

> User (after OJ-47): "Undo that."

- Pass: one undo restores both generators' prior `p_max_pu`, including `Solar1`'s network-native values. Fail: one of the two left changed.

| Ticket | Journeys |
| --- | --- |
| RP-01 | OJ-48 |
| RP-03 | OJ-49 |
| RP-04 | OJ-47, OJ-48 |
| RP-05 | OJ-50 |
| RP-06 | OJ-51, OJ-52, OJ-53 |
| RP-07 | OJ-51, OJ-52 |
| RP-08 | OJ-47, OJ-50, OJ-51 |
| RP-09 | OJ-54 |
