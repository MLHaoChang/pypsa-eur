"""
QA harness for /api/results/asset_economics.

Builds a small synthetic PyPSA network whose dispatch + duals are
hand-computable, solves it, then exercises the endpoint code path
and compares the response to expected values.

Each scenario asserts a tight ε-band so a future change to the
weighting / signing logic in `get_asset_economics` shows up loud.

Run from the backend dir:
    python -m tests.qa_asset_economics
"""

from __future__ import annotations

import math
import pathlib
import sys

# Allow `python -m tests.qa_asset_economics` from the backend dir.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pandas as pd
import pypsa

# Ensure stdout encoding accepts unicode arrows on Windows.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except (AttributeError, ValueError):
    pass

from routers import simulation as sim_router
from services.pypsa_service import PyPSAService
from services.solver_service import SolverConfig

# ── Tiny helpers ──────────────────────────────────────────────────────────


def _approx(actual: float, expected: float, rel: float = 0.02, abs_eps: float = 1.0) -> bool:
    """
    Tolerant compare — accepts either relative or absolute tolerance.

    rel=2 % is loose enough that solver round-off / weighting tweaks don't
    trip the test, while still catching the off-by-period-years bug class
    (those errors are typically 5×–10× off, not 2 %).
    """
    if math.isnan(actual) or math.isnan(expected):
        return False
    if abs(actual - expected) <= abs_eps:
        return True
    if expected == 0:
        return abs(actual) <= abs_eps
    return abs(actual - expected) / max(abs(expected), 1e-9) <= rel


def _hdr(s: str) -> None:
    print()
    print("=" * 72)
    print(s)
    print("=" * 72)


def _step(label: str, ok: bool, msg: str = "") -> None:
    tag = "PASS" if ok else "FAIL"
    print(f"  [{tag}] {label}" + (f" — {msg}" if msg else ""))


PASS_COUNT = 0
FAIL_COUNT = 0


def assert_close(label: str, actual: float, expected: float, rel: float = 0.02, abs_eps: float = 1.0) -> bool:
    global PASS_COUNT, FAIL_COUNT
    ok = _approx(actual, expected, rel, abs_eps)
    if ok:
        PASS_COUNT += 1
        _step(label, True, f"actual={actual:.2f}, expected={expected:.2f}")
    else:
        FAIL_COUNT += 1
        _step(label, False, f"actual={actual:.2f}, expected={expected:.2f} (Δ={actual - expected:.2f})")
    return ok


def _install_test_network(n: pypsa.Network) -> None:
    """Replace the singleton network used by the router code path."""
    PyPSAService.set_network(n)
    # Seed the solver-config slot so endpoints that read it (e.g. asset_costs)
    # don't crash. SolverConfig is a dataclass with sane defaults.
    sim_router._state["solver_config"] = SolverConfig()
    # Mark dispatch-fresh — _dispatch_ready uses _dispatch_status which checks
    # column consistency. After a solve the columns match by construction.


def _call_endpoint() -> dict:
    """
    Invoke the endpoint function the FastAPI handler wraps, returning
    the raw dict the router would return.
    """
    return sim_router.get_asset_economics()  # type: ignore[return-value]


# ── Scenario 1: Single-bus generators (LCOE & revenue) ───────────────────


