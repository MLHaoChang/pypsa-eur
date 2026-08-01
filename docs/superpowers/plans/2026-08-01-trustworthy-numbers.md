# Structurally Trustworthy Numbers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make it structurally impossible for a cost-bearing number to be silently wrong or silently absent across the app's nine economic surfaces.

**Architecture:** One small network solved for real by HiGHS becomes a shared fixture. An oracle module that imports nothing from `services/` recomputes the expected economics from first principles. One test asserts every surface against the oracle (catching consistent wrongness) and against every other surface (catching drift), while an exhaustive-by-default coverage matrix makes a missing component class a failure rather than a silence.

**Tech Stack:** Python 3.13, pytest, PyPSA 1.1.2, HiGHS, FastAPI; TypeScript, vitest.

**Spec:** `docs/superpowers/specs/2026-08-01-trustworthy-numbers-design.md`

## Global Constraints

- **The oracle must never import from `services/`.** Task 2 enforces this with an AST test. An oracle sharing an implementation with its subject asserts nothing.
- **The oracle must never call `periodized_capital_costs` or `with_periodized_cost_defaults`** to build an expectation.
- Run the suite with `pixi run gui-tests <path>` from the repo root. Never a bare `pytest`, never a hardcoded interpreter path.
- Frontend checks: `cd pypsa-gui/frontend && PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx vitest run <path>`.
- Capture real exit codes with redirect (`cmd > log 2>&1; echo $?`), never through a pipe — a pipeline reports only its last stage.
- `pypsa-gui/backend/services/asset_results/compute.py` is the **concurrent session's** file (last commit `e1f8dc47`, 2026-08-01 08:58). Re-run `git branch --show-current` and `git status --porcelain` immediately before any commit, and use path-limited `git commit <paths>` — never `git add -A`.
- Never write to or delete under `pypsa-gui/backend/projects/` or `~/Documents/PyPSA GUI/Projects/`.

### Verified PyPSA facts this plan depends on

Measured 2026-08-01; do not re-derive, and do not "simplify" the oracle to drop them.

1. `n.generators.capital_cost` (the **DataFrame column**) stays `0.0` even when `overnight_cost`, `lifetime` and `discount_rate` are all set. The derivation lives on the **components accessor**, `n.c["Generator"].capital_cost`. This distinction is the bug.
2. The accessor returns `overnight_cost × CRF(discount_rate, lifetime) × (snapshots_in_one_period / 8760)` where `CRF(r, n) = r / (1 - (1+r)**-n)`. Verified exactly at 2, 24, 168 and 8760 snapshots.
3. **Multi-period normalises by ONE period's snapshot count, not the total.** With 2 periods × 24 snapshots the factor is `24/8760`, not `48/8760`.
4. `investment_period_weightings["years"]` is applied by the statistics/reporting layer, *not* baked into `capital_cost`.
5. Defaults: `capital_cost`, `marginal_cost`, `fom_cost` → `0.0`; `overnight_cost`, `discount_rate` → `NaN`; `lifetime` → `inf`.
6. `tests/conftest.py`'s `reset_backend` fixture is **`autouse=True`** and calls `PyPSAService.reset_network()` **before and after every test**, and resets `sim_router._state["solver_config"] = SolverConfig()`. A session-scoped solved network therefore cannot stay installed — it must be re-installed per test.

---

## File Structure

| Path | Responsibility |
|---|---|
| `pypsa-gui/backend/tests/golden/__init__.py` | package marker |
| `pypsa-gui/backend/tests/golden/fixture.py` | builds + solves the golden network; installs it per test |
| `pypsa-gui/backend/tests/golden/oracle.py` | independent arithmetic. Imports nothing from `services/` |
| `pypsa-gui/backend/tests/golden/coverage.py` | `COVERAGE` + `EXCLUSIONS` tables |
| `pypsa-gui/backend/tests/test_golden_fixture.py` | the fixture itself is correct |
| `pypsa-gui/backend/tests/test_golden_oracle.py` | the oracle is independent and arithmetically right |
| `pypsa-gui/backend/tests/test_golden_coverage.py` | every class × surface is covered or excluded-with-reason |
| `pypsa-gui/backend/tests/test_golden_economics.py` | anchors + cross-surface agreement |
| `pypsa-gui/frontend/src/pages/results/__fixtures__/asset-economics.golden.json` | payload emitted by the backend test |
| `pypsa-gui/frontend/src/pages/results/Economics.mapping.test.ts` | pure mapping-function assertions |

---

## Task 1: The golden fixture

**Files:**
- Create: `pypsa-gui/backend/tests/golden/__init__.py`
- Create: `pypsa-gui/backend/tests/golden/fixture.py`
- Test: `pypsa-gui/backend/tests/test_golden_fixture.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `build_golden_network() -> pypsa.Network` — unsolved.
  - `solve_golden_network() -> pypsa.Network` — built + solved, cached at module level.
  - `GOLDEN_DISCOUNT_RATE: float = 0.07`
  - `GOLDEN_PERIODS: tuple[int, int] = (2030, 2035)`
  - `GOLDEN_YEARS: tuple[int, int] = (5, 10)`
  - `SNAPSHOTS_PER_PERIOD: int = 24`
  - `install_golden(network) -> None` — installs into the active `ProjectContext` and sets the solver config.

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/backend/tests/test_golden_fixture.py`:

```python
"""
The golden network must contain every shape that has actually produced a wrong
number, and must solve. If this file fails, every other golden test is
meaningless — so it asserts the fixture's composition, not just that it runs.
"""
from __future__ import annotations

import pandas as pd
import pytest

from tests.golden import fixture as gf


@pytest.fixture(scope="module")
def solved():
    return gf.solve_golden_network()


def test_it_solves(solved):
    assert solved.is_solved


def test_it_is_multi_period_with_different_year_weightings(solved):
    # The 22% CAPEX gap appears only in multi-period, and only bites when the
    # periods carry DIFFERENT weights — equal weights hide an averaging bug.
    assert isinstance(solved.snapshots, pd.MultiIndex)
    assert list(solved.investment_periods) == list(gf.GOLDEN_PERIODS)
    years = solved.investment_period_weightings["years"].tolist()
    assert years == list(gf.GOLDEN_YEARS)
    assert len(set(years)) == 2, "equal year weights would hide the bug this exists to catch"


def test_the_overnight_shape_is_present_and_has_no_raw_capital_cost(solved):
    # The exact shape that made Asset Detail read 22-100% low: cost supplied
    # via overnight_cost, with capital_cost left at its 0.0 default.
    assert solved.generators.at["gas", "overnight_cost"] == 900_000.0
    assert solved.generators.at["gas", "capital_cost"] == 0.0
    assert solved.generators.at["gas", "p_nom_extendable"]


def test_the_direct_capital_cost_shape_is_present(solved):
    # The shape that already works. Proves a fix does not regress it.
    assert solved.lines.at["L_ab", "capital_cost"] == 1_000_000.0
    assert pd.isna(solved.lines.at["L_ab", "overnight_cost"])


def test_the_link_class_is_present(solved):
    # The class that was missing from /results/asset_economics entirely.
    assert "electrolyzer" in solved.links.index
    assert solved.links.at["electrolyzer", "efficiency"] == 0.7


def test_a_genuinely_zero_cost_asset_is_present(solved):
    # Proves a real zero still reports zero and is not flagged as unresolvable.
    assert solved.storage_units.at["bess", "capital_cost"] == 0.0
    assert pd.isna(solved.storage_units.at["bess", "overnight_cost"])


def test_both_extendable_and_non_extendable_are_present(solved):
    ext = solved.generators["p_nom_extendable"]
    assert ext.any() and not ext.all()


def test_install_survives_the_autouse_reset(solved):
    # conftest's reset_backend is autouse and calls reset_network() BEFORE every
    # test, so the fixture must re-install rather than rely on session state.
    from services.pypsa_service import PyPSAService
    import routers.simulation as sim_router

    gf.install_golden(solved)

    assert PyPSAService.get_network() is solved
    assert sim_router._state["solver_config"].discount_rate == gf.GOLDEN_DISCOUNT_RATE
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run gui-tests tests/test_golden_fixture.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'tests.golden'`

- [ ] **Step 3: Write the fixture**

Create `pypsa-gui/backend/tests/golden/__init__.py` (empty file).

Create `pypsa-gui/backend/tests/golden/fixture.py`:

```python
"""
The golden network: one small, real, SOLVED network containing every shape that
has produced a wrong number in this app.

Solved for real rather than hand-built. 11 backend test files already call
`.optimize()`, so this follows convention — but the deciding reason is that a
hand-built solved state encodes the author's belief about PyPSA's sign and
weighting conventions. If that belief is wrong, every surface agrees with every
other and all of them are wrong. Letting HiGHS produce the dispatch removes
that failure mode entirely.

Composition is deliberately MODERATE: only shapes that have actually failed.
It grows by incident, not by imagination.
"""
from __future__ import annotations

import pandas as pd
import pypsa

GOLDEN_DISCOUNT_RATE = 0.07
GOLDEN_PERIODS = (2030, 2035)
# DIFFERENT on purpose. Equal weights would hide an averaging bug, which is
# exactly the class of error the 22% Asset Detail gap belonged to.
GOLDEN_YEARS = (5, 10)
SNAPSHOTS_PER_PERIOD = 24

_SOLVED: pypsa.Network | None = None


def build_golden_network() -> pypsa.Network:
    n = pypsa.Network()

    timesteps = pd.date_range("2030-01-01", periods=SNAPSHOTS_PER_PERIOD, freq="h")
    idx = pd.MultiIndex.from_product(
        [list(GOLDEN_PERIODS), timesteps], names=["period", "timestep"]
    )
    # CLAUDE.md: a MultiIndex built with from_product has `.name = None`, and a
    # multi->multi rebuild propagates that to every _t table, after which
    # linopy reports `dim_0 is not a valid dimension`. Always set it.
    idx.name = "snapshot"
    n.set_snapshots(idx)
    n.investment_periods = list(GOLDEN_PERIODS)
    n.investment_period_weightings["years"] = list(GOLDEN_YEARS)

    n.add("Bus", "elec_a")
    n.add("Bus", "elec_b")
    n.add("Bus", "h2", carrier="H2")
    n.add("Carrier", "H2")
    n.add("Carrier", "gas", co2_emissions=0.2)
    n.add("Carrier", "solar")

    # --- the shape that broke: cost via overnight_cost, capital_cost unset ---
    n.add(
        "Generator", "gas",
        bus="elec_a", carrier="gas",
        p_nom=100.0, p_nom_extendable=True, p_nom_max=1000.0,
        marginal_cost=50.0,
        overnight_cost=900_000.0, lifetime=25.0,
        build_year=GOLDEN_PERIODS[0],
    )
    # --- the shape that works: capital_cost supplied directly, NOT extendable
    n.add(
        "Generator", "solar",
        bus="elec_b", carrier="solar",
        p_nom=200.0, p_nom_extendable=False,
        marginal_cost=0.0, capital_cost=27_500.0,
        build_year=GOLDEN_PERIODS[0],
    )
    # --- direct capital_cost on a Line
    n.add(
        "Line", "L_ab",
        bus0="elec_a", bus1="elec_b",
        s_nom=500.0, x=0.1, r=0.01,
        capital_cost=1_000_000.0,
    )
    # --- the class that was missing entirely
    n.add(
        "Link", "electrolyzer",
        bus0="elec_a", bus1="h2", carrier="H2",
        efficiency=0.7,
        p_nom=50.0, p_nom_extendable=True, p_nom_max=500.0,
        marginal_cost=10.0,
        overnight_cost=1_500_000.0, lifetime=20.0,
        build_year=GOLDEN_PERIODS[0],
    )
    # --- a genuinely zero-cost asset: zero must stay zero, not become "unset"
    n.add(
        "StorageUnit", "bess",
        bus="elec_b", p_nom=50.0, p_nom_extendable=False,
        max_hours=4.0, capital_cost=0.0, marginal_cost=0.0,
    )

    n.add("Load", "demand_e", bus="elec_b", p_set=120.0)
    n.add("Load", "demand_h2", bus="h2", p_set=20.0)
    return n


def solve_golden_network() -> pypsa.Network:
    """Build + solve once per process. HiGHS on 48 snapshots is sub-second."""
    global _SOLVED
    if _SOLVED is None:
        n = build_golden_network()
        n.optimize(solver_name="highs", multi_investment_periods=True)
        _SOLVED = n
    return _SOLVED


def install_golden(network: pypsa.Network) -> None:
    """
    Install into the active context and pin the solver config.

    Required per test, not per session: conftest's `reset_backend` is
    autouse and calls `PyPSAService.reset_network()` both before and after
    EVERY test, and resets `solver_config` to `SolverConfig()`. Without the
    re-install the network is empty and the discount rate is a default.
    """
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig
    import routers.simulation as sim_router

    ctx = PyPSAService._ensure_active()
    ctx.network = network
    sim_router._state["solver_config"] = SolverConfig(
        discount_rate=GOLDEN_DISCOUNT_RATE,
        multi_investment_periods=True,
        investment_periods=list(GOLDEN_PERIODS),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pixi run gui-tests tests/test_golden_fixture.py -q`
