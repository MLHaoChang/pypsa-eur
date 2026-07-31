# Asset Detail — Phase 1 (Generator end-to-end) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship an eleventh Results tab that evaluates ONE asset in detail — searchable picker, eight result categories with three-state applicability, selectable scalar and time-series metrics, table + charts in three view modes, XLSX/CSV/PNG/SVG export, and three chatbot tools — complete for `Generator`, with `summary` working for all eight component classes.

**Architecture:** A backend metric registry (`services/asset_results/registry.py`) declares every metric once: category, applicable classes, unit, origin, preconditions, compute function. An applicability resolver turns `(asset, network state)` into `ok | blocked | na` per metric and per category. Two thin endpoints serve the UI and the chat tools from that one source. The frontend holds no metric knowledge — it renders labels, units and statuses straight from the response.

**Tech Stack:** FastAPI · PyPSA 1.x · pandas · openpyxl · React 19 · TanStack Query v5 · TanStack Virtual · Recharts 2 · Tailwind 4 · Zustand · pytest · vitest

**Spec:** [`docs/superpowers/specs/2026-07-31-asset-detail-results-design.md`](../specs/2026-07-31-asset-detail-results-design.md)

## Global Constraints

- **Branch check before every commit.** This worktree is shared with other agent sessions. Run `git branch --show-current` immediately before each commit — do not trust an earlier answer. Use path-limited `git commit <paths> -m …`, never `git add -A`.
- **Reads never take the PyPSA lock.** Every endpoint in this plan is read-only. Never wrap a read in `PyPSAService.get_lock()`.
- **Never `df.to_json()`.** Use `df_to_json` / `safe_values` / `ts_payload` from `services/serialization.py`. Non-finite floats must serialise to `null` or `JSONResponse.render` returns a 21-byte plain-text 500.
- **Local imports of pandas/numpy/math** inside function bodies, matching the existing style in `routers/results.py`.
- **Cross-platform.** Windows and macOS arm64 both. No hardcoded interpreter paths; use `pixi run …`.
- **Category ids** are exactly: `summary`, `capacity`, `dispatch`, `storage`, `loadflow`, `prices`, `economics`, `emissions`.
- **Status values** are exactly: `ok`, `blocked`, `na`. Remedy actions are exactly: `run_simulation`, `run_ac_pf`, `open_properties`.
- **Backend tests:** `pixi run gui-tests tests/<file> -v` (runs in the `test` environment with `cwd=pypsa-gui/backend`).
- **Frontend tests:** `cd pypsa-gui/frontend && npx vitest run src/<path>`.
- **Typecheck:** `cd pypsa-gui/frontend && PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx tsc --noEmit -p tsconfig.json`.
- **Phase 1 scope:** `Generator` gets all eight categories. All other classes get `summary` only; their other categories resolve `na` with reason `"not yet available — arrives in phase 2"` via `NOT_YET_IMPLEMENTED`. Phases 2–3 replace those with real metrics.

## File Structure

**Create — backend**

| File | Responsibility |
|---|---|
| `pypsa-gui/backend/services/asset_results/__init__.py` | Public surface: `resolve_asset`, `compute_asset`, `build_workbook` |
| `pypsa-gui/backend/services/asset_results/registry.py` | The `Metric` table — every metric declared exactly once |
| `pypsa-gui/backend/services/asset_results/applicability.py` | `(class, category, preconditions) → Status` |
| `pypsa-gui/backend/services/asset_results/compute.py` | One function per metric; no HTTP, no serialisation |
| `pypsa-gui/backend/services/asset_results/export.py` | pandas/openpyxl workbook builder |
| `pypsa-gui/backend/routers/asset_results.py` | Two endpoints, thin |

**Create — frontend** (all under `pypsa-gui/frontend/src/pages/results/asset/`)

| File | Responsibility |
|---|---|
| `types.ts` | Response types mirroring the endpoint contract |
| `api.ts` | Query functions + export URL builders |
| `AssetPicker.tsx` | Virtualised, class-grouped, searchable asset list |
| `MetricChecklist.tsx` | Two zones, three states, remedy buttons |
| `AssetTable.tsx` | Virtualised, view-mode-aware data table |
| `AssetCharts.tsx` | Unit-grouped chart stack with a shared X axis |
| `exportPng.ts` | SVG → canvas → PNG rasteriser |
| `selectionMemory.ts` | Per-`(class, category)` localStorage tick-set |
| `AssetDetail.tsx` | Shell wiring all of the above |

**Modify**

| File | Change |
|---|---|
| `pypsa-gui/backend/main.py` | One `include_router` line |
| `pypsa-gui/backend/services/chat_tools.py` | Three dispatcher functions + registry entries |
| `pypsa-gui/backend/services/chat_tools_schema.py` | Three `_t(...)` schemas; `RESULTS_TAB_ENUM` gains `"asset"` |
| `pypsa-gui/frontend/src/pages/Results.tsx` | Eleventh tab, scrollable strip, compare-tab mapping |
| `pypsa-gui/frontend/src/layout/PropertiesPanel.tsx` | "View results" action |
| `pypsa-gui/frontend/src/layout/BottomPanel.tsx` | Row action |
| `pypsa-gui/frontend/src/components/ChatPanel.tsx` | Handle `kind="open_asset_detail"` ui_event |
| `pypsa-gui/frontend/src/store/uiStore.ts` | `assetDetailRequest` slot + setter/clearer |

---

## Task 1: Metric registry + applicability resolver

Pure Python. No PyPSA, no FastAPI — so it is fast to test and the shape is validated before anything depends on it.

**Files:**
- Create: `pypsa-gui/backend/services/asset_results/__init__.py`
- Create: `pypsa-gui/backend/services/asset_results/registry.py`
- Create: `pypsa-gui/backend/services/asset_results/applicability.py`
- Test: `pypsa-gui/backend/tests/test_asset_results_registry.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `registry.Metric` frozen dataclass with fields `id, label, unit, kind, category, classes, origin, compute, formula, requires, source_override`
  - `registry.CATEGORIES: tuple[tuple[str, str], ...]`, `registry.CATEGORY_IDS: tuple[str, ...]`, `registry.CATEGORY_LABELS: dict[str, str]`
  - `registry.ALL_CLASSES: tuple[str, ...]`
  - `registry.METRICS: tuple[Metric, ...]`
  - `registry.metrics_for(component_class: str, category: str) -> tuple[Metric, ...]`
  - `registry.metric_by_id(metric_id: str) -> Metric | None`
  - `registry.REQ_DISPATCH / REQ_AC_PF / REQ_DUALS / REQ_COMMITTABLE / REQ_CO2 / REQ_NOT_YET: str`
  - `applicability.Remedy(action: str, label: str)` frozen dataclass
  - `applicability.Status(status: str, reason: str = "", remedy: Remedy | None = None)` frozen dataclass, plus `applicability.OK: Status`
  - `applicability.category_na_reason(category: str, component_class: str) -> str`
  - `applicability.resolve_metric(metric, component_class, precond: dict[str, Status]) -> Status`
  - `applicability.resolve_category(category, component_class, precond) -> Status`

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/backend/tests/test_asset_results_registry.py`:

```python
"""Registry + applicability invariants. Pure-Python: no network, no client."""
import pytest

from services.asset_results import applicability as ap
from services.asset_results import registry as reg


def test_every_metric_id_is_unique():
    ids = [m.id for m in reg.METRICS]
    assert len(ids) == len(set(ids)), "duplicate metric ids"


def test_every_metric_lands_in_a_known_category_and_classes():
    for m in reg.METRICS:
        assert m.category in reg.CATEGORY_IDS, f"{m.id}: bad category {m.category}"
        assert m.classes, f"{m.id}: declares no classes"
        for c in m.classes:
            assert c in reg.ALL_CLASSES, f"{m.id}: unknown class {c}"


def test_every_metric_is_computable_and_well_formed():
    for m in reg.METRICS:
        assert callable(m.compute), f"{m.id}: compute is not callable"
        assert m.kind in ("series", "scalar"), f"{m.id}: bad kind {m.kind}"
        assert m.origin in ("output", "input", "derived"), f"{m.id}: bad origin"
        if m.kind == "series":
            assert m.unit != "" or m.id == "status", f"{m.id}: series needs a unit"
        if m.origin == "derived":
            assert m.formula, f"{m.id}: derived metrics must carry a formula"


def test_summary_covers_every_component_class():
    for c in reg.ALL_CLASSES:
        assert reg.metrics_for(c, "summary"), f"{c} has no summary metrics"


def test_generator_has_all_eight_categories_in_phase_1():
    for cat in reg.CATEGORY_IDS:
        assert reg.metrics_for("Generator", cat), f"Generator lacks {cat}"


def test_metric_by_id_round_trips_and_misses_cleanly():
    assert reg.metric_by_id("p").id == "p"
    assert reg.metric_by_id("no_such_metric") is None


def test_resolve_metric_returns_na_for_a_class_the_metric_excludes():
    curtailment = reg.metric_by_id("curtailment")
    st = ap.resolve_metric(curtailment, "Line", {})
    assert st.status == "na"
    assert st.remedy is None, "na must never carry a remedy"
    assert "Line" in st.reason


def test_resolve_metric_surfaces_the_first_unmet_precondition():
    status_metric = reg.metric_by_id("status")
    blocked = ap.Status(
        "blocked", "unit commitment is not enabled on Gas 1",
        ap.Remedy("open_properties", "Enable committable"),
    )
    st = ap.resolve_metric(status_metric, "Generator", {reg.REQ_COMMITTABLE: blocked})
    assert st.status == "blocked"
    assert st.remedy.action == "open_properties"


def test_resolve_metric_is_ok_when_every_precondition_is_ok():
    p = reg.metric_by_id("p")
    precond = {r: ap.OK for r in (reg.REQ_DISPATCH, reg.REQ_AC_PF, reg.REQ_DUALS)}
    assert ap.resolve_metric(p, "Generator", precond).status == "ok"


def test_resolve_category_is_na_when_the_class_has_no_metrics_there():
    # Storage, not loadflow: a Generator genuinely has NO storage metric.
    # Generator/loadflow is the spec's ○ (partial) — see the next test.
    st = ap.resolve_category("storage", "Generator", {})
    assert st.status == "na"
    assert "store energy" in st.reason


def test_generator_loadflow_is_blocked_not_na_because_reactive_power_applies():
    """`q` exists on a Generator but only in the AC PF snapshot, so the
    category is blocked until that stage runs — never n/a."""
    blocked = ap.Status("blocked", "AC power flow has not been run",
                        ap.Remedy("run_ac_pf", "Run AC power flow"))
    st = ap.resolve_category("loadflow", "Generator", {reg.REQ_AC_PF: blocked})
    assert st.status == "blocked"
    assert st.remedy.action == "run_ac_pf"


def test_a_reason_every_member_shares_beats_the_generic_one():
    """A phase-2 placeholder must say "not yet available", not "Dispatch does
    not apply to Load" — Loads DO dispatch, it is simply not wired up yet."""
    st = ap.resolve_category("dispatch", "Load", {
        reg.REQ_NOT_YET: ap.Status(
            "na", "not yet available — arrives in a later phase of this feature"),
    })
    assert st.status == "na"
    assert "not yet available" in st.reason


def test_resolve_category_is_ok_when_any_member_metric_is_ok():
    precond = {reg.REQ_DISPATCH: ap.OK}
    assert ap.resolve_category("dispatch", "Generator", precond).status == "ok"


def test_resolve_category_is_blocked_when_no_member_is_ok():
    blocked = ap.Status("blocked", "network has not been solved",
                        ap.Remedy("run_simulation", "Run simulation"))
    st = ap.resolve_category("dispatch", "Generator", {reg.REQ_DISPATCH: blocked})
    assert st.status == "blocked"
    assert st.remedy.action == "run_simulation"


@pytest.mark.parametrize("cat,cls,needle", [
    ("loadflow", "Generator", "branch"),
    ("storage", "Generator", "store energy"),
    ("dispatch", "Bus", "dispatch"),
    ("capacity", "Bus", "capacity"),
    ("emissions", "Load", "CO"),
])
def test_category_na_reasons_are_specific(cat, cls, needle):
    assert needle in ap.category_na_reason(cat, cls)
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd "$(git rev-parse --show-toplevel)" && pixi run gui-tests tests/test_asset_results_registry.py -v
```

Expected: collection error — `ModuleNotFoundError: No module named 'services.asset_results'`.

- [ ] **Step 3: Write `registry.py`**

```python
"""
The metric registry — the single source of truth for per-asset results.

Every metric is declared exactly once here: which category it belongs to,
which component classes it applies to, its unit, whether it came from the
solver or from user input, what preconditions it needs, and how to compute
it. The endpoint reflects these fields straight into its response, so the
frontend holds NO metric knowledge of its own — adding a metric is one edit
in this file plus its compute function, and nothing in TypeScript changes.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from . import compute as C

Kind = Literal["series", "scalar"]
Origin = Literal["output", "input", "derived"]

CATEGORIES: tuple[tuple[str, str], ...] = (
    ("summary", "Summary"),
    ("capacity", "Capacity"),
    ("dispatch", "Dispatch"),
    ("storage", "Storage"),
    ("loadflow", "Load flow"),
    ("prices", "Prices & duals"),
    ("economics", "Economics"),
    ("emissions", "Emissions"),
)
CATEGORY_IDS: tuple[str, ...] = tuple(c for c, _ in CATEGORIES)
CATEGORY_LABELS: dict[str, str] = dict(CATEGORIES)

ALL_CLASSES: tuple[str, ...] = (
    "Bus", "Generator", "Load", "Line", "Transformer",
    "Link", "StorageUnit", "Store",
)

# ── Preconditions ───────────────────────────────────────────────────────────
# A metric lists the ids it needs; the resolver looks each up in the
# precondition map computed per request and returns the first that is not ok.
REQ_DISPATCH = "dispatch"          # a FRESH solve (dispatch_status == "fresh")
REQ_AC_PF = "ac_pf"                # AC PF stage results present in _state
REQ_DUALS = "duals"                # LP duals captured on this component class
REQ_COMMITTABLE = "committable"    # this asset has committable=True
REQ_CO2 = "co2"                    # this asset's carrier declares co2_emissions
REQ_NOT_YET = "not_yet"            # phase 2/3 placeholder — always `na`


@dataclass(frozen=True)
class Metric:
    id: str
    label: str
    unit: str
    kind: Kind
    category: str
    classes: tuple[str, ...]
    origin: Origin
    compute: Callable[..., Any]
    formula: str = ""
    requires: tuple[str, ...] = field(default_factory=tuple)
    # Metrics that only exist in the AC PF snapshot pin their own source, so
    # they read the right frame whatever the panel's lopf/ac_pf toggle says.
    source_override: str | None = None


# Two metrics every class shares. Declared ONCE with classes=ALL_CLASSES —
# not per-class — so their ids stay stable, human-readable strings that mean
# the same thing in the API, the checklist and the exported workbook.
_SUMMARY_METRICS: tuple[Metric, ...] = (
    Metric(id="identity", label="Identity", unit="", kind="scalar",
           category="summary", classes=ALL_CLASSES, origin="input",
           compute=C.summary_identity),
    Metric(id="params", label="Parameters", unit="", kind="scalar",
           category="summary", classes=ALL_CLASSES, origin="input",
           compute=C.summary_params),
)


_GENERATOR_METRICS: tuple[Metric, ...] = (
    # ── capacity ─────────────────────────────────────────────────────────
    Metric(id="p_nom", label="Installed capacity", unit="MW", kind="scalar",
           category="capacity", classes=("Generator",), origin="input",
           compute=C.gen_p_nom),
    Metric(id="p_nom_opt", label="Optimised capacity", unit="MW", kind="scalar",
           category="capacity", classes=("Generator",), origin="output",
           compute=C.gen_p_nom_opt, requires=(REQ_DISPATCH,)),
    Metric(id="p_nom_delta", label="Capacity expansion", unit="MW", kind="scalar",
           category="capacity", classes=("Generator",), origin="derived",
           formula="p_nom_opt − p_nom", compute=C.gen_p_nom_delta,
           requires=(REQ_DISPATCH,)),
    Metric(id="capex_annual", label="Annualised CAPEX", unit="EUR/a", kind="scalar",
           category="capacity", classes=("Generator",), origin="derived",
           formula="capital_cost × p_nom_opt", compute=C.gen_capex_annual,
           requires=(REQ_DISPATCH,)),
    Metric(id="p_nom_opt_by_vintage", label="Capacity by vintage", unit="MW",
           kind="scalar", category="capacity", classes=("Generator",),
           origin="derived", formula="Σ p_nom_opt over `<name>@<year>` rows",
           compute=C.gen_p_nom_by_vintage, requires=(REQ_DISPATCH,)),

    # ── dispatch ─────────────────────────────────────────────────────────
    Metric(id="p", label="Active power", unit="MW", kind="series",
           category="dispatch", classes=("Generator",), origin="output",
           compute=C.gen_p, requires=(REQ_DISPATCH,)),
    Metric(id="p_max_pu", label="Availability", unit="pu", kind="series",
           category="dispatch", classes=("Generator",), origin="input",
           compute=C.gen_p_max_pu, requires=(REQ_DISPATCH,)),
    Metric(id="available", label="Available power", unit="MW", kind="series",
           category="dispatch", classes=("Generator",), origin="derived",
           formula="p_nom_opt × p_max_pu", compute=C.gen_available,
           requires=(REQ_DISPATCH,)),
    Metric(id="curtailment", label="Curtailment", unit="MW", kind="series",
           category="dispatch", classes=("Generator",), origin="derived",
           formula="p_nom_opt × p_max_pu − p", compute=C.gen_curtailment,
           requires=(REQ_DISPATCH,)),
    Metric(id="capacity_factor", label="Capacity factor", unit="pu", kind="series",
           category="dispatch", classes=("Generator",), origin="derived",
           formula="p ÷ p_nom_opt", compute=C.gen_capacity_factor,
           requires=(REQ_DISPATCH,)),
    Metric(id="status", label="Committed", unit="", kind="series",
           category="dispatch", classes=("Generator",), origin="output",
           compute=C.gen_status, requires=(REQ_DISPATCH, REQ_COMMITTABLE)),
    Metric(id="start_up", label="Start-up", unit="", kind="series",
           category="dispatch", classes=("Generator",), origin="output",
           compute=C.gen_start_up, requires=(REQ_DISPATCH, REQ_COMMITTABLE)),
    Metric(id="shut_down", label="Shut-down", unit="", kind="series",
           category="dispatch", classes=("Generator",), origin="output",
           compute=C.gen_shut_down, requires=(REQ_DISPATCH, REQ_COMMITTABLE)),
    Metric(id="energy_mwh", label="Energy", unit="MWh", kind="scalar",
           category="dispatch", classes=("Generator",), origin="derived",
           formula="Σ p × snapshot_weighting(generators) × period years",
           compute=C.gen_energy, requires=(REQ_DISPATCH,)),
    Metric(id="full_load_hours", label="Full-load hours", unit="h", kind="scalar",
           category="dispatch", classes=("Generator",), origin="derived",
           formula="energy ÷ p_nom_opt", compute=C.gen_full_load_hours,
           requires=(REQ_DISPATCH,)),
    Metric(id="mean_capacity_factor", label="Mean capacity factor", unit="pu",
           kind="scalar", category="dispatch", classes=("Generator",),
           origin="derived", formula="energy ÷ (p_nom_opt × weighted hours)",
           compute=C.gen_mean_cf, requires=(REQ_DISPATCH,)),
    Metric(id="curtailed_mwh", label="Curtailed energy", unit="MWh", kind="scalar",
           category="dispatch", classes=("Generator",), origin="derived",
           formula="Σ curtailment × weighting", compute=C.gen_curtailed_energy,
           requires=(REQ_DISPATCH,)),
    Metric(id="peak_mw", label="Peak output", unit="MW", kind="scalar",
           category="dispatch", classes=("Generator",), origin="derived",
           formula="max p", compute=C.gen_peak, requires=(REQ_DISPATCH,)),
    Metric(id="zero_hours", label="Zero-output hours", unit="h", kind="scalar",
           category="dispatch", classes=("Generator",), origin="derived",
           formula="count of snapshots where p ≈ 0, weighted",
           compute=C.gen_zero_hours, requires=(REQ_DISPATCH,)),
    Metric(id="max_ramp_up", label="Max ramp up", unit="MW/h", kind="scalar",
           category="dispatch", classes=("Generator",), origin="derived",
           formula="max Δp between consecutive snapshots",
           compute=C.gen_max_ramp_up, requires=(REQ_DISPATCH,)),
    Metric(id="max_ramp_down", label="Max ramp down", unit="MW/h", kind="scalar",
           category="dispatch", classes=("Generator",), origin="derived",
           formula="min Δp between consecutive snapshots",
           compute=C.gen_max_ramp_down, requires=(REQ_DISPATCH,)),
    Metric(id="n_starts", label="Start-up count", unit="", kind="scalar",
           category="dispatch", classes=("Generator",), origin="derived",
           formula="Σ start_up", compute=C.gen_n_starts,
           requires=(REQ_DISPATCH, REQ_COMMITTABLE)),

    # ── loadflow (reactive only — hence the ○ in the spec matrix) ─────────
    Metric(id="q", label="Reactive power", unit="MVAr", kind="series",
           category="loadflow", classes=("Generator",), origin="output",
           compute=C.gen_q, requires=(REQ_DISPATCH, REQ_AC_PF),
           source_override="ac_pf"),

    # ── prices ───────────────────────────────────────────────────────────
    Metric(id="bus_marginal_price", label="Bus marginal price", unit="EUR/MWh",
           kind="series", category="prices", classes=("Generator",),
           origin="output", compute=C.gen_bus_price,
           requires=(REQ_DISPATCH, REQ_DUALS)),
    Metric(id="mu_upper", label="μ upper", unit="EUR/MWh", kind="series",
           category="prices", classes=("Generator",), origin="output",
           compute=C.gen_mu_upper, requires=(REQ_DISPATCH, REQ_DUALS)),
    Metric(id="mu_lower", label="μ lower", unit="EUR/MWh", kind="series",
           category="prices", classes=("Generator",), origin="output",
           compute=C.gen_mu_lower, requires=(REQ_DISPATCH, REQ_DUALS)),
    Metric(id="capture_price", label="Capture price", unit="EUR/MWh",
           kind="scalar", category="prices", classes=("Generator",),
           origin="derived", formula="Σ p·λ·w ÷ Σ p·w",
           compute=C.gen_capture_price, requires=(REQ_DISPATCH, REQ_DUALS)),
    Metric(id="capture_rate", label="Capture rate", unit="pu", kind="scalar",
           category="prices", classes=("Generator",), origin="derived",
           formula="capture price ÷ time-weighted mean bus price",
           compute=C.gen_capture_rate, requires=(REQ_DISPATCH, REQ_DUALS)),
    Metric(id="binding_hours", label="Binding hours", unit="h", kind="scalar",
           category="prices", classes=("Generator",), origin="derived",
           formula="count of snapshots where μ_upper or μ_lower ≠ 0",
           compute=C.gen_binding_hours, requires=(REQ_DISPATCH, REQ_DUALS)),

    # ── economics ────────────────────────────────────────────────────────
    Metric(id="revenue_eur", label="Revenue", unit="EUR", kind="scalar",
           category="economics", classes=("Generator",), origin="derived",
           formula="Σ p × bus price × weighting", compute=C.gen_revenue,
           requires=(REQ_DISPATCH, REQ_DUALS)),
    Metric(id="vom_cost_eur", label="Variable O&M", unit="EUR", kind="scalar",
           category="economics", classes=("Generator",), origin="derived",
           formula="Σ |p| × marginal_cost × weighting", compute=C.gen_vom,
           requires=(REQ_DISPATCH,)),
    Metric(id="fixed_cost_eur", label="Fixed cost", unit="EUR/a", kind="scalar",
           category="economics", classes=("Generator",), origin="derived",
           formula="capital_cost × p_nom_opt", compute=C.gen_fixed_cost,
           requires=(REQ_DISPATCH,)),
    Metric(id="net_profit_eur", label="Net profit", unit="EUR", kind="scalar",
           category="economics", classes=("Generator",), origin="derived",
           formula="revenue − (fixed cost + VOM)", compute=C.gen_net_profit,
           requires=(REQ_DISPATCH, REQ_DUALS)),
    Metric(id="lcoe_eur_per_mwh", label="LCOE", unit="EUR/MWh", kind="scalar",
           category="economics", classes=("Generator",), origin="derived",
           formula="(fixed cost + VOM) ÷ energy", compute=C.gen_lcoe,
           requires=(REQ_DISPATCH,)),

    # ── emissions ────────────────────────────────────────────────────────
    Metric(id="co2_rate", label="CO₂ rate", unit="t/h", kind="series",
           category="emissions", classes=("Generator",), origin="derived",
           formula="p ÷ efficiency × carrier.co2_emissions",
           compute=C.gen_co2_rate, requires=(REQ_DISPATCH, REQ_CO2)),
    Metric(id="co2_total_t", label="CO₂ emitted", unit="t", kind="scalar",
           category="emissions", classes=("Generator",), origin="derived",
           formula="Σ CO₂ rate × weighting", compute=C.gen_co2_total,
           requires=(REQ_DISPATCH, REQ_CO2)),
    Metric(id="co2_intensity", label="CO₂ intensity", unit="t/MWh", kind="scalar",
           category="emissions", classes=("Generator",), origin="derived",
           formula="CO₂ emitted ÷ energy", compute=C.gen_co2_intensity,
           requires=(REQ_DISPATCH, REQ_CO2)),

    # ── storage — structurally n/a for Generator, so nothing is declared ──
)

# Phase 2/3 placeholders: every non-Generator class gets one always-`na`
# metric per non-summary category, so the tab strip renders the full eight
# and says WHY rather than silently showing seven.
_PLACEHOLDERS: tuple[Metric, ...] = tuple(
    Metric(id=f"__pending__{cls}__{cat}", label=CATEGORY_LABELS[cat], unit="",
           kind="scalar", category=cat, classes=(cls,), origin="derived",
           formula="—", compute=C.not_yet, requires=(REQ_NOT_YET,))
    for cls in ALL_CLASSES if cls != "Generator"
    for cat in CATEGORY_IDS if cat != "summary"
)

METRICS: tuple[Metric, ...] = (
    _SUMMARY_METRICS + _GENERATOR_METRICS + _PLACEHOLDERS
)

_BY_ID: dict[str, Metric] = {m.id: m for m in METRICS}


def metric_by_id(metric_id: str) -> Metric | None:
    return _BY_ID.get(metric_id)


def metrics_for(component_class: str, category: str) -> tuple[Metric, ...]:
    return tuple(
        m for m in METRICS
        if m.category == category and component_class in m.classes
    )
```

