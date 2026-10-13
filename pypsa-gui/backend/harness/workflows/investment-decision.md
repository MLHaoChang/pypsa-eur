---
id: investment-decision
title: Is this investment worth it?
intent: A question-led decision study (battery at my site, data-centre power, waste heat) with an assumptions ledger and a verdict.
when: [guided, expert]
order: 70
status: planned
opening_request: Help me decide whether an investment is worth it. Ask me which question I have and what you need to know.
steps:
  - id: question
    title: Pick the question
    done_when: A decision question is chosen and a Decision study exists.
  - id: parameters
    title: Key parameters
    done_when: Every key parameter has a user value or an accepted generic default in the ledger.
  - id: verdict
    title: Verdict
    done_when: The user has heard the verdict, its drivers and whether it is marginal.
  - id: report
    title: Report
    done_when: The report is generated or declined.
---

Planned: the tools below are defined in plan v1.4 (U3) and do not exist
yet; this workflow stays out of the menu until they do.

## Step: question

Offer the questions in the owner's order (battery at site, data-centre
power, waste heat, hydrogen, off-grid) with `ask_user`, then
`start_decision_study`.

## Step: parameters

For each key parameter ask with `ask_user`, offering the generic default as
the recommended option with its source and year; `set_study_parameter` on
the answer.

## Step: verdict

`run_decision_study`, then `get_study_findings` and `explain_verdict`.
State whether the verdict is recommended, marginal or not recommended and
which driver could flip it.

## Step: report

Offer `generate_study_report`; list every generic default the report
relies on.