Expected: PASS (9 tests)

If `n.optimize(...)` reports infeasible, raise `demand_e`/`demand_h2` or `p_nom_max` until it solves — the fixture must be feasible with headroom, not tight.

- [ ] **Step 5: Commit**

```bash
cd "$(git rev-parse --show-toplevel)"
git branch --show-current
git status --porcelain
git add pypsa-gui/backend/tests/golden/__init__.py \
        pypsa-gui/backend/tests/golden/fixture.py \
        pypsa-gui/backend/tests/test_golden_fixture.py
git commit -m "test(gui): golden network fixture for economic consistency"
```

---

## Task 2: The independent oracle

**Files:**
- Create: `pypsa-gui/backend/tests/golden/oracle.py`
- Test: `pypsa-gui/backend/tests/test_golden_oracle.py`

**Interfaces:**
- Consumes: `tests.golden.fixture` constants.
- Produces:
  - `crf(rate: float, lifetime: float) -> float`
  - `annualised_capital_cost(overnight_cost: float, rate: float, lifetime: float, snapshots_per_period: int) -> float`
  - `horizon_capex(rate_per_mw: float, p_nom_opt: float, years: tuple[int, ...]) -> float`

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/backend/tests/test_golden_oracle.py`:

```python
"""
The oracle must be arithmetically right AND structurally independent.

Independence is not a style preference. This session produced a test that
passed against a never-reset deque and asserted nothing; an oracle that calls
the helper it is checking is the same failure with better manners. The AST test
below makes the independence mechanical rather than aspirational.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from tests.golden import oracle


def test_crf_matches_the_textbook_capital_recovery_factor():
    # 0.07 / (1 - 1.07^-25), worked by hand to 10 places.
    assert oracle.crf(0.07, 25.0) == pytest.approx(0.0858105172, abs=1e-10)


def test_crf_of_a_zero_rate_is_straight_line_depreciation():
    # r -> 0 is a removable singularity: the limit is 1/n. A naive formula
    # divides by zero here.
    assert oracle.crf(0.0, 25.0) == pytest.approx(1.0 / 25.0)


def test_annualised_capital_cost_scales_by_one_periods_snapshots():
    # MEASURED against PyPSA 1.1.2: the components accessor returns
    # overnight x CRF x (snapshots_in_ONE_period / 8760). Multi-period
    # normalises by one period, NOT the total across periods.
    got = oracle.annualised_capital_cost(
        overnight_cost=900_000.0, rate=0.07, lifetime=25.0, snapshots_per_period=24
    )
    assert got == pytest.approx(211.5876, abs=1e-3)


def test_annualised_capital_cost_at_a_full_year_is_the_plain_annuity():
    got = oracle.annualised_capital_cost(
        overnight_cost=900_000.0, rate=0.07, lifetime=25.0, snapshots_per_period=8760
    )
    assert got == pytest.approx(77_229.4655, abs=1e-3)


def test_horizon_capex_weights_by_investment_period_years():
    # years are applied by the reporting layer, NOT baked into capital_cost.
    assert oracle.horizon_capex(100.0, 2.0, (5, 10)) == pytest.approx(100.0 * 2.0 * 15)


def test_the_oracle_imports_nothing_from_services():
    """
    STRUCTURAL GUARANTEE, not a convention. An oracle that shares an
    implementation with its subject is a tautology; this makes that verifiable
    by reading the import block.
    """
    src = pathlib.Path(oracle.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)

    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)

    banned = [m for m in imported if m.split(".")[0] in {"services", "routers"}]
    assert not banned, (
        f"oracle.py must not import from services/ or routers/: {banned}. "
        "It exists to check them from the outside."
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run gui-tests tests/test_golden_oracle.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'tests.golden.oracle'`

- [ ] **Step 3: Write the oracle**

Create `pypsa-gui/backend/tests/golden/oracle.py`:

```python
"""
Expected economics, computed from first principles.

This module MUST NOT import from `services/` or `routers/`. It exists to check
them from the outside; sharing their arithmetic would make every assertion a
tautology. `test_golden_oracle.py` enforces that with an AST scan.

The formulas below were verified empirically against PyPSA 1.1.2 on
2026-08-01. If PyPSA changes an upstream convention these tests fail and it
will be briefly ambiguous whether the app or this file is wrong. That is the
intended behaviour: a SILENT convention change is what produced NaN CAPEX
across every asset parameterised via overnight_cost.
"""
from __future__ import annotations

HOURS_PER_YEAR = 8760


def crf(rate: float, lifetime: float) -> float:
    """
    Capital recovery factor: r / (1 - (1+r)^-n).

    At r = 0 the closed form divides by zero, but the limit is straight-line
    depreciation, 1/n. PyPSA allows a zero discount rate, so the branch is
    reachable rather than defensive.
    """
    if rate == 0:
        return 1.0 / lifetime
    return rate / (1.0 - (1.0 + rate) ** -lifetime)


def annualised_capital_cost(
    overnight_cost: float,
    rate: float,
    lifetime: float,
    snapshots_per_period: int,
) -> float:
    """
    What PyPSA's components accessor returns for `capital_cost`, per MW.

    MEASURED: overnight x CRF x (snapshots_in_ONE_period / 8760). Exact at
    2, 24, 168 and 8760 snapshots.

    The scaling is the trap. Omit it and a 24-snapshot fixture is wrong by a
    factor of 365 — and the "fix" would be to break working code.

    Multi-period normalises by ONE period's snapshot count, not the total:
    2 periods x 24 snapshots gives 24/8760, never 48/8760.
    """
    return overnight_cost * crf(rate, lifetime) * (snapshots_per_period / HOURS_PER_YEAR)


def horizon_capex(rate_per_mw: float, p_nom_opt: float, years: tuple[int, ...]) -> float:
    """
    Total CAPEX over the planning horizon.

    `investment_period_weightings["years"]` is applied by the reporting layer,
    NOT baked into capital_cost — so it multiplies here rather than inside
    `annualised_capital_cost`.
    """
    return rate_per_mw * p_nom_opt * sum(years)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pixi run gui-tests tests/test_golden_oracle.py -q`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
cd "$(git rev-parse --show-toplevel)"
git branch --show-current && git status --porcelain
git add pypsa-gui/backend/tests/golden/oracle.py \
        pypsa-gui/backend/tests/test_golden_oracle.py
git commit -m "test(gui): independent economics oracle with AST-enforced isolation"
```

---

## Task 3: The coverage matrix

**Files:**
- Create: `pypsa-gui/backend/tests/golden/coverage.py`
- Test: `pypsa-gui/backend/tests/test_golden_coverage.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `SURFACES: tuple[str, ...]` — the nine surface ids.
  - `COVERAGE: dict[str, set[str]]` — surface id → component classes it reports.
  - `EXCLUSIONS: dict[tuple[str, str], str]` — (surface, class) → written reason.
  - `FIXTURE_CLASSES: frozenset[str]` — classes present in the golden network.

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/backend/tests/test_golden_coverage.py`:

```python
"""
Exhaustive-by-default: every fixture class, on every surface, is either
COVERED or EXCLUDED with a written reason.

