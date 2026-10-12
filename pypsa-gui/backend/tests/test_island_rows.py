"""
I2a of the campus island plan (2026-10-07): ride-through, fuel and bridge
reserve in the hub LP (``services/solver/island.py``).

Every case is a tiny hand network where one row binds, with the answer
worked by hand from the plan. A grid supply serves the load in normal
operation, so storage and gensets are sized by the island rows alone; the
storage is the cheapest thing that satisfies them, so the LP returns the
smallest p_nom the rows allow.

- (a) sustained ride-through: η·e(t−1) ≥ Σ_{τ=t}^{t+D−1} d(τ)·Δ + B(t),
  d(τ) ≥ L_c(τ) − G(τ), 0 ≤ d(τ) ≤ Σ p_nom over the ride-through storage,
  G = Σ p_nom_genset − k·U_max;
- (b) the UPS bridge: η·e_ups(t−1) ≥ P_it(t)·bridge_s/3600;
- (c) fuel: a fuel Store's e_nom ≥ L_c,peak·fuel_autonomy_h/η_genset.
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np
import pandas as pd
import pypsa
import pytest

from gridspine.schema.island import validate_island_config
from services.solver.island import (
    IslandRefusal,
    _wrap_with_island,
    island_extra_functionality,
    strategy_refusal,
)
from services.solver_service import SolverConfig

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_templates as T  # noqa: E402

TOL = 1e-4


def _a(v):
    return {"value": v, "source": "assumed"}


_LIMITS = {"rocof_hz_per_s": _a(1.0), "f_min_hz": _a(47.5), "f_max_hz": _a(51.5),
           "qss_band_hz": _a(1.0)}
_BESS = {"kind": "bess", "control": "gfm_droop", "pf_rated": _a(0.95), "droop_pct": _a(4.0),
         "q_droop_pct": _a(4.0), "tau_f_s": _a(0.1), "i_max_pu": _a(1.2), "k_gfm_min": _a(1.0)}


def _genset(start_s=10.0, sync_s=5.0, **extra):
    return {"kind": "genset", "governor": "droop", "droop_pct": _a(4.0), "q_droop_pct": _a(4.0),
            "H_s": _a(1.5), "T_g_s": _a(0.5), "ramp_pu_per_s": _a(0.2), "start_s": _a(start_s),
            "sync_s": _a(sync_s), "load_step_max_pct": _a(50.0), "cos_phi_r": _a(0.8),
            "xd_sat": _a(2.0), "excitation": "avr_pmg", "neutral_earthing": "solid", **extra}


def _island(*, units=None, sustained_h=4.0, k=None, scenarios=("planned",), rt=None,
            bridge_s=600.0, fuel_h=None):
    units = {"bess": dict(_BESS), "crit": {"kind": "load", "critical": True}} \
        if units is None else units
    req = {"bridge_s": _a(bridge_s), "sustained_h": _a(sustained_h),
           "rocof_window_ms": _a(500.0), "gfm_margin": _a(0.1), "scenarios": list(scenarios),
           "frequency_limits": {"transition": dict(_LIMITS), "steady_island": dict(_LIMITS)}}
    if k is not None:
        req["genset_redundancy_n"] = _a(k)
    if rt is not None:
        req["ride_through_storage"] = list(rt)
    if fuel_h is not None:
        req["fuel_autonomy_h"] = _a(fuel_h)
    return validate_island_config({"units": units, "requirements": req})


def _net(*, crit=10.0, hours=24, freq="1h", weight=None, gensets=(), max_hours=2.0,
         eta=1.0, cyclic=True, soc_init=0.0, bess=True, grid_out=()):
    """One bus: a cheap grid supply, the critical load ``crit`` (MW, scalar
    or per snapshot), fixed gensets [(name, MW)] that never run in normal
    operation, and an extendable BESS ``bess``."""
    n = pypsa.Network()
    sns = pd.date_range("2030-01-01", periods=hours, freq=freq)
    n.set_snapshots(sns)
    step_h = pd.Timedelta(freq).total_seconds() / 3600.0
    n.snapshot_weightings["objective"] = weight if weight is not None else step_h
    n.snapshot_weightings["generators"] = weight if weight is not None else step_h
    n.snapshot_weightings["stores"] = step_h
    n.add("Bus", "site")
    n.add("Load", "crit", bus="site",
          p_set=pd.Series(np.broadcast_to(crit, hours).astype(float), index=sns))
    p_max = pd.Series(1.0, index=sns)
    p_max.iloc[list(grid_out)] = 0.0
    n.add("Generator", "grid", bus="site", p_nom=1000.0, marginal_cost=10.0, p_max_pu=p_max)
    for name, mw in gensets:
        n.add("Generator", name, bus="site", p_nom=mw, marginal_cost=1000.0)
    if bess:
        n.add("StorageUnit", "bess", bus="site", p_nom_extendable=True, capital_cost=100.0,
              max_hours=max_hours, efficiency_dispatch=eta, efficiency_store=1.0,
              cyclic_state_of_charge=cyclic, state_of_charge_initial=soc_init)
    return n


def _solve(n, island, **kw):
    status, cond = n.optimize(solver_name="highs",
                              extra_functionality=island_extra_functionality(island, **kw))
    assert (status, cond) == ("ok", "optimal")
    return n


def _p_nom(n, name="bess"):
    return float(n.storage_units.at[name, "p_nom_opt"])


# ── (a) sustained ride-through ──────────────────────────────────────────────


def test_energy_row_binds():
    """10 MW for D = 4 h is 40 MWh; at 2 h of storage that is 20 MW."""
    assert _p_nom(_solve(_net(max_hours=2.0), _island())) == pytest.approx(20.0, abs=TOL)


def test_power_row_binds():
    """At 8 h of storage the energy needs only 5 MW; the 10 MW deficit needs 10."""
    assert _p_nom(_solve(_net(max_hours=8.0), _island())) == pytest.approx(10.0, abs=TOL)


def test_n_plus_0_genset_leaves_a_4_mw_deficit():
    units = {"bess": dict(_BESS), "crit": {"kind": "load", "critical": True}, "g": _genset()}
    n = _solve(_net(gensets=[("g", 6.0)]), _island(units=units, k=0))
    assert _p_nom(n) == pytest.approx(8.0, abs=TOL)
    assert n.storage_units_t.state_of_charge["bess"].min() >= 16.0 - TOL


def test_n_plus_1_takes_out_the_largest_unit():
    """2 × 3 MW at N+1: G = 6 − 3 = 3, so the deficit is 7 MW and 28 MWh."""
    units = {"bess": dict(_BESS), "crit": {"kind": "load", "critical": True},
             "g1": _genset(), "g2": _genset()}
    n = _solve(_net(gensets=[("g1", 3.0), ("g2", 3.0)]), _island(units=units, k=1))
    assert _p_nom(n) == pytest.approx(14.0, abs=TOL)
    assert n._island_facts["n_plus_k"] == 1


def test_n_plus_1_takes_out_the_largest_of_unequal_units():
    """4 + 2 MW at N+1: G = 6 − 4 = 2, so 8 MW and 32 MWh (not 6 − 2)."""
    units = {"bess": dict(_BESS), "crit": {"kind": "load", "critical": True},
             "g1": _genset(), "g2": _genset()}
    n = _solve(_net(gensets=[("g1", 4.0), ("g2", 2.0)]), _island(units=units, k=1))
    assert _p_nom(n) == pytest.approx(16.0, abs=TOL)


def _extendable_genset(n, name, *, p_nom_max, p_nom_min=0.0, p_max_pu=1.0):
    n.add("Generator", name, bus="site", p_nom_extendable=True, p_nom_min=p_nom_min,
          p_nom_max=p_nom_max, capital_cost=1.0, marginal_cost=1000.0, p_max_pu=p_max_pu)


def test_an_extendable_genset_counts_as_its_unit_mw_in_n_plus_k():
    """10 MW fixed plus an extendable genset of 12 MW units (never built):
    U_max = 12, G = 10 − 12 = −2, so the deficit is 12 MW → 24 MW of BESS."""
    n = _net(gensets=[("g1", 10.0)])
    _extendable_genset(n, "gx", p_nom_max=0.0)
    units = {"bess": dict(_BESS), "crit": {"kind": "load", "critical": True},
             "g1": _genset(), "gx": _genset(unit_mw=_a(12.0))}
    assert _p_nom(_solve(n, _island(units=units, k=1))) == pytest.approx(24.0, abs=TOL)


def test_an_extendable_genset_without_unit_mw_is_refused():
    n = _net(gensets=[("g1", 10.0)])
    _extendable_genset(n, "gx", p_nom_max=10.0)
    units = {"bess": dict(_BESS), "crit": {"kind": "load", "critical": True},
             "g1": _genset(), "gx": _genset()}
    with pytest.raises(IslandRefusal, match="unit_mw"):
        n.optimize(solver_name="highs",
                   extra_functionality=island_extra_functionality(_island(units=units, k=1)))


def test_genset_availability_scales_its_capacity():
    """A built 6 MW genset at p_max_pu 0.5 carries 3 MW: deficit 7 → 14 MW."""
    n = _net()
    _extendable_genset(n, "gx", p_nom_min=6.0, p_nom_max=6.0, p_max_pu=0.5)
    units = {"bess": dict(_BESS), "crit": {"kind": "load", "critical": True},
             "gx": _genset(unit_mw=_a(6.0))}
    assert _p_nom(_solve(n, _island(units=units, k=0))) == pytest.approx(14.0, abs=TOL)


def test_dispatch_efficiency_scales_the_stored_energy():
    """η_dis 0.95: e = 40/0.95 = 42.1 MWh, p_nom 21.05 MW."""
    n = _solve(_net(eta=0.95), _island())
    assert _p_nom(n) == pytest.approx(40.0 / 0.95 / 2.0, abs=TOL)


def test_the_objective_weighting_does_not_change_the_mwh():
    """52.14-weighted snapshots (stores 1 h) give the same reserve."""
    n = _solve(_net(weight=8760.0 / 168), _island())
    assert _p_nom(n) == pytest.approx(20.0, abs=TOL)


def test_two_hour_snapshots_count_hours_not_snapshots():
    """Δ = 2 h: D = 4 h is two snapshots, still 40 MWh."""
    n = _solve(_net(hours=12, freq="2h"), _island())
    assert _p_nom(n) == pytest.approx(20.0, abs=TOL)


def test_cyclic_window_wraps_round_the_end_of_the_period():
    """10 MW in hours 22, 23, 0, 1: the window from 22 wraps to 40 MWh."""
    crit = np.zeros(24)
    crit[[22, 23, 0, 1]] = 10.0
    n = _solve(_net(crit=crit), _island())
    assert _p_nom(n) == pytest.approx(20.0, abs=TOL)
    assert n._island_facts["truncated"] is False


def test_non_cyclic_window_is_truncated_and_flagged():
    """Without cyclic SoC the window from 22 stops at the end (20 MWh), and
    e(t−1) at the first snapshot is the initial SoC."""
    crit = np.zeros(24)
    crit[[22, 23, 0, 1]] = 10.0
    n = _solve(_net(crit=crit, cyclic=False, soc_init=20.0), _island())
    assert _p_nom(n) == pytest.approx(10.0, abs=TOL)
    assert n._island_facts["truncated"] is True


def test_the_row_reads_the_soc_before_the_start_hour():
    """10 MW at hour 5 only, with the grid out at 5 so the BESS also carries
    it then. The reserve for a start at 5 is e(4) ≥ 10 → 20 MW at 0.5 h.
    Reading e(5) would ask e(4) ≥ 20 → 40 MW."""
    crit = np.zeros(24)
    crit[5] = 10.0
    n = _solve(_net(crit=crit, max_hours=0.5, grid_out=[5]), _island(sustained_h=1.0))
    assert _p_nom(n) == pytest.approx(20.0, abs=TOL)


def test_each_investment_period_has_its_own_rows():
    """Two periods: 10 MW in 2030, 15 MW in 2040. The 2040 window needs
    60 MWh → 30 MW at 2 h; each period's window wraps within itself."""
    n = _net()
    n.set_investment_periods([2030, 2040])
    p_set = n.loads_t.p_set
    p_set.loc[p_set.index.get_level_values(0) == 2040, "crit"] = 15.0
    n = _solve(n, _island())
    assert _p_nom(n) == pytest.approx(30.0, abs=TOL)
    assert set(n._island_facts["periods"]) == {"2030", "2040"}


