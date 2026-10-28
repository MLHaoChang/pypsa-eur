"""The process units on the ``year`` time base (G2P-10): the plan solves them as a plain LP and the binary
check re-solves sampled weeks from the plan's state. GK A's conditions: the relaxation is undone on every
exit path, the window starts and ends where the plan is, and the reports say what was checked."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pypsa
import pytest

from services.solver import process_uc as PU
from tests.test_process_uc_solve_path import _run

UNIT_ATTRS = ["committable", "p_min_pu", "min_up_time", "min_down_time", "up_time_before",
              "down_time_before", "start_up_cost", "shut_down_cost"]


def _year_net() -> pypsa.Network:
    """``test_process_uc_solve_path._process_net`` on one chronological year: the unit wants to stop in
    the 4 dear hours of each 12, below its minimum load."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2027-01-01", periods=PU.YEAR_HOURS, freq="h"))
    n.add("Carrier", ["AC", "NH3"])
    n.add("Bus", "el", carrier="AC")
    n.add("Bus", "nh3", carrier="NH3", unit="t")
    price = np.where((np.arange(PU.YEAR_HOURS) % 12) < 4, 500.0, 20.0)
    n.add("Generator", "grid", bus="el", p_nom=100.0, marginal_cost=price, carrier="AC")
    n.add("Link", "hb", bus0="el", bus1="nh3", p_nom=10.0, efficiency=0.5, committable=True,
          p_min_pu=0.4, min_down_time=3, carrier="NH3")
    n.links["eh_process"] = True
    n.add("Load", "offtake", bus="nh3", p_set=3.0, carrier="NH3")
    n.add("Store", "tank", bus="nh3", e_nom=20.0, e_cyclic=True, carrier="NH3")
    n.add("Generator", "__lost_NH3", bus="nh3", p_nom=1e3, marginal_cost=2000.0, carrier="NH3")
    n.generators["process_role"] = ""
    n.generators.loc["__lost_NH3", "process_role"] = "shortfall"
    return n


def _units(n) -> pd.DataFrame:
    return n.links[UNIT_ATTRS].copy()


def _fingerprint():
    from services.pypsa_service import PyPSAService
    from services.workflow_tools import _network_input_fingerprint

    return _network_input_fingerprint(PyPSAService.get_active_context(), exact=True)


def _install(n):
    from services.pypsa_service import PyPSAService
    from tests.conftest import install_network_into_backend

    install_network_into_backend(n)
    return PyPSAService.get_network()


@pytest.mark.live_solve
def test_the_plan_is_plain_and_the_units_are_byte_equal_after_the_solve(reset_backend):
    from services.solver_service import SolverConfig

    assert PU.planning_uc_mode(_year_net(), SolverConfig()) == "plain"
    before, before_t = _units(_year_net()), _year_net().links_t.p_min_pu.copy()
    _install(_year_net())
    fp = _fingerprint()
    n, (status, _), state, lines = _run(_year_net())
    assert status in ("ok", "optimal")
    pd.testing.assert_frame_equal(_units(n), before)
    pd.testing.assert_frame_equal(n.links_t.p_min_pu, before_t)
    assert _fingerprint() == fp
    assert any(PU.PLAIN_NOTE in line for line in lines)
    check = state["last_process_uc_check"]
    assert check["status"] == "ok", check["message"]
    assert check["message"].startswith(PU.PLAIN_NOTE)
    assert len(check["periods"]) == len(PU.YEAR_WINDOW_DAYS)
    assert check["worst"]["window"] in [r["window"] for r in check["periods"]]
    assert any(r["item"] == "worst window" for r in check["ledger"])
    assert PU.PLAIN_NOTE in check["ledger"][0]["value"]


@pytest.mark.live_solve
def test_a_raising_solve_still_puts_the_units_back(reset_backend, monkeypatch):
    before = _units(_year_net())
    _install(_year_net())
    fp = _fingerprint()

    seen = []

    def boom(self, *a, **k):
        seen.append(_units(self._n).loc["hb", ["committable", "p_min_pu"]].tolist())
        raise RuntimeError("solver crashed mid-plan")

    monkeypatch.setattr(pypsa.optimization.OptimizationAccessor, "__call__", boom)
    n, (status, _), _, _ = _run(_year_net())
    assert status not in ("ok", "optimal")
    assert seen and seen[0] == [False, 0.0], "the plan solve saw the units relaxed"
    pd.testing.assert_frame_equal(_units(n), before)
    assert _fingerprint() == fp


