<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-43 — Owner of the pending LC-D20 to LC-D23 gate rows

Status: ready-for-human
Type: task

Opened under owner decision LV14-OWNER-1 (live-voice issue 14). Issue 32 is done
(LC32-CLOSE-1), so the `r3-uncovered:LC-D20` to `LC-D23` blockers in
`full_coverage` (`pypsa-gui/backend/smoke/assistant_control_matrix.py`, `R3_ISSUE`) point here.

This issue owns the follow-up list that issue 32 left pending in
`smoke/family_manifest.py`:

- `LC-D20.delivery_gates`
- `LC-D20.path_injection`
- `LC-D21.report_gates`
- `LC-D22.concurrency_gates`
- `LC-D23.wire_gates`

It stays open until each row is covered by a behavioural fixture or the owner decides to
drop it. LC-D03 and LC-D24 went to fix packages 33 and 35.
