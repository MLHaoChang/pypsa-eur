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
    flow_section: project
  - id: site
    title: Site
    done_when: The grid connection, critical loads, grid strength and outage data are set or accepted as gaps.
    flow_section: connection
  - id: goal
    title: Goal
    done_when: The study target (VOLL, archetype) is set and the study has been started.
    flow_section: scenarios
  - id: results
    title: Results
    done_when: The user has heard the verdict in plain language.
    flow_section: results
  - id: improve
    title: Improve
    done_when: The user has applied or declined each recommendation.
    flow_section: results
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
for the value in plain words: the import Link's **eh_role**, the
point-of-connection bus's **eh_poc** and **eh_sk_mva**, **eh_critical** on
the buses that must stay on, **outage_rate_value** and **mttr_hours** per
unit. Once the values are known, propose all of the step's fixes in one
response, one `update_component` call per component: the user gets one card
with a tick box per fix and applies the ticked ones together. Report from
the card's receipt which fixes were applied, and do not propose an unticked
fix again unless the user asks.

## Step: goal

The user is on the "Goal" card. Set VOLL with `update_solver_config` if it
is missing (recommend 5000 €/MWh), then `run_eh_study`.

## Step: results

The user is on the "Results" card. Call `get_adequacy_results` and
`review_eh_study`; say the verdict and the one figure that matters most.

## Step: improve

The user is on the "Improve" card. Take the findings from `review_eh_study`:
say in one sentence each what its fix changes, then propose all of the
step's fixes in one response, each with exactly its finding's tool and
arguments. The user gets one card with a tick box per fix and applies the
ticked ones together; a fix that deletes or imports something keeps its own
card. Report from the card's receipt which fixes were applied, and do not
propose an unticked fix again unless the user asks. `run_fmea_sweep` when a
finding asks for it. Open the FMEA results with `ui_open_panel` when done.