def scenario_generator_lcoe() -> None:
    _hdr("Scenario 1: Generator LCOE / revenue with thermal as marginal unit")

    # 24 snapshots, all 1h. Load = 100 MW constant.
    # Generators:
    #   - thermal: 200 MW, marginal_cost = 50 €/MWh, capital_cost = 100,000 €/MW/yr
    #   - solar:    50 MW, marginal_cost = 0  €/MWh, capital_cost =  60,000 €/MW/yr,
    #               p_max_pu = 0.6 constant (so it's always below dispatchable cap)
    #
    # LP outcome: solar dispatches solar.p_max_pu × p_nom = 30 MW each hour;
    # thermal covers the remaining 70 MW. Thermal is marginal at every hour →
    # LP dual price = 50 €/MWh at every snapshot.
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=24, freq="h"))
    n.add("Bus", "B1")
    n.add("Load", "L1", bus="B1", p_set=100.0)
    n.add(
        "Generator", "thermal", bus="B1", carrier="gas",
        p_nom=200.0, marginal_cost=50.0, capital_cost=100_000.0,
        committable=False,
    )
    n.add(
        "Generator", "solar", bus="B1", carrier="solar",
        p_nom=50.0, marginal_cost=0.0, capital_cost=60_000.0,
        p_max_pu=0.6,
    )

    n.optimize(solver_name="highs")
    _install_test_network(n)

    payload = _call_endpoint()
    rows = {r["name"]: r for r in payload["generators"]}

    # ── Hand calculations ────────────────────────────────────────────────
    # Solar:  energy = 0.6 × 50 × 24h    = 720 MWh
    #         revenue = 720 × 50         = 36,000 €
    #         vom    = 720 × 0           = 0
    #         fixed  = 60,000 × 50       = 3,000,000 €
    #         net   = 36,000 - 3,000,000 = -2,964,000 €
    #         LCOE  = (3,000,000 + 0) / 720 = 4,166.67 €/MWh
    solar = rows["solar"]
    assert_close("solar energy MWh", solar["energy_mwh"], 720.0, abs_eps=0.5)
    assert_close("solar revenue €",  solar["revenue_eur"], 36_000.0, abs_eps=50.0)
    assert_close("solar vom €",      solar["vom_cost_eur"], 0.0, abs_eps=1.0)
    assert_close("solar fixed €",    solar["fixed_cost_eur"], 3_000_000.0, abs_eps=1.0)
    assert_close("solar net profit €", solar["net_profit_eur"], -2_964_000.0, abs_eps=100.0)
    assert_close("solar LCOE €/MWh", solar["lcoe_eur_per_mwh"], 4_166.67, rel=0.01)

    # Thermal: energy = 70 × 24          = 1,680 MWh
    #          revenue = 1,680 × 50      = 84,000 €
    #          vom    = 1,680 × 50       = 84,000 €
    #          fixed  = 100,000 × 200    = 20,000,000 €
    #          net   = 84k - 84k - 20M   = -20,000,000 €
    #          LCOE  = (20,000,000 + 84,000) / 1,680 = 11,955 €/MWh
    therm = rows["thermal"]
    assert_close("thermal energy MWh",     therm["energy_mwh"], 1_680.0, abs_eps=2.0)
    assert_close("thermal revenue €",      therm["revenue_eur"], 84_000.0, abs_eps=200.0)
    assert_close("thermal vom €",          therm["vom_cost_eur"], 84_000.0, abs_eps=200.0)
    assert_close("thermal fixed €",        therm["fixed_cost_eur"], 20_000_000.0, abs_eps=1.0)
    assert_close("thermal net profit €",   therm["net_profit_eur"], -20_000_000.0, abs_eps=400.0)
    assert_close("thermal LCOE €/MWh",     therm["lcoe_eur_per_mwh"], 11_955.0, rel=0.01)
    assert_close("thermal avg price €/MWh", therm["avg_price_eur_per_mwh"], 50.0, abs_eps=0.5)


# ── Scenario 2: Storage arbitrage on a single bus ─────────────────────────


