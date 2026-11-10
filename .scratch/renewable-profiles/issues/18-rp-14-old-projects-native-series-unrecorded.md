<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# RP-14: old projects' network-native series are unrecorded

Status: done (RP-18 PR)
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

### 2026-11-10: plan GO from GK B (read-time `unrecorded`), conditions R1–R6

Finding: a pre-RP-14 native column and a raw upload both have no record, and the network `_t` is rewritten from `user_ts` on every autosave, so values cannot tell them apart. Stamp-on-open rejected (could relabel a measured upload). Chosen: a read-time `unrecorded` state, read as measured everywhere, plus one explicit user confirmation.

- R1: every reader of `profile_kind` / `is_measured` lists `unrecorded` as measured (never native); one test each. Readers: `readiness.profile_kind`, `supplied_profiles` (feeds `unplaced_renewables`), `readiness_rows`, `defaults.is_measured` (loads and the batch apply guard in `routers/profiles.py`), `current.describe_current`, frontend `readinessBadge`, `readyRows`, `notReadyReason`.
- R2: a readiness read changes no file and no meta (tested).
- R3: `POST .../profiles/unrecorded/confirm` writes only the meta, refuses a series whose digest changed since the card (whole batch), one undo restores the empty meta; no chat tool reaches it.
- R4: the card lists every series, no truncation; a series without a digest disables the button.
- R5: `unrecorded` and `digest` are typed in `api/profiles.ts`; vitests and `tsc` run.
- R6: mutants on `readiness.py` against `test_profile_unrecorded.py`.
