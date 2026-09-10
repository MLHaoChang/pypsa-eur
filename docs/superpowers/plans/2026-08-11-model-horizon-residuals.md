# Model Horizon Residuals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop a removed investment period's CAPEX budget from constraining the LP, and stop per-period snapshot weights being reset to 1.0 when periods carry distinct calendars.

**Architecture:** Two independent backend fixes, one per file. The first adds a conditional guard inside an existing `extra_functionality` wrapper in `solver_service.py`. The second changes the shape of a capture/reapply helper pair in `routers/network.py` from "one frame keyed by timestep" to "one frame per period", mirroring the per-period rebuild already in `set_investment_periods`. No frontend, no API contract change.

**Tech Stack:** FastAPI + PyPSA + pandas + linopy (backend), pytest.

## Global Constraints

- **Repo root:** `~/Desktop/Code Test/pypsa-eur`. All paths below are relative to it.
- **Branch:** `feature/local-app-impl`. **This checkout is shared with other live sessions** — one was actively committing chat/assistant work throughout the previous branch. Before EVERY commit re-run `git branch --show-current`; do not trust an earlier answer. Commit path-limited (`git commit <paths>`), never `git add -A`.
- **Every task is TDD.** Write the failing test, run it, capture the real failing output, then implement. Each task report carries a **TDD Evidence** section with the RED command and its real output, then the GREEN command and its real output. There is no "too simple to test" exemption.
- **RED must be behavioural, not a missing symbol.** Both tasks modify existing functions, so an `ImportError`/`AttributeError` RED is not acceptable evidence — the failing assertion must show the wrong *value* (a budget that still binds, a weight that reset to 1.0).
- **Backend test command** (from repo root): `pixi run python -m pytest pypsa-gui/backend/tests/test_model_horizon_endpoints.py -v`
- **Full backend suite** (from repo root): `pixi run python -m pytest pypsa-gui/backend/tests -q -p no:warnings`
- **Known-failing baseline — ignore these 7:** `ModuleNotFoundError: No module named 'webview'` in `test_desktop_bootstrap.py` (3), `test_desktop_downloads.py` (2), `test_packaging_requirements.py` (1), `test_shutdown.py` (1). Any OTHER failure is yours.
- **This suite prints no aggregate count line.** Confirmed across five runs. Judge by pytest's `short test summary info` block — it lists every FAILED and every ERROR — and by the run reaching `[100%]`. Do not fabricate a passed-count.
- **Never hardcode an interpreter path.** Use `pixi run python`.
- **Do not prune** `capex_budget_per_period` / `load_scalers_by_carrier` keys on period removal. Explicitly rejected in the spec; entries persist and re-adding a year restores them.
- End every commit message with: `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `pypsa-gui/backend/services/solver_service.py` | `capex_budget_fn` gains a conditional stale-period guard. | 1 |
| `pypsa-gui/backend/routers/network.py` | `_capture_snapshot_weights_per_timestep` / `_reapply_snapshot_weights` become per-period. | 2 |
| `pypsa-gui/backend/tests/test_model_horizon_endpoints.py` | **Extend** (18 tests today). Both tasks append here. | 1, 2 |

Existing helpers in that test file to REUSE rather than re-create: `_multi_index(periods, block)` (`:161`), `_multi_index_per_period(pairs)` (`:259`), `_distinct_range_network()` (`:271`) — three periods on three different operational years, exactly the fixture Task 2 needs — and `_period_blocks(n)` (`:250`). Fixtures available: `client`, `install_network`, `session_ctx`, plus `build_network` from `tests.conftest`.

**Trap that already bit the previous branch:** calling `PyPSAService.get_network()` from a test body AFTER a request resolves the WRONG network — `_ensure_active()` reads a request-scoped ContextVar, and after the request it falls through to `cls._active`, which `adopt_process_foreground()` has already cleared, self-healing a fresh empty network. Use the `session_ctx(client).network` pattern, as the Task-4 tests at `:286-360` do.

---

### Task 1: A removed period's CAPEX budget stops binding the LP

**Files:**
- Modify: `pypsa-gui/backend/services/solver_service.py` — inside `capex_budget_fn`, which begins at `:2706` within `_wrap_with_capex_budget` (`:2655`)
- Modify: `pypsa-gui/backend/tests/test_model_horizon_endpoints.py` (append)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: no new symbols. `_wrap_with_capex_budget`'s signature is unchanged; only `capex_budget_fn`'s per-period loop gains a guard.

**Background the implementer needs.** `_wrap_with_capex_budget` composes a per-period CAPEX cap into the LP through `extra_functionality`. Its inner `capex_budget_fn` iterates `normalized.items()` — the **config dict**, `cfg.capex_budget_per_period`, keyed by period year — and for each entry constrains every extendable asset whose `build_year` equals that period. Nothing checks the period still exists in `n.investment_periods`, so a budget for a deleted period keeps binding.

Per-period **load scaling** already does the opposite (`_apply_modelling_assumptions` step 5, around `:4437`): it iterates `sorted(set(period_level))` — the network's actual periods — so a stale key there is inert. This task makes the budget behave the same way.

**The trap.** `_wrap_with_capex_budget` is **not** multi-period gated at its call site (`:742`) — it runs whenever `capex_budget_per_period` is non-empty, flat network or not. On a flat network, constraining by `build_year == P` is legitimate: `build_year` is a per-asset attribute that does not require investment periods to exist. A naive `if period not in n.investment_periods: continue` would silently disable every budget on every flat network. The guard must therefore be conditional on the network HAVING periods.

- [ ] **Step 1: Write the three failing tests**

Append to `pypsa-gui/backend/tests/test_model_horizon_endpoints.py`:

```python
# ── CAPEX budget: stale investment periods must not constrain the LP ───────
# `capex_budget_fn` iterated cfg.capex_budget_per_period (the CONFIG dict) and
# constrained every extendable asset with build_year == period, without ever
# checking the period still exists in n.investment_periods. Remove 2040 from the
# horizon and its cap kept binding. Per-period load scaling already iterates the
# NETWORK's periods, so the two config maps disagreed; this aligns them.


