<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Planning criteria: redispatch as a stage-2 remedial action

Status: needs-triage
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