Opt-in was rejected because it reproduces the exact failure being fixed.
Forgetting to opt Links into asset_economics produces SILENCE, and silence is
how the missing Link block shipped. Inverting the default turns an absence into
a decision with a name on it.
"""
from __future__ import annotations

from tests.golden import coverage as cov


def test_every_class_on_every_surface_is_covered_or_excluded():
    holes = []
    for surface in cov.SURFACES:
        covered = cov.COVERAGE.get(surface, set())
        for cls in sorted(cov.FIXTURE_CLASSES):
            if cls in covered:
                continue
            if (surface, cls) in cov.EXCLUSIONS:
                continue
            holes.append(f"{surface} x {cls}")
    assert not holes, (
        "Undeclared surface/class pairs. Either the surface reports this class "
        "(add it to COVERAGE) or it deliberately does not (add it to EXCLUSIONS "
        "with a reason):\n  " + "\n  ".join(holes)
    )


def test_exclusion_reasons_are_real_sentences():
    # A reason of "n/a" is an absence with extra steps.
    weak = [
        f"{s} x {c}: {why!r}"
        for (s, c), why in cov.EXCLUSIONS.items()
        if len(why.strip()) < 25
    ]
    assert not weak, "Exclusion reasons must explain WHY:\n  " + "\n  ".join(weak)


def test_no_exclusion_contradicts_its_coverage_entry():
    both = [
        f"{s} x {c}"
        for (s, c) in cov.EXCLUSIONS
        if c in cov.COVERAGE.get(s, set())
    ]
    assert not both, "Listed as both covered and excluded:\n  " + "\n  ".join(both)


def test_every_surface_has_a_coverage_entry():
    missing = [s for s in cov.SURFACES if s not in cov.COVERAGE]
    assert not missing, f"surfaces with no COVERAGE entry: {missing}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run gui-tests tests/test_golden_coverage.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'tests.golden.coverage'`

- [ ] **Step 3: Write the coverage tables**

Create `pypsa-gui/backend/tests/golden/coverage.py`:

```python
"""
Which economic surface reports which component class.

Doubles as the honest answer to "what does this app actually report?" — a
question that currently cannot be answered without reading nine endpoints.
"""
from __future__ import annotations

SURFACES = (
    "asset_economics",
    "cost_breakdown",
    "economics_by_carrier",
    "statistics",
    "lcoh",
    "asset_costs",
    "asset_results",
    "asset_results_xlsx",
    "compare_economics",
)

FIXTURE_CLASSES = frozenset({"Generator", "Line", "Link", "StorageUnit"})

COVERAGE: dict[str, set[str]] = {
    "asset_economics":      {"Generator", "StorageUnit", "Link"},
    "cost_breakdown":       {"Generator", "StorageUnit", "Link", "Line"},
    "economics_by_carrier": {"Generator", "StorageUnit", "Link", "Line"},
    "statistics":           {"Generator", "StorageUnit", "Link", "Line"},
    "lcoh":                 {"Link"},
    "asset_costs":          {"Generator", "StorageUnit", "Link", "Line"},
    "asset_results":        {"Generator", "StorageUnit", "Link", "Line"},
    "asset_results_xlsx":   {"Generator", "StorageUnit", "Link", "Line"},
    "compare_economics":    {"Generator", "StorageUnit", "Link", "Line"},
}

EXCLUSIONS: dict[tuple[str, str], str] = {
    ("asset_economics", "Line"): (
        "Lines carry no dispatchable energy of their own, so per-asset "
        "revenue and unit cost are undefined for them. Line CAPEX is "
        "reported by cost_breakdown and the Capacity Expansion tab instead."
    ),
    ("lcoh", "Generator"): (
        "LCOH is a per-electrolyser metric: it levelises the cost of hydrogen "
        "OUTPUT. A generator produces no hydrogen, so the ratio has no "
        "denominator."
    ),
    ("lcoh", "Line"): (
        "LCOH levelises hydrogen output. A line transports electricity and "
        "produces no hydrogen, so the metric does not apply."
    ),
    ("lcoh", "StorageUnit"): (
        "LCOH levelises hydrogen output. A battery stores electricity and "
        "produces no hydrogen; hydrogen STORES are a separate case and are "
        "not in the golden fixture."
    ),
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pixi run gui-tests tests/test_golden_coverage.py -q`
Expected: PASS (4 tests)

If a pair fails, do NOT silence it by widening `COVERAGE` — check whether the surface really reports that class. A wrong `COVERAGE` entry makes Task 4 assert against a surface that never had the data.

- [ ] **Step 5: Commit**

```bash
cd "$(git rev-parse --show-toplevel)"
git branch --show-current && git status --porcelain
git add pypsa-gui/backend/tests/golden/coverage.py \
        pypsa-gui/backend/tests/test_golden_coverage.py
git commit -m "test(gui): exhaustive-by-default coverage matrix for economic surfaces"
```

---

## Task 4: Anchors and cross-surface agreement — the discovery gate

This is the task that produces the disagreement list. It is EXPECTED to find failures; Asset Detail's `capex_annual` is already measured at 22–100% low. Known failures are recorded as `pytest.mark.xfail(strict=True)` so the suite stays green while the bug stays visible — and `strict=True` means the marker itself fails once the bug is fixed without removing it.

**Files:**
- Create: `pypsa-gui/backend/tests/test_golden_economics.py`
- Create: `docs/superpowers/findings/2026-08-01-economic-surface-disagreements.md`

**Interfaces:**
- Consumes: `tests.golden.fixture`, `tests.golden.oracle`, `tests.golden.coverage`.
- Produces: `golden` pytest fixture (function-scoped, installs the network).

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/backend/tests/test_golden_economics.py`:

```python
"""
Every economic surface, against an independent oracle AND against every other
surface.

