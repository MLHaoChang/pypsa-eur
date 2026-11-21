<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Grid-readiness rows that read solved sizes say when the solve is not current

Status: ready-for-agent
Size: S
Blocked by: none
Gated: GK A
Likely files: pypsa-gui/backend/services/adequacy/grid_readiness.py (`_unit_rating` and the rows that read `p_nom_opt`); pypsa-gui/backend/tests/test_grid_readiness_live.py
Type: task (engine)
Found by: GK A review of #1690 (L5), DC grid-readiness walkthrough 04.

## Problem

Since #1690 the scorecard reads the loaded network, unsaved edits included. The rows that rate a unit by its solved size (`_unit_rating`, which prefers `p_nom_opt`) have no "is the solve current?" check. After an edit made after a solve, they show the old solve's size straight away, where before a save was needed to reach the same state. Only the LOLE row checks `edited_since`.

#1691 labels these figures "(from the last solve)", so the words are not wrong, but the row does not say that the network has changed since.

## Expected

- Each row that reads `p_nom_opt` checks `edited_since` (the same check the LOLE row uses), and says when the network was edited after the solve it reads.
- A test: solve, edit a unit's size, and the row says the solve is not current.

## Notes

- Once #1690 and #1691 have both landed, reword #1691's `scr_gate` docstring ("the saved network's last solve"): the scorecard now reads the loaded network, so it is the loaded network's last solve.
