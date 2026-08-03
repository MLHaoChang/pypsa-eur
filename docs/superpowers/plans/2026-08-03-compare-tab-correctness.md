# Compare Tab Correctness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish, with tests that run in the gate, whether each of the ten Compare result tabs computes correct numbers — and fix the ones that don't.

**Architecture:** Numeric tests call the ten `_compute_*_summary` functions in `routers/compare.py` directly against the solved golden multi-period fixture; a metric-kind registry drives the additivity checks so rates and stocks are never sum-checked; cross-surface tests compare each tab against the live `/results/*` endpoint for the same network; the frontend, where the A-minus-B arithmetic actually lives, is tested by rendering each tab with a mocked API.

**Tech Stack:** pytest + PyPSA + HiGHS (backend); Vitest + @testing-library/react + React Query (frontend). Run backend tests with `pixi run gui-tests`, never bare `pytest` — the `default` pixi env lacks `pywebview` and produces seven spurious failures.

## Global Constraints

- Backend test command is `pixi run gui-tests` (resolves the `test` environment). Baseline as of 2026-08-03: **1976 passed, 1 skipped, 0 failed**. Any task ending with a different failure count has broken something.
- Frontend test command is `npx vitest run` from `pypsa-gui/frontend`, with `.pixi/envs/default/bin` on PATH. Baseline: **57 files, 401 tests passing**. `tsc --noEmit` must stay clean.
- `pytest.ini` sets `python_files = test_*.py`. A file not named `test_*.py` is NOT collected and does not gate anything.
- Frontend vitest runs `globals: false` — import `describe`/`it`/`expect`/`vi`/`beforeAll` explicitly from `vitest`.
- The golden fixture's periods are `GOLDEN_PERIODS = (2030, 2035)` with `GOLDEN_YEARS = (5, 10)`; horizon total is 15 years. `by_period` dict keys are **stringified ints** (`"2030"`, `"2035"`).
- Never widen or delete an assertion to make a test pass. A failing test that reflects a real defect gets `@pytest.mark.xfail(strict=True, reason=...)` plus a findings entry, and the fix follows in its own task.
- One commit per defect. Do not batch fixes.
- This repo shares its worktree with a concurrent session. Stage files explicitly by path; never `git add -A` or `git add .`.

---

## File Structure

**Create:**
- `pypsa-gui/backend/tests/compare_support.py` — metric-kind registry and shared helpers. Deliberately NOT named `test_*.py`; it is a support module, not a test file.
- `pypsa-gui/backend/tests/test_compare_contract.py` — payload population, determinism, and the registry completeness guard.
- `pypsa-gui/backend/tests/test_compare_invariants.py` — registry-driven additivity plus per-tab internal identities.
- `pypsa-gui/backend/tests/test_compare_cross_surface.py` — each tab against its live `/results/*` counterpart.
- `pypsa-gui/backend/tests/test_compare_oracle.py` — independent recomputation for overview and storage cycling.
- `pypsa-gui/backend/tests/test_compare_endpoint.py` — wiring through `client` + `api_project`.
- `pypsa-gui/frontend/src/pages/CompareView.test.tsx` — A/A identity and delta arithmetic.
- `docs/superpowers/findings/2026-08-03-compare-tab-correctness.md` — the findings record.