Anchors catch CONSISTENT wrongness — all surfaces agreeing on a wrong annuity.
Agreement catches coverage gaps and drift. Neither alone suffices: on
2026-07-31 the Economics tab and the LCOH panel agreed exactly at EUR 246.02,
and they would have agreed just as neatly had the annuity been wrong.
"""
from __future__ import annotations

import pytest

import routers.results as R
from tests.golden import fixture as gf
from tests.golden import oracle

REL = 1e-9  # values derived from one dispatch; float sums over 48 snapshots


@pytest.fixture()
def golden(reset_backend):
    """
    Install the solved golden network AFTER conftest's autouse reset.

    Depending on `reset_backend` explicitly is load-bearing: it is autouse and
    calls PyPSAService.reset_network() before every test, so without the
    ordering this fixture would install into a context that is about to be
    wiped.
    """
    n = gf.solve_golden_network()
    gf.install_golden(n)
    return n


def _gas_expected_capex(n) -> float:
    """Independent expectation for the `gas` generator's horizon CAPEX."""
    rate = oracle.annualised_capital_cost(
        overnight_cost=float(n.generators.at["gas", "overnight_cost"]),
        rate=gf.GOLDEN_DISCOUNT_RATE,
        lifetime=float(n.generators.at["gas", "lifetime"]),
        snapshots_per_period=gf.SNAPSHOTS_PER_PERIOD,
    )
    return oracle.horizon_capex(
        rate, float(n.generators.at["gas", "p_nom_opt"]), gf.GOLDEN_YEARS
    )


def test_asset_economics_reports_the_link_class_at_all(golden):
    # The regression that started this: /results/asset_economics returned
    # generators, storage_units and stores, with no `links` key at all.
    payload = R.get_asset_economics()

    assert "links" in payload
    assert [r["name"] for r in payload["links"]] == ["electrolyzer"]


def test_asset_economics_gas_capex_matches_the_oracle(golden):
    payload = R.get_asset_economics()
    row = next(r for r in payload["generators"] if r["name"] == "gas")

    assert row["fixed_cost_eur"] == pytest.approx(_gas_expected_capex(golden), rel=REL)


def test_a_genuinely_zero_cost_asset_reports_zero_not_unresolvable(golden):
    payload = R.get_asset_economics()
    row = next(r for r in payload["storage_units"] if r["name"] == "bess")

    assert row["fixed_cost_eur"] == 0.0


def test_cost_breakdown_agrees_with_asset_economics_on_gas(golden):
    ae = R.get_asset_economics()
    gas = next(r for r in ae["generators"] if r["name"] == "gas")
    cb = R.get_cost_breakdown()

    gas_capex = _find_component_capex(cb, "Generator", "gas")
    assert gas_capex == pytest.approx(gas["fixed_cost_eur"], rel=1e-6)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "MEASURED 2026-07-31 on the user's own project: Asset Detail's "
        "capex_annual reads the RAW capital_cost column (compute.py:297) "
        "instead of resolving via periodized_capital_costs, so it reports "
        "22.3% low for Gas_B2, 41.7% low for PV_B3 and EUR 0 for the "
        "electrolyser. Fixed in Task 5; strict=True fails this marker once "
        "the bug is gone so it cannot outlive the defect."
    ),
)
def test_asset_detail_capex_agrees_with_asset_economics(golden):
    import routers.asset_results as AR

    ae = R.get_asset_economics()
    gas = next(r for r in ae["generators"] if r["name"] == "gas")

    detail = AR.get_asset_results(component_class="Generator", name="gas",
                                  category="capacity")
    capex = _find_metric(detail, "capex_annual")

    # Asset Detail reports an ANNUAL rate; asset_economics reports the horizon
    # total. Compare on the same basis.
    annual = gas["fixed_cost_eur"] / sum(gf.GOLDEN_YEARS)
    assert capex == pytest.approx(annual, rel=1e-6)


def _find_component_capex(cost_breakdown: dict, component_class: str, name: str) -> float:
    """
    Pull one component's CAPEX out of /results/cost_breakdown.

    Written as a helper because the payload shape is nested and asserting
    through it inline makes the failure message useless.
    """
    for row in cost_breakdown.get("by_component", []):
        if row.get("component") == component_class and row.get("name") == name:
            return float(row.get("capex", 0.0))
    raise AssertionError(
        f"cost_breakdown has no entry for {component_class} '{name}' — "
        "if that is intended, add it to EXCLUSIONS in tests/golden/coverage.py"
    )


def _find_metric(asset_detail: dict, metric_id: str) -> float:
    for m in asset_detail.get("metrics", []):
        if m.get("id") == metric_id:
            return float(m.get("value"))
    raise AssertionError(f"Asset Detail has no metric {metric_id!r}")
```

- [ ] **Step 1b: Drive agreement from the coverage matrix, not by hand**

The four assertions above are hand-written and cover four surfaces. The spec
requires agreement across all nine, and hand-writing nine sets of assertions
guarantees the ninth is forgotten. Drive them from `COVERAGE` instead.

Each surface needs a small adapter reducing its payload to a common shape, so
one loop can compare them all:

```python
# surface id -> callable(network) -> {(component_class, asset_name): horizon_capex_eur}
Adapter = Callable[[Any], dict[tuple[str, str], float]]
```

One complete worked example — write the remaining eight by READING each
payload, never by guessing its shape:

```python
def _from_asset_economics(_n) -> dict[tuple[str, str], float]:
    payload = R.get_asset_economics()
    out: dict[tuple[str, str], float] = {}
    for key, cls in (("generators", "Generator"),
                     ("storage_units", "StorageUnit"),
                     ("stores", "Store"),
                     ("links", "Link")):
        for row in payload.get(key, []):
            out[(cls, row["name"])] = float(row["fixed_cost_eur"])
    return out


ADAPTERS: dict[str, Adapter] = {
    "asset_economics": _from_asset_economics,
    # ... eight more, one per entry in coverage.SURFACES
}


def test_every_surface_agrees_on_every_covered_asset(golden):
    """
    One loop over the coverage matrix. A surface that reports NOTHING for a
    class it claims to cover fails here — which is the missing-Link bug, caught
    structurally rather than by someone remembering to look.
    """
    from tests.golden import coverage as cov

    reported = {sid: ADAPTERS[sid](golden) for sid in cov.SURFACES if sid in ADAPTERS}

    baseline = reported["asset_economics"]
    problems: list[str] = []

    for sid, values in reported.items():
        if sid == "asset_economics":
            continue
        for cls in sorted(cov.COVERAGE.get(sid, set()) & cov.FIXTURE_CLASSES):
            names = [n for (c, n) in baseline if c == cls]
            for name in names:
                if (cls, name) not in values:
                    problems.append(
                        f"{sid} claims to cover {cls} but reported nothing for {name!r}"
                    )
                    continue
                a, b = baseline[(cls, name)], values[(cls, name)]
                if a != pytest.approx(b, rel=1e-6):
                    problems.append(
                        f"{sid} {cls} {name}: {b:,.2f} vs asset_economics {a:,.2f} "
                        f"({abs(b - a) / a * 100 if a else float('inf'):.1f}% apart)"
                    )

    assert not problems, "Economic surfaces disagree:\n  " + "\n  ".join(problems)
