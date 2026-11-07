<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# DC Backup card: the "cannot be priced" reason gives no cause

Status: done
Type: wording
Found by: Logged reviewing the DC cards (DC-05/DC-06); owner list DOCS-1.

## Problem

The reason says that the cost cannot be priced but not why, nor which input to enter (UX: a disabled or missing state names its reason and the fix link). Name the missing input and add the "Missing: <input word> ›" link. Details: ask the coordinator.

## Where to look

`pypsa-gui/frontend/src/pages/DcBackupSection.tsx` line ~257: `The cost against the gensets cannot be priced.`

## Comments

### 2026-11-07: triage sweep (FILL-1107)

- Likely files: `pypsa-gui/frontend/src/pages/DcBackupSection.tsx` (~257).
- Blocked by: none.
- Model tier: sonnet.
- Gated: UX (12).
- Size: S.
- Name the missing input and add the "Missing: <input word> ›" link; test it.
