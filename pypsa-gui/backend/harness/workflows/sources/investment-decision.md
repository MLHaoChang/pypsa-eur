---
id: investment-decision
title: Is this investment worth it?
intent: A question-led decision (a battery at my site, or whether the plant of this template is worth building) with its assumptions, a verdict and a report.
when: [guided, expert]
order: 70
status: active
opening_request: Help me decide whether an investment is worth it. Ask me which question I have and what you need to know.
profile: eh_site@2
steps:
  - id: question
    title: Pick the question
    done_when: A decision question is chosen, and a Decision study exists (bess_at_site) or the template's business-case lines are stored (plant_business_case).
  - id: parameters
    title: Key parameters
    done_when: Every key parameter has a user value or an accepted generic default in the ledger; for plant_business_case every asked value is the user's (or its line is removed) and the user kept or changed the assumed defaults.
  - id: verdict
    title: Verdict
    done_when: The user has heard the verdict, its drivers and whether it is marginal (plant_business_case, the verdict with EBITDA margin, NPV, IRR and the break-even price).
    stage: explore
  - id: report
    title: Report
    done_when: The report is generated or declined.
    stage: report
---

The decision study tools (bess_at_site) take **project** and **study_id**. **project** is the study's own
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

The second question is "Is this plant worth building?" (**plant_business_case**),
on the project open now when it was made from a template (a data centre, an H2 hub,
ammonia, a concentrator, an IPP, a microgrid or the generic site). It needs the
finance inputs (`get_finance_inputs`; without them ask for them first) and a solved
network for the investment case (offer `run_simulation` when there is none). Apply
the template's preset with `apply_business_case_preset` and the `digest` of
`get_business_case` with detail 'inputs'. A template with several presets refuses
with their list: ask the user which with `ask_user` and pass it as **variant**. A
template with no preset (the generic site, or one not covered yet) refuses with the
generic offered rows: ask which the plant has (`ask_user`, several allowed) and pass
their labels as **rows**. The server fills every cost default from its pack; nothing
is typed by you. The card replaces any lines already stored; say so.

## Step: parameters

`start_decision_study` returned the key parameters, each with its default
and source. Ask once, as one Assumptions card: a single `ask_user` whose
`detail` lists every parameter with its default, unit, source and year in
plain words (never raw keys), and whose options are "Keep all defaults"
(recommended) and "Change some". Never one question per parameter. If the
user changes some, they say which in free text: `set_study_parameter` for
each changed one in the same turn, with the row's own unit; the rest stay as
listed. Everything else stays the generic defaults; say that once.
Inputs the user must give (bidding zone, connection, load, tariff) are asked
above and never get a default or a "Keep all defaults".

For **plant_business_case**, `apply_business_case_preset` returned `to_ask`
(`get_business_case` lists it again): every empty price, quantity or share, and
kW per GPU when a GPU-hour line needs it. Ask them all in ONE `ask_user` whose
`detail` lists each with its line, unit and the preset's public range when there
is one (a range, never a value to use); the user answers in free text. Never invent
a value, and never fill one from a range. Store what the user gave with
`set_business_case_lines` and its **set** argument (only the named cells change),
each price citing that turn (turn_id and the quote with its unit). A value the user
did not give stays empty and the run refuses it (say which), or the user removes
the line. Then offer the assumed defaults (the `assumed` list) as one Assumptions
card, as above: each with value, unit, source and year; "Keep all defaults"
(recommended) or "Change some". Kept defaults stay assumed. A changed one is a
**set** cell that cites the user's words (its citation names the **column**:
quantity, also for a share, life_years or money_year), even when it equals the
default; a changed price default also asks the money year it is in.

## Step: verdict

Put both runs in one plan (`start_task`, two steps, titled with the user's
words): `run_decision_study` (options), then `run_decision_study` with stage
`tornado`. Each step keeps its own run card, which the user approves as ever.
The plan waits on each run, so the conversation continues once, by itself,
when the last one ends and the user is not asked to come back between them.
Say that the runs were started and that you will report when they finish; do
not poll. A run that fails or is aborted fails its step with the run's own
error: read it to the user, name the stage that failed, and give no verdict
for it. When the tornado has finished (the verdict needs it), read the verdict with
`get_study_findings` and `explain_verdict`: say whether it is recommended,
marginal or not recommended, at what size, and which driver could flip it.
A figure that is not established is said so, never read as zero.

For **plant_business_case**, the plan's two steps are `run_investment_case`, then
`run_business_case`, the same way. When both have finished, read `get_business_case`
and give its **verdict**: worth building (project NPV 0 or more), not worth building
(below 0) or not established (say why). Quote the EBITDA margin (net and gross of
pass-through), the project NPV and IRR, and the break-even price of the break-even
line with how far the entered price can fall (or must rise) before the NPV reaches
0: that is the driver that could flip it. When the verdict rests on assumed
defaults, say so and name those lines.

## Step: report

Offer `generate_study_report` (Word by default, the workbook on request);
say that the report lists every generic default it relies on.

For **plant_business_case**, offer `export_results_workbook`: its "Business case"
sheet holds the P&L by year, the KPIs, the break-even and every assumed default
with its source and year.
