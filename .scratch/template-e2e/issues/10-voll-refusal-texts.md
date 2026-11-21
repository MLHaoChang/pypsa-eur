<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 10 — The other VOLL refusals still read "requires a VOLL > 0"

Status: ready-for-agent
Owner: template-e2e slice-2 builder (successor of session_01G7qCukBqpE7RUE97mdvTio)
Size: S
Gated: UX (wording); GK B (typed refusal kinds)
Likely files: pypsa-gui/backend/services/adequacy/fmea_sweep_runner.py (l.54); pypsa-gui/backend/services/adequacy/coupling_loop_runner.py (l.206); pypsa-gui/backend/services/adequacy/frontier_loop_runner.py (l.58)
Journey fit: run (Expert and assistant) → the refusal the user reads.

## Problem

#1645 made the reliability study's refusal open with the Solver settings label: "Value of Lost Load (VOLL): must be above 0 to run the reliability study. Set it in Solver settings." (`VOLL_FIELD_LABEL`, pinned to SolverSettings.tsx). The FMEA sweep, the coupling loop and the frontier still say "the <x> requires a VOLL > 0 in solver settings" (RAW-NAMES-1).

## Acceptance

- Each refusal opens with `VOLL_FIELD_LABEL` and names what it would run, in the same form.
- Each refusal keeps its typed kind (e.g. the frontier's `unsupported_scope`), and the tests pinning those kinds pass.
