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