- [ ] **Step 4: Write `applicability.py`**

```python
"""
Resolve a metric or a category to one of three states for a given asset.

  ok      — computable right now
  blocked — applies to this class, but a precondition is unmet; carries a
            reason and an actionable remedy
  na      — cannot ever apply to this class; carries a reason, never a remedy

The distinction is the point. "Gas 1 has no load flow" and "you have not run
AC power flow yet" are both greyed-out, but only one of them is worth acting
on, and conflating them makes the tab useless for diagnosing a model.
"""
from __future__ import annotations

from dataclasses import dataclass

from .registry import CATEGORY_LABELS, Metric, metrics_for

VALID_ACTIONS = frozenset({"run_simulation", "run_ac_pf", "open_properties"})


@dataclass(frozen=True)
class Remedy:
    action: str
    label: str

    def __post_init__(self) -> None:
        if self.action not in VALID_ACTIONS:
            raise ValueError(f"unknown remedy action: {self.action}")


@dataclass(frozen=True)
class Status:
    status: str  # "ok" | "blocked" | "na"
    reason: str = ""
    remedy: Remedy | None = None

    def as_dict(self) -> dict:
        out: dict = {"status": self.status}
        if self.reason:
            out["reason"] = self.reason
        if self.remedy is not None:
            out["remedy"] = {"action": self.remedy.action, "label": self.remedy.label}
        return out


OK = Status("ok")

_BRANCH_OR_BUS = {"Line", "Transformer", "Link", "Bus"}
_STORAGE = {"StorageUnit", "Store"}
_DISPATCHING = {"Generator", "Load", "Link", "StorageUnit", "Store"}
_SIZEABLE = {"Generator", "Line", "Transformer", "Link", "StorageUnit", "Store"}


def category_na_reason(category: str, component_class: str) -> str:
    """Why this category can never apply to this class. Specific beats generic."""
    if category == "loadflow" and component_class not in _BRANCH_OR_BUS:
        return f"{component_class} is not a branch or bus component"
    if category == "storage" and component_class not in _STORAGE:
        return f"{component_class} does not store energy"
    if category == "dispatch" and component_class not in _DISPATCHING:
        return f"{component_class} does not dispatch power"
    if category == "capacity" and component_class not in _SIZEABLE:
        return f"{component_class} has no optimisable capacity"
    if category == "emissions":
        return f"{component_class} does not emit CO₂"
    return f"{CATEGORY_LABELS.get(category, category)} does not apply to {component_class}"


def _na(reason: str) -> Status:
    return Status("na", reason)


def resolve_metric(
    metric: Metric, component_class: str, precond: dict[str, Status]
) -> Status:
    """First unmet precondition wins; unlisted preconditions are treated as ok."""
    if component_class not in metric.classes:
        return _na(f"{metric.label} is not defined for {component_class}")
    for req in metric.requires:
        st = precond.get(req, OK)
        if st.status != "ok":
            return st
    return OK


def resolve_category(
    category: str, component_class: str, precond: dict[str, Status]
) -> Status:
    """
    ok      — at least one member metric resolves ok
    blocked — members exist, none is ok, at least one is blocked
    na      — no members, or every member is na

    When every member is `na` for the SAME reason, that reason wins over the
    generic one. Without this, a phase-2 placeholder reports "Dispatch does
    not apply to Load" — which is false. Loads dispatch; it is simply not
    wired up yet, and the placeholder's own reason says so.
    """
    members = metrics_for(component_class, category)
    if not members:
        return _na(category_na_reason(category, component_class))
    resolved = [resolve_metric(m, component_class, precond) for m in members]
    if any(r.status == "ok" for r in resolved):
        return OK
    for r in resolved:
        if r.status == "blocked":
            return r
    reasons = {r.reason for r in resolved if r.reason}
    if len(reasons) == 1:
        return _na(reasons.pop())
    return _na(category_na_reason(category, component_class))
```

- [ ] **Step 5: Write the `__init__.py` stub**

`compute.py` does not exist yet, so give it a minimal module now — Task 2 fills it in. Create `pypsa-gui/backend/services/asset_results/compute.py` containing only the functions the registry references, each raising until Task 2:

```python
"""Per-metric computation. One function per metric; no HTTP, no serialisation."""
from __future__ import annotations

from typing import Any


def _todo(*_a: Any, **_k: Any):  # replaced wholesale in Task 2
    raise NotImplementedError


def not_yet(*_a: Any, **_k: Any) -> None:
    """Phase 2/3 placeholder metric — never invoked (always resolves `na`)."""
    return None


summary_identity = summary_params = _todo
gen_p_nom = gen_p_nom_opt = gen_p_nom_delta = gen_capex_annual = _todo
gen_p_nom_by_vintage = _todo
gen_p = gen_p_max_pu = gen_available = gen_curtailment = _todo
gen_capacity_factor = gen_status = gen_start_up = gen_shut_down = _todo
gen_energy = gen_full_load_hours = gen_mean_cf = gen_curtailed_energy = _todo
gen_peak = gen_zero_hours = gen_max_ramp_up = gen_max_ramp_down = _todo
gen_n_starts = gen_q = _todo
gen_bus_price = gen_mu_upper = gen_mu_lower = _todo
gen_capture_price = gen_capture_rate = gen_binding_hours = _todo
gen_revenue = gen_vom = gen_fixed_cost = gen_net_profit = gen_lcoe = _todo
gen_co2_rate = gen_co2_total = gen_co2_intensity = _todo
```

Create `pypsa-gui/backend/services/asset_results/__init__.py`:

```python
"""Per-asset results: registry, applicability resolution, computation, export."""
from .applicability import OK, Remedy, Status, resolve_category, resolve_metric
from .registry import CATEGORIES, CATEGORY_IDS, CATEGORY_LABELS, METRICS, Metric

__all__ = [
    "CATEGORIES", "CATEGORY_IDS", "CATEGORY_LABELS", "METRICS", "Metric",
    "OK", "Remedy", "Status", "resolve_category", "resolve_metric",
]
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
cd "$(git rev-parse --show-toplevel)" && pixi run gui-tests tests/test_asset_results_registry.py -v
```

Expected: PASS, 13 tests.

- [ ] **Step 7: Commit**

```bash
cd "$(git rev-parse --show-toplevel)" && git branch --show-current   # must be feature/local-app-impl
git commit pypsa-gui/backend/services/asset_results/ pypsa-gui/backend/tests/test_asset_results_registry.py \
  -m "feat(gui): metric registry + three-state applicability for per-asset results

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Precondition evaluation + the compute context

Turns a live network into the `dict[str, Status]` the resolver consumes, and
builds the shared context every compute function receives. This is where the
five real causes of "blocked" are detected.

**Files:**
- Modify: `pypsa-gui/backend/services/asset_results/compute.py` (replace the stub header, keep the `_todo` aliases for metrics Task 3 covers)
- Create: `pypsa-gui/backend/tests/test_asset_results_preconditions.py`

**Interfaces:**
- Consumes: `registry.REQ_*`, `applicability.Status`, `applicability.Remedy`.
- Produces:
  - `compute.Ctx` frozen dataclass: `n, component_class, name, source, sns, weights, is_multi, params`
    (no separate `years` field — the per-period year multiplier is already folded
    into `weights`, and Task 4's `cost_weights` recomputes its own)
  - `compute.build_ctx(n, component_class, name, *, source, sns) -> Ctx`
  - `compute.preconditions(n, component_class, name) -> dict[str, Status]`
  - `compute.attr_for(component_class) -> str` (e.g. `"Generator" → "generators"`)
  - `compute.summary_identity(ctx) -> dict`, `compute.summary_params(ctx) -> dict`

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/backend/tests/test_asset_results_preconditions.py`:

```python
"""The five causes of `blocked`, each reached by constructing the state."""
import pandas as pd
import pypsa
import pytest

from services.asset_results import compute as C
from services.asset_results import registry as reg
from tests.conftest import build_network


def test_unsolved_network_blocks_dispatch_with_a_run_remedy():
    n = build_network(solve=False)
    pre = C.preconditions(n, "Generator", "gas")
    st = pre[reg.REQ_DISPATCH]
    assert st.status == "blocked"
    assert st.remedy.action == "run_simulation"


def test_solved_network_clears_the_dispatch_precondition():
    n = build_network(solve=True)
    assert C.preconditions(n, "Generator", "gas")[reg.REQ_DISPATCH].status == "ok"


def test_stale_dispatch_blocks_and_says_so():
    n = build_network(solve=True)
    n.add("Generator", "late_addition", bus="B1", p_nom=1.0)  # topology moved
    st = C.preconditions(n, "Generator", "gas")[reg.REQ_DISPATCH]
    assert st.status == "blocked"
    assert "stale" in st.reason.lower() or "changed" in st.reason.lower()


def test_non_committable_generator_blocks_the_uc_metrics():
    n = build_network(solve=True)
    st = C.preconditions(n, "Generator", "gas")[reg.REQ_COMMITTABLE]
    assert st.status == "blocked"
    assert st.remedy.action == "open_properties"
    assert "gas" in st.reason


def test_ac_pf_precondition_is_blocked_until_the_stage_runs():
    n = build_network(solve=True)
    st = C.preconditions(n, "Generator", "gas")[reg.REQ_AC_PF]
    assert st.status == "blocked"
    assert st.remedy.action == "run_ac_pf"


def test_carrier_without_co2_blocks_the_emissions_metrics():
    n = build_network(solve=True)
    st = C.preconditions(n, "Generator", "gas")[reg.REQ_CO2]
    assert st.status == "blocked"
    assert "co2_emissions" in st.reason


def test_carrier_with_co2_clears_the_emissions_precondition():
    n = build_network(solve=False)
    n.add("Carrier", "gas", co2_emissions=0.2)
    n.optimize(solver_name="highs")
    assert C.preconditions(n, "Generator", "gas")[reg.REQ_CO2].status == "ok"


def test_not_yet_is_always_na_and_carries_no_remedy():
    n = build_network(solve=True)
    st = C.preconditions(n, "Generator", "gas")[reg.REQ_NOT_YET]
    assert st.status == "na"
    assert st.remedy is None


def test_attr_for_maps_every_class():
    for cls, attr in [
        ("Bus", "buses"), ("Generator", "generators"), ("Load", "loads"),
        ("Line", "lines"), ("Transformer", "transformers"), ("Link", "links"),
        ("StorageUnit", "storage_units"), ("Store", "stores"),
    ]:
        assert C.attr_for(cls) == attr
    with pytest.raises(KeyError):
        C.attr_for("Nonsense")


def test_summary_identity_reports_class_carrier_and_bus():
    n = build_network(solve=True)
    ctx = C.build_ctx(n, "Generator", "gas", source="lopf", sns=n.snapshots)
    ident = C.summary_identity(ctx)
    assert ident["name"] == "gas"
    assert ident["class"] == "Generator"
    assert ident["carrier"] == "gas"
    assert ident["bus"] == "B1"


def test_summary_params_works_on_an_unsolved_network():
    n = build_network(solve=False)
    ctx = C.build_ctx(n, "Generator", "gas", source="lopf", sns=n.snapshots)
    params = C.summary_params(ctx)
    assert params["p_nom"] == pytest.approx(200.0)
    assert params["marginal_cost"] == pytest.approx(50.0)


def test_build_ctx_carries_weights_matching_the_snapshot_count():
    n = build_network(solve=True, gens_weight=3.0)
    ctx = C.build_ctx(n, "Generator", "gas", source="lopf", sns=n.snapshots)
    assert len(ctx.weights) == len(n.snapshots)
    assert float(ctx.weights.iloc[0]) == pytest.approx(3.0)


def test_multi_period_weights_apply_the_years_multiplier_exactly_once():
    """`snapshot_weights` already folds in investment_period_weightings.years
    for a MultiIndex. Applying the years map a second time in build_ctx would
    give weight x years^2 and inflate every energy and cost total."""
    n = build_network(solve=False)
    base = n.snapshots
    mi = pd.MultiIndex.from_product([[2026, 2031], base], names=["period", "timestep"])
    mi.name = "snapshot"
    n.set_snapshots(mi)
    n.investment_periods = [2026, 2031]
    n.investment_period_weightings["years"] = 5.0
    n.snapshot_weightings["generators"] = 3.0

    ctx = C.build_ctx(n, "Generator", "gas", source="lopf", sns=n.snapshots)
    # 3.0 (snapshot weight) x 5.0 (years) = 15.0 — NOT 75.0.
    assert float(ctx.weights.iloc[0]) == pytest.approx(15.0)
    assert ctx.is_multi is True
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd "$(git rev-parse --show-toplevel)" && pixi run gui-tests tests/test_asset_results_preconditions.py -v
```

Expected: FAIL — `AttributeError: module 'services.asset_results.compute' has no attribute 'preconditions'`.

- [ ] **Step 3: Replace the head of `compute.py`**

Keep the `_todo` alias block at the bottom for everything Task 3 has not yet
implemented; delete an alias as each real function lands.

```python
"""
Per-metric computation. One function per metric.

Every function takes a single `Ctx` and returns either a pandas Series aligned
to `ctx.sns` (series metrics) or a JSON-ready scalar/dict (scalar metrics). No
HTTP, no serialisation, no NaN scrubbing — the router owns all three.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.dispatch_status import dispatch_status as _dispatch_status
from services.period_utils import (
    is_multi_period,
    period_years_map,
    snapshot_weights,
)

# NOTE: `.applicability` and `.registry` are imported INSIDE the functions that
# need them, never at module level. `registry.py` does `from . import compute as C`
# at module scope to bind each Metric's compute function, so a module-level import
# back into registry (or into applicability, which imports registry) is a genuine
# cycle and raises ImportError the moment the package is first imported. Same
# reason `routers.simulation._state_snapshot` is imported function-locally below.

_ATTR: dict[str, str] = {
    "Bus": "buses", "Generator": "generators", "Load": "loads",
    "Line": "lines", "Transformer": "transformers", "Link": "links",
    "StorageUnit": "storage_units", "Store": "stores",
}


def attr_for(component_class: str) -> str:
    """PyPSA's lowercase-plural DataFrame attribute for a component class."""
    return _ATTR[component_class]


@dataclass(frozen=True)
class Ctx:
    n: Any
    component_class: str
    name: str
    source: str
    sns: Any             # pd.Index — the (already filtered) snapshot slice
    weights: Any         # pd.Series aligned to sns — snapshot × period weight
    is_multi: bool
    params: dict         # the asset's static row, NaN-scrubbed


def build_ctx(n, component_class: str, name: str, *, source: str, sns) -> Ctx:
    import math

    import pandas as pd

    attr = attr_for(component_class)
    df = getattr(n, attr)
    params: dict = {}
    if name in df.index:
        for k, v in df.loc[name].to_dict().items():
            if isinstance(v, float) and not math.isfinite(v):
                params[k] = None
            elif isinstance(v, pd.Timestamp):
                params[k] = v.isoformat()
            else:
                params[k] = v

    # Energy basis: the `generators` weighting column, matching n.statistics()
    # and every existing results endpoint.
    #
    # `snapshot_weights` ALREADY multiplies by investment_period_weightings.years
    # when `sns` is a MultiIndex — that is the entire purpose of the helper
    # (period_utils.py:123-135). Do NOT apply the years map again here. Doing so
    # yields weight × years² and inflates every energy and cost total by a factor
    # of `years` on any multi-period network — the "~5× wrong" bug class
    # period_utils exists to prevent, in the over-count direction.
    try:
        w = snapshot_weights(n, "generators", sns)
    except Exception:
        w = pd.Series(1.0, index=sns)
    multi = is_multi_period(n)
    return Ctx(n=n, component_class=component_class, name=name, source=source,
               sns=sns, weights=w, is_multi=multi, params=params)


# ── Preconditions ───────────────────────────────────────────────────────────

def preconditions(n, component_class: str, name: str) -> dict[str, Status]:
    """
    Evaluate every precondition once per request.

    Returned map is consumed by `applicability.resolve_metric`; anything a
    metric does not list is irrelevant to it.
    """
    from routers.simulation import _state_snapshot

    out: dict[str, Status] = {}

    # 1. A FRESH solve. `is_solved` alone is not trustworthy — it survives a
    #    netcdf round-trip even when the _t frames are empty or describe a
    #    different topology, so gate on dispatch_status like every other
    #    results endpoint does.
    ds = _dispatch_status(n) if getattr(n, "is_solved", False) else "none"
    if ds == "fresh":
        out[REQ_DISPATCH] = OK
    elif ds == "stale":
        out[REQ_DISPATCH] = Status(
            "blocked",
            "results are stale — the network changed after the last solve, so "
            "dispatch no longer matches the current topology",
            Remedy("run_simulation", "Re-run simulation"),
        )
    else:
        out[REQ_DISPATCH] = Status(
            "blocked", "the network has not been solved",
            Remedy("run_simulation", "Run simulation"),
        )

    # 2. AC power flow stage results.
    ac = _state_snapshot().get("ac_pf_results")
    out[REQ_AC_PF] = OK if isinstance(ac, dict) and ac else Status(
        "blocked", "AC power flow has not been run",
        Remedy("run_ac_pf", "Run AC power flow"),
    )

    # 3. LP duals for this component class + nodal prices.
    out[REQ_DUALS] = _duals_status(n, component_class)

    # 4. Unit commitment on THIS asset.
    out[REQ_COMMITTABLE] = _committable_status(n, component_class, name)

    # 5. A carbon-bearing carrier on THIS asset.
    out[REQ_CO2] = _co2_status(n, component_class, name)

    # Phase 2/3 placeholder — never actionable.
    out[REQ_NOT_YET] = Status(
        "na", "not yet available — arrives in a later phase of this feature")
    return out


def _duals_status(n, component_class: str) -> Status:
    prices = getattr(n.buses_t, "marginal_price", None)
    if prices is not None and not prices.empty:
        return OK
    return Status(
        "blocked",
        "LP duals were not captured in this solve",
        Remedy("run_simulation", "Re-run simulation"),
    )


def _committable_status(n, component_class: str, name: str) -> Status:
    if component_class not in ("Generator", "Link"):
        return Status("na", f"{component_class} is not committable")
    df = getattr(n, attr_for(component_class))
    if name in df.index and bool(df.at[name, "committable"]):
        status = getattr(getattr(n, f"{attr_for(component_class)}_t"), "status", None)
        if status is not None and name in getattr(status, "columns", []):
            return OK
        return Status(
            "blocked",
            f"{name} is committable but this solve produced no commitment status",
            Remedy("run_simulation", "Re-run simulation"),
        )
    return Status(
        "blocked",
        f"unit commitment is not enabled on {name} (committable = false)",
        Remedy("open_properties", "Enable committable"),
    )


def _co2_status(n, component_class: str, name: str) -> Status:
    df = getattr(n, attr_for(component_class))
    if name not in df.index or "carrier" not in df.columns:
        return Status("na", f"{component_class} has no carrier")
    carrier = str(df.at[name, "carrier"])
    carriers = n.carriers
    if carrier in carriers.index and "co2_emissions" in carriers.columns:
        val = float(carriers.at[carrier, "co2_emissions"] or 0.0)
        if val > 0:
            return OK
    return Status(
        "blocked",
        f"carrier '{carrier}' declares no co2_emissions, so emissions are zero "
        f"by assumption rather than by result",
        Remedy("open_properties", f"Set co2_emissions on '{carrier}'"),
    )


# ── summary (every class) ───────────────────────────────────────────────────

def summary_identity(ctx: Ctx) -> dict:
    p = ctx.params
    out = {"name": ctx.name, "class": ctx.component_class,
           "carrier": p.get("carrier", "")}
    for key in ("bus", "bus0", "bus1", "bus2", "type"):
        if key in p and p[key] not in (None, ""):
            out[key] = p[key]
    return out


# Static columns worth showing per class. Curated, not exhaustive — the full
# parameter sheet stays in PropertiesPanel, which can also edit it.
_SUMMARY_PARAMS: dict[str, tuple[str, ...]] = {
    "Bus": ("v_nom", "control", "x", "y", "carrier"),
    "Generator": ("p_nom", "p_nom_extendable", "p_nom_min", "p_nom_max",
                  "marginal_cost", "capital_cost", "efficiency", "committable",
                  "build_year", "lifetime"),
    "Load": ("p_set", "q_set"),
    "Line": ("s_nom", "s_nom_extendable", "length", "r", "x", "b",
             "capital_cost", "v_nom"),
    "Transformer": ("s_nom", "s_nom_extendable", "r", "x", "tap_ratio",
                    "capital_cost"),
    "Link": ("p_nom", "p_nom_extendable", "efficiency", "marginal_cost",
             "capital_cost", "committable"),
    "StorageUnit": ("p_nom", "p_nom_extendable", "max_hours",
                    "efficiency_store", "efficiency_dispatch", "marginal_cost",
                    "capital_cost", "cyclic_state_of_charge"),
    "Store": ("e_nom", "e_nom_extendable", "e_cyclic", "marginal_cost",
              "capital_cost", "standing_loss"),
}


def summary_params(ctx: Ctx) -> dict:
    keys = _SUMMARY_PARAMS.get(ctx.component_class, ())
    return {k: ctx.params.get(k) for k in keys if k in ctx.params}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd "$(git rev-parse --show-toplevel)" && pixi run gui-tests tests/test_asset_results_preconditions.py -v
```

Expected: PASS, 12 tests. Re-run Task 1's file too — it must still pass.

- [ ] **Step 5: Commit**

```bash
cd "$(git rev-parse --show-toplevel)" && git branch --show-current
git commit pypsa-gui/backend/services/asset_results/compute.py \
  pypsa-gui/backend/tests/test_asset_results_preconditions.py \
  -m "feat(gui): precondition evaluation + compute context for per-asset results

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Generator capacity + dispatch computation

**Files:**
- Modify: `pypsa-gui/backend/services/asset_results/compute.py` (append; delete the matching `_todo` aliases)
- Create: `pypsa-gui/backend/tests/test_asset_results_compute_dispatch.py`

**Interfaces:**
- Consumes: `Ctx`, `attr_for` from Task 2.
- Produces: `series_for(ctx, attr) -> pd.Series | None` plus the 14 capacity/dispatch compute functions named in the Task 1 registry. Series functions return a `pd.Series` indexed by `ctx.sns`; scalar functions return `float | int | dict | None`.

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/backend/tests/test_asset_results_compute_dispatch.py`:

