<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Run-metrics partial-convergence warnings (run-metrics/2)

Status: needs-triage
Type: task
Blocked by: 13 (required warning slots, #1831)

## Problem

AA-13 (#1831) added the required warning slot (receipt policy "5") and emits it
from one profile only, `ac-branch-loading/1`: a window with a non-converged or
unknown AC power-flow snapshot keeps an `ac_pf_convergence` warning slot first in
`result_slots`, whatever metrics are asked.

The run-level key results (`run-metrics/1`, `harness/assurance_shapes.py`) do
not warn when the run they summarise only partly converged. GK B4 dropped this
from AA-13 (its slice S2): adding a warning to `run-metrics/1` would change a
reviewed contract, so it needs a new `run-metrics/2` input and profile version.

## Scope

- Decide which run-level sources count as partial convergence (AC power flow
  after LOPF, and any others the run manifest records).
- Add `run-metrics/2` that emits the required warning slot from AA-13, leaving
  `run-metrics/1` as it is; additive contract snapshot only.
- Test first: a partly converged run keeps its warning slot whatever metrics are
  asked, and `request_complete` stays false. Kill a mutant that drops it.
- Browser: reuse AA-13's warning row and `WARNED_LABEL`; UX review.
