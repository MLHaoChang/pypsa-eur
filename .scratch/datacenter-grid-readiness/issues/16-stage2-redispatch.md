<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Planning criteria: redispatch as a stage-2 remedial action

Status: ready-for-agent
Type: task
Blocked by: none (follows 13, merged in #708)
Gated: **yes** (gridspine)
Model tier: opus
Size: M
Owner decision: DC13-SCOPE (redispatch deferred from 13)

**Confidentiality.** Cite only the public Saudi Arabian Grid Code (SAGC, May 2026) and generic engineering. Never name a client, its network, its MW figures or any scope-of-work text, here, in code, tests, fixtures or PR bodies.

## Why

Stage 2 of `screen_planning` (`gridspine/static/planning_criteria.py`) lets only the named on-load tap changers and switched shunts act. A branch above its normal rating that taps and shunts cannot relieve fails stage 2 (the "stage 2 models no redispatch" line of `PLANNING_LEDGER`), although planning criteria allow generation redispatch after an outage.

## What to build

1. A redispatch control in `Controls`: the units that may move, each within its limits, with a cost or priority order.
2. Stage 2 runs it after the tap and shunt walk, only when a branch is still above its normal rating, and reports the MW moved per unit.
3. Update the `PLANNING_LEDGER` line and the `NO_REDISPATCH` note.

## Acceptance

- A test where taps and shunts cannot relieve an overload but redispatch can: stage 2 passes, and the row reports the redispatch.
- A test where redispatch hits a unit limit: stage 2 still fails, and the note names the limit.
- No redispatch control named gives today's behaviour.

## Triage (gatekeeper, 2026-10-26)

**Status `ready-for-agent`, gated, size M, opus.** Stage 2 is `remedial_walk` in
`gridspine/static/planning_criteria.py`. It walks discrete `_Knob`s one step at a time on the
`(voltage excursion, overload above normal, steps moved)` key. Today nothing outside the tests
calls `screen_planning`, so this is engine-only.

Rulings:

- **Balanced moves only.** Moving one unit lets the slack (the external grid) absorb the change,
  which is an import, not redispatch. Every move is a pair: one named unit up and one named unit
  down by the same MW, so the slack injection stays within a tolerance. The slack is never a
  redispatch unit unless the caller names it, and the row then says so.
- **Control:** `Controls.redispatch`, a tuple of `(unit name, priority)`, plus a step size in MW
  (validated finite and > 0). Moves stay within each unit's `min_p_mw`/`max_p_mw` on the hour's
  dispatch. A unit that is out of service or missing is a ContractError before the base solve,
  like the taps and shunts.
- **Order:** redispatch runs after the tap and shunt walk, only while a branch is still above its
  normal rating. It reuses the walk's key, so a pair that relieves the overload but worsens the
  voltage excursion is not taken. Among equal keys, the lower priority number wins, then the name
  (deterministic). Respect the existing `TAP_ROUNDS`-style cap on solves, with a named bound in the
  ledger.
- **Report:** per-row MW moved per unit (signed) next to the tap/shunt moves. When it fails, a note
  names the binding limit (`unit at max_p_mw`, `no down unit left`, `solve cap reached`).
- **Ledger:** replace the "stage 2 models no redispatch" sentence and `NO_REDISPATCH` only when
  redispatch is named. With no redispatch control, the output is byte-identical to today (a
  regression test compares the rows on the existing fixtures).
- **Tests:** the issue's three acceptance tests, plus balanced moves (slack P before/after within
  tolerance), and that `contingencies`, `dispatch` and the caller's net are not mutated.
- Not in scope: cost-optimal redispatch (OPF) or redispatch in stage 1.
