---
id: explain-results
title: Explain my results
intent: Read the solved results, say what drives them, and point at the evidence.
when: [expert, guided]
order: 50
opening_request: Explain the results of the current project in plain language and tell me what drives them.
steps:
  - id: status
    title: Are the results current?
    done_when: The user knows whether results are fresh, stale or absent.
  - id: explain
    title: Explain
    done_when: The user has heard the headline figures, the binding driver and where to look.
---

## Step: status

Call `get_simulation_status`. Stale results describe an older network: say
so and offer `run_simulation` before explaining. No results: offer the
Run-a-study workflow or `run_simulation`.

## Step: explain

Call `get_project_results_summary` and, for investment questions,
`explain_investment`. Open the matching results view with `ui_open_panel`
so the user sees what you cite. Give evidence, never a verdict the data does
not carry; mark any figure the backend reports as unavailable as unavailable,
not zero. For a written report offer `build_study_report`.
