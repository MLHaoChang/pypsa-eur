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

## Comments

GK A plan verdict (2026-11-20): GO with conditions for PR 1 (engine).
- C1 Preview = apply: a parity test over every template × archetype pack (keep and accept) diffs the user-facing values after apply against `planned_changes` (kind, before_mw, after_mw); two import Links on one PCC pinned (how the summed limit maps to per-Link writes).
- C2 Every caller listed in the PR body; a missing decision reads keep. A model-supplied "accept" reaches the network only through a card that shows before/after and binds the choice in its digest.
- C3 The study record, its inputs/adequacy hash and any certification digest bind `connection_limit` and the MW the study used; a keep study never reads current after a change to accept, or the reverse (both directions tested).
- C4 Result and certification state the limit they ran on; keep on an unbounded connection leaves no weak-grid cap, says so, and is not labelled or certified as the weak-grid case.
- C5 "Differs" is an exact compare; text rounds for display only; a non-MW import bus is stated or refused.
- C6 Old warning strings grepped and every consumer updated; frontend types follow the `planned_changes` shape.
- C7 mutants.py on archetypes.py and the runner branch, plus targeted mutants per guard.
- Off-grid: the site-type choice counts as the accept only when off_grid is the user's own saved choice; a template default, inference or model-supplied off_grid lists `connection_closed` with needs_accept true.

Owner decision (2026-11-20, TE2E-10-OFFGRID-1): an off-grid study's closing of the grid connection is listed before Run in plain words; the off-grid site type the user chose counts as the accept (no extra click). Per GK A, only when off_grid is the user's own saved choice (`planned_changes(..., off_grid_chosen=True)`); otherwise the row needs an accept.
