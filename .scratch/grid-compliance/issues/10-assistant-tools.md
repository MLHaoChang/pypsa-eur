<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-10 Assistant tools for the compliance workflow

Status: needs-triage
Type: task
Parent: `.scratch/grid-compliance/spec.md`
Blocked by: GC-09 backend (`/api/grid-compliance`)
Gated: **yes** (GK B)
Size: M
Owner decision: GC-SCOPE-1, GC-TEXT-1, GC09-CHAT-1 (2026-11-17: UI_INTERNAL cap 11 → 12 for this one group until these tools land)

**Confidentiality (GC-TEXT-1).** Cite only the public grid code and generic engineering; never
name a project, customer, consultant, EPC or OEM.

## Why

GC-09 ships `/api/grid-compliance` with no chat tool. Its seven routes sit in the
`grid_compliance_workflow` group of `UI_INTERNAL` (`tests/test_route_chat_disposition.py`), whose
cap went from 11 to 12 for it.

## What to build

Chat tools that serve every `/api/grid-compliance` route (read the tracker; set a status; edit
the data list; freeze; log and answer a TSO comment; build a pack), registered in
`harness/catalogue.py` with the capability inventory regenerated, each route in
`SERVED_BY_TOOL`.

## Done when

- `grid_compliance_workflow` is gone from `UI_INTERNAL` and the cap in
  `test_ui_internal_stays_small` is back to 11.
- Each write tool goes through the same guards and lock as its route (tests).