def _budget_network(periods, build_year, p_nom_max=1000.0):
    """
    One extendable generator with an explicit build_year, on a MultiIndex
    network spanning `periods`. The generator is deliberately cheap and the
    load large, so an UNCONSTRAINED solve builds up to p_nom_max — which makes
    a binding budget observable as a smaller p_nom_opt.
    """
    n = pypsa.Network()
    block = pd.date_range("2024-01-01", periods=2, freq="h")
    n.set_snapshots(_multi_index(periods, block))
    n.investment_periods = periods
    n.add("Bus", "B1")
    n.add("Load", "L1", bus="B1", p_set=500.0)
    n.add(
        "Generator", "G1", bus="B1",
        p_nom_extendable=True, p_nom_max=p_nom_max,
        build_year=build_year, lifetime=100,
        capital_cost=1.0, marginal_cost=1.0,
    )
    n.generators.loc["G1", "overnight_cost"] = 1.0
    return n


def _solve_and_get_p_nom_opt(client, budgets):
    """PUT the budget map, run a solve, return G1's optimised capacity."""
    r = client.put("/api/simulation/solver_config",
                   json={"capex_budget_per_period": budgets})
    assert r.status_code == 200, r.text
    r = client.post("/api/simulation/run")
    assert r.status_code in (200, 202), r.text
    _wait_for_solve(client)
    return float(session_ctx(client).network.generators.at["G1", "p_nom_opt"])


def test_a_budget_for_a_period_not_in_the_horizon_does_not_constrain(
    client, install_network, session_ctx,
):
    # 2040 is NOT an investment period, but the generator still carries
    # build_year=2040 and the config still holds 2040's budget.
    install_network(_budget_network([2030, 2050], build_year=2040))
    p_nom = _solve_and_get_p_nom_opt(client, {"2040": 10.0})
    assert p_nom > 10.0, (
        "a budget for a period outside n.investment_periods must not bind; "
        f"got p_nom_opt={p_nom}"
    )


