<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# RP-15: a lone snapshot between equal gaps is refused

Status: needs-triage
Type: bug
Found by: RP-15 (`15-constant-step-per-run.md`); owner list DOCS-1.

## Problem

A series with sampled windows where one snapshot sits between two equal gaps is refused as not constant-step. It should be accepted. Add the case to the RP-15 tests. Details: ask the coordinator.

## Where to look

The constant-step-per-run check added by RP-15 (`pypsa-gui/backend/services/profile_gen/`, and `pypsa-gui/frontend/src/components/series/renewableGeneration.ts`).
