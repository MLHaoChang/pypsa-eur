# Model Horizon residuals: stale period budgets and per-period weights — design

**Date:** 2026-08-11
**Status:** approved, ready for planning
**Scope:** the two confirmed defects left open by the 2026-08-09 Model Horizon
defect work (merged as `a6319b43`). Both are backend-only. Neither was caused by
that work; one was made reachable by it.

## Why these two, now

The 2026-08-09 branch closed nine of eleven catalogued defects. Its final
whole-branch review left two real bugs on the table, both deliberately:

1. **B9 was recorded PARTIAL.** The plan's fix — invalidating `solverConfig` when
   investment periods change — is implemented and correct, but it is
   cache-scoped, while the defect as stated in that design was "deleting a period
   leaves its CAPEX budget and load scalers behind". They are still behind, and
   one of them reaches the LP.
2. **A pre-existing weights bug became reachable.** Fixing B5 (per-period
   operational ranges surviving a period edit) removed the very condition that
   was masking it.

Both were scoped out at the time because each is a judgement call rather than a
mechanical fix. This spec makes those calls.

## Defect 1 — a removed period's CAPEX budget still binds the LP

`capex_budget_fn` inside `_wrap_with_capex_budget`
(`pypsa-gui/backend/services/solver_service.py:2655`) iterates the **config
dict**:

```python
for period, budget in normalized.items():
    # constrain every extendable asset with build_year == period
```

Nothing checks that `period` is still in `n.investment_periods`. Remove period
2040 from the horizon and its €500 M cap keeps constraining any extendable asset
that still carries `build_year=2040`.

**The two per-period config maps do not agree with each other.** Per-period load
scaling (`_apply_modelling_assumptions` step 5, around `solver_service.py:4437`)
iterates the **network's** periods instead:

```python
for period in sorted(set(period_level)):
    ...
    raw = car_block.get(str(period))
```

so a stale load-scaler key never matches and is inert at solve time. Only the
CAPEX budget can produce a wrong answer. That asymmetry is the actual defect;
the fix makes the budget behave the way the scalers already do.

### The trap this fix must avoid

`_wrap_with_capex_budget` is **not** gated on multi-period at its call site
(`solver_service.py:742`) — it runs whenever `capex_budget_per_period` is
non-empty, flat network or not. On a flat network, constraining by
`build_year == P` is still meaningful: `build_year` is a per-asset attribute and
does not require investment periods to exist.

So a naive `if period not in n.investment_periods: continue` would **silently
disable every budget on every flat network** — introducing a regression while
fixing a bug. The gate has to be conditional on the network having periods at
all.

### Fix

Resolve the active set once per solve, letting it be `None` when the network has
no investment periods:

```python
active = {int(p) for p in n.investment_periods} if len(n.investment_periods) else None
...
for period, budget in normalized.items():
    if active is not None and period not in active:
        _emit(f"period {period}: budget €{budget:,.0f} ignored — not in the "
              f"model's investment periods {sorted(active)}")
        continue
```

- Multi-period network → a removed period's budget no longer binds.
- Flat network → `active is None`, every budget still applies by `build_year`.
- The skip is **logged** through the wrapper's existing `_emit` helper, so an
  ignored budget is visible in the solver log rather than silently dropped. Same
  principle as the ambiguous-snapshot-key warning added on the previous branch.

**Accepted cost:** entries stay on disk. Re-adding a removed year restores its
old budget. Pruning them at removal time was considered and rejected — it would
couple a network router to SolverConfig and make deletion irreversible.

## Defect 2 — per-period weights reset to 1.0 on distinct calendars

`_capture_snapshot_weights_per_timestep` (`routers/network.py:2968`) captures
only the first period, keyed by timestep:

```python
first_p = sw.index.get_level_values(0)[0]
sw_per_ts = sw.loc[first_p].copy()
```

`_reapply_snapshot_weights` (`:3005`) then reindexes **every** period's timestep
slice against that single capture and fills the misses:

```python
aligned = captured.reindex(ts_slice).fillna(1.0)
```

On a network where 2030 runs on 2019 dates and 2040 on 2020 dates, 2040's slice
matches nothing in a 2019-keyed capture, so `.fillna(1.0)` wipes its weights —
silently under-weighting that period's operational cost in the LP objective.

The helper's own docstring states the assumption that made this safe:

> the canonical multi-period workflow uses the same operational year per period,
> so broadcasting weights matches user intent

That was true only because the B5 bug **enforced** it — every period edit
collapsed all periods onto period 0's calendar, so the timestamp match always
succeeded. With B5 fixed, distinct calendars survive and the assumption is false.

### Fix

`_capture_snapshot_weights_per_timestep` returns a `{period: frame}` map for
MultiIndex input; flat input keeps returning a single frame, unchanged.

`_reapply_snapshot_weights`, per period:

1. that period survived → reindex **its own** captured frame onto its timesteps
2. genuinely new period → reindex the **first** captured period's frame as a
   template
3. anything still unmatched → `1.0`

This is deliberately the same shape as the range fix already in
`set_investment_periods` — survivor keeps its own, newcomer gets a template — so
the two read as one idea rather than two unrelated patches. Replace the stale
docstring justification, whose premise no longer holds.

**Margin deliberately not solved:** a genuinely new period whose calendar matches
nothing in the template still lands on 1.0. There is nothing to inherit in that
case, and the alternative is guessing.

## Testing

Both fixes are backend-only. Tests go in
`pypsa-gui/backend/tests/test_model_horizon_endpoints.py` — 18 tests today, and
the established home for this area since the previous branch.

| Test | Asserts |
|---|---|
| CAPEX, removed period | its budget does not constrain, and the skip reaches the log |
| CAPEX, active period | its budget still binds — the gate is not over-broad |
| CAPEX, flat network | budgets still apply by `build_year` — **the regression guard** |
| Weights, distinct calendars | each surviving period keeps its OWN weights across a period add |
| Weights, new period | inherits the template rather than defaulting to 1.0 |
| Weights, same calendar | today's broadcast behaviour is unchanged |

The flat-network CAPEX test is the most important one in the set: it guards the
exact regression this design is shaped to avoid.

## Out of scope

- Pruning `capex_budget_per_period` / `load_scalers_by_carrier` keys when a
  period is removed. Rejected above; entries persist and re-adding restores them.
- The `n.investment_periods` / `cfg.investment_periods` duality generally.
- Anything on the frontend. Neither defect has a UI surface.
- The Model Horizon page's structural redesign, still deferred.

## Concurrency note

Checked at design time: `feature/local-app-impl` clean at `a6319b43`, ~22 live
Claude sessions, and another session actively committing chat/assistant work to
this same branch. The two files this touches —
`services/solver_service.py` and `routers/network.py` — are outside that
session's recent file set, but re-check before implementing; this checkout is
shared.
