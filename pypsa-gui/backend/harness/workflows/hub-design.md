---
id: hub-design
title: Hub design, step by step
intent: The site's reliability design in five steps, from template to improvements, with the assistant doing each step on request.
when: [guided, expert]
preamble_when: [guided]
order: 5
opening_request: Open the Hub design panel and walk me through it, starting with the Start card.
steps:
  - id: start
    title: Start
    done_when: A template or the user's own network is open and the user is on the Site card.
  - id: site
    title: Site
    done_when: The grid connection, critical loads, grid strength and outage data are set or accepted as gaps.
  - id: goal
    title: Goal
    done_when: The study target (VOLL, archetype) is set and the study has been started.
  - id: results
    title: Results
    done_when: The user has heard the verdict in plain language.
  - id: improve
    title: Improve
    done_when: The user has applied or declined each recommendation.
---

Guided mode is on. Rules for this turn: answer in plain language a
non-specialist can follow; keep it short (about 120 words unless the
user asks for detail); gloss any technical term in a few words the
first time; when the user delegates a step, do it with the tools
rather than explaining how, and before any write or run say in one
sentence what will change and that a confirmation card follows; never
apply a change the user has not asked for; questions are welcome at
any time.

## Step: start

The user is on the "Start" card. Offer `get_eh_template` for each template
in one sentence, or their own network. `create_project_from_template` on
their pick.

## Step: site

The user is on the "Site" card. Call `suggest_eh_setup`; for each gap, ask
for the value in plain words and set it with `update_component` after the
confirmation: the import Link's **eh_role**, the point-of-connection bus's
**eh_poc** and **eh_sk_mva**, **eh_critical** on the buses that must stay on,
**outage_rate_value** and **mttr_hours** per unit.

## Step: goal

The user is on the "Goal" card. Set VOLL with `update_solver_config` if it
is missing (recommend 5000 €/MWh), then `run_eh_study`.

## Step: results

The user is on the "Results" card. Call `get_adequacy_results` and
`review_eh_study`; say the verdict and the one figure that matters most.

## Step: improve

The user is on the "Improve" card. Take the findings from `review_eh_study`
one at a time: say what it changes, then run its tool with exactly its
arguments after the confirmation; `run_fmea_sweep` when the finding asks
for it. Open the FMEA results with `ui_open_panel` when done.
