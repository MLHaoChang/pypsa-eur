# The `qa_*.py` drivers had rotted, and were reporting green while doing it

**Date:** 2026-09-05
**Found while:** answering "does it make sense to do e2e QA of the backend decomposition?"
**Status:** resolved on 2026-09-06 — the mechanical rot was fixed first, then the
four auth-blocked drivers were unblocked (see the follow-up at the bottom). One
driver remains blocked, and cannot be fixed by editing.

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


---

# Follow-up, 2026-09-06: the four auth-blocked drivers now run

Option 2 above, taken — and it turned out to be more than a login helper,
because the diagnosis in the table above was incomplete in two ways. Recording
both, since the second is the one that would have bitten anyone who took the
table at face value.

## What the diagnosis got wrong

**It said three drivers "call project handlers directly". Only one does.**
`qa_save_load_roundtrip` calls `save_project` / `load_project` as plain
functions. `qa_rename_project` and `qa_results_summary_compare` drive HTTP for
their assertions and only reach for a direct `save_project()` in their SETUP
helper — so they had BOTH failures at once, the `Depends` sentinel first and
then a wall of 401s.

**It missed that the drivers write into the developer's checkout.**
`qa_layout_persistence` computed its project paths as
`backend/tests/../projects/<name>` and `rmtree`'d them; `qa_rename_project` and
`qa_results_summary_compare` used `routers.projects.PROJECTS_DIR`. Nothing
sandboxed any of it. That is independent of auth and was the more urgent of the
two problems.

## The shape of the fix

`tests/qa_support.py` — new. A driver imports it BEFORE `main`, which is what
pins `DATABASE_URL`, `PROJECTS_ROOT` and `PYPSAGUI_APP_DATA_DIR` at throwaway
locations, seeds an org, and hands back a signed-in `TestClient`.

It gets that by importing `tests/conftest.py` rather than restating it. Two
helpers were extracted there from fixture bodies into plain functions —
`make_auth_db()` and `install_network_into_backend()` — with the fixtures now
thin wrappers around them, so the drivers and the suite share ONE copy of the
sandbox. A second copy would drift, and would drift silently: miss `StaticPool`
and the seeded user simply is not there for the request that needs it, which
reads as an auth bug.

## Three things that only surfaced by running them

**`PyPSAService.set_network()` is not how you install a network any more.** It
writes the process foreground, which a session adopts exactly ONCE. A driver
calling it twice keeps saving the first network while believing it swapped. The
suite's `install_network` fixture already handled this — dropping resident
scratch contexts and un-binding live sessions — which is why extracting it
mattered more than the auth wiring did.

**Reading `PyPSAService.get_network()` reads the wrong context.** The active
project is per session, so a driver checking the process foreground after an
HTTP call is looking at a different context from the one the route just mutated.
`qa_rename_project`'s "in-memory n.name syncs" scenario looked like a broken
product hook; it was the driver looking in the wrong place.
`qa_support.session_context()` resolves the client's own context, the way
`conftest`'s `session_ctx` fixture does.

**Two assertions were pinning pre-tenancy semantics, not product behaviour.**

* `qa_rename_project` asserted "old project dir removed / new project dir
  exists". Directory movement on rename is LOCAL-mode only
  (`project_registry._may_move_directory`); in web mode the directory is
  UUID-keyed and stays put while the row's `name` changes. Rewritten to assert
  what the mode under test actually contracts: the renamed project resolves, its
  directory exists and still holds `network.nc`, and the old name resolves to
  nothing.
* `qa_rename_project` asserted `400` for a traversal-shaped rename. It is `200`
  now, and the traversal is contained by `safe_names.safe_dir_name`. The
  assertion was rewritten to check the property the status code was defending —
  the directory stays inside the projects root — and the contract change is
  written up separately in
  `2026-09-06-rename-accepts-any-name-and-it-reaches-a-header.md`, along with
  the thing that chase turned up: the project name reaches
  `Content-Disposition` unescaped.

`qa_rename_project`'s child-reparent scenario also had to be rebuilt: it wrote
`parent_project` into `metadata.json`, but `_rename_project_db` reparents
`direct_children(db, project)` — a query on `Project.parent_project_id`. The
tree is now built through `POST /{base}/scenarios`, so the DB link exists.

## Where the drivers stand

| driver | before | after |
|---|---|---|
| `qa_rename_project` | crashed in setup, 0 assertions | **23, all pass** |
| `qa_results_summary_compare` | crashed in setup, 0 assertions | **53, all pass** |
| `qa_save_load_roundtrip` | 1 (the crash), 0 real | **52, all pass** |
| `qa_layout_persistence` | 27, 26 fail | **30, all pass** |

All nineteen drivers were then run: **eighteen exit 0**.

## The one that stays blocked

`qa_phase4_compare` still exits 1, and editing cannot change that. It reads two
SOLVED scenario projects by name out of a server on `127.0.0.1:8000`, and its
central check is a concurrency smoke test whose whole point is real HTTP against
real uvicorn — an in-process `TestClient` would not exercise the HDF5 race it
was written to catch. It also acquired a second precondition at the auth
migration that this document did not previously record: even with a server
running, its `urllib` requests are unauthenticated and get 401.

It now says so. A `preflight()` reports the one blocking reason in a single
line — no server, no session, or no such project — instead of twenty-two
identical `Connection refused` entries, and `PYPSA_GUI_QA_COOKIE` lets an
operator hand it a session cookie.

## What is still open

Option 3 — converting these to pytest tests, so they run in the
`gui-backend-tests` CI job — is still open and still contradicts the explicit
decision in `pytest.ini`. It is a smaller job than it was: the drivers now share
one sandbox with the suite, so the conversion is mostly mechanical. But nothing
runs them automatically, which means the rot this document describes can start
over the moment someone stops typing the command by hand.
