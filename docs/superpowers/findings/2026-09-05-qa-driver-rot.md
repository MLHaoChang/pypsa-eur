# The `qa_*.py` drivers had rotted, and were reporting green while doing it

**Date:** 2026-09-05
**Found while:** answering "does it make sense to do e2e QA of the backend decomposition?"
**Status:** the mechanical rot is fixed; five drivers remain blocked on preconditions (below)

## What these files are

`pypsa-gui/backend/tests/qa_*.py` — nineteen standalone PASS/FAIL drivers, run as
`python tests/qa_x.py`, each ending in `sys.exit(1 if FAIL else 0)`.

`pytest.ini` excludes them **on purpose**:

```ini
python_files = test_*.py
```

with a comment saying they are "hand-rolled PASS/FAIL drivers, not pytest
functions". That is a reasonable decision, and it has a consequence nobody
tracked: **nothing runs them.** They are not in the pytest suite, and until
2026-09-05 there was no CI for the backend at all. They were last exercised
whenever someone last typed the command by hand.

## What had rotted

### 1. Handlers referenced at an address they left two moves ago

Ten call sites across five drivers did `sim_router.get_cost_breakdown()`,
`sim_router.get_emissions()`, and so on. Those serializers were carved out of
`routers/simulation.py` into `routers/results.py` well before the 2026-09-04
decomposition, so every one of those calls raised
`AttributeError: module 'routers.simulation' has no attribute 'get_...'`.

Eight source-text assertions had the same problem in a worse form: they
`read_text()` a source file and search it for a snippet. When the code moves,
the file still exists, the read succeeds, the snippet is absent, and the
assertion reports a **false failure about the product**:

> `[FAIL] line_duals multiplies congestion rent by years weights — missing year multiplier`

The multiplier was there the whole time; the assertion was reading a file it
had left. One of them — the `co2_by_carrier` lowercasing check — pointed at
`routers/simulation.py` for code that lives in the solver's cost-decomposition
logging and **never** lived in either router. That assertion had never once
checked what it claimed to check.

### 2. A crash counted as nothing at all

Every driver wraps its scenarios like this:

```python
try:
    test_carrier_kpis_multi_period()
except Exception as e:
    print(f"  A3 crashed: {type(e).__name__}: {e}")
```

The `except` prints and moves on. It does **not** touch `FAIL_COUNT`. So a
scenario that dies before its first assertion contributes nothing to the
summary, and the driver exits `0`.

`qa_batch_a` was the clearest case. Two of its three scenarios crashed on the
stale references above, and it printed:

```
Total: 2  Pass: 2  Fail: 0
```

and exited zero. Green, and testing almost nothing. This is the same failure
mode `pixi.toml` already warns about for the desktop tests — "a skipped test
reads as a green suite, which is how a hole this size stays open" — reappearing
in a place nobody was looking.

### 3. One stale expectation, hidden behind a crash

`qa_asset_economics`' multi-period scenario expected `fixed_cost_eur` to be
**1,000,000 €** — the annual figure (10 MW × 100,000 €/MW/yr) — split across
periods as 2/7 and 5/7. The code returns **7,000,000 €**: the annual figure
times `investment_period_weightings["years"]` (2 + 5).

The code is right. `tests/golden/oracle.py::horizon_capex` — the oracle behind
the collected, CI-green `test_golden_economics.py` — is exactly:

```python
return rate_per_mw * p_nom_opt * sum(years)
```

and `services/period_utils.py` calls omitting that multiplier "the ~5x too
small bug class this module exists to prevent". The driver was written against
the pre-fix semantics and never caught up, because it crashed on a stale
reference before reaching the assertion and the crash was not counted.

## What was fixed

| fix | scope |
|---|---|
| stale `sim_router.get_*` → `routers.results` | 10 call sites, 5 files |
| stale source-text paths → the modules that hold the code now | 8 assertions, 2 files |
| `_crashed()` helper: a crashed scenario now counts as a failure | 19 sites, 11 files |
| the three stale horizon-cost expectations | `qa_asset_economics` |

Result, running all nineteen:

| | before | after |
|---|---|---|
| exit 0 | 13 | **14** |
| exit 1 | 6 | 5 |

The count barely moves and badly understates the change, because several of the
"13" were exiting zero without testing anything. The assertions actually
executed:

| driver | before | after |
|---|---|---|
| `qa_batch_a` | 2 assertions, 2 scenarios crashed | **8, all pass** |
| `qa_batch_b` | 6 assertions, 3 fail | **12, all pass** |
| `qa_batch_c` | 22 assertions, 6 fail | **23, all pass** |
| `qa_emissions_per_period` | crashed | **10, all pass** |
| `qa_asset_economics` | 26, silently 0 fail | **26, all pass** |

Every one of these produces identical results on `master` and on the
decomposition branch — checked by running the fixed drivers against both
checkouts. None of this rot was caused by the refactor; the refactor is simply
what caused anyone to run them.

## What is still blocked, and why

Five drivers still exit 1. Each has a single root cause, and none is a product
defect:

| driver | blocked on |
|---|---|
| `qa_rename_project` | calls project handlers directly; they now take a FastAPI-injected `user`/`db`, so `services/project_registry.py::_org_id_or_none` receives a `Depends` sentinel |
| `qa_results_summary_compare` | same |
| `qa_save_load_roundtrip` | same |
| `qa_layout_persistence` | unauthenticated in-process client — every request returns `401 Authentication required` |
| `qa_phase4_compare` | drives HTTP against a **live** backend on `:8000`; `Connection refused` without one |

The first four all date from before the auth/tenancy migration. Making them run
means giving a standalone script the scaffolding `tests/conftest.py` already
provides to pytest — a seeded org, a signed-in session, a DB — which is real
work and duplicates fixtures that exist. The fifth is not fixable by editing at
all; it needs an operator to start a server.

## What to do about it

The mechanical rot is gone, so these drivers now tell the truth about what they
run. The open question is whether they should keep existing in this form.

Three options, in increasing order of effort:

1. **Leave them.** They now fail honestly and loudly. Someone running one gets a
   real answer.
2. **Give the four auth-blocked drivers a shared login helper**, so a standalone
   script can seed an org and sign in the way `conftest.py` does.
3. **Convert them to pytest tests.** They would then get the fixtures for free
   and run in the new `gui-backend-tests` CI job. This contradicts the explicit
   decision recorded in `pytest.ini`, so it should be a deliberate reversal
   rather than a drive-by.

Whatever is chosen, the thing worth keeping is the `_crashed()` change: without
it, any future rot in these files is silent again.