```python
"""Generator capacity + dispatch metrics, checked against direct frame reads."""
import pytest

from services.asset_results import compute as C
from tests.conftest import build_network


@pytest.fixture
def ctx():
    n = build_network(solve=True)
    return C.build_ctx(n, "Generator", "gas", source="lopf", sns=n.snapshots)


def test_p_matches_a_direct_read_of_the_dispatch_frame(ctx):
    got = C.gen_p(ctx)
    want = ctx.n.generators_t.p["gas"]
    assert list(got.values) == pytest.approx(list(want.values))


def test_available_is_optimised_capacity_times_availability(ctx):
    avail = C.gen_available(ctx)
    p_nom_opt = float(ctx.n.generators.at["gas", "p_nom_opt"])
    assert list(avail.values) == pytest.approx([p_nom_opt] * len(ctx.sns))


def test_curtailment_is_available_minus_dispatch_and_never_negative(ctx):
    curt = C.gen_curtailment(ctx)
    avail = C.gen_available(ctx)
    p = C.gen_p(ctx)
    assert list(curt.values) == pytest.approx(list((avail - p).clip(lower=0).values))
    assert (curt >= 0).all()


def test_capacity_factor_is_dispatch_over_optimised_capacity(ctx):
    cf = C.gen_capacity_factor(ctx)
    p_nom_opt = float(ctx.n.generators.at["gas", "p_nom_opt"])
    assert list(cf.values) == pytest.approx(list((C.gen_p(ctx) / p_nom_opt).values))


def test_capacity_factor_is_none_when_optimised_capacity_is_zero():
    n = build_network(solve=True)
    n.generators.loc["gas", "p_nom_opt"] = 0.0
    ctx = C.build_ctx(n, "Generator", "gas", source="lopf", sns=n.snapshots)
    assert C.gen_capacity_factor(ctx) is None


def test_energy_applies_the_snapshot_weighting():
    n = build_network(solve=True, gens_weight=3.0)
    ctx = C.build_ctx(n, "Generator", "gas", source="lopf", sns=n.snapshots)
    raw = float(n.generators_t.p["gas"].sum())
    assert C.gen_energy(ctx) == pytest.approx(raw * 3.0)


def test_full_load_hours_is_energy_over_optimised_capacity(ctx):
    flh = C.gen_full_load_hours(ctx)
    p_nom_opt = float(ctx.n.generators.at["gas", "p_nom_opt"])
    assert flh == pytest.approx(C.gen_energy(ctx) / p_nom_opt)


def test_peak_is_the_maximum_dispatch(ctx):
    assert C.gen_peak(ctx) == pytest.approx(float(ctx.n.generators_t.p["gas"].max()))


def test_zero_hours_counts_weighted_snapshots_at_zero_output():
    n = build_network(solve=True)
    ctx = C.build_ctx(n, "Generator", "solar", source="lopf", sns=n.snapshots)
    p = n.generators_t.p["solar"]
    expected = float((p.abs() < 1e-9).sum())
    assert C.gen_zero_hours(ctx) == pytest.approx(expected)


def test_ramp_metrics_read_consecutive_differences(ctx):
    diffs = ctx.n.generators_t.p["gas"].diff().dropna()
    assert C.gen_max_ramp_up(ctx) == pytest.approx(float(diffs.max()))
    assert C.gen_max_ramp_down(ctx) == pytest.approx(float(diffs.min()))


def test_capacity_scalars_read_the_static_columns(ctx):
    assert C.gen_p_nom(ctx) == pytest.approx(200.0)
    assert C.gen_p_nom_opt(ctx) == pytest.approx(
        float(ctx.n.generators.at["gas", "p_nom_opt"]))
    assert C.gen_p_nom_delta(ctx) == pytest.approx(
        C.gen_p_nom_opt(ctx) - C.gen_p_nom(ctx))


def test_capex_annual_is_capital_cost_times_optimised_capacity(ctx):
    assert C.gen_capex_annual(ctx) == pytest.approx(
        100_000.0 * float(ctx.n.generators.at["gas", "p_nom_opt"]))


def test_vintage_breakdown_is_none_on_a_flat_network(ctx):
    assert C.gen_p_nom_by_vintage(ctx) is None


def test_vintage_breakdown_sums_the_at_year_rows():
    n = build_network(solve=True)
    n.add("Generator", "gas@2030", bus="B1", carrier="gas", p_nom=0.0)
    n.generators.loc["gas@2030", "p_nom_opt"] = 80.0
    ctx = C.build_ctx(n, "Generator", "gas", source="lopf", sns=n.snapshots)
    assert C.gen_p_nom_by_vintage(ctx) == {"2030": pytest.approx(80.0)}


def test_series_for_returns_none_for_an_absent_attribute(ctx):
    assert C.series_for(ctx, "no_such_attr") is None
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd "$(git rev-parse --show-toplevel)" && pixi run gui-tests tests/test_asset_results_compute_dispatch.py -v
```

Expected: FAIL — `NotImplementedError` from the `_todo` alias.

- [ ] **Step 3: Append the implementations to `compute.py`**

Delete the `_todo` aliases these replace.

```python
# ── Series access ───────────────────────────────────────────────────────────

def series_for(ctx: Ctx, attr: str):
    """
    This asset's column from `<component>_t.<attr>`, reindexed to ctx.sns.

    Honours the lopf/ac_pf snapshot the same way every /results/* endpoint
    does, and returns None (rather than raising) whenever the attribute, the
    frame or the column is absent — the caller turns that into `null`.
    """
    from routers.results import _result_df

    df = _result_df(ctx.n, f"{attr_for(ctx.component_class)}_t", attr, ctx.source)
    if df is None or getattr(df, "empty", True):
        return None
    if ctx.name not in df.columns:
        return None
    return df[ctx.name].reindex(ctx.sns)


def _static(ctx: Ctx, col: str) -> float | None:
    v = ctx.params.get(col)
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _wsum(ctx: Ctx, s) -> float | None:
    """Weighted sum over the context's snapshot slice."""
    if s is None:
        return None
    return float((s.fillna(0.0) * ctx.weights).sum())


# ── capacity ────────────────────────────────────────────────────────────────

def gen_p_nom(ctx: Ctx):
    return _static(ctx, "p_nom")


def gen_p_nom_opt(ctx: Ctx):
    return _static(ctx, "p_nom_opt")


def gen_p_nom_delta(ctx: Ctx):
    opt, nom = gen_p_nom_opt(ctx), gen_p_nom(ctx)
    return None if opt is None or nom is None else opt - nom


def gen_capex_annual(ctx: Ctx):
    cc, opt = _static(ctx, "capital_cost"), gen_p_nom_opt(ctx)
    return None if cc is None or opt is None else cc * opt


def gen_p_nom_by_vintage(ctx: Ctx):
    """
    Per-period capacity recovered from the solver's `<name>@<year>` clone rows.

    Those rows are transient and hidden from every asset list, but they carry
    the per-vintage p_nom_opt the user actually wants to see under the parent.
    Returns None on a flat network (no vintages) so the metric renders as
    "not applicable here" rather than an empty object.
    """
    df = getattr(ctx.n, attr_for(ctx.component_class))
    prefix = f"{ctx.name}@"
    out: dict[str, float] = {}
    for row in df.index:
        rs = str(row)
        if not rs.startswith(prefix):
            continue
        year = rs[len(prefix):]
        if not year.isdigit():
            continue
        try:
            out[year] = float(df.at[row, "p_nom_opt"])
        except (KeyError, TypeError, ValueError):
            continue
    return out or None


# ── dispatch: series ────────────────────────────────────────────────────────

def gen_p(ctx: Ctx):
    return series_for(ctx, "p")


def gen_p_max_pu(ctx: Ctx):
    """
    Availability. Time-varying when the user uploaded a profile, otherwise the
    static column broadcast across the horizon — a renewable with a flat
    p_max_pu still has a meaningful availability curve.
    """
    import pandas as pd

    s = series_for(ctx, "p_max_pu")
    if s is not None:
        return s
    v = _static(ctx, "p_max_pu")
    return None if v is None else pd.Series(v, index=ctx.sns)


def gen_available(ctx: Ctx):
    pu, opt = gen_p_max_pu(ctx), gen_p_nom_opt(ctx)
    return None if pu is None or opt is None else pu * opt


def gen_curtailment(ctx: Ctx):
    avail, p = gen_available(ctx), gen_p(ctx)
    if avail is None or p is None:
        return None
    # Clip at zero: tiny LP tolerances put p a hair above availability, and a
    # negative curtailment is not a thing anyone wants to explain.
    return (avail - p).clip(lower=0.0)


def gen_capacity_factor(ctx: Ctx):
    p, opt = gen_p(ctx), gen_p_nom_opt(ctx)
    if p is None or not opt:
        return None
    return p / opt


def gen_status(ctx: Ctx):
    return series_for(ctx, "status")


def gen_start_up(ctx: Ctx):
    return series_for(ctx, "start_up")


def gen_shut_down(ctx: Ctx):
    return series_for(ctx, "shut_down")


def gen_q(ctx: Ctx):
    return series_for(ctx, "q")


# ── dispatch: scalars ───────────────────────────────────────────────────────

def gen_energy(ctx: Ctx):
    return _wsum(ctx, gen_p(ctx))


def gen_full_load_hours(ctx: Ctx):
    e, opt = gen_energy(ctx), gen_p_nom_opt(ctx)
    return None if e is None or not opt else e / opt


def gen_mean_cf(ctx: Ctx):
    e, opt = gen_energy(ctx), gen_p_nom_opt(ctx)
    hours = float(ctx.weights.sum())
    return None if e is None or not opt or not hours else e / (opt * hours)


def gen_curtailed_energy(ctx: Ctx):
    return _wsum(ctx, gen_curtailment(ctx))


def gen_peak(ctx: Ctx):
    p = gen_p(ctx)
    return None if p is None or p.empty else float(p.max())


def gen_zero_hours(ctx: Ctx):
    p = gen_p(ctx)
    if p is None:
        return None
    return float(((p.abs() < 1e-9).astype(float) * ctx.weights).sum())


def gen_max_ramp_up(ctx: Ctx):
    p = gen_p(ctx)
    if p is None or len(p) < 2:
        return None
    return float(p.diff().dropna().max())


def gen_max_ramp_down(ctx: Ctx):
    p = gen_p(ctx)
    if p is None or len(p) < 2:
        return None
    return float(p.diff().dropna().min())


def gen_n_starts(ctx: Ctx):
    s = gen_start_up(ctx)
    return None if s is None else int(round(float(s.fillna(0.0).sum())))
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd "$(git rev-parse --show-toplevel)" && pixi run gui-tests tests/test_asset_results_compute_dispatch.py -v
```

Expected: PASS, 15 tests.

- [ ] **Step 5: Commit**

```bash
cd "$(git rev-parse --show-toplevel)" && git branch --show-current
git commit pypsa-gui/backend/services/asset_results/compute.py \
  pypsa-gui/backend/tests/test_asset_results_compute_dispatch.py \
  -m "feat(gui): generator capacity + dispatch metrics

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Generator prices, economics and emissions

The reconciliation test is the important one here. `/results/asset_economics`
already computes revenue, VOM, fixed cost, profit and LCOE for every generator.
Two independent implementations of one number is exactly how this codebase has
been bitten before, so the test asserts they agree.

**Files:**
- Modify: `pypsa-gui/backend/services/asset_results/compute.py` (append; remove the remaining `_todo` aliases and the `_todo` helper itself)
- Create: `pypsa-gui/backend/tests/test_asset_results_compute_economics.py`

**Interfaces:**
- Consumes: `Ctx`, `series_for`, `_static`, `_wsum`, `gen_p`, `gen_energy` from Task 3.
- Produces: `cost_weights(ctx) -> pd.Series`, `bus_price_series(ctx) -> pd.Series | None`, and the 11 prices/economics/emissions compute functions from the Task 1 registry.

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/backend/tests/test_asset_results_compute_economics.py`:

```python
"""Prices, economics and emissions — including reconciliation with the
existing /results/asset_economics endpoint."""
import pytest

from services.asset_results import compute as C
from tests.conftest import build_network


@pytest.fixture
def ctx():
    n = build_network(solve=True)
    return C.build_ctx(n, "Generator", "gas", source="lopf", sns=n.snapshots)


def test_bus_price_reads_the_generators_own_bus(ctx):
    got = C.gen_bus_price(ctx)
    want = ctx.n.buses_t.marginal_price["B1"]
    assert list(got.values) == pytest.approx(list(want.values))


def test_revenue_is_dispatch_times_price_times_weighting(ctx):
    p = ctx.n.generators_t.p["gas"]
    lam = ctx.n.buses_t.marginal_price["B1"]
    assert C.gen_revenue(ctx) == pytest.approx(float((p * lam).sum()))


def test_vom_is_absolute_dispatch_times_marginal_cost(ctx):
    p = ctx.n.generators_t.p["gas"]
    assert C.gen_vom(ctx) == pytest.approx(float(p.abs().sum()) * 50.0)


def test_fixed_cost_is_capital_cost_times_optimised_capacity(ctx):
    assert C.gen_fixed_cost(ctx) == pytest.approx(
        100_000.0 * float(ctx.n.generators.at["gas", "p_nom_opt"]))


def test_net_profit_is_revenue_minus_fixed_and_variable(ctx):
    assert C.gen_net_profit(ctx) == pytest.approx(
        C.gen_revenue(ctx) - (C.gen_fixed_cost(ctx) + C.gen_vom(ctx)))


def test_lcoe_is_total_cost_over_energy(ctx):
    assert C.gen_lcoe(ctx) == pytest.approx(
        (C.gen_fixed_cost(ctx) + C.gen_vom(ctx)) / C.gen_energy(ctx))


def test_lcoe_is_none_when_the_asset_produced_nothing():
    n = build_network(solve=True)
    n.generators_t.p["gas"] = 0.0
    ctx = C.build_ctx(n, "Generator", "gas", source="lopf", sns=n.snapshots)
    assert C.gen_lcoe(ctx) is None


def test_capture_price_is_the_dispatch_weighted_mean_price(ctx):
    p = ctx.n.generators_t.p["gas"]
    lam = ctx.n.buses_t.marginal_price["B1"]
    assert C.gen_capture_price(ctx) == pytest.approx(
        float((p * lam).sum()) / float(p.sum()))


def test_capture_rate_compares_against_the_time_weighted_mean(ctx):
    lam = ctx.n.buses_t.marginal_price["B1"]
    assert C.gen_capture_rate(ctx) == pytest.approx(
        C.gen_capture_price(ctx) / float(lam.mean()))


def test_binding_hours_counts_snapshots_with_a_nonzero_dual(ctx):
    mu = ctx.n.generators_t.mu_upper.get("gas")
    expected = 0.0 if mu is None else float((mu.abs() > 1e-9).sum())
    assert C.gen_binding_hours(ctx) == pytest.approx(expected)


def test_co2_rate_divides_by_efficiency():
    n = build_network(solve=False)
    n.add("Carrier", "gas", co2_emissions=0.2)
    n.generators.loc["gas", "efficiency"] = 0.5
    n.optimize(solver_name="highs")
    ctx = C.build_ctx(n, "Generator", "gas", source="lopf", sns=n.snapshots)
    rate = C.gen_co2_rate(ctx)
    want = n.generators_t.p["gas"] / 0.5 * 0.2
    assert list(rate.values) == pytest.approx(list(want.values))
    assert C.gen_co2_total(ctx) == pytest.approx(float(want.sum()))
    assert C.gen_co2_intensity(ctx) == pytest.approx(
        float(want.sum()) / C.gen_energy(ctx))


def test_economics_reconcile_with_the_asset_economics_endpoint(client, install_network):
    """Two implementations of one number must agree. See CLAUDE.md."""
    n = build_network(solve=True)
    install_network(n)
    rows = client.get("/api/results/asset_economics").json()["generators"]
    row = next(r for r in rows if r["name"] == "gas")
    ctx = C.build_ctx(n, "Generator", "gas", source="lopf", sns=n.snapshots)
    assert C.gen_revenue(ctx) == pytest.approx(row["revenue_eur"], rel=1e-6)
    assert C.gen_vom(ctx) == pytest.approx(row["vom_cost_eur"], rel=1e-6)
    assert C.gen_fixed_cost(ctx) == pytest.approx(row["fixed_cost_eur"], rel=1e-6)
    assert C.gen_net_profit(ctx) == pytest.approx(row["net_profit_eur"], rel=1e-6)


def test_reconciles_when_a_subsidised_renewable_sets_the_price(
        client, install_network):
    """curtailment_cost drags the bus dual negative when a subsidised
    renewable is the price-setting unit — an LP artefact, not a real price.
    Both implementations must strip it via corrected_marginal_prices, or
    revenue and capture price diverge from the Results tab."""
    n = build_network(solve=False)
    n.generators.loc["solar", "curtailment_cost"] = 30.0
    n.optimize(solver_name="highs")
    install_network(n)
    rows = client.get("/api/results/asset_economics").json()["generators"]
    row = next(r for r in rows if r["name"] == "solar")
    ctx = C.build_ctx(n, "Generator", "solar", source="lopf", sns=n.snapshots)
    assert C.gen_revenue(ctx) == pytest.approx(row["revenue_eur"], rel=1e-6)


def test_reconciles_with_a_time_varying_marginal_cost(client, install_network):
    """/results/asset_economics reads marginal_cost via
    get_switchable_as_dense. A static-only read here would understate VOM for
    any generator with a fuel-price profile."""
    import pandas as pd
    n = build_network(solve=False)
    n.generators_t.marginal_cost = pd.DataFrame(
        {"gas": [40.0, 60.0, 80.0, 100.0]}, index=n.snapshots)
    n.optimize(solver_name="highs")
    install_network(n)
    rows = client.get("/api/results/asset_economics").json()["generators"]
    row = next(r for r in rows if r["name"] == "gas")
    ctx = C.build_ctx(n, "Generator", "gas", source="lopf", sns=n.snapshots)
    assert C.gen_vom(ctx) == pytest.approx(row["vom_cost_eur"], rel=1e-6)
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd "$(git rev-parse --show-toplevel)" && pixi run gui-tests tests/test_asset_results_compute_economics.py -v
```

Expected: FAIL — `NotImplementedError`.

- [ ] **Step 3: Append the implementations to `compute.py`**

Then delete the `_todo` helper and its alias block entirely — every name it
covered now has a real implementation.

```python
# ── Cost weighting ──────────────────────────────────────────────────────────
# Energy figures use the `generators` weighting column (ctx.weights); COST
# figures use `objective`. `/results/asset_economics` draws the same
# distinction, and the reconciliation test in this task depends on matching it.

def cost_weights(ctx: Ctx):
    import pandas as pd

    from services.period_utils import snapshot_weights

    # Same rule as build_ctx: snapshot_weights already folds in
    # investment_period_weightings.years for a MultiIndex `sns`. Multiplying by
    # the years map again here would inflate every cost total by a factor of
    # `years` on a multi-period network.
    try:
        return snapshot_weights(ctx.n, "objective", ctx.sns)
    except Exception:
        return pd.Series(1.0, index=ctx.sns)


def _cost_wsum(ctx: Ctx, s) -> float | None:
    if s is None:
        return None
    return float((s.fillna(0.0) * cost_weights(ctx)).sum())


# ── prices ──────────────────────────────────────────────────────────────────

def bus_price_series(ctx: Ctx):
    """
    Nodal price at the asset's own bus, with the curtailment-cost subsidy
    distortion removed. `bus` for injecting components, `bus0` for branches —
    Phase 1 only needs the former.

    MUST go through `corrected_marginal_prices`, which routers/results.py
    documents as the single source of truth for the merit-order correction and
    which `/results/asset_economics` and the Compare tab both price against.
    The curtailment_cost term adds `-cost × p` to the LP objective for
    subsidised renewables, dragging the bus dual negative when such a unit sets
    the price — an LP-accounting artefact, not a real price. Reading raw
    `buses_t.marginal_price` here would make revenue, capture price and capture
    rate diverge from the Results tab for any generator with
    `curtailment_cost > 0`.
    """
    bus = ctx.params.get("bus") or ctx.params.get("bus0")
    if not bus:
        return None
    from routers.results import corrected_marginal_prices

    try:
        df = corrected_marginal_prices(ctx.n)
    except Exception:
        return None
    if df is None or getattr(df, "empty", True) or bus not in df.columns:
        return None
    return df[bus].reindex(ctx.sns)


def gen_bus_price(ctx: Ctx):
    return bus_price_series(ctx)


def gen_mu_upper(ctx: Ctx):
    return series_for(ctx, "mu_upper")


def gen_mu_lower(ctx: Ctx):
    return series_for(ctx, "mu_lower")


def gen_capture_price(ctx: Ctx):
    p, lam = gen_p(ctx), bus_price_series(ctx)
    if p is None or lam is None:
        return None
    w = cost_weights(ctx)
    denom = float((p.fillna(0.0) * w).sum())
    if abs(denom) < 1e-9:
        return None
    return float((p.fillna(0.0) * lam.fillna(0.0) * w).sum()) / denom


def gen_capture_rate(ctx: Ctx):
    cap, lam = gen_capture_price(ctx), bus_price_series(ctx)
    if cap is None or lam is None:
        return None
    w = cost_weights(ctx)
    hours = float(w.sum())
    if not hours:
        return None
    mean_price = float((lam.fillna(0.0) * w).sum()) / hours
    return None if abs(mean_price) < 1e-9 else cap / mean_price


def gen_binding_hours(ctx: Ctx):
    # No early return when BOTH series are absent. A non-extendable,
    # non-committable generator legitimately has no mu_upper/mu_lower columns
    # at all — PyPSA enforces its dispatch bounds as variable bounds rather
    # than linear constraints, so no dual is ever assigned. That means the
    # bound never bound: zero binding hours, not "unknown". An early
    # `return None` here would also make the `else 0.0` fallback below dead
    # code and contradict this task's own test.
    up, lo = gen_mu_upper(ctx), gen_mu_lower(ctx)
    binding = None
    for s in (up, lo):
        if s is None:
            continue
        b = s.abs() > 1e-9
        binding = b if binding is None else (binding | b)
    return float(binding.astype(float).sum()) if binding is not None else 0.0


# ── economics ───────────────────────────────────────────────────────────────

def gen_revenue(ctx: Ctx):
    p, lam = gen_p(ctx), bus_price_series(ctx)
    return None if p is None or lam is None else _cost_wsum(ctx, p * lam)


def gen_vom(ctx: Ctx):
    # Time-varying marginal_cost first, static column as fallback — the same
    # idiom gen_p_max_pu uses, and what /results/asset_economics does via
    # get_switchable_as_dense. A static-only read silently diverges from the
    # Results tab for any generator carrying a fuel-price profile.
    p = gen_p(ctx)
    if p is None:
        return None
    mc = None
    try:
        mc_df = ctx.n.get_switchable_as_dense(ctx.component_class, "marginal_cost")
        if mc_df is not None and ctx.name in getattr(mc_df, "columns", []):
            mc = mc_df[ctx.name].reindex(ctx.sns)
    except Exception:
        mc = None
    if mc is None:
        mc = _static(ctx, "marginal_cost")
        if mc is None:
            return None
    return _cost_wsum(ctx, p.abs() * mc)


def gen_fixed_cost(ctx: Ctx):
    return gen_capex_annual(ctx)


def gen_net_profit(ctx: Ctx):
    rev, fixed, vom = gen_revenue(ctx), gen_fixed_cost(ctx), gen_vom(ctx)
    if rev is None:
        return None
    return rev - ((fixed or 0.0) + (vom or 0.0))


def gen_lcoe(ctx: Ctx):
    e = gen_energy(ctx)
    if not e:
        return None
    return ((gen_fixed_cost(ctx) or 0.0) + (gen_vom(ctx) or 0.0)) / e


# ── emissions ───────────────────────────────────────────────────────────────

def _co2_intensity_of_carrier(ctx: Ctx) -> float | None:
    carrier = ctx.params.get("carrier")
    carriers = ctx.n.carriers
    if not carrier or carrier not in carriers.index:
        return None
    if "co2_emissions" not in carriers.columns:
        return None
    try:
        return float(carriers.at[carrier, "co2_emissions"] or 0.0)
    except (TypeError, ValueError):
        return None


def gen_co2_rate(ctx: Ctx):
    p = gen_p(ctx)
    factor = _co2_intensity_of_carrier(ctx)
    eff = _static(ctx, "efficiency") or 1.0
    if p is None or factor is None or not eff:
        return None
    # PyPSA's convention: co2_emissions is per unit of PRIMARY energy, so the
    # electrical output is divided by efficiency to recover fuel input first.
    return p / eff * factor


def gen_co2_total(ctx: Ctx):
    return _wsum(ctx, gen_co2_rate(ctx))


def gen_co2_intensity(ctx: Ctx):
    total, e = gen_co2_total(ctx), gen_energy(ctx)
    return None if total is None or not e else total / e
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd "$(git rev-parse --show-toplevel)" && pixi run gui-tests tests/test_asset_results_compute_economics.py -v
cd "$(git rev-parse --show-toplevel)" && pixi run gui-tests tests/ -k asset_results -v
```

