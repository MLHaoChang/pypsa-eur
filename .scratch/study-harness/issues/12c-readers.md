<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 12c: the readers of the `ic.baseline_keeps` reason (B12, G5)

Before: `_ic_baseline_keeps` gave the gate the raw `"<state>: <code>, <code>"`
(`not_established: baseline_drops:diesel_1`, `not_run: no_case`,
`not_run: case_stale, results_not_current`). After: the typed state stays in the
fact's value and the reason is `ic.reason_words(fact)`, with new words for
`baseline_drops:<asset>` ("the case's baseline drops the owned asset <asset>")
and `no_baseline_plan`.

## The grep

`git grep` over `pypsa-gui/` (backend and frontend, tests excluded) for every
substring of the old text: `baseline_drops`, `no_baseline_plan`,
`not_established: `, `not_run: `, `case_stale, results_not_current`.

- `baseline_drops`, `no_baseline_plan`: only `services/ic_counterfactual.py`
  (where they are made) and `tests/test_ic_baseline_keeps_fact.py`.
- `not_established: `: `services/results_workbook/overview.py` writes its own
  workbook notes with that prefix; it never reads a gate reason.
- `not_run: `, `case_stale, results_not_current`: no reader outside the tests.

No code parses the gate's reason text. The reason travels as a string field
of the gate record; these are the places it is read:

| Reader | What it does with the reason | Test (`tests/test_run_ic_words_and_counts.py`) |
|---|---|---|
| `run_gates.evaluate` (the gate record, `run.stages.result_gate.gate.rules[].reason`) | stores it beside the typed state | `test_the_gate_keeps_the_typed_state_and_gives_the_reason_in_words` |
| `assistant_tasks._run_view` (the chat tool `get_task` and the run monitor's view) | copies it to the stage's `gate` entry | `test_the_words_reach_the_task_view_the_chat_tool_and_the_monitor_read` |
| `services/reports/harness_chapter.gates_rows` (the run report's gates table) | prints it in the Note column | `test_the_words_reach_the_reports_harness_chapter` |
| `run_handoff.checks` (the hand-off's `checks` and the report's `result_checks_*` counts) | copies it beside each not-evaluated rule | `test_fuel_autonomy_and_frequency_are_their_own_state_on_the_hand_off` |
| `run_loops._l2_message` (the L2 turn's failed rules) | never reached: L2 carries failing `block` rules only, and both investment rules warn | `test_a_warn_rule_never_reaches_the_l2_message` |

The frontend's run monitor shows a rule's verdict and value, not its reason.
`tests/test_ic_baseline_keeps_fact.py` pins the new words for the dropped
asset, no case, and a stale case.
