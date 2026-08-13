# Remaining Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox
> (`- [ ]`) syntax for tracking.

**Goal:** Close the three piles left after the ctx-binding/compare-nulls branch landed —
leftover cleanup, the deferred wrong-number defects, and the top architecture candidate.

**Architecture:** Two defects are the same class as the ones Task 7/8 fixed on the previous
branch: a figure that could not be computed ships as a plausible number instead of an
absence. Both are closed the same way — a signal on the block, honoured by the frontend.
The architecture task collapses the CAPEX walk into the existing `services/economics.py`
leaf module, which is already the established seam for figures both Results and Compare
must agree on.

**Tech Stack:** FastAPI + PyPSA + Pydantic (backend), React + TypeScript + Vitest
(frontend), pytest via `pixi run gui-tests`.

## Global Constraints

- **ADR-0001**: zero is a legitimate result in an energy-system model; an unresolvable
  figure never ships as `0.0`. A block that could not compute says so.
- **Canonical test command is `pixi run gui-tests`.** Never `pixi run pytest`.
- Foreground bash has a **120 s hard cap** that auto-backgrounds longer commands. Full-suite
  runs go through `run_in_background`, not a raised timeout.
- `services/economics.py` is a **LEAF module**: stdlib + pandas only. It must never import
  `routers.*`. Anything needing `routers.results._result_df` stays where it is.
- Commit path-limited (`git commit <path>`), never `git add -A` — other sessions share this
  worktree.
- Re-run `git branch --show-current` immediately before every commit; the branch moves.

## Concurrency (checked 2026-08-13 21:04 CEST)

Main tree clean at `6cf5741b`. Sibling worktrees: `solve-queue-full-pass` holds uncommitted
edits to `solve_queue.py` / `solve_job_store.py` / `SolveQueuePanel.tsx` — **no overlap**
with this plan's files. `asset-editing` clean. 22 `claude` processes live.
Decision: **proceed**.

## File Structure

| File | Responsibility in this plan |
|---|---|
| `backend/models/schemas.py` | `partial` flag on `CurtailmentComparison` |
| `backend/routers/compare.py` | set `partial`; delegate CAPEX walk to the seam |
| `backend/routers/results.py` | stop dropping `available` at the economics wire |
| `backend/services/economics.py` | new home for the annuitised-CAPEX walk |
| `frontend/src/api/types.ts` | mirror both new fields |
| `frontend/src/pages/CompareView.tsx` | render the partial marker; one spelling of unavailable |
| `backend/tests/test_compare_availability.py` | Task B RED test |
| `backend/tests/test_results_economics_availability.py` | Task C RED test (new file) |

---

### Task A: Cleanup

**Files:**
- Delete: `.claude/worktrees/ctx-binding-and-compare-nulls/` (143 MB, git already pruned it)

**TDD exemption — named, not assumed:** this task's entire diff is the removal of
build artifacts outside the source tree. No behaviour changes, so no test covers it.

- [ ] **Step 1: Confirm git no longer tracks the worktree**

```bash
git worktree list | grep ctx-binding && echo "STILL REGISTERED - stop" || echo "safe"
```
Expected: `safe`.

- [ ] **Step 2: Remove the directory**

```bash
rm -rf "/Users/orange/Desktop/Code Test/pypsa-eur/.claude/worktrees/ctx-binding-and-compare-nulls"
```

---

### Task B: curtailment reports the PARTIAL case

**Files:**
- Modify: `backend/models/schemas.py` (`CurtailmentComparison`)
- Modify: `backend/routers/compare.py:2379-2385` (the non-empty return)
- Modify: `frontend/src/api/types.ts`
- Test: `backend/tests/test_compare_availability.py`

