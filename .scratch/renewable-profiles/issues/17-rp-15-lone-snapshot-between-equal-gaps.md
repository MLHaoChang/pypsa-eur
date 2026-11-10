<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# RP-15: a lone snapshot between equal gaps is refused

Status: done (RP-17 PR)
Type: bug
Found by: RP-15 (`15-constant-step-per-run.md`); owner list DOCS-1.

## Problem

A series with sampled windows where one snapshot sits between two equal gaps is refused as not constant-step. It should be accepted. Add the case to the RP-15 tests. Details: ask the coordinator.

## Where to look

The constant-step-per-run check added by RP-15 (`pypsa-gui/backend/services/profile_gen/`, and `pypsa-gui/frontend/src/components/series/renewableGeneration.ts`).

## Comments

### 2026-11-07: triage sweep (FILL-1107)

- Likely files: `services/profile_gen/` and `frontend/src/components/series/renewableGeneration.ts` (constant-step check).
- Blocked by: none.
- Model tier: sonnet.
- Gated: GK B.
- Size: S.
- Accept the lone snapshot between equal gaps; add the case to the RP-15 tests on both sides.
