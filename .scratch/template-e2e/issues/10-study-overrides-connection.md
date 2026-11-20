<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 10 — The reliability study silently changes the user's grid connection

Status: ready-for-agent
Impact: high
Templates: Concentrator, ammonia, every weak-grid template
Found by: Expert-mode walk, 2026-11-17 (master 0ab07339)

## What

Run study > PIPELINE says: "weak_flexible raised import Link 'grid_import' p_nom from 35 MW to 50 MW" (concentrator); "lowered ... from 55 MW to 50 MW" (ammonia); all weak-grid runs "set p_nom_max from inf to N MW".

## Why it matters

The connection limit is the user's most important decision and the study rewrites it with no ask, in raw wording.

## Suggested fix

Show every change the study pack makes to a user value as a decision before Run (keep / accept), in plain words; never override the connection limit silently.

## Triage (2026-11-19, master 9f850d36)

Checked by code reading only; the app was not re-run. `services/adequacy/archetypes.py:335` still words the change as raised/lowered. Decision (owner, 2026-11-19, TE2E-10-ASK-1): before Run, list each change the study makes to a user value in plain words, and require keep/accept before the grid connection limit changes. Acceptance: no change to a user value without it being listed before Run; the connection limit never changes without an accept; wording has no raw ids; a test per weak-grid template family. Gated: GK A and UX. Blocked by: none.