**Interfaces:**
- Produces: `CurtailmentComparison.partial: bool` — `True` when at least one generator's
  figure could not be computed but others succeeded, so the shipped total UNDERSTATES.
  Defaults `False`, which is correct for every existing construction: the empty-result
  return already encodes total failure in `available`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_compare_availability.py`:

```python
def test_curtailment_flags_the_partial_case_when_some_generators_failed():
    """
    The non-empty return: "wind" computes a real figure, "solar" fails on
    path 5, and the block ships wind's number alone.

    `available` is correctly True — a real measurement IS present — so it
    cannot carry this. Without a second signal the block is
    indistinguishable from a complete answer, and it understates by exactly
    solar's contribution. This is the case Task 8 ruled out of scope and
    recorded rather than dropped.

    Same `inf`-after-solve technique as the two tests above: NaN would be
    sanitised by `.fillna(0.0)` before any `isfinite` guard sees it.
    """
    import pandas as pd
    import pypsa

    import routers.compare as CMP

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.add("Bus", "b1", carrier="AC")
    n.add("Carrier", "AC")
    n.add("Carrier", "gas")
    n.add("Carrier", "wind")
    n.add("Carrier", "solar")
    n.add(
        "Generator", "gas",
        bus="b1", carrier="gas",
        p_nom=100.0, marginal_cost=50.0,
    )
    n.add(
        "Generator", "wind",
        bus="b1", carrier="wind",
        p_nom=50.0, p_nom_extendable=False, marginal_cost=0.0,
        p_max_pu=[0.5, 0.6, 0.4, 0.7],
    )
    n.add(
        "Generator", "solar",
        bus="b1", carrier="solar",
        p_nom=50.0, p_nom_extendable=False, marginal_cost=0.0,
        p_max_pu=[0.3, 0.8, 0.2, 0.9],
    )
    n.add("Load", "load1", bus="b1", p_set=20.0)
    n.optimize(solver_name="highs")

    # Corrupt ONLY solar, after the solve. wind still produces a real figure.
    n.generators_t.p_max_pu.loc[n.snapshots[0], "solar"] = float("inf")

    block = CMP._compute_curtailment_summary(n, [], False, True)

    assert block.available is True, (
        "wind produced a real measurement — the block is not unavailable"
    )
    assert block.partial is True, (
        "solar's figure could not be computed, so the shipped total "
        "understates; a complete-looking answer here is the ADR-0001 "
        "failure mode with a plausible number instead of a zero"
    )
    assert "wind" in block.by_carrier_gwh
    assert "solar" not in block.by_carrier_gwh
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pixi run gui-tests -k test_curtailment_flags_the_partial_case_when_some_generators_failed
```
Expected: FAIL — `AttributeError`/`assert False is True` on `block.partial`.

- [ ] **Step 3: Add the field to the schema**

In `backend/models/schemas.py`, immediately after `CurtailmentComparison.available`:

```python
    # True means SOME generator's figure could not be computed while others
    # succeeded, so every number below is a real measurement that UNDERSTATES
    # the true total. `available` cannot carry this: a real measurement IS
    # present, so `available` is correctly True and a consumer that only
    # checks it sees a complete answer. Empty-result total failure is still
    # signalled by `available=False` alone, where `partial` stays False
    # because there is no partial figure to qualify. See ADR-0001.
    partial: bool = False
```

- [ ] **Step 4: Set the flag on the non-empty return**

In `backend/routers/compare.py`, the final return of `_compute_curtailment_summary`:

```python
    return CurtailmentComparison(
        available=True,
        partial=(failed > 0),
        total_gwh=_to_pv(total_bucket),
        by_carrier_gwh=_to_pv_dict(curt_by_carrier),
        rate_pct_by_carrier=_to_pv_dict(rate_by_carrier),
        system_rate_pct=_to_pv({"total": sys_rate_t, "by_period": sys_rate_pp}),
    )
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
pixi run gui-tests -k test_curtailment
```
Expected: PASS, and the two Task 8 tests still pass.

- [ ] **Step 6: Mirror the field in the frontend type**

In `frontend/src/api/types.ts`, on the `CurtailmentComparison` interface, after `available`:

```ts
  /** Some generators' figures could not be computed — the totals understate. */
  partial?: boolean
```

- [ ] **Step 7: Commit**

```bash
git branch --show-current
git commit pypsa-gui/backend/models/schemas.py \
           pypsa-gui/backend/routers/compare.py \
           pypsa-gui/backend/tests/test_compare_availability.py \
           pypsa-gui/frontend/src/api/types.ts \
  -m "fix(compare): curtailment flags the partial case instead of understating silently"
