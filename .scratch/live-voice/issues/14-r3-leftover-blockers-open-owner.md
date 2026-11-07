<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Give the r3-uncovered blockers an open owner issue

Status: ready-for-agent
Type: task

The `r3-uncovered:LC-D20`, `LC-D21`, `LC-D22` and `LC-D23` blockers in
`full_coverage` (`pypsa-gui/backend/smoke/assistant_control_matrix.py`, `R3_ISSUE`)
point at live-copilot issue 32, which is done. Owner decision LC32-CLOSE-1 keeps
their pending rows (LC-D20.delivery_gates, LC-D20.path_injection, LC-D21.report_gates,
LC-D22.concurrency_gates, LC-D23.wire_gates) as the follow-up list. LC-D03 and LC-D24
went to the fix packages 33 and 35. No open issue owns the D20–D23 rows.

Task: name an open owner issue for those rows and repoint the blockers to it, or
record the owner's choice to leave them where they are. Then extend the standing-blocker
pointer guard (`_standing_blocker_problem`, LV 08 R4; it fails closed on any status other
than needs-triage, needs-info, ready-for-agent or ready-for-human) to cover them too.
Raised by GK B G4 on the LV 08 close-out.

## Comments

### 2026-11-07: triage sweep (FILL-1107)

- Not buildable until answered.
- Question (GK B): Owner: the `r3-uncovered` rows LC-D20 to LC-D23 point at the done live-copilot issue 32. Should we (a) open one new live-copilot issue that owns those rows and repoint them, or (b) leave them as the follow-up list (LC32-CLOSE-1) and only extend the standing-blocker guard? (a) is S, sonnet, GK B.
- Blocked by: the answer.

### 2026-11-07: needs-info answered (coordinator)

Owner (card, LV14-OWNER-1): "New owner issue". Open one live-copilot issue that owns rows LC-D20 to LC-D23 and repoint them from the done issue 32. S, Sonnet, GK B.