# ── (b) the UPS bridge and the seamless GFM bridge ─────────────────────────


def _ups_case(eta):
    n = _net(bess=False, gensets=[("g", 30.0)], crit=0.0)
    n.add("Load", "it", bus="site", p_set=20.0)
    n.add("StorageUnit", "ups", bus="site", p_nom_extendable=True, capital_cost=100.0,
          max_hours=1.0, efficiency_dispatch=eta, efficiency_store=1.0,
          cyclic_state_of_charge=True)
    units = {"ups": {"kind": "ups", "it_load": ["it"], "walk_in_s": _a(15.0),
                     "f_window_hz": _a(2.0)},
             "it": {"kind": "load", "critical": True}, "g": _genset()}
    return _solve(n, _island(units=units, k=0))


@pytest.mark.parametrize("eta,need", [(1.0, 20.0 * 600 / 3600), (0.95, 20.0 * 600 / 3600 / 0.95)])
def test_ups_holds_the_bridge_energy_at_every_hour(eta, need):
    """20 MW of IT for 600 s: 3.33 MWh at η 1, 3.51 MWh at η 0.95."""
    n = _ups_case(eta)
    assert _p_nom(n, "ups") == pytest.approx(need, abs=TOL)
    assert n.storage_units_t.state_of_charge["ups"].min() >= need - TOL