```

Add `from typing import Any, Callable` to the imports.

**When a surface has no per-asset CAPEX at all** (e.g. `economics_by_carrier`
aggregates by carrier, not asset), do NOT force it into this shape. Give it no
adapter, and record in `EXCLUSIONS` that it reports per-carrier rather than
per-asset. Bending a surface into the wrong shape to satisfy a loop is how a
test starts asserting something nobody meant.


- [ ] **Step 2: Run the test and record what it finds**

Run: `pixi run gui-tests tests/test_golden_economics.py -q > /tmp/golden.log 2>&1; echo "EXIT=$?"`

The payload shapes for `cost_breakdown` and Asset Detail are asserted here for the first time. `_find_component_capex` and `_find_metric` are written against the shapes as documented; if they raise `AssertionError` about a missing entry, **read the actual payload before changing the helper** — a shape mismatch is a finding, not a bug in the test.

Adjust the two helpers to the real payload shape, then re-run. Do NOT weaken an assertion to make it pass.

- [ ] **Step 3: Write the disagreement list**

Create `docs/superpowers/findings/2026-08-01-economic-surface-disagreements.md` recording, for every surface pair checked: the asset, the metric, both values, the relative difference, and whether it is a wrong number (fix) or a shape/naming mismatch (defer). Include the surfaces that agreed — a list of only failures cannot be distinguished from a list nobody finished.

- [ ] **Step 4: Verify the suite is green with known failures marked**

Run: `pixi run gui-tests tests/test_golden_economics.py -q > /tmp/golden.log 2>&1; echo "EXIT=$?"`
Expected: `EXIT=0`, with the Asset Detail test reported as `xfail`.

- [ ] **Step 5: Commit**

```bash
cd "$(git rev-parse --show-toplevel)"
git branch --show-current && git status --porcelain
git add pypsa-gui/backend/tests/test_golden_economics.py \
        docs/superpowers/findings/2026-08-01-economic-surface-disagreements.md
git commit -m "test(gui): anchor + cross-surface economic assertions (discovery gate)"
```

---

## Task 5: Fix Asset Detail's CAPEX

In scope regardless of triage: already known, already quantified, already user-visible.

**Files:**
- Modify: `pypsa-gui/backend/services/asset_results/compute.py:295-297`
- Modify: `pypsa-gui/backend/services/asset_results/registry.py:93-96` (formula string)
- Modify: `pypsa-gui/backend/tests/test_golden_economics.py` (remove the xfail)

**Interfaces:**
- Consumes: `periodized_capital_costs(n, cfg)` from `services.solver_service`.
- Produces: no signature change — `gen_capex_annual(ctx)` keeps its shape.

**⚠ `compute.py` is the concurrent session's file** (`e1f8dc47`, 2026-08-01 08:58). Check `git status --porcelain` before starting; if it is dirty, stop and report rather than editing around them.

- [ ] **Step 1: Remove the xfail so the test fails for the right reason**

In `tests/test_golden_economics.py`, delete the entire `@pytest.mark.xfail(...)` decorator above `test_asset_detail_capex_agrees_with_asset_economics`.

- [ ] **Step 2: Run to confirm it now fails**

Run: `pixi run gui-tests tests/test_golden_economics.py::test_asset_detail_capex_agrees_with_asset_economics -q`
Expected: FAIL, with the reported value ~22% below the expected one.

- [ ] **Step 3: Fix the computation**

In `pypsa-gui/backend/services/asset_results/compute.py`, replace `gen_capex_annual`:

```python
def gen_capex_annual(ctx: Ctx):
    """
    Annualised CAPEX, EUR/a.

    Resolves through `periodized_capital_costs` rather than reading the raw
    `capital_cost` column. MEASURED 2026-07-31: the raw column is 0.0 whenever
    the user parameterised the asset via `overnight_cost` — PyPSA derives the
    real figure on the components accessor, not on the DataFrame — so a raw
    read reported 22.3% low for a gas plant, 41.7% low for solar and EUR 0 for
    an electrolyser, against the Economics tab's figure for the same asset.
    """
    from services.solver_service import periodized_capital_costs
    import routers.simulation as sim_router

    opt = gen_p_nom_opt(ctx)
    if opt is None:
        return None

    cfg = sim_router._state.get("solver_config")
    try:
        costs = periodized_capital_costs(ctx.n, cfg)
        cc = float(
            costs.get(attr_for(ctx.component_class), {})
                 .get(ctx.name, {})
                 .get("capital_cost", 0.0)
        )
    except Exception:  # noqa: BLE001 — fall back to the raw column, never crash
        raw = _static(ctx, "capital_cost")
        cc = float(raw) if raw is not None else 0.0

    return cc * opt
```

Exact `Ctx` contract, verified — do not guess these:

```python
@dataclass
class Ctx:
    n: Any               # the pypsa.Network — NOT `network`
    component_class: str # "Generator", "Link", ...
    name: str
    source: str
    sns: Any
    weights: Any
    is_multi: bool
    params: dict         # the asset's static row, NaN-SCRUBBED
```

`attr_for(component_class)` (already imported in `compute.py`) maps
`"Generator"` → `"generators"`, which is the key `periodized_capital_costs`
returns.

- [ ] **Step 4: Correct the metric's formula string**

In `registry.py:95`, the `formula=` text is user-visible and currently lies. Change:

```python
           formula="capital_cost × p_nom_opt", compute=C.gen_capex_annual,
```

to:

```python
           formula="overnight_cost × annuity(discount_rate, lifetime) × p_nom_opt",
           compute=C.gen_capex_annual,
```

- [ ] **Step 5: Run to verify it passes**

Run: `pixi run gui-tests tests/test_golden_economics.py -q > /tmp/g.log 2>&1; echo "EXIT=$?"`
Expected: `EXIT=0`, no xfail remaining.

Then the full suite, to catch any Asset Detail test that asserted the old wrong value:

Run: `pixi run gui-tests -q -p no:warnings > /tmp/full.log 2>&1; echo "EXIT=$?"`
Expected: `EXIT=0`. If an existing test fails because it asserted `capital_cost × p_nom_opt`, that test was encoding the bug — update it and say so in the commit message.

- [ ] **Step 6: Commit**

```bash
cd "$(git rev-parse --show-toplevel)"
git branch --show-current && git status --porcelain
git add pypsa-gui/backend/services/asset_results/compute.py \
        pypsa-gui/backend/services/asset_results/registry.py \
        pypsa-gui/backend/tests/test_golden_economics.py