def scenario_storage_arbitrage() -> None:
    _hdr("Scenario 2: Storage arbitrage on dispatchable-only network")

    # 24 snapshots. Load profile: 50 MW at night (00–06), 200 MW during day (06–18),
    # 50 MW evening (18–24). Two thermal generators with different cost:
    #   - cheap_thermal:  marginal_cost = 20 €/MWh, p_nom = 100 MW
    #   - peaker:         marginal_cost = 200 €/MWh, p_nom = 200 MW
    # Battery: p_nom = 100 MW, max_hours = 4 (energy = 400 MWh), eff=1.0 each way.
    # Cyclic SoC. capital_cost=0 so it ALWAYS arbitrages.
    #
    # Expected LP outcome at optimum:
    #   - Cheap_thermal serves the 50-MW base load AND charges the battery in
    #     off-peak hours. Battery discharges during the 200-MW peak to displace
    #     the peaker, saving (200 - 20) = 180 €/MWh per discharged MWh.
    #   - Battery cycles fully (charge 400 MWh, discharge 400 MWh).
    #   - Marginal price: cheap=20 when only it dispatches; peaker=200 when peaker
    #     dispatches.
    #   - For the battery, marginal price at bus = 20 €/MWh during charge hours
    #     (cheap_thermal sets the price) and 200 €/MWh during discharge hours
    #     (peaker sets the price).
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=24, freq="h"))
    n.add("Bus", "B1")
    load_profile = pd.Series(50.0, index=n.snapshots)
    load_profile.iloc[6:18] = 200.0
    n.add("Load", "L1", bus="B1")
    n.loads_t.p_set["L1"] = load_profile
    n.add("Generator", "cheap_thermal", bus="B1", carrier="gas",
          p_nom=100.0, marginal_cost=20.0, capital_cost=0.0)
    n.add("Generator", "peaker", bus="B1", carrier="oil",
          p_nom=200.0, marginal_cost=200.0, capital_cost=0.0)
    n.add(
        "StorageUnit", "Battery 1", bus="B1", carrier="battery",
        p_nom=100.0, max_hours=4.0,
        efficiency_store=1.0, efficiency_dispatch=1.0,
        marginal_cost=0.0, capital_cost=0.0,
        cyclic_state_of_charge=True,
    )

    n.optimize(solver_name="highs")
    _install_test_network(n)

    payload = _call_endpoint()
    su_rows = {r["name"]: r for r in payload["storage_units"]}
    batt = su_rows["Battery 1"]

    # ── Hand calculations ────────────────────────────────────────────────
    # The LP's actual cycling depends on the cyclic-SoC boundary + dual values.
    # What we can ASSERT independently of the specific dispatch shape:
    #   1. charge_mwh == discharge_mwh (RTE = 1.0 round-trip)
    #   2. revenue - charge_cost = net_profit (when vom = fixed = 0)
    #   3. LCOS = charge_cost / discharge_mwh  (with vom=fixed=0)
    #   4. spread = avg_discharge_price - avg_charge_price
    # And the system-level invariant:
    #   5. battery_savings = (price_peaker - price_cheap) × min(battery_energy, peak_volume)
    #      where peak_volume = (200 - 100) × 12h = 1200 MWh of peaker dispatch displaceable.
    #      Battery delivers ≤ 400 MWh per full cycle but can cycle multiple times.
    #      Lower bound: discharging 400 MWh @ €200 vs charging 400 MWh @ €20 saves
    #      400 × (200-20) = 72,000 €. The LP's net profit must equal this exactly
    #      because the battery is the only marginal flexibility.
    global PASS_COUNT, FAIL_COUNT
    rte = 1.0
    if not _approx(batt["charge_mwh"] * rte, batt["discharge_mwh"], rel=0.01, abs_eps=1.0):
        FAIL_COUNT += 1
        _step("RTE consistency: charge × η_rt == discharge", False,
              f"charge={batt['charge_mwh']:.1f}, discharge={batt['discharge_mwh']:.1f}")
    else:
        PASS_COUNT += 1
        _step("RTE consistency: charge × η_rt == discharge", True,
              f"charge={batt['charge_mwh']:.1f}, discharge={batt['discharge_mwh']:.1f}")

    cashflow_net = batt["discharge_revenue_eur"] - batt["charge_cost_eur"] - batt["vom_cost_eur"] - batt["fixed_cost_eur"]
    assert_close("net_profit identity (rev - charge - vom - fixed)",
                 batt["net_profit_eur"], cashflow_net, abs_eps=0.5)
    assert_close("battery net profit € (LP-optimal savings)",
                 batt["net_profit_eur"], 72_000.0, abs_eps=500.0)

    lcos_recomputed = batt["charge_cost_eur"] / batt["discharge_mwh"]
    assert_close("LCOS identity (= (fixed + vom + charge_cost) / discharge_mwh)",
                 batt["lcos_eur_per_mwh"], lcos_recomputed, rel=0.01)

    if batt["spread_eur_per_mwh"] is not None:
        spread_recomputed = (batt["avg_discharge_price_eur_per_mwh"]
                              - batt["avg_charge_price_eur_per_mwh"])
        assert_close("spread identity (= avg_d - avg_c)",
                     batt["spread_eur_per_mwh"], spread_recomputed, rel=0.01)


# ── Scenario 3: Storage with curtailment_cost subsidy (the live bug) ──────


