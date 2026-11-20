<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 06: After an Expert solve, Guided Results say "Not run yet" and show no Control card

Status: ready-for-agent
Impact: high
Templates: Island Microgrid, Green Ammonia Hub (staged growth); likely every template
Found by: control-strategy walkthrough, 2026-11-17 (master 9491878d). Shots: `../shots/06-guided-results-not-run.png`, `../shots/dark-guided-results.png`

## Repro

1. Island Microgrid, Expert. Run LOPF (Summary: total system cost €3.47 M). Then Grid study › Island › Run cost-of-islanding study (cost of island capability 184,125).
2. Press Guided › 10 Results & reports.

## Actual

- The headline reads "Not run yet: The study writes its verdict, cost and risks here."
- The Operation card reads "Solved: Every modelled hour dispatched; its cost is in the yearly cost above". There is no cost above.
- No card shows the island study. The only control line is "Grid-forming settings: no recommendation yet."
- On Green Ammonia Hub, where Control is out of scope, the same "Grid-forming settings" line still shows.

## Expected

Guided shows what Expert shows (EXPERT-GUIDED-1: neither mode shows less than the other):
- the solve's yearly cost, with its source run, or the Operation card says in words where the cost is;
- a Control card when the Control work group has a result (cost of island capability, limits reached), with "Open the detail ›";
- no grid-forming line when Control is out of scope.

## Relation

This is the mirror of `.scratch/ux-walkthrough-2026-11/issues/01-guided-expert-result-mismatch.md` (#1618): there, a Guided study left Expert empty; here, an Expert solve leaves Guided empty. Fix both with one rule for which run each mode's headline reads.

## Breaks

- EXPERT-GUIDED-1.
- HOLISTIC-1.
- `.scratch/project-workflow/design.md:402`, rule 7 ("Same section, same state in both modes").