**Modify (only if a task's measurement demands it):**
- `pypsa-gui/backend/tests/golden/coverage.py` — add the eight uncovered tabs to `SURFACES`.
- `pypsa-gui/backend/routers/compare.py` — fixes only, one defect per commit.
- `pypsa-gui/backend/tests/golden/fixture.py` — only if a tab cannot be exercised non-trivially without a new asset.

---

### Task 1: The metric-kind registry

Every later additivity check reads this. Getting it wrong produces false defects — an ad-hoc sweep on 2026-08-03 flagged `co2_intensity`, `load_factor`, `energy_capacity` and `bus_capacity_by_carrier` as broken when all four are simply not additive.

**Files:**
- Create: `pypsa-gui/backend/tests/compare_support.py`
- Test: `pypsa-gui/backend/tests/test_compare_contract.py`

**Interfaces:**
- Produces: `KIND: dict[str, str]` mapping `"<model>.<field>"` → `"extensive" | "intensive" | "stock"`; `classify(model_name, field) -> str`; `EXTENSIVE_FIELDS` / `INTENSIVE_FIELDS` / `STOCK_FIELDS` as derived frozensets.

- [ ] **Step 1: Write the registry**

Three kinds, distinguished by what `total` means relative to `by_period`:

- `extensive` — `total == sum(by_period.values())`. Flows and costs.
- `intensive` — `total` is a weighted mean or rate. Summing is meaningless.
- `stock` — `total` is a level (installed capacity). `by_period` holds either levels or increments; the relation is per-field and is asserted in Task 5, not here.

```python
"""Shared support for the Compare tab correctness suite. NOT a test module."""
from __future__ import annotations

EXTENSIVE, INTENSIVE, STOCK = "extensive", "intensive", "stock"

KIND: dict[str, str] = {
    # ── CapacityComparison ────────────────────────────────────────────────
    # Installed levels are stocks; "new_*" are per-period increments and so
    # do sum. capex is M€ committed over the horizon and sums (see
    # _compute_total_annuitised_capex: b["total"] = sum(by_period.values())).
    "CapacityComparison.capacity_mw_by_carrier": STOCK,
    "CapacityComparison.storage_mw_by_carrier": STOCK,
    "CapacityComparison.storage_mwh_by_carrier": STOCK,
    "CapacityComparison.link_capacity_mw_by_carrier": STOCK,
    "CapacityComparison.capex_meur_by_carrier": EXTENSIVE,
    "CapacityComparison.new_capex_meur_by_carrier": EXTENSIVE,
    "CapacityComparison.new_capacity_mw_by_carrier": EXTENSIVE,
    "CapacityComparison.new_storage_mw_by_carrier": EXTENSIVE,
    "CapacityComparison.new_storage_mwh_by_carrier": EXTENSIVE,
    "CapacityComparison.new_link_capacity_mw_by_carrier": EXTENSIVE,
    # ── DispatchComparison ────────────────────────────────────────────────
    "DispatchComparison.dispatch_gwh_by_carrier": EXTENSIVE,
    "DispatchComparison.opex_meur": EXTENSIVE,
    "DispatchComparison.total_load_gwh": EXTENSIVE,
    # Cycles is a per-year RATE. _compute_storage_cycling_summary's docstring
    # states the horizon value is the AVERAGE of per-period cycles, so that a
    # unit cycling 100×/yr in every period reads 100, not 300.
    "DispatchComparison.storage_cycles_by_carrier": INTENSIVE,
    # ── LoadingComparison / LineLoadingEntry ──────────────────────────────
    "LineLoadingEntry.peak_loading": INTENSIVE,
    "LineLoadingEntry.mean_loading": INTENSIVE,
    "LineLoadingEntry.binding_hours": EXTENSIVE,
    # ── PricesComparison / CarrierPriceStats ──────────────────────────────
    "PricesComparison.mean_price": INTENSIVE,
    "PricesComparison.median_price": INTENSIVE,
    "CarrierPriceStats.mean_price": INTENSIVE,
    "CarrierPriceStats.median_price": INTENSIVE,
    # ── EmissionsComparison ───────────────────────────────────────────────
    "EmissionsComparison.total_kt": EXTENSIVE,
    "EmissionsComparison.by_carrier_kt": EXTENSIVE,
    "EmissionsComparison.intensity_kg_per_mwh": INTENSIVE,
    # ── EconomicsComparison / CarrierEconomics / AssetLCOHEntry ───────────
    "CarrierEconomics.revenue_meur": EXTENSIVE,
    "CarrierEconomics.opex_meur": EXTENSIVE,
    "CarrierEconomics.gen_cost_meur": EXTENSIVE,
    "CarrierEconomics.storage_charge_cost_meur": EXTENSIVE,
    "CarrierEconomics.curtailment_cost_meur": EXTENSIVE,
    "CarrierEconomics.lost_load_cost_meur": EXTENSIVE,
    "CarrierEconomics.capex_meur": EXTENSIVE,
    "CarrierEconomics.dispatch_gwh": EXTENSIVE,
    "CarrierEconomics.lcoe_eur_per_mwh": INTENSIVE,
    "AssetLCOHEntry.capex_meur": EXTENSIVE,
    "AssetLCOHEntry.opex_meur": EXTENSIVE,
    "AssetLCOHEntry.input_cost_meur": EXTENSIVE,
    "AssetLCOHEntry.output_gwh": EXTENSIVE,
    "AssetLCOHEntry.lcoh_eur_per_mwh": INTENSIVE,
    # ── CurtailmentComparison ─────────────────────────────────────────────
    "CurtailmentComparison.total_gwh": EXTENSIVE,
    "CurtailmentComparison.by_carrier_gwh": EXTENSIVE,
    "CurtailmentComparison.rate_pct_by_carrier": INTENSIVE,
    "CurtailmentComparison.system_rate_pct": INTENSIVE,
    # ── LostLoadComparison ────────────────────────────────────────────────
    "LostLoadComparison.total_mwh": EXTENSIVE,
    "LostLoadComparison.total_cost_meur": EXTENSIVE,
    "LostLoadBus.energy_mwh": EXTENSIVE,
    "LostLoadBus.cost_meur": EXTENSIVE,
    "LostLoadByCarrier.energy_mwh": EXTENSIVE,
    "LostLoadByCarrier.cost_meur": EXTENSIVE,
    # ── StorageCyclingComparison / StorageUnitCycles ──────────────────────
    "StorageCyclingComparison.cycles_by_carrier": INTENSIVE,
    "StorageUnitCycles.throughput_mwh": EXTENSIVE,
    "StorageUnitCycles.cycles": INTENSIVE,
}

EXTENSIVE_FIELDS = frozenset(k for k, v in KIND.items() if v == EXTENSIVE)
INTENSIVE_FIELDS = frozenset(k for k, v in KIND.items() if v == INTENSIVE)
STOCK_FIELDS = frozenset(k for k, v in KIND.items() if v == STOCK)


def classify(model_name: str, field: str) -> str:
    """Kind for `<model>.<field>`; KeyError if unclassified (deliberate)."""
    return KIND[f"{model_name}.{field}"]
```

- [ ] **Step 2: Write the completeness guard**

The registry is only useful if it cannot silently fall behind the schema. This test walks the Pydantic models and fails when a `CarrierPeriodValue`-typed field has no classification — so an eleventh metric cannot be added without a decision being recorded.

```python
"""Contract-level checks: the payload is populated, deterministic, classified."""
from __future__ import annotations

import pytest

from models import schemas
from tests import compare_support as cs

MODELS_WITH_PERIOD_VALUES = (
    "CapacityComparison", "DispatchComparison", "LineLoadingEntry",
    "PricesComparison", "CarrierPriceStats", "EmissionsComparison",
    "CarrierEconomics", "AssetLCOHEntry", "CurtailmentComparison",
    "LostLoadComparison", "LostLoadBus", "LostLoadByCarrier",
    "StorageCyclingComparison", "StorageUnitCycles",
)


def _period_value_fields(model_name: str) -> list[str]:
    """Fields whose type is CarrierPeriodValue or dict[str, CarrierPeriodValue]."""
    model = getattr(schemas, model_name)
    out = []
    for field_name, info in model.model_fields.items():
        ann = str(info.annotation)
        if "CarrierPeriodValue" in ann:
            out.append(field_name)
    return out


def test_every_period_bearing_field_is_classified():
    missing = []
    for model_name in MODELS_WITH_PERIOD_VALUES:
        for field in _period_value_fields(model_name):
            if f"{model_name}.{field}" not in cs.KIND:
                missing.append(f"{model_name}.{field}")
    assert not missing, (
        "These fields carry a per-period breakdown but no extensive/intensive/"
        "stock classification, so the additivity suite would skip them "
        "silently:\n  " + "\n  ".join(missing)
    )


def test_the_registry_names_no_field_that_has_been_removed():
    """The mirror image: a renamed field must not leave a dead entry behind."""
    stale = []
    for key in cs.KIND:
        model_name, field = key.split(".", 1)
        model = getattr(schemas, model_name, None)
        if model is None or field not in model.model_fields:
            stale.append(key)
    assert not stale, f"registry entries with no matching field: {stale}"
```

- [ ] **Step 3: Run both tests to verify they pass**

Run: `pixi run gui-tests tests/test_compare_contract.py -p no:warnings -q`
Expected: 2 passed. If `test_every_period_bearing_field_is_classified` FAILS, the field inventory in Step 1 is out of date — add the named fields with a justified kind rather than deleting the assertion.

- [ ] **Step 4: Commit**

```bash
git add pypsa-gui/backend/tests/compare_support.py pypsa-gui/backend/tests/test_compare_contract.py
git commit -m "test(compare): classify every per-period metric as extensive, intensive or stock"
```

---

### Task 2: The summary harness

**Files:**
- Modify: `pypsa-gui/backend/tests/compare_support.py`
- Test: `pypsa-gui/backend/tests/test_compare_contract.py`

**Interfaces:**
- Consumes: `tests.golden.fixture.solve_golden_network()`, `install_golden(n)`, `GOLDEN_PERIODS`, `GOLDEN_YEARS`.
- Produces: `summarise(n) -> dict[str, object]` returning `{"capacity": ..., "dispatch": ..., "loading": ..., "prices": ..., "emissions": ..., "economics": ..., "curtailment": ..., "lost_load": ..., "storage_cycling": ..., "periods": [...], "is_multi": bool}`.

`get_results_summary` is NOT callable here: it takes `AuthorizedProject = ProjectAccessDep` and reads `project.directory / "network.nc"`, so it needs a DB row and org-scoped storage. Task 20 covers that path. Everything else calls the compute functions directly, which is the same choice the 2026-08-01 plan made and for the same reason.

- [ ] **Step 1: Add the harness to `compare_support.py`**

```python
def summarise(n) -> dict:
    """
    Every tab's payload for one network, by calling the compute functions the
    endpoint calls. Mirrors `get_results_summary`'s own argument derivation so
    the two cannot drift apart silently.
    """
    import routers.compare as CMP
    from services import period_utils

    is_multi = period_utils.is_multi_period(n)
    periods = sorted({int(p) for p in n.snapshots.get_level_values(0)}) if is_multi else []
    has_solve = not getattr(n.generators_t, "p", None) is None

    return {
        "periods": periods,
        "is_multi": is_multi,
        "capacity":        CMP._compute_capacity_summary(n, periods, is_multi, has_solve),
        "dispatch":        CMP._compute_dispatch_summary(n, periods, is_multi, has_solve),
        "loading":         CMP._compute_loading_summary(n, periods, is_multi, has_solve),
        "prices":          CMP._compute_prices_summary(n, periods, is_multi, has_solve),
        "emissions":       CMP._compute_emissions_summary(n, periods, is_multi, has_solve),
        # prices_from_state=False is what the endpoint passes for a loaded
        # bundle (routers/compare.py:2700) — read the network's own duals,
        # never the live singleton's cached snapshot.
        "economics":       CMP._compute_economics_summary(n, periods, is_multi, has_solve,
                                                          prices_from_state=False),
        "curtailment":     CMP._compute_curtailment_summary(n, periods, is_multi, has_solve),
        "lost_load":       CMP._compute_lost_load_summary(n, periods, is_multi, has_solve),
        "storage_cycling": CMP._compute_storage_cycling_summary(n, periods, is_multi, has_solve),
    }


TAB_FIELDS = ("capacity", "dispatch", "loading", "prices", "emissions",
              "economics", "curtailment", "lost_load", "storage_cycling")
```

- [ ] **Step 2: Add the golden fixture and a smoke test**

```python
import pytest

from tests.golden import fixture as gf


@pytest.fixture()
def golden(reset_backend):
    """Solved golden network, installed after conftest's autouse reset."""
    n = gf.solve_golden_network()
    gf.install_golden(n)
    return n


def test_the_harness_derives_the_same_periods_the_fixture_declares(golden):
    s = cs.summarise(golden)
    assert s["is_multi"] is True
    assert s["periods"] == list(gf.GOLDEN_PERIODS)
```

- [ ] **Step 3: Run it**

Run: `pixi run gui-tests tests/test_compare_contract.py -p no:warnings -q`
Expected: 3 passed. A failure naming a `_compute_*_summary` signature means the argument order in Step 1 is wrong — read `get_results_summary` (`routers/compare.py:2649`) and match it exactly.

- [ ] **Step 4: Commit**

```bash
git add pypsa-gui/backend/tests/compare_support.py pypsa-gui/backend/tests/test_compare_contract.py
git commit -m "test(compare): add a harness that summarises the golden network tab by tab"
```

---

### Task 3: Population and determinism

Nine optional fields on `ResultsSummary` default to `None`, and the model's own docstring says later phases "fill in additional optional fields" — so a tab that computes correctly and is never populated is a live failure mode that no numeric test would catch.

**Files:**
- Modify: `pypsa-gui/backend/tests/test_compare_contract.py`

- [ ] **Step 1: Write the tests**

```python
def test_every_tab_is_populated_for_a_solved_multi_period_network(golden):
    s = cs.summarise(golden)
    empty = [f for f in cs.TAB_FIELDS if s[f] is None]
    assert not empty, f"tabs returning None on a solved network: {empty}"


def test_summarising_twice_gives_an_identical_payload(golden):
    """
    The backend computes no delta — CompareView.tsx diffs two independent
    fetches client-side. So A-vs-A showing zero everywhere rests on the
    summary being a pure function of the network. If it is not, every
    comparison inherits the noise.
    """
    first, second = cs.summarise(golden), cs.summarise(golden)
    for field in cs.TAB_FIELDS:
        assert first[field].model_dump() == second[field].model_dump(), (
            f"{field} differs between two summarisations of one network")
```

- [ ] **Step 2: Run them**

Run: `pixi run gui-tests tests/test_compare_contract.py -p no:warnings -q`
Expected: 5 passed.

If `test_every_tab_is_populated...` FAILS, that is a finding, not a test bug: record which tabs returned `None`, mark the test `@pytest.mark.xfail(strict=True, reason="<tab> returns None on a solved network — see findings §N")` and open a fix task. Do not weaken the assertion to the tabs that happen to work.

- [ ] **Step 3: Commit**

```bash
git add pypsa-gui/backend/tests/test_compare_contract.py
git commit -m "test(compare): assert every tab populates and the summary is deterministic"
```

---

### Task 4: Registry-driven additivity

**Files:**
- Create: `pypsa-gui/backend/tests/test_compare_invariants.py`

**Interfaces:**
- Consumes: `compare_support.summarise`, `compare_support.KIND`, `classify`.

- [ ] **Step 1: Write the walker and the additivity test**

```python
"""Structural invariants: additivity by metric kind, plus per-tab identities."""
from __future__ import annotations

import pytest

from models.schemas import CarrierPeriodValue
from tests import compare_support as cs
from tests.golden import fixture as gf

REL = 1e-9


@pytest.fixture()
def golden(reset_backend):
    n = gf.solve_golden_network()
    gf.install_golden(n)
    return n


def _walk_period_values(obj, model_name=None, path=""):
    """Yield (model_name, field, label, CarrierPeriodValue) through the payload."""
    if isinstance(obj, CarrierPeriodValue):
        return
    model_name = model_name or type(obj).__name__
    for field in getattr(obj, "model_fields", {}):
        value = getattr(obj, field)
        label = f"{path}{model_name}.{field}"
        if isinstance(value, CarrierPeriodValue):
            yield model_name, field, label, value
        elif isinstance(value, dict):
            for key, item in value.items():
                if isinstance(item, CarrierPeriodValue):
                    yield model_name, field, f"{label}[{key}]", item
                else:
                    yield from _walk_period_values(item, path=f"{label}[{key}].")
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if hasattr(item, "model_fields"):
                    yield from _walk_period_values(item, path=f"{label}[{i}].")


def test_every_extensive_metric_sums_across_periods(golden):
    s = cs.summarise(golden)
    bad = []
    for tab in cs.TAB_FIELDS:
        for model_name, field, label, cpv in _walk_period_values(s[tab]):
            if cs.classify(model_name, field) != cs.EXTENSIVE:
                continue
            if not cpv.by_period:
                continue
            total_of_parts = sum(cpv.by_period.values())
            if abs(cpv.total) < 1e-12 and abs(total_of_parts) < 1e-12:
                continue
            if cpv.total == 0 or abs(total_of_parts / cpv.total - 1.0) > 1e-6:
                bad.append(f"{tab}: {label} total={cpv.total!r} "
                           f"sum(by_period)={total_of_parts!r}")
    assert not bad, "extensive metrics whose periods do not sum to the total:\n  " \
        + "\n  ".join(bad)


def test_no_intensive_metric_is_the_sum_of_its_periods(golden):
    """
    The mirror image, and the one that catches a well-meant "fix". An
    intensive metric equal to the sum of its periods on a multi-period network
    means someone applied the additivity rule where it does not belong.
    Skips the degenerate cases where sum and mean coincide.
    """
    s = cs.summarise(golden)
    bad = []
    for tab in cs.TAB_FIELDS:
        for model_name, field, label, cpv in _walk_period_values(s[tab]):
            if cs.classify(model_name, field) != cs.INTENSIVE:
                continue
            parts = [v for v in cpv.by_period.values()]
            if len(parts) < 2 or abs(cpv.total) < 1e-12:
                continue
            if all(abs(p) < 1e-12 for p in parts):
                continue
            if abs(sum(parts) / cpv.total - 1.0) < 1e-6:
                bad.append(f"{tab}: {label} total={cpv.total!r} == sum(by_period)")
    assert not bad, "intensive metrics reported as a sum:\n  " + "\n  ".join(bad)
```

- [ ] **Step 2: Run them**

Run: `pixi run gui-tests tests/test_compare_invariants.py -p no:warnings -q`
Expected: 2 passed.

Any failure here is a **finding**. Record the exact `label`, `total` and `sum(by_period)` in the findings doc, mark `xfail(strict=True)`, and open a fix task. Do not reclassify a metric to make the test pass — reclassification is only correct if the metric's *meaning* was misjudged in Task 1, which must be argued from the compute function, not from the test result.

- [ ] **Step 3: Commit**

```bash
git add pypsa-gui/backend/tests/test_compare_invariants.py
git commit -m "test(compare): additivity holds for extensive metrics and is absent from intensive ones"
```

---

### Tasks 5–13: per-tab invariants and cross-surface checks

Each task follows the same five steps. Write the test, run it, record or fix, re-run, commit. Per-tab detail follows; the shared steps are stated once here and are **not** optional:

1. Add the test(s) to `test_compare_invariants.py` (internal identities) and `test_compare_cross_surface.py` (counterpart agreement).
2. Run `pixi run gui-tests tests/test_compare_invariants.py tests/test_compare_cross_surface.py -p no:warnings -q`.
3. If red: establish root cause before touching anything, write the finding, `xfail(strict=True)` with a reason naming the findings section, and open a fix task in this plan.
4. Re-run to a known state.
5. Commit with `git add` naming the test files explicitly.

Cross-surface tests need the live endpoint reading the same network. `install_golden(n)` makes the golden network the live singleton, so the `/results/*` functions can be imported and called directly:

```python
import routers.results as R
econ = R.get_asset_economics()          # reads the installed singleton
```

---

### Task 5: Capacity tab

**Files:** `tests/test_compare_invariants.py`, `tests/test_compare_cross_surface.py`

- [ ] **Step 1: Internal identity — stock vs increment semantics**

`CapacityComparison`'s docstring says `by_period` holds "the sum of vintages with `build_year=P`", while `total` is `p_nom_opt` — the whole stock. Those are different quantities, so the relationship must be pinned rather than assumed:

```python
def test_installed_capacity_total_is_at_least_the_sum_of_its_vintages(golden):
    """
    `total` is the installed stock; `by_period` are the vintages built in each
    period. Pre-existing capacity has no vintage, so the sum of vintages can be
    less than the total but never more.
    """
    cap = cs.summarise(golden)["capacity"]
    for carrier, cpv in cap.capacity_mw_by_carrier.items():
        if not cpv.by_period:
            continue
        assert sum(cpv.by_period.values()) <= cpv.total * (1 + 1e-9), (
            f"{carrier}: vintages {cpv.by_period} exceed installed total {cpv.total}")


def test_new_capacity_never_exceeds_installed_capacity(golden):
    cap = cs.summarise(golden)["capacity"]
    for carrier, new in cap.new_capacity_mw_by_carrier.items():
        installed = cap.capacity_mw_by_carrier.get(carrier)
        assert installed is not None, f"{carrier} has new build but no installed entry"
        assert new.total <= installed.total * (1 + 1e-9)
```

- [ ] **Step 2: Cross-surface — CAPEX against `asset_costs`**

```python
def test_capacity_capex_agrees_with_periodized_capital_costs(golden):
    """
    Σ per-carrier capex_meur must equal Σ over assets of
    periodized_capital_costs × p_nom_opt × horizon years — the resolution
    asset_economics, cost_breakdown and asset_costs all share.
    """
    from services.solver_service import periodized_capital_costs
    from routers.simulation import _state

    cap = cs.summarise(golden)["capacity"]
    tab_total_eur = sum(c.total for c in cap.capex_meur_by_carrier.values()) * 1e6

    pcc = periodized_capital_costs(golden, _state.get("solver_config"))
    horizon_years = float(sum(gf.GOLDEN_YEARS))
    expected = 0.0
    for attr, nom in (("generators", "p_nom"), ("storage_units", "p_nom"),
                      ("stores", "e_nom")):
        df = getattr(golden, attr)
        for name in df.index:
            cc = pcc.get(attr, {}).get(name, {}).get("capital_cost", 0.0)
            opt = float(df.at[name, f"{nom}_opt"] if f"{nom}_opt" in df.columns
                        else df.at[name, nom])
            expected += cc * opt * horizon_years

    assert tab_total_eur == pytest.approx(expected, rel=1e-6)
```

- [ ] **Step 3–5:** run, record or fix, commit as `test(compare): pin capacity stock semantics and cross-check CAPEX`.

**Expect this task to surface suspect S1** (Task 14). The oracle above walks generators, storage_units and stores — the same three `_compute_total_annuitised_capex` walks — so it will PASS while still omitting links. That is deliberate: this task proves the capacity tab is self-consistent, and Task 14 asks the separate question of whether omitting links is right.

---

### Task 6: Dispatch tab

- [ ] **Step 1: Internal identity**

```python
def test_dispatch_energy_uses_the_generators_weighting_basis(golden):
    """
    Energy must be weighted by snapshot_weightings.generators — the basis
    n.statistics() and the Results-tab KPIs use. Recomputed here from PyPSA
    primitives so a change of basis in compare.py fails loudly.
    """
    from services import period_utils

    disp = cs.summarise(golden)["dispatch"]
    w = period_utils.snapshot_weights(golden, "generators", golden.snapshots)
    p = golden.generators_t.p
    expected_gwh = {}
    for gen in golden.generators.index:
        carrier = str(golden.generators.at[gen, "carrier"] or "unknown").lower()
        expected_gwh[carrier] = expected_gwh.get(carrier, 0.0) + \
            float((p[gen] * w).sum()) / 1e3
    for carrier, want in expected_gwh.items():
        got = disp.dispatch_gwh_by_carrier.get(carrier)
        assert got is not None, f"carrier {carrier} missing from dispatch tab"
        assert got.total == pytest.approx(want, rel=1e-6)
```

- [ ] **Step 2: Cross-surface against `/results/carrier_kpis`**

```python
def test_dispatch_agrees_with_carrier_kpis(golden):
    import routers.results as R

    disp = cs.summarise(golden)["dispatch"]
    kpis = R.get_carrier_kpis()
    # Shape check first — a silently-renamed key would otherwise make the
    # loop below iterate zero times and pass vacuously.
    assert kpis, "carrier_kpis returned nothing for a solved network"
    compared = 0
    for row in (kpis.get("carriers") or kpis.get("rows") or []):
        carrier = str(row.get("carrier", "")).lower()
        want = row.get("energy_gwh") if "energy_gwh" in row else None
        if carrier not in disp.dispatch_gwh_by_carrier or want is None:
            continue
        assert disp.dispatch_gwh_by_carrier[carrier].total == pytest.approx(want, rel=1e-6)
        compared += 1
    assert compared >= 1, "no carrier compared — key names have drifted"
```

The `compared >= 1` guard is load-bearing. Read the actual payload of `get_carrier_kpis()` first and correct the key names; a loop over a mis-keyed payload passes while checking nothing, which is the exact failure this whole plan exists to eliminate.

- [ ] **Step 3–5:** run, record or fix, commit as `test(compare): dispatch energy basis and carrier-KPI agreement`.

---

### Task 7: Line loading tab

- [ ] **Step 1: Internal identities**

```python
def test_binding_hours_never_exceed_the_horizon(golden):
    from services import period_utils

    loading = cs.summarise(golden)["loading"]
    horizon_hours = float(period_utils.snapshot_weights(
        golden, "generators", golden.snapshots).sum())
    for entry in loading.lines:
        assert entry.binding_hours.total <= horizon_hours * (1 + 1e-9), (
            f"{entry.name}: {entry.binding_hours.total} h binding of "
            f"{horizon_hours} h horizon")


def test_mean_loading_never_exceeds_peak_loading(golden):
    for entry in cs.summarise(golden)["loading"].lines:
        assert entry.mean_loading.total <= entry.peak_loading.total + 1e-9, entry.name
```

- [ ] **Step 2:** run, record or fix, commit as `test(compare): line-loading bounds`.

**This task's real target is suspect S2** (Task 15). Both invariants above hold under either weighting basis on the golden fixture, because `objective` and `generators` are equal there. The basis question is only decidable with the fixture Task 15 builds; do not conclude "loading is fine" from a green run here, and say so in the findings.

---

### Task 8: Prices tab

- [ ] **Step 1: Internal identities**

```python
def test_the_duration_curve_is_monotonically_non_increasing(golden):
    curve = cs.summarise(golden)["prices"].duration_curve
    assert curve, "empty duration curve on a solved network"
    assert all(curve[i] >= curve[i + 1] - 1e-9 for i in range(len(curve) - 1)), \
        "duration curve is not sorted descending"


def test_price_statistics_lie_inside_the_observed_range(golden):
    pr = cs.summarise(golden)["prices"]
    assert pr.min_price - 1e-9 <= pr.median_price.total <= pr.max_price + 1e-9
    assert pr.min_price - 1e-9 <= pr.mean_price.total <= pr.max_price + 1e-9
    for carrier, stats in pr.by_carrier_stats.items():
        assert pr.min_price - 1e-9 <= stats.mean_price.total <= pr.max_price + 1e-9, carrier
```

- [ ] **Step 2: Cross-surface against `/results/prices`** — read `R.get_prices()`'s actual shape first, then assert the tab's `mean_price.total` equals the same weighted mean computed from that payload, with a `compared >= 1` guard as in Task 6.

- [ ] **Step 3–5:** run, record or fix, commit as `test(compare): price duration curve and statistic bounds`.

---

### Task 9: Emissions tab

- [ ] **Step 1: Internal identity and cross-surface**

```python
def test_per_carrier_emissions_sum_to_the_total(golden):
    em = cs.summarise(golden)["emissions"]
    parts = sum(c.total for c in em.by_carrier_kt.values())
    assert parts == pytest.approx(em.total_kt.total, rel=1e-6)


def test_intensity_is_total_emissions_over_total_load(golden):
    s = cs.summarise(golden)
    em, disp = s["emissions"], s["dispatch"]
    if disp.total_load_gwh.total <= 0:
        pytest.skip("no load in the fixture — intensity is undefined")
    # kt / GWh → kg/MWh is a factor of 1000 (1 kt = 1e6 kg, 1 GWh = 1e3 MWh).
    expected = em.total_kt.total * 1e6 / (disp.total_load_gwh.total * 1e3)
    assert em.intensity_kg_per_mwh.total == pytest.approx(expected, rel=1e-6)


def test_emissions_agree_with_the_results_emissions_endpoint(golden):
    import routers.results as R

    em = cs.summarise(golden)["emissions"]
    live = R.get_emissions()
    assert live, "emissions endpoint returned nothing"
    total_key = next((k for k in ("total_kt", "total_co2_kt", "total") if k in live), None)
    assert total_key, f"no recognised total key in {sorted(live)}"
    assert em.total_kt.total == pytest.approx(live[total_key], rel=1e-6)
```

If `test_intensity_is_total_emissions_over_total_load` fails, check the denominator before assuming the numerator is wrong — intensity may be defined against generation rather than load. Whichever it is, pin it and state the definition in the findings.

- [ ] **Step 2–5:** run, record or fix, commit as `test(compare): emissions totals, intensity definition and endpoint agreement`.

---

### Task 10: Economics tab

- [ ] **Step 1: Internal identity — the LCOE quotient**

This is suspect S3. `_compute_economics_summary`'s docstring says `capex (€/yr)` and `LCOE = (Σ capex + Σ opex) / Σ dispatch_MWh`. If capex is per-year and dispatch is horizon-summed, LCOE is low by the horizon's year count — the defect fixed in `asset_results` on 2026-08-03 (commit 922eb4d0).

```python
def test_lcoe_is_total_cost_over_total_energy(golden):
    econ = cs.summarise(golden)["economics"]
    checked = 0
    for carrier, e in econ.by_carrier.items():
        if e.dispatch_gwh.total <= 0:
            continue
        expected = ((e.capex_meur.total + e.opex_meur.total) * 1e6) / \
                   (e.dispatch_gwh.total * 1e3)
        assert e.lcoe_eur_per_mwh.total == pytest.approx(expected, rel=1e-6), (
            f"{carrier}: LCOE {e.lcoe_eur_per_mwh.total} != "
            f"(capex {e.capex_meur.total} + opex {e.opex_meur.total}) M€ / "
            f"{e.dispatch_gwh.total} GWh")
        checked += 1
    assert checked >= 1, "no carrier with positive dispatch — vacuous test"
```

- [ ] **Step 2: Cross-surface against `asset_economics`**

```python
def test_compare_capex_agrees_with_asset_economics_per_carrier(golden):
    import routers.results as R

    econ = cs.summarise(golden)["economics"]
    live = R.get_asset_economics()
    by_carrier = {}
    for bucket in ("generators", "storage_units", "stores", "links"):
        for row in live.get(bucket, []) or []:
            c = str(row.get("carrier", "") or "").lower()
            by_carrier[c] = by_carrier.get(c, 0.0) + float(row.get("fixed_cost_eur") or 0.0)
    compared = 0
    for carrier, e in econ.by_carrier.items():
        if carrier not in by_carrier:
            continue
        assert e.capex_meur.total * 1e6 == pytest.approx(by_carrier[carrier], rel=1e-6), carrier
        compared += 1
    assert compared >= 1, "no carrier compared — asset_economics keys have drifted"
```

- [ ] **Step 3–5:** run, record or fix, commit as `test(compare): economics LCOE identity and CAPEX agreement with asset_economics`.

---

### Task 11: Curtailment tab

- [ ] **Step 1: Internal identities**

```python
def test_per_carrier_curtailment_sums_to_the_total(golden):
    cur = cs.summarise(golden)["curtailment"]
    parts = sum(c.total for c in cur.by_carrier_gwh.values())
    assert parts == pytest.approx(cur.total_gwh.total, rel=1e-6)


def test_curtailment_rate_is_a_percentage_between_zero_and_one_hundred(golden):
    cur = cs.summarise(golden)["curtailment"]
    for carrier, rate in cur.rate_pct_by_carrier.items():
        assert -1e-9 <= rate.total <= 100.0 + 1e-9, f"{carrier}: {rate.total}%"
    assert -1e-9 <= cur.system_rate_pct.total <= 100.0 + 1e-9
```

- [ ] **Step 2: Cross-surface against `/results/curtailment`**, reading the live payload's real keys first and guarding with `compared >= 1`.

- [ ] **Step 3–5:** run, record or fix, commit as `test(compare): curtailment totals and rate bounds`.

---

### Task 12: Lost load tab

- [ ] **Step 1: Internal identities**

```python
def test_lost_load_cost_is_energy_times_voll(golden):
    ll = cs.summarise(golden)["lost_load"]
    if not ll.available or ll.total_mwh.total <= 0:
        pytest.skip("fixture sheds no load — see Task 12 note on fixture extension")
    expected_meur = ll.total_mwh.total * ll.voll_eur_per_mwh / 1e6
    assert ll.total_cost_meur.total == pytest.approx(expected_meur, rel=1e-6)


def test_per_bus_and_per_carrier_lost_load_agree_with_the_total(golden):
    ll = cs.summarise(golden)["lost_load"]
    if not ll.available:
        pytest.skip("lost load not available on this network")
    assert sum(b.energy_mwh.total for b in ll.by_bus) == \
        pytest.approx(ll.total_mwh.total, rel=1e-6)
    assert sum(c.energy_mwh.total for c in ll.by_carrier) == \
        pytest.approx(ll.total_mwh.total, rel=1e-6)
```

- [ ] **Step 2: Decide about the fixture**

If both tests skip, the tab is untested. Before extending the fixture, check whether adding a VOLL slack changes the LP optimum for every other test that uses the golden network — it will if load is shed. **Prefer a separate, locally-built network in this test module over mutating the shared fixture.** State which you chose and why in the findings; a skipped test recorded as "passing" is the failure mode this plan exists to remove.

- [ ] **Step 3–5:** run, record or fix, commit as `test(compare): lost-load VOLL identity and breakdown consistency`.

---

### Task 13: Storage cycling tab

- [ ] **Step 1: Oracle — cycles from first principles**

No live counterpart exists, so this is recomputation:

```python
def test_cycles_are_throughput_over_twice_the_energy_capacity(golden):
    """
    One equivalent full cycle = a full charge AND discharge, so the
    denominator is 2 × energy capacity. Σ|p| counts both halves.
    """
    sc = cs.summarise(golden)["storage_cycling"]
    assert sc.by_unit, "no storage units in the summary"
    for u in sc.by_unit:
        if u.energy_mwh <= 0:
            continue
        expected = u.throughput_mwh.total / (2.0 * u.energy_mwh)
        assert u.cycles.total == pytest.approx(expected, rel=1e-6), (
            f"{u.name}: cycles {u.cycles.total} vs throughput "
            f"{u.throughput_mwh.total} / (2 × {u.energy_mwh})")


def test_horizon_cycles_are_the_average_of_periods_not_the_sum(golden):
    """
    Documented behaviour: a unit cycling 100×/yr in every period reads 100 for
    the horizon, not 300. Guards against a well-meant additivity "fix".
    """
    sc = cs.summarise(golden)["storage_cycling"]
    for u in sc.by_unit:
        if len(u.cycles.by_period) < 2:
            continue
        parts = list(u.cycles.by_period.values())
        assert u.cycles.total == pytest.approx(sum(parts) / len(parts), rel=1e-6), u.name
```

Read `_compute_storage_cycling_summary` before running: if its denominator is `1 ×` energy capacity rather than `2 ×`, the first test fails and the question is which convention the UI labels. Resolve it against the tab's own description string in `CompareView.tsx` — "One cycle = a full charge + discharge of total energy capacity" — and record the resolution.

- [ ] **Step 2–5:** run, record or fix, commit as `test(compare): storage cycling oracle and average-not-sum horizon rule`.

---

### Task 14: Suspect S1 — link CAPEX omitted from the Capacity tab

**Files:** `tests/test_compare_cross_surface.py`, possibly `routers/compare.py`, findings doc.

- [ ] **Step 1: Measure the disagreement**

```python
def test_capacity_and_economics_agree_on_total_capex(golden):
    """
    Two tabs of one comparison must not report different CAPEX for one
    network. _compute_total_annuitised_capex walks Generator/StorageUnit/Store;
    _compute_economics_summary walks those plus Link.
    """
    s = cs.summarise(golden)
    cap_total = sum(c.total for c in s["capacity"].capex_meur_by_carrier.values())
    econ_total = sum(e.capex_meur.total for e in s["economics"].by_carrier.values())
    assert cap_total == pytest.approx(econ_total, rel=1e-6), (
        f"Capacity tab {cap_total} M€ vs Economics tab {econ_total} M€")
```

- [ ] **Step 2: Run and record the measured gap**

Run: `pixi run gui-tests tests/test_compare_cross_surface.py::test_capacity_and_economics_agree_on_total_capex -p no:warnings -q`

Expected: FAIL, by the golden electrolyzer's CAPEX. Record both numbers and the difference in the findings.

- [ ] **Step 3: Escalate, do not decide**

The omission is deliberate and commented (`routers/compare.py`, `_walk`'s trailing comment): lines and links are excluded because `n.statistics()` reports passive branches as zero CAPEX. That reasoning holds for a fixed line and fails for an **extendable link**, which does enter the LP objective. Two defensible resolutions:

- include extendable links (`p_nom_extendable == True`) in `_compute_total_annuitised_capex`, leaving passive branches out; or
- keep the omission and surface it in the Capacity tab's UI copy.

This is a product decision. Mark the test `@pytest.mark.xfail(strict=True, reason="S1: Capacity omits link CAPEX that Economics counts — awaiting product decision, findings §S1")`, write the findings entry with both numbers, and **stop**. Do not implement either resolution without an answer.

- [ ] **Step 4: Commit**

```bash
git add pypsa-gui/backend/tests/test_compare_cross_surface.py docs/superpowers/findings/2026-08-03-compare-tab-correctness.md
git commit -m "test(compare): record the Capacity/Economics CAPEX disagreement (S1, xfail pending decision)"
```

---

### Task 15: Suspect S2 — the hours basis in Loading and Prices

Both call `_build_snapshot_weights(n)`, which defaults to `objective` (the COST basis), then use the result for binding-hour counts and the duration curve's hours axis. The helper's own docstring assigns hours and energy to `generators`. The columns are identical on an ordinary hourly year, so the golden fixture cannot decide this.

- [ ] **Step 1: Build a network where the two columns genuinely differ**

```python
def _rep_week_network():
    """
    A network whose objective and generators weighting columns differ, which is
    what a representative-period run produces and what makes the basis choice
    observable at all.
    """
    n = gf.solve_golden_network()
    n.snapshot_weightings["objective"] = 1.0
    n.snapshot_weightings["generators"] = 3.0
    return n


def test_binding_hours_use_the_energy_basis_not_the_cost_basis():
    import routers.compare as CMP
    from services import period_utils

    n = _rep_week_network()
    gf.install_golden(n)
    periods = sorted({int(p) for p in n.snapshots.get_level_values(0)})
    loading = CMP._compute_loading_summary(n, periods, True, True)

    gen_hours = float(period_utils.snapshot_weights(n, "generators", n.snapshots).sum())
    obj_hours = float(period_utils.snapshot_weights(n, "objective", n.snapshots).sum())
    assert gen_hours != pytest.approx(obj_hours), "fixture failed to separate the bases"

    binding = [e.binding_hours.total for e in loading.lines if e.binding_hours.total > 0]
    if not binding:
        pytest.skip("no branch is ever binding in this fixture — see Step 2")
    assert max(binding) <= gen_hours * (1 + 1e-9)
    assert max(binding) > obj_hours * (1 + 1e-9) or \
        all(b <= obj_hours for b in binding), \
        "binding hours are reported on the cost basis"
```

- [ ] **Step 2: If nothing is ever binding, make something bind**

A skip here means the test proves nothing. Reduce a line's `s_nom` in the local network until it reaches its limit, and note the change in the test's docstring. Do not mutate `tests/golden/fixture.py` — other tests depend on its LP optimum.

- [ ] **Step 3: Run, then fix or clear**

If the test shows hours on the cost basis, the fix is one argument at two call sites — `_build_snapshot_weights(n, "generators")` in `_compute_loading_summary` and in `_compute_prices_summary` — but verify the prices case separately: a duration curve's hours axis and a cost-weighted mean price are different questions, and only the first is unambiguously an hours quantity.

If the measurement shows the current basis is right, say so plainly in the findings and delete the suspect. A cleared suspect is a result.

- [ ] **Step 4: Commit** as `fix(gui): count binding hours on the energy weighting basis` or `docs: clear suspect S2 — loading and prices already use the intended basis`.

---

### Task 16: Suspect S3 — resolution

Task 10 Step 1 decides this. If `test_lcoe_is_total_cost_over_total_energy` passed, S3 is cleared — record it in the findings as cleared, with the measured numbers, and close the task.

If it failed, the fix mirrors commit 922eb4d0:

- [ ] **Step 1:** Establish which term is on the wrong basis by printing `capex_meur.total`, `opex_meur.total`, `dispatch_gwh.total` and the horizon year count for one carrier.
- [ ] **Step 2:** Scale the annual term by the horizon years, using `period_utils.period_years_map(n)` — the same source `total_years_factor` uses in `routers/results.py`. Do not introduce a second way of computing the factor.
- [ ] **Step 3:** Re-run Task 10's test plus `pixi run gui-tests` in full.
- [ ] **Step 4:** Commit as `fix(gui): put Compare economics LCOE on one time basis`.

---

### Task 17: Frontend A/A identity

The backend computes no delta. `CompareView.tsx` fetches A and B through two `useQuery` calls per tab and subtracts them in JSX. Nothing in that file is exported, so these tests render.

**Files:** Create `pypsa-gui/frontend/src/pages/CompareView.test.tsx`

- [ ] **Step 1: Write the harness and the identity test**

```tsx
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

vi.mock('../api/projects')

import { projectsApi } from '../api/projects'
import CompareView from './CompareView'

/** Minimal but non-trivial payload: two periods, non-zero values everywhere. */
const summary = (project: string) => ({
  project, is_multi_period: true, periods: [2030, 2035], has_solve: true,
  capacity: {
    capacity_mw_by_carrier: { solar: { total: 200, by_period: { '2030': 200, '2035': 0 } } },
    capex_meur_by_carrier: { solar: { total: 82.5, by_period: { '2030': 27.5, '2035': 55 } } },
    new_capex_meur_by_carrier: {}, new_capacity_mw_by_carrier: {},
    storage_mw_by_carrier: {}, storage_mwh_by_carrier: {},
    new_storage_mw_by_carrier: {}, new_storage_mwh_by_carrier: {},
    link_capacity_mw_by_carrier: {}, new_link_capacity_mw_by_carrier: {},
  },
  dispatch: {
    dispatch_gwh_by_carrier: { solar: { total: 300, by_period: { '2030': 100, '2035': 200 } } },
    opex_meur: { total: 12, by_period: { '2030': 4, '2035': 8 } },
    total_load_gwh: { total: 300, by_period: { '2030': 100, '2035': 200 } },
    storage_cycles_by_carrier: {},
  },
  loading: { lines: [] }, prices: null, emissions: null, economics: null,
  curtailment: null, lost_load: null, storage_cycling: null,
})

const renderCompare = () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><CompareView /></QueryClientProvider>)
}

beforeEach(() => {
  vi.mocked(projectsApi.resultsSummary).mockImplementation(
    async (name: string) => summary(name) as never)
})

describe('A/A identity', () => {
  it('shows no non-zero delta when both sides are the same payload', async () => {
    renderCompare()
    await waitFor(() => expect(screen.queryByText(/82\.5/)).toBeTruthy())
    // Every delta cell is rendered by <Delta>, which formats a signed number.
    // With identical inputs none may carry a sign.
    const signed = screen.queryAllByText(/^[+−-]\s*\d/)
    expect(signed.map(e => e.textContent)).toEqual([])
  })
})
```

- [ ] **Step 2: Make it run**

`CompareView` reads persisted A/B selection from `localStorage` and needs both projects picked before it renders a tab (`bothPicked`). Read `storedCmp`/`persistCmp` and the `CMP_*_KEY` constants at the top of `CompareView.tsx` and seed `localStorage` in `beforeEach`. jsdom 29 exposes `localStorage` as a bare `{}` in this project — the same trap `topologyLayoutStore.test.tsx` documents — so stub it explicitly rather than assuming it works.

- [ ] **Step 3:** Run `npx vitest run src/pages/CompareView.test.tsx`. Expected: pass. A non-empty `signed` array is a **finding**: record which tab and which cell.

- [ ] **Step 4: Commit** as `test(compare): identical payloads must produce no deltas`.

---

### Task 18: Frontend delta arithmetic

- [ ] **Step 1: Sign convention**

Give B a larger value than A and assert the rendered delta is positive on every tab; then reverse and assert negative. `<Delta>` takes an `invert` prop used inconsistently across tabs (`invert` on prices/loading/economics, bare on storage cycling, `neutral` on lost load). `invert` controls colour, not sign — confirm that by reading the component before writing the assertion, and test the **number**, not the colour.

- [ ] **Step 2: Zero and absent baselines**

```tsx
it('does not report a 100% reduction when a tab is absent on one side', async () => {
  vi.mocked(projectsApi.resultsSummary).mockImplementation(async (name: string) => {
    const s = summary(name)
    if (name === 'B') s.emissions = null
    return s as never
  })
  renderCompare()
  await waitFor(() => expect(screen.queryByText(/-100/)).toBeNull())
})
```

A `null` tab means "not reported", not "zero". If the UI renders it as a 100% reduction, that is a finding.

- [ ] **Step 3: Period selection** — with the period selector on 2030, both sides must read `by_period['2030']`; neither may fall back to `total`. Assert against the fixture's deliberately asymmetric values (`2030: 27.5`, `2035: 55`, total `82.5`), which make a fallback visible.

- [ ] **Step 4:** Run, record or fix, commit as `test(compare): delta signs, absent baselines and period selection`.

---

### Task 19: Endpoint wiring

- [ ] **Step 1:** Create `tests/test_compare_endpoint.py` using `client` and the `api_project` fixture (which creates the DB row and org-scoped storage that `ProjectAccessDep` requires). Install the golden network, save it as a project, `GET /api/projects/<name>/results-summary`, and assert all nine optional fields are non-null and that `capacity.capex_meur_by_carrier` matches what `compare_support.summarise` produced for the same network.
- [ ] **Step 2:** Run, record or fix, commit as `test(compare): the summary endpoint serialises every tab`.

---

### Task 20: Coverage matrix

- [ ] **Step 1:** Add the eight uncovered tabs to `tests/golden/coverage.py::SURFACES` with an entry each in `COVERAGE` (or in the `NO_ADAPTER_REASONS` map with a stated reason). `test_golden_coverage.py` asserts `set(ADAPTERS) | set(NO_ADAPTER_REASONS) == set(coverage.SURFACES)`, so an unexplained addition fails the build — which is the point.
- [ ] **Step 2:** Run `pixi run gui-tests tests/test_golden_coverage.py`, then the full suite.
- [ ] **Step 3:** Commit as `test(compare): bring all ten tabs into the coverage matrix`.

---

### Task 21: Real-project spot check and findings

- [ ] **Step 1:** With the app running, `curl` `/api/projects/3_nodes_system/results-summary` and run the same invariants from `compare_support` against the live payload. This is evidence, not a regression guard — the project is user data and cannot be committed.
- [ ] **Step 2:** Finish `docs/superpowers/findings/2026-08-03-compare-tab-correctness.md`: one section per tab; every claim marked MEASURED or INFERRED; agreements recorded explicitly so "no complaint" is never read as "not checked"; suspects S1–S3 each resolved as fixed, cleared or escalated.
- [ ] **Step 3:** Run `pixi run gui-tests` and `npx vitest run` in full; both must be at or above baseline.
- [ ] **Step 4:** Commit as `docs: record the Compare tab correctness findings`.

---

## Self-Review

**Spec coverage.** Cross-surface → Tasks 5–13. Invariants → Tasks 4–13. Oracle → Tasks 5, 6, 13. Extensive/intensive classification → Tasks 1, 4. A/A identity → Task 17 (frontend, per the spec's correction). Backend determinism → Task 3. Suspects S1/S2/S3 → Tasks 14/15/16. Frontend derived values → Tasks 17, 18. Endpoint wiring → Task 19. Coverage matrix → Task 20. Findings doc and spot check → Task 21. No spec section is unimplemented.

**Known softness, stated rather than hidden.** Tasks 6, 8, 11 assert against live endpoints whose exact payload keys were not read while writing this plan. Each carries a `compared >= 1` guard so a mis-keyed loop fails loudly instead of passing vacuously, and each step says to read the real payload first. That is a deliberate trade — inventing key names here would be worse than instructing the implementer to look.

**Fix tasks are conditional by nature.** This is an examination; which defects exist is the output, not the input. Tasks 14–16 are written for the three suspects a code read identified. Any further defect found in Tasks 5–13 gets an `xfail(strict=True)`, a findings entry, and its own fix task appended — the protocol is stated in the shared steps before Task 5 rather than repeated per task.
