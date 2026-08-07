# Post-Myopic Close-Out Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the three items left open after the myopic-foresight work — verify the four shipped fixes actually behave correctly in the running app, eliminate the last known Capacity-vs-Economics CAPEX divergence, and move the session's hard-won pitfall notes somewhere that survives a worktree reset.

**Architecture:** Three independent tasks against the existing PyPSA Studio backend/frontend. Task 1 is verification-only (drives the installed `.app`, changes no source). Task 2 is a one-line behaviour change in `routers/compare.py` guarded by a new regression test that must fail first. Task 3 is a documentation move with no code impact.

**Tech Stack:** Python 3.13 / FastAPI / PyPSA 1.x / linopy / HiGHS backend; React + TypeScript + Vite + Vitest frontend; pixi for environments; PyInstaller for the macOS bundle.

## Global Constraints

- **A second Claude session shares this worktree.** Before any commit, re-run `git branch --show-current` — do not trust an earlier answer. Check `git status --porcelain` and only ever stage your own files.
- **Stage files explicitly by path.** NEVER `git add -A`, `git add .`, or `git commit -a`.
- **NEVER run** `git checkout`, `git stash`, `git reset`, or `git clean` — they would destroy the other session's uncommitted work.
- **Backend tests run in the `test` pixi env only:** `pixi run gui-tests` from the repo root, or `.pixi/envs/test/bin/python -m pytest` from `pypsa-gui/backend`. A bare `pytest` resolves the `default` env, which has no `pywebview`, and produces fake failures.
- **Do not pass `-q` to pytest.** `pytest.ini` already sets `addopts = -q`; a second `-q` suppresses the summary line entirely. Use `--no-header -v` or `-p no:warnings`.
- **Node comes from pixi:** `.pixi/envs/default/bin/node`. There is no system `npx`. Prefix with `export PATH="<repo>/.pixi/envs/default/bin:$PATH"`.
- **Commit message trailer**, on every commit:
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`
- Do not rebuild or reinstall the macOS app as part of this plan. The controller decides when to rebuild.

---

### Task 1: Verify the four shipped fixes in the running app

**Files:**
- Create: `pypsa-gui/backend/smoke/verify_myopic_ui.py` (driver script; lives under `smoke/`, NOT `tests/`, because it starts a live backend and must never be collected by the unit suite)
- Modify: none
- Test: this task IS the test — its deliverable is evidence, not code

**Interfaces:**
- Consumes: the installed app at `/Applications/PyPSA Studio.app` (already built and hash-verified), and the backend package under `pypsa-gui/backend`
- Produces: `docs/superpowers/findings/2026-08-06-ui-verification.md` — a findings file recording, per fix, the observed values and a PASS/FAIL verdict

**Background the implementer needs.** Four fixes shipped tonight and none has been exercised through a running system:

1. `fix(gui): report the true horizon cost for myopic runs` (`e4b8d2f7`) — the status-bar objective for a myopic solve must now equal the Economics tab total. Before the fix they differed by −42.9% on a 3-period system.
2. `fix(gui): warn when myopic locks capacity after the first period` (`b4dc11d6`) — a myopic network with no per-period vintage bounds must produce a `myopic_capacity_locked_after_first_period` warning from preflight.
3. `fix(gui): make a myopic run's build periods visible after the solve` (`6d009a28`) — after a myopic solve, `n.meta["vintage_results"]` must carry an entry per expanded asset so the Capacity Expansion "by period" chart is non-empty.
4. `fix(gui): stop Compare showing absent curtailment as zero` (`97728ad5`) — frontend-only; covered by Vitest, verify by code-read + the existing tests, do not attempt to drive the React UI.

Do NOT try to automate clicking the packaged GUI. Drive the **backend HTTP API**, which is what the UI calls — it gives the same numbers with far less flakiness.

- [ ] **Step 1: Start a backend and confirm it answers**

```bash
cd "$(git rev-parse --show-toplevel)/pypsa-gui/backend"
../../.pixi/envs/test/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8123 &
for i in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8123/api/health || true)
  [ "$code" = "200" ] && { echo "backend up"; break; }
  sleep 2
done
```

