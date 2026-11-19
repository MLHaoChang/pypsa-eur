<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 10 — The reliability study silently changes the user's grid connection

Status: needs-info
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

Checked by code reading only; the app was not re-run. Still true: `services/adequacy/archetypes.py:335` still words the change as raised/lowered. Question for the coordinator: how far should the fix go? (1) Recommended: show each change to a user value as a plain-words line before Run, and refuse to change the connection limit without keep/accept, as the issue suggests. (2) Plain words after the run only, no ask. (3) Do not change the user's connection limit at all; the study uses it as set and reports the shortfall.
