<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 18 — Cost and capacity headline figures disagree or are missing

Status: needs-triage
Impact: medium
Templates: DC staged, H2 staged, microgrid (both), ammonia, DC
Found by: Expert-mode walk, 2026-11-17 (master 0ab07339)

## What

LOPF toast "Solved · €120.29 M" vs Summary "€195 M" (H2 staged); €382.67M vs €576M (DC staged), no basis stated. Summary "No cost breakdown / No capacities for this solve" and "Loading the headline figures…" stuck (microgrid staged, ammonia). Off-grid microgrid lists "grid 100 MW" in Key capacities; DC lists "grid 500 MW" against a 40/55 MW connection. Ammonia: delivered 785 kt exceeds made 672 kt, unexplained. Study card: "Achieved 0.00‱" beside "LOLE 4.58 h/yr Not certified".

## Why it matters

Compare and decide depend on numbers that contradict each other.

## Suggested fix

One cost number with a stated basis (NPV or LP objective); fix empty summary; hide irrelevant assets.
