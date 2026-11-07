<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 38 — The LOLE loop's goal matches the granted goal

Status: needs-triage
Type: task
Gated: yes (GK B)
Model tier: sonnet
Parent: [07-report-handoff-and-pilot.md](07-report-handoff-and-pilot.md) (GK B (34) W1)

## What

In envelope mode, `run_gates._loop_converged` passes a converged `run_coupling_loop` record without comparing the loop's own `target_lole_h` and `goal_sources` with the goal frozen on the granted envelope (`envelope["goal"]`, P5a/F1). A loop that ran against another goal would still pass the result gate.

## Acceptance

- `_loop_converged` returns NoEvidence (or a failing verdict, as GK B decides) when the loop record's `target_lole_h` differs from `envelope["goal"]["value"]`, or when its source differs from the frozen goal's.
- The check applies only where the envelope froze a goal; a run without one keeps today's verdicts.
- A test for each mismatch, plus a hand mutant that drops the comparison.
