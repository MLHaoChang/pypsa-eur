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
