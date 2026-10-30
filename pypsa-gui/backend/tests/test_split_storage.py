"""
AS-02 (`.scratch/asset-sizing-loop/issues/02-separate-power-energy-storage.md`): storage as a `Store` (energy E)
with charger and discharger `Link`s (power P), the PyPSA-Eur battery layout, so power and energy are separate
LP variables. `_wrap_with_split_storage` adds per store:

  * the power tie, charger p_nom == discharger efficiency x discharger p_nom (P is AC-side MW both ways);
  * min_hours x P <= E <= max_hours x P (the technology's duration range);
  * P <= c_rate_max x E (the C-rate cap).

`migrate_storage_unit` turns a fixed-`max_hours` `StorageUnit` into the same layout with min = max = max_hours,
and must leave the objective unchanged. All limits are generic engineering values marked "assumed" (SAGC-1).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pypsa
import pytest

from services.solver.split_storage import (
    STORAGE_TECHNOLOGIES,
    STORE_COLUMNS,
    SplitStorageError,
    _wrap_with_split_storage,
    add_split_storage,
    migrate_storage_unit,
)

HOURS = 48


def _site(price: np.ndarray) -> pypsa.Network:
    """One AC bus, a flat 50 MW load and an import at `price` EUR/MWh. The objective weights the hours up to a
    year so annual capital costs (EUR/MW/yr, EUR/MWh/yr) compare with the arbitrage; SOC steps stay 1 h."""
    n = pypsa.Network()
    n.set_snapshots(pd.RangeIndex(len(price)))
    n.snapshot_weightings.loc[:, "objective"] = 8760 / len(price)
    n.add("Bus", "ac", carrier="AC")
    n.add("Load", "load", bus="ac", p_set=50.0)
    n.add("Generator", "import", bus="ac", p_nom=1e4, marginal_cost=pd.Series(price, n.snapshots))
    return n


def _solve(n: pypsa.Network) -> float:
    status, _ = n.optimize(solver_name="highs", extra_functionality=_wrap_with_split_storage(n, None))
    assert status == "ok"
    return float(n.objective)


def _peaks(on: int, off: int, hours: int = HOURS, low: float = 10.0, high: float = 200.0) -> np.ndarray:
    """`on` expensive hours, then `off` cheap hours, repeated."""
    return np.array([high if t % (on + off) < on else low for t in range(hours)], dtype=float)


def _p_e(n: pypsa.Network, name: str) -> tuple[float, float]:
    return float(n.links.at[f"{name} charger", "p_nom_opt"]), float(n.stores.at[name, "e_nom_opt"])


def test_technology_hours_are_the_issue_ranges():
    t = STORAGE_TECHNOLOGIES
    assert (t["supercap"].min_hours, t["supercap"].max_hours) == pytest.approx((5 / 3600, 60 / 3600))
    assert (t["high_c_bess"].min_hours, t["high_c_bess"].max_hours) == (0.25, 1.0)
    assert (t["energy_bess"].min_hours, t["energy_bess"].max_hours) == (2.0, 8.0)
    for tech in t.values():
        assert tech.c_rate_max > 0 and tech.source == "assumed"


def test_one_energy_bess_solves_with_e_over_p_inside_2_to_8_hours():
    n = _site(_peaks(on=5, off=7))
    add_split_storage(n, "bess", "ac", "energy_bess", power_capital_cost=30e3, energy_capital_cost=10e3)
    _solve(n)
    p, e = _p_e(n, "bess")
    assert p > 1.0
    assert 2.0 - 1e-6 <= e / p <= 8.0 + 1e-6
    # The power tie: the discharger delivers P at the AC bus.
    eff = n.links.at["bess discharger", "efficiency"]
    assert n.links.at["bess discharger", "p_nom_opt"] * eff == pytest.approx(p, rel=1e-6)


def test_hand_case_100_mw_at_4_h_is_400_mwh():
    n = _site(_peaks(on=4, off=8))
    add_split_storage(n, "bess", "ac", "energy_bess", p_nom=100.0, hours=(4.0, 4.0),
                      energy_capital_cost=10e3)
    _solve(n)
    assert n.stores.at["bess", "e_nom_opt"] == pytest.approx(400.0, rel=1e-6)
    # P is AC-side (GK B): the charger is 100 MW and the discharger draws 100 / 0.96 from the store, so
    # putting P on the store side would move these numbers.
    assert n.links.at["bess charger", "p_nom"] == 100.0
    assert n.links.at["bess discharger", "p_nom"] == pytest.approx(100.0 / 0.96)


def test_hours_outside_the_technology_range_are_refused():
    n = _site(_peaks(on=4, off=8))
    with pytest.raises(SplitStorageError, match="outside"):
        add_split_storage(n, "bess", "ac", "high_c_bess", hours=(0.25, 4.0))


def test_the_c_rate_cap_binds_where_arbitrage_would_pick_a_shorter_duration():
    # Two-hour peaks: with energy dearer than power, arbitrage wants about 2 h.
    price = _peaks(on=2, off=10)
    free = _site(price)
    add_split_storage(free, "bess", "ac", "energy_bess", power_capital_cost=5e3, energy_capital_cost=40e3)
    _solve(free)
    p, e = _p_e(free, "bess")
    # Two hours at the AC bus, plus the discharge loss (default efficiency 0.96).
    assert p > 1.0 and e / p == pytest.approx(2.0 / 0.96, rel=1e-4)

    capped = _site(price)
    add_split_storage(capped, "bess", "ac", "energy_bess", power_capital_cost=5e3, energy_capital_cost=40e3,
                      c_rate_max=0.25)
    _solve(capped)
    p, e = _p_e(capped, "bess")
    assert p > 1.0
    assert p == pytest.approx(0.25 * e, rel=1e-6)      # the cap binds: E/P = 4 h, not ~2 h
    assert capped.objective > free.objective


def _with_storage_unit(price: np.ndarray, **su) -> pypsa.Network:
    n = _site(price)
    n.add("StorageUnit", "bess", bus="ac", **su)
    return n


@pytest.mark.parametrize("su", [
    dict(p_nom_extendable=True, max_hours=4.0, capital_cost=60e3, efficiency_store=0.95,
         efficiency_dispatch=0.9, standing_loss=0.001, marginal_cost=1.5, cyclic_state_of_charge=True),
    dict(p_nom=30.0, max_hours=6.0, efficiency_store=0.92, efficiency_dispatch=0.92,
         state_of_charge_initial=60.0, p_max_pu=0.8, p_min_pu=-0.5),
], ids=["extendable", "fixed"])
def test_a_legacy_storage_unit_project_gives_the_same_objective_after_migration(su):
    price = _peaks(on=5, off=7)
    legacy = _with_storage_unit(price, **su)
    status, _ = legacy.optimize(solver_name="highs")
    assert status == "ok"

    migrated = _with_storage_unit(price, **su)
    migrate_storage_unit(migrated, "bess")
    assert "bess" not in migrated.storage_units.index
    assert {"bess charger", "bess discharger"} <= set(migrated.links.index)
    assert migrated.stores.at["bess", "min_hours"] == migrated.stores.at["bess", "max_hours"] == su["max_hours"]
    assert _solve(migrated) == pytest.approx(float(legacy.objective), rel=1e-6)
    p, e = _p_e(migrated, "bess")
    assert e == pytest.approx(su["max_hours"] * p, rel=1e-6)


def test_migration_refuses_what_it_cannot_carry():
    n = _with_storage_unit(_peaks(on=5, off=7), p_nom=10.0, max_hours=2.0,
                           inflow=pd.Series(1.0, pd.RangeIndex(HOURS)))
    with pytest.raises(SplitStorageError, match="inflow"):
        migrate_storage_unit(n, "bess")
    assert "bess" in n.storage_units.index       # nothing changed


def test_two_technologies_compete_on_cost_in_one_run():
    def run(price):
        n = _site(price)
        add_split_storage(n, "fast", "ac", "high_c_bess", power_capital_cost=30e3, energy_capital_cost=20e3)
        add_split_storage(n, "slow", "ac", "energy_bess", power_capital_cost=40e3, energy_capital_cost=10e3)
        _solve(n)
        return {k: _p_e(n, k)[0] for k in ("fast", "slow")}

    # One-hour spikes: the 2 h minimum makes the energy BESS buy energy it never uses.
    short = run(_peaks(on=1, off=11, high=400.0))
    assert short["fast"] > 1.0 and short["slow"] < 1e-3
    # Six-hour peaks: the high-C BESS tops out at 1 h.
    long = run(_peaks(on=6, off=18))
    assert long["slow"] > 1.0 and long["fast"] < 1e-3


def test_the_wrapper_is_a_no_op_without_split_storage_and_chains_the_user_function():
    n = _site(_peaks(on=4, off=8))
    assert _wrap_with_split_storage(n, None) is None
    sentinel = object()
    assert _wrap_with_split_storage(n, sentinel) is sentinel

    seen = []
    add_split_storage(n, "bess", "ac", "energy_bess", power_capital_cost=30e3, energy_capital_cost=10e3)
    fn = _wrap_with_split_storage(n, lambda n_, s: seen.append(len(s)))
    n.optimize(solver_name="highs", extra_functionality=fn)
    assert seen == [HOURS]
    assert "split_storage_min_hours-bess" in n.model.constraints


def test_run_simulation_applies_the_split_storage_rows():
    import queue
    import threading

    from services import solver_service
    from services.pypsa_service import PyPSAService

    n = _site(_peaks(on=2, off=10))
    add_split_storage(n, "bess", "ac", "energy_bess", power_capital_cost=5e3, energy_capital_cost=40e3,
                      c_rate_max=0.25, p_nom_max=200.0, e_nom_max=2000.0)
    PyPSAService.set_network(n)
    log_q: queue.SimpleQueue = queue.SimpleQueue()
    status, condition = solver_service.run_simulation(
        solver_service.SolverConfig(), n, PyPSAService.get_lock(), threading.Event(), log_q,
        state_update=lambda **kw: None)
    lines = []
    while not log_q.empty():
        lines.append(str(log_q.get()))
    assert status == "ok", (condition, [ln for ln in lines if "ERROR" in ln or "TRACEBACK" in ln])
    assert any("[STORAGE]" in ln and "split-storage rows" in ln for ln in lines)
    p, e = _p_e(n, "bess")
    assert p > 1.0 and p == pytest.approx(0.25 * e, rel=1e-6)


def _run_issues(n):
    from services.solver_service import SolverConfig
    from services.validation_service import validate_for_run

    return {(i.code, i.name) for i in validate_for_run(n, SolverConfig()) if i.severity == "error"}


def test_validation_counts_a_priced_member_for_its_tied_partners():
    n = _site(_peaks(on=4, off=8))
    # Power priced on the charger only, energy unpriced: the discharger and Store are sized by the rows.
    add_split_storage(n, "bess", "ac", "energy_bess", power_capital_cost=30e3, p_nom_max=200.0, e_nom_max=2000.0)
    assert not {c for c, _ in _run_issues(n)} & {"store_no_capital_cost", "link_no_capital_cost"}

    # A migrated StorageUnit carries its whole cost on the power side.
    m = _with_storage_unit(_peaks(on=4, off=8), p_nom_extendable=True, p_nom_max=100.0, max_hours=4.0,
                           capital_cost=60e3)
    migrate_storage_unit(m, "bess")
    assert not {c for c, _ in _run_issues(m)} & {"store_no_capital_cost", "link_no_capital_cost",
                                                  "store_e_nom_bounds", "link_p_nom_bounds"}

    # Nothing priced at all is still "built free".
    free = _site(_peaks(on=4, off=8))
    add_split_storage(free, "bess", "ac", "energy_bess", p_nom_max=200.0, e_nom_max=2000.0)
    assert {("store_no_capital_cost", "bess"), ("link_no_capital_cost", "bess charger"),
            ("link_no_capital_cost", "bess discharger")} <= _run_issues(free)


# ── each row binds somewhere (a mutant that drops it fails here) ─────────────


def test_the_power_tie_binds_with_a_short_charge_window():
    # Ten expensive hours, two cheap ones: untied, the LP would buy a large charger and a small discharger.
    n = _site(_peaks(on=10, off=2))
    add_split_storage(n, "bess", "ac", "energy_bess", power_capital_cost=20e3, energy_capital_cost=5e3)
    _solve(n)
    p, e = _p_e(n, "bess")
    assert p > 1.0
    assert n.links.at["bess discharger", "p_nom_opt"] * 0.96 == pytest.approx(p, rel=1e-6)


def test_min_hours_binds_on_one_hour_spikes():
    # The default C-rate (1 / min_hours) enforces the same 2 h here; the hand case (4-4 h) pins min_hours alone.
    n = _site(_peaks(on=1, off=11, high=400.0))
    add_split_storage(n, "bess", "ac", "energy_bess", power_capital_cost=30e3, energy_capital_cost=10e3)
    _solve(n)
    p, e = _p_e(n, "bess")
    assert p > 1.0 and e / p == pytest.approx(2.0, rel=1e-6)


def test_max_hours_binds_on_long_peaks():
    n = _site(_peaks(on=3, off=9))
    add_split_storage(n, "fast", "ac", "high_c_bess", power_capital_cost=5e3, energy_capital_cost=5e3)
    _solve(n)
    p, e = _p_e(n, "fast")
    assert p > 1.0 and e / p == pytest.approx(1.0, rel=1e-6)


# ── fixed sizes are refused before the solve ─────────────────────────────────


def test_fixed_sizes_that_cannot_meet_the_hours_are_refused_before_the_solve():
    from services.solver.split_storage import size_conflicts

    n = _site(_peaks(on=4, off=8))
    add_split_storage(n, "bess", "ac", "energy_bess", p_nom=100.0, power_capital_cost=1.0,
                      energy_capital_cost=1.0, e_nom_max=150.0)       # 100 MW needs >= 200 MWh
    assert [name for name, _ in size_conflicts(n)] == ["bess"]
    assert ("split_storage_sizes", "bess") in _run_issues(n)

    n.links.at["bess charger", "p_nom_extendable"] = True           # one side extendable, the other fixed
    assert "both be extendable or both fixed" in size_conflicts(n)[0][1]

    ok = _with_storage_unit(_peaks(on=4, off=8), p_nom=30.0, max_hours=6.0)
    migrate_storage_unit(ok, "bess")                                 # both fixed, consistent
    assert size_conflicts(ok) == []
    ok.stores.at["bess", "e_nom"] = 100.0                            # 30 MW x 6 h is 180 MWh
    assert [name for name, _ in size_conflicts(ok)] == ["bess"]


# ── persistence ──────────────────────────────────────────────────────────────


def test_the_tags_survive_a_netcdf_round_trip_and_a_project_copy(tmp_path):
    from services.pypsa_service import PyPSAService
    from services.workflow_tools import _network_copy

    price = _peaks(on=2, off=10)
    n = _site(price)
    add_split_storage(n, "bess", "ac", "energy_bess", power_capital_cost=5e3, energy_capital_cost=40e3,
                      c_rate_max=0.25)
    path = tmp_path / "n.nc"
    PyPSAService.export_network_to_netcdf(n, path)
    back = pypsa.Network()
    PyPSAService.import_network_from_netcdf(back, path)
    copied = _network_copy(n)
    for m in (back, copied):
        assert m.stores.loc["bess", list(STORE_COLUMNS)].tolist() == n.stores.loc["bess", list(STORE_COLUMNS)].tolist()
    assert _solve(back) == pytest.approx(_solve(n), rel=1e-9)
    p, e = _p_e(back, "bess")
    assert p == pytest.approx(0.25 * e, rel=1e-6)                    # the reloaded store keeps its rows


# ── migration is exact or refused ────────────────────────────────────────────


@pytest.mark.parametrize("attr, value, why", [
    ("marginal_cost_storage", 0.5, "marginal_cost_storage"),
    ("cyclic_state_of_charge_per_period", True, "cyclic_state_of_charge_per_period"),
    ("spill_cost", 2.0, "spill_cost"),
])
def test_migration_refuses_a_static_attribute_it_does_not_map(attr, value, why):
    n = _with_storage_unit(_peaks(on=5, off=7), p_nom=10.0, max_hours=2.0, **{attr: value})
    with pytest.raises(SplitStorageError, match=why):
        migrate_storage_unit(n, "bess")
    assert "bess" in n.storage_units.index


@pytest.mark.parametrize("attr", ["p_max_pu", "p_min_pu", "efficiency_dispatch", "marginal_cost"])
def test_migration_refuses_a_time_varying_input(attr):
    base = {"p_max_pu": 0.9, "p_min_pu": -0.9, "efficiency_dispatch": 0.9, "marginal_cost": 1.0}[attr]
    n = _with_storage_unit(_peaks(on=5, off=7), p_nom=10.0, max_hours=2.0,
                           **{attr: pd.Series(base, pd.RangeIndex(HOURS))})
    with pytest.raises(SplitStorageError, match=f"{attr} varies in time"):
        migrate_storage_unit(n, "bess")


def test_migration_refuses_a_build_year_in_a_multi_period_network():
    n = _site(_peaks(on=5, off=7, hours=24))
    n.set_snapshots(pd.MultiIndex.from_product([[2030], range(24)]))
    n.set_investment_periods([2030])
    n.add("StorageUnit", "bess", bus="ac", p_nom=10.0, max_hours=2.0, build_year=2030)
    with pytest.raises(SplitStorageError, match="build_year .* multi-period"):
        migrate_storage_unit(n, "bess")


# ── input fingerprint ────────────────────────────────────────────────────────


def _fingerprints(n):
    from types import SimpleNamespace

    from services import workflow_tools as wt

    ctx = SimpleNamespace(network=n, solver_state={"solver_config": {}}, user_ts={})
    return wt._network_input_fingerprint(ctx), wt._network_input_fingerprint(ctx, exact=True)


def test_an_untagged_network_keeps_masters_fingerprint():
    # Computed with master's `_network_input_fingerprint` (before AS-02) on this network.
    n = pypsa.Network()
    n.set_snapshots(pd.RangeIndex(4))
    n.add("Bus", "ac", carrier="AC")
    n.add("Load", "load", bus="ac", p_set=50.0)
    n.add("Generator", "import", bus="ac", p_nom=1e4, marginal_cost=10.0)
    n.add("StorageUnit", "su", bus="ac", p_nom=10.0, max_hours=4.0)
    assert _fingerprints(n) == ("01aa7e777b1b1099ca597a49b43584875853df09a43019447178454c45b98d3a",
                                "a0b27942bed5c3925530ed47f8a8945dc3523fb7761ec710b37f06980280d91d")


@pytest.mark.parametrize("col, value", [("max_hours", 6.0), ("min_hours", 3.0), ("c_rate_max", 0.25),
                                        ("storage_technology", "high_c_bess")])
def test_editing_a_split_storage_tag_changes_both_fingerprints(col, value):
    n = _site(_peaks(on=4, off=8))
    add_split_storage(n, "bess", "ac", "energy_bess", power_capital_cost=30e3, energy_capital_cost=10e3)
    before = _fingerprints(n)
    n.stores.at["bess", col] = value
    after = _fingerprints(n)
    assert after[0] != before[0] and after[1] != before[1]


def test_a_two_period_myopic_run_applies_the_rows_in_every_period():
    import queue
    import threading

    from services import solver_service
    from services.pypsa_service import PyPSAService

    periods = [2030, 2035]
    price = _peaks(on=2, off=10, hours=24)
    n = pypsa.Network()
    idx = pd.MultiIndex.from_product([periods, pd.RangeIndex(24)], names=["period", "timestep"])
    n.set_snapshots(idx)
    n.investment_periods = periods
    n.snapshot_weightings.loc[:, "objective"] = 8760 / 24
    n.add("Bus", "ac", carrier="AC")
    n.add("Load", "load", bus="ac", p_set=50.0)
    n.add("Generator", "import", bus="ac", p_nom=1e4,
          marginal_cost=pd.Series(np.tile(price, len(periods)), n.snapshots))
    add_split_storage(n, "bess", "ac", "energy_bess", power_capital_cost=5e3, energy_capital_cost=40e3,
                      c_rate_max=0.25, p_nom_max=200.0, e_nom_max=2000.0)
    for frame in (n.stores, n.links):
        frame["lifetime"] = 50.0
    PyPSAService.set_network(n)
    log_q: queue.SimpleQueue = queue.SimpleQueue()
    cfg = solver_service.SolverConfig(solve_strategy="myopic", multi_investment_periods=True,
                                      investment_periods=periods, voll=0.0)
    status, condition = solver_service.run_simulation(
        cfg, n, PyPSAService.get_lock(), threading.Event(), log_q, state_update=lambda **kw: None)
    lines = []
    while not log_q.empty():
        lines.append(str(log_q.get()))
    assert status in ("ok", "optimal"), (condition, [ln for ln in lines if "ERROR" in ln or "TRACEBACK" in ln])
    assert sum("[STORAGE]" in ln and "split-storage rows" in ln for ln in lines) == len(periods)
    p, e = _p_e(n, "bess")
    assert p > 1.0 and p == pytest.approx(0.25 * e, rel=1e-6)