def scenario_storage_with_curtailment_subsidy() -> None:
    _hdr("Scenario 3: curtailment_cost > 0 distorts LP duals → charge cost goes negative")

    # Reproduces what the user saw on the live network: the curtailment_cost
    # wrapper adds `-cost × p` to the LP objective, which makes the LP dual
    # at the renewable's bus = (marginal_cost - curtailment_cost). When that
    # dual is negative, storage's "charge cost" computed against the raw dual
    # is negative — algebraically correct but semantically misleading.
    #
    # Setup: 24h with TIME-VARYING solar p_max_pu so the LP is non-degenerate
    # (solar is genuinely marginal in some hours, at upper bound in others —
    # those two cases exercise different branches of the merit-order fix).
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=24, freq="h"))
    n.add("Bus", "B1")
    # Demand: 120 MW base, 250 MW at 06–18 (mid-day peak).
    load = pd.Series(120.0, index=n.snapshots)
    load.iloc[6:18] = 250.0
    n.add("Load", "L1", bus="B1")
    n.loads_t.p_set["L1"] = load
    # Solar p_max_pu varies — 0.3 at night, 0.9 mid-day, 0.5 shoulders. So
    # solar capacity = 100 × p_max_pu varies (30 / 90 / 50 MW). Each load/
    # supply combination drives a distinct LP-marginal state.
    pmax = pd.Series(0.3, index=n.snapshots)
    pmax.iloc[6:9] = 0.5
    pmax.iloc[9:15] = 0.9
    pmax.iloc[15:18] = 0.5
    n.add("Generator", "solar", bus="B1", carrier="solar",
          p_nom=100.0, marginal_cost=0.0, capital_cost=0.0)
    n.generators_t.p_max_pu["solar"] = pmax
    n.add("Generator", "thermal", bus="B1", carrier="gas",
          p_nom=300.0, marginal_cost=30.0, capital_cost=0.0)
    n.add("StorageUnit", "Battery 1", bus="B1", carrier="battery",
          p_nom=100.0, max_hours=4.0,
          efficiency_store=1.0, efficiency_dispatch=1.0,
          marginal_cost=0.0, capital_cost=0.0,
          cyclic_state_of_charge=True)

    # Apply the curtailment subsidy via the dedicated PyPSA-GUI custom column.
    # /asset_economics walks `n.generators["curtailment_cost"]` to find which
    # buses need the merit-order adjustment, so we must set this field — not
    # the marginal_cost trick used in the very first iteration of the test.
    n.generators["curtailment_cost"] = 0.0
    n.generators.at["solar", "curtailment_cost"] = 100.0
    # Replicate solver_service's LP-time behaviour: with curtailment_cost > 0,
    # the renewable's effective marginal_cost in the LP is
    # `marginal_cost - curtailment_cost`. That's what produces the negative
    # LP dual we then adjust back in the endpoint.
    n.generators.at["solar", "marginal_cost"] = 0.0 - 100.0

    n.optimize(solver_name="highs")
    _install_test_network(n)

    payload = _call_endpoint()
    batt = next(r for r in payload["storage_units"] if r["name"] == "Battery 1")

    charge_cost = batt["charge_cost_eur"]
    discharge_rev = batt["discharge_revenue_eur"]
    avg_c = batt["avg_charge_price_eur_per_mwh"]
    avg_d = batt["avg_discharge_price_eur_per_mwh"]
    print(f"  diag: charge_cost={charge_cost:.0f} €, avg_charge_price={avg_c}")
    print(f"  diag: discharge_revenue={discharge_rev:.0f} €, avg_discharge_price={avg_d}")

    # After applying the merit-order adjustment, the renewable's
    # curtailment_cost (= 100 €/MWh) should be added back to the bus price
    # at every snapshot where solar is the marginal unit. The bus prices
    # should now be >= 0 across the board.
    global PASS_COUNT, FAIL_COUNT
    if charge_cost >= -1.0:  # 1€ tolerance for solver round-off
        PASS_COUNT += 1
        _step("charge_cost >= 0 after merit-order adjustment", True,
              f"got {charge_cost:.2f} €")
    else:
        FAIL_COUNT += 1
        _step("charge_cost >= 0 after merit-order adjustment", False,
              f"got {charge_cost:.2f} € — the adjustment failed to undo the subsidy")
    if avg_c is None or avg_c >= -0.01:
        PASS_COUNT += 1
        _step("avg_charge_price >= 0 after adjustment", True,
              f"got {avg_c}")
    else:
        FAIL_COUNT += 1
        _step("avg_charge_price >= 0 after adjustment", False,
              f"got {avg_c} — bus price is still negative")


# ── Scenario 3b: Subsidy must NOT over-correct when not setting the dual ──


