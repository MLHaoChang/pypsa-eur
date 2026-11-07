<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# RP-14: `readyRows` excludes `network_native` from bulk Generate

Status: needs-info
Type: bug
Found by: RP-14 (`14-native-series-read-as-measured.md`); owner list DOCS-1.

## Problem

`readyRows` leaves out `network_native` rows, so bulk Generate cannot replace them; the user has to do each by hand. Decide whether bulk Generate should include them (with the replace confirmation). Details: ask the coordinator.

## Where to look

`pypsa-gui/frontend/src/components/series/DefaultsReview.tsx` / `renewableGeneration.ts` (`readyRows`).

## Comments

### 2026-11-07: triage sweep (FILL-1107)

- Not buildable until answered.
- Question (UX (12)): UX (12): should bulk Generate include `network_native` rows (with the replace confirmation), or stay as is and leave them to per-row Generate? Include = S, sonnet, UX (12) review; stay = close as wontfix.
- Blocked by: the answer.