def test_a_budget_for_a_live_period_still_binds(client, install_network, session_ctx):
    # The gate must not be over-broad: 2030 IS a period, so its cap applies.
    install_network(_budget_network([2030, 2050], build_year=2030))
    p_nom = _solve_and_get_p_nom_opt(client, {"2030": 10.0})
    assert p_nom <= 10.0 + 1e-6, (
        f"a live period's budget must still constrain; got p_nom_opt={p_nom}"
    )


def test_a_budget_still_binds_on_a_flat_network(client, install_network, session_ctx):
    # REGRESSION GUARD. _wrap_with_capex_budget is not multi-period gated, and
    # build_year is meaningful without investment periods. A naive membership
    # check against an empty n.investment_periods would silently disable every
    # budget on every flat network.
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2024-01-01", periods=2, freq="h"))
    n.add("Bus", "B1")
    n.add("Load", "L1", bus="B1", p_set=500.0)
    n.add(
        "Generator", "G1", bus="B1",
        p_nom_extendable=True, p_nom_max=1000.0,
        build_year=2030, lifetime=100,
        capital_cost=1.0, marginal_cost=1.0,
    )
    n.generators.loc["G1", "overnight_cost"] = 1.0
    install_network(n)

    p_nom = _solve_and_get_p_nom_opt(client, {"2030": 10.0})
    assert p_nom <= 10.0 + 1e-6, (
        f"flat-network budgets must still apply by build_year; got p_nom_opt={p_nom}"
    )
```

You also need a solve-completion helper. Add it beside the others:

```python
def _wait_for_solve(client, timeout_s: float = 120.0) -> None:
    """
    Block until `/api/simulation/status` leaves the running state.

    The solve runs on a worker thread (`n.optimize()` is blocking, so the
    backend never runs it inline). Poll rather than sleeping a fixed interval.
    """
    import time
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        st = client.get("/api/simulation/status").json()
        if st.get("status") not in ("running", "queued"):
            return
        time.sleep(0.25)
    raise AssertionError("solve did not finish within the timeout")
```

**Before running:** verify the three endpoint shapes this uses actually match this codebase — the solver-config PUT path and body key, the run endpoint's path and success code, and the status endpoint's field name and running-state values. Read `pypsa-gui/backend/routers/simulation.py` and adjust the helpers to what is really there. Do NOT adjust the assertions; only the plumbing.

- [ ] **Step 2: Run the tests to verify they fail — behaviourally**

Run:
```bash
pixi run python -m pytest pypsa-gui/backend/tests/test_model_horizon_endpoints.py -v -k "budget"
```
Expected: `test_a_budget_for_a_period_not_in_the_horizon_does_not_constrain` FAILS with `p_nom_opt` pinned at ~10.0 — the stale 2040 budget binding. The other two PASS already (they describe behaviour that is currently correct and must stay correct). Paste the real assertion output.

If the first test PASSES unexpectedly, stop and report: it means the LP is not reaching the constraint at all and the fixture is not exercising the defect. Do not proceed to implement against a test that cannot fail.

- [ ] **Step 3: Add the conditional guard**

In `pypsa-gui/backend/services/solver_service.py`, inside `capex_budget_fn`, immediately after `def capex_budget_fn(n, snapshots):` and before the `for period, budget in normalized.items():` loop:

```python
        # Budgets are keyed by period year. On a MULTI-PERIOD network a key
        # that is no longer in n.investment_periods is stale — the user removed
        # that year from the horizon — and must not constrain the LP. Per-period
        # load scaling already ignores stale keys (it iterates the network's
        # periods, not the config's); this makes the budget agree.
        #
        # `active is None` on a FLAT network, where every budget still applies:
        # _wrap_with_capex_budget is not multi-period gated, and `build_year` is
        # a per-asset attribute that does not need investment periods to exist.
        # A flat membership check here would silently disable every flat-network
        # budget — a regression, not a fix.
        active = (
            {int(p) for p in n.investment_periods}
            if len(n.investment_periods) > 0
            else None
        )
