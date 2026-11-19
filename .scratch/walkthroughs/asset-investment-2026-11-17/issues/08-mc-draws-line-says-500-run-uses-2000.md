<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# The Monte Carlo panel says 500 draws and the run uses 2,000

Status: ready-for-agent
Type: bug (asset-investment walkthrough, WALK-E2E-1)
Journey steps: J2 Monte Carlo (Expert)
Severity: low
Send to: `pages/results/McPanel.tsx`, `components/reliabilityGoal/ReliabilityGoalSummary.tsx`
Duplicate check: none.

## What happens

1. Island Microgrid › Expert › Results › Reliability › Sequential Monte Carlo reads "Reliability goal: MC draws 500 (default)".
2. Run study. The result reads "LOLE 4.59 h/yr · 95% CI 2.77–6.41 h/yr · n=2000" and "draw cap reached before the target coefficient of variation".

The Expert panel batches to `MAX_DRAWS = 2000` (`services/adequacy/mc.py:68`, `:924`). The Guided study certifies on 500 draws ("draws 500" in its MC LOLE block). The line above the button names the goal's 500, which this run doesn't use.

## Expected

The line states what the run does ("up to 2,000 draws, stops at 5 % CoV"), or the run honours the goal's draw count. Guided and Expert say which of the two each used, because the confidence of the verdict depends on it.

## Breaks

review-ux.md: visible reasons, results traceable to inputs. EXPERT-GUIDED-1: the two modes use different draw counts without saying so.

## Done when

On the Island run, the line above Run study and the result's n agree.