def test_seamless_gfm_bridge_adds_to_the_window_in_one_row():
    """A 10 MW step with start + sync of 15 s: B = 10·15/3600 = 0.0417 MWh,
    on top of the 40 MWh window in the same row (separate rows would give
    max(40, 0.0417) = 40)."""
    units = {"bess": dict(_BESS), "crit": {"kind": "load", "critical": True},
             "g": _genset(start_s=10.0, sync_s=5.0)}
    n = _solve(_net(gensets=[("g", 0.0)]), _island(units=units, k=0, scenarios=("seamless",)),
               delta_p_plus=10.0)
    assert _p_nom(n) == pytest.approx((40.0 + 10.0 * 15.0 / 3600.0) / 2.0, abs=TOL)


def test_planned_islanding_holds_no_gfm_bridge():
    units = {"bess": dict(_BESS), "crit": {"kind": "load", "critical": True},
             "g": _genset(start_s=10.0, sync_s=5.0)}
    n = _solve(_net(gensets=[("g", 0.0)]), _island(units=units, k=0), delta_p_plus=10.0)
    assert _p_nom(n) == pytest.approx(20.0, abs=TOL)


# ── (c) fuel ────────────────────────────────────────────────────────────────


def test_fuel_store_holds_the_autonomy_at_the_critical_peak():
    """A Link genset (η 0.4) on a fuel bus with an extendable tank: 10 MW for
    24 h is 240 MWh of electricity, 600 MWh of fuel."""
    n = _net(bess=False)
    n.add("Bus", "fuel")
    n.add("Link", "g", bus0="fuel", bus1="site", p_nom=25.0, efficiency=0.4, marginal_cost=1000.0)
    n.add("Store", "tank", bus="fuel", e_nom_extendable=True, capital_cost=1.0, e_cyclic=True)
    units = {"crit": {"kind": "load", "critical": True}, "g": _genset()}
    n = _solve(n, _island(units=units, k=0, fuel_h=24.0))
    assert float(n.stores.at["tank", "e_nom_opt"]) == pytest.approx(600.0, abs=TOL)
    assert n._island_facts["fuel"]["status"] == "row"


