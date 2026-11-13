<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-48a — The LC-D20 delivery expiry row

Status: ready-for-agent
Model tier: sonnet
Size: S
Blocked by: none
Gated: GK B
Likely files:
- pypsa-gui/backend/smoke/family_manifest.py (`LC-D20.delivery_ttl`)
- pypsa-gui/backend/tests/test_lc43_d20_delivery_gates.py
- pypsa-gui/backend/services/ (the delivery store, to be located)
Type: task
Parent: [48-pending-delivery-report-undo-wire-rows.md](48-pending-delivery-report-undo-wire-rows.md) (split by owner decision LC48-SPLIT-1)

The LC-D20 share of LC-48's pending rows. No gate is built for these; each row in `pypsa-gui/backend/smoke/family_manifest.py` points at LC-48 until its ticket lands.

## Rows

- `LC-D20.delivery_ttl`: a delivery and its download never expire; no expiry gate is built.

## Done when

Each row above is covered by a behavioural fixture written the LC-43 way (a public seam, a hand oracle, the exact refusal kind, and `tests/lc43_gates.py::state` unchanged on every refusal), or the owner decides to drop it. Dropping a row is an owner decision (GK B C1). When the last row is covered, update its manifest row and the `r3-uncovered:LC-D20` blocker in `assistant_control_matrix.py`.

## Comments
