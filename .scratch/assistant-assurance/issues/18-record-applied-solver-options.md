<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Record the solver options actually applied at the solve call

Status: needs-triage
Type: task
Found by: GK B (65) on #1680 (issue 10, S2), point (c).

## Problem

`harness/solve_validity.applied_gap` reconstructs the MIP gap that
`solver_service` passed. It reads the persisted `solver_config` and mirrors
`_resolve_mip_kwargs` and the committable-unit condition
(`solver_service.py` near the "MIP knobs" block). The same goes for the
process-UC mode (`process_uc.planning_uc_mode`). Any drift between that mirror
and the solve path, or a path that bypasses it (for example the island
decomposition in `services/solver/island.py`), reports a gap or a solve kind
that the solver never used. A gap that cannot be read today reads
`solve_gap_not_recorded`.

## Acceptance

- The solve call records the effective solver options and the
  linearised/binary commitment in `solver_state`, next to the input fingerprints.
- `solve_validity` reads that record and keeps the mirror only for runs
  sealed before it.
- Coordinate with the owner of `solver_service.py`'s solve calls (#1663,
  DGS-SEED-1).

## Comments
