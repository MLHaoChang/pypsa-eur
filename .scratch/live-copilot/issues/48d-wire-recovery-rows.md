<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-48d — The LC-D23 wire rows (history, approval, profile, goal)

Status: ready-for-agent
Model tier: opus
Size: L
Blocked by: none
Gated: GK B
Likely files:
- pypsa-gui/backend/smoke/family_manifest.py (`LC-D23.history_repair`, `LC-D23.approval_resume`, `LC-D23.profile_recovery`, `LC-D23.goal_switch`)
- pypsa-gui/backend/tests/test_lc43_d23_wire_gates.py
- pypsa-gui/backend/harness/ (history, confirmation and task code, to be located)
Type: task
Parent: [48-pending-delivery-report-undo-wire-rows.md](48-pending-delivery-report-undo-wire-rows.md) (split by owner decision LC48-SPLIT-1)

The LC-D23 share of LC-48's pending rows. No gate is built for these; each row in `pypsa-gui/backend/smoke/family_manifest.py` points at LC-48 until its ticket lands.

## Rows

- `LC-D23.history_repair`: a stored history with an unpaired tool_use is sent as it is; no repair step is built (the harness only avoids writing one).
- `LC-D23.approval_resume`: a pending approval is not resumed after a restart (it expires or reads `unknown_confirmation_token`); no resume is built.
- `LC-D23.profile_recovery`: after a refused provider switch or a deleted profile, recovery (a new chat, or picking a profile and resending) is not tested.
- `LC-D23.goal_switch`: no gate refuses or recovers a workflow, goal or task switch mid-turn; none is built.

## Done when

Each row above is covered by a behavioural fixture written the LC-43 way (a public seam, a hand oracle, the exact refusal kind, and `tests/lc43_gates.py::state` unchanged on every refusal), or the owner decides to drop it. Dropping a row is an owner decision (GK B C1). When the last row is covered, update its manifest row and the `r3-uncovered:LC-D23` blocker in `assistant_control_matrix.py`.

## Comments