Expected: PASS. The whole `asset_results` selection is green.

- [ ] **Step 5: Commit**

```bash
cd "$(git rev-parse --show-toplevel)" && git branch --show-current
git commit pypsa-gui/backend/services/asset_results/compute.py \
  pypsa-gui/backend/tests/test_asset_results_compute_economics.py \
  -m "feat(gui): generator prices, economics and emissions metrics

Reconciles revenue/VOM/fixed/profit against /results/asset_economics so the
two implementations of one number cannot drift.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Orchestration + the GET endpoint

**Files:**
- Create: `pypsa-gui/backend/services/asset_results/service.py` (orchestration — added to the layout the spec sketched, so `compute.py` stays metric functions only)
- Modify: `pypsa-gui/backend/services/asset_results/__init__.py` (re-export)
- Create: `pypsa-gui/backend/routers/asset_results.py`
- Modify: `pypsa-gui/backend/main.py` (one `include_router` line, after the results router at line ~734)
- Create: `pypsa-gui/backend/tests/test_asset_results_endpoint.py`

**Interfaces:**
- Consumes: everything from Tasks 1–4.
- Produces:
  - `service.list_assets(n) -> list[dict]` — transient-filtered `[{class, name, carrier, bus}]`
  - `service.slice_snapshots(n, from_iso, to_iso, period) -> pd.Index`
  - `service.apply_view_mode(index, periods, series_map, metrics, mode) -> dict` with keys `index, periods, pct_of_hours, columns, series`
  - `service.build_response(n, component_class, name, *, category, metric_ids, source, from_iso, to_iso, period, mode) -> dict`
  - Route `GET /api/results/asset/assets`
  - Route `GET /api/results/asset/{component_class}/{name}`

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/backend/tests/test_asset_results_endpoint.py`:

```python
"""The per-asset endpoint: contract, gating, filters and view modes."""
import pytest

from tests.conftest import build_network

BASE = "/api/results/asset"


def _get(client, path, **params):
    return client.get(f"{BASE}{path}", params=params)


def test_asset_list_is_transient_filtered(client, install_network):
    n = build_network(solve=True)
    n.add("Generator", "__voll_B1", bus="B1", p_nom=1e6, marginal_cost=1e5)
    n.add("Generator", "gas@2030", bus="B1", carrier="gas", p_nom=0.0)
    from services.pypsa_service import PyPSAService
    install_network(n)
    PyPSAService.mark_transient("Generator", "__voll_B1")
    PyPSAService.mark_transient("Generator", "gas@2030")

    names = [a["name"] for a in _get(client, "/assets").json()["assets"]]
    assert "gas" in names
    assert "__voll_B1" not in names
    assert "gas@2030" not in names


def test_every_category_is_returned_with_a_status(client, install_network):
    install_network(build_network(solve=True))
    body = _get(client, "/Generator/gas", category="dispatch").json()
    ids = [c["id"] for c in body["categories"]]
    assert ids == ["summary", "capacity", "dispatch", "storage",
                   "loadflow", "prices", "economics", "emissions"]
    by_id = {c["id"]: c for c in body["categories"]}
    assert by_id["dispatch"]["status"] == "ok"
    assert by_id["storage"]["status"] == "na"
    assert "store energy" in by_id["storage"]["reason"]
    assert by_id["loadflow"]["status"] in ("blocked", "na")


def test_metrics_list_includes_blocked_entries_because_it_is_the_checklist(
        client, install_network):
    install_network(build_network(solve=True))
    body = _get(client, "/Generator/gas", category="dispatch").json()
    by_id = {m["id"]: m for m in body["metrics"]}
    assert by_id["p"]["status"] == "ok"
    assert by_id["status"]["status"] == "blocked"
    assert by_id["status"]["remedy"]["action"] == "open_properties"
    assert "unit" in by_id["p"] and by_id["p"]["unit"] == "MW"
    assert by_id["curtailment"]["origin"] == "derived"
    assert by_id["curtailment"]["formula"]


def test_requested_series_match_a_direct_frame_read(client, install_network):
    n = build_network(solve=True)
    install_network(n)
    body = _get(client, "/Generator/gas", category="dispatch", metrics="p").json()
    assert body["series"]["p"] == pytest.approx(list(n.generators_t.p["gas"].values))
    assert len(body["index"]) == len(n.snapshots)


def test_blocked_metrics_are_never_served_even_if_requested(client, install_network):
    install_network(build_network(solve=True))
    body = _get(client, "/Generator/gas", category="dispatch",
                metrics="p,status").json()
    assert "p" in body["series"]
    assert "status" not in body["series"]


def test_summary_stays_live_on_an_unsolved_network(client, install_network):
    install_network(build_network(solve=False))
    body = _get(client, "/Generator/gas", category="summary").json()
    by_id = {c["id"]: c for c in body["categories"]}
    assert by_id["summary"]["status"] == "ok"
    assert by_id["dispatch"]["status"] == "blocked"
    assert by_id["dispatch"]["remedy"]["action"] == "run_simulation"
    assert body["scalars"]["params"]["p_nom"] == pytest.approx(200.0)


def test_stale_dispatch_blocks_every_result_category(client, install_network):
    n = build_network(solve=True)
    n.add("Generator", "added_after_solve", bus="B1", p_nom=1.0)
    install_network(n)
    body = _get(client, "/Generator/gas", category="dispatch").json()
    by_id = {c["id"]: c for c in body["categories"]}
    assert by_id["summary"]["status"] == "ok"
    assert by_id["dispatch"]["status"] == "blocked"
    assert body["series"] == {}


def test_unknown_asset_is_404(client, install_network):
    install_network(build_network(solve=True))
    assert _get(client, "/Generator/nope", category="summary").status_code == 404


def test_unknown_class_is_404(client, install_network):
    install_network(build_network(solve=True))
    assert _get(client, "/Nonsense/gas", category="summary").status_code == 404


def test_unknown_category_is_422(client, install_network):
    install_network(build_network(solve=True))
    assert _get(client, "/Generator/gas", category="nope").status_code == 422


def test_horizon_filter_narrows_the_index(client, install_network):
    n = build_network(solve=True)
    install_network(n)
    body = _get(client, "/Generator/gas", category="dispatch", metrics="p",
                **{"from": "2025-01-01T01:00:00", "to": "2025-01-01T02:00:00"}).json()
    assert len(body["index"]) == 2
    assert len(body["series"]["p"]) == 2


def test_non_finite_values_serialise_to_null(client, install_network):
    n = build_network(solve=True)
    n.generators_t.p.iloc[0, n.generators_t.p.columns.get_loc("gas")] = float("nan")
    install_network(n)
    r = _get(client, "/Generator/gas", category="dispatch", metrics="p")
    assert r.status_code == 200          # not a 21-byte plain-text 500
    assert r.json()["series"]["p"][0] is None


def test_duration_mode_sorts_each_series_and_reports_percentiles(
        client, install_network):
    n = build_network(solve=True)
    install_network(n)
    body = _get(client, "/Generator/gas", category="dispatch", metrics="p",
                mode="duration").json()
    vals = body["series"]["p"]
    assert vals == sorted(vals, reverse=True)
    assert body["pct_of_hours"][0] == pytest.approx(1 / len(vals))
    assert body["columns"][0]["metric_id"] == "p"


def test_monthly_mode_emits_one_column_triple_per_metric(client, install_network):
    install_network(build_network(solve=True))
    body = _get(client, "/Generator/gas", category="dispatch", metrics="p",
                mode="monthly").json()
    ids = [c["id"] for c in body["columns"]]
    assert ids == ["p__mean", "p__max", "p__energy"]
    assert [c["agg"] for c in body["columns"]] == ["mean", "max", "energy"]
    assert body["index"] == ["2025-01"]


def test_chronological_columns_carry_no_aggregation(client, install_network):
    install_network(build_network(solve=True))
    body = _get(client, "/Generator/gas", category="dispatch", metrics="p").json()
    assert body["columns"] == [
        {"id": "p", "label": "Active power", "unit": "MW",
         "metric_id": "p", "agg": None}
    ]


def _multi_period_network():
    """2-period MultiIndex network, solved. `mi.name = "snapshot"` is
    load-bearing: this repo has a documented failure class where a MultiIndex
    loses its overall `.name` and xarray then reports a `dim_0` error."""
    import pandas as pd
    n = build_network(solve=False)
    base = n.snapshots
    mi = pd.MultiIndex.from_product([[2026, 2031], base], names=["period", "timestep"])
    mi.name = "snapshot"
    n.set_snapshots(mi)
    n.investment_periods = [2026, 2031]
    n.investment_period_weightings["years"] = 5.0
    n.optimize(solver_name="highs")
    return n


def test_multi_period_series_align_and_do_not_come_back_all_null(
        client, install_network):
    """`series_for` reindexes a `_t` frame to ctx.sns. On a MultiIndex the
    reindex aligns by tuple; if it ever silently misaligned, every value would
    be null and the tab would look empty rather than broken."""
    install_network(_multi_period_network())
    body = _get(client, "/Generator/gas", category="dispatch", metrics="p").json()
    assert len(body["index"]) == 8            # 4 timesteps x 2 periods
    assert body["periods"] == [2026] * 4 + [2031] * 4
    assert all(v is not None for v in body["series"]["p"])


def test_period_filter_narrows_to_one_investment_period(client, install_network):
    install_network(_multi_period_network())
    body = _get(client, "/Generator/gas", category="dispatch", metrics="p",
                period="2031").json()
    assert len(body["index"]) == 4
    assert set(body["periods"]) == {2031}


def test_multi_period_energy_applies_years_exactly_once(client, install_network):
    """Guards the same double-multiplication that shipped in Task 2: with
    years=5 and 8 snapshots, energy must be 5x the raw dispatch sum, not 25x."""
    n = _multi_period_network()
    install_network(n)
    body = _get(client, "/Generator/gas", category="dispatch",
                metrics="energy_mwh").json()
    raw = float(n.generators_t.p["gas"].sum())
    assert body["scalars"]["energy_mwh"] == pytest.approx(raw * 5.0)
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd "$(git rev-parse --show-toplevel)" && pixi run gui-tests tests/test_asset_results_endpoint.py -v
```

Expected: every test 404s — the router is not mounted.

- [ ] **Step 3: Write `service.py`**

```python
"""
Orchestration: pick the asset, slice the horizon, run the requested metrics,
reshape for the view mode. `compute.py` stays metric functions; this module
is the only place that knows about requests.
"""
from __future__ import annotations

from typing import Any

from services.pypsa_service import PyPSAService

from . import compute as C
from .applicability import resolve_category, resolve_metric
from .registry import (
    CATEGORIES,
    CATEGORY_IDS,
    ALL_CLASSES,
    metric_by_id,
    metrics_for,
)

VIEW_MODES = ("chronological", "duration", "monthly")


def list_assets(n) -> list[dict]:
    """Every selectable asset, transient rows removed — same filter as every
    other asset list, so `__voll_*` and `<name>@<year>` never appear."""
    out: list[dict] = []
    for cls in ALL_CLASSES:
        attr = C.attr_for(cls)
        df = getattr(n, attr, None)
        if df is None or df.empty:
            continue
        transient: set[str] = set()
        if PyPSAService.has_any_transient_rows():
            transient = PyPSAService.get_transient_rows(cls)
        for name in df.index:
            if name in transient:
                continue
            row = df.loc[name]
            out.append({
                "class": cls,
                "name": str(name),
                "carrier": str(row.get("carrier", "") or ""),
                "bus": str(row.get("bus", row.get("bus0", "")) or ""),
            })
    return out


def slice_snapshots(n, from_iso: str | None, to_iso: str | None, period):
    """Apply the Results shell's horizon filter + period strip to n.snapshots."""
    import pandas as pd

    sns = n.snapshots
    if isinstance(sns, pd.MultiIndex):
        if period is not None:
            keep = [s for s in sns if str(s[0]) == str(period)]
            sns = pd.MultiIndex.from_tuples(keep, names=sns.names) if keep else sns
            sns.name = "snapshot"
        stamps = [pd.Timestamp(s[1]).isoformat() for s in sns]
    else:
        stamps = [pd.Timestamp(s).isoformat() for s in sns]

    if from_iso or to_iso:
        keep_idx = [
            i for i, st in enumerate(stamps)
            if (not from_iso or st >= from_iso) and (not to_iso or st <= to_iso)
        ]
        sns = sns[keep_idx]
    return sns


def _stamps_and_periods(n, sns) -> tuple[list[str], list | None]:
    import pandas as pd

    if isinstance(sns, pd.MultiIndex):
        return ([pd.Timestamp(s[1]).isoformat() for s in sns],
                [s[0] for s in sns])
    return ([pd.Timestamp(s).isoformat() for s in sns], None)


def apply_view_mode(stamps, periods, series_map: dict, metrics: dict, mode: str) -> dict:
    """
    Reshape the chronological series into the requested view.

    Returns index / periods / pct_of_hours / columns / series. `columns`
    describes every emitted column (id, label, unit, metric_id, agg) so the
    frontend never has to infer a naming convention — the registry stays the
    only place that knows what a metric is called.
    """
    import math

    def col(mid: str, agg: str | None) -> dict:
        m = metrics[mid]
        suffix = {"mean": " (mean)", "max": " (max)", "energy": " (energy)"}
        return {
            "id": mid if agg is None else f"{mid}__{agg}",
            "label": m.label + ("" if agg is None else suffix[agg]),
            "unit": m.unit if agg != "energy" else f"{m.unit}h",
            "metric_id": mid,
            "agg": agg,
        }

    if mode == "duration":
        out_series: dict[str, list] = {}
        n_rows = 0
        for mid, vals in series_map.items():
            finite = sorted(
                (v for v in vals if v is not None and math.isfinite(v)),
                reverse=True,
            )
            out_series[mid] = finite
            n_rows = max(n_rows, len(finite))
        # Pad every series to the longest so the table stays rectangular.
        for mid in out_series:
            out_series[mid] += [None] * (n_rows - len(out_series[mid]))
        return {
            "index": [str(i + 1) for i in range(n_rows)],
            "periods": None,
            "pct_of_hours": [(i + 1) / n_rows for i in range(n_rows)] if n_rows else [],
            "columns": [col(mid, None) for mid in series_map],
            "series": out_series,
        }

    if mode == "monthly":
        months: list[str] = []
        buckets: dict[str, list[int]] = {}
        for i, st in enumerate(stamps):
            key = st[:7]
            if key not in buckets:
                buckets[key] = []
                months.append(key)
            buckets[key].append(i)
        columns: list[dict] = []
        out_series = {}
        for mid, vals in series_map.items():
            for agg in ("mean", "max", "energy"):
                c = col(mid, agg)
                columns.append(c)
                acc = []
                for mth in months:
                    picked = [vals[i] for i in buckets[mth]
                              if vals[i] is not None and math.isfinite(vals[i])]
                    if not picked:
                        acc.append(None)
                    elif agg == "mean":
                        acc.append(sum(picked) / len(picked))
                    elif agg == "max":
                        acc.append(max(picked))
                    else:
                        acc.append(sum(picked))
                out_series[c["id"]] = acc
        return {"index": months, "periods": None, "pct_of_hours": None,
                "columns": columns, "series": out_series}

    return {
        "index": stamps,
        "periods": periods,
        "pct_of_hours": None,
        "columns": [col(mid, None) for mid in series_map],
        "series": series_map,
    }


def build_response(
    n, component_class: str, name: str, *, category: str,
    metric_ids: list[str], source: str, from_iso: str | None,
    to_iso: str | None, period, mode: str,
) -> dict:
    from services.serialization import clean_scalar

    from routers.simulation import _state_snapshot

    precond = C.preconditions(n, component_class, name)
    sns = slice_snapshots(n, from_iso, to_iso, period)
    stamps, periods = _stamps_and_periods(n, sns)

    categories = []
    for cid, label in CATEGORIES:
        st = resolve_category(cid, component_class, precond)
        categories.append({"id": cid, "label": label, **st.as_dict()})

    members = metrics_for(component_class, category)
    metric_rows, resolved = [], {}
    for m in members:
        st = resolve_metric(m, component_class, precond)
        resolved[m.id] = st
        row = {"id": m.id, "label": m.label, "unit": m.unit, "kind": m.kind,
               "origin": m.origin, **st.as_dict()}
        if m.formula:
            row["formula"] = m.formula
        metric_rows.append(row)

    wanted = [mid for mid in metric_ids
              if mid in resolved and resolved[mid].status == "ok"]
    series_map: dict[str, list] = {}
    scalars: dict[str, Any] = {}
    by_id = {m.id: m for m in members}

    for mid in wanted:
        m = by_id[mid]
        ctx = C.build_ctx(
            n, component_class, name,
            source=(m.source_override or source), sns=sns,
        )
        try:
            value = m.compute(ctx)
        except Exception:
            value = None
        if value is None:
            continue
        if m.kind == "series":
            series_map[mid] = [clean_scalar(v) for v in list(value.values)]
        else:
            scalars[mid] = clean_scalar(value) if not isinstance(value, dict) \
                else {k: clean_scalar(v) for k, v in value.items()}

    shaped = apply_view_mode(stamps, periods, series_map, by_id, mode)
    state = _state_snapshot()
    ctx0 = C.build_ctx(n, component_class, name, source=source, sns=sns)

    return {
        "asset": {**C.summary_identity(ctx0), "params": C.summary_params(ctx0)},
        "solve": {
            "source": source,
            "objective": clean_scalar(state.get("objective")),
            "solve_time": clean_scalar(state.get("solve_time")),
            "condition": state.get("condition"),
        },
        "category": category,
        "mode": mode,
        "categories": categories,
        "metrics": metric_rows,
        "scalars": scalars,
        **shaped,
    }
```

- [ ] **Step 4: Write `routers/asset_results.py` and mount it**

```python
"""Read-only per-asset results. Two endpoints; all logic lives in the service."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from services.asset_results import service as svc
from services.asset_results.registry import ALL_CLASSES, CATEGORY_IDS
from services.pypsa_service import PyPSAService

logger = logging.getLogger("pypsa_gui.asset_results")

router = APIRouter()


@router.get("/assets", operation_id="list_asset_results_assets")
def list_assets():
    """Every selectable asset, transient rows filtered out."""
    return {"assets": svc.list_assets(PyPSAService.get_network())}


@router.get("/{component_class}/{name}", operation_id="get_asset_results")
def get_asset_results(
    component_class: str,
    name: str,
    category: str = Query("summary"),
    metrics: str = Query(""),
    source: str = Query("lopf"),
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    period: str | None = Query(None),
    mode: str = Query("chronological"),
):
    if component_class not in ALL_CLASSES:
        raise HTTPException(404, f"Unknown component class '{component_class}'")
    if category not in CATEGORY_IDS:
        raise HTTPException(422, f"Unknown category '{category}'")
    if mode not in svc.VIEW_MODES:
        raise HTTPException(422, f"Unknown view mode '{mode}'")
    if source not in ("lopf", "ac_pf"):
        source = "lopf"  # fail soft, matching every other results endpoint

    n = PyPSAService.get_network()
    df = getattr(n, svc.C.attr_for(component_class))
    if name not in df.index:
        raise HTTPException(404, f"No {component_class} named '{name}'")

    metric_ids = [m for m in (metrics.split(",") if metrics else []) if m]
    try:
        return svc.build_response(
            n, component_class, name, category=category, metric_ids=metric_ids,
            source=source, from_iso=from_, to_iso=to, period=period, mode=mode,
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("asset results failed for %s/%s", component_class, name)
        raise HTTPException(500, "Failed to compute asset results")
```

Add to `main.py` immediately after the results router (line ~734):

```python
app.include_router(asset_results.router, prefix="/api/results/asset", tags=["asset-results"])
```

…and add `asset_results` to the `from routers import (...)` block at the top of
`main.py`. Mount it AFTER `results.results_router` so the more specific
`/api/results/asset/...` prefix is registered without shadowing.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd "$(git rev-parse --show-toplevel)" && pixi run gui-tests tests/test_asset_results_endpoint.py -v
```

Expected: PASS, 15 tests.

- [ ] **Step 6: Commit**

```bash
cd "$(git rev-parse --show-toplevel)" && git branch --show-current
git commit pypsa-gui/backend/services/asset_results/ pypsa-gui/backend/routers/asset_results.py \
  pypsa-gui/backend/main.py pypsa-gui/backend/tests/test_asset_results_endpoint.py \
  -m "feat(gui): GET /api/results/asset — per-asset results endpoint

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: XLSX export endpoint

**Files:**
- Create: `pypsa-gui/backend/services/asset_results/export.py`
- Modify: `pypsa-gui/backend/routers/asset_results.py` (one route)
- Create: `pypsa-gui/backend/tests/test_asset_results_export.py`

**Interfaces:**
- Consumes: `service.build_response`.
- Produces:
  - `export.build_workbook(n, component_class, name, *, scope, category, metric_ids, source, from_iso, to_iso, period, mode, project) -> bytes`
  - Route `GET /api/results/asset/{component_class}/{name}/export.xlsx`

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/backend/tests/test_asset_results_export.py`:

```python
"""The workbook: provenance, sheet layout and the two scopes."""
import io

import openpyxl
import pytest

from tests.conftest import build_network

URL = "/api/results/asset/Generator/gas/export.xlsx"


def _book(resp):
    assert resp.status_code == 200
    return openpyxl.load_workbook(io.BytesIO(resp.content))


def test_configured_scope_writes_about_summary_and_the_category(
        client, install_network):
    install_network(build_network(solve=True))
    wb = _book(client.get(URL, params={
        "scope": "view", "category": "dispatch", "metrics": "p,energy_mwh"}))
    assert wb.sheetnames == ["About", "Summary", "Dispatch"]


def test_about_sheet_carries_every_provenance_field(client, install_network):
    install_network(build_network(solve=True))
    wb = _book(client.get(URL, params={"scope": "view", "category": "dispatch",
                                       "metrics": "p"}))
    keys = {row[0] for row in wb["About"].iter_rows(min_col=1, max_col=1,
                                                    values_only=True) if row[0]}
    for expected in ("Asset", "Component class", "Category", "View mode",
                     "Result source", "Horizon from", "Horizon to", "Period",
                     "Objective", "PyPSA version", "Generated at"):
        assert expected in keys, f"About sheet is missing '{expected}'"


def test_data_sheet_header_matches_the_columns_contract(client, install_network):
    install_network(build_network(solve=True))
    wb = _book(client.get(URL, params={"scope": "view", "category": "dispatch",
                                       "metrics": "p"}))
    header = [c.value for c in wb["Dispatch"][1]]
    assert header[0] == "snapshot"
    assert "Active power (MW)" in header


def test_duration_mode_writes_rank_and_percentile_columns(client, install_network):
    install_network(build_network(solve=True))
    wb = _book(client.get(URL, params={"scope": "view", "category": "dispatch",
                                       "metrics": "p", "mode": "duration"}))
    header = [c.value for c in wb["Dispatch"][1]]
    assert header[0] == "rank"
    assert header[1] == "pct_of_hours"


def test_full_scope_writes_every_applicable_category(client, install_network):
    install_network(build_network(solve=True))
    wb = _book(client.get(URL, params={"scope": "full"}))
    assert "Dispatch" in wb.sheetnames
    assert "Capacity" in wb.sheetnames
    assert "Storage" not in wb.sheetnames, "n/a categories must be omitted"


def test_full_scope_lists_the_omitted_categories_in_about(client, install_network):
    install_network(build_network(solve=True))
    wb = _book(client.get(URL, params={"scope": "full"}))
    text = "\n".join(
        str(row[0]) + "|" + str(row[1])
        for row in wb["About"].iter_rows(min_col=1, max_col=2, values_only=True)
    )
    assert "Storage" in text and "store energy" in text


