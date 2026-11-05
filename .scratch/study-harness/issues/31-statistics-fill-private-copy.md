<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 31 — B6: the statistics cost fill runs on a private copy, not the live network

Status: in progress
Type: bug
Blocked by: none
Gated: yes (GK B)
Model tier: opus
Owner decisions: FILL-11-7 (open the B6 fix), GK-SPLIT-1
Related: #1133 (BUG-1, same bug class as SH-06 (b))

GK B logged this while reviewing BUG-1 (#1133). Every report path wrapped its read in `with_periodized_cost_defaults` on the LIVE network, with no lock. For the length of the call that fill scales `capital_cost` and `fom_cost` to the modelled horizon and fills a blank `discount_rate` and `lifetime` in place. A reader that takes no lock and lands in that window hashes the transient values. Examples are the input fingerprint behind `run_envelope.settle` and the chat freshness checks (`workflow_tools._network_input_fingerprint`), and settle's `_identity` read.

## What to build

1. `periodized_cost_view(n, cfg, *, for_back_calculation=False)` in `services/solver/periodized_costs.py`. It runs the existing fill on a private copy of `n` and yields the copy. The copy is `copy.deepcopy` with a memo that shares every dynamic frame and `n._model` by reference, so only the static frames are copied: 5 ms on a 1500-generator, 8760 h network, against 1.4 s for `n.copy()` and 3.8 s for `n.statistics()` on the same network. A view of a network already on the per-horizon basis (inside the solve's own fill, or of another view) inherits that state, so the scaling never applies twice. The copy's `_FOM_SCALED` registration is dropped when the block exits, including when it raises.
2. Every report path reads through the view: `compute_statistics`, `compute_cost_breakdown` (2 blocks), `_fixed_cost_by_period` (objective decomposition), `compute_lcoh`, `horizon_system_cost`, `periodized_capital_costs` and `value_flows._asset_costs`. Their routers and other callers (results, simulation `/asset_costs`, workbook, finance, compare, campus, study runner, EH report) need no change.
3. The in-place `fill_periodized_cost_defaults` / `with_periodized_cost_defaults` stay for the solve, which runs them on the solving network and reverts them after the solve (`solver/assumptions.py`).

Why not the lock: the fingerprint readers take no lock, so locking the fill would protect them only if every reader also took the lock.

## Acceptance criteria

- [x] No report entry point fills the live network: a probe at each fill sees the live network untouched and never the target (red on master for all 7).
- [x] V1: each entry point writes no shared object. Dynamic frames, `snapshot_weightings` and `_model` keep their hash and identity. The fill target owns its static frames and shares the dynamic ones.
- [x] V2: an AST pin allows the in-place fill only in `solver/assumptions.py` (and inside the helper module).
- [x] V3: a `_network_input_fingerprint(ctx)` read taken mid-`compute_statistics` equals the read before (red on master).
- [x] V4: a view inside the solve's in-place fill keeps the single scaling (values asserted); a view of a view is idempotent; the view's registration is dropped, also when the block raises.
- [x] V5: all 7 entry points return master's output on the same fixture (`tests/fixtures/b6_report_outputs_master.json`, written on master 711d18638).
- [x] Mutants: a view that returns `n` itself, and a view that shares the static frames, both go red.

## Likely files

- `pypsa-gui/backend/services/solver/periodized_costs.py`
- `pypsa-gui/backend/services/results/{statistics,cost_breakdown,lcoh,objective_decomposition,value_flows}.py`, `services/cost_totals.py`
- `pypsa-gui/backend/tests/test_statistics_fill_private_copy.py`

## Comments

**GK B (23), plan verdict: GO WITH CHANGES.** A private view rather than the lock, because the fingerprint readers take no lock. The solve's own fill (`assumptions.py`) and the horizon-cost read inside the solve (`solver_service:1548`) are out of scope; #1133 covers those readers while the solve holds the lock. Conditions:

- **V1. Shared frames stay untouched.** At every converted entry point, test that the fill and the code inside the block write no shared object: hash each dynamic frame and `snapshot_weightings`, plus `id(n._model)`, before and after; the copy's static frames are not the live network's and its dynamic frames are. If the fill ever needs a dynamic attribute, copy that frame in the view, never mutate the shared one.
- **V2. No caller left behind.** An AST or grep pin over production code that allows the in-place fill only on the solve path.
- **V3. The bug itself.** A `_network_input_fingerprint(ctx)` read taken mid-call equals the read before, on a short-horizon network where the fill scales `capital_cost`; fails on master.
- **V4. Scaling flags.** A view inside the solve's fill never scales twice (assert values); a view nested in a view is idempotent; the copy's `_FOM_SCALED` entry is dropped with the copy.
- **V5. Same output.** compute_statistics, cost_breakdown, objective_decomposition, lcoh, horizon_system_cost and periodized_capital_costs return master's values on the same fixture.
- Mutants: a view that returns `n` → V3 and the live-unchanged test go red; a view that shares the static frames → V1 goes red.
