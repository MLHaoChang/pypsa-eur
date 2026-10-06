---
id: improve-design
title: Improve the design
intent: Find the weakest point of the current design, test one change on a scenario, and compare.
when: [expert, guided]
order: 60
opening_request: What is the single most useful thing I can improve in this design, and why? Then help me test it.
steps:
  - id: find
    title: Find the weak point
    done_when: One finding is named with its evidence and the user chose whether to test it.
  - id: test
    title: Test one change on a scenario
    done_when: A scenario with the change exists and is solved.
  - id: compare
    title: Compare
    done_when: The user has seen the delta against the base and decided whether to keep it.
---

## Step: find

Call `review_eh_study` (hub designs) or `diagnose_network` and
`get_project_results_summary` (other networks). Pick the finding with the
largest effect and present it with `ask_user`: test it on a scenario, see
the other findings, or stop. Recommend testing it.

## Step: test

Call `create_scenario` from the current project, apply the change there
with the review finding's own tool and arguments (or `update_component`),
then `run_simulation` or `run_fmea_sweep` as the finding requires. Every run
and every delete shows a confirmation card.

## Step: compare

Call `compare_scenarios` on the base and the scenario and open the compare
rail with `ui_open_panel`. State the delta with units. Ask whether to keep
the scenario, try the next finding, or stop.
