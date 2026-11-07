<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# RP-14: old projects' network-native series are unrecorded

Status: ready-for-agent
Type: task
Found by: RP-14 (`14-native-series-read-as-measured.md`); owner list DOCS-1.

## Problem

The import now seeds a record with `provenance: network_native`, but projects created before it have no record, so their network-native series still read as measured. Add a read-time backfill or a one-off migration. Details: ask the coordinator.

## Where to look

`pypsa-gui/frontend/src/components/series/renewableGeneration.ts`, `RenewableReadiness.tsx`; `services/profile_gen/`.

## Comments

### 2026-11-07: triage sweep (FILL-1107)

- Likely files: `renewableGeneration.ts`, `RenewableReadiness.tsx`, `services/profile_gen/`.
- Blocked by: none.
- Model tier: sonnet.
- Gated: GK B; UX (12) if the card changes.
- Size: M.
- Prefer a read-time backfill (no migration); the plan states the choice.