def test_template_fuel_is_not_checked_and_the_template_still_solves():
    """The Data Center gensets are Generators with no fuel bus. With the
    rows on, the template stays feasible and the UPS holds its bridge."""
    n = T.build_eh_datacenter()
    island = validate_island_config(T.ISLAND_CONFIG["eh_datacenter"])
    n = _solve(n, island)
    assert n._island_facts["fuel"]["status"] == "not checked"
    assert n._island_facts["n_plus_k"] == 1
    it = n.loads_t.p_set["it_load"]
    soc_before = n.storage_units_t.state_of_charge["ups_battery"].shift(
        1, fill_value=n.storage_units_t.state_of_charge["ups_battery"].iloc[-1])
    assert (soc_before * 0.95 >= it * 600.0 / 3600.0 - TOL).all()


# ── where the rows run ──────────────────────────────────────────────────────


@pytest.mark.parametrize("strategy,sclopf", [("rolling", False), ("full", True),
                                             ("myopic", False)])
def test_rolling_scopf_and_myopic_are_refused_with_a_reason(strategy, sclopf):
    reason = strategy_refusal(solve_strategy=strategy, sclopf=sclopf)
    assert reason and "island" in reason


def test_the_plain_solve_is_allowed():
    assert strategy_refusal(solve_strategy="full", sclopf=False) is None


def test_constraints_off_leave_the_lp_untouched():
    """Q5: the rows are opt-in. Off, the wrapper hands back the same
    callback, so every template solves exactly as before."""
    def user_fn(n, sns):
        pass
    for tid in sorted(T.BUILDERS):
        n = T.BUILDERS[tid]()
        assert _wrap_with_island(n, user_fn, SolverConfig()) is user_fn
        assert _wrap_with_island(n, None, SolverConfig()) is None


