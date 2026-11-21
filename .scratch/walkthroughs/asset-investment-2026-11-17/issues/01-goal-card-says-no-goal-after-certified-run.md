<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# The Goal card says "No goal yet" while the verdict is certified against 3 h/yr

Status: done (#1677, #1734)
Type: bug (asset-investment walkthrough, WALK-E2E-1)
Journey steps: J2 Reliability goal → verdict (Guided and Expert)
Severity: high
Send to: reliability goal (GoalCard, ReliabilityGoalSummary, McPanel)
Duplicate check: extends `.scratch/ux-walkthrough-2026-11/issues/05-stale-step-numbers-and-first-warning.md` (#1618). That issue covers the staged-growth templates giving no verdict when no goal is set. This one is a single-year template where one screen applies the goal and three others say it is not set.

## What happens

1. Create "Island Microgrid" from the template. Guided › 8 Scenarios & optimisation shows two things: the Assumptions row "Reliability goal (LOLE): 3 h/yr (assumed)", and the Goal card's "Allowed shortfall" field empty with "No goal yet. Type how many hours per year without power you can accept (for example 3) to get a pass or fail answer."
2. 9 Readiness and run › Run study (20 s). Section 10 reads "Certified: no shortfall expected, under the 3 h/yr goal", and the Reliability tile reads "Certified: the goal is 3 h/yr".
3. Back on 8, the Goal card still reads "No goal yet" with the field empty (`shots/goal-after-run.small.png`).
4. Show the engineering detail › Reliability › Adequacy reads "No reliability target was applied to the last solve…", and the EH study line reads "allowed shortfall not set".
5. Expert › Results › Reliability › Sequential Monte Carlo › Run study returns LOLE 4.59 h/yr (95 % CI 2.77–6.41) with no verdict word. With a 3 h/yr goal this is a clear miss: "Could not be reached".

One goal reads as set on one screen and unset on three others. The user can't tell which number the verdict used, or whether typing 3 in the empty field changes anything.

## Expected

Every screen reads one goal value. When the template's assumed 3 h/yr applies, three screens show it:

- The Goal card shows 3 in the field, marked "assumed", and offers to confirm it.
- The Adequacy readout and the EH line state the 3 h/yr goal.
- The Expert Monte Carlo result carries the verdict word against it: Target met, Not confident or Could not be reached (`components/activityWords.ts:107-111`).

When no goal applies, the Guided verdict must not say "under the 3 h/yr goal".

## Breaks

EXPERT-GUIDED-1 (Expert never shows less than Guided): the Expert Monte Carlo has no verdict where Guided has one. Adequacy issue 04 (verdict words). review-ux.md: the verdict and its key must agree.

## Done when

The Island Microgrid run above shows the same goal value on the Goal card, the Adequacy readout, the EH line and the Expert Monte Carlo result. A test pins that the Goal card reads the assumed goal the verdict uses.
