<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Edit label "Build year (year)" ignores `SELF_EVIDENT_UNITS`

Status: needs-triage
Type: wording
Found by: UIP-01 (#1195), relayed by the coordinator.

## Problem

A year field reads "Build year (year)": the unit repeats the word. `SELF_EVIDENT_UNITS` is meant to suppress such units; the label path does not use it for `build_year`. Fix and add a test (see `AssetFields.fixLink.test.tsx`, which expects "Build year").

## Where to look

`pypsa-gui/frontend/src/utils/assetForm.ts` (`SELF_EVIDENT_UNITS`) and the edit-label builder.
