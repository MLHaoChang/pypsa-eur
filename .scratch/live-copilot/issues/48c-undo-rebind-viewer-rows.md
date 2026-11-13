<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-48c — The LC-D22 undo rows (rebind, viewer role)

Status: ready-for-agent
Model tier: opus
Size: M
Blocked by: none
Gated: GK B
Likely files:
- pypsa-gui/backend/smoke/family_manifest.py (`LC-D22.rebind`, `LC-D22.viewer_role`)
- pypsa-gui/backend/tests/test_lc43_d22_concurrency_gates.py
- pypsa-gui/backend/services/network_undo.py, services/undo_service.py
Type: task
Parent: [48-pending-delivery-report-undo-wire-rows.md](48-pending-delivery-report-undo-wire-rows.md) (split by owner decision LC48-SPLIT-1)

The LC-D22 share of LC-48's pending rows. No gate is built for these; each row in `pypsa-gui/backend/smoke/family_manifest.py` points at LC-48 until its ticket lands.

## Rows

- `LC-D22.rebind`: an edit racing a switch of the active project (build_delivery `project_changed`, a write rebinding mid-turn) is not tested.
- `LC-D22.viewer_role`: project ACL knows only admin and member; no read-only role refuses an edit, undo or restore. A new role is a product decision: ask the owner before building it, or record the row as dropped only on the owner's word (GK B C1).

## Done when

Each row above is covered by a behavioural fixture written the LC-43 way (a public seam, a hand oracle, the exact refusal kind, and `tests/lc43_gates.py::state` unchanged on every refusal), or the owner decides to drop it. Dropping a row is an owner decision (GK B C1). When the last row is covered, update its manifest row and the `r3-uncovered:LC-D22` blocker in `assistant_control_matrix.py`.

## Comments