Expected: `backend up`. If `/api/health` 404s, find the real health route with
`curl -s http://127.0.0.1:8123/openapi.json | python -c "import json,sys; print([p for p in json.load(sys.stdin)['paths'] if 'health' in p])"`.

- [ ] **Step 2: Write the driver script**

Create `pypsa-gui/backend/smoke/verify_myopic_ui.py`:

```python
"""
Verify the four fixes shipped 2026-08-05 against a LIVE backend.

Not under tests/ on purpose: it builds a network, runs real LP solves, and
would slow the unit suite for no benefit. Run it directly:

    .pixi/envs/test/bin/python -u pypsa-gui/backend/smoke/verify_myopic_ui.py

`-u` is load-bearing — without unbuffered stdout the prints never flush to a
background task's output file and a working run looks like a silent hang.
"""
import sys

import pandas as pd
import pypsa

sys.path.insert(0, "/Users/orange/Desktop/Code Test/pypsa-eur/pypsa-gui/backend")

from routers.results import get_cost_breakdown
from routers.simulation import _compute_run_objective
from services.pypsa_service import PyPSAService
from services.solver_service import SolverConfig, run_simulation
from services.validation_service import validate_for_run

PERIODS = [2030, 2035, 2040]
RESULTS: list[tuple[str, bool, str]] = []


def record(label: str, ok: bool, detail: str) -> None:
    RESULTS.append((label, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {label}: {detail}", flush=True)


def build() -> pypsa.Network:
    """3-period network with 44% demand growth and one extendable generator."""
    n = pypsa.Network()
    idx = pd.MultiIndex.from_product(
        [PERIODS, pd.date_range("2030-01-01", periods=6, freq="h")],
        names=["period", "timestep"],
    )
    idx.name = "snapshot"
    n.set_snapshots(idx)
    n.investment_periods = PERIODS
    n.investment_period_weightings["years"] = 5.0
    n.investment_period_weightings["objective"] = 5.0
    n.add("Bus", "B")
    n.add("Carrier", "gas")
    growth = {2030: 1.0, 2035: 1.2, 2040: 1.44}
    n.add("Load", "L", bus="B", p_set=pd.Series(
        [100.0 * growth[p] for p, _ in n.snapshots], index=n.snapshots))
    n.add("Generator", "GAS", bus="B", carrier="gas", p_nom_extendable=True,
          capital_cost=25_000.0, marginal_cost=85.0, p_nom_max=5_000.0)
    n.add("Generator", "VOLL", bus="B", carrier="gas", p_nom=10_000.0,
          marginal_cost=5_000.0)
    return n


def install(n: pypsa.Network) -> None:
    PyPSAService.set_network(n)
    with PyPSAService._registry_lock:
        for k in [k for k in PyPSAService._contexts if k.startswith("scratch:")]:
            PyPSAService._contexts.pop(k, None)


def main() -> int:
    import queue
    import threading

    cfg = SolverConfig(solve_strategy="myopic", multi_investment_periods=True,
                       investment_periods=PERIODS, voll=0.0)

    # FIX 2 — the capacity-lock warning must fire BEFORE the solve.
    n = build()
    codes = [i.code for i in validate_for_run(n, cfg)]
    record("fix2 capacity-lock warning",
           "myopic_capacity_locked_after_first_period" in codes,
           f"codes={codes}")

    # Solve it for real, through run_simulation (not the myopic driver alone).
    install(n)
    log_q: queue.SimpleQueue = queue.SimpleQueue()
    status, condition = run_simulation(
        cfg, n, PyPSAService.get_lock(), threading.Event(), log_q)
    record("solve completed", status in ("ok", "optimal"), f"{status}/{condition}")

    # FIX 1 — status bar must equal the Economics tab.
    install(n)
    status_bar = _compute_run_objective(n, cfg)
    cb = get_cost_breakdown()
    economics = float(cb["total"]) if isinstance(cb, dict) else float("nan")
    agree = (economics == 0 and status_bar == 0) or abs(
        status_bar - economics) <= abs(economics) * 1e-6
    record("fix1 status bar == Economics", agree,
           f"status_bar={status_bar:,.0f} economics={economics:,.0f}")

    # FIX 3 — build periods recorded so the per-period chart is non-empty.
    vr = (n.meta or {}).get("vintage_results", {}).get("Generator", {})
    entry = vr.get("GAS")
    years = [p["build_year"] for p in entry["periods"]] if entry else []
    record("fix3 build period recorded", bool(years), f"build_years={years}")
    record("fix3 build_year column untouched",
           float(n.generators.at["GAS", "build_year"]) == 0.0,
           f"build_year={float(n.generators.at['GAS', 'build_year'])}")

    print("\n" + "=" * 60, flush=True)
    failed = [r for r in RESULTS if not r[1]]
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: Run it**

```bash
cd "$(git rev-parse --show-toplevel)"
.pixi/envs/test/bin/python -u pypsa-gui/backend/smoke/verify_myopic_ui.py
```

Expected: every line prints `PASS`, exit code 0. If any check FAILs, that is a
real finding — record the observed numbers, do NOT edit the fix to make the
check pass.

- [ ] **Step 4: Confirm fix 4 by its existing tests**

```bash
cd "$(git rev-parse --show-toplevel)/pypsa-gui/frontend"
export PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH"
npx vitest run src/pages/CompareView.test.tsx --reporter=dot
```

Expected: `36 passed`, and crucially **no "expected fail"** in the summary —
the old `it.fails` is now a real assertion.

- [ ] **Step 5: Write the findings file**

Create `docs/superpowers/findings/2026-08-06-ui-verification.md` with one
section per fix: what was run, the observed numbers, and PASS/FAIL. Include the
actual status-bar and Economics figures rather than just "they matched" — a
future reader needs to know the magnitudes to spot a regression.

- [ ] **Step 6: Commit**

```bash
git add pypsa-gui/backend/smoke/verify_myopic_ui.py docs/superpowers/findings/2026-08-06-ui-verification.md
git commit -m "test(gui): verify the four myopic/compare fixes against a live solve

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Count non-extendable links in the Capacity tab's CAPEX

