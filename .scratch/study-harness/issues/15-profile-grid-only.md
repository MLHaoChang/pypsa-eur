<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 15 — Harness profiles: grid-only templates (3bus, ieee14, belgium, ieee39)

Status: ready-for-agent
Type: task
Blocked by: 07
Gated: yes (GK A for the PF and N-1 rules; GK B for the harness parts)
Model tier: sonnet
Owner decisions: SH-PILOT-1
Design: [design.md](../design.md) §9.3 "Grid-only"

## What to build

1. One profile `grid_only@1` serving the four ids; `BLANK_PRESET` scope; no params.
2. Setup refuses `belgium`/`ieee39` with a reason when their source is missing (`_build.py:228, 247`).
3. Result rules: core plus PF convergence, voltage band and line loading ≤ 100 %, gated only on AC PF results the engine labels as PF, never on LOPF values (GK A, ACPF-BUG); N-1 `warn` when the solver config's option is on.
4. No tunables: L2 diagnoses and ends `needs_user`. Explore skipped by default.

## Acceptance criteria

- [ ] `3bus` and `ieee14` runs reach hand-off with the LC-21 network chapters.
- [ ] A seeded overload fails the loading rule and the run asks the user (no agent edit).
- [ ] Without an AC PF result, the PF rules are `not_evaluated`; LOPF flows never satisfy them.

## Likely files

`pypsa-gui/backend/services/run_profiles.py`, `pypsa-gui/backend/services/run_gates.py`, `pypsa-gui/backend/tests/test_run_profile_grid_only.py` (new)

## Comments
