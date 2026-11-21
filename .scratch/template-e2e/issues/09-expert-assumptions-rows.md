<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 09 — Expert shows none of the Assumptions rows Guided shows

Status: ready-for-agent
Owner: template-e2e slice-2 builder (successor of session_01G7qCukBqpE7RUE97mdvTio)
Size: M
Gated: UX (UX 2 session_01G7qCukikR93QrY7d2YFJ1c)
Likely files: pypsa-gui/frontend/src/pages/hubDesign/AssumptionsCard.tsx; pypsa-gui/frontend/src/pages/hubDesign/SectionView.tsx; the Expert Issues panel
Journey fit: set up (sections 3, 5, 8) → readiness → run, Expert mode. Guided and Expert agree (EXPERT-GUIDED-1; "Expert never shows less than Guided", #1193).

## Problem

Every assumed row (`assumed_*` flow items) renders only in Guided, through `AssumptionsCard` in `SectionView`. That covers the template ledger rows, e.g. "Reliability goal (LOLE): 3 h/yr (assumed)", and since #1645 the run's own rows (simulation runs, site type, outage rate, repair time, outage-rate basis, link efficiency). Expert's Issues panel lists preflight validation only. On an eh_microgrid project, the sidebar Issues badge reads 19 while the panel reads "Errors 0 / Warnings 0 / Findings 0". The gap predates #1645.

## Acceptance

- Expert shows every assumed row, old and new, through the same component and with the same words as Guided.
- Expert shots of the rows go to UX with the slice-2 plan.
- This is a stated slice-2 deliverable (UX 2 ruling on #1645). If slice 2 slips past the next UI slice in this area, tell UX 2.