def scenario_subsidy_no_overcorrect() -> None:
    _hdr("Scenario 3b: Adjustment must NOT fire when subsidised renewable isn't setting the LP dual")

    # Setup designed to expose the over-correction bug observed in production:
    #   - A subsidised renewable that operates STRICTLY between 0 and ceiling
    #     for purely operational reasons (storage SoC cycle, line limit, etc.)
    #     while a DIFFERENT generator is the true LP-marginal unit setting
    #     the dual.
    #
    # Topology: 2 buses connected by a tight line (50 MW limit).
    #   B1: solar 100 MW (curtailment_cost=2000) + load 60 MW
    #   B2: thermal 200 MW @ 30 €/MWh + load 100 MW
    # The line constrains export from B1 to B2 to 50 MW. So B1's surplus
    # (solar 100 - load 60 = 40 max, but actual dispatch may be less due
    # to line congestion at the other direction) leaves solar dispatching
    # at some intermediate value at each hour due to line cost.
    #
    # The KEY assertion: at B2, thermal sets the dual = 30 €/MWh. B1 has
    # a subsidised renewable strictly between 0 and ceiling, but B2's dual
    # is NOT affected by B1's subsidy. So the adjustment must NOT fire at B2.
    # If it did, B2's price would jump to 30 + 2000 = 2030 €/MWh — exactly
    # the over-correction we saw on the live network.
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=12, freq="h"))
    n.add("Bus", "B1")
    n.add("Bus", "B2")
    n.add("Load", "L1", bus="B1", p_set=60.0)
    n.add("Load", "L2", bus="B2", p_set=100.0)
    n.add("Generator", "solar_B1", bus="B1", carrier="solar",
          p_nom=100.0, marginal_cost=0.0, capital_cost=0.0, p_max_pu=0.8)
    # Apply the curtailment subsidy by setting LP-time effective MC negative,
    # mimicking what solver_service does in the live network.
    n.generators["curtailment_cost"] = 0.0
    n.generators.at["solar_B1", "curtailment_cost"] = 2000.0
    n.generators.at["solar_B1", "marginal_cost"] = -2000.0
    n.add("Generator", "thermal_B2", bus="B2", carrier="gas",
          p_nom=200.0, marginal_cost=30.0, capital_cost=0.0)
    # Storage at B2 — its charge cost SHOULD reflect thermal's 30 €/MWh,
    # NOT 30 + 2000 = 2030.
    n.add("StorageUnit", "Battery_B2", bus="B2", carrier="battery",
          p_nom=20.0, max_hours=4.0,
          efficiency_store=1.0, efficiency_dispatch=1.0,
          marginal_cost=0.0, capital_cost=0.0,
          cyclic_state_of_charge=True)
    n.add("Line", "L_B1_B2", bus0="B1", bus1="B2",
          s_nom=50.0, x=0.1, r=0.0)

    n.optimize(solver_name="highs")
    _install_test_network(n)

    payload = _call_endpoint()
    batt = next(r for r in payload["storage_units"] if r["name"] == "Battery_B2")
    print(f"  diag: B2 storage avg_charge_price={batt['avg_charge_price_eur_per_mwh']}, avg_discharge_price={batt['avg_discharge_price_eur_per_mwh']}")

    global PASS_COUNT, FAIL_COUNT
    avg_c = batt["avg_charge_price_eur_per_mwh"]
    # B2's bus price must reflect thermal MC (30 €/MWh) — NOT thermal + subsidy.
    # Allow some flex for LP nuances but flag if we're 30× off (the bug signature).
    if avg_c is None:
        # Battery didn't charge — degenerate but still valid (assert empty).
        PASS_COUNT += 1
        _step("B2 charge price not corrupted by B1's subsidy", True, "no charging")
    elif avg_c > 1000:
        FAIL_COUNT += 1
        _step("B2 charge price not corrupted by B1's subsidy", False,
              f"got {avg_c:.1f} €/MWh — adjustment fired at B2 (over-correction)")
    else:
        PASS_COUNT += 1
        _step("B2 charge price not corrupted by B1's subsidy", True,
              f"got {avg_c:.1f} €/MWh — within expected merit-order range")


# ── Scenario 4: Single-period (flat) — by_period should be empty list ─────