git commit -m "fix(gui): resolve Asset Detail CAPEX through the periodized helper"
```

---

## Task 6: Emit the frontend payload, with a drift guard

**Files:**
- Modify: `pypsa-gui/backend/tests/test_golden_economics.py`
- Create: `pypsa-gui/frontend/src/pages/results/__fixtures__/asset-economics.golden.json`

**Interfaces:**
- Produces: the JSON file above, regenerated on every backend test run.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_golden_economics.py`:

```python
def test_the_frontend_payload_fixture_is_current(golden):
    """
    The frontend mapping test reads a committed copy of this payload. If the
    copy drifts from what the backend actually returns, that test passes
    against a fiction — so regenerate it here and let CI fail on a dirty tree.
    """
    import json
    import pathlib

    payload = R.get_asset_economics()
    dest = (
        pathlib.Path(__file__).resolve().parents[2]
        / "frontend" / "src" / "pages" / "results" / "__fixtures__"
        / "asset-economics.golden.json"
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")

    assert dest.exists()
```

- [ ] **Step 2: Run it to generate the file**

Run: `pixi run gui-tests tests/test_golden_economics.py -q`
Expected: PASS, and `asset-economics.golden.json` now exists.

- [ ] **Step 3: Confirm the drift guard works**

```bash
cd "$(git rev-parse --show-toplevel)"
git status --porcelain pypsa-gui/frontend/src/pages/results/__fixtures__/
```
Expected: the file shows as untracked/modified. That dirty state IS the guard — CI should fail when it appears after a test run.

- [ ] **Step 4: Commit**

```bash
cd "$(git rev-parse --show-toplevel)"
git branch --show-current && git status --porcelain
git add pypsa-gui/backend/tests/test_golden_economics.py \
        pypsa-gui/frontend/src/pages/results/__fixtures__/asset-economics.golden.json
git commit -m "test(gui): emit the golden asset-economics payload for the frontend"
```

---

## Task 7: Frontend mapping assertions

**Files:**
- Create: `pypsa-gui/frontend/src/pages/results/Economics.mapping.test.ts`

**Interfaces:**
- Consumes: `asset-economics.golden.json`; the mapping functions in `Economics.tsx`.
- Produces: nothing.

The mapping functions are currently module-private. Export them (`export function makeGenRow`, and the same for `makeSURow`, `makeStoreRow`, `makeLinkRow`) — exporting a pure function for test is preferable to reaching into module internals.

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/frontend/src/pages/results/Economics.mapping.test.ts`:

```ts
import { describe, it, expect } from 'vitest'
import golden from './__fixtures__/asset-economics.golden.json'
import { makeGenRow, makeLinkRow, makeSURow } from './Economics'
import type { GeneratorEconomicsRow, LinkEconomicsRow, StorageUnitEconomicsRow } from '../../api/simulation'

// The backend can be perfectly self-consistent while the frontend maps the
// wrong field. A Link deliberately puts GROSS revenue in `revenue_eur` and the
// energy it bought in `charge_cost_eur`, reusing the storage columns — swap
// those two and the tab shows a wildly wrong net profit while all nine backend
// surfaces still agree with each other.