```

---

### Task C: `/results` economics stops dropping `available`

**Files:**
- Modify: `backend/routers/results.py` — `get_economics_by_carrier`, BOTH return sites
- Test: `backend/tests/test_results_economics_availability.py` (create)

**Interfaces:**
- Consumes: `EconomicsComparison.available` from Task 4 of the previous branch.
- Produces: the endpoint's JSON gains `"available": bool` alongside `"by_carrier"`.

**Two drop sites, not one** — found by reading the function rather than trusting the
line reference:

1. `if not _dispatch_ready(n): return {}` — an unsolved network gets a bare `{}`, which
   is indistinguishable from "solved, but no carriers". This is the WIDER hole.
2. `return {"by_carrier": ...}` on the success path — drops the computed `available`.

Both are fixed; a fix to only the second leaves the unsolved case still lying.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_results_economics_availability.py`:

```python
"""
The Results economics endpoint must forward the block's `available` flag.

`_compute_economics_summary` sets `available` (previous branch, Task 4), but
this endpoint returns ONLY `by_carrier` — so every Compare-side availability
fix stops at Compare, and the Results tab has no way to tell a real zero from
a figure that was never resolved. Same ADR-0001 property, one wire short.
"""
from __future__ import annotations

import pandas as pd
import pypsa

from services.pypsa_service import PyPSAService


def _unsolved_network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.add("Bus", "b1", carrier="AC")
    n.add("Carrier", "AC")
    n.add("Generator", "gas", bus="b1", carrier="AC", p_nom=100.0, marginal_cost=50.0)
    n.add("Load", "load1", bus="b1", p_set=20.0)
    return n


def test_economics_endpoint_forwards_the_available_flag(monkeypatch):
    import routers.results as R

    n = _unsolved_network()
    monkeypatch.setattr(PyPSAService, "get_network", staticmethod(lambda: n))

    payload = R.get_economics_by_carrier()

    assert "available" in payload, (
        "the block computes `available`; dropping it at the wire means the "
        "Results tab cannot distinguish a real zero from an unresolved "
        "figure — see ADR-0001"
    )
    assert payload["available"] is False
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pixi run gui-tests pypsa-gui/backend/tests/test_results_economics_availability.py
```
Expected: FAIL — `assert "available" in payload`.

- [ ] **Step 3: Forward the flag at BOTH return sites**

The not-ready guard:

```python
    n = PyPSAService.get_network()
    if not _dispatch_ready(n):
        # `{}` here was indistinguishable from "solved, but this network has
        # no carriers" — a caller could not tell an absence from a measured
        # empty result. Same shape as the success path, availability false.
        return {"available": False, "by_carrier": {}}
```

The success path:

```python
        # Return the by_carrier dict PLUS the availability flag — dropping
        # the flag here made every downstream consumer read an unresolved
        # figure as a measured zero (ADR-0001). Still drops per_asset_lcoh
        # (lives in /api/results/lcoh) to keep the payload small.
        return {
            "available": bool(result.available),
            "by_carrier": {k: v.model_dump() for k, v in result.by_carrier.items()},
        }
```

The `except` path at the bottom returns `{"error": ..., "trace": ...}` and is left
alone — an error body is already unambiguous.

- [ ] **Step 4: Run the test to verify it passes**

```bash
pixi run gui-tests pypsa-gui/backend/tests/test_results_economics_availability.py
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git branch --show-current
git commit pypsa-gui/backend/routers/results.py \
           pypsa-gui/backend/tests/test_results_economics_availability.py \
  -m "fix(results): forward the economics block's available flag at the wire"
```

---

### Task D: one spelling of the unavailable cell

**Files:**
- Modify: `frontend/src/pages/CompareView.tsx`

**Interfaces:**
- Produces: a single exported constant the whole file renders for an unresolved figure,
  replacing the four ad-hoc spellings the whole-branch review recorded.

- [ ] **Step 1: Inventory the spellings before changing anything**

```bash
grep -n 'UNAVAILABLE' pypsa-gui/frontend/src/pages/CompareView.tsx | head -40
```

Record the distinct right-hand-side literals. If the file already renders exactly one
literal, this task is a no-op — say so in the report and skip to Task E rather than
inventing a refactor.

- [ ] **Step 2: Write the failing test**

