<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# The library-reference preview is routed as a write

Status: ready-for-agent (outsourced task D, docs/agents/outsourced.md)
Type: task

`POST /api/network/library_reference/preview` (`routers/network_library_reference.py`)
only reads, but it sits under `/api/network/`. So the middleware in `main.py` treats each
preview as an edit:

- it takes an undo snapshot (`_UNDO_PREFIXES`);
- it bumps the network revision;
- it clears solver dispatch when results exist (`_DISPATCH_INVALIDATE_PREFIXES`).

Opening the cost-reference card therefore throws away a solve.

Fix: mount the preview outside `/api/network`, as the equipment-table preview does
(`/api/equipment_table/preview`, data-to-model PR-2, #581). Then add a test that the
undo depth and the network revision stay unchanged after a preview. Leave
`_UNDO_EXCLUDE` as it is.

Found by the gatekeeper while reviewing #581 (2026-10-22); kept out of that PR on purpose.
