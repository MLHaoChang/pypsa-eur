<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# RP-14: `readyRows` excludes `network_native` from bulk Generate

Status: needs-triage
Type: bug
Found by: RP-14 (`14-native-series-read-as-measured.md`); owner list DOCS-1.

## Problem

`readyRows` leaves out `network_native` rows, so bulk Generate cannot replace them; the user has to do each by hand. Decide whether bulk Generate should include them (with the replace confirmation). Details: ask the coordinator.

## Where to look

`pypsa-gui/frontend/src/components/series/DefaultsReview.tsx` / `renewableGeneration.ts` (`readyRows`).
