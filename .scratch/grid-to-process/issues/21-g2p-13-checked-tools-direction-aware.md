<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# G2P-13: make `checked_tools` direction-aware so an enum widening is accepted by `--write`

Status: needs-triage
Type: task
Found by: G2P-13 (#1194) PR body, "Follow-up".

## Problem

#1194 added the `grid_risk` value to `ui_open_panel`'s results-tab enum. The frozen input-schema hash had to be edited by hand because `--write` does not accept a widening of a checked tool's input enum. Make the check direction-aware: a widening (additive) is accepted by `--write`, a narrowing or removal is still refused. Needs a plan to GK B (AA-V1-CHANGE-1 applies).

## Where to look

`pypsa-gui/backend/harness/assurance_contracts.py`, the `checked_tools` handling (`"checked_tools": "returns"`, ~line 56 and ~125).
