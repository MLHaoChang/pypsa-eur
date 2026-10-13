---
id: investment-decision
title: Is this investment worth it?
intent: A question-led decision study (today, a battery at my site) with an assumptions ledger, a verdict and a report.
when: [guided, expert]
order: 70
status: active
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

Each study tool takes **project** and **study_id**. **project** is the study's own
base project, which `start_decision_study` returns; only
`start_decision_study` takes the project open now, and it does not change it.

## Step: question

Today one question can be answered: "Do I need a battery at my site?"
(**bess_at_site**). Data-centre power, waste heat, hydrogen and off-grid are
coming; say so if asked. Ask for the bidding zone, the grid connection in MW
and the load (a sector profile with its annual MWh, or an uploaded meter
file) with `ask_user`; never invent them. Ask whether the site has a tariff
of its own; without one the defaults' illustrative tariff is used and the
report says so. Then `start_decision_study`.

## Step: parameters

`start_decision_study` returned the key parameters, each with its default
and source. For each, ask with `ask_user`, offering the default as the
recommended option with its source and year; `set_study_parameter` on a
changed answer, with the row's own unit. Everything else stays the generic
defaults; say that once.

## Step: verdict

`run_decision_study` runs the options; poll `get_study_findings` until it
has finished. Then `run_decision_study` with stage `tornado`, and poll
again: the verdict needs the tornado. Read the verdict with
`get_study_findings` and `explain_verdict`: say whether it is recommended,
marginal or not recommended, at what size, and which driver could flip it.
A figure that is not established is said so, never read as zero.

## Step: report

Offer `generate_study_report` (Word by default, the workbook on request);
say that the report lists every generic default it relies on.