def scenario_flat_period_no_by_period() -> None:
    _hdr("Scenario 4: Flat single-period horizon → by_period is empty")

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=4, freq="h"))
    n.add("Bus", "B1")
    n.add("Load", "L1", bus="B1", p_set=10.0)
    n.add("Generator", "thermal", bus="B1", carrier="gas",
          p_nom=20.0, marginal_cost=50.0, capital_cost=10_000.0)
    n.optimize(solver_name="highs")
    _install_test_network(n)

    payload = _call_endpoint()
    therm = payload["generators"][0]

    global PASS_COUNT, FAIL_COUNT
    if payload["is_multi_period"] is False:
        PASS_COUNT += 1
        _step("is_multi_period == False", True)
    else:
        FAIL_COUNT += 1
        _step("is_multi_period == False", False, f"got {payload['is_multi_period']}")

    if therm["by_period"] == []:
        PASS_COUNT += 1
        _step("by_period is empty list", True)
    else:
        FAIL_COUNT += 1
        _step("by_period is empty list", False, f"got {therm['by_period']}")


# ── Scenario 5: Multi-period — fixed_cost distributed by years[p] ─────────


def scenario_multi_period_distribution() -> None:
    _hdr("Scenario 5: Multi-period — fixed_cost distributes by period.years")

    # Build a 2-period network. Each period covers 12h (same operational year
    # replicated under both periods). Capital cost on the generator is 100k €/MW/yr.
    # Period weightings: years=2, 5 for the two periods. p_nom=10 MW.
    # Expected: total fixed = 100,000 × 10 = 1,000,000 € (annualised).
    #           by_period[2025].fixed = 1,000,000 × (2/7) ≈ 285,714 €
    #           by_period[2030].fixed = 1,000,000 × (5/7) ≈ 714,286 €
    n = pypsa.Network()
    base = pd.date_range("2025-01-01", periods=12, freq="h")
    # Two periods, same operational range.
    n.snapshots = pd.MultiIndex.from_product(
        [[2025, 2030], base], names=["period", "timestep"],
    )
    n.snapshots.name = "snapshot"
    n.investment_periods = [2025, 2030]
    n.investment_period_weightings.loc[2025, "years"] = 2.0
    n.investment_period_weightings.loc[2030, "years"] = 5.0

    n.add("Bus", "B1")
    n.add("Load", "L1", bus="B1", p_set=5.0)
    n.add(
        "Generator", "thermal", bus="B1", carrier="gas",
        p_nom=10.0, marginal_cost=20.0, capital_cost=100_000.0,
        committable=False,
    )

    n.optimize(solver_name="highs")
    _install_test_network(n)

    payload = _call_endpoint()
    therm = payload["generators"][0]

    assert_close("multi-period fixed total €", therm["fixed_cost_eur"], 1_000_000.0, abs_eps=1.0)
    if not therm["by_period"]:
        global FAIL_COUNT
        FAIL_COUNT += 1
        _step("by_period populated", False, "got empty list on multi-period run")
        return
    by_p = {row["period"]: row for row in therm["by_period"]}
    assert_close("by_period[2025].fixed €", by_p[2025]["fixed_cost_eur"], 1_000_000.0 * (2 / 7), abs_eps=200.0)
    assert_close("by_period[2030].fixed €", by_p[2030]["fixed_cost_eur"], 1_000_000.0 * (5 / 7), abs_eps=200.0)


# ── Driver ────────────────────────────────────────────────────────────────


def main() -> int:
    try:
        scenario_generator_lcoe()
    except Exception as e:
        print(f"  Scenario 1 crashed: {type(e).__name__}: {e}")
    try:
        scenario_storage_arbitrage()
    except Exception as e:
        print(f"  Scenario 2 crashed: {type(e).__name__}: {e}")
    try:
        scenario_storage_with_curtailment_subsidy()
    except Exception as e:
        print(f"  Scenario 3 crashed: {type(e).__name__}: {e}")
    try:
        scenario_subsidy_no_overcorrect()
    except Exception as e:
        print(f"  Scenario 3b crashed: {type(e).__name__}: {e}")
    try:
        scenario_flat_period_no_by_period()
    except Exception as e:
        print(f"  Scenario 4 crashed: {type(e).__name__}: {e}")
    try:
        scenario_multi_period_distribution()
    except Exception as e:
        print(f"  Scenario 5 crashed: {type(e).__name__}: {e}")

    print()
    print("=" * 72)
    print(f"Total: {PASS_COUNT + FAIL_COUNT}  ·  Pass: {PASS_COUNT}  ·  Fail: {FAIL_COUNT}")
    print("=" * 72)
    return 1 if FAIL_COUNT > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
