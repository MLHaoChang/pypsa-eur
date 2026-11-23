<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 04: The grid-readiness scorecard keeps showing the old verdict after an edit, until the project is saved

Status: done

Done in #1690 (a8f6c4fa).
Type: task
Impact: high (misleads: a weak grid reads as a pass for up to the 5-minute autosave)
Templates: every template with the scorecard; walked on eh_datacenter (Guided and Expert)
Found by: DC grid-readiness walkthrough, 2026-11-17 (master 9491878d)
Breaks: WALK-E2E-1 "results traceable to inputs"; review-ux.md "Freshness wording is 'out of date'" (no freshness mark is shown); review-ux.md "A notice clears when its condition ends"

## Repro

1. Create Data Center Energy Hub with defaults (Gensets, any architecture). Guided › 3 Connection & grid.
2. Assumptions › "Fault level at the PoC: 250 MVA (assumed)" › Edit ›. Enter 60, Source Measured, Save fault level. Back to the cards.
3. Read Site › Grid readiness: "Short-circuit ratio at the PoC".
4. Press Ctrl+S (or wait for autosave), read it again.
5. Same in Expert: Assets › Generators › Import values from a file… with a CSV raising genset_1 and genset_2 to 12 MW; Apply; read the energization row in Readiness and run › Issues; then Ctrl+S.

## Actual

- Step 3: SCR still "Pass · Project: 8.333" (250 / 30). The live bus already holds 60 MVA.
- Step 4: SCR "Warn · Project: 2", and the summary goes from 4 to 5 things to look at. Hand check: 60 / 30 = 2.0. RVC becomes 13.24 % and flicker Pst 5.66, matching (cos ψ·23.4 + sin ψ·2.34)/60 at X/R 4 and 1.359 × 250/60.
- Step 5: the energization limit stays 69.8 MW after Apply and moves to 71.8 MW only after the save. Hand check: (12+12+10+10) − 12 = 32 MW firm, + 39.8 = 71.8.

Nothing on the scorecard says it reads the saved project, or that unsaved edits exist. `ConnectionLimits.tsx` invalidates the scorecard query after Save, but `GET …/grid-readiness` "reads the saved network" (`routers/campus_electrical.py:245`), so the refetch returns the old values.

## Expected

After an edit the scorecard shows the edited values, or it says "Out of date: save the project to update" (ResultState words), with a Save action next to that line. The scorecard in the report (it reads the same saved file) follows the same rule.

## Pointers

`frontend/src/components/gridReadiness/ConnectionLimits.tsx:104-117`; `frontend/src/components/gridReadiness/GridReadinessScorecard.tsx`; `backend/routers/campus_electrical.py:245` (`grid_readiness_for` on the saved netCDF).
