<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Study sizing from the request, and the PoC grid wording

Status: needs-triage
Type: task
Review: GK A
Source: te 20 hand-off (#2138, #2139); issue 20 file is owned by a live session (filed 2026-11-28 from the coordinator's follow-up lists)

## What is left

- (a) `eh_study.py` should pass the study request to `sizing_summary_from_network`, and the off-grid check wording needs a pass (te 09+12 area).
- A PoC grid generator without an import Link reads "unbounded" while section 5 lists it as a site asset (risk named in #2138). Unfiled until now.
- Related: te 51 (`import_capacity` ignores `p_max_pu`) is already filed as needs-triage.

## Comments