**Files:**
- Modify: `pypsa-gui/backend/routers/compare.py:880`
- Test: `pypsa-gui/backend/tests/test_compare_link_capex_parity.py` (create)

**Interfaces:**
- Consumes: `_compute_total_annuitised_capex(n, periods, is_multi, years_map, pcc)` in `routers/compare.py:795`, and `routers.results.get_cost_breakdown()`
- Produces: no new public surface — a behaviour change plus a regression test

**Background the implementer needs.** `_compute_total_annuitised_capex` builds
the Capacity tab's headline CAPEX metric. Its own docstring states the contract:

> Mirrors the per-period aggregation in ``cost_breakdown`` so the two views show the same gas / solar / battery CAPEX numbers.

Earlier today a fix added links to that walk, but restricted to extendable ones:

```python
_walk(n.links, "p_nom", "links", extendable_only=True)
```

The Economics tab (`/results/cost_breakdown`) is built from `n.statistics()`,
which charges `capital_cost × p_nom_opt` for **every** asset — extendable or
not. So a **non-extendable link carrying a `capital_cost`** is counted by
Economics and omitted by the Capacity tab, and the two disagree. That is the
residual this task closes.

Note the `extendable_only` parameter is used **only** for links. Leave the
parameter itself in place; other call sites and tests may rely on it existing.

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/backend/tests/test_compare_link_capex_parity.py`:

```python
"""
The Capacity tab's CAPEX must equal the Economics tab's for the same network.

`_compute_total_annuitised_capex`'s docstring promises it "mirrors the
per-period aggregation in cost_breakdown". Economics is built from
`n.statistics()`, which charges capital_cost x p_nom_opt for EVERY asset. The
Capacity walk restricted links to extendable ones, so a NON-extendable link
carrying a capital_cost was counted by one view and not the other.
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest

import routers.simulation as sim_router
from routers.compare import _compute_total_annuitised_capex, _periodized_lookup
from routers.results import get_cost_breakdown
from services.solver_service import SolverConfig


def _network_with_fixed_costly_link() -> pypsa.Network:
    """Two buses joined by a NON-extendable link that still carries capex."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.add("Bus", "A")
    n.add("Bus", "B")
    n.add("Carrier", "gas")
    n.add("Carrier", "dc")
    n.add("Load", "L", bus="B", p_set=50.0)
    n.add("Generator", "g", bus="A", carrier="gas", p_nom=500.0,
          marginal_cost=10.0)
    # The asset under test: fixed size, real capital_cost.
    n.add("Link", "DC", bus0="A", bus1="B", carrier="dc",
          p_nom=200.0, p_nom_extendable=False, efficiency=1.0,
          capital_cost=90_000.0)
    return n