Add to the existing CompareView test file (find it with
`ls pypsa-gui/frontend/src/pages/__tests__/ | grep -i compare`) a test that renders a
comparison with one side unavailable and asserts the rendered text matches the single
constant. Use the fixture shape already in that file — do not invent a prop shape.

- [ ] **Step 3: Run it RED, collapse to one constant, run it GREEN**

```bash
cd pypsa-gui/frontend && npx vitest run src/pages/CompareView.availability.test.tsx
```

- [ ] **Step 4: Commit**

```bash
git branch --show-current
git commit pypsa-gui/frontend/src/pages/CompareView.tsx \
           pypsa-gui/frontend/src/pages/CompareView.availability.test.tsx \
  -m "refactor(compare): one spelling of the unavailable cell"
```

---

### Task E: give the CAPEX walk a seam (architecture candidate #1)

**Files:**
- Modify: `backend/services/economics.py` (add the walk)
- Modify: `backend/routers/compare.py:811-896` (delegate)
- Test: `backend/tests/test_economics_capex_walk.py` (create)

**Interfaces:**
- Produces: `services.economics.annuitised_capex_by_carrier(gens, storage_units, stores,
  links, *, periods, is_multi, years_map, capital_cost_of) -> dict[str, dict]` —
  same return shape `_compute_total_annuitised_capex` has today
  (`carrier -> {"total": float, "by_period": {str: float}}`).
- `capital_cost_of` is a callable `(row, comp_attr) -> float`, so the leaf module never
  needs `_safe_capital_cost`'s `pcc` plumbing or any `routers.*` import. This is the
  seam: one adapter today (compare.py), a second when Results adopts it.

**Scope ruling, stated up front:** this task extracts the walk; it does NOT collapse
`get_cost_breakdown`'s `n.statistics()`-based aggregation into it. Those are two genuinely
different mechanisms, and choosing between them is a behavioural decision with a real
answer only the human can give — the two capex parity suites exist precisely because the
answers differ at the edges. Extracting the walk removes the duplication that is safe to
remove and leaves the parity suites pinning the remaining seam.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_economics_capex_walk.py`:

```python
"""
The annuitised-CAPEX walk lives in the leaf economics module, not in a router.

`_compute_total_annuitised_capex` sat in `routers/compare.py`, so the Results
side could not reach it without importing a router — which is why a second
walk grew there and why two parity suites exist to keep them agreeing. This
test pins the walk at its new address and proves the leaf contract: the
module imports nothing from `routers.*`.
"""
from __future__ import annotations

import pandas as pd


def test_capex_walk_is_importable_from_the_leaf_module():
    from services.economics import annuitised_capex_by_carrier

    gens = pd.DataFrame(
        {"p_nom_opt": [100.0], "carrier": ["solar"]},
        index=["s1"],
    )
    empty = pd.DataFrame()

    out = annuitised_capex_by_carrier(
        gens, empty, empty, empty,
        periods=[], is_multi=False, years_map={},
        capital_cost_of=lambda row, comp_attr: 50_000.0,
    )

    # 100 MW x 50 000 EUR/MW/a = 5 MEUR/a
    assert out["solar"]["total"] == 5.0


def test_economics_module_stays_a_leaf():
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[1] / "services" / "economics.py"
    text = src.read_text(encoding="utf-8")
    assert "routers" not in text.replace("routers/results.py", "").replace(
        "routers/compare.py", ""
    ), "services/economics.py must not import routers.* — it is a leaf module"