def test_constraints_on_without_a_sidecar_are_refused():
    cfg = SolverConfig(island_constraints=True)
    with pytest.raises(IslandRefusal, match="island_config.json"):
        _wrap_with_island(_net(), None, cfg, island=None)


# ── through run_simulation ──────────────────────────────────────────────────


def _run(n, cfg, monkeypatch, island):
    import queue
    import threading

    from services import solver_service
    from services.pypsa_service import PyPSAService
    from services.solver import island as island_mod

    monkeypatch.setattr(island_mod, "load_project_island", lambda: island)
    PyPSAService.set_network(n)
    log_q: queue.SimpleQueue = queue.SimpleQueue()
    status, condition = solver_service.run_simulation(
        cfg, n, PyPSAService.get_lock(), threading.Event(), log_q,
        state_update=lambda **kw: None)
    lines = []
    while not log_q.empty():
        lines.append(str(log_q.get()))
    return status, condition, lines


def test_run_simulation_refuses_a_rolling_solve_with_the_reason(monkeypatch):
    cfg = SolverConfig(island_constraints=True, solve_strategy="rolling")
    status, condition, lines = _run(_net(), cfg, monkeypatch, _island())
    assert status == "error" and condition.startswith("island_refused")
    assert any("[PHASE] Refused" in ln and "rolling" in ln for ln in lines)
    assert not any(ln.startswith("TRACEBACK") for ln in lines)


def test_run_simulation_builds_the_rows_on_the_plain_solve(monkeypatch):
    n = _net(max_hours=2.0)
    n.storage_units.at["bess", "p_nom_max"] = 100.0   # the app's preflight wants a finite bound
    status, condition, _ = _run(n, SolverConfig(island_constraints=True), monkeypatch, _island())
    assert status in ("ok", "optimal"), condition
    assert _p_nom(n) == pytest.approx(20.0, abs=TOL)


# ── dispatch-only solves skip the rows (owner, 2026-10-12) ──────────────────


def _fixed_too_small():
    """Nothing extendable, and a 2 MW / 2 h BESS that cannot hold 40 MWh."""
    n = _net()
    n.storage_units.at["bess", "p_nom_extendable"] = False
    n.storage_units.at["bess", "p_nom"] = 2.0
    return n


def test_a_solve_with_nothing_extendable_skips_the_rows():
    """The rows are a sizing standard: with nothing to buy, they would only
    make an as-built dispatch infeasible."""
    n = _solve(_fixed_too_small(), _island())
    assert n._island_facts["skipped"] == "dispatch-only: nothing the rows can size is extendable"
    assert not any(c.startswith("Island-") for c in n.model.constraints)


def test_an_operational_re_solve_skips_the_rows():
    """The FMEA sweep pins capacities with bounds and keeps them extendable;
    it marks the network `_ic_operational`, which is what is read."""
    n = _net()
    n._ic_operational = True
    n = _solve(n, _island())
    assert n._island_facts["skipped"] == "dispatch-only: operational re-solve"
    assert not any(c.startswith("Island-") for c in n.model.constraints)


def test_a_sizing_solve_is_not_skipped():
    n = _solve(_net(), _island())
    assert n._island_facts.get("skipped") is None


# ── review round (2026-10-12) ───────────────────────────────────────────────


def test_extendable_assets_the_rows_cannot_buy_do_not_make_a_sizing_solve():
    """An extendable PV (not a genset, not ride-through storage) cannot hold
    the reserve, so the solve is still dispatch-only for the rows."""
    n = _fixed_too_small()
    n.add("Generator", "pv", bus="site", p_nom_extendable=True, capital_cost=1.0,
          p_nom_max=10.0, marginal_cost=0.0)
    n = _solve(n, _island())
    assert n._island_facts["skipped"] == "dispatch-only: nothing the rows can size is extendable"


def test_a_genset_built_later_does_not_count_in_an_earlier_period():
    """A 10 MW genset built for 2040 is no help in 2030: the 2030 deficit is
    the whole 10 MW, so 40 MWh → 20 MW of BESS (0 if it were counted)."""
    n = _net()
    n.add("Generator", "gx", bus="site", p_nom_extendable=True, p_nom_min=10.0,
          p_nom_max=10.0, capital_cost=1.0, marginal_cost=1000.0, build_year=2040,
          lifetime=100)
    n.set_investment_periods([2030, 2040])
    units = {"bess": dict(_BESS), "crit": {"kind": "load", "critical": True},
             "gx": _genset(unit_mw=_a(10.0))}
    n = _solve(n, _island(units=units, k=0))
    assert _p_nom(n) == pytest.approx(20.0, abs=TOL)


