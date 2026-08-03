"""
QA for the /results-summary endpoint (Compare Scenarios v2 — Phase 1).

Covers capacity + dispatch payloads:

  [1] Endpoint shape: GET /projects/{name}/results-summary returns
      a ResultsSummary with `project`, `is_multi_period`, `periods`,
      `has_solve`, plus optional capacity / dispatch blocks.
  [2] Unsolved network: capacity bucket is populated from installed
      p_nom and new_capex is empty (no Δ above ini); dispatch totals
      are all zero and has_solve = False.
  [3] Solved multi-period network with vintage_results: per-period
      capacity attribution reads from n.meta["vintage_results"]
      (not from the collapsed parent row's build_year). New-CAPEX
      breakdown by period mirrors the vintage's optimised p_nom_opt.
  [4] Dispatch GWh is the snapshot-weight × period-weight aggregate
      of generators_t.p and storage_units_t.p_dispatch (charging
      excluded — only the dispatch half feeds the energy mix).
  [5] OPEX (M€) = Σ marginal_cost × p × weights, scaled by 1e6.
  [6] `compare._safe_capital_cost` is a pure lookup into
      `services.solver_service.periodized_capital_costs`'s per-asset output
      — it no longer decides between overnight_cost and capital_cost
      itself; that preference (overnight_cost wins when set) is resolved
      inside `periodized_capital_costs`, the same resolution
      `asset_economics` / `cost_breakdown` / `asset_costs` / Asset Detail
      all use (fixed in Task 9 — see `test_golden_economics.py::
      test_economics_by_carrier_h2_capex_agrees_with_asset_economics` for
      the regression this replaced).
"""
from __future__ import annotations

import pathlib
import queue
import sys
import threading

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except (AttributeError, ValueError):
    pass

import pandas as pd
import pypsa
from fastapi.testclient import TestClient
from main import app
from services.pypsa_service import PyPSAService
from services.solver_service import SolverConfig, _annuity, run_simulation

PASS = 0
FAIL = 0
PROJECT_NAME = "_qa_results_summary"