def _solved_plain():
    """The year net planned as the plain LP, the units then put back (as before the check)."""
    from services.solver_service import SolverConfig

    n = _year_net()
    undo = PU.relax_for_plan(n, SolverConfig())
    n.optimize(solver_name="highs")
    undo()
    return n


def test_a_window_starting_on_a_fractional_plan_hour_starts_the_unit_off():
    n = _solved_plain()
    first = PU.YEAR_WINDOW_DAYS[1] * 24
    p_min = 0.4 * 10.0
    # The plan's hour before the window: running, but below the minimum load (a fractional commitment).
    n.links_t.p0.iloc[first - 3:first, n.links_t.p0.columns.get_loc("hb")] = 0.5 * p_min
    m = PU.window_copy(n, None, first)
    assert m.links.at["hb", "up_time_before"] == 0
    assert m.links.at["hb", "down_time_before"] >= 3
    # At the minimum load it is on.
    n.links_t.p0.iloc[first - 2:first, n.links_t.p0.columns.get_loc("hb")] = p_min
    m = PU.window_copy(n, None, first)
    assert m.links.at["hb", "up_time_before"] == 2 and m.links.at["hb", "down_time_before"] == 0


def test_a_window_starts_at_the_plans_levels_and_ends_no_lower():
    n = _solved_plain()
    first = PU.YEAR_WINDOW_DAYS[2] * 24
    sns = n.snapshots
    m = PU.window_copy(n, None, first)
    assert m.stores.at["tank", "e_initial"] == pytest.approx(n.stores_t.e.at[sns[first - 1], "tank"])
    assert not m.stores.at["tank", "e_cyclic"]
    last = sns[first + PU.CHECK_WINDOW - 1]
    floor = n.stores_t.e.at[last, "tank"] / 20.0
    assert m.stores_t.e_min_pu.at[last, "tank"] == pytest.approx(max(floor, 0.0))
    assert (m.generators_t.p_max_pu.loc[sns[first:first + PU.CHECK_WINDOW], "__lost_NH3"] == 1.0).all()


def test_the_end_level_binds_in_the_binary_window():
    """Dropping the end pin lets the window run the tank down for free: pinned, it ends at the plan."""
    n = _solved_plain()
    first = PU.YEAR_WINDOW_DAYS[0] * 24
    sns = n.snapshots[first:first + PU.CHECK_WINDOW]
    m = PU.window_copy(n, None, first)
    m.optimize(snapshots=sns, solver_name="highs")
    assert m.stores_t.e.at[sns[-1], "tank"] >= n.stores_t.e.at[sns[-1], "tank"] - 1e-6


def test_the_windows_solve_side_by_side_with_the_same_result(monkeypatch):
    from services.solver_service import SolverConfig

    n = _solved_plain()
    results = {}
    for workers in (1, 3):
        monkeypatch.setattr(PU, "check_workers", lambda w=workers: w)
        check = PU.binary_check(n, SolverConfig())
        assert check["workers"] == workers and check["status"] == "ok", check["message"]
        results[workers] = [(r["window"], r["cost_lin"], r["cost_bin"]) for r in check["periods"]]
    assert [w for w, *_ in results[3]] == [w for w, *_ in results[1]]
    for (_, lin1, bin1), (_, lin3, bin3) in zip(results[1], results[3]):
        assert lin3 == pytest.approx(lin1, rel=1e-6) and bin3 == pytest.approx(bin1, rel=1e-3)


def test_the_check_leaves_a_core_to_the_plan_and_never_drops_below_one(monkeypatch):
    monkeypatch.setattr(PU.os, "cpu_count", lambda: 4)
    assert PU.check_workers() == 3
    monkeypatch.setattr(PU.os, "cpu_count", lambda: 2)
    assert PU.check_workers() == 1
    monkeypatch.setattr(PU.os, "cpu_count", lambda: None)
    assert PU.check_workers() == 1


def test_the_worst_window_ignores_shortfall_noise():
    def row(window, pct, extra, above):
        return {"window": window, "pct": pct,
                "extra_shortfall": {"b": {"value": extra, "above_tolerance": above}}}

    rows = [row("jan", 0.02, 0.0, False), row("oct", 0.009, 4e-10, False)]
    assert PU._worst(rows)["window"] == "jan"
    rows.append(row("jul", 0.001, 5.0, True))
    assert PU._worst(rows)["window"] == "jul"
