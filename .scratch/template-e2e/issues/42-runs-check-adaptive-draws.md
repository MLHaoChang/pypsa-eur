<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 42 — The study start's runs check ignores the MC's adaptive draws

Status: needs-triage
Source: issue 21, GK A (35) review of #1927, ask 4 (option: claim only what holds, file the edge)
Severity: blocks rarely
Journey fit: inputs → run. A study the MC could have decided is refused before it starts.

## What

`eh_study.draws_short_for` takes the requested draws as the run's length. `mc_adequacy` runs with its default `max_draws=MAX_DRAWS` (2000): a first batch that sees a rare non-zero LOLE has an infinite CoV, so the MC keeps drawing up to 2000, and the floor it reports is `w_min / n_total`. Such a run can resolve the goal, or show a fail, at the draw count the start check refused. A run that sees no shortfall converges at its first batch, so the refusal is right for that common case. Since #1927 the refusal says only that ("at N runs, a check that sees no shortfall cannot certify the goal").

## Acceptance

- Decide whether the start check should stay a refusal, become a warning, or base itself on the draws the MC is sure to reach, and record the choice.
- A test runs a network whose first batch sees a shortfall at a refused draw count and pins the chosen behaviour.

Gated: GK A. Blocked by: none.
