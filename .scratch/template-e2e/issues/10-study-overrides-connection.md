<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 10 — The reliability study silently changes the user's grid connection

Status: needs-triage
Impact: high
Templates: Concentrator, ammonia, every weak-grid template
Found by: Expert-mode walk, 2026-11-17 (master 0ab07339)

## What

Run study > PIPELINE says: "weak_flexible raised import Link 'grid_import' p_nom from 35 MW to 50 MW" (concentrator); "lowered ... from 55 MW to 50 MW" (ammonia); all weak-grid runs "set p_nom_max from inf to N MW".

## Why it matters

The connection limit is the user's most important decision and the study rewrites it with no ask, in raw wording.

## Suggested fix

Show every change the study pack makes to a user value as a decision before Run (keep / accept), in plain words; never override the connection limit silently.