def _capacity_tab_capex(n) -> float:
    pcc = _periodized_lookup(n)
    per_carrier = _compute_total_annuitised_capex(
        n, [], False, {}, pcc)
    return sum(v["total"] for v in per_carrier.values())


def test_a_fixed_link_with_capital_cost_is_counted_by_the_capacity_tab():
    n = _network_with_fixed_costly_link()
    n.optimize(solver_name="highs")

    total = _capacity_tab_capex(n)
    # 200 MW x 90 000 EUR/MW/a = 18 MEUR/a, reported in MEUR.
    assert total == pytest.approx(18.0, rel=1e-6), (
        f"the fixed link's CAPEX is missing from the Capacity tab: {total}"
    )


def test_capacity_and_economics_agree_on_the_same_network(install_network):
    n = _network_with_fixed_costly_link()
    n.optimize(solver_name="highs")
    install_network(n)
    sim_router._state["solver_config"] = SolverConfig()

    payload = get_cost_breakdown()
    assert isinstance(payload, dict), "cost_breakdown did not return a payload"
    economics_capex = float(payload["capex"]) / 1e6  # EUR -> MEUR

    assert _capacity_tab_capex(n) == pytest.approx(economics_capex, rel=1e-6)


def test_an_extendable_link_is_still_counted():
    """Guards against the fix over-reaching and dropping the extendable path."""
    n = _network_with_fixed_costly_link()
    n.links.loc["DC", "p_nom_extendable"] = True
    n.links.loc["DC", "p_nom_max"] = 1_000.0
    n.optimize(solver_name="highs")
    assert _capacity_tab_capex(n) > 0.0
```

- [ ] **Step 2: Run it and confirm it fails for the right reason**

```bash
cd "$(git rev-parse --show-toplevel)/pypsa-gui/backend"
../../.pixi/envs/test/bin/python -m pytest tests/test_compare_link_capex_parity.py --no-header -v -p no:warnings
```

Expected: `test_a_fixed_link_with_capital_cost_is_counted_by_the_capacity_tab`
FAILS with the total at `0.0` (or missing the 18.0 contribution), and
`test_capacity_and_economics_agree_on_the_same_network` FAILS on the mismatch.
`test_an_extendable_link_is_still_counted` should PASS already.

If instead the first test passes, STOP: the divergence does not reproduce and
this task's premise is wrong. Report that rather than changing code.

- [ ] **Step 3: Make the minimal change**

In `pypsa-gui/backend/routers/compare.py`, at the link walk (line ~880), drop
the restriction and explain why in place:

```python
    # Every link that carries a capital_cost, not only the extendable ones.
    # `cost_breakdown` is built from `n.statistics()`, which charges
    # capital_cost x p_nom_opt for EVERY asset — so restricting this walk to
    # extendables made a fixed link with a capital_cost show up in Economics
    # and vanish from the Capacity tab, contradicting this function's
    # documented contract of mirroring cost_breakdown.
    _walk(n.links,         "p_nom", "links")
```

- [ ] **Step 4: Run the test again**

```bash
cd "$(git rev-parse --show-toplevel)/pypsa-gui/backend"
../../.pixi/envs/test/bin/python -m pytest tests/test_compare_link_capex_parity.py --no-header -v -p no:warnings
```

Expected: 3 passed.

- [ ] **Step 5: Run the whole compare suite for regressions**

```bash
cd "$(git rev-parse --show-toplevel)/pypsa-gui/backend"
../../.pixi/envs/test/bin/python -m pytest tests/test_compare_contract.py tests/test_compare_invariants.py tests/test_compare_cross_surface.py tests/test_compare_endpoint.py tests/test_cost_totals_contract.py --no-header -p no:warnings
```

Expected: all pass. The cross-surface suite is the one most likely to notice
this change — if it fails, read the assertion before touching anything, because
it may be encoding the OLD extendable-only behaviour as expected.

- [ ] **Step 6: Commit**

```bash
git add pypsa-gui/backend/routers/compare.py pypsa-gui/backend/tests/test_compare_link_capex_parity.py
git commit -m "fix(gui): count non-extendable links in the Capacity tab's CAPEX

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Move the pitfall notes into version control