def test_unsolved_network_still_exports_the_summary(client, install_network):
    install_network(build_network(solve=False))
    wb = _book(client.get(URL, params={"scope": "full"}))
    assert "Summary" in wb.sheetnames
    assert "Dispatch" not in wb.sheetnames


def test_bad_scope_is_422(client, install_network):
    install_network(build_network(solve=True))
    assert client.get(URL, params={"scope": "nope"}).status_code == 422
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd "$(git rev-parse --show-toplevel)" && pixi run gui-tests tests/test_asset_results_export.py -v
```

Expected: 404 on every request.

- [ ] **Step 3: Write `export.py`**

```python
"""
Workbook builder. One `About` sheet of provenance, one `Summary` sheet of
scalars, then one sheet per exported category.

An exported file outlives the screen it came from, so every assumption that
shaped the numbers — source, horizon, period, view mode — is written down.
"""
from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Any

from .registry import CATEGORY_IDS, CATEGORY_LABELS
from .service import build_response

SCOPES = ("view", "full")


def _about_rows(resp: dict, *, scope: str, project: str | None,
                from_iso, to_iso, period, omitted: list[tuple[str, str]]) -> list[list]:
    import pypsa

    rows: list[list[Any]] = [
        ["Asset", resp["asset"]["name"]],
        ["Component class", resp["asset"]["class"]],
        ["Carrier", resp["asset"].get("carrier", "")],
        ["Bus", resp["asset"].get("bus", "")],
        ["Project", project or "(unsaved)"],
        ["Export scope", scope],
        ["Category", CATEGORY_LABELS.get(resp["category"], resp["category"])],
        ["View mode", resp["mode"]],
        ["Result source", resp["solve"]["source"]],
        ["Solver condition", resp["solve"].get("condition") or "—"],
        ["Objective", resp["solve"].get("objective")],
        ["Solve time (s)", resp["solve"].get("solve_time")],
        ["Horizon from", from_iso or "(full horizon)"],
        ["Horizon to", to_iso or "(full horizon)"],
        ["Period", str(period) if period is not None else "(all periods)"],
        ["PyPSA version", getattr(pypsa, "__version__", "unknown")],
        ["Generated at", datetime.now(timezone.utc).isoformat(timespec="seconds")],
    ]
    if resp["mode"] == "duration":
        rows.append(["Note", "Duration mode sorts EACH series independently — "
                             "a row is a rank, not a snapshot"])
    for cat_label, reason in omitted:
        rows.append([f"Omitted: {cat_label}", reason])
    return rows


def _data_rows(resp: dict) -> tuple[list, list[list]]:
    cols = resp["columns"]
    if resp["mode"] == "duration":
        header = ["rank", "pct_of_hours"] + [
            f"{c['label']} ({c['unit']})" if c["unit"] else c["label"] for c in cols]
        rows = []
        for i, rank in enumerate(resp["index"]):
            rows.append([rank, resp["pct_of_hours"][i]]
                        + [resp["series"][c["id"]][i] for c in cols])
        return header, rows

    first = "month" if resp["mode"] == "monthly" else "snapshot"
    header = [first]
    if resp.get("periods"):
        header.append("period")
    header += [f"{c['label']} ({c['unit']})" if c["unit"] else c["label"]
               for c in cols]
    rows = []
    for i, stamp in enumerate(resp["index"]):
        row = [stamp]
        if resp.get("periods"):
            row.append(resp["periods"][i])
        row += [resp["series"][c["id"]][i] for c in cols]
        rows.append(row)
    return header, rows


def build_workbook(
    n, component_class: str, name: str, *, scope: str, category: str,
    metric_ids: list[str], source: str, from_iso, to_iso, period, mode: str,
    project: str | None,
) -> bytes:
    import pandas as pd

    from .registry import metrics_for

    if scope == "view":
        categories = [category]
    else:
        categories = list(CATEGORY_IDS)

    sheets: dict[str, tuple[list, list[list]]] = {}
    scalar_rows: list[list] = [["Category", "Metric", "Value", "Unit", "Formula"]]
    omitted: list[tuple[str, str]] = []
    first_resp: dict | None = None

    for cat in categories:
        ids = metric_ids if scope == "view" else [
            m.id for m in metrics_for(component_class, cat)]
        resp = build_response(
            n, component_class, name, category=cat, metric_ids=ids,
            source=source, from_iso=from_iso, to_iso=to_iso, period=period,
            mode=mode,
        )
        if first_resp is None:
            first_resp = resp
        st = next(c for c in resp["categories"] if c["id"] == cat)
        if st["status"] != "ok":
            omitted.append((CATEGORY_LABELS[cat], st.get("reason", "")))
            continue

        by_id = {m["id"]: m for m in resp["metrics"]}
        for mid, val in resp["scalars"].items():
            m = by_id.get(mid, {})
            label = m.get("label", mid)
            if isinstance(val, dict):
                for k, v in val.items():
                    scalar_rows.append([CATEGORY_LABELS[cat], f"{label} — {k}",
                                        v, m.get("unit", ""), m.get("formula", "")])
            else:
                scalar_rows.append([CATEGORY_LABELS[cat], label, val,
                                    m.get("unit", ""), m.get("formula", "")])
        if resp["columns"]:
            sheets[CATEGORY_LABELS[cat]] = _data_rows(resp)

    assert first_resp is not None
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xl:
        about = _about_rows(first_resp, scope=scope, project=project,
                            from_iso=from_iso, to_iso=to_iso, period=period,
                            omitted=omitted)
        pd.DataFrame(about, columns=["Field", "Value"]).to_excel(
            xl, sheet_name="About", index=False, header=False)
        pd.DataFrame(scalar_rows[1:], columns=scalar_rows[0]).to_excel(
            xl, sheet_name="Summary", index=False)
        for sheet, (header, rows) in sheets.items():
            pd.DataFrame(rows, columns=header).to_excel(
                xl, sheet_name=sheet[:31], index=False)
    return buf.getvalue()
```

- [ ] **Step 4: Add the route to `routers/asset_results.py`**

Register it BEFORE `/{component_class}/{name}` so the literal `export.xlsx`
segment is not swallowed by the `{name}` path parameter.

```python
@router.get("/{component_class}/{name}/export.xlsx",
            operation_id="export_asset_results_xlsx")
