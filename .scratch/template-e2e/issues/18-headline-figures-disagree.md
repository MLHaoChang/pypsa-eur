<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 18 — Cost and capacity headline figures disagree or are missing

Status: ready-for-agent
Impact: medium
Templates: DC staged, H2 staged, microgrid (both), ammonia, DC
Found by: Expert-mode walk, 2026-11-17 (master 0ab07339)

## What

LOPF toast "Solved · €120.29 M" vs Summary "€195 M" (H2 staged); €382.67M vs €576M (DC staged), no basis stated. Summary "No cost breakdown / No capacities for this solve" and "Loading the headline figures…" stuck (microgrid staged, ammonia). Off-grid microgrid lists "grid 100 MW" in Key capacities; DC lists "grid 500 MW" against a 40/55 MW connection. Ammonia: delivered 785 kt exceeds made 672 kt, unexplained. Study card: "Achieved 0.00‱" beside "LOLE 4.58 h/yr Not certified".

## Why it matters

Compare and decide depend on numbers that contradict each other.

## Suggested fix

One cost number with a stated basis (NPV or LP objective); fix empty summary; hide irrelevant assets.

## Triage (2026-11-20, master 8a039460)

Checked by code reading only; the app was not re-run. Still true: `HeadlineStrip.tsx` still has the 'Loading the headline figures' state. Acceptance: every cost figure states its basis (LP objective or NPV) wherever it shows; the toast and the Summary agree or say why they differ; the empty-summary and stuck-loading states are fixed; assets that do not apply to the template (grid on an off-grid microgrid) are not listed in Key capacities. Gated: GK A and UX. Blocked by: none.

## Comments

- Seen again 2026-11-20 (template walk, master 1f6905e3): H2: Guided 'Yearly cost' EUR 7.61 M vs Expert Run LOPF Summary 'Total system cost' EUR 9.02 M (status bar 'Obj: € 9 m'), no basis stated on either. DC (34.7 M) and microgrid (3.47 M) agree.
