---
id: run-study
title: Run a reliability study
intent: Check the site is ready, run the Energy Hub study, and read the adequacy results.
when: [expert, guided]
order: 40
opening_request: Run a reliability study on this network. Check the setup first and tell me what is missing before anything runs.
profile: eh_site@2
steps:
  - id: readiness
    title: Check the setup
    done_when: review_eh_study or suggest_eh_setup reports nothing blocking, or the user has accepted the gaps.
    stage: input_gate
  - id: run
    title: Run the study
    done_when: run_eh_study was started after the confirmation card and finished.
    stage: solve
  - id: read
    title: Read the results
    done_when: The user has heard the adequacy metrics in plain language and knows the next workflow.
    stage: result_gate
---

## Step: readiness

Call `suggest_eh_setup`; if it reports gaps (grid import link, critical
loads, grid strength, outage data), explain each in one sentence. Propose
all of the step's fixes in one response, one `update_component` call per
component: the user gets one card with a tick box per fix and applies the
ticked ones together. Do not ask about each fix in turn. Report from the
card's receipt which fixes were applied, and do not propose an unticked fix
again unless the user asks. Then `review_eh_study` for anything still
blocking.

## Step: run

Say what the study will do and that a confirmation card follows, then call
`run_eh_study`. Report progress from the status frames; do not call another
write while it runs.

## Step: read

Call `get_adequacy_results` for the metrics the study produced and
`get_project_results_summary`. Give the loss-of-load figures with their unit
and one sentence on what drives them. Offer the Explain-results and
Improve-the-design workflows.
