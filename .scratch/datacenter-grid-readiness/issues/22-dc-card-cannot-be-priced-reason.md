<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# DC Backup card: the "cannot be priced" reason gives no cause

Status: needs-triage
Type: wording
Found by: Logged reviewing the DC cards (DC-05/DC-06); owner list DOCS-1.

## Problem

The reason says that the cost cannot be priced but not why, nor which input to enter (UX: a disabled or missing state names its reason and the fix link). Name the missing input and add the "Missing: <input word> ›" link. Details: ask the coordinator.

## Where to look

`pypsa-gui/frontend/src/pages/DcBackupSection.tsx` line ~257: `The cost against the gensets cannot be priced.`