```

Then guard the loop body — make this the first statement inside `for period, budget in normalized.items():`:

```python
            if active is not None and period not in active:
                _emit(
                    f"period {period}: budget EUR {budget:,.0f} ignored — not in "
                    f"the model's investment periods {sorted(active)}"
                )
                continue
```

The `_emit` helper is already defined just above `capex_budget_fn` and prefixes `[BUDGET]`.

- [ ] **Step 4: Run the tests to verify they pass**

Run:
```bash
pixi run python -m pytest pypsa-gui/backend/tests/test_model_horizon_endpoints.py -v -k "budget"
```
Expected: all three PASS.

- [ ] **Step 5: Prove the guard is not over-broad**

Temporarily change the guard to `if active is not None:` (dropping the membership test, so EVERY budget is skipped on a multi-period network) and re-run. Expected: `test_a_budget_for_a_live_period_still_binds` now FAILS. **Restore the line and verify with `git diff` that `solver_service.py` shows only your intended change.** Record both the mutation output and the restoration check in your report.

- [ ] **Step 6: Run the full backend suite**

Run:
```bash
pixi run python -m pytest pypsa-gui/backend/tests -q -p no:warnings
```
Expected: run reaches `[100%]`; the summary block lists exactly the 7 baseline `webview` FAILED entries and zero ERROR entries. `_wrap_with_capex_budget` is reachable from every solve path, so this suite is the real check.

- [ ] **Step 7: Commit**

```bash
git branch --show-current   # confirm feature/local-app-impl
git commit pypsa-gui/backend/services/solver_service.py \
           pypsa-gui/backend/tests/test_model_horizon_endpoints.py \
  -m "fix(gui): a removed period's CAPEX budget stops constraining the LP

capex_budget_fn iterated the config dict and constrained every extendable
asset with build_year == period, never checking the period still existed
in n.investment_periods. Remove 2040 from the horizon and its cap kept
binding. Per-period load scaling already iterates the network's periods,
so the two per-period config maps disagreed about what a stale key means.

The guard is conditional on the network HAVING periods: _wrap_with_capex_
budget is not multi-period gated, and on a flat network build_year is
meaningful on its own, so a flat membership check would have disabled
every flat-network budget. Skips are logged rather than silent.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Per-period snapshot weights survive distinct calendars

**Files:**
- Modify: `pypsa-gui/backend/routers/network.py:2968-3002` (`_capture_snapshot_weights_per_timestep`) and `:3005-3046` (`_reapply_snapshot_weights`)
- Modify: `pypsa-gui/backend/tests/test_model_horizon_endpoints.py` (append)

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `_capture_snapshot_weights_per_timestep(n) -> pd.DataFrame | dict[int, pd.DataFrame] | None` — a **dict keyed by period** on MultiIndex input, a single frame on flat input (unchanged), `None` when weights are all-default. `_reapply_snapshot_weights(n, captured) -> None` accepts either shape.

**Background the implementer needs.** These two helpers bracket `n.set_snapshots(...)`: PyPSA's setter reindexes `_snapshots_data` with `fill_value=default_snapshot_weightings`, so any custom weights are lost across a snapshot reshape unless captured and re-applied. They are called from two places — `set_multi_period_snapshots` (`:1522`/`:1530`) and `set_investment_periods` (`:1797`/`:1805`).

Today the capture takes only the first period, keyed by timestep:

```python
first_p = sw.index.get_level_values(0)[0]
sw_per_ts = sw.loc[first_p].copy()
```

and the reapply reindexes **every** period's timestep slice against that one capture, `.fillna(1.0)`. On a network where 2030 runs on 2019 dates and 2040 on 2020 dates, 2040 matches nothing and its weights are wiped.

The helper's docstring justifies this with "the canonical multi-period workflow uses the same operational year per period, so broadcasting weights matches user intent". That was true only because a now-fixed bug **enforced** it — every period edit collapsed all periods onto period 0's calendar. Replace that justification; the premise is gone.

- [ ] **Step 1: Write the three failing tests**

Append to `pypsa-gui/backend/tests/test_model_horizon_endpoints.py`:

```python
# ── Per-period snapshot weightings across a period edit ───────────────────
# _capture_snapshot_weights_per_timestep captured ONLY period 0, keyed by
# timestep; _reapply_snapshot_weights reindexed every period against it and
# .fillna(1.0)'d the misses. Once periods carry DISTINCT calendars — which the
# per-period range fix made survivable — periods 2+ matched nothing and their
# weights were silently reset to 1.0, under-weighting their OPEX in the LP.


def _weights_by_period(n) -> dict[int, list[float]]:
    """{period: [objective weight per timestep]} for a MultiIndex network."""
    sw = n.snapshot_weightings["objective"]
    out: dict[int, list[float]] = {}
    for (p, _ts), v in sw.items():
        out.setdefault(int(p), []).append(float(v))
    return out


def test_each_period_keeps_its_own_weights_when_calendars_differ(
    client, install_network, session_ctx,
):
    # 2030→2019, 2040→2020, 2050→2021 — three distinct operational years.
    n = _distinct_range_network()
    # Give every period a DIFFERENT, non-default weight so a reset to 1.0 and a
    # broadcast from period 0 are both distinguishable from correct behaviour.
    marks = {2030: 2.0, 2040: 3.0, 2050: 4.0}
    for (p, ts) in n.snapshots:
        n.snapshot_weightings.loc[(p, ts), "objective"] = marks[int(p)]
    live = install_network(n)
    before = _weights_by_period(live)

    r = client.post("/api/network/investment_periods",
                    json={"periods": [2030, 2040, 2050, 2060]})
    assert r.status_code == 200, r.text

    after = _weights_by_period(session_ctx(client).network)
    assert after[2030] == before[2030]
    assert after[2040] == before[2040], (
        "period 2040 must keep its OWN weights, not period 2030's and not 1.0; "
        f"got {after[2040]}, expected {before[2040]}"
    )
    assert after[2050] == before[2050]


def test_a_new_period_inherits_the_template_rather_than_defaulting(
    client, install_network, session_ctx,
):
    n = _distinct_range_network()
    for (p, ts) in n.snapshots:
        n.snapshot_weightings.loc[(p, ts), "objective"] = 2.0
    install_network(n)

    r = client.post("/api/network/investment_periods",
                    json={"periods": [2030, 2040, 2050, 2060]})
    assert r.status_code == 200, r.text

    after = _weights_by_period(session_ctx(client).network)
    assert 2060 in after
    assert all(v == 2.0 for v in after[2060]), (
        "a genuinely new period inherits the first captured period's weights as "
        f"a template, since its range is templated from it too; got {after[2060]}"
    )


def test_same_calendar_broadcast_is_unchanged(client, install_network, session_ctx):
    # The common workflow: one operational year replicated under every period.
    # Behaviour here must not change.
    block = pd.date_range("2024-01-01", periods=2, freq="h")
    n = pypsa.Network()
    n.set_snapshots(_multi_index([2030, 2040], block))
    n.investment_periods = [2030, 2040]
    n.add("Bus", "B1")
    for (p, ts) in n.snapshots:
        n.snapshot_weightings.loc[(p, ts), "objective"] = 7.0
    install_network(n)

    r = client.post("/api/network/investment_periods",
                    json={"periods": [2030, 2040, 2050]})
    assert r.status_code == 200, r.text

    after = _weights_by_period(session_ctx(client).network)
    for period in (2030, 2040, 2050):
        assert all(v == 7.0 for v in after[period]), (
            f"same-calendar broadcast regressed for {period}: {after[period]}"
        )
```

- [ ] **Step 2: Run the tests to verify they fail — behaviourally**

Run:
```bash
pixi run python -m pytest pypsa-gui/backend/tests/test_model_horizon_endpoints.py -v -k "weights or calendar or template"
```
Expected: `test_each_period_keeps_its_own_weights_when_calendars_differ` FAILS showing `after[2040]` as `[1.0, 1.0, ...]` instead of `[3.0, 3.0, ...]` — the reset. Paste the real assertion diff.

- [ ] **Step 3: Make the capture per-period**

In `pypsa-gui/backend/routers/network.py`, replace the body of `_capture_snapshot_weights_per_timestep` (keeping the function name) with:

