<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-41 — The frontier, MC and margin-loop panels do not show the stale state

Status: needs-triage
Type: bug

Split from LC-39. `GET /api/results/{frontier,mc,margin_loop}` now carry `edited_since_study`
(true after a network edit, null while running or without a captured revision), but no panel
reads it, so a panel still shows pre-edit numbers unflagged. The manifest row is
`LC-D13.stale_source_panels` (`_pending`).

UI work: it needs the UX reviewer. Follow how the EH panel shows the same flag.

## Acceptance

- [ ] Each of the three panels shows a stale banner when `edited_since_study` is true.
- [ ] `LC-D13.stale_source_panels` becomes `_covered`.

## Comments
