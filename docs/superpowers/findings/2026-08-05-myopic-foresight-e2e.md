# Myopic foresight — end-to-end examination

**Date:** 2026-08-05
**Scope:** the myopic solve strategy from solver config → LP loop → reported results.
**Verdict:** the optimisation itself is sound. Two defects, both in what the app
*reports* and *warns about* rather than in what it solves. Both fixed.

Probes are in the session scratchpad; each is a self-contained script that
rebuilds its network and prints the numbers quoted here.

---

## D1 — the reported system cost was wrong, in a config-dependent direction

One myopic solve produced three different costs:

| surface | value | vs. truth |
|---|---:|---:|
| solve log (`Summary — objective=…`) | 139,489,176 | −75.5% |
| status bar / solve queue (`_compute_run_objective`) | 324,370,136 | −42.9% |
| Economics tab (`/results/cost_breakdown`) | 568,508,747 | correct |

The perfect-foresight control on the same network returned **522,944,835 on all
three**, which is what makes this myopic-specific rather than a disagreement
about the cost basis.

### Root cause

1. `_freeze_period_capacities` pins capacity built in earlier periods to
   `p_nom = p_nom_opt, p_nom_extendable = False`.
2. PyPSA's investment-cost term charges CAPEX for **extendables only**. A
   non-extendable's capex is meant to arrive via `n.objective_constant`.
3. `n.objective_constant` is **identically zero under
   `multi_investment_periods=True`**. In PyPSA's `define_objective`
   (`optimization/optimize.py`), the multi-invest branch computes
   `weighted_cost` but the `terms.append(...)` that consumes it sits inside the
   single-period `else`. Measured directly — same network shape, flat →
   `objective_constant = 10,000`; multi-invest → `0`.
4. So summing the per-period LP objectives charges each asset's CAPEX **once, in
   its build period**, and never for the rest of its service life.

### Why no correction factor would have worked

With `lf_aggregate_future=True` the error **flips sign**: each iteration's LP
also spans representative future-period snapshots, so those periods' OPEX is
counted once in the lookahead and again when the period is actually solved.
Measured: **−42.9%** without lookahead, **+22.2%** with it.

### Impact

On a 3-period system with growing demand, the status bar made myopic look
**9.9% cheaper** than perfect foresight when it was actually **31.9% more
expensive** — the error inverted the comparison the number exists to support.

### Fix

Report the statistics-based horizon cost for myopic — the same basis the
Economics tab and the Compare tab already use, which is what makes a myopic run
comparable with a full-foresight one.

- New `services/cost_totals.py::horizon_system_cost(n, cfg)`. It takes its
  network and config **explicitly** because the solve-queue dispatcher prices a
  background project with that job's config, outside `solving_context(ctx)` —
  `get_cost_breakdown()` resolves both from ambient state and would have priced
  the foreground project instead.
- `_compute_run_objective(n, cfg=None)` uses it on the myopic branch;
  full-horizon solves keep the LP total (a single LP already prices the whole
  horizon, and the two bases agree there).
- The `Summary` line in `run_simulation` reports the same total, labelled
  `myopic horizon total`, keeping the final-period LP value visible.
- `_myopic_period_objectives` is still populated and still exposed by
  `/results/objective_decomposition`.

`tests/test_cost_totals_contract.py` pins `horizon_system_cost` to
`get_cost_breakdown()["total"]` across flat / multi-period / myopic networks, so
the two implementations cannot drift apart unnoticed.

---

## D2 — myopic silently locked capacity at the first period's optimum

`_freeze_period_capacities` freezes every extendable asset **active** in the
period. An asset left at the default `build_year = 0` is active in every period,
so the first iteration freezes it and **no later period can ever add to it**.

3-period system, demand +44% by 2040:

| | capacity | unserved MWh (2030 / 2035 / 2040) |
|---|---:|---|
| myopic, no vintage bounds | 977 MW, frozen | 47 → 1,756 → **5,183** |
| myopic + per-period vintage bounds | 1,406 MW (977 / +195 / +234) | 47 → 56 → 68 |

Both report `ok/optimal`. The solve log said only "no new extendable assets —
running operational dispatch", which reads like a normal outcome.

This is not a flaw in the freeze mechanism: PyPSA has one capacity variable per
asset, so vintages (one row per period, `build_year = period`) are the only way
an asset can expand in more than one period, and the vintage path works
correctly — the defer/freeze sequencing produced exactly the right per-period
increments above. The defect was that the un-configured case degenerates
**silently**, and `_check_myopic_foresight` had no check for it (it covered
multi-period, SCLOPF and AC PF only).

### Fix

`_check_myopic_capacity_lock` in `services/validation_service.py` emits
`myopic_capacity_locked_after_first_period` (severity **warning** — freezing is
legitimate when the user means "decide the fleet once, then operate it"). It
names the affected assets, counts any that DO have per-period bounds, and skips
assets dated into a later period (those are decided by their own iteration).

Surfaces with no frontend change: `IssuesPanel` renders by severity, and
`run_simulation` streams warnings as `[VALIDATION] WARN`.

---

## Also fixed in passing

`_myopic_period_objectives` was never cleared for non-myopic runs, and it is the
marker every downstream consumer keys off to answer "did this run go myopic?".
Re-solving the same network full-horizon left the previous run's marker in
place, so the full run was reported through the myopic path. `run_simulation`
now clears it on the non-myopic branch.

---

## Checked and found correct

- **The optimisation itself.** Myopic's true cost ≥ perfect foresight's on every
  system tried, as it must be. The reporting was wrong, not the solve.
- **Build decisions.** Each myopic LP weighs annualised CAPEX against one
  period's OPEX at the same period weighting, so the capex:opex ratio is
  consistent with perfect foresight — the decisions are right.
- **Freeze / defer / vintage sequencing.** Each vintage is decided exactly once,
  by the iteration whose `current_period == build_year`.
- **The Compare tab.** Uses the annuitised
  `p_nom_opt × capital_cost × years[P]` path, so it always agreed with Economics.
- **Limited foresight** runs without error.

## Not pursued

- `aggregate_period_snapshots` returned 96 representatives for 96 future
  snapshots (weight ×1.00) on a 48 h/period network — no clustering happened.
  May be correct for that size; worth a look if limited foresight is ever
  expected to shrink the LP.
- The dispatch-only branch (`has_active_extendable == False`) does not call
  `_defer_future_vintage_builds`. Future vintages are inactive in that period so
  their cost coefficient is zero and their `p_nom_opt` is overwritten by their
  own iteration — believed benign, not proven.