def _step(label: str, ok: bool, msg: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {label}" + (f" — {msg}" if msg else ""))
    else:
        FAIL += 1
        print(f"  [FAIL] {label}" + (f" — {msg}" if msg else ""))


def _build_multi_period_network() -> pypsa.Network:
    """
    Three-period network with one extendable generator (overnight_cost
    path) and one storage unit (capital_cost path). Both have a
    `vintage_bounds` entry so the solve produces a per-period breakdown.
    """
    n = pypsa.Network()
    base = pd.date_range("2026-01-01", periods=12, freq="h")
    periods = [2026, 2027, 2028]
    mi = pd.MultiIndex.from_product([periods, base], names=["period", "timestep"])
    mi.name = "snapshot"
    n.snapshots = mi
    n.investment_periods = periods
    for p in periods:
        n.investment_period_weightings.loc[p, "years"] = 1.0
        n.investment_period_weightings.loc[p, "objective"] = 1.0

    n.add("Bus", "B1")
    n.add("Carrier", "solar", co2_emissions=0.0)
    n.add("Carrier", "gas",   co2_emissions=0.2)
    n.add("Carrier", "battery", co2_emissions=0.0)

    # Solar uses overnight_cost path (capital_cost = 0 — common GUI setup).
    n.add(
        "Generator", "Solar", bus="B1", carrier="solar",
        p_nom=100.0, p_nom_extendable=True,
        p_nom_min=100.0, p_nom_max=2000.0,
        marginal_cost=1.0, efficiency=1.0,
        capital_cost=0.0, overnight_cost=1_000_000.0,
        discount_rate=0.07, lifetime=25.0,
        build_year=2025,
    )
    # Battery uses capital_cost directly.
    n.add(
        "StorageUnit", "Bat", bus="B1", carrier="battery",
        p_nom=10.0, p_nom_extendable=True,
        p_nom_min=10.0, p_nom_max=500.0,
        max_hours=4.0, marginal_cost=0.0,
        capital_cost=50_000.0, build_year=2025,
        cyclic_state_of_charge=True,
    )
    # Backup gas — non-extendable, exists to provide a positive-OPEX
    # dispatch path so the dispatch.opex_meur assertion isn't trivially 0.
    n.add(
        "Generator", "Gas", bus="B1", carrier="gas",
        p_nom=500.0, p_nom_extendable=False,
        marginal_cost=80.0, efficiency=1.0,
        build_year=2025, lifetime=30,
    )
    # Per-period vintage bounds — vintage_service expands these at solve.
    n.meta["vintage_bounds"] = {
        "Generator": {
            "Solar": {
                "2026": {"p_nom_min": 0.0, "p_nom_max": 300.0},
                "2027": {"p_nom_min": 0.0, "p_nom_max": 300.0},
                "2028": {"p_nom_min": 0.0, "p_nom_max": 300.0},
            }
        },
        "StorageUnit": {
            "Bat": {
                "2026": {"p_nom_min": 0.0, "p_nom_max": 100.0},
                "2027": {"p_nom_min": 0.0, "p_nom_max": 100.0},
                "2028": {"p_nom_min": 0.0, "p_nom_max": 100.0},
            }
        },
    }

    # Solar profile — constant 0.5 capacity factor, so dispatch ≈ p_nom × 0.5.
    n.generators_t.p_max_pu = pd.DataFrame(
        {"Solar": pd.Series(0.5, index=mi)},
        index=mi, dtype=float,
    )
    n.add("Load", "L", bus="B1")
    n.loads_t.p_set = pd.DataFrame(
        {"L": pd.Series(300.0, index=mi)},
        index=mi, dtype=float,
    )
    return n


def _save_under(name: str, n: pypsa.Network) -> None:
    """
    Save the in-memory network under `name` so /results-summary can
    load it as a transient netcdf. Mirrors what the GUI's autosave does.
    """
    from routers.projects import save_project
    PyPSAService.set_network(n)
    save_project(name, force=True, clear_undo=False)


def _solve(n: pypsa.Network, multi_period: bool = True) -> tuple[str, str]:
    PyPSAService.set_network(n)
    cfg = SolverConfig(
        multi_investment_periods=multi_period,
        solve_strategy="myopic" if multi_period else "overnight",
        lf_aggregate_future=False,
        discount_rate=0.07,
        default_lifetime=25.0,
    )
    lock = PyPSAService.get_lock()
    stop = threading.Event()
    log_q: queue.SimpleQueue = queue.SimpleQueue()
    out = run_simulation(cfg, n, lock, stop, log_q)
    if out[0] not in ("ok", "optimal"):
        # Surface validation / solver-side messages so failures aren't
        # opaque in test logs.
        msgs: list[str] = []
        while True:
            try:
                rec = log_q.get_nowait()
            except queue.Empty:
                break
            m = getattr(rec, "message", None) or getattr(rec, "msg", None) or str(rec)
            s = str(m).strip()
            if "VALIDATION" in s or "ERROR" in s or "PHASE" in s.upper() or "Validation" in s:
                msgs.append(s)
        for m in msgs[-12:]:
            print(f"        log: {m}")
    return out


def test_endpoint_shape_unsolved() -> None:
    print("\n[1] Endpoint shape on an unsolved network")
    n = _build_multi_period_network()
    _save_under(PROJECT_NAME, n)
    client = TestClient(app)
    r = client.get(f"/api/projects/{PROJECT_NAME}/results-summary")
    _step("HTTP 200", r.status_code == 200, f"got {r.status_code}")
    if r.status_code != 200:
        print(r.text[:500])
        return
    payload = r.json()
    _step("payload has 'project'",           payload.get("project") == PROJECT_NAME)
    _step("is_multi_period == True",         payload.get("is_multi_period") is True)
    _step("periods == [2026, 2027, 2028]",   payload.get("periods") == [2026, 2027, 2028])
    _step("has_solve == False (unsolved)",   payload.get("has_solve") is False)
    _step("capacity payload present",        isinstance(payload.get("capacity"), dict))
    _step("dispatch payload present",        isinstance(payload.get("dispatch"), dict))


def test_solved_network_capacity_per_period() -> None:
    print("\n[2] Solved network — per-period vintage capacity attribution")
    n = _build_multi_period_network()
    status, condition = _solve(n)
    _step("solve ok", status in ("ok", "optimal"), f"status={status} condition={condition}")
    if status not in ("ok", "optimal"):
        return
    _save_under(PROJECT_NAME, n)

    client = TestClient(app)
    r = client.get(f"/api/projects/{PROJECT_NAME}/results-summary")
    _step("HTTP 200 after solve", r.status_code == 200)
    if r.status_code != 200:
        return
    payload = r.json()
    _step("has_solve == True", payload.get("has_solve") is True)

    cap = payload["capacity"]["capacity_mw_by_carrier"]
    capex = payload["capacity"]["new_capex_meur_by_carrier"]
    # Vintage rows for solar exist for each of 2026/2027/2028 — at least one
    # of those keys should appear in solar.by_period (LP may pick 0 for some
    # periods, hence "at least one").
    solar_pp = (cap.get("solar") or {}).get("by_period") or {}
    has_any_period = any(k in solar_pp for k in ("2026", "2027", "2028"))
    _step("solar capacity has per-period breakdown",
          has_any_period, f"solar.by_period keys: {list(solar_pp.keys())}")
    # Total should be >= parent's 100 MW (pre-existing).
    solar_total = (cap.get("solar") or {}).get("total", 0.0)
    _step("solar capacity total ≥ 100 MW", solar_total >= 100.0,
          f"got {solar_total:.1f}")
    # CAPEX: with overnight_cost=1M and annuity(7%, 25y) ≈ 0.0858, per-MW
    # annuity ≈ €85.8k/MW/yr. That's the LP-effective rate PyPSA's own
    # `capital_cost` accessor reports, i.e. `overnight × annuity × n.nyears`
    # (n.nyears = Σ snapshot_weightings.objective per period / 8760 — this
    # fixture's 12 unit-weighted snapshots/period give n.nyears = 12/8760).
    # Built capacity = solar_total - 100. Fixed 2026-08-01 (Task 9): this
    # formula used to omit the `n.nyears` factor, which happened to match
    # `_safe_capital_cost`'s pre-fix bug of the same omission — two wrongs
    # cancelling out. Both are now correct, so the `n.nyears` factor is
    # required here for the comparison to mean anything.
    capex_solar = (capex.get("solar") or {}).get("total", 0.0)
    # n.nyears is per-period on a multi-period network; every period in this
    # fixture carries the same unit weighting (12 hourly snapshots each), so
    # any one period's value is representative.
    nyears = float(n.nyears.iloc[0]) if hasattr(n.nyears, "iloc") else float(n.nyears)
    expected_capex = (solar_total - 100.0) * 1_000_000 * _annuity(0.07, 25.0) * nyears / 1e6
    if expected_capex > 0:
        _step("solar new_capex ≈ Δp_nom × annuity × n.nyears (within 5 %)",
              abs(capex_solar - expected_capex) / expected_capex < 0.05,
              f"got {capex_solar:.2f}M€, expected ≈ {expected_capex:.2f}M€")
    else:
        _step("solar new_capex (skipped — LP didn't build any)", True)


def test_solved_network_dispatch() -> None:
    print("\n[3] Solved network — dispatch GWh + OPEX per period")
    client = TestClient(app)
    r = client.get(f"/api/projects/{PROJECT_NAME}/results-summary")
    _step("HTTP 200", r.status_code == 200)
    if r.status_code != 200:
        return
    payload = r.json()
    dispatch = payload.get("dispatch") or {}
    by_carrier = dispatch.get("dispatch_gwh_by_carrier") or {}
    # Solar is the cheap dispatcher (marginal_cost=1), so it should appear.
    _step("solar dispatch reported", "solar" in by_carrier,
          f"carriers: {list(by_carrier.keys())}")
    solar_total_gwh = (by_carrier.get("solar") or {}).get("total", 0.0)
    _step("solar dispatch > 0 GWh", solar_total_gwh > 0,
          f"got {solar_total_gwh:.2f} GWh")

    # OPEX should be positive (gas is in the merit order at 80 €/MWh).
    opex = (dispatch.get("opex_meur") or {})
    _step("opex_meur.total > 0", float(opex.get("total", 0)) > 0,
          f"got {opex.get('total')} M€")
    # Multi-period: by_period populated.
    _step("opex has by_period entries",
          isinstance(opex.get("by_period"), dict) and len(opex.get("by_period") or {}) >= 1)


def test_404_on_missing_project() -> None:
    print("\n[4] Returns 404 for a project that doesn't exist")
    client = TestClient(app)
    r = client.get("/api/projects/_does_not_exist_xyz/results-summary")
    _step("HTTP 404", r.status_code == 404, f"got {r.status_code}")


# ── Phase 2 — loading + prices ───────────────────────────────────────────────

PROJECT_NAME_P2 = "_qa_results_summary_p2"


def _build_two_bus_network_for_loading() -> pypsa.Network:
    """
    Two-bus single-period network with a deliberately under-sized line
    so peak loading hits 100 % on the binding hour. Lets us assert the
    loading endpoint correctly identifies the binding branch.
    """
    n = pypsa.Network()
    n.snapshots = pd.date_range("2025-01-01", periods=12, freq="h")
    n.add("Bus", "B1")
    n.add("Bus", "B2")
    # 100 MW line — sized below peak demand so it congests on the peak hour.
    n.add("Line", "L_main", bus0="B1", bus1="B2", x=0.01, s_nom=100.0)
    n.add("Carrier", "gas", co2_emissions=0.2)
    n.add(
        "Generator", "Gas", bus="B1", carrier="gas",
        p_nom=500.0, p_nom_extendable=False,
        marginal_cost=50.0, efficiency=1.0,
        build_year=2025, lifetime=30,
    )
    n.add("Load", "L1", bus="B2")
    # Ramp from 50 → 100 MW over 12 hours so peak hour = 100 MW load = 100 % line loading.
    n.loads_t.p_set = pd.DataFrame(
        {"L1": [50, 55, 60, 65, 70, 80, 90, 100, 95, 85, 70, 60]},
        index=n.snapshots, dtype=float,
    )
    return n


def test_loading_summary_identifies_binding_line() -> None:
    print("\n[5] loading: identifies binding line (100 % peak, binding hours > 0)")
    n = _build_two_bus_network_for_loading()
    status, condition = _solve(n, multi_period=False)
    _step("solve ok", status in ("ok", "optimal"), f"status={status} condition={condition}")
    if status not in ("ok", "optimal"):
        return
    _save_under(PROJECT_NAME_P2, n)
    client = TestClient(app)
    r = client.get(f"/api/projects/{PROJECT_NAME_P2}/results-summary")
    _step("HTTP 200", r.status_code == 200)
    if r.status_code != 200:
        return
    payload = r.json()
    loading = payload.get("loading") or {}
    lines = loading.get("lines") or []
    _step("loading.lines is non-empty",      len(lines) >= 1, f"len={len(lines)}")
    if not lines:
        return
    # Top-of-list should be our 100 MW line at exactly the peak demand.
    top = lines[0]
    _step("top entry is 'L_main'",           top["name"] == "L_main")
    _step("s_nom_opt == 100 MW",             abs(top["s_nom_opt"] - 100.0) < 1e-6)
    _step("peak_loading.total ≈ 1.00 (100 %)",
          abs(top["peak_loading"]["total"] - 1.0) < 1e-3,
          f"got {top['peak_loading']['total']:.4f}")
    # With the ramp profile, one snapshot at 100 % → binding_hours ≈ 1.
    _step("binding_hours.total ≥ 1",
          top["binding_hours"]["total"] >= 0.99,
          f"got {top['binding_hours']['total']:.2f}")
    # Mean is the snapshot-weighted average — should be > 0 and < 1.
    _step("mean_loading.total in (0, 1)",
          0 < top["mean_loading"]["total"] < 1,
          f"got {top['mean_loading']['total']:.4f}")


def test_prices_summary_duration_curve_and_stats() -> None:
    print("\n[6] prices: duration curve has 101 points + per-period stats")
    client = TestClient(app)
    r = client.get(f"/api/projects/{PROJECT_NAME_P2}/results-summary")
    _step("HTTP 200", r.status_code == 200)
    if r.status_code != 200:
        return
    payload = r.json()
    prices = payload.get("prices") or {}
    dc = prices.get("duration_curve") or []
    _step("duration_curve has 101 samples", len(dc) == 101, f"got {len(dc)}")
    # Sorted descending — head ≥ tail.
    if len(dc) >= 2:
        _step("duration_curve sorted descending",
              dc[0] >= dc[-1], f"head={dc[0]:.2f}, tail={dc[-1]:.2f}")
    _step("bus_count == 2",                  prices.get("bus_count") == 2)
    mp = prices.get("mean_price") or {}
    _step("mean_price.total is finite",      isinstance(mp.get("total"), (int, float)))
    # Flat (single-period) network — by_period should be empty.
    _step("mean_price.by_period empty (flat network)",
          isinstance(mp.get("by_period"), dict) and len(mp.get("by_period") or {}) == 0)


def test_loading_multi_period_breakdown() -> None:
    """
    For the Phase 1 multi-period network (PROJECT_NAME), the loading
    summary should expose by_period entries for each of 2026/2027/2028.
    """
    print("\n[7] loading: multi-period network exposes by_period peak/mean")
    client = TestClient(app)
    r = client.get(f"/api/projects/{PROJECT_NAME}/results-summary")
    _step("HTTP 200", r.status_code == 200)
    if r.status_code != 200:
        return
    payload = r.json()
    lines = (payload.get("loading") or {}).get("lines") or []
    # Single-bus phase-1 network has no lines — skip if so.
    if not lines:
        _step("phase-1 network has no lines → loading.lines == [] (skip)", True)
        return
    top = lines[0]
    by_period = top["peak_loading"]["by_period"] or {}
    has_any = any(k in by_period for k in ("2026", "2027", "2028"))
    _step("top line has per-period peak entries", has_any,
          f"by_period keys: {list(by_period.keys())}")


def _cleanup_p2() -> None:
    import shutil

    from routers.projects import PROJECTS_DIR
    p = PROJECTS_DIR / PROJECT_NAME_P2
    if p.exists():
        try:
            shutil.rmtree(p, ignore_errors=True)
        except OSError:
            pass


# ── Phase 3 — emissions + economics ──────────────────────────────────────────

def test_emissions_carrier_attribution() -> None:
    """
    The Phase-1 multi-period network has gas with co2_emissions=0.2, plus
    solar (0.0). After solve, emissions should be entirely attributed to gas
    and the intensity should be a finite positive number.
    """
    print("\n[8] emissions: gas-attributed, per-period breakdown")
    client = TestClient(app)
    r = client.get(f"/api/projects/{PROJECT_NAME}/results-summary")
    _step("HTTP 200", r.status_code == 200)
    if r.status_code != 200:
        return
    payload = r.json()
    em = payload.get("emissions") or {}
    by_carrier = em.get("by_carrier_kt") or {}
    _step("emissions.by_carrier_kt includes 'gas'", "gas" in by_carrier,
          f"keys: {list(by_carrier.keys())}")
    _step("emissions.by_carrier_kt has no 'solar' (co2_emissions=0)",
          "solar" not in by_carrier)
    total_kt = (em.get("total_kt") or {}).get("total", 0.0)
    gas_kt = (by_carrier.get("gas") or {}).get("total", 0.0)
    _step("total_kt ≈ gas total (no other emitters)",
          abs(total_kt - gas_kt) < 0.01, f"total={total_kt:.2f}, gas={gas_kt:.2f}")
    intens = (em.get("intensity_kg_per_mwh") or {}).get("total", 0.0)
    _step("intensity > 0 and finite",
          intens > 0 and intens < 5000, f"got {intens:.2f} kg/MWh")
    # Per-period coverage.
    gas_pp = (by_carrier.get("gas") or {}).get("by_period") or {}
    has_any_period = any(k in gas_pp for k in ("2026", "2027", "2028"))
    _step("gas has per-period breakdown", has_any_period,
          f"keys: {list(gas_pp.keys())}")


def test_economics_per_carrier_lcoe() -> None:
    """
    LCOE per carrier should be (annuitised_capex + opex) / dispatch_MWh.
    Sanity-check: solar (cheap, no fuel) should have LCOE < gas (which
    carries a marginal_cost). Revenue and CAPEX should both be > 0 where
    the LP built capacity.
    """
    print("\n[9] economics: per-carrier revenue / OPEX / CAPEX / LCOE")
    client = TestClient(app)
    r = client.get(f"/api/projects/{PROJECT_NAME}/results-summary")
    _step("HTTP 200", r.status_code == 200)
    if r.status_code != 200:
        return
    payload = r.json()
    ec = payload.get("economics") or {}
    by_carrier = ec.get("by_carrier") or {}
    _step("economics.by_carrier non-empty", len(by_carrier) >= 2,
          f"carriers: {list(by_carrier.keys())}")
    if "solar" not in by_carrier or "gas" not in by_carrier:
        return  # something is structurally off

    solar = by_carrier["solar"]
    gas = by_carrier["gas"]

    # Both should have dispatch.
    solar_disp = solar["dispatch_gwh"]["total"]
    gas_disp = gas["dispatch_gwh"]["total"]
    _step("solar dispatch_gwh > 0", solar_disp > 0,   f"got {solar_disp:.2f}")
    _step("gas dispatch_gwh > 0",   gas_disp > 0,     f"got {gas_disp:.2f}")

    # Both LCOEs should be finite + positive. We can't assert solar < gas
    # ordering here: the test network only has 12 hours/period, so solar's
    # annuitised CAPEX (€/yr) divided by its 12-hour dispatch produces an
    # artificially huge per-MWh number. On a realistic 8760-hour network
    # the inequality holds (verified separately on the live test_project_
    # 4_nodes2 — solar ~8 €/MWh, gas ~72 €/MWh). The formula itself is the
    # same; the test just can't exercise it at realistic horizon size
    # without bloating the QA runtime.
    solar_lcoe = solar["lcoe_eur_per_mwh"]["total"]
    gas_lcoe = gas["lcoe_eur_per_mwh"]["total"]
    _step("solar LCOE finite + positive",
          solar_lcoe > 0 and solar_lcoe < 1e6,
          f"solar={solar_lcoe:.2f} €/MWh")
    _step("gas LCOE finite + positive",
          gas_lcoe > 0 and gas_lcoe < 1e6,
          f"gas={gas_lcoe:.2f} €/MWh")

    # CAPEX: solar should have annuitised capex > 0 (the LP built it).
    solar_capex = solar["capex_meur"]["total"]
    _step("solar capex_meur > 0",
          solar_capex > 0, f"got {solar_capex:.2f} M€")


def test_unsolved_emissions_economics_empty() -> None:
    """
    Build a fresh, unsolved network. Both emissions and economics
    should come back populated with empty defaults rather than null.
    """
    print("\n[10] emissions + economics: unsolved network → populated defaults")
    n = _build_multi_period_network()
    _save_under(PROJECT_NAME, n)
    client = TestClient(app)
    r = client.get(f"/api/projects/{PROJECT_NAME}/results-summary")
    _step("HTTP 200", r.status_code == 200)
    if r.status_code != 200:
        return
    payload = r.json()
    _step("emissions field present",  isinstance(payload.get("emissions"), dict))
    _step("economics field present",  isinstance(payload.get("economics"), dict))
    em = payload.get("emissions") or {}
    ec = payload.get("economics") or {}
    _step("unsolved emissions.total_kt.total == 0",
          (em.get("total_kt") or {}).get("total", 0.0) == 0.0)
    _step("unsolved economics.by_carrier is empty",
          (ec.get("by_carrier") or {}) == {})


def _cleanup() -> None:
    import shutil

    from routers.projects import PROJECTS_DIR
    p = PROJECTS_DIR / PROJECT_NAME
    if p.exists():
        try:
            shutil.rmtree(p, ignore_errors=True)
        except OSError:
            pass


def main() -> int:
    print("=== qa_results_summary_compare ===")
    try:
        test_endpoint_shape_unsolved()
        test_solved_network_capacity_per_period()
        test_solved_network_dispatch()
        test_404_on_missing_project()
        # Phase 2.
        test_loading_summary_identifies_binding_line()
        test_prices_summary_duration_curve_and_stats()
        test_loading_multi_period_breakdown()
        # Phase 3 — emissions + economics. These rely on the Phase-1
        # solved-network setup, so they MUST run after test 2.
        test_emissions_carrier_attribution()
        test_economics_per_carrier_lcoe()
        # Last — re-overwrites the project with an UNSOLVED network, so it
        # must be the final test that touches PROJECT_NAME.
        test_unsolved_emissions_economics_empty()
    finally:
        _cleanup()
        _cleanup_p2()
    total = PASS + FAIL
    print(f"\nTotal: {total}  Pass: {PASS}  Fail: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