describe('asset economics row mapping', () => {
  it('carries a link’s gross revenue and input cost into the storage columns', () => {
    const link = (golden.links as LinkEconomicsRow[])[0]
    const row = makeLinkRow(link)

    expect(row.group).toBe('Converters')
    expect(row.revenue_eur).toBe(link.gross_revenue_eur)
    expect(row.charge_cost_eur).toBe(link.input_cost_eur)
    // NOT the net figure — that lives in net_profit_eur.
    expect(row.revenue_eur).not.toBe(link.revenue_eur)
  })

  it('carries a link’s OUTPUT as energy, never its input', () => {
    const link = (golden.links as LinkEconomicsRow[])[0]
    const row = makeLinkRow(link)

    expect(row.energy_mwh).toBe(link.energy_mwh)
    expect(row.charge_mwh).toBe(link.input_energy_mwh)
    expect(row.energy_mwh).not.toBe(link.input_energy_mwh)
  })

  it('preserves generator cost fields exactly', () => {
    const gen = (golden.generators as GeneratorEconomicsRow[])[0]
    const row = makeGenRow(gen)

    expect(row.fixed_cost_eur).toBe(gen.fixed_cost_eur)
    expect(row.vom_cost_eur).toBe(gen.vom_cost_eur)
    expect(row.net_profit_eur).toBe(gen.net_profit_eur)
    expect(row.charge_cost_eur).toBe(0)
  })

  it('preserves a zero-cost storage unit as zero', () => {
    const su = (golden.storage_units as StorageUnitEconomicsRow[])[0]
    const row = makeSURow(su)

    expect(row.fixed_cost_eur).toBe(0)
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd pypsa-gui/frontend && PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx vitest run src/pages/results/Economics.mapping.test.ts`
Expected: FAIL — the mapping functions are not exported.

- [ ] **Step 3: Export the mapping functions**

In `Economics.tsx`, add `export` to each of `makeGenRow`, `makeSURow`, `makeStoreRow`, `makeLinkRow`. Change nothing else.

- [ ] **Step 4: Run to verify it passes**

Run: `cd pypsa-gui/frontend && PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx vitest run src/pages/results/Economics.mapping.test.ts`
Expected: PASS (4 tests)

Then typecheck: `npx tsc --noEmit -p tsconfig.json > /tmp/tsc.log 2>&1; echo "EXIT=$?"` → `EXIT=0`.

- [ ] **Step 5: Commit**

```bash
cd "$(git rev-parse --show-toplevel)"
git branch --show-current && git status --porcelain
git add pypsa-gui/frontend/src/pages/results/Economics.mapping.test.ts \
        pypsa-gui/frontend/src/pages/results/Economics.tsx
git commit -m "test(gui): assert economics row mapping against the golden payload"
```

---

## Task 8: Say why a cost could not be resolved

Scoped to the NaN-defaulted fields only. `capital_cost`, `marginal_cost` and `fom_cost` all default to `0.0` and PyPSA materialises that on load, so "never set" is unrecoverable for them — measured: `links_marginal_cost` was absent from the user's netCDF entirely and still read `0.0`.

**Files:**
- Modify: `pypsa-gui/backend/services/asset_results/compute.py`
- Modify: `pypsa-gui/backend/tests/test_golden_economics.py`

**Interfaces:**
- Produces: `gen_capex_unresolved_reason(ctx) -> str | None`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_golden_economics.py`:

```python
def test_an_unresolvable_capex_says_why_instead_of_reporting_zero(golden):
    """
    discount_rate = NaN is what turned every annuity into NaN and made
    n.statistics() report EUR 0 CAPEX for assets the LP had costed correctly.
    A zero here is indistinguishable from a free asset; a reason is not.
    """
    import routers.simulation as sim_router
    from services.asset_results import compute as C
    from services.solver_service import SolverConfig

    # Strip the discount rate: `gas` is priced via overnight_cost, so its
    # annuity can no longer be resolved.
    sim_router._state["solver_config"] = SolverConfig(discount_rate=float("nan"))
    golden.generators.loc["gas", "discount_rate"] = float("nan")

    ctx = C.build_ctx(golden, "Generator", "gas", source="lopf", sns=golden.snapshots)
    reason = C.gen_capex_unresolved_reason(ctx)

    assert reason is not None
    assert "discount_rate" in reason


def test_a_resolvable_capex_has_no_reason(golden):
    from services.asset_results import compute as C

    ctx = C.build_ctx(golden, "Generator", "gas", source="lopf", sns=golden.snapshots)
    assert C.gen_capex_unresolved_reason(ctx) is None


def test_a_genuine_zero_is_not_reported_as_unresolvable(golden):
    # `bess` has capital_cost = 0.0 deliberately. Zero is an answer.
    from services.asset_results import compute as C

    ctx = C.build_ctx(golden, "StorageUnit", "bess", source="lopf", sns=golden.snapshots)
    assert C.gen_capex_unresolved_reason(ctx) is None
```

Note the constructor, verified: `build_ctx(n, component_class, name, *, source, sns)` — `source` and `sns` are keyword-only. Do **not** call `Ctx(...)` directly; it is an 8-field dataclass carrying pre-computed `weights` and `params`.

`ctx.params` is **NaN-scrubbed**, so `_static` returns `None` for an unset `overnight_cost` rather than `NaN`. The implementation below checks for both — keep both branches.

- [ ] **Step 2: Run to verify it fails**

Run: `pixi run gui-tests tests/test_golden_economics.py -k unresolv -q`
Expected: FAIL — `AttributeError: module 'compute' has no attribute 'gen_capex_unresolved_reason'`

- [ ] **Step 3: Implement**

Add to `pypsa-gui/backend/services/asset_results/compute.py`:

```python
def gen_capex_unresolved_reason(ctx: Ctx) -> str | None:
    """
    Why this asset's CAPEX could not be annualised, or None if it could.

    Only the NaN-defaulted inputs are detectable. `capital_cost`,
    `marginal_cost` and `fom_cost` default to 0.0 and PyPSA materialises that
    on load — MEASURED: `links_marginal_cost` was absent from a real netCDF
    entirely and still read 0.0 in memory — so for those fields "unset" and
    "deliberately zero" are indistinguishable and this function stays silent.
    """
    import math

    import routers.simulation as sim_router

    overnight = _static(ctx, "overnight_cost")
    if overnight is None or (isinstance(overnight, float) and math.isnan(overnight)):
        # Priced directly via capital_cost (or genuinely free). Nothing to annualise.
        return None

    rate = _static(ctx, "discount_rate")
    if rate is None or (isinstance(rate, float) and math.isnan(rate)):
        cfg = sim_router._state.get("solver_config")
        cfg_rate = getattr(cfg, "discount_rate", None)
        if cfg_rate is None or (isinstance(cfg_rate, float) and math.isnan(cfg_rate)):
            return (
                "CAPEX cannot be annualised: this asset is priced via "
                "overnight_cost, but discount_rate is unset on the asset and "
                "no solver-config default applies."
            )

    lifetime = _static(ctx, "lifetime")
    if lifetime is None or (isinstance(lifetime, float) and not math.isfinite(lifetime)):
        return (
            "CAPEX cannot be annualised: this asset is priced via "
            "overnight_cost, but lifetime is unset (infinite), so there is no "
            "period to spread the investment over."
        )

    return None
```

- [ ] **Step 4: Run to verify it passes**

Run: `pixi run gui-tests tests/test_golden_economics.py -q > /tmp/g.log 2>&1; echo "EXIT=$?"`
Expected: `EXIT=0`

- [ ] **Step 5: Commit**

```bash
cd "$(git rev-parse --show-toplevel)"
git branch --show-current && git status --porcelain
git add pypsa-gui/backend/services/asset_results/compute.py \
        pypsa-gui/backend/tests/test_golden_economics.py
git commit -m "feat(gui): explain an unresolvable CAPEX instead of reporting zero"
```

---

## Task 9: Triage the disagreement list

**Files:**
- Modify: `docs/superpowers/findings/2026-08-01-economic-surface-disagreements.md`
- Create (only if phase 2 is non-trivial): `docs/superpowers/plans/2026-08-XX-economic-surface-remediation.md`

- [ ] **Step 1: Run the full suite and capture the state**

Run: `pixi run gui-tests -q -p no:warnings > /tmp/full.log 2>&1; echo "EXIT=$?"`

- [ ] **Step 2: Apply the triage rule to every remaining disagreement**

- **A user-visible number is wrong → in scope.** Add a task to a follow-up plan.
- **Shape, naming or rounding mismatch → deferred.** Record it with the reason.

- [ ] **Step 3: Update the findings document**

State plainly: which surfaces were compared, which agreed, which did not, what was fixed here, what is deferred and why. Include the surfaces that agreed — a list of only failures is indistinguishable from a list nobody finished.

- [ ] **Step 4: Report to the user**

Give the disagreement count, the fixed/deferred split, and a size estimate for phase 2. This is the point at which the user makes the scope call the spec deliberately left open.

- [ ] **Step 5: Commit**

```bash
cd "$(git rev-parse --show-toplevel)"
git branch --show-current && git status --porcelain
git add docs/superpowers/findings/2026-08-01-economic-surface-disagreements.md
git commit -m "docs: triage the economic-surface disagreement list"
```

---

## Verification checklist

- [ ] `pixi run gui-tests -q -p no:warnings > /tmp/f.log 2>&1; echo $?` → `0`
- [ ] `cd pypsa-gui/frontend && npx vitest run > /tmp/v.log 2>&1; echo $?` → `0`
- [ ] `npx tsc --noEmit -p tsconfig.json > /tmp/t.log 2>&1; echo $?` → `0`
- [ ] No `@pytest.mark.xfail` remains in `test_golden_economics.py` without a finding entry explaining why it is still there
- [ ] `tests/golden/oracle.py` still imports nothing from `services/` or `routers/`
- [ ] `git status --porcelain` clean, including the emitted frontend fixture
- [ ] Rebuild the DMG (`bash pypsa-gui/build-macos.sh`) — CLAUDE.md requires it for any change that reaches the app, and Tasks 5 and 8 change backend behaviour
