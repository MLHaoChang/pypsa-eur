<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-48b — The LC-D21 report rows (stale report, resume)

Status: ready-for-agent
Model tier: opus
Size: M
Blocked by: none
Gated: GK B
Likely files:
- pypsa-gui/backend/smoke/family_manifest.py (`LC-D21.stale_report`, `LC-D21.report_resume`)
- pypsa-gui/backend/tests/test_lc43_d21_report_gates.py
- pypsa-gui/backend/services/reports/
Type: task
Parent: [48-pending-delivery-report-undo-wire-rows.md](48-pending-delivery-report-undo-wire-rows.md) (split by owner decision LC48-SPLIT-1)

The LC-D21 share of LC-48's pending rows. No gate is built for these; each row in `pypsa-gui/backend/smoke/family_manifest.py` points at LC-48 until its ticket lands.

## Rows

- `LC-D21.stale_report`: a report generated or exported after its study changed is neither refused nor flagged (only the IC case note); no stale gate is built.
- `LC-D21.report_resume`: a report job interrupted by a restart is not resumed (its record is in-process state); no resume is built.

## Done when

Each row above is covered by a behavioural fixture written the LC-43 way (a public seam, a hand oracle, the exact refusal kind, and `tests/lc43_gates.py::state` unchanged on every refusal), or the owner decides to drop it. Dropping a row is an owner decision (GK B C1). When the last row is covered, update its manifest row and the `r3-uncovered:LC-D21` blocker in `assistant_control_matrix.py`.

## Comments