```

- [ ] **Step 2: Run it RED**

```bash
pixi run gui-tests pypsa-gui/backend/tests/test_economics_capex_walk.py
```
Expected: FAIL — `ImportError: cannot import name 'annuitised_capex_by_carrier'`.

- [ ] **Step 3: Move the walk into the leaf module**

Copy the body of `_compute_total_annuitised_capex` (compare.py:811-896) into
`services/economics.py` as `annuitised_capex_by_carrier`, with two substitutions:
- the four `n.<component>` reads become the four positional DataFrame parameters
- `_safe_capital_cost(row, pcc, comp_attr)` becomes `capital_cost_of(row, comp_attr)`
- `period_utils.years_for_period(years_map, p)` — import `period_utils` at module top;
  it is already a leaf sibling, so this does not break the contract.

Carry the whole comment block verbatim: it records two measured regressions
(25.154535 vs 25.320785 M€ on the golden fixture, 56.192453 M€ on a real project)
and the reasoning for including non-extendable links. That reasoning is the module's
value, not decoration.

- [ ] **Step 4: Delegate from compare.py**

Replace the body of `_compute_total_annuitised_capex` with:

```python
def _compute_total_annuitised_capex(
    n, periods, is_multi, years_map, pcc,
) -> dict:
    """
    Thin adapter over ``services.economics.annuitised_capex_by_carrier``.

    The walk itself moved to the leaf module so the Results side can reach it
    without importing a router. This wrapper survives because every call site
    passes ``n`` and ``pcc``; the leaf takes DataFrames and a cost callable so
    it stays free of PyPSA-object and router coupling.
    """
    from services.economics import annuitised_capex_by_carrier

    return annuitised_capex_by_carrier(
        n.generators, n.storage_units, n.stores, n.links,
        periods=periods, is_multi=is_multi, years_map=years_map,
        capital_cost_of=lambda row, comp_attr: _safe_capital_cost(row, pcc, comp_attr),
    )
```

- [ ] **Step 5: Run the new test GREEN, then both parity suites**

```bash
pixi run gui-tests pypsa-gui/backend/tests/test_economics_capex_walk.py
pixi run gui-tests pypsa-gui/backend/tests/test_compare_link_capex_parity.py \
                   pypsa-gui/backend/tests/test_compare_store_capex_parity.py
```
Expected: all PASS. The parity suites are the behaviour-preservation proof — they
compare the Capacity tab's number against Economics', so a walk that drifted during
the move fails them.

- [ ] **Step 6: Commit**

```bash
git branch --show-current
git commit pypsa-gui/backend/services/economics.py \
           pypsa-gui/backend/routers/compare.py \
           pypsa-gui/backend/tests/test_economics_capex_walk.py \
  -m "refactor(economics): move the annuitised-CAPEX walk to the leaf module"
```

---

### Task F: full-suite verification

- [ ] **Step 1: Run the whole backend suite in the background**

```bash
pixi run gui-tests
```
Run via `run_in_background` — the suite takes ~7 minutes, well past the 120 s
foreground cap.

Expected: no regressions against the 2540-passed / 18-skipped baseline, plus this
plan's new tests.

- [ ] **Step 2: Frontend typecheck and tests**

```bash
cd pypsa-gui/frontend && PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx tsc --noEmit -p tsconfig.json
cd pypsa-gui/frontend && npx vitest run
```

---

## Explicitly NOT in this plan

Recorded so the omission is a decision, not a silent drop:

- **Architecture candidate #3** (client-side Asset write chokepoint) — frontend-wide,
  its own plan.
- **#2b `Resolved[T]`** — a type-system redesign touching all nine Comparison blocks.
  The bare `available: bool` that shipped covers the defect; `Resolved[T]` is the
  refactor that would make the next one impossible. Different risk profile.
- **#5b cross-session swap** (`get_lock()` on the wrong context, chat_tools.py:2607 and
  :2808) — a concurrency fix that wants its own reproduction harness first.
- **chat_tools' ~105 direct router calls** — the standing hazard the architecture review
  named. Broke twice on the previous branch (Tasks 2 and 3). Needs a guard test, not a
  point fix.

## Risks

| Risk | Mitigation |
|---|---|
| Task E's move drifts the CAPEX number | The two parity suites compare Capacity vs Economics on the same network — they fail on any drift. Run them explicitly in Step 5, not just in the full suite. |
| Task D turns out to be a no-op | Step 1 inventories before changing. A single existing spelling means report and skip, not invent a refactor. |
| The branch moves under the commits | `git branch --show-current` immediately before each commit; path-limited commits so a concurrent session's edits are never swept in. |
| Task C's endpoint name guessed wrong | Step 2 reads the actual `def` before the test runs. |

## Open items

- Whether `get_cost_breakdown` should eventually adopt the leaf walk (retiring
  `n.statistics()` for CAPEX) is **unresolved and deliberately out of scope** — see
  Task E's scope ruling.