```python
def _capture_snapshot_weights_per_timestep(n):
    """
    Snapshot ``n.snapshot_weightings`` so it survives a subsequent
    ``n.set_snapshots(mi)`` reset.

    PyPSA's ``set_snapshots`` reindexes ``_snapshots_data`` with
    ``fill_value=default_snapshot_weightings``, so custom weights (a
    representative-week factor of 52.14, half-hour resolution 0.5, …) are
    silently lost across a reshape and the LP's ``n.nyears`` collapses.

    Returns:
      • MultiIndex network → ``{period: frame}``, each frame indexed by that
        period's own timesteps. Capturing only period 0 and broadcasting it was
        correct only while a now-fixed bug forced every period onto period 0's
        calendar; with distinct calendars survivable, a broadcast wipes every
        period whose dates differ.
      • Flat network → a single frame indexed by timestep.
      • ``None`` when every weight is the PyPSA default 1.0 — nothing to
        preserve, and skipping saves a needless write.
    """
    sw = n.snapshot_weightings.copy()
    if sw.empty:
        return None
    if (sw == 1.0).all().all():
        return None
    if isinstance(sw.index, pd.MultiIndex):
        captured: dict[int, pd.DataFrame] = {}
        level0 = sw.index.get_level_values(0)
        for p in level0.unique():
            block = sw[level0 == p].copy()
            # Drop the period level so the frame is keyed by timestep alone —
            # the reapply path reindexes it against the NEW index's timesteps.
            block.index = pd.DatetimeIndex(block.index.get_level_values(1))
            block.index.name = "snapshot"
            captured[int(p)] = block
        return captured
    sw.index.name = "snapshot"
    return sw
```

Note the all-default check moved BEFORE the MultiIndex branch — it applies to both shapes and is cheaper first.

- [ ] **Step 4: Make the reapply per-period**

Replace the body of `_reapply_snapshot_weights` (keeping the name) with:

```python
def _reapply_snapshot_weights(n, captured) -> None:
    """
    Write captured weights back onto ``n.snapshot_weightings`` after
    ``set_snapshots`` has rebuilt the index. Must run AFTER the reshape. Holds
    no lock — caller's responsibility.

    Per period, in order:
      1. that period survived the reshape → reindex ITS OWN captured frame
      2. genuinely new period → reindex the FIRST captured period's frame as a
         template, matching how ``set_investment_periods`` templates a new
         period's operational range from the first existing one
      3. anything still unmatched → 1.0

    Accepts either capture shape: ``{period: frame}`` from a MultiIndex source
    or a single frame from a flat one.
    """
    if captured is None:
        return
    idx = n.snapshots
    if isinstance(idx, pd.MultiIndex):
        if isinstance(captured, dict):
            if not captured:
                return
            template = captured[sorted(captured)[0]]
        else:
            # Flat source promoted to MultiIndex: one frame for every period.
            template = captured
            captured = {}
        chunks = []
        for p in idx.get_level_values(0).unique():
            mask = idx.get_level_values(0) == p
            ts_slice = idx[mask].get_level_values(1)
            source = captured.get(int(p), template)
            aligned = source.reindex(ts_slice).fillna(1.0)
            aligned.index = idx[mask]
            chunks.append(aligned)
        new_sw = pd.concat(chunks)
    else:
        # MultiIndex source demoted to flat: use the first captured period.
        flat_source = captured[sorted(captured)[0]] if isinstance(captured, dict) else captured
        new_sw = flat_source.reindex(idx).fillna(1.0)
        new_sw.index = idx
    # The setter validates df.index.equals(n.snapshots); we built new_sw against
    # n.snapshots so it passes. Assign per column in case a future PyPSA adds a
    # weight column we did not capture.
    for col in new_sw.columns:
        if col in n.snapshot_weightings.columns:
            try:
                n.snapshot_weightings[col] = new_sw[col].values
            except Exception:
                # Column-level failure must not break the whole solve; the 1.0
                # default is an acceptable fallback for that column.
                pass
```

- [ ] **Step 5: Run the tests to verify they pass**

