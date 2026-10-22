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
            bridge_s=600.0, fuel_h=None, rocof=1.0, qss=1.0, margin=0.0, blocks=None):
    """The I2b limits default slack enough that the I2a cases are sized by
    the I2a rows alone: the steady-island MVA row (all scenarios) at a 0
    margin is below every I2a answer."""
    units = {"bess": dict(_BESS), "crit": {"kind": "load", "critical": True}} \
        if units is None else units
    limits = dict(_LIMITS, rocof_hz_per_s=_a(rocof), qss_band_hz=_a(qss))
    req = {"bridge_s": _a(bridge_s), "sustained_h": _a(sustained_h),
           "rocof_window_ms": _a(500.0), "gfm_margin": _a(margin), "scenarios": list(scenarios),
           "frequency_limits": {"transition": dict(limits), "steady_island": dict(limits)}}
    if blocks is not None:
        req["pickup_blocks"] = _a(list(blocks))
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
    # ups_bridged, so the UPS bridge applies (I2b's matrix); one 1 MW block
    # leaves the 30 MW genset's pickup, RoCoF and droop rows slack.
    return _solve(n, _island(units=units, k=0, scenarios=("ups_bridged",), blocks=[1.0]))


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
    n = _solve(_net(gensets=[("g", 0.0)]),
               _island(units=units, k=0, scenarios=("seamless",), rocof=1e6, qss=1e6),
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


def test_one_snapshot_cannot_define_the_ride_through_step():
    """Δ is the gap between snapshots. One snapshot has no gap, so the
    window length D = sustained_h/Δ is undefined — a refusal, not a solve
    that treats the hour as 1 h because the weightings say so."""
    with pytest.raises(IslandRefusal, match="at least two snapshots"):
        _net(hours=1).optimize(
            solver_name="highs",
            extra_functionality=island_extra_functionality(_island(sustained_h=1.0)))


def test_uneven_snapshot_spacing_is_refused():
    """A mixed 1 h / 2 h index must not be reduced to one Δ. The energy row
    is d·Δ over D = sustained_h/Δ snapshots; the wrong Δ mis-sizes the
    reserve."""
    n = _net(hours=3)
    n.set_snapshots(pd.to_datetime([
        "2030-01-01 00:00", "2030-01-01 01:00", "2030-01-01 03:00"]))
    with pytest.raises(IslandRefusal, match="evenly spaced"):
        n.optimize(solver_name="highs",
                   extra_functionality=island_extra_functionality(_island(sustained_h=1.0)))


def test_a_window_that_is_not_a_whole_number_of_snapshots_is_refused():
    """1.5 h of ride-through on a 1 h index is not an integer window. Rounding
    it to 1 or 2 snapshots would hold the wrong MWh."""
    with pytest.raises(IslandRefusal, match="whole number"):
        _net(hours=4).optimize(
            solver_name="highs",
            extra_functionality=island_extra_functionality(_island(sustained_h=1.5)))


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
    n = _solve(n, _island(units=units, k=0, scenarios=("seamless",), rocof=1e6, qss=1e6),
               delta_p_plus=step)
    assert _p_nom(n) == pytest.approx((40.0 + 10.0 * 15.0 / 3600.0) / 2.0, abs=TOL)
    assert n._island_facts["seamless_bridge"] == "held"


def test_the_initial_soc_is_credited_once_at_the_start_of_the_horizon():
    """Non-cyclic, carried across periods, starting at 4 MWh with 0.5 h of
    storage. 10 MW at the first 2040 hour needs SoC(end of 2030) ≥ 10 →
    p_nom 20. Crediting the initial 4 MWh again at the start of 2040 would
    ask only 6 MWh → 12 MW."""
    n = _net(crit=np.zeros(24), cyclic=False, soc_init=4.0, max_hours=0.5)
    n.set_investment_periods([2030, 2040])
    p_set = n.loads_t.p_set
    p_set.iloc[24, p_set.columns.get_loc("crit")] = 10.0
    n = _solve(n, _island(sustained_h=1.0))
    assert _p_nom(n) == pytest.approx(20.0, abs=TOL)


# ══ I2b: the instant of islanding ═══════════════════════════════════════════
#
# A hub: the grid supply on its own bus, an import Link into the site tagged
# eh_role=grid_import (and an export Link back when the site exports), the
# loads, a must-run PV and an extendable GFM BESS. The PCC import is
# p_imp = η·p0_imp − p0_exp; the steps are
#   ΔP⁺ = p_imp − Σ_{late UPS} X_u,   X_u ≤ L_it,u,  X_u ≤ p_nom_u,  X_u ≥ 0
#   ΔP⁻ = −p_imp + Σ_{late UPS} L_it,u
# Each case sets the limits it does not test slack (RoCoF and the qss band
# at 1e6, a 0 MVA margin, no critical load), so one row sizes the BESS.

SLACK = {"rocof": 1e6, "qss": 1e6}
_VSM = {"kind": "bess", "control": "gfm_vsm", "pf_rated": _a(1.0), "droop_pct": _a(4.0),
        "q_droop_pct": _a(4.0), "H_v_s": _a(4.0), "i_max_pu": _a(1.2), "k_gfm_min": _a(1.0)}
_FW_PV = {"kind": "pv", "control": "gfl_fw", "pf_rated": _a(0.95), "fw_k_mw_per_hz": _a(10.0),
          "fw_deadband_hz": _a(0.2), "fw_delay_s": _a(0.5), "fw_ramp_mw_per_s": _a(2.0),
          "fw_p_limit_mw": _a(4.0)}


def _ups(walk_in_s=20.0, transfers=True):
    return {"kind": "ups", "it_load": ["it"], "walk_in_s": _a(walk_in_s), "f_window_hz": _a(2.0),
            "transfers_on_islanding": transfers}


def _hub(*, load=0.0, crit=0.0, it=0.0, pv=0.0, hours=4, export=False, gensets=(),
         bess_hours=4.0, ups_mw=None, ups_extendable=False, extra_storage=()):
    """``gensets``: [(name, MW)] fixed, never dispatched (marginal 1000);
    ``ups_mw`` adds a UPS StorageUnit of that rating (fixed, or pinned by
    p_nom_min = p_nom_max when ``ups_extendable``)."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=hours, freq="h"))
    n.add("Bus", "grid")
    n.add("Bus", "site")
    n.add("Generator", "supply", bus="grid", p_nom=1000.0, p_min_pu=-1.0, marginal_cost=10.0)
    n.add("Link", "imp", bus0="grid", bus1="site", p_nom=1000.0, efficiency=1.0)
    n.links["eh_role"] = ""
    n.links.at["imp", "eh_role"] = "grid_import"
    if export:
        n.add("Link", "exp", bus0="site", bus1="grid", p_nom=1000.0, efficiency=1.0)
        n.links.at["exp", "eh_role"] = "grid_export"
    n.add("Load", "load", bus="site", p_set=load)
    n.add("Load", "crit", bus="site", p_set=crit)
    n.add("Load", "it", bus="site", p_set=it)
    if pv:
        n.add("Generator", "pv", bus="site", p_nom=pv, p_min_pu=1.0, p_max_pu=1.0)
    for name, mw in gensets:
        n.add("Generator", name, bus="site", p_nom=mw, marginal_cost=1000.0)
    n.add("StorageUnit", "bess", bus="site", p_nom_extendable=True, capital_cost=100.0,
          max_hours=bess_hours, cyclic_state_of_charge=True)
    if ups_mw is not None:
        kw = (dict(p_nom_extendable=True, p_nom_min=ups_mw, p_nom_max=ups_mw)
              if ups_extendable else dict(p_nom=ups_mw))
        n.add("StorageUnit", "ups", bus="site", max_hours=1.0, cyclic_state_of_charge=True, **kw)
    for name, mw, hours_ in extra_storage:
        n.add("StorageUnit", name, bus="site", p_nom=mw, max_hours=hours_,
              cyclic_state_of_charge=True)
    return n


def _units(*, bess=_BESS, ups=None, gensets=None, pv=None, extra=None):
    units = {"crit": {"kind": "load", "critical": True}}
    if bess is not None:
        units["bess"] = dict(bess)
    if ups is not None:
        units["ups"] = ups
    for name, spec in (gensets or {}).items():
        units[name] = spec
    if pv is not None:
        units["pv"] = pv
    units.update(extra or {})
    return units


def _seamless(units, **kw):
    kw = {**SLACK, "sustained_h": 1.0, "bridge_s": 1.0, **kw}
    return _island(units=units, k=kw.pop("k", 0), scenarios=kw.pop("scenarios", ("seamless",)),
                   **kw)


# ── (b↑) upward headroom at t = 0⁺ ─────────────────────────────────────────


def test_headroom_covers_the_import_step_with_the_ups_on_line():
    """10 MW import, the UPS on line (no transfer): the BESS needs 10 MW of
    upward headroom at every hour, so p_nom = 10."""
    n = _hub(load=4.0, it=6.0, ups_mw=10.0)
    n = _solve(n, _seamless(_units(ups=_ups(transfers=False))))
    assert _p_nom(n) == pytest.approx(10.0, abs=TOL)


def test_a_late_walk_in_transfer_relieves_the_step():
    """A 6 MW UPS that transfers and walks in after the gensets join
    (20 s ≥ 10 + 5 s): ΔP⁺ = 10 − 6 = 4 MW. With the event rows the 6 MW IT
    load, the largest load, is E2's step: 6 MW."""
    island = _seamless(_units(ups=_ups(walk_in_s=20.0), gensets={"g": _genset()}))
    n = _solve(_hub(load=4.0, it=6.0, ups_mw=10.0, gensets=[("g", 0.0)]), island, event_rows=False)
    assert _p_nom(n) == pytest.approx(4.0, abs=TOL)
    n = _solve(_hub(load=4.0, it=6.0, ups_mw=10.0, gensets=[("g", 0.0)]), island)
    assert _p_nom(n) == pytest.approx(6.0, abs=TOL)


def test_a_walk_in_exactly_at_the_join_counts_as_late():
    """ΔP⁺ = 4 MW as above; the event rows' E2 (the 6 MW IT load) makes it 6."""
    island = _seamless(_units(ups=_ups(walk_in_s=15.0), gensets={"g": _genset()}))
    n = _solve(_hub(load=4.0, it=6.0, ups_mw=10.0, gensets=[("g", 0.0)]), island, event_rows=False)
    assert _p_nom(n) == pytest.approx(4.0, abs=TOL)
    n = _solve(_hub(load=4.0, it=6.0, ups_mw=10.0, gensets=[("g", 0.0)]), island)
    assert _p_nom(n) == pytest.approx(6.0, abs=TOL)


def test_an_early_walk_in_gives_no_relief():
    """Walk-in 10 s < 15 s: the IT load is back before the gensets join, so
    the BESS carries the whole 10 MW."""
    n = _hub(load=4.0, it=6.0, ups_mw=10.0, gensets=[("g", 0.0)])
    n = _solve(n, _seamless(_units(ups=_ups(walk_in_s=10.0), gensets={"g": _genset()})))
    assert _p_nom(n) == pytest.approx(10.0, abs=TOL)


@pytest.mark.parametrize("ups_mw,headroom", [(4.0, 6.0), (8.0, 4.0)])
def test_extendable_ups_relief_is_the_smaller_of_its_load_and_its_rating(ups_mw, headroom):
    """L_it 6 MW. An extendable UPS of 4 MW relieves X = 4 (headroom 6);
    one of 8 MW relieves X = L_it = 6 (headroom 4). With the event rows E2's
    step, the 6 MW IT load, holds the BESS at 6 MW in both."""
    island = _seamless(_units(ups=_ups(walk_in_s=20.0), gensets={"g": _genset()}))
    for event_rows, want in ((False, headroom), (True, 6.0)):
        n = _hub(load=4.0, it=6.0, ups_mw=ups_mw, ups_extendable=True, gensets=[("g", 0.0)])
        n = _solve(n, island, event_rows=event_rows)
        assert _p_nom(n) == pytest.approx(want, abs=TOL)
        assert _p_nom(n, "ups") == pytest.approx(ups_mw, abs=TOL)


def test_a_spinning_genset_adds_headroom_and_burns_its_minimum_load():
    """A spinning 6 MW genset at p_min_pu 0.3 carries 6 − p_g of the 10 MW
    step: the BESS needs 4 MW. It runs at ≥ 1.8 MW every hour, which is the
    fuel it costs. Cold, the BESS needs 10 MW and the genset never runs.

    With the event rows the 10 MW load is also E2's step, where the genset is
    credited at its 50 % load step, 3 MW (frequency.py:632), not its full
    headroom: the BESS needs 10 − 3 = 7 MW. E1 still credits the full
    headroom (the known gap the I2b docs name)."""
    units = _units(gensets={"g": _genset(spinning=True, p_min_pu=_a(0.3))})
    n = _solve(_hub(load=10.0, gensets=[("g", 6.0)]), _seamless(units), event_rows=False)
    assert _p_nom(n) == pytest.approx(4.0, abs=TOL)
    assert (n.generators_t.p["g"] >= 1.8 - TOL).all()
    n = _solve(_hub(load=10.0, gensets=[("g", 6.0)]), _seamless(units))
    assert _p_nom(n) == pytest.approx(7.0, abs=TOL)
    assert (n.generators_t.p["g"] >= 1.8 - TOL).all()
    cold = _solve(_hub(load=10.0, gensets=[("g", 6.0)]),
                  _seamless(_units(gensets={"g": _genset()})))
    assert _p_nom(cold) == pytest.approx(10.0, abs=TOL)
    assert cold.generators_t.p["g"].abs().max() <= TOL


def test_the_minimum_load_row_holds_in_every_scenario():
    """Spinning without seamless still burns the minimum load (planned)."""
    units = _units(gensets={"g": _genset(spinning=True, p_min_pu=_a(0.3))})
    n = _solve(_hub(load=10.0, gensets=[("g", 6.0)]), _seamless(units, scenarios=("planned",)))
    assert (n.generators_t.p["g"] >= 1.8 - TOL).all()


def test_ups_bridged_has_no_headroom_row():
    """Under ups_bridged the bus goes dead: no BESS headroom is bought."""
    units = _units(gensets={"g": _genset()})
    n = _solve(_hub(load=10.0, gensets=[("g", 20.0)]),
               _seamless(units, scenarios=("ups_bridged",), blocks=[1.0]))
    assert _p_nom(n) == pytest.approx(0.0, abs=TOL)


# ── (b↓) downward headroom ──────────────────────────────────────────────────


def test_the_export_step_needs_downward_power():
    """The site exports 8 MW (a must-run 8 MW PV, no load): ΔP⁻ = 8, and the
    BESS absorbs it: p_s + p_nom ≥ 8."""
    n = _solve(_hub(pv=8.0, export=True), _seamless(_units()))
    assert _p_nom(n) == pytest.approx(8.0, abs=TOL)


def test_a_late_transferring_ups_adds_its_load_to_the_export_step():
    """Exporting 8 MW with a 6 MW IT load that transfers late: ΔP⁻ = 8 + 6."""
    n = _hub(pv=14.0, it=6.0, export=True, ups_mw=10.0, gensets=[("g", 0.0)])
    n = _solve(n, _seamless(_units(ups=_ups(walk_in_s=20.0), gensets={"g": _genset()})))
    assert _p_nom(n) == pytest.approx(14.0, abs=TOL)


def test_an_early_transfer_adds_its_load_to_the_export_step_at_the_instant():
    """Owner, 2026-10-13: every transferring UPS drops its IT load at 0⁺,
    early walk-in or late, so the 0⁺ surplus is 8 + 6 even with a 10 s
    walk-in (the plan's late-only sum would give 8)."""
    n = _hub(pv=14.0, it=6.0, export=True, ups_mw=10.0, gensets=[("g", 0.0)])
    n = _solve(n, _seamless(_units(ups=_ups(walk_in_s=10.0), gensets={"g": _genset()})))
    assert _p_nom(n) == pytest.approx(14.0, abs=TOL)


def test_the_qss_export_step_counts_only_late_transfers():
    """After the gensets join, an early walk-in UPS has its load back: the
    qss export step is 4 MW, not 10. Band 0.5 Hz, R 4 %: p/2·0.5 ≥ 4 → 16
    (counting the early UPS would give 40; the 0⁺ power row gives 10)."""
    n = _hub(pv=10.0, it=6.0, export=True, ups_mw=10.0, gensets=[("g", 0.0)])
    n = _solve(n, _seamless(_units(ups=_ups(walk_in_s=10.0), gensets={"g": _genset()}),
                            qss=0.5))
    assert _p_nom(n) == pytest.approx(16.0, abs=TOL)


def test_soc_room_holds_the_surplus_until_frequency_watt_curtails_it():
    """t_curt = 0.5 s delay + 4 MW / 2 MW/s = 2.5 s. 8 MW for 2.5 s is
    0.005556 MWh of room; at 0.0005 h of storage that is 11.11 MW (the power
    row alone gives 8)."""
    n = _hub(pv=8.0, export=True, bess_hours=0.0005)
    n = _solve(n, _seamless(_units(pv=_FW_PV)))
    assert _p_nom(n) == pytest.approx(8.0 * 2.5 / 3600.0 / 0.0005, abs=1e-3)
    assert n._island_facts["surplus"]["t_curt_s"] == pytest.approx(2.5)


def test_a_surplus_with_no_frequency_watt_is_reported_not_islandable():
    """Without a gfl_fw unit the surplus is not curtailable: no SoC row, and
    every exporting hour is reported."""
    from services.solver.island import not_islandable_hours

    n = _hub(pv=8.0, export=True, bess_hours=0.0005)
    n = _solve(n, _seamless(_units()))
    assert _p_nom(n) == pytest.approx(8.0, abs=TOL)
    assert n._island_facts["surplus"]["status"] == "not curtailable: no gfl_fw unit"
    assert list(not_islandable_hours(n)) == list(n.snapshots)


def test_an_importing_site_has_no_not_islandable_hours():
    from services.solver.island import not_islandable_hours

    n = _solve(_hub(load=10.0), _seamless(_units()))
    assert list(not_islandable_hours(n)) == []


# ── (b■) genset pickup blocks (ups_bridged) ─────────────────────────────────


def test_pickup_blocks_size_the_gensets_at_n_plus_1():
    """ls 50 %. A fixed 6 MW genset and an extendable one of 4 MW units, N+1,
    largest block 3 MW: 0.5·(6 + p) − 1·max(0.5·6, 0.5·4) ≥ 3 → p = 6."""
    n = _hub(gensets=[("g1", 6.0)])
    _extendable_genset(n, "gx", p_nom_max=100.0)
    units = _units(bess=None, gensets={"g1": _genset(), "gx": _genset(unit_mw=_a(4.0))})
    n = _solve(n, _seamless(units, scenarios=("ups_bridged",), blocks=[2.0, 3.0], k=1))
    assert float(n.generators.at["gx", "p_nom_opt"]) == pytest.approx(6.0, abs=TOL)


def test_pickup_blocks_too_large_for_fixed_gensets_are_refused():
    """Nothing the row can size: a refusal naming the row, not an
    infeasible LP."""
    n = _hub(gensets=[("g1", 4.0), ("g2", 4.0)])
    units = _units(gensets={"g1": _genset(), "g2": _genset()})
    with pytest.raises(IslandRefusal, match="pickup"):
        n.optimize(solver_name="highs", extra_functionality=island_extra_functionality(
            _seamless(units, scenarios=("ups_bridged",), blocks=[3.0], k=1)))


# ── (c) grid-forming MVA ────────────────────────────────────────────────────


def test_gfm_mva_at_the_instant_binds_with_cold_gensets():
    """10 MW critical, served by a 10 MW must-run PV (no import step). At
    0⁺ the cold genset does not count: s_bess = p/0.95 ≥ 1.1·10 → 11.58 MW."""
    n = _hub(crit=10.0, pv=10.0, gensets=[("g", 20.0)])
    n = _solve(n, _seamless(_units(gensets={"g": _genset()}), margin=0.1))
    assert _p_nom(n) == pytest.approx(11.0 * 0.95, abs=TOL)


def test_gfm_mva_in_the_steady_island_counts_every_genset():
    """planned: only the steady row. A 4 MW genset (5 MVA at 0.8) leaves
    11 − 5 = 6 MVA to the BESS: 5.7 MW. A separate, fixed ride-through
    store carries (a)."""
    rt = {"kind": "bess", "control": "gfl_pq", "pf_rated": _a(0.95)}
    n = _hub(crit=10.0, pv=10.0, gensets=[("g", 4.0)], extra_storage=[("rt", 100.0, 10.0)])
    units = _units(gensets={"g": _genset()}, extra={"rt": rt})
    n = _solve(n, _seamless(units, scenarios=("planned",), margin=0.1, rt=["rt"]))
    assert _p_nom(n) == pytest.approx(6.0 * 0.95, abs=TOL)


def test_seamless_with_no_former_at_the_instant_is_refused():
    """A critical load, cold gensets and no GFM storage: nothing forms the
    island at 0⁺, and nothing the row can size changes that."""
    n = _hub(crit=10.0, pv=10.0, gensets=[("g", 20.0)])
    n.storage_units.at["bess", "p_nom_extendable"] = False
    _extendable_genset(n, "gx", p_nom_max=10.0)
    units = _units(bess=None, gensets={"g": _genset(), "gx": _genset(unit_mw=_a(10.0))})
    with pytest.raises(IslandRefusal, match="grid-forming"):
        n.optimize(solver_name="highs", extra_functionality=island_extra_functionality(
            _seamless(units, margin=0.1)))


# ── (d) RoCoF ───────────────────────────────────────────────────────────────


def test_rocof_with_a_vsm_bess():
    """ΔP⁺ 4 MW, f0 50 Hz, 1 Hz/s, H_v 4 s: 2·4·s ≥ 50·4 → s = 25 MVA
    (pf 1)."""
    n = _solve(_hub(load=4.0), _seamless(_units(bess=_VSM), rocof=1.0))
    assert _p_nom(n) == pytest.approx(25.0, abs=TOL)


def test_rocof_counts_a_vsm_on_its_apparent_power():
    """H_v is on S_n: at pf 0.8, s = p/0.8, so 2·4·p/0.8 ≥ 200 → p = 20 MW
    (25 if the inertia were counted on p)."""
    vsm = dict(_VSM, pf_rated=_a(0.8))
    n = _solve(_hub(load=4.0), _seamless(_units(bess=vsm), rocof=1.0))
    assert _p_nom(n) == pytest.approx(20.0, abs=TOL)


def test_rocof_with_a_droop_bess():
    """τ_f 0.1 s, R 4 %: M = 0.1·p/(2·0.04); 2M = 2.5·p ≥ 200 → p = 80 MW."""
    n = _solve(_hub(load=4.0), _seamless(_units(), rocof=1.0))
    assert _p_nom(n) == pytest.approx(80.0, abs=TOL)


def test_rocof_on_the_export_step():
    """Exporting 4 MW: the same 25 MVA VSM."""
    n = _solve(_hub(pv=4.0, export=True), _seamless(_units(bess=_VSM), rocof=1.0))
    assert _p_nom(n) == pytest.approx(25.0, abs=TOL)


def test_rocof_counts_a_spinning_gensets_inertia():
    """A spinning 4 MW genset, H 1.5 s on 5 MVA: 7.5 MWs. Its minimum load
    (1.2 MW) also carries part of the 4 MW load, so ΔP⁺ = 2.8 MW:
    2·(4·s + 7.5) ≥ 50·2.8 → s = 15.625 (23.125 if the step stayed 4)."""
    units = _units(bess=_VSM, gensets={"g": _genset(spinning=True, p_min_pu=_a(0.3))})
    n = _solve(_hub(load=4.0, gensets=[("g", 4.0)]), _seamless(units, rocof=1.0))
    assert _p_nom(n) == pytest.approx(15.625, abs=TOL)
    assert n.generators_t.p["g"].max() == pytest.approx(1.2, abs=TOL)


def test_rocof_under_ups_bridged_takes_the_largest_block_at_n_plus_1():
    """A fixed 10 MW genset and an extendable one of 10 MW units, H 1.5 s,
    cos φ 0.8, N+1, block 3 MW, 1 Hz/s:
    2·(1.875·(10 + p) − 1.875·10) ≥ 150 → p = 40."""
    n = _hub(gensets=[("g1", 10.0)])
    _extendable_genset(n, "gx", p_nom_max=100.0)
    units = _units(bess=None, gensets={"g1": _genset(), "gx": _genset(unit_mw=_a(10.0))})
    n = _solve(n, _seamless(units, scenarios=("ups_bridged",), blocks=[3.0], k=1, rocof=1.0))
    assert float(n.generators.at["gx", "p_nom_opt"]) == pytest.approx(40.0, abs=TOL)


# ── (e) quasi-steady-state frequency ───────────────────────────────────────


def _qss_gensets():
    return {"g1": _genset(), "g2": _genset()}


def test_qss_on_the_import_step_at_n_plus_1():
    """ΔP⁺ 4 MW, band 0.5 Hz, R 4 %, f0 50. Gensets 2 + 1 MW, N+1:
    ΣK = p/2 + (2 + 1)/2 − 2/2 = p/2 + 0.5 ≥ 8 → p = 15."""
    n = _hub(load=4.0, gensets=[("g1", 2.0), ("g2", 1.0)])
    n = _solve(n, _seamless(_units(gensets=_qss_gensets()), qss=0.5, k=1))
    assert _p_nom(n) == pytest.approx(15.0, abs=TOL)


def test_qss_on_the_export_step():
    n = _hub(pv=4.0, export=True, gensets=[("g1", 2.0), ("g2", 1.0)])
    n = _solve(n, _seamless(_units(gensets=_qss_gensets()), qss=0.5, k=1))
    assert _p_nom(n) == pytest.approx(15.0, abs=TOL)


def test_qss_under_ups_bridged_takes_the_largest_block():
    """Block 4 MW, band 0.5 Hz: ΣK ≥ 8. A fixed 2 MW genset and an extendable
    one of 2 MW units, N+1: (2 + p)/2 − 1 ≥ 8 → p = 16."""
    n = _hub(gensets=[("g1", 2.0)])
    _extendable_genset(n, "gx", p_nom_max=100.0)
    units = _units(bess=None, gensets={"g1": _genset(), "gx": _genset(unit_mw=_a(2.0))})
    n = _solve(n, _seamless(units, scenarios=("ups_bridged",), blocks=[4.0], k=1, qss=0.5))
    assert float(n.generators.at["gx", "p_nom_opt"]) == pytest.approx(16.0, abs=TOL)


# ── the GFM bridge from I2b's own ΔP⁺, and where the rows apply ────────────


def test_the_gfm_bridge_reads_the_islanding_step_with_its_relief():
    """10 MW critical for 4 h in the BESS (40 MWh) plus B = ΔP⁺·15 s. The
    import is 16 MW, of which a late UPS relieves 6: B = 10·15/3600."""
    n = _hub(crit=10.0, it=6.0, ups_mw=10.0, gensets=[("g", 0.0)], bess_hours=2.0)
    units = _units(ups=_ups(walk_in_s=20.0), gensets={"g": _genset()})
    n = _solve(n, _seamless(units, sustained_h=4.0))
    assert _p_nom(n) == pytest.approx((40.0 + 10.0 * 15.0 / 3600.0) / 2.0, abs=TOL)
    assert n._island_facts["seamless_bridge"] == "held"


def test_a_gfm_store_outside_ride_through_holds_the_bridge_in_its_own_row():
    """The GFM BESS is not in RT (a fixed GFL store carries the ride-through):
    it holds B = ΔP⁺·15 s = 10·15/3600 MWh in a row of its own. At 0.001 h
    of storage that is 41.67 MW (headroom alone gives 10)."""
    rt = {"kind": "bess", "control": "gfl_pq", "pf_rated": _a(0.95)}
    n = _hub(load=10.0, gensets=[("g", 0.0)], bess_hours=0.001,
             extra_storage=[("rt", 100.0, 10.0)])
    units = _units(gensets={"g": _genset()}, extra={"rt": rt})
    n = _solve(n, _seamless(units, rt=["rt"]))
    assert _p_nom(n) == pytest.approx(10.0 * 15.0 / 3600.0 / 0.001, abs=1e-3)
    assert "Island-gfm-bridge" in n.model.constraints


def test_planned_islanding_holds_no_ups_bridge():
    """The matrix: the UPS bridge is for unplanned islanding only."""
    n = _hub(it=6.0, ups_mw=0.0, ups_extendable=True)
    n.storage_units.at["ups", "p_nom_max"] = 100.0
    n.storage_units.at["ups", "capital_cost"] = 1.0
    units = _units(ups=_ups(transfers=False))
    n = _solve(n, _seamless(units, scenarios=("planned",), bridge_s=600.0))
    assert _p_nom(n, "ups") == pytest.approx(0.0, abs=TOL)


def test_planned_islanding_has_no_instant_rows():
    """planned: no headroom, RoCoF or qss row, so a 10 MW import buys no BESS."""
    n = _solve(_hub(load=10.0), _seamless(_units(), scenarios=("planned",), rocof=1.0, qss=0.5))
    assert _p_nom(n) == pytest.approx(0.0, abs=TOL)


def test_seamless_without_a_tagged_import_link_is_refused():
    n = _hub(load=10.0)
    n.links.at["imp", "eh_role"] = ""
    with pytest.raises(IslandRefusal, match="grid_import"):
        n.optimize(solver_name="highs",
                   extra_functionality=island_extra_functionality(_seamless(_units())))


def test_import_efficiency_scales_the_step():
    """η_imp 0.9: 10 MW of load is 10 MW on the site side, so ΔP⁺ = 10."""
    n = _hub(load=10.0)
    n.links.at["imp", "efficiency"] = 0.9
    n = _solve(n, _seamless(_units()))
    assert _p_nom(n) == pytest.approx(10.0, abs=TOL)


def test_each_investment_period_reads_its_own_step():
    """4 MW import in 2030, 8 MW in 2040: the BESS needs 8 MW."""
    n = _hub(load=4.0)
    n.set_investment_periods([2030, 2040])
    p_set = n.loads_t.p_set
    if "load" not in p_set:
        p_set["load"] = 4.0
    p_set.loc[p_set.index.get_level_values(0) == 2040, "load"] = 8.0
    n = _solve(n, _seamless(_units()))
    assert _p_nom(n) == pytest.approx(8.0, abs=TOL)


# ── the Data Center template under I2b (owner, 2026-10-13) ─────────────────


def test_template_islands_ups_bridged_in_5_mw_blocks():
    """The template ships ups_bridged with 11 × 5 MW blocks. The RoCoF row on
    a 5 MW block binds: 4 × 10 MW plus genset_new (10 MW units), H 1.5 s,
    cos φ 0.8, N+1, 1 Hz/s: 2·1.875·(40 + p − 10) ≥ 250 → p = 36.67 MW."""
    cfg = T.ISLAND_CONFIG["eh_datacenter"]["requirements"]
    assert cfg["scenarios"] == ["ups_bridged"]
    assert cfg["pickup_blocks"]["value"] == [5.0] * 11
    n = _solve(T.build_eh_datacenter(), validate_island_config(T.ISLAND_CONFIG["eh_datacenter"]))
    assert float(n.generators.at["genset_new", "p_nom_opt"]) == pytest.approx(
        250.0 / 3.75 - 30.0, abs=1e-3)
    assert "Island-rocof-block" in n.model.constraints


# ── the seamless island's own events: E2, E3 (N-1), E5 (Guided smoke 2026-10-16) ──
#
# I6 judges E2 (the largest load step), E3 (a unit trip) and E5 (the PV lost)
# as well as the islanding step; the event rows size the same headroom for
# them. The toys pin each one: a second, fixed GFM battery ``b2`` beside the
# extendable ``bess``, PV that must run, and no export.

_EVENTS = {"blocks": [0.1]}      # a 0.1 MW pickup block keeps E2 out of the way


def _two_gfm_hub(*, pv, load, b2_mw):
    n = _hub(pv=pv, hours=4, extra_storage=[("b2", b2_mw, 4.0)])
    n.loads_t.p_set["load"] = pd.Series(load, index=n.snapshots, dtype=float)
    return n


def _two_gfm_units():
    return _units(pv={"kind": "pv", "control": "gfl_qu", "pf_rated": _a(0.95),
                      "qu_points": _a([[0.9, 0.33], [1.1, -0.33]])},
                  extra={"b2": dict(_BESS)})


def test_a_charging_batterys_trip_needs_the_other_former_to_absorb_it():
    """6 MW of PV that must run and no load for two hours: the batteries
    charge 6 MW. Whichever charges, a trip of the charging one leaves its
    charge as surplus for the other: bess_nom − c_bess ≥ c_b2 with c_bess +
    c_b2 = 6, so bess ≥ 6 MW. Without the event rows the fixed 6 MW b2 takes
    it all and no BESS is bought."""
    island = _seamless(_two_gfm_units(), **_EVENTS)
    n = _solve(_two_gfm_hub(pv=6.0, load=[0.0, 0.0, 12.0, 12.0], b2_mw=6.0), island, event_rows=False)
    assert _p_nom(n) == pytest.approx(0.0, abs=TOL)
    n = _solve(_two_gfm_hub(pv=6.0, load=[0.0, 0.0, 12.0, 12.0], b2_mw=6.0), island)
    assert _p_nom(n) == pytest.approx(6.0, abs=TOL)
    rows = n._island_facts["instant"]["rows"]
    assert {"Island-n1-down-b2", "Island-n1-down-bess", "Island-n1-up-b2", "Island-e2-up",
            "Island-e5-up"} <= set(rows)
    assert n._island_facts["n1_no_former"] == []


def test_the_pv_lost_or_tripped_needs_up_headroom():
    """6 MW of PV carries a 6 MW load, the batteries idle: E5 (the PV lost)
    and E3 (the PV tripping) both need 6 MW up. The fixed b2 gives 4, so the
    BESS gives 2. Without the event rows there is no import to lose: none."""
    island = _seamless(_two_gfm_units(), **_EVENTS)
    n = _solve(_two_gfm_hub(pv=6.0, load=[6.0] * 4, b2_mw=4.0), island, event_rows=False)
    assert _p_nom(n) == pytest.approx(0.0, abs=TOL)
    n = _solve(_two_gfm_hub(pv=6.0, load=[6.0] * 4, b2_mw=4.0), island)
    assert _p_nom(n) == pytest.approx(2.0, abs=TOL)


def test_the_trip_of_the_only_former_gets_no_row_and_says_so():
    """One GFM battery: its trip leaves no grid former, and no dispatch
    survives that, so it gets no N-1 row (the check judges it); the facts say
    so. The PV lost (E5, which covers the PV's own trip) still needs the
    BESS's up headroom."""
    units = _units(pv={"kind": "pv", "control": "gfl_qu", "pf_rated": _a(0.95),
                       "qu_points": _a([[0.9, 0.33], [1.1, -0.33]])})
    n = _hub(pv=6.0, hours=4)
    n.loads_t.p_set["load"] = pd.Series(6.0, index=n.snapshots)
    n = _solve(n, _seamless(units, **_EVENTS))
    rows = set(n._island_facts["instant"]["rows"])
    assert not any(r.startswith("Island-n1-") and r.endswith("-bess") for r in rows)
    assert "Island-e5-up" in rows and "Island-n1-up-pv" not in rows    # E5 implies the PV's own trip
    assert n._island_facts["n1_no_former"] == ["E3 of bess leaves no grid former: not sized"]
    assert _p_nom(n) == pytest.approx(6.0, abs=TOL)


def test_an_event_the_site_cannot_hold_is_infeasible_and_the_diagnosis_names_the_rows():
    """3 MW of PV that must run and no load for two hours: the batteries
    charge 3 MW. b2 is fixed at 2 MW and the BESS can be built to 2 MW at
    most, so whichever trips, the other cannot absorb its charge
    (2 − c_bess ≥ c_b2 with c_bess + c_b2 = 3 has no solution). The LP is
    infeasible with the event rows and solves without them; the solver's
    infeasibility hints name the island rows."""
    import queue

    from services.solver.diagnostics import _diagnose_infeasibility

    def net():
        n = _two_gfm_hub(pv=3.0, load=[0.0, 0.0, 6.0, 6.0], b2_mw=2.0)
        n.storage_units.at["bess", "p_nom_max"] = 2.0
        return n
    island = _seamless(_two_gfm_units(), **_EVENTS)
    assert _solve(net(), island, event_rows=False) is not None
    n = net()
    status, cond = n.optimize(solver_name="highs", extra_functionality=island_extra_functionality(island))
    assert status != "ok" and "infeasible" in str(cond).lower()
    log = queue.SimpleQueue()
    _diagnose_infeasibility(n, None, log)
    text = " ".join(log.get() for _ in range(log.qsize()))
    assert "island rows are on" in text and "Island-n1-down-b2" in text and "Island-e2-up" in text


# ── E2 and the pickup order by criticality tier (grid-to-process ticket 08) ──


def _process_hub():
    """The hub with two process units on site (and one fed straight from the grid, outside the island)."""
    n = _hub(load=2.0, hours=4)
    n.add("Bus", "prod")
    n.add("Load", "prod_load", bus="prod", p_set=6.0)
    n.add("Link", "unit", bus0="site", bus1="prod", p_nom=20.0, efficiency=0.5)
    n.add("Link", "flex", bus0="site", bus1="prod", p_nom=20.0, efficiency=0.5)
    n.add("Link", "outside", bus0="grid", bus1="prod", p_nom=1.0, efficiency=0.5)
    n.links["eh_process"] = n.links.index.isin(["unit", "flex", "outside"])
    n.links["criticality_tier"] = ""
    n.links.loc[["unit", "flex"], "criticality_tier"] = ["process_critical", "process_flexible"]
    return n


def test_without_pickup_blocks_each_island_process_unit_is_a_load_step():
    """E2's largest load step counts a process unit's draw (its p0, a decision here: a row each), as a charging
    battery's; a unit fed from the grid side is not picked up by the island, and explicit blocks replace them."""
    n = _solve(_process_hub(), _seamless(_units()))
    rows = set(n._island_facts["instant"]["rows"])
    assert {"Island-e2-up-unit", "Island-e2-up-flex"} <= rows and "Island-e2-up-outside" not in rows
    n = _solve(_process_hub(), _seamless(_units(), blocks=[0.1]))
    assert not {r for r in n._island_facts["instant"]["rows"] if r.startswith("Island-e2-up-unit")}


def test_the_pickup_sequence_runs_by_tier_then_size_then_name_from_the_solved_draw():
    from services.solver.island import pickup_sequence

    n = _process_hub()
    before = pickup_sequence(n)
    assert [b["name"] for b in before][:3] == ["unit", "flex", "prod_load"]    # tiers, then Loads by size
    assert [b["kind"] for b in before[2:]] == ["load"] * (len(before) - 2)
    assert {b["basis"] for b in before if b["kind"] == "process_unit"} == {"not_solved"}
    assert all(b["block_mw"] is None for b in before if b["kind"] == "process_unit")
    n.links_t.p0 = pd.DataFrame({"unit": [3.0, 5.0, 1.0, 0.0], "flex": [12.0, 0.0, 0.0, 0.0]},
                                index=n.snapshots)
    seq = pickup_sequence(n)
    assert [(b["name"], b["block_mw"], b["basis"]) for b in seq[:2]] == [
        ("unit", 5.0, "solved_p0_max"), ("flex", 12.0, "solved_p0_max")]     # a tier outranks a size
    assert "outside" not in {b["name"] for b in seq}
    n.links.loc["flex", "criticality_tier"] = "process_critical"
    assert [b["name"] for b in pickup_sequence(n)[:2]] == ["flex", "unit"]        # same tier: largest first


def test_an_untiered_process_unit_is_picked_up_as_process_critical_and_flagged():
    from services.solver.island import pickup_sequence

    n = _process_hub()
    n.links.loc["unit", "criticality_tier"] = "critcal"              # a typo is no tier
    seq = pickup_sequence(n)
    (unit,) = [b for b in seq if b["name"] == "unit"]
    assert unit["flag"] == "tier_unset" and unit["tier"] == "critcal"
    assert [b["name"] for b in seq][:2] == ["unit", "flex"]          # with process_critical, before flexible
    assert all("flag" not in b for b in seq if b["name"] != "unit")
