<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 30 — A solve's transient input changes leak into the run identity

Status: ready-for-agent
Type: bug
Gated: yes (GK B)
Model tier: opus
Owner decisions: FILL-11-6 (fix the SH-06 (b) bug), USE-2, GK-SPLIT-1, VERDICT-PR
Parent: [06-explore-cases.md](06-explore-cases.md) (found by SH-06 (b))

During a queue solve, `fill_periodized_cost_defaults` (`services/solver/periodized_costs.py`) scales an
extendable asset's `capital_cost` in place on the live network by the horizon factor (1000 → 0.228 for a
2-hour horizon) and reverts it after the LP. `run_envelope.settle` reads the network identity right after
enqueue, without the solve's mutation lock, and records that transient fingerprint as a chain link, so the
next effectful step is refused `brief_stale`. A one-year horizon hides it (factor 1).

## What to build

Identity excludes solver-transient state (GK B ruled out "solve a copy" as unbounded): `run_simulation`
registers a complete snapshot of its network's inputs before its first in-place change, under the mutation
lock; `_network_input_fingerprint` (default and exact) hashes the snapshot, with the reader's own config,
while it is registered; the outermost call's `finally` clears it.

## Acceptance criteria

- [ ] A short-horizon base solve, a settle, then an envelope-admitted effectful step is admitted (red on master: brief_stale).
- [ ] A fingerprint read mid-solve equals the pre-solve and post-solve reads, with every reverting layer the fixture can switch on.
- [ ] Re-entrant: a nested `run_simulation` on the same network never clears the outer snapshot.
- [ ] Registered only while the thread holds the owning ctx's mutation lock (or on a private copy).
- [ ] Mutants m1–m6 each turn a test red.

## Likely files

- `pypsa-gui/backend/services/solver_service.py`
- `pypsa-gui/backend/services/workflow_tools.py`
- `pypsa-gui/backend/tests/test_solve_transient_identity.py`

## Comments

GK B (23) plan ruling, GO WITH CHANGES on option B (2026-11-05):

- B1. List every in-place layer of `run_simulation` (periodized costs, freeze_capacities, apply_energization,
  relax_for_plan, apply_commercial_for_solve, myopic freezes, load scalers, discount_rate/lifetime fills) and
  whether it reverts before the registry clears. A lasting change: show the field is outside
  `_network_input_fingerprint`, or test mid-solve == post-solve with that layer on. T2 runs with every
  reverting layer the short-horizon fixture can switch on.
- B2. Re-entrant registry (myopic driver, AC-PF stage, adequacy loops call `run_simulation`): the outermost
  snapshot stays; an inner `finally` never clears it. Nested-call test.
- B3. Register only for a network whose ctx's mutation lock the current thread holds (a private copy keyed
  by its own object is fine); build the copy fully before inserting it.
- B4. Mid-solve readers keep their own config (m4); default and `exact=True` both hash the snapshot.
- B5. T4 (envelope level, the owner's Done) is required before merge; no explore fixture needed.
- B6. Out of scope: the `statistics()` fill in `routers/simulation.py` (no mutation lock); a separate ticket.
- Mutants m1–m4 plus m5 (inner finally clears) and m6 (no lock check).