Run:
```bash
pixi run python -m pytest pypsa-gui/backend/tests/test_model_horizon_endpoints.py -v -k "weights or calendar or template"
```
Expected: all three PASS.

- [ ] **Step 6: Run the whole file, then the full suite**

Run:
```bash
pixi run python -m pytest pypsa-gui/backend/tests/test_model_horizon_endpoints.py -v
```
Expected: 24 tests pass (18 existing + 3 from Task 1 + 3 here). The four Task-4 range tests and the two `had_custom_weights` tests exercise these same two helpers, so they are your regression net — if any of them breaks, the capture shape change is wrong.

Run:
```bash
pixi run python -m pytest pypsa-gui/backend/tests -q -p no:warnings
```
Expected: `[100%]`, exactly the 7 baseline `webview` FAILED entries, zero ERROR entries.

- [ ] **Step 7: Prove the fix is load-bearing**

Temporarily change `source = captured.get(int(p), template)` to `source = template` — restoring the old broadcast — and re-run the three new tests. Expected: `test_each_period_keeps_its_own_weights_when_calendars_differ` FAILS again. **Restore, then verify with `git diff` that `network.py` carries only your intended change.** Record both outputs.

- [ ] **Step 8: Commit**

```bash
git branch --show-current   # confirm feature/local-app-impl
git commit pypsa-gui/backend/routers/network.py \
           pypsa-gui/backend/tests/test_model_horizon_endpoints.py \
  -m "fix(gui): per-period snapshot weights survive distinct calendars

_capture_snapshot_weights_per_timestep captured only period 0, keyed by
timestep, and _reapply_snapshot_weights reindexed every period against it
and filled the misses with 1.0. Once periods carry distinct operational
years, periods 2+ matched nothing and their weights were silently reset —
under-weighting their OPEX in the LP objective.

The helper's docstring justified the broadcast by saying every period
shares one operational year. That held only because a now-fixed bug
enforced it: every period edit collapsed all periods onto period 0's
calendar, so the timestamp match always succeeded.

Capture per period; a survivor keeps its own weights, a genuinely new
period inherits the first captured period's as a template — the same
shape set_investment_periods already uses for operational ranges.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Verification: the whole set

- [ ] **Both suites, recorded output**

```bash
pixi run python -m pytest pypsa-gui/backend/tests -q -p no:warnings
cd pypsa-gui/frontend && PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx vitest run
```

The frontend is untouched by this plan; run it once to confirm that is true (expect 103 files / 867 tests).

- [ ] **State the artifact status plainly**

Source-only, like the branch before it. The `.app` in `/Applications` and any DMG remain stale until someone runs `bash pypsa-gui/build-macos.sh`. Say so in the final report rather than implying these fixes reach the packaged app.

---

## Self-Review

**Spec coverage:** Defect 1 (stale CAPEX budget) → Task 1, including the flat-network regression guard the spec names as the most important test. Defect 2 (per-period weights) → Task 2, all three spec'd tests present. The spec's "out of scope" list is honoured: no pruning, no frontend, no `cfg.investment_periods` work.

**Placeholder scan:** no TBD/TODO, no "handle edge cases", every code step carries real code. One deliberate instruction to *verify before running* rather than assume — Task 1 Step 1 tells the implementer to check the three simulation endpoint shapes against `routers/simulation.py` and adjust the plumbing helpers. That is a real gap in my knowledge, stated as such, with the assertions fenced off from adjustment so it cannot become a licence to weaken the test.

**Type consistency:** `_capture_snapshot_weights_per_timestep` returns `dict[int, pd.DataFrame] | pd.DataFrame | None` and `_reapply_snapshot_weights` accepts exactly those three shapes — including the flat→multi promotion case (single frame, MultiIndex target) and the multi→flat demotion case (dict, flat target), both of which are live call paths in `set_investment_periods`. Test helper names (`_weights_by_period`, `_budget_network`, `_wait_for_solve`, `_solve_and_get_p_nom_opt`) are defined once and used consistently; reused existing helpers (`_multi_index`, `_distinct_range_network`) are cited with their current line numbers.