**Files:**
- Create: `pypsa-gui/docs/pitfalls-myopic-and-cost-reporting.md`
- Modify: `CLAUDE.md` (replace the three long rows with one-line pointers)

**Interfaces:**
- Consumes: the three "Known Pitfalls" rows added to `CLAUDE.md` on 2026-08-05
- Produces: a tracked document other sessions and future readers can rely on

**Background the implementer needs.** `CLAUDE.md` is listed in `.gitignore`
(line 89), so everything in it exists only in this working copy. A parallel
session that resets the worktree discards it. Three rows added on 2026-08-05
carry findings that cost real investigation:

1. Summing per-period LP objectives to get a myopic horizon cost — why it is wrong in both directions, and that `n.objective_constant` is identically zero under `multi_investment_periods=True`.
2. Myopic freezing capacity at the first period, silently.
3. `_myopic_period_objectives` left on the network after a non-myopic re-solve.

Find them with:

```bash
grep -n "Summing the per-period LP objectives\|Myopic freezing capacity at the FIRST period\|_myopic_period_objectives\` left on the network" CLAUDE.md
```

- [ ] **Step 1: Create the tracked document**

Create `pypsa-gui/docs/pitfalls-myopic-and-cost-reporting.md`. Copy the three
rows' full text out of `CLAUDE.md` verbatim — do not summarise, the detail is
the value — and restructure each as a section with a `## ` heading, a
**Symptom** line, a **Cause** paragraph, and a **Rule** line. Add a short intro
stating that these came out of the 2026-08-05 myopic examination and that the
measured numbers are from a 3-period test system.

Cross-reference the committed findings docs so a reader can get the full
investigation:
`docs/superpowers/findings/2026-08-03-compare-tab-correctness.md` and
`docs/superpowers/findings/2026-08-05-myopic-foresight-e2e.md`.

- [ ] **Step 2: Replace the CLAUDE.md rows with pointers**

Keep a row per pitfall so the table still surfaces the trap, but make each one
a single line ending in a pointer, e.g.:

```
| Summing the per-period LP objectives to get a myopic horizon cost | Wrong in BOTH directions (−42.9% plain, +22.4% with `lf_aggregate_future`) — `n.objective_constant` is identically zero under `multi_investment_periods=True`. Use `services/cost_totals.py::horizon_system_cost`. Full detail: `pypsa-gui/docs/pitfalls-myopic-and-cost-reporting.md`. |
```

- [ ] **Step 3: Verify the document renders and nothing was lost**

```bash
cd "$(git rev-parse --show-toplevel)"
wc -l pypsa-gui/docs/pitfalls-myopic-and-cost-reporting.md
grep -c "^## " pypsa-gui/docs/pitfalls-myopic-and-cost-reporting.md
```

Expected: three `## ` sections. Re-read the new file against the original rows
and confirm every measured number survived the move (−42.9%, +22.2%, 977 MW,
47/1756/5183 MWh, 977/+195/+234, 47/56/68).

- [ ] **Step 4: Commit**

`CLAUDE.md` is gitignored and CANNOT be committed — do not attempt to `git add`
it; that would fail or require a force-add, which is wrong here.

```bash
git add pypsa-gui/docs/pitfalls-myopic-and-cost-reporting.md
git commit -m "docs: track the myopic and cost-reporting pitfalls outside CLAUDE.md

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Final gate (controller runs this, not the task implementers)

```bash
cd "$(git rev-parse --show-toplevel)"
pixi run gui-tests --no-header -p no:warnings 2>&1 | tail -5
cd pypsa-gui/frontend && export PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH"
npx tsc --noEmit && npx vitest run --reporter=dot 2>&1 | tail -5
```

Expected: backend ≥ 2279 passed / 0 failed; frontend ≥ 635 passed / 0 failed; tsc silent.