def export_asset_results_xlsx(
    component_class: str,
    name: str,
    scope: str = Query("view"),
    category: str = Query("summary"),
    metrics: str = Query(""),
    source: str = Query("lopf"),
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    period: str | None = Query(None),
    mode: str = Query("chronological"),
):
    from fastapi.responses import Response

    from services.asset_results import export as xls

    if component_class not in ALL_CLASSES:
        raise HTTPException(404, f"Unknown component class '{component_class}'")
    if scope not in xls.SCOPES:
        raise HTTPException(422, f"Unknown scope '{scope}'")
    if category not in CATEGORY_IDS:
        raise HTTPException(422, f"Unknown category '{category}'")

    n = PyPSAService.get_network()
    df = getattr(n, svc.C.attr_for(component_class))
    if name not in df.index:
        raise HTTPException(404, f"No {component_class} named '{name}'")

    metric_ids = [m for m in (metrics.split(",") if metrics else []) if m]
    blob = xls.build_workbook(
        n, component_class, name, scope=scope, category=category,
        metric_ids=metric_ids, source=source, from_iso=from_, to_iso=to,
        period=period, mode=mode, project=PyPSAService.get_loaded_project(),
    )
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)
    fname = f"{safe}_{category}.xlsx"
    return Response(
        content=blob,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd "$(git rev-parse --show-toplevel)" && pixi run gui-tests tests/test_asset_results_export.py -v
cd "$(git rev-parse --show-toplevel)" && pixi run gui-tests tests/ -k asset_results -v
```

Expected: PASS. All five backend test files green.

- [ ] **Step 6: Commit**

```bash
cd "$(git rev-parse --show-toplevel)" && git branch --show-current
git commit pypsa-gui/backend/services/asset_results/export.py \
  pypsa-gui/backend/routers/asset_results.py \
  pypsa-gui/backend/tests/test_asset_results_export.py \
  -m "feat(gui): provenance-stamped xlsx export for per-asset results

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Frontend types, API client and selection memory

No UI yet — just the contract and the localStorage rules, so the components in
Tasks 8–11 have something typed to build against.

**Files:**
- Create: `pypsa-gui/frontend/src/pages/results/asset/types.ts`
- Create: `pypsa-gui/frontend/src/pages/results/asset/api.ts`
- Create: `pypsa-gui/frontend/src/pages/results/asset/selectionMemory.ts`
- Test: `pypsa-gui/frontend/src/pages/results/asset/selectionMemory.test.ts`

**Interfaces:**
- Consumes: the response contract from Task 5.
- Produces:
  - `types.ts`: `MetricStatus`, `Remedy`, `CategoryStatus`, `MetricRow`, `ColumnSpec`, `AssetRef`, `AssetResultsResponse`, `ViewMode`, `CATEGORY_ORDER`
  - `api.ts`: `assetResultsApi.listAssets()`, `assetResultsApi.get(params)`, `assetResultsApi.exportXlsxUrl(params)`
  - `selectionMemory.ts`: `loadSelection(cls, category)`, `saveSelection(cls, category, ids)`, `reconcileSelection(remembered, metrics)`

- [ ] **Step 1: Write the failing test**

Create `selectionMemory.test.ts`:

```ts
import { beforeEach, describe, expect, it } from 'vitest'
import { loadSelection, reconcileSelection, saveSelection } from './selectionMemory'
import type { MetricRow } from './types'

const m = (id: string, status: MetricRow['status'], kind: MetricRow['kind'] = 'series'): MetricRow =>
  ({ id, label: id, unit: 'MW', kind, origin: 'output', status })

describe('selectionMemory', () => {
  beforeEach(() => localStorage.clear())

  it('round-trips a tick-set per class and category', () => {
    saveSelection('Generator', 'dispatch', ['p', 'curtailment'])
    expect(loadSelection('Generator', 'dispatch')).toEqual(['p', 'curtailment'])
  })

  it('keeps classes independent', () => {
    saveSelection('Generator', 'dispatch', ['p'])
    saveSelection('Line', 'loadflow', ['p0'])
    expect(loadSelection('Generator', 'dispatch')).toEqual(['p'])
    expect(loadSelection('Line', 'loadflow')).toEqual(['p0'])
  })

  it('returns null when nothing was ever saved, so callers can fall back to a default', () => {
    expect(loadSelection('Store', 'storage')).toBeNull()
  })

  it('survives corrupt storage without throwing', () => {
    localStorage.setItem('assetDetail:metrics:Generator:dispatch', '{not json')
    expect(loadSelection('Generator', 'dispatch')).toBeNull()
  })

  it('drops remembered metrics that are no longer ok', () => {
    const metrics = [m('p', 'ok'), m('status', 'blocked'), m('losses', 'na')]
    expect(reconcileSelection(['p', 'status', 'losses'], metrics)).toEqual(['p'])
  })

  it('drops remembered ids that no longer exist at all', () => {
    expect(reconcileSelection(['p', 'gone'], [m('p', 'ok')])).toEqual(['p'])
  })

  it('falls back to the first two ok series when nothing is remembered', () => {
    const metrics = [m('p', 'ok'), m('curtailment', 'ok'), m('mu_upper', 'ok'),
                     m('energy_mwh', 'ok', 'scalar')]
    expect(reconcileSelection(null, metrics)).toEqual(['p', 'curtailment', 'energy_mwh'])
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd pypsa-gui/frontend && npx vitest run src/pages/results/asset/selectionMemory.test.ts
```

Expected: FAIL — cannot resolve `./selectionMemory`.

- [ ] **Step 3: Write `types.ts`**

```ts
// Response contract for GET /api/results/asset/{class}/{name}.
// Mirrors services/asset_results/service.py::build_response. The frontend
// holds NO metric knowledge — labels, units, formulas and applicability all
// arrive from the backend registry.

export type MetricStatus = 'ok' | 'blocked' | 'na'
export type ViewMode = 'chronological' | 'duration' | 'monthly'

export interface Remedy {
  /** Closed set — see applicability.VALID_ACTIONS. */
  action: 'run_simulation' | 'run_ac_pf' | 'open_properties'
  label: string
}

export interface CategoryStatus {
  id: string
  label: string
  status: MetricStatus
  reason?: string
  remedy?: Remedy
}

export interface MetricRow {
  id: string
  label: string
  unit: string
  kind: 'series' | 'scalar'
  origin: 'output' | 'input' | 'derived'
  status: MetricStatus
  reason?: string
  remedy?: Remedy
  formula?: string
}

export interface ColumnSpec {
  id: string
  label: string
  unit: string
  metric_id: string
  agg: 'mean' | 'max' | 'energy' | null
}

export interface AssetRef {
  class: string
  name: string
  carrier: string
  bus: string
}

export interface AssetResultsResponse {
  asset: AssetRef & { params: Record<string, unknown> }
  solve: {
    source: 'lopf' | 'ac_pf'
    objective: number | null
    solve_time: number | null
    condition: string | null
  }
  category: string
  mode: ViewMode
  categories: CategoryStatus[]
  metrics: MetricRow[]
  scalars: Record<string, number | string | null | Record<string, number | null>>
  index: string[]
  periods: Array<number | string> | null
  pct_of_hours: number[] | null
  columns: ColumnSpec[]
  series: Record<string, Array<number | null>>
}

/** Display order of the category strip. Must match registry.CATEGORIES. */
export const CATEGORY_ORDER = [
  'summary', 'capacity', 'dispatch', 'storage',
  'loadflow', 'prices', 'economics', 'emissions',
] as const
```

- [ ] **Step 4: Write `api.ts`**

The axios instance is `api/client.ts`'s DEFAULT export named `client` — there is
no named `api` export, so import it as a default.

```ts
import client from '../../../api/client'
import type { AssetRef, AssetResultsResponse, ViewMode } from './types'

export interface AssetQueryParams {
  componentClass: string
  name: string
  category: string
  metrics: string[]
  source: 'lopf' | 'ac_pf'
  fromIso: string | null
  toIso: string | null
  period: number | string | null
  mode: ViewMode
}

function query(p: AssetQueryParams, extra: Record<string, string> = {}) {
  const q: Record<string, string> = {
    category: p.category,
    metrics: p.metrics.join(','),
    source: p.source,
    mode: p.mode,
    ...extra,
  }
  if (p.fromIso) q.from = p.fromIso
  if (p.toIso) q.to = p.toIso
  if (p.period != null) q.period = String(p.period)
  return q
}

const path = (p: AssetQueryParams) =>
  `/results/asset/${encodeURIComponent(p.componentClass)}/${encodeURIComponent(p.name)}`

export const assetResultsApi = {
  async listAssets(): Promise<AssetRef[]> {
    const { data } = await client.get('/results/asset/assets')
    return data.assets ?? []
  },

  async get(p: AssetQueryParams): Promise<AssetResultsResponse> {
    const { data } = await client.get(path(p), { params: query(p) })
    return data
  },

  /** Absolute URL for the workbook — used as an <a download> href so the
   *  browser streams it straight to disk without buffering in JS. */
  exportXlsxUrl(p: AssetQueryParams, scope: 'view' | 'full'): string {
    const qs = new URLSearchParams(query(p, { scope })).toString()
    return `${client.defaults.baseURL ?? ''}${path(p)}/export.xlsx?${qs}`
  },
}
```

- [ ] **Step 5: Write `selectionMemory.ts`**

```ts
import type { MetricRow } from './types'

const key = (cls: string, category: string) => `assetDetail:metrics:${cls}:${category}`

/** Remembered tick-set, or null when this (class, category) has never been
 *  configured — callers fall back to a computed default. */
export function loadSelection(cls: string, category: string): string[] | null {
  try {
    const raw = localStorage.getItem(key(cls, category))
    if (!raw) return null
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) && parsed.every(x => typeof x === 'string')
      ? parsed : null
  } catch { return null }
}

export function saveSelection(cls: string, category: string, ids: string[]): void {
  try { localStorage.setItem(key(cls, category), JSON.stringify(ids)) }
  catch { /* quota or private mode — the tab still works, it just forgets */ }
}

/**
 * Reconcile a remembered tick-set against the metrics the backend actually
 * resolved for THIS asset.
 *
 * Remembered ids that are gone, blocked or n/a are dropped silently — their
 * reason is already visible in the checklist, so a toast would be noise.
 * With nothing remembered, default to the first two `ok` series plus the
 * first `ok` scalar: enough to show something useful, few enough to read.
 */
export function reconcileSelection(
  remembered: string[] | null,
  metrics: MetricRow[],
): string[] {
  const ok = new Set(metrics.filter(m => m.status === 'ok').map(m => m.id))
  if (remembered) return remembered.filter(id => ok.has(id))
  const series = metrics.filter(m => m.status === 'ok' && m.kind === 'series')
  const scalars = metrics.filter(m => m.status === 'ok' && m.kind === 'scalar')
  return [...series.slice(0, 2), ...scalars.slice(0, 1)].map(m => m.id)
}
```

- [ ] **Step 6: Run the test to verify it passes**

```bash
cd pypsa-gui/frontend && npx vitest run src/pages/results/asset/selectionMemory.test.ts
cd pypsa-gui/frontend && PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx tsc --noEmit -p tsconfig.json
```

Expected: 7 tests PASS, typecheck clean.

- [ ] **Step 7: Commit**

```bash
cd "$(git rev-parse --show-toplevel)" && git branch --show-current
git commit pypsa-gui/frontend/src/pages/results/asset/ \
  -m "feat(gui): asset-results types, api client and selection memory

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: MetricChecklist — two zones, three states

**Files:**
- Create: `pypsa-gui/frontend/src/pages/results/asset/MetricChecklist.tsx`
- Test: `pypsa-gui/frontend/src/pages/results/asset/MetricChecklist.test.tsx`

**Interfaces:**
- Consumes: `MetricRow`, `Remedy` from Task 7.
- Produces: `MetricChecklist` default export with props
  `{ metrics: MetricRow[]; selected: string[]; onToggle(id: string): void; onRemedy(r: Remedy): void }`.

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import MetricChecklist from './MetricChecklist'
import type { MetricRow } from './types'

const METRICS: MetricRow[] = [
  { id: 'p', label: 'Active power', unit: 'MW', kind: 'series', origin: 'output', status: 'ok' },
  { id: 'energy_mwh', label: 'Energy', unit: 'MWh', kind: 'scalar', origin: 'derived',
    status: 'ok', formula: 'Σ p × w' },
  { id: 'status', label: 'Committed', unit: '', kind: 'series', origin: 'output',
    status: 'blocked', reason: 'unit commitment is not enabled on Gas 1',
    remedy: { action: 'open_properties', label: 'Enable committable' } },
  { id: 'loading', label: 'Loading', unit: '%', kind: 'series', origin: 'derived',
    status: 'na', reason: 'Generator is not a branch component', formula: '|p0| ÷ s_nom_opt' },
]

const setup = (over = {}) => {
  const onToggle = vi.fn(); const onRemedy = vi.fn()
  render(<MetricChecklist metrics={METRICS} selected={['p']}
    onToggle={onToggle} onRemedy={onRemedy} {...over} />)
  return { onToggle, onRemedy }
}

describe('MetricChecklist', () => {
  it('splits scalars and series into two labelled zones', () => {
    setup()
    expect(screen.getByText(/summary values/i)).toBeTruthy()
    expect(screen.getByText(/time series/i)).toBeTruthy()
  })

  it('ticks an ok metric and reports the toggle', async () => {
    const { onToggle } = setup()
    await userEvent.click(screen.getByRole('checkbox', { name: /Energy/ }))
    expect(onToggle).toHaveBeenCalledWith('energy_mwh')
  })

  it('disables blocked and na metrics', () => {
    setup()
    expect(screen.getByRole('checkbox', { name: /Committed/ })).toHaveProperty('disabled', true)
    expect(screen.getByRole('checkbox', { name: /Loading/ })).toHaveProperty('disabled', true)
  })

  it('ignores a click on a blocked metric', async () => {
    const { onToggle } = setup()
    await userEvent.click(screen.getByRole('checkbox', { name: /Committed/ }))
    expect(onToggle).not.toHaveBeenCalled()
  })

  it('shows the reason for both blocked and na', () => {
    setup()
    expect(screen.getByText(/unit commitment is not enabled/i)).toBeTruthy()
    expect(screen.getByText(/not a branch component/i)).toBeTruthy()
  })

  it('offers a remedy for blocked but never for na', () => {
    const { } = setup()
    expect(screen.getByRole('button', { name: /Enable committable/ })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /branch/ })).toBeNull()
  })

  it('fires the remedy handler with the action', async () => {
    const { onRemedy } = setup()
    await userEvent.click(screen.getByRole('button', { name: /Enable committable/ }))
    expect(onRemedy).toHaveBeenCalledWith(
      { action: 'open_properties', label: 'Enable committable' })
  })

  it('marks input- and derived-origin metrics so they are not mistaken for results', () => {
    setup()
    expect(screen.getByTitle(/Σ p × w/)).toBeTruthy()
  })
})
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd pypsa-gui/frontend && npx vitest run src/pages/results/asset/MetricChecklist.test.tsx
```

Expected: FAIL — cannot resolve `./MetricChecklist`.

- [ ] **Step 3: Implement `MetricChecklist.tsx`**

```tsx
import { Ban, CircleAlert, Sigma } from 'lucide-react'
import type { MetricRow, Remedy } from './types'

interface Props {
  metrics: MetricRow[]
  selected: string[]
  onToggle: (id: string) => void
  onRemedy: (remedy: Remedy) => void
}

// Three visual states, deliberately distinct. `blocked` means "you can fix
// this"; `na` means "this can never apply here". Collapsing them into one
// grey would leave the user unable to tell whether running AC PF would light
// half the strip up.
function Row({ m, checked, onToggle, onRemedy }: {
  m: MetricRow; checked: boolean
  onToggle: (id: string) => void; onRemedy: (r: Remedy) => void
}) {
  const disabled = m.status !== 'ok'
  return (
    <li className="flex flex-col gap-0.5 py-0.5">
      <label className={`flex items-center gap-1.5 text-[11px] ${disabled ? 'text-muted' : 'text-text'}`}>
        <input
          type="checkbox"
          aria-label={m.label}
          checked={checked}
          disabled={disabled}
          onChange={() => { if (!disabled) onToggle(m.id) }}
          className="accent-accent disabled:opacity-40"
        />
        <span className={disabled ? 'line-through decoration-border' : ''}>{m.label}</span>
        {m.unit && <span className="text-[10px] text-muted font-mono">{m.unit}</span>}
        {m.origin === 'input' && (
          <span title="Model input, not a solver result"
            className="text-[9px] uppercase tracking-wide text-muted border border-border rounded px-1">in</span>
        )}
        {m.origin === 'derived' && m.formula && (
          <span title={m.formula} className="text-muted"><Sigma size={10} /></span>
        )}
        {m.status === 'blocked' && <CircleAlert size={11} className="text-warn" />}
        {m.status === 'na' && <Ban size={11} className="text-muted" />}
      </label>
      {disabled && m.reason && (
        <span className="pl-5 text-[10px] text-muted flex items-center gap-1.5">
          {m.reason}
          {m.status === 'blocked' && m.remedy && (
            <button
              onClick={() => onRemedy(m.remedy!)}
              className="text-accent hover:underline"
            >{m.remedy.label} →</button>
          )}
        </span>
      )}
    </li>
  )
}

export default function MetricChecklist({ metrics, selected, onToggle, onRemedy }: Props) {
  const sel = new Set(selected)
  const scalars = metrics.filter(m => m.kind === 'scalar')
  const series = metrics.filter(m => m.kind === 'series')
  const zone = (title: string, rows: MetricRow[]) => rows.length === 0 ? null : (
    <div className="mb-2">
      <div className="text-[9px] uppercase tracking-wider text-muted mb-1">{title}</div>
      <ul className="flex flex-col">
        {rows.map(m => (
          <Row key={m.id} m={m} checked={sel.has(m.id)}
            onToggle={onToggle} onRemedy={onRemedy} />
        ))}
      </ul>
    </div>
  )
  return (
    <div className="px-2 py-2 border-b border-border">
      {zone('Summary values', scalars)}
      {zone('Time series', series)}
    </div>
  )
}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd pypsa-gui/frontend && npx vitest run src/pages/results/asset/MetricChecklist.test.tsx
```

Expected: PASS, 8 tests.

- [ ] **Step 5: Commit**

```bash
cd "$(git rev-parse --show-toplevel)" && git branch --show-current
git commit pypsa-gui/frontend/src/pages/results/asset/MetricChecklist.tsx \
  pypsa-gui/frontend/src/pages/results/asset/MetricChecklist.test.tsx \
  -m "feat(gui): metric checklist with two zones and three applicability states

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: AssetPicker — searchable, class-grouped, virtualised

**Files:**
- Create: `pypsa-gui/frontend/src/pages/results/asset/AssetPicker.tsx`
- Test: `pypsa-gui/frontend/src/pages/results/asset/AssetPicker.test.tsx`

**Interfaces:**
- Consumes: `AssetRef` from Task 7.
- Produces: `AssetPicker` default export, props
  `{ assets: AssetRef[]; selected: AssetRef | null; onSelect(a: AssetRef): void }`;
  plus a named export `filterAssets(assets: AssetRef[], query: string): AssetRef[]`
  and `groupByClass(assets: AssetRef[]): Array<[string, AssetRef[]]>`, both pure
  so they can be tested without rendering.

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import AssetPicker, { filterAssets, groupByClass } from './AssetPicker'
import type { AssetRef } from './types'

const A = (cls: string, name: string, carrier = ''): AssetRef =>
  ({ class: cls, name, carrier, bus: 'B1' })

const ASSETS = [
  A('Generator', 'Gas 1', 'gas'), A('Generator', 'Gas 2', 'gas'),
  A('Generator', 'Wind 1', 'onwind'), A('Line', 'L1'), A('Bus', 'B1'),
]

describe('filterAssets', () => {
  it('matches a case-insensitive substring of the name', () => {
    expect(filterAssets(ASSETS, 'gas').map(a => a.name)).toEqual(['Gas 1', 'Gas 2'])
  })
  it('also matches the carrier, so "onwind" finds the turbine', () => {
    expect(filterAssets(ASSETS, 'onwind').map(a => a.name)).toEqual(['Wind 1'])
  })
  it('returns everything for an empty query', () => {
    expect(filterAssets(ASSETS, '   ')).toHaveLength(5)
  })
})

describe('groupByClass', () => {
  it('groups in the canonical class order, skipping empty classes', () => {
    expect(groupByClass(ASSETS).map(([c, rows]) => [c, rows.length]))
      .toEqual([['Bus', 1], ['Generator', 3], ['Line', 1]])
  })
})

describe('AssetPicker', () => {
  it('reports the clicked asset', async () => {
    const onSelect = vi.fn()
    render(<AssetPicker assets={ASSETS} selected={null} onSelect={onSelect} />)
    await userEvent.click(screen.getByRole('button', { name: /Gas 2/ }))
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ name: 'Gas 2' }))
  })

  it('narrows the list as the user types', async () => {
    render(<AssetPicker assets={ASSETS} selected={null} onSelect={vi.fn()} />)
    await userEvent.type(screen.getByRole('searchbox'), 'wind')
    expect(screen.queryByRole('button', { name: /Gas 1/ })).toBeNull()
    expect(screen.getByRole('button', { name: /Wind 1/ })).toBeTruthy()
  })

  it('selects the first match on Enter', async () => {
    const onSelect = vi.fn()
    render(<AssetPicker assets={ASSETS} selected={null} onSelect={onSelect} />)
    await userEvent.type(screen.getByRole('searchbox'), 'gas{Enter}')
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ name: 'Gas 1' }))
  })

  it('marks the selected row as current', () => {
    render(<AssetPicker assets={ASSETS} selected={ASSETS[0]} onSelect={vi.fn()} />)
    expect(screen.getByRole('button', { name: /Gas 1/ }))
      .toHaveProperty('ariaCurrent', 'true')
  })

  it('shows an empty state when nothing matches', async () => {
    render(<AssetPicker assets={ASSETS} selected={null} onSelect={vi.fn()} />)
    await userEvent.type(screen.getByRole('searchbox'), 'zzzz')
    expect(screen.getByText(/no assets match/i)).toBeTruthy()
  })
})
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd pypsa-gui/frontend && npx vitest run src/pages/results/asset/AssetPicker.test.tsx
```

- [ ] **Step 3: Implement `AssetPicker.tsx`**

```tsx
import { useMemo, useRef, useState } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { Search } from 'lucide-react'
import type { AssetRef } from './types'

// Canonical display order — mirrors registry.ALL_CLASSES.
const CLASS_ORDER = ['Bus', 'Generator', 'Load', 'Line', 'Transformer',
  'Link', 'StorageUnit', 'Store'] as const

export function filterAssets(assets: AssetRef[], query: string): AssetRef[] {
  const q = query.trim().toLowerCase()
  if (!q) return assets
  return assets.filter(a =>
    a.name.toLowerCase().includes(q) || a.carrier.toLowerCase().includes(q))
}

export function groupByClass(assets: AssetRef[]): Array<[string, AssetRef[]]> {
  return CLASS_ORDER
    .map(cls => [cls, assets.filter(a => a.class === cls)] as [string, AssetRef[]])
    .filter(([, rows]) => rows.length > 0)
}

/** Flatten groups into a single virtualisable row list: headers + assets. */
type Row = { kind: 'header'; label: string } | { kind: 'asset'; asset: AssetRef }

export default function AssetPicker(
  { assets, selected, onSelect }: {
    assets: AssetRef[]; selected: AssetRef | null; onSelect: (a: AssetRef) => void
  },
) {
  const [query, setQuery] = useState('')
  const scrollRef = useRef<HTMLDivElement>(null)

  const filtered = useMemo(() => filterAssets(assets, query), [assets, query])
  const rows = useMemo<Row[]>(() => {
    const out: Row[] = []
    for (const [cls, group] of groupByClass(filtered)) {
      out.push({ kind: 'header', label: `${cls} (${group.length})` })
      for (const asset of group) out.push({ kind: 'asset', asset })
    }
    return out
  }, [filtered])

  // A 5 000-asset network must not render 5 000 DOM nodes.
  const virt = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 24,
    overscan: 12,
  })

  return (
    <div className="flex flex-col h-full min-h-0 border-r border-border bg-panel">
      <div className="shrink-0 p-2 border-b border-border">
        <div className="flex items-center gap-1.5 px-2 h-7 border border-border rounded bg-bg">
          <Search size={12} className="text-muted" />
          <input
            type="search"
            role="searchbox"
            aria-label="Search assets"
            value={query}
            placeholder="Search assets…"
            onChange={e => setQuery(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter') {
                const first = rows.find(r => r.kind === 'asset')
                if (first && first.kind === 'asset') onSelect(first.asset)
              } else if (e.key === 'Escape') {
                setQuery('')
                ;(e.target as HTMLInputElement).blur()
              }
            }}
            className="flex-1 bg-transparent text-[11px] outline-none"
          />
        </div>
      </div>

      <div ref={scrollRef} className="flex-1 min-h-0 overflow-y-auto">
        {rows.length === 0 ? (
          <p className="p-3 text-[11px] text-muted">No assets match “{query}”.</p>
        ) : (
          <div style={{ height: virt.getTotalSize(), position: 'relative' }}>
            {virt.getVirtualItems().map(v => {
              const row = rows[v.index]
              const style: React.CSSProperties = {
                position: 'absolute', top: 0, left: 0, width: '100%',
                height: v.size, transform: `translateY(${v.start}px)`,
              }
              if (row.kind === 'header') {
                return (
                  <div key={v.key} style={style}
                    className="px-2 flex items-center text-[9px] uppercase tracking-wider text-muted bg-panel">
                    {row.label}
                  </div>
                )
              }
              const a = row.asset
              const active = selected?.class === a.class && selected?.name === a.name
              return (
                <button
                  key={v.key} style={style}
                  aria-current={active ? 'true' : undefined}
                  onClick={() => onSelect(a)}
                  title={a.carrier ? `${a.name} · ${a.carrier}` : a.name}
                  className={`px-2 pl-4 flex items-center gap-1.5 text-left text-[11px] truncate
                    ${active ? 'bg-accent/15 text-accent' : 'text-text hover:bg-border/40'}`}
                >
                  <span className="truncate">{a.name}</span>
                  {a.carrier && (
                    <span className="text-[9px] text-muted truncate">{a.carrier}</span>
                  )}
                </button>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Run the tests to verify they pass**

`jsdom` gives every element zero height, so the virtualiser would render no
rows. Add this to the top of the test file, above the imports of the component:

```tsx
// jsdom reports 0 for every measured box; give the virtualiser a real viewport.
beforeAll(() => {
  Object.defineProperty(HTMLElement.prototype, 'offsetHeight',
    { configurable: true, value: 600 })
  Object.defineProperty(HTMLElement.prototype, 'getBoundingClientRect',
    { configurable: true, value: () => ({ height: 600, width: 240, top: 0, left: 0,
      right: 240, bottom: 600, x: 0, y: 0, toJSON: () => ({}) }) })
})
```

```bash
cd pypsa-gui/frontend && npx vitest run src/pages/results/asset/AssetPicker.test.tsx
```

Expected: PASS, 9 tests.

- [ ] **Step 5: Commit**

```bash
cd "$(git rev-parse --show-toplevel)" && git branch --show-current
git commit pypsa-gui/frontend/src/pages/results/asset/AssetPicker.tsx \
  pypsa-gui/frontend/src/pages/results/asset/AssetPicker.test.tsx \
  -m "feat(gui): virtualised, class-grouped asset picker

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: AssetTable — virtualised, view-mode aware

**Files:**
- Create: `pypsa-gui/frontend/src/pages/results/asset/AssetTable.tsx`
- Test: `pypsa-gui/frontend/src/pages/results/asset/AssetTable.test.tsx`

**Interfaces:**
- Consumes: `AssetResultsResponse`, `ColumnSpec` from Task 7.
- Produces: `AssetTable` default export, props `{ data: AssetResultsResponse }`;
  named export `tableRows(data: AssetResultsResponse): { header: string[]; rows: unknown[][] }`
  — pure, and reused verbatim by the CSV export in Task 12 so the file and the
  screen can never disagree.

- [ ] **Step 1: Write the failing test**

```tsx
import { describe, expect, it } from 'vitest'
import { tableRows } from './AssetTable'
import type { AssetResultsResponse } from './types'

const base = (over: Partial<AssetResultsResponse>): AssetResultsResponse => ({
  asset: { class: 'Generator', name: 'Gas 1', carrier: 'gas', bus: 'B1', params: {} },
  solve: { source: 'lopf', objective: 1, solve_time: 1, condition: 'optimal' },
  category: 'dispatch', mode: 'chronological', categories: [], metrics: [],
  scalars: {}, index: [], periods: null, pct_of_hours: null, columns: [], series: {},
  ...over,
})

describe('tableRows', () => {
  it('puts snapshot first in chronological mode', () => {
    const { header, rows } = tableRows(base({
      index: ['2026-01-01T00:00:00', '2026-01-01T01:00:00'],
      columns: [{ id: 'p', label: 'Active power', unit: 'MW', metric_id: 'p', agg: null }],
      series: { p: [120, 135] },
    }))
    expect(header).toEqual(['snapshot', 'Active power (MW)'])
    expect(rows).toEqual([
      ['2026-01-01T00:00:00', 120], ['2026-01-01T01:00:00', 135],
    ])
  })

  it('adds a period column only when the response carries periods', () => {
    const { header } = tableRows(base({
      index: ['a'], periods: [2026],
      columns: [{ id: 'p', label: 'p', unit: 'MW', metric_id: 'p', agg: null }],
      series: { p: [1] },
    }))
    expect(header).toEqual(['snapshot', 'period', 'p (MW)'])
  })

  it('uses rank and pct_of_hours in duration mode', () => {
    const { header, rows } = tableRows(base({
      mode: 'duration', index: ['1', '2'], pct_of_hours: [0.5, 1],
      columns: [{ id: 'p', label: 'p', unit: 'MW', metric_id: 'p', agg: null }],
      series: { p: [135, 120] },
    }))
    expect(header).toEqual(['rank', 'pct_of_hours', 'p (MW)'])
    expect(rows[0]).toEqual(['1', 0.5, 135])
  })

  it('uses month in monthly mode and keeps the aggregated column labels', () => {
    const { header } = tableRows(base({
      mode: 'monthly', index: ['2026-01'],
      columns: [
        { id: 'p__mean', label: 'Active power (mean)', unit: 'MW', metric_id: 'p', agg: 'mean' },
        { id: 'p__energy', label: 'Active power (energy)', unit: 'MWh', metric_id: 'p', agg: 'energy' },
      ],
      series: { p__mean: [58], p__energy: [512] },
    }))
    expect(header).toEqual(['month', 'Active power (mean) (MW)', 'Active power (energy) (MWh)'])
  })

  it('renders a missing value as an empty cell rather than NaN', () => {
    const { rows } = tableRows(base({
      index: ['a'],
      columns: [{ id: 'p', label: 'p', unit: 'MW', metric_id: 'p', agg: null }],
      series: { p: [null] },
    }))
    expect(rows[0][1]).toBeNull()
  })
})
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd pypsa-gui/frontend && npx vitest run src/pages/results/asset/AssetTable.test.tsx
```

- [ ] **Step 3: Implement `AssetTable.tsx`**

```tsx
import { useRef } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import type { AssetResultsResponse } from './types'

const head = (label: string, unit: string) => unit ? `${label} (${unit})` : label

/**
 * The exact rows the table renders — also the rows the CSV export writes.
 * Pure, so the file and the screen cannot drift.
 */
export function tableRows(data: AssetResultsResponse): {
  header: string[]; rows: unknown[][]
} {
  const cols = data.columns
  if (data.mode === 'duration') {
    return {
      header: ['rank', 'pct_of_hours', ...cols.map(c => head(c.label, c.unit))],
      rows: data.index.map((rank, i) => [
        rank, data.pct_of_hours?.[i] ?? null,
        ...cols.map(c => data.series[c.id]?.[i] ?? null),
      ]),
    }
  }
  const first = data.mode === 'monthly' ? 'month' : 'snapshot'
  const withPeriod = data.mode === 'chronological' && !!data.periods
  return {
    header: [first, ...(withPeriod ? ['period'] : []),
             ...cols.map(c => head(c.label, c.unit))],
    rows: data.index.map((stamp, i) => [
      stamp,
      ...(withPeriod ? [data.periods![i]] : []),
      ...cols.map(c => data.series[c.id]?.[i] ?? null),
    ]),
  }
}

const fmt = (v: unknown) =>
  v === null || v === undefined ? '' :
  typeof v === 'number' ? (Number.isInteger(v) ? String(v) : v.toFixed(3)) : String(v)

export default function AssetTable({ data }: { data: AssetResultsResponse }) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const { header, rows } = tableRows(data)
  const virt = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 22,
    overscan: 20,
  })

  if (header.length <= 1) {
    return (
      <p className="p-4 text-[11px] text-muted">
        Tick a time series on the left to populate the table.
      </p>
    )
  }

  return (
    <div ref={scrollRef} className="flex-1 min-h-0 overflow-auto">
      <table className="w-full text-[11px] font-mono border-collapse">
        <thead className="sticky top-0 bg-panel z-10">
          <tr>
            {header.map(h => (
              <th key={h}
                className="text-left px-2 py-1 border-b border-border font-medium whitespace-nowrap">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody style={{ height: virt.getTotalSize(), position: 'relative' }}>
          {virt.getVirtualItems().map(v => (
            <tr key={v.key}
              style={{ position: 'absolute', top: 0, left: 0, width: '100%',
                       height: v.size, transform: `translateY(${v.start}px)`,
                       display: 'table', tableLayout: 'fixed' }}
              className="border-b border-border/40">
              {rows[v.index].map((cell, ci) => (
                <td key={ci} className="px-2 py-0.5 tabular-nums whitespace-nowrap">
                  {fmt(cell)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd pypsa-gui/frontend && npx vitest run src/pages/results/asset/AssetTable.test.tsx
```

Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
cd "$(git rev-parse --show-toplevel)" && git branch --show-current
git commit pypsa-gui/frontend/src/pages/results/asset/AssetTable.tsx \
  pypsa-gui/frontend/src/pages/results/asset/AssetTable.test.tsx \
  -m "feat(gui): virtualised, view-mode-aware asset results table

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: AssetCharts (one chart per unit) + PNG export

**Files:**
- Create: `pypsa-gui/frontend/src/pages/results/asset/AssetCharts.tsx`
- Create: `pypsa-gui/frontend/src/pages/results/asset/exportPng.ts`
- Test: `pypsa-gui/frontend/src/pages/results/asset/AssetCharts.test.tsx`
- Test: `pypsa-gui/frontend/src/pages/results/asset/exportPng.test.ts`

**Interfaces:**
- Consumes: `AssetResultsResponse`, `ColumnSpec`; `colourForCarrier` and `CHART_GRID` / `CHART_AXIS` / `CHART_TOOLTIP` from `../shared`.
- Produces:
  - `AssetCharts` default export, props `{ data: AssetResultsResponse }`
  - named export `groupColumnsByUnit(columns: ColumnSpec[]): Array<{ unit: string; columns: ColumnSpec[] }>`
  - `exportPng.ts`: `downloadPNG(container: HTMLElement | null, filename: string, scale?: number): Promise<boolean>`

- [ ] **Step 1: Write the failing tests**

`AssetCharts.test.tsx`:

```tsx
import { describe, expect, it } from 'vitest'
import { groupColumnsByUnit } from './AssetCharts'
import type { ColumnSpec } from './types'

const c = (id: string, unit: string): ColumnSpec =>
  ({ id, label: id, unit, metric_id: id, agg: null })

describe('groupColumnsByUnit', () => {
  it('keeps one group when every series shares a unit', () => {
    const g = groupColumnsByUnit([c('p', 'MW'), c('curtailment', 'MW')])
    expect(g).toHaveLength(1)
    expect(g[0].unit).toBe('MW')
    expect(g[0].columns.map(x => x.id)).toEqual(['p', 'curtailment'])
  })

  it('splits into one group per distinct unit', () => {
    const g = groupColumnsByUnit([c('p', 'MW'), c('mu', 'EUR/MWh'), c('cf', 'pu')])
    expect(g.map(x => x.unit)).toEqual(['MW', 'EUR/MWh', 'pu'])
  })

  it('preserves first-seen unit order so the layout is stable across renders', () => {
    const g = groupColumnsByUnit([c('mu', 'EUR/MWh'), c('p', 'MW'), c('mu2', 'EUR/MWh')])
    expect(g.map(x => x.unit)).toEqual(['EUR/MWh', 'MW'])
    expect(g[0].columns.map(x => x.id)).toEqual(['mu', 'mu2'])
  })

  it('groups unitless series together under a dash', () => {
    const g = groupColumnsByUnit([c('status', ''), c('start_up', '')])
    expect(g).toHaveLength(1)
    expect(g[0].unit).toBe('–')
  })

  it('returns nothing for no columns', () => {
    expect(groupColumnsByUnit([])).toEqual([])
  })
})
```

`exportPng.test.ts`:

```ts
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { downloadPNG } from './exportPng'

function mountSvg(): HTMLElement {
  const host = document.createElement('div')
  host.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50"></svg>'
  document.body.appendChild(host)
  return host
}

// A 1×1 PNG. Magic bytes 89 50 4e 47 are what the assertion checks for.
const PNG_BYTES = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])

beforeEach(() => {
  vi.stubGlobal('URL', { ...URL, createObjectURL: () => 'blob:x', revokeObjectURL: () => {} })
  HTMLCanvasElement.prototype.getContext = vi.fn(() => ({ drawImage: vi.fn() })) as never
  HTMLCanvasElement.prototype.toBlob = function (cb: BlobCallback) {
    cb(new Blob([PNG_BYTES], { type: 'image/png' }))
  } as never
  // jsdom never fires Image.onload; resolve it synchronously.
  Object.defineProperty(global.Image.prototype, 'src', {
    configurable: true,
    set(this: HTMLImageElement) { setTimeout(() => this.onload?.(new Event('load')), 0) },
  })
})

describe('downloadPNG', () => {
  it('returns false when the container is null', async () => {
    expect(await downloadPNG(null, 'x.png')).toBe(false)
  })

  it('returns false when the container holds no svg', async () => {
    expect(await downloadPNG(document.createElement('div'), 'x.png')).toBe(false)
  })

  it('produces a PNG blob with the right magic bytes', async () => {
    let captured: Blob | null = null
    vi.stubGlobal('URL', {
      ...URL,
      createObjectURL: (b: Blob) => { captured = b; return 'blob:x' },
      revokeObjectURL: () => {},
    })
    expect(await downloadPNG(mountSvg(), 'chart.png')).toBe(true)
    const bytes = new Uint8Array(await captured!.arrayBuffer())
    expect([...bytes.slice(0, 4)]).toEqual([0x89, 0x50, 0x4e, 0x47])
  })

  it('scales the canvas so the file is not a blurry screenshot', async () => {
    const created: HTMLCanvasElement[] = []
    const realCreate = document.createElement.bind(document)
    vi.spyOn(document, 'createElement').mockImplementation((tag: string) => {
      const el = realCreate(tag)
      if (tag === 'canvas') created.push(el as HTMLCanvasElement)
      return el
    })
    await downloadPNG(mountSvg(), 'chart.png', 2)
    expect(created[0].width).toBe(200)
    expect(created[0].height).toBe(100)
  })
})
```

- [ ] **Step 2: Run both to verify they fail**

```bash
cd pypsa-gui/frontend && npx vitest run src/pages/results/asset/AssetCharts.test.tsx src/pages/results/asset/exportPng.test.ts
```

- [ ] **Step 3: Implement `exportPng.ts`**

```ts
/**
 * Rasterise the chart's SVG to a PNG.
 *
 * Recharts styles with presentation attributes rather than CSS, so serialising
 * the SVG and drawing it into a canvas produces a faithful image without
 * inlining computed styles. Charts contain no external images, so the canvas
 * is never tainted and toBlob always succeeds.
 *
 * Deliberately mirrors downloadSVG in ../shared.tsx — same white background
 * rect, same namespace repair, same "return false if the chart has not
 * mounted" contract.
 */
export async function downloadPNG(
  container: HTMLElement | null,
  filename: string,
  scale = 2,
): Promise<boolean> {
  if (!container) return false
  const svg = container.querySelector('svg')
  if (!svg) return false

  const rect = svg.getBoundingClientRect()
  const width = Math.max(1, Math.round(rect.width || svg.clientWidth || 640))
  const height = Math.max(1, Math.round(rect.height || svg.clientHeight || 320))

  const clone = svg.cloneNode(true) as SVGSVGElement
  if (!clone.getAttribute('xmlns')) clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg')
  clone.setAttribute('width', String(width))
  clone.setAttribute('height', String(height))
  if (!clone.querySelector('rect[data-bg]')) {
    const bg = document.createElementNS('http://www.w3.org/2000/svg', 'rect')
    bg.setAttribute('width', '100%')
    bg.setAttribute('height', '100%')
    bg.setAttribute('fill', '#ffffff')
    bg.setAttribute('data-bg', 'true')
    clone.insertBefore(bg, clone.firstChild)
  }

  const source = new XMLSerializer().serializeToString(clone)
  const svgUrl = URL.createObjectURL(new Blob([source], { type: 'image/svg+xml;charset=utf-8' }))

  try {
    const img = new Image()
    await new Promise<void>((resolve, reject) => {
      img.onload = () => resolve()
      img.onerror = () => reject(new Error('svg failed to decode'))
      img.src = svgUrl
    })
    const canvas = document.createElement('canvas')
    canvas.width = width * scale
    canvas.height = height * scale
    const ctx = canvas.getContext('2d')
    if (!ctx) return false
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height)

    const blob = await new Promise<Blob | null>(res => canvas.toBlob(res, 'image/png'))
    if (!blob) return false
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    a.click()
    URL.revokeObjectURL(url)
    return true
  } catch {
    return false
  } finally {
    URL.revokeObjectURL(svgUrl)
  }
}
```

- [ ] **Step 4: Implement `AssetCharts.tsx`**

```tsx
import { useMemo, useRef } from 'react'
import {
  CartesianGrid, Legend, Line, LineChart, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from 'recharts'
import toast from 'react-hot-toast'
import { Download } from 'lucide-react'
import { CHART_AXIS, CHART_GRID, CHART_TOOLTIP, colourForCarrier, downloadSVG }
  from '../shared'
import { downloadPNG } from './exportPng'
import type { AssetResultsResponse, ColumnSpec } from './types'

/**
 * Group the selected series by unit.
 *
 * One unit → one chart, which is the common case (p, available and
 * curtailment are all MW). Three units → three stacked charts sharing the
 * X axis. No dual axes: they invite false visual correlation, and they buy
 * nothing in the single-unit case.
 *
 * First-seen order is preserved so adding a series never reshuffles the
 * layout under the user's cursor.
 */
export function groupColumnsByUnit(columns: ColumnSpec[]):
  Array<{ unit: string; columns: ColumnSpec[] }> {
  const order: string[] = []
  const byUnit = new Map<string, ColumnSpec[]>()
  for (const c of columns) {
    const unit = c.unit || '–'
    if (!byUnit.has(unit)) { byUnit.set(unit, []); order.push(unit) }
    byUnit.get(unit)!.push(c)
  }
  return order.map(unit => ({ unit, columns: byUnit.get(unit)! }))
}

function UnitChart(
  { data, unit, columns, xKey, assetName }: {
    data: Array<Record<string, unknown>>; unit: string
    columns: ColumnSpec[]; xKey: string; assetName: string
  },
) {
  const ref = useRef<HTMLDivElement>(null)
  const base = `${assetName}_${unit.replace(/[^A-Za-z0-9]+/g, '_')}`
  return (
    <div className="mb-3">
      <div className="flex items-center gap-2 px-1 mb-1">
        <span className="text-[10px] uppercase tracking-wider text-muted">{unit}</span>
        <span className="flex-1" />
        <button
          onClick={() => {
            downloadSVG(ref.current, `${base}.svg`)
              ? toast.success('Exported SVG')
              : toast.error('Chart not ready — try again once it renders')
          }}
          className="flex items-center gap-1 text-[11px] text-muted hover:text-accent"
        ><Download size={11} /> SVG</button>
        <button
          onClick={async () => {
            const ok = await downloadPNG(ref.current, `${base}.png`)
            ok ? toast.success('Exported PNG')
               : toast.error('Chart not ready — try again once it renders')
          }}
          className="flex items-center gap-1 text-[11px] text-muted hover:text-accent"
        ><Download size={11} /> PNG</button>
      </div>
      <div ref={ref} style={{ height: 200 }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid {...CHART_GRID} />
            <XAxis dataKey={xKey} {...CHART_AXIS} minTickGap={40} />
            <YAxis {...CHART_AXIS} />
            <Tooltip {...CHART_TOOLTIP} />
            <Legend wrapperStyle={{ fontSize: 10 }} />
            {columns.map((c, i) => (
              <Line key={c.id} type="monotone" dataKey={c.id} name={c.label}
                stroke={colourForCarrier(c.metric_id, i)} dot={false}
                strokeWidth={1.25} isAnimationActive={false} />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

export default function AssetCharts({ data }: { data: AssetResultsResponse }) {
  const groups = useMemo(() => groupColumnsByUnit(data.columns), [data.columns])
  const xKey = data.mode === 'duration' ? 'rank'
    : data.mode === 'monthly' ? 'month' : 'snapshot'

  const rows = useMemo(() => data.index.map((stamp, i) => {
    const row: Record<string, unknown> = { [xKey]: stamp }
    for (const c of data.columns) row[c.id] = data.series[c.id]?.[i] ?? null
    return row
  }), [data, xKey])

  if (groups.length === 0) {
    return (
      <p className="p-4 text-[11px] text-muted">
        Tick a time series on the left to draw it over the horizon.
      </p>
    )
  }
  return (
    <div className="overflow-y-auto">
      {groups.map(g => (
        <UnitChart key={g.unit} unit={g.unit} columns={g.columns}
          data={rows} xKey={xKey} assetName={data.asset.name} />
      ))}
    </div>
  )
}
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd pypsa-gui/frontend && npx vitest run src/pages/results/asset/AssetCharts.test.tsx src/pages/results/asset/exportPng.test.ts
```

Expected: PASS, 9 tests.

- [ ] **Step 6: Commit**

```bash
cd "$(git rev-parse --show-toplevel)" && git branch --show-current
git commit pypsa-gui/frontend/src/pages/results/asset/AssetCharts.tsx \
  pypsa-gui/frontend/src/pages/results/asset/AssetCharts.test.tsx \
  pypsa-gui/frontend/src/pages/results/asset/exportPng.ts \
  pypsa-gui/frontend/src/pages/results/asset/exportPng.test.ts \
  -m "feat(gui): unit-grouped asset charts with SVG and PNG export

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: AssetDetail shell + the eleventh Results tab

**Files:**
- Create: `pypsa-gui/frontend/src/pages/results/asset/AssetDetail.tsx`
- Modify: `pypsa-gui/frontend/src/pages/Results.tsx` (tab id, TABS entry, scrollable strip, `RESULTS_TO_COMPARE_TAB`, body switch)
- Test: `pypsa-gui/frontend/src/pages/results/asset/AssetDetail.test.tsx`

**Interfaces:**
- Consumes: `AssetPicker`, `MetricChecklist`, `AssetTable` (+ `tableRows`), `AssetCharts`, `assetResultsApi`, `loadSelection`/`saveSelection`/`reconcileSelection`, `useResultsFilter` from `../filterContext`, `downloadCSV` from `../shared`, `nk` from `../../../utils/queryKeys`.
- Produces: `AssetDetail` default export (no props — reads filter + project from context/store).

- [ ] **Step 1: Write the failing test**

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import AssetDetail from './AssetDetail'
import { assetResultsApi } from './api'
import type { AssetResultsResponse } from './types'

vi.mock('./api')

const CATEGORIES = [
  { id: 'summary', label: 'Summary', status: 'ok' as const },
  { id: 'capacity', label: 'Capacity', status: 'ok' as const },
  { id: 'dispatch', label: 'Dispatch', status: 'ok' as const },
  { id: 'storage', label: 'Storage', status: 'na' as const,
    reason: 'Generator does not store energy' },
  { id: 'loadflow', label: 'Load flow', status: 'na' as const,
    reason: 'Generator is not a branch or bus component' },
  { id: 'prices', label: 'Prices & duals', status: 'ok' as const },
  { id: 'economics', label: 'Economics', status: 'ok' as const },
  { id: 'emissions', label: 'Emissions', status: 'blocked' as const,
    reason: "carrier 'gas' declares no co2_emissions",
    remedy: { action: 'open_properties' as const, label: 'Set co2_emissions' } },
]

const RESPONSE: AssetResultsResponse = {
  asset: { class: 'Generator', name: 'Gas 1', carrier: 'gas', bus: 'B1',
           params: { p_nom: 200 } },
  solve: { source: 'lopf', objective: 1e9, solve_time: 2, condition: 'optimal' },
  category: 'dispatch', mode: 'chronological', categories: CATEGORIES,
  metrics: [
    { id: 'p', label: 'Active power', unit: 'MW', kind: 'series',
      origin: 'output', status: 'ok' },
    { id: 'energy_mwh', label: 'Energy', unit: 'MWh', kind: 'scalar',
      origin: 'derived', status: 'ok', formula: 'Σ p × w' },
  ],
  scalars: { energy_mwh: 512000 },
  index: ['2026-01-01T00:00:00'], periods: null, pct_of_hours: null,
  columns: [{ id: 'p', label: 'Active power', unit: 'MW', metric_id: 'p', agg: null }],
  series: { p: [120] },
}

const renderIt = () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><AssetDetail /></QueryClientProvider>)
}

beforeEach(() => {
  localStorage.clear()
  vi.mocked(assetResultsApi.listAssets).mockResolvedValue([
    { class: 'Generator', name: 'Gas 1', carrier: 'gas', bus: 'B1' },
    { class: 'Generator', name: 'Wind 1', carrier: 'onwind', bus: 'B1' },
  ])
  vi.mocked(assetResultsApi.get).mockResolvedValue(RESPONSE)
  vi.mocked(assetResultsApi.exportXlsxUrl).mockReturnValue('http://x/export.xlsx')
})

describe('AssetDetail', () => {
  it('auto-selects the first asset and shows its identity', async () => {
    renderIt()
    expect(await screen.findByText(/Gas 1/)).toBeTruthy()
    await waitFor(() => expect(screen.getByText(/carrier/i)).toBeTruthy())
  })

  it('greys out categories the class cannot use and explains why', async () => {
    renderIt()
    const loadflow = await screen.findByRole('tab', { name: /Load flow/ })
    expect(loadflow).toHaveProperty('disabled', true)
    expect(loadflow.getAttribute('title')).toMatch(/not a branch/)
  })

  it('renders a blocked category as disabled but distinct from n/a', async () => {
    renderIt()
    const emissions = await screen.findByRole('tab', { name: /Emissions/ })
    expect(emissions).toHaveProperty('disabled', true)
    expect(emissions.getAttribute('title')).toMatch(/co2_emissions/)
  })

  it('shows selected scalars as KPI cards', async () => {
    renderIt()
    expect(await screen.findByText(/Energy/)).toBeTruthy()
    expect(await screen.findByText(/512000|512,000/)).toBeTruthy()
  })

  it('switches view mode and refetches with the new mode', async () => {
    renderIt()
    await screen.findByRole('tab', { name: /Dispatch/ })
    await userEvent.click(screen.getByRole('button', { name: /Duration/ }))
    await waitFor(() => expect(vi.mocked(assetResultsApi.get)).toHaveBeenCalledWith(
      expect.objectContaining({ mode: 'duration' })))
  })

  it('remembers the tick-set per class across asset switches', async () => {
    renderIt()
    await screen.findByRole('checkbox', { name: /Active power/ })
    await userEvent.click(screen.getByRole('checkbox', { name: /Active power/ }))
    await waitFor(() => expect(
      JSON.parse(localStorage.getItem('assetDetail:metrics:Generator:dispatch')!),
    ).not.toContain('p'))
  })

  it('offers both export scopes', async () => {
    renderIt()
    expect(await screen.findByRole('link', { name: /Export configured view/ })).toBeTruthy()
    expect(await screen.findByRole('link', { name: /Full asset report/ })).toBeTruthy()
  })
})
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd pypsa-gui/frontend && npx vitest run src/pages/results/asset/AssetDetail.test.tsx
```

- [ ] **Step 3: Implement `AssetDetail.tsx`**

```tsx
import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Download } from 'lucide-react'
import toast from 'react-hot-toast'
import { useUIStore } from '../../../store/uiStore'
import { nk } from '../../../utils/queryKeys'
import { useResultsFilter } from '../filterContext'
import { downloadCSV, KPI, Seg } from '../shared'
import AssetCharts from './AssetCharts'
import AssetPicker from './AssetPicker'
import AssetTable, { tableRows } from './AssetTable'
import MetricChecklist from './MetricChecklist'
import { assetResultsApi, type AssetQueryParams } from './api'
import { loadSelection, reconcileSelection, saveSelection } from './selectionMemory'
import { CATEGORY_ORDER, type AssetRef, type Remedy, type ViewMode } from './types'

export default function AssetDetail() {
  const currentProject = useUIStore(s => s.currentProject)
  const setSlidePanel = useUIStore(s => s.setSlidePanel)
  const setSelectedComponent = useUIStore(s => s.setSelectedComponent)
  const assetDetailRequest = useUIStore(s => s.assetDetailRequest)
  const clearAssetDetailRequest = useUIStore(s => s.clearAssetDetailRequest)
  const filter = useResultsFilter()

  const [asset, setAsset] = useState<AssetRef | null>(null)
  const [category, setCategory] = useState('dispatch')
  const [mode, setMode] = useState<ViewMode>('chronological')
  const [selected, setSelected] = useState<string[]>([])
  const [view, setView] = useState<'table' | 'chart'>('table')

  const { data: assets = [] } = useQuery({
    queryKey: nk(currentProject, 'assetResults', 'assets'),
    queryFn: assetResultsApi.listAssets,
  })

  // Auto-select so the pane is never empty on arrival.
  useEffect(() => {
    if (!asset && assets.length > 0) setAsset(assets[0])
  }, [assets, asset])

  // Deep link from PropertiesPanel / BottomPanel / map / chatbot.
  useEffect(() => {
    if (!assetDetailRequest) return
    const match = assets.find(a =>
      a.class === assetDetailRequest.componentClass &&
      a.name === assetDetailRequest.name)
    if (match) {
      setAsset(match)
      if (assetDetailRequest.category) setCategory(assetDetailRequest.category)
      if (assetDetailRequest.metrics?.length) setSelected(assetDetailRequest.metrics)
      if (assetDetailRequest.mode) setMode(assetDetailRequest.mode)
      if (assetDetailRequest.chart != null) setView(assetDetailRequest.chart ? 'chart' : 'table')
    }
    clearAssetDetailRequest()
  }, [assetDetailRequest, assets, clearAssetDetailRequest])

  const params: AssetQueryParams | null = asset && {
    componentClass: asset.class, name: asset.name, category, metrics: selected,
    source: 'lopf', fromIso: filter.fromIso, toIso: filter.toIso,
    period: filter.selectedPeriod, mode,
  }

  const { data } = useQuery({
    queryKey: nk(currentProject, 'assetResults', asset?.class, asset?.name,
                 category, selected.join(','), mode,
                 filter.fromIso, filter.toIso, filter.selectedPeriod),
    queryFn: () => assetResultsApi.get(params!),
    enabled: !!params,
  })

  // Reconcile the remembered tick-set the moment the backend tells us what is
  // actually available for THIS asset. Metrics that became blocked or n/a are
  // dropped silently — their reason is already on screen in the checklist.
  useEffect(() => {
    if (!data || !asset) return
    const next = reconcileSelection(loadSelection(asset.class, category), data.metrics)
    setSelected(prev =>
      prev.length && prev.every(id => next.includes(id) || data.metrics
        .some(m => m.id === id && m.status === 'ok')) ? prev : next)
  }, [data?.metrics, asset?.class, asset?.name, category])

  // A category that is not `ok` for the newly-picked asset falls back to the
  // first that is — summary at worst, which works even unsolved.
  useEffect(() => {
    if (!data) return
    const active = data.categories.find(c => c.id === category)
    if (active && active.status !== 'ok') {
      const fallback = data.categories.find(c => c.status === 'ok')
      if (fallback) setCategory(fallback.id)
    }
  }, [data?.categories, category])

  const toggle = (id: string) => {
    if (!asset) return
    const next = selected.includes(id)
      ? selected.filter(x => x !== id) : [...selected, id]
    setSelected(next)
    saveSelection(asset.class, category, next)
  }

  const onRemedy = (r: Remedy) => {
    if (r.action === 'run_simulation') setSlidePanel('simparams')
    else if (r.action === 'run_ac_pf') setSlidePanel('simparams')
    else if (r.action === 'open_properties' && asset) {
      setSelectedComponent({ type: asset.class, name: asset.name })
      setSlidePanel(null)
    }
  }

  const scalarCards = useMemo(() => {
    if (!data) return []
    return data.metrics
      .filter(m => m.kind === 'scalar' && selected.includes(m.id) && m.id in data.scalars)
      .map(m => ({ metric: m, value: data.scalars[m.id] }))
  }, [data, selected])

  const exportCsv = () => {
    if (!data || !asset) return
    const { header, rows } = tableRows(data)
    downloadCSV(`${asset.name}_${category}.csv`, header, rows)
    toast.success('Exported CSV')
  }

  return (
    <div className="flex h-full min-h-0">
      <div className="w-56 shrink-0"><AssetPicker
        assets={assets} selected={asset} onSelect={a => { setAsset(a); }} /></div>

      <div className="flex-1 min-w-0 flex flex-col">
        {/* Identity header */}
        <div className="shrink-0 px-3 py-2 border-b border-border">
          {asset ? (
            <div className="flex items-baseline gap-2">
              <span className="text-[13px] font-medium">{asset.name}</span>
              <span className="text-[11px] text-muted">
                {asset.class}{asset.carrier && ` · carrier ${asset.carrier}`}
                {asset.bus && ` · bus ${asset.bus}`}
              </span>
            </div>
          ) : <span className="text-[11px] text-muted">No assets in this network.</span>}
        </div>

        {/* Category strip — greyed entries carry their reason as a tooltip */}
        <div role="tablist" className="shrink-0 flex items-center gap-0 px-2
          border-b border-border overflow-x-auto">
          {CATEGORY_ORDER.map(id => {
            const c = data?.categories.find(x => x.id === id)
            const label = c?.label ?? id
            const disabled = !c || c.status !== 'ok'
            return (
              <button key={id} role="tab" disabled={disabled}
                aria-selected={category === id}
                title={disabled ? (c?.reason ?? 'loading…') : label}
                onClick={() => setCategory(id)}
                className={`h-8 px-2.5 text-[11px] whitespace-nowrap border-b-2 -mb-px
                  ${category === id ? 'border-accent text-accent' : 'border-transparent'}
                  ${disabled
                    ? `text-muted/50 cursor-not-allowed
                       ${c?.status === 'blocked' ? 'italic' : 'line-through decoration-border'}`
                    : 'text-muted hover:text-text'}`}
              >{label}</button>
            )
          })}
        </div>

        <div className="flex-1 min-h-0 flex">
          <div className="w-60 shrink-0 overflow-y-auto border-r border-border">
            {data && <MetricChecklist metrics={data.metrics} selected={selected}
              onToggle={toggle} onRemedy={onRemedy} />}
          </div>

          <div className="flex-1 min-w-0 flex flex-col">
            {/* Controls + exports */}
            <div className="shrink-0 flex items-center gap-2 px-2 py-1.5
              border-b border-border">
              <Seg value={view} onChange={setView}
                options={[{ value: 'table', label: 'Table' },
                          { value: 'chart', label: 'Chart' }]} />
              <Seg value={mode} onChange={setMode}
                options={[{ value: 'chronological', label: 'Chronological' },
                          { value: 'duration', label: 'Duration' },
                          { value: 'monthly', label: 'Monthly' }]} />
              <span className="flex-1" />
              <button onClick={exportCsv}
                className="flex items-center gap-1 text-[11px] text-muted hover:text-accent">
                <Download size={11} /> CSV
              </button>
              {params && (
                <>
                  <a href={assetResultsApi.exportXlsxUrl(params, 'view')} download
                    className="flex items-center gap-1 text-[11px] text-muted hover:text-accent">
                    <Download size={11} /> Export configured view
                  </a>
                  <a href={assetResultsApi.exportXlsxUrl(params, 'full')} download
                    className="flex items-center gap-1 text-[11px] text-muted hover:text-accent">
                    <Download size={11} /> Full asset report
                  </a>
                </>
              )}
            </div>

            {scalarCards.length > 0 && (
              <div className="shrink-0 flex flex-wrap gap-2 px-2 py-2 border-b border-border">
                {scalarCards.map(({ metric, value }) => (
                  <KPI key={metric.id} label={metric.label} unit={metric.unit}
                    hint={metric.formula}
                    value={typeof value === 'object' && value !== null
                      ? Object.entries(value).map(([k, v]) => `${k}: ${v}`).join('  ')
                      : String(value ?? '—')} />
                ))}
              </div>
            )}

            <div className="flex-1 min-h-0 flex flex-col">
              {data && (view === 'table'
                ? <AssetTable data={data} />
                : <AssetCharts data={data} />)}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Wire the eleventh tab into `Results.tsx`**

Five edits:

1. Extend the union and the valid set:
```ts
type ResultsTab =
  | 'overview' | 'capex' | 'dispatch' | 'loadflow' | 'prices' | 'emissions'
  | 'economics' | 'curtailment' | 'lostload' | 'storage' | 'asset'

const VALID_TABS: ReadonlySet<ResultsTab> = new Set<ResultsTab>([
  'overview', 'capex', 'dispatch', 'loadflow', 'prices', 'emissions',
  'economics', 'curtailment', 'lostload', 'storage', 'asset',
])
```
2. Append to `TABS` (import `Crosshair` from lucide-react):
```ts
{ id: 'asset', label: 'Asset Detail', Icon: Crosshair,
  tip: 'One asset in full — every applicable result, as numbers or charts, exportable' },
```
3. `RESULTS_TO_COMPARE_TAB` gains `asset: 'overview'` — CompareView is
   scenario-vs-scenario and has no per-asset equivalent.
4. The tab strip container gains `overflow-x-auto` and `shrink-0` on each
   button so eleven tabs scroll rather than wrap:
```tsx
<div className="flex items-center shrink-0 border-b border-border bg-panel px-2 gap-0 overflow-x-auto">
```
5. Body switch gains `{t === 'asset' && <AssetDetail />}` with
   `import AssetDetail from './results/asset/AssetDetail'`.

- [ ] **Step 5: Run tests + typecheck**

```bash
cd pypsa-gui/frontend && npx vitest run src/pages/results/asset/
cd pypsa-gui/frontend && PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx tsc --noEmit -p tsconfig.json
```

Expected: all asset tests PASS (7 new + the earlier files), typecheck clean.
`Seg` and `KPI` must be imported from `../shared` — confirm their exported
signatures match the usage above before running.

- [ ] **Step 6: Commit**

```bash
cd "$(git rev-parse --show-toplevel)" && git branch --show-current
git commit pypsa-gui/frontend/src/pages/results/asset/AssetDetail.tsx \
  pypsa-gui/frontend/src/pages/results/asset/AssetDetail.test.tsx \
  pypsa-gui/frontend/src/pages/Results.tsx \
  -m "feat(gui): Asset Detail tab — per-asset results evaluation

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 13: uiStore slot + the four deep links

**Files:**
- Modify: `pypsa-gui/frontend/src/store/uiStore.ts`
- Modify: `pypsa-gui/frontend/src/layout/PropertiesPanel.tsx`
- Modify: `pypsa-gui/frontend/src/layout/BottomPanel.tsx`
- Modify: `pypsa-gui/frontend/src/components/ChatPanel.tsx`
- Test: `pypsa-gui/frontend/src/store/assetDetailRequest.test.ts`

**Interfaces:**
- Produces on `uiStore`:
  - `assetDetailRequest: AssetDetailRequest | null` where
    `AssetDetailRequest = { componentClass: string; name: string; category?: string; metrics?: string[]; mode?: ViewMode; chart?: boolean }`
  - `requestAssetDetail(req: AssetDetailRequest): void` — also sets
    `selectedComponent`, opens the `results` slide panel and calls
    `requestResultsTab('asset')`, so all four entry points share one path
  - `clearAssetDetailRequest(): void`

- [ ] **Step 1: Write the failing test**

```ts
import { beforeEach, describe, expect, it } from 'vitest'
import { useUIStore } from './uiStore'

describe('requestAssetDetail', () => {
  beforeEach(() => {
    useUIStore.setState({
      assetDetailRequest: null, resultsTabRequest: null,
      selectedComponent: null, activeSlidePanel: null,
    })
  })

  it('stores the request verbatim', () => {
    useUIStore.getState().requestAssetDetail({
      componentClass: 'Generator', name: 'Gas 1',
      category: 'dispatch', metrics: ['p'], mode: 'duration', chart: true,
    })
    expect(useUIStore.getState().assetDetailRequest).toEqual({
      componentClass: 'Generator', name: 'Gas 1',
      category: 'dispatch', metrics: ['p'], mode: 'duration', chart: true,
    })
  })

  it('opens the Results panel on the asset tab in one call', () => {
    useUIStore.getState().requestAssetDetail({ componentClass: 'Line', name: 'L1' })
    const s = useUIStore.getState()
    expect(s.activeSlidePanel).toBe('results')
    expect(s.resultsTabRequest).toBe('asset')
  })

  it('also selects the component so PropertiesPanel stays in sync', () => {
    useUIStore.getState().requestAssetDetail({ componentClass: 'Bus', name: 'B1' })
    expect(useUIStore.getState().selectedComponent).toEqual({ type: 'Bus', name: 'B1' })
  })

  it('clears cleanly so a second request re-fires', () => {
    useUIStore.getState().requestAssetDetail({ componentClass: 'Bus', name: 'B1' })
    useUIStore.getState().clearAssetDetailRequest()
    expect(useUIStore.getState().assetDetailRequest).toBeNull()
  })
})
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd pypsa-gui/frontend && npx vitest run src/store/assetDetailRequest.test.ts
```

- [ ] **Step 3: Add the slot to `uiStore.ts`**

Alongside the existing `resultsTabRequest` declarations (~line 269, ~384, ~489):

```ts
export interface AssetDetailRequest {
  componentClass: string
  name: string
  category?: string
  metrics?: string[]
  mode?: 'chronological' | 'duration' | 'monthly'
  chart?: boolean
}
```

State: `assetDetailRequest: null,`

Actions:
```ts
// ONE path for all four entry points (Properties, bottom table, map,
// chatbot). Each of them only has to call this — the panel, the tab and the
// selection all move together, so none of them can drift out of step.
requestAssetDetail: (req) => set({
  assetDetailRequest: req,
  selectedComponent: { type: req.componentClass, name: req.name },
  activeSlidePanel: 'results',
  resultsTabRequest: 'asset',
}),
clearAssetDetailRequest: () => set({ assetDetailRequest: null }),
```

Add both to the store's TypeScript interface next to `requestResultsTab` /
`clearResultsTabRequest`.

- [ ] **Step 4: Add the three UI entry points**

**PropertiesPanel** — in the header of the selected-asset card:
```tsx
<button
  onClick={() => useUIStore.getState().requestAssetDetail({
    componentClass: selected.type, name: selected.name })}
  title="Open this asset's results in the Asset Detail tab"
  className="flex items-center gap-1 text-[11px] text-muted hover:text-accent"
><ExternalLink size={11} /> View results</button>
```

**BottomPanel `AssetTable`** — a trailing action cell per row, using the tab's
component class:
```tsx
<button
  onClick={e => { e.stopPropagation()
    useUIStore.getState().requestAssetDetail({
      componentClass: COMPONENT_CLASS_FOR_TAB[tab], name: row.name }) }}
  title="View this asset's results"
  className="text-muted hover:text-accent"
><ExternalLink size={11} /></button>
```
Add `const COMPONENT_CLASS_FOR_TAB: Record<string, string> = { Buses: 'Bus',
Lines: 'Line', Transformers: 'Transformer', Generators: 'Generator', Storage:
'StorageUnit', Stores: 'Store', Loads: 'Load', Links: 'Link' }` next to the
existing `TAB_COLUMNS` map.

**ChatPanel** — beside the existing `select_component` handler (~line 104):
```ts
if (d.kind === 'open_asset_detail' && d.component_class && d.name) {
  useUIStore.getState().requestAssetDetail({
    componentClass: d.component_class,
    name: d.name,
    category: d.category,
    metrics: d.metrics,
    mode: d.mode,
    chart: d.chart,
  })
  return
}
```

- [ ] **Step 5: Run tests + typecheck**

```bash
cd pypsa-gui/frontend && npx vitest run src/store/assetDetailRequest.test.ts
cd pypsa-gui/frontend && npx vitest run
cd pypsa-gui/frontend && PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx tsc --noEmit -p tsconfig.json
```

Expected: 4 new tests PASS, the full frontend suite still green, typecheck clean.

- [ ] **Step 6: Commit**

```bash
cd "$(git rev-parse --show-toplevel)" && git branch --show-current
git commit pypsa-gui/frontend/src/store/uiStore.ts \
  pypsa-gui/frontend/src/store/assetDetailRequest.test.ts \
  pypsa-gui/frontend/src/layout/PropertiesPanel.tsx \
  pypsa-gui/frontend/src/layout/BottomPanel.tsx \
  pypsa-gui/frontend/src/components/ChatPanel.tsx \
  -m "feat(gui): deep links into Asset Detail from properties, table and chat

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 14: Three chatbot tools

**Files:**
- Modify: `pypsa-gui/backend/services/chat_tools.py` (three dispatchers + `DISPATCHERS` entries)
- Modify: `pypsa-gui/backend/services/chat_tools_schema.py` (three `_t(...)` schemas; `RESULTS_TAB_ENUM` gains `"asset"`)
- Create: `pypsa-gui/backend/tests/test_chat_asset_results.py`

**Interfaces:**
- Consumes: `service.build_response`, `export.build_workbook` from Tasks 5–6.
- Produces:
  - `get_asset_results(component_class, name, *, category="summary", metrics=None, source="lopf", from_iso=None, to_iso=None, period=None, resolution="stats", max_rows=2000) -> dict`
  - `ui_open_asset_detail(component_class, name, *, category=None, metrics=None, mode=None, chart=None) -> dict`
  - `export_asset_results(component_class, name, *, scope="view", category="summary", metrics=None, filename=None, source="lopf", mode="chronological") -> dict`

- [ ] **Step 1: Write the failing test**

```python
"""The three asset-results chat tools: schema/signature parity, the statistics
default, and the raw-mode cap."""
import json

import pytest

from services import chat_tools as T
from services import chat_tools_schema as S
from tests.conftest import build_network


def _schema(name: str) -> dict:
    return next(t for t in S.TOOLS if t["name"] == name)


ASSET_TOOLS = ("get_asset_results", "ui_open_asset_detail", "export_asset_results")


def test_every_asset_tool_is_registered_and_dispatchable():
    for name in ASSET_TOOLS:
        assert _schema(name), f"{name} missing from TOOLS"
        assert callable(T.DISPATCHERS[name]), f"{name} missing from DISPATCHERS"


def test_schema_optional_fields_all_have_python_defaults():
    """Every field NOT in `required` must have a default, or the dispatcher
    raises TypeError the moment the model correctly omits it."""
    import inspect
    for name in ASSET_TOOLS:
        sch = _schema(name)["input_schema"]
        sig = inspect.signature(T.DISPATCHERS[name])
        for field in sch["properties"]:
            if field in sch.get("required", []):
                continue
            assert field in sig.parameters, f"{name}: schema field {field} not a param"
            assert sig.parameters[field].default is not inspect.Parameter.empty, \
                f"{name}: optional field {field} has no Python default"


def test_results_tab_enum_offers_the_new_tab():
    assert "asset" in S.RESULTS_TAB_ENUM


def test_default_resolution_is_statistics_not_raw_arrays(install_network):
    install_network(build_network(solve=True))
    out = T.get_asset_results("Generator", "gas", category="dispatch", metrics=["p"])
    assert out["resolution"] == "stats"
    assert "series" not in out
    st = out["series_stats"]["p"]
    for key in ("min", "max", "mean", "sum", "p50", "p95", "peak_at",
                "zero_hours", "sparkline"):
        assert key in st, f"missing statistic {key}"
    assert len(st["sparkline"]) <= 48


def test_the_default_response_stays_small_enough_to_be_worth_sending(install_network):
    """An hourly year x 10 metrics is ~87k numbers. The default must not be
    anywhere near that."""
    n = build_network(solve=True)
    install_network(n)
    out = T.get_asset_results("Generator", "gas", category="dispatch",
                              metrics=["p", "curtailment", "capacity_factor"])
    assert len(json.dumps(out)) < 8_000


def test_raw_resolution_returns_arrays_and_flags_truncation(install_network):
    install_network(build_network(solve=True))
    out = T.get_asset_results("Generator", "gas", category="dispatch",
                              metrics=["p"], resolution="raw", max_rows=2)
    assert out["resolution"] == "raw"
    assert len(out["series"]["p"]) == 2
    assert out["truncated"] is True
    assert out["n_total"] == 4
    assert "export_asset_results" in out["note"]


def test_raw_resolution_is_not_truncated_when_it_fits(install_network):
    install_network(build_network(solve=True))
    out = T.get_asset_results("Generator", "gas", category="dispatch",
                              metrics=["p"], resolution="raw", max_rows=100)
    assert out["truncated"] is False


def test_blocked_metrics_are_reported_with_their_reason(install_network):
    install_network(build_network(solve=True))
    out = T.get_asset_results("Generator", "gas", category="dispatch",
                              metrics=["p", "status"])
    unavailable = {u["id"]: u for u in out["unavailable"]}
    assert "status" in unavailable
    assert unavailable["status"]["status"] == "blocked"
    assert "committable" in unavailable["status"]["reason"]


def test_scalars_are_always_returned_for_the_category(install_network):
    install_network(build_network(solve=True))
    out = T.get_asset_results("Generator", "gas", category="dispatch")
    assert out["scalars"]["energy_mwh"] is not None


def test_ui_tool_emits_a_typed_ui_event_and_never_mutates():
    ev = T.ui_open_asset_detail("Generator", "Gas 1", category="dispatch",
                                metrics=["p"], mode="duration", chart=True)
    assert ev["_ui_event"] is True
    assert ev["kind"] == "open_asset_detail"
    assert ev["component_class"] == "Generator"
    assert ev["name"] == "Gas 1"
    assert ev["metrics"] == ["p"]
    assert ev["chart"] is True


def test_ui_tool_omits_unset_optionals_so_the_panel_keeps_its_state():
    ev = T.ui_open_asset_detail("Generator", "Gas 1")
    assert "category" not in ev and "metrics" not in ev and "mode" not in ev


def test_export_tool_writes_an_upload_the_chat_panel_can_offer(install_network):
    install_network(build_network(solve=True))
    out = T.export_asset_results("Generator", "gas", scope="view",
                                 category="dispatch", metrics=["p"])
    assert out["filename"].endswith(".xlsx")
    assert out["kind"] == "agent_export"
    assert out["bytes"] > 0


def test_export_tool_rejects_an_unknown_asset(install_network):
    install_network(build_network(solve=True))
    with pytest.raises(Exception):
        T.export_asset_results("Generator", "nope")
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd "$(git rev-parse --show-toplevel)" && pixi run gui-tests tests/test_chat_asset_results.py -v
```

Expected: `AttributeError: module 'services.chat_tools' has no attribute 'get_asset_results'`.

- [ ] **Step 3: Add the dispatchers to `chat_tools.py`**

```python
def _series_stats(index: list[str], values: list, *, points: int = 48) -> dict:
    """
    Compress a series to something worth putting in a context window.

    An hourly year is 8 760 numbers per metric; ten metrics is ~87 000. The
    agent can answer almost every real question — peak, mean, total, when it
    peaks, how often it sits at zero — from these ~12 fields plus a coarse
    sparkline, and it is told to reach for the export tool when it cannot.
    """
    import math

    finite = [(i, float(v)) for i, v in enumerate(values)
              if v is not None and math.isfinite(float(v))]
    if not finite:
        return {"min": None, "max": None, "mean": None, "sum": None,
                "p50": None, "p95": None, "peak_at": None,
                "zero_hours": 0, "sparkline": []}
    vals = [v for _, v in finite]
    ordered = sorted(vals)
    peak_i = max(finite, key=lambda t: t[1])[0]

    def pct(q: float) -> float:
        k = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
        return ordered[k]

    step = max(1, len(vals) // points)
    return {
        "min": ordered[0],
        "max": ordered[-1],
        "mean": sum(vals) / len(vals),
        "sum": sum(vals),
        "p50": pct(0.5),
        "p95": pct(0.95),
        "peak_at": index[peak_i] if peak_i < len(index) else None,
        "zero_hours": sum(1 for v in vals if abs(v) < 1e-9),
        "sparkline": [round(v, 4) for v in vals[::step]][:points],
    }


def get_asset_results(
    component_class: str,
    name: str,
    *,
    category: str = "summary",
    metrics: list | None = None,
    source: str = "lopf",
    from_iso: str | None = None,
    to_iso: str | None = None,
    period: str | None = None,
    resolution: str = "stats",
    max_rows: int = 2000,
) -> dict:
    """Per-asset results for the agent. Statistics by default; raw on request."""
    from services.asset_results import service as svc
    from services.asset_results.registry import metrics_for
    from services.pypsa_service import PyPSAService

    n = PyPSAService.get_network()
    df = getattr(n, svc.C.attr_for(component_class))
    if name not in df.index:
        raise ValueError(f"No {component_class} named '{name}'")

    # No explicit metric list means "everything in this category" — the agent
    # asks a question, it does not know the registry's metric ids up front.
    requested = [str(m) for m in (metrics or [])]
    if not requested:
        requested = [m.id for m in metrics_for(component_class, category)]

    resp = svc.build_response(
        n, component_class, name, category=category, metric_ids=requested,
        source=source, from_iso=from_iso, to_iso=to_iso, period=period,
        mode="chronological",
    )

    unavailable = [
        {"id": m["id"], "label": m["label"], "status": m["status"],
         "reason": m.get("reason", "")}
        for m in resp["metrics"] if m["status"] != "ok"
    ]
    out: dict = {
        "asset": resp["asset"],
        "category": category,
        "categories": [{"id": c["id"], "status": c["status"],
                        "reason": c.get("reason", "")} for c in resp["categories"]],
        "scalars": resp["scalars"],
        "unavailable": unavailable,
        "n_snapshots": len(resp["index"]),
    }
    if resolution == "raw":
        out["resolution"] = "raw"
        out["index"] = resp["index"][:max_rows]
        out["series"] = {k: v[:max_rows] for k, v in resp["series"].items()}
        out["truncated"] = len(resp["index"]) > max_rows
        out["n_total"] = len(resp["index"])
        out["note"] = (
            "Truncated to the first {} rows. Call export_asset_results for the "
            "complete set as a workbook.".format(max_rows)
            if out["truncated"] else "Complete — no truncation."
        )
    else:
        out["resolution"] = "stats"
        out["series_stats"] = {
            k: _series_stats(resp["index"], v) for k, v in resp["series"].items()
        }
    return out


def ui_open_asset_detail(
    component_class: str,
    name: str,
    *,
    category: str | None = None,
    metrics: list | None = None,
    mode: str | None = None,
    chart: bool | None = None,
) -> dict:
    """Open the Asset Detail tab pre-configured. No backend mutation."""
    event: dict[str, Any] = {
        "_ui_event": True, "kind": "open_asset_detail",
        "component_class": component_class, "name": name,
    }
    if category:
        event["category"] = category
    if metrics:
        event["metrics"] = [str(m) for m in metrics]
    if mode:
        event["mode"] = mode
    if chart is not None:
        event["chart"] = bool(chart)
    return event


def export_asset_results(
    component_class: str,
    name: str,
    *,
    scope: str = "view",
    category: str = "summary",
    metrics: list | None = None,
    filename: str | None = None,
    source: str = "lopf",
    mode: str = "chronological",
) -> dict:
    """Write the workbook into the project's uploads/ as an agent export."""
    from services.asset_results import export as xls
    from services.asset_results import service as svc
    from services.pypsa_service import PyPSAService

    n = PyPSAService.get_network()
    df = getattr(n, svc.C.attr_for(component_class))
    if name not in df.index:
        raise ValueError(f"No {component_class} named '{name}'")

    blob = xls.build_workbook(
        n, component_class, name, scope=scope, category=category,
        metric_ids=[str(m) for m in (metrics or [])], source=source,
        from_iso=None, to_iso=None, period=None, mode=mode,
        project=PyPSAService.get_loaded_project(),
    )
    safe_name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)
    fname = filename or f"{safe_name}_{category}.xlsx"
    # Reuse the SAME writer the existing export_to_excel tool uses, so the
    # download chip, the 25 MB cap and the filename sanitiser are shared.
    return _write_agent_export(fname, blob, kind="agent_export")
```

If `_write_agent_export` does not already exist, extract it from the body of
`export_to_excel` (which currently writes the workbook bytes into `uploads/`)
so both tools go through one path, and have `export_to_excel` call it too.

Register all three in `DISPATCHERS`:
```python
"get_asset_results": get_asset_results,
"ui_open_asset_detail": ui_open_asset_detail,
"export_asset_results": export_asset_results,
```

- [ ] **Step 4: Add the three schemas to `chat_tools_schema.py`**

Add `"asset"` to `RESULTS_TAB_ENUM`, and define the category/mode enums once
near the other module-level constants:

```python
ASSET_CATEGORY_ENUM = [
    "summary", "capacity", "dispatch", "storage",
    "loadflow", "prices", "economics", "emissions",
]
ASSET_VIEW_MODE_ENUM = ["chronological", "duration", "monthly"]
ASSET_RESOLUTION_ENUM = ["stats", "raw"]
```

```python
    _t(
        "get_asset_results",
        "Per-asset results for ONE component (e.g. Generator 'Gas 1'). "
        "Returns {asset, category, categories[{id,status,reason}], scalars, "
        "unavailable[{id,label,status,reason}], n_snapshots} plus, by default "
        "(resolution='stats'), series_stats[metric] = {min,max,mean,sum,p50,"
        "p95,peak_at,zero_hours,sparkline} — a <=48-point downsample, NOT the "
        "full series. Use this default for questions about totals, peaks, "
        "timing and shape. resolution='raw' returns real arrays truncated to "
        "max_rows (default 2000) with truncated + n_total set; an hourly year "
        "is 8760 rows, so prefer export_asset_results when the user wants the "
        "complete data rather than an answer. Metrics that do not apply come "
        "back under `unavailable` with the reason, never as an error. "
        "Safety: read.",
        {
            "component_class": {"type": "string", "enum": COMPONENT_CLASS_ENUM},
            "name": {"type": "string"},
            "category": {"type": "string", "enum": ASSET_CATEGORY_ENUM},
            "metrics": {"type": "array", "items": {"type": "string"}},
            "source": {"type": "string", "enum": RESULTS_SOURCE_ENUM},
            "from_iso": {"type": "string"},
            "to_iso": {"type": "string"},
            "period": {"type": "string"},
            "resolution": {"type": "string", "enum": ASSET_RESOLUTION_ENUM},
            "max_rows": {"type": "integer"},
        },
        ["component_class", "name"],
    ),
    _t(
        "ui_open_asset_detail",
        "Open the Results > Asset Detail tab on one asset, optionally with a "
        "category, a metric selection, a view mode and the chart toggle "
        "pre-set. Emits a ui_event (kind=open_asset_detail); NO backend "
        "mutation. Omitted arguments leave the panel's current state alone. "
        "Safety: read.",
        {
            "component_class": {"type": "string", "enum": COMPONENT_CLASS_ENUM},
            "name": {"type": "string"},
            "category": {"type": "string", "enum": ASSET_CATEGORY_ENUM},
            "metrics": {"type": "array", "items": {"type": "string"}},
            "mode": {"type": "string", "enum": ASSET_VIEW_MODE_ENUM},
            "chart": {"type": "boolean"},
        },
        ["component_class", "name"],
    ),
    _t(
        "export_asset_results",
        "Write one asset's results to an xlsx workbook in the project's "
        "uploads/ (kind='agent_export'), so the chat panel shows a download "
        "chip. scope='view' exports the named category and metrics; "
        "scope='full' exports every applicable category with every available "
        "metric. The workbook always opens with an About sheet carrying the "
        "project, solve time, objective, result source, horizon, period and "
        "view mode. Returns {filename, bytes, kind}. Safety: write.",
        {
            "component_class": {"type": "string", "enum": COMPONENT_CLASS_ENUM},
            "name": {"type": "string"},
            "scope": {"type": "string", "enum": ["view", "full"]},
            "category": {"type": "string", "enum": ASSET_CATEGORY_ENUM},
            "metrics": {"type": "array", "items": {"type": "string"}},
            "filename": {"type": "string"},
            "source": {"type": "string", "enum": RESULTS_SOURCE_ENUM},
            "mode": {"type": "string", "enum": ASSET_VIEW_MODE_ENUM},
        },
        ["component_class", "name"],
    ),
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd "$(git rev-parse --show-toplevel)" && pixi run gui-tests tests/test_chat_asset_results.py -v
cd "$(git rev-parse --show-toplevel)" && pixi run gui-tests -q
```

Expected: 13 new tests PASS. The whole backend suite is green — in particular
the existing `len(TOOLS) == len(DISPATCHERS)` parity test, which now counts
three more of each.

- [ ] **Step 6: Commit**

```bash
cd "$(git rev-parse --show-toplevel)" && git branch --show-current
git commit pypsa-gui/backend/services/chat_tools.py \
  pypsa-gui/backend/services/chat_tools_schema.py \
  pypsa-gui/backend/tests/test_chat_asset_results.py \
  -m "feat(gui): three chatbot tools for per-asset results

get_asset_results defaults to statistics rather than raw arrays — an hourly
year x 10 metrics is ~87k numbers, which would consume a large share of the
context window for one question.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 15: Phase-1 verification

No new code. This is the gate before phases 2–3 begin, and specifically the
point at which the registry's shape is judged.

- [ ] **Step 1: Full suites**

```bash
cd "$(git rev-parse --show-toplevel)" && pixi run gui-tests -q
cd pypsa-gui/frontend && npx vitest run
cd pypsa-gui/frontend && PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx tsc --noEmit -p tsconfig.json
```

All three must be green. Record the counts.

- [ ] **Step 2: Manual walkthrough against a running app**

```bash
bash pypsa-gui/start.sh
```

Load a solved project, then confirm each of these by hand:

1. Results → Asset Detail exists and the strip scrolls to reach it.
2. Search "gas", pick a generator; identity header shows class, carrier, bus.
3. Load flow and Storage are greyed with a *reason* on hover; Dispatch is live.
4. Tick `p` + `curtailment` + `Energy`; table fills, KPI card appears.
5. Chart view draws ONE chart. Add `μ upper` → a second chart appears below it,
   sharing the X axis.
6. Duration and Monthly modes reshape both the table and the chart.
7. Narrow the horizon filter — the row count drops.
8. Export configured view → xlsx opens with About/Summary/Dispatch sheets.
9. Full asset report → more sheets; About lists the omitted categories.
10. CSV, SVG and PNG all download and open.
11. `__voll_*` and `<name>@<year>` are absent from the picker.
12. Open an unsolved project: Summary still works, everything else is blocked
    with a Run button.
13. Add a bus without re-solving: every result category goes blocked/stale.
14. Ask the chatbot: *"what were Gas 1's dispatch and revenue, then export it"*
    — it answers with numbers, opens the tab configured, and produces a chip.

- [ ] **Step 3: Rebuild the macOS app**

CLAUDE.md is explicit: a green suite says the source is fixed, not that the
artifact the user runs is current. The frontend is served from `frontend/dist/`
in the packaged app, so a frontend-only change still needs this.

```bash
bash pypsa-gui/build-macos.sh
```

Check the exit status. Do not describe the DMG as current unless this succeeded.

- [ ] **Step 4: Judge the registry before phase 2**

Phase 1 exists to test one hypothesis: that adding a component class is mostly
declarative. Answer explicitly before starting phase 2:

- How many of the 14 Generator compute functions are genuinely class-specific
  versus reusable for Line/Link/StorageUnit?
- Did any metric need a field the `Metric` dataclass does not have?
- Did `applicability.py` need a special case that is really a metric property?

If two or more of those point the wrong way, revise the registry shape now —
before it is replicated across seven more classes. Record the answer in the
phase-2 plan.

- [ ] **Step 5: Commit the verification record**

```bash
cd "$(git rev-parse --show-toplevel)" && git branch --show-current
git commit docs/superpowers/plans/2026-07-31-asset-detail-phase1-generator.md \
  -m "docs: phase 1 verification record for Asset Detail

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Spec coverage

Every decision in the design maps to a task in this plan, except where the
phasing defers it:

| Spec decision | Task |
|---|---|
| D1 backend metric registry, `source_override` | 1, used in 5 |
| D2 eight categories | 1 |
| D3 metric inventory (Generator) | 1, 3, 4 |
| D4 outputs + interpretive inputs (`origin`) | 1, rendered in 8 |
| D5 three states with remedies | 1 (resolve), 2 (detect), 8 (render) |
| D6 two checklist zones | 8 |
| D7 one chart per unit | 11 |
| D8 three view modes | 5 (reshape), 10 (table), 11 (chart), 12 (control) |
| D9 picker-left layout | 9, 12 |
| D10 two exports + provenance; xlsx/csv/svg/png | 6, 11, 12 |
| D11 eleventh tab + deep links | 12, 13 |
| D12 hidden rows — vintage `p_nom_opt` | 3 (`gen_p_nom_by_vintage`), 5 (`list_assets` filter) |
| D12 hidden rows — VOLL unserved energy | **Phase 3** — it is a Load metric, and Load arrives with StorageUnit/Store |
| D13 unsolved and stale | 2, asserted in 5 |
| D14 endpoint shape | 5 |
| D15 module layout | 1, 5, 6 (plus `service.py`, added so `compute.py` stays metric functions only) |
| D16 three chatbot tools, statistics default | 14 |
| D17 vertical-slice build order | 15 (the gate) |
| D18 selection memory | 7, wired in 12 |

**Deliberately out of phase 1**, per D17: Line, Transformer, Link and Bus
(phase 2); StorageUnit, Store and Load (phase 3). Until then every non-Generator
class shows a live `summary` and seven categories that read
"not yet available — arrives in a later phase of this feature". That string is
the `REQ_NOT_YET` precondition from Task 1 and is the one place to change when
phase 2 begins.
