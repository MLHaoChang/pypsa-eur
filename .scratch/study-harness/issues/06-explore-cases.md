<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 06 — Explore stage: ≤4 cases through existing case engines

Status: done (#1127; deviation-2 test with HF-1)
Type: task
Blocked by: 05
Gated: yes (GK B)
Model tier: opus
Owner decisions: AI-558 (≤4 cases, ≤2 changes each, sourced ranges), AI-567
Design: [design.md](../design.md) §3 stage 6, §6.7

## What to build

1. Agent proposes ≤4 cases with ≤2 changes each and a sourced range per change (AI-558 Suggest-cases); harness admits them into `run.cases`.
2. Each case runs through `run_sensitivity_sweep` (network), experiments (two runnable cases today), or the decision-study option runner for investment questions; then its own result gate.
3. A case that needs an absent domain adapter is `not_runnable` with the reason (live-voice 10 adapters, not built here).

## Acceptance criteria

- [ ] A fifth case or a third change is refused at proposal.
- [ ] eh_site: connection vs BESS vs genset, 3 cases, each with its own gate result and settled status.
- [ ] A failing case does not fail the run; the base case does.
- [ ] An investment case runs through the decision-study runner on study-owned forks; the base project is unchanged.

## Likely files

`pypsa-gui/backend/harness/runner.py`, `pypsa-gui/backend/services/run_profiles.py`, `pypsa-gui/backend/services/study/runner.py` (read only unless a seam is needed), `pypsa-gui/backend/tests/test_harness_run_explore.py` (new)

## Comments

## Plan verdicts (4 Nov, builder session_01G7qCujFkC7LtwMRPL7BJ68)

The plan:
- The agent proposes cases through a run-only `propose_cases` tool (explore turns only). Harness code admits them into `run.cases`, all or nothing, with typed refusals.
- A profile names its explore levers (`explore.levers`), separate from `tunable`. eh_site@1's are `Link poc_link p_nom`, `genset_new_* p_nom_max`, `bess_new_* p_nom_max` and `max_hours`; its investment question is `bess_at_site`.
- Network cases run through one `run_sensitivity_sweep`, on forks. Investment cases run through `start_decision_study` / `set_study_parameter` / `run_decision_study`, on study-owned forks. A case with no adapter is `not_runnable` with its reason.
- Each case is judged by the profile's result_gate rules on its own project (`run_gates.Env.case`), skipping rules that carry `engine` (SH-05 PR C).
- The explore gate is `explore.unsettled == 0`.
- The code goes in a new `harness/run_explore.py`.
- Red tests: `pypsa-gui/backend/tests/test_harness_run_explore.py`.

### GK B (21): GO WITH CHANGES

Q1–Q6 all (a), as recommended:
- Q1: the `propose_cases` tool.
- Q2: a new module.
- Q3: sources are `research:`, `profile:` or `ledger:`.
- Q4: no experiments path.
- Q5: no L2 inside a case; it is a follow-up.
- Q6: `poc_link p_nom` is an explore-only lever.

Code waits on SH-05 PR C, #1100 and #1104.

- **E1** (ruling #875). Admission refuses a sweep whose `baseline` or any `*project*` arg is not the run's own project. The case changes are exactly the admitted `run.cases` changes: the args come from the harness, never from the model. Test both.
- **E2** (#1072). A case value lies in its sourced range. A `research:` source must be verified AND user-confirmed at admission, re-read under the lock and not cached. A row rejected after proposal refuses. Test a source rejected between proposal and admission.
- **E3** (eh_site@1 in place). A run whose grant predates explore never enters it with sweep solves its envelope doesn't cover (C8). Test a granted-before run resuming: explore cards or skips, with no unattended sweep. If the profile is not snapshotted at intake, say how in-flight runs behave.
- **E4** (#1088, #1070). The check-and-reserve of solves, and every `run.cases` write, happen under `runner._file_lock` with the live-lease check. Add a takeover test.
- **E5** (SH-05 rule). Each case's solve count covers every job the sweep enqueues, plus any probe or closing re-solve. Investment cases reserve the upper bound before the start and settle actuals after. Test reserve against actual.
- **E6** (#992). Case gates read the child from disk and never activate it. The active project and the base's `network_revision` are unchanged after the case gates (tested).
- **E7.** Every lever, `poc_link p_nom` included, is refused as an L1/L2 tunable, and `_check_tunable` applies both ways. A profile listing a lever as tunable is refused at load (tested).
- **E8.** `propose_cases` is offered only in explore turns (`stage_tools`), and a call outside one refuses. Add a reachability row.
- **E9.** The kinds go in a `tool-error-kinds.d` fragment. Each typed refusal gets a fail-closed test and a base-class mutant.
- **E10** (#1058). A case job's deadline settles on its own timer and on the boot sweep, not only on the next touch.
- **E11.** The range row shape matches SH-29's (below).

### Agreed with SH-05 PR C (session_01G7qCunDhV74iECmHrF3cpp)

- `Env` and `_solve_job` stay as they are in PR C; SH-06 adds `Env.case` after it merges.
- Case gates skip rules with an `engine` key (`LOOP_FACT`).
- `solves_used` sums the `reserved` / `solves` audit rows. SH-06 writes `{"event": "explore", "tool", "call_id", "case", "reserved"}`, plus `"solves"` at settle.
- `run_sensitivity_sweep` is in `RUN_TOOLS`, so its admitted `used` row already counts one solve. The explore row must not count that sweep again: it carries the case jobs beyond the first, or the `used` row is excluded for an explore call (tested).

### Agreed with SH-29 (SH-09 follow-ups, session_01G7qCukyXRkELTh91Va9cgB)

- **Gap.** An explore gap is `research: {unit, basis, shape: "range"}`, and its `target` is the lever's `{component, name, attr}`.
- **Row.** `{value: null, range: {low, high}, unit, basis, url, quote, status, verification, …}`. It is verified only when both bounds parse from a verbatim quote in the gap's unit, with low ≤ high. It is read through `source_ledger.ranges(rows, gap_key)`.
- **Confirmation.** A range is confirmed on research's own `research_confirm` card. Only `confirmed` rows are admissible (E2).
- **Change.** A change carries `range {low, high, unit}` and `source: "research:<row id>"`. Admission checks that low, high and unit equal the row's.

### GK B (21) clarifications (4 Nov)

- **E7 (a).** Only levers flagged `explore_only: true` (eh_site@1: `Link poc_link p_nom`) are refused as tunables. NEVER_TUNABLE applies to every lever. A lever that is also an L2 tunable stays both: a what-if on forks, an envelope-bounded fix on the base. eh_site@1's L2 tunables are unchanged.
- **Sweep (a).** Issue 08's ruling stands (`run_envelope.py:57`). The explore sweep is always the user's own card, and no profile envelope lists it; a profile that does is refused at load. E1's checks (baseline, `*project*` args, args from `run.cases`) apply when that card is dispatched. E3 reduces to one test: a resumed pre-change run reaches the sweep card and never runs it unattended. The test that the sweep is not double-counted (the RUN_TOOLS `used` row against the explore audit row) stays.
