"""
Smoke test for Batch C polish items.

C1: lost_load endpoint emits voll_eur_per_mwh
C2: LoadFlow Lines table shows s_nom_opt column
C3: line_duals returns voll_bound_hours per line
C4: carrier names are lowercase in cost_breakdown / emissions / carrier_kpis
C5: emissions banner copy mentions "horizon total"
C6: Economics frontend type has charge_mwh in by_period
C7: Prices Payload type has source + note
C8: DispatchStack uses stampWithPeriod for multi-period x-axis
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except (AttributeError, ValueError):
    pass

import pandas as pd
import pypsa
from routers import simulation as sim_router
# The results serializers live in `routers/results.py` — they were carved out
# of `routers/simulation.py` and this driver still reached for them there, so
# every scenario below crashed on an AttributeError. `sim_router` is still the
# right home for `_state`, which is why both imports are here.
from routers import results as results_router
from services.pypsa_service import PyPSAService
from services.solver_service import SolverConfig

PASS_COUNT = 0
FAIL_COUNT = 0
REPO = pathlib.Path(__file__).resolve().parent.parent.parent


def _step(label: str, ok: bool, msg: str = "") -> None:
    global PASS_COUNT, FAIL_COUNT
    if ok:
        PASS_COUNT += 1
        print(f"  [PASS] {label}" + (f" — {msg}" if msg else ""))
    else:
        FAIL_COUNT += 1
        print(f"  [FAIL] {label}" + (f" — {msg}" if msg else ""))


def _crashed(label: str, exc: BaseException) -> None:
    """
    A scenario that raised never ran its assertions, so it must COUNT as a
    failure — printing the traceback and moving on leaves the driver exiting 0
    while testing nothing. That is exactly how the stale
    `routers.simulation.get_*` references below went unnoticed: the scenarios
    crashed on every run and the summary still read "Fail: 0".
    """
    _step(f"{label} ran without crashing", False, f"{type(exc).__name__}: {exc}")

def _has_in(path_str: str, snippet: str) -> bool:
    return snippet in (REPO / path_str).read_text(encoding="utf-8")


def test_c1_lost_load_voll() -> None:
    print("\n[C1] lost_load emits voll_eur_per_mwh")
    # `/results/lost_load` is served from `routers/results.py` — the read-only
    # serializers were carved out of `routers/simulation.py`, which this
    # assertion still named.
    ok = _has_in("backend/routers/results.py",
                 "voll_eur_per_mwh")
    _step("voll_eur_per_mwh in /results/lost_load payload", ok)
    ok2 = _has_in("frontend/src/api/simulation.ts", "voll_eur_per_mwh: number")
    _step("voll_eur_per_mwh in API client type", ok2)
    ok3 = _has_in("frontend/src/pages/results/AggregatedOverview.tsx",
                  "voll_eur_per_mwh")
    _step("Overview reads voll_eur_per_mwh", ok3)


def test_c2_lines_table_s_nom_opt() -> None:
    print("\n[C2] LoadFlow Lines table shows s_nom_opt")
    text = (REPO / "frontend" / "src" / "pages" / "results" / "LoadFlow.tsx").read_text(encoding="utf-8")
    has_header = ">s_nom_opt<" in text
    has_expansion_class = "text-accent font-semibold" in text and "expanded" in text
    _step("s_nom_opt column header present", has_header)
    _step("expanded-line callout (class change)", has_expansion_class)


def test_c3_line_duals_voll_bound() -> None:
    print("\n[C3] line_duals returns voll_bound_hours per line")
    # `get_line_duals`' body now lives in `services/results/line_duals.py`
    # (out of simulation.py into results.py, then into a service by the
    # 2026-09-04 decomposition).
    text = (REPO / "backend" / "services" / "results" / "line_duals.py").read_text(encoding="utf-8")
    has_field = '"voll_bound_hours": voll_bound' in text
    has_threshold = "VOLL_THRESHOLD = 10_000" in text
    _step("voll_bound_hours field emitted", has_field)
    _step("VOLL_THRESHOLD = 10_000 €/MWh", has_threshold)
    # Frontend
    fe_text = (REPO / "frontend" / "src" / "pages" / "results" / "LoadFlow.tsx").read_text(encoding="utf-8")
    has_voll_chip = "VOLL:" in fe_text and "vollHours" in fe_text
    _step("LoadFlow renders VOLL chip on binding-hours cell", has_voll_chip)


def test_c4_carrier_lowercase() -> None:
    print("\n[C4] carrier names are lowercase across endpoints")
    # These three lowercasing rules used to be read out of one file. They never
    # all lived there: the co2_by_carrier one is in the SOLVER's cost-decomposition
    # logging, not in any results endpoint, so that assertion was already reading
    # the wrong source before the 2026-09-04 decomposition moved the other two
    # into services. Each is now checked where it actually lives.
    cb_text = (REPO / "backend" / "services" / "results" / "cost_breakdown.py").read_text(encoding="utf-8")
    em_text = (REPO / "backend" / "services" / "results" / "emissions.py").read_text(encoding="utf-8")
    solver_text = (REPO / "backend" / "services" / "solver" / "diagnostics.py").read_text(encoding="utf-8")
    # cost_breakdown lowers carrier:
    has_cb_lower = 'carrier = carrier.lower() if carrier else ""' in cb_text
    # emissions lowers carrier:
    has_em_lower = (
        '(str(gens.at[name, "carrier"]) if "carrier" in gens.columns else "").lower()' in em_text
    )
    # co2_by_carrier keys lowered
    has_co2_lower = 'str(k).lower(): float(v)' in solver_text
    _step("cost_breakdown normalises carrier to lowercase", has_cb_lower)
    _step("emissions normalises gen carrier to lowercase", has_em_lower)
    _step("co2_by_carrier lookup keys lowercased", has_co2_lower)


def test_c5_emissions_banner() -> None:
    print("\n[C5] Emissions tab labels scope explicitly")
    text = (REPO / "frontend" / "src" / "pages" / "results" / "Emissions.tsx").read_text(encoding="utf-8")
    # Post-period-scoping rewrite: the tab now actually honors the period
    # filter (slices the data) instead of showing a warning banner.
    # The scope label appears in section headers as "(horizon total)" or
    # "(period N)" depending on selectedPeriod — both flavours must be
    # present in the code.
    has_scope_label = "scopeLabel" in text or "scope:" in text
    has_horizon_label = "horizon total" in text.lower()
    has_period_label = "period ${" in text  # template literal "period ${view.period}"
    _step("scopeLabel variable wired through section headers", has_scope_label)
    _step("'horizon total' label present", has_horizon_label)
    _step("'period N' template literal present", has_period_label)


def test_c6_economics_charge_mwh_threaded() -> None:
    print("\n[C6] Economics threads per-period charge_mwh from backend")
    text = (REPO / "frontend" / "src" / "pages" / "results" / "Economics.tsx").read_text(encoding="utf-8")
    has_type = "charge_mwh: number" in text
    has_use = "pe.charge_mwh ?? 0" in text
    no_approx = "r.charge_mwh * (pe.energy_mwh / r.energy_mwh)" not in text
    _step("AggregatedAssetRow.by_period[].charge_mwh exists", has_type)
    _step("sumGroup/projectedRows use pe.charge_mwh directly", has_use)
    _step("approximation removed", no_approx)


def test_c7_prices_fallback_banner() -> None:
    print("\n[C7] Prices renders banner when source='fallback'")
    text = (REPO / "frontend" / "src" / "pages" / "results" / "Prices.tsx").read_text(encoding="utf-8")
    has_type = "source?: 'lp' | 'fallback'" in text
    has_banner = "tsP.source === 'fallback'" in text
    _step("PricesPayload type includes source/note/fallback_per_snapshot", has_type)
    _step("Banner conditional on source='fallback'", has_banner)


def test_c8_dispatch_stamp_with_period() -> None:
    print("\n[C8] DispatchStack uses stampWithPeriod on multi-period")
    text = (REPO / "frontend" / "src" / "pages" / "results" / "DispatchStack.tsx").read_text(encoding="utf-8")
    has_import = "stampWithPeriod" in text
    has_tick = "stampWithPeriod(s, Number(p))" in text
    has_period_label = "period_label" in text
    _step("imports stampWithPeriod", has_import)
    _step("XAxis tickFormatter calls stampWithPeriod", has_tick)
    _step("rows carry period_label field", has_period_label)


def test_live_endpoint_smoke() -> None:
    print("\n[Live smoke] /results/cost_breakdown returns lowercase carrier")
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=6, freq="h"))
    n.snapshot_weightings.loc[:, ["objective", "generators", "stores"]] = 1500.0
    n.add("Bus", "B1")
    n.add("Load", "L1", bus="B1", p_set=100.0)
    n.add("Generator", "thermal", bus="B1", carrier="GAS",  # title-cased
          p_nom=200.0, marginal_cost=50.0, capital_cost=1000.0)
    n.optimize(solver_name="highs")
    PyPSAService.set_network(n)
    sim_router._state["solver_config"] = SolverConfig()
    res = results_router.get_cost_breakdown()
    by_c = res.get("by_carrier", []) if isinstance(res, dict) else []
    carriers = {r.get("carrier") for r in by_c}
    _step("by_carrier rows are lowercase",
          all(c == c.lower() for c in carriers if c),
          f"got {carriers}")


def main() -> int:
    for label, fn in [
        ("C1", test_c1_lost_load_voll),
        ("C2", test_c2_lines_table_s_nom_opt),
        ("C3", test_c3_line_duals_voll_bound),
        ("C4", test_c4_carrier_lowercase),
        ("C5", test_c5_emissions_banner),
        ("C6", test_c6_economics_charge_mwh_threaded),
        ("C7", test_c7_prices_fallback_banner),
        ("C8", test_c8_dispatch_stamp_with_period),
        ("Live", test_live_endpoint_smoke),
    ]:
        try:
            fn()
        except Exception as e:
            _crashed("{label}", e)
    print()
    print("=" * 60)
    print(f"Total: {PASS_COUNT + FAIL_COUNT}  Pass: {PASS_COUNT}  Fail: {FAIL_COUNT}")
    print("=" * 60)
    return 1 if FAIL_COUNT > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