def test_non_cyclic_soc_carries_into_the_next_period():
    """PyPSA's default (state_of_charge_initial_per_period False) carries the
    SoC from 2030 into 2040. 10 MW at the first 2040 hour (D = 1 h) is held
    by the SoC 2030 ended with, not by state_of_charge_initial (0)."""
    crit = np.zeros(24)
    n = _net(crit=crit, cyclic=False, soc_init=0.0)
    n.set_investment_periods([2030, 2040])
    p_set = n.loads_t.p_set
    p_set.iloc[24, p_set.columns.get_loc("crit")] = 10.0
    n = _solve(n, _island(sustained_h=1.0))
    soc = n.storage_units_t.state_of_charge["bess"]
    assert float(soc.iloc[23]) >= 10.0 - TOL
    assert _p_nom(n) == pytest.approx(10.0, abs=TOL)


def test_a_fixed_genset_counts_its_unit_mw_in_n_plus_k():
    """A fixed 40 MW Generator standing for 4 × 10 MW: N+1 takes out 10 MW,
    not 40. Deficit 50 − 30 = 20 MW → 80 MWh → 40 MW of BESS."""
    units = {"bess": dict(_BESS), "crit": {"kind": "load", "critical": True},
             "fleet": _genset(unit_mw=_a(10.0)), "spare": _genset()}
    n = _solve(_net(crit=50.0, gensets=[("fleet", 40.0), ("spare", 0.0)]),
               _island(units=units, k=1))
    assert _p_nom(n) == pytest.approx(40.0, abs=TOL)


def test_names_the_network_lacks_are_recorded_and_warned():
    units = {"bess": dict(_BESS), "crit": {"kind": "load", "critical": True},
             "g": _genset(), "g9": _genset()}
    said = []
    n = _net(gensets=[("g", 6.0)])
    status, _ = n.optimize(solver_name="highs", extra_functionality=island_extra_functionality(
        _island(units=units, k=0), emit=said.append))
    assert n._island_facts["missing"] == {"genset": ["g9"]}
    assert any("g9" in s for s in said)


def test_a_period_shorter_than_the_window_is_a_refusal_not_a_crash():
    with pytest.raises(IslandRefusal, match="sustained_h"):
        _net().optimize(solver_name="highs",
                        extra_functionality=island_extra_functionality(_island(sustained_h=48.0)))


def test_pf_runs_and_scopf_without_branches_are_not_refused():
    """A power flow never builds the LP, and SCOPF with no branch selected
    falls back to the plain solve."""
    def fn(n, sns):
        pass
    island = _island()
    assert _wrap_with_island(_net(), fn, SolverConfig(island_constraints=True, mode="pf",
                                                       solve_strategy="rolling"),
                             island=island) is fn
    wrapped = _wrap_with_island(_net(), fn, SolverConfig(island_constraints=True, sclopf=True),
                                island=island)
    assert wrapped is not fn


def test_an_operational_re_solve_does_not_read_the_sidecar(monkeypatch):
    from services.solver import island as island_mod

    def boom():
        raise AssertionError("the sidecar was read")
    monkeypatch.setattr(island_mod, "load_project_island", boom)
    n = _net()
    n._ic_operational = True
    assert _wrap_with_island(n, None, SolverConfig(island_constraints=True)) is None


def test_a_flat_delta_p_plus_series_lines_up_with_investment_periods():
    """ΔP⁺ given on plain timestamps still reaches both periods' bridge."""
    units = {"bess": dict(_BESS), "crit": {"kind": "load", "critical": True},
             "g": _genset(start_s=10.0, sync_s=5.0)}
    n = _net(gensets=[("g", 0.0)])
    step = pd.Series(10.0, index=n.snapshots)
    n.set_investment_periods([2030, 2040])
    n = _solve(n, _island(units=units, k=0, scenarios=("seamless",)), delta_p_plus=step)
    assert _p_nom(n) == pytest.approx((40.0 + 10.0 * 15.0 / 3600.0) / 2.0, abs=TOL)
    assert n._island_facts["seamless_bridge"] == "held"
