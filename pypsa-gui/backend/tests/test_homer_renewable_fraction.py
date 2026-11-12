"""Minimum renewable fraction (homer-parity 01a, owner decision HOMER-GAPS-1).

HOMER's definition on the electrical side: f_ren = 1 − E_nonren / E_served,
E_served = electrical load − every slack's supply, E_nonren = non-renewable
Generators on electrical buses plus Link gensets fed from a fuel bus.
`min_renewable_fraction` (0–1, None/0 = off) adds one LP row per investment
period; `renewable_fraction(n)` is the KPI, shown whether the row is on or not.
"""
from __future__ import annotations

import queue
import threading

import numpy as np
import pandas as pd
import pypsa
import pytest
from pydantic import ValidationError

from models.schemas import SolverConfigSchema
from services.pypsa_service import PyPSAService
from services.solver.renewable_fraction import (
    _wrap_with_min_renewable_fraction,
    renewable_fraction,
    row_name,
)
from services.solver_service import SolverConfig, run_simulation

HOURS = 24
LOAD_MW = 10.0
# PV shape: 0 at night, 1 from 08:00 to 15:59 (8 h).
PV_PU = np.array([1.0 if 8 <= h < 16 else 0.0 for h in range(HOURS)])


def _network(*, pv_max: float = 100.0) -> pypsa.Network:
    """PV (extendable), a diesel genset, a battery: the 3-asset fixture."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=HOURS, freq="h"))
    for c in ("solar", "diesel", "battery"):
        n.add("Carrier", c)
    n.add("Bus", "ac", carrier="AC")
    n.add("Load", "site", bus="ac", p_set=LOAD_MW)
    n.add("Generator", "pv", bus="ac", carrier="solar", p_nom_extendable=True,
          p_nom_max=pv_max, capital_cost=40.0, p_max_pu=pd.Series(PV_PU, index=n.snapshots))
    n.add("Generator", "diesel", bus="ac", carrier="diesel", p_nom=LOAD_MW,
          marginal_cost=5.0)
    n.add("StorageUnit", "bess", bus="ac", carrier="battery", p_nom_extendable=True,
          max_hours=4.0, capital_cost=60.0, efficiency_store=0.95,
          efficiency_dispatch=0.95, cyclic_state_of_charge=True)
    return n


def _drain(q: queue.SimpleQueue) -> list[str]:
    out: list[str] = []
    while True:
        try:
            out.append(str(q.get_nowait()))
        except queue.Empty:
            return out


def _solve(n: pypsa.Network, **cfg_kw):
    PyPSAService.set_network(n)
    q: queue.SimpleQueue = queue.SimpleQueue()
    status, condition = run_simulation(
        SolverConfig(**cfg_kw), n, PyPSAService.get_lock(), threading.Event(),
        q, state_update=lambda **kw: None,
    )
    return status, condition, _drain(q)


# ── the input ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [-0.1, 1.5, float("nan")])
def test_the_input_rejects_values_outside_0_to_1(bad):
    with pytest.raises(ValidationError):
        SolverConfigSchema(min_renewable_fraction=bad)


@pytest.mark.parametrize("ok", [None, 0.0, 0.6, 1.0])
def test_the_input_accepts_a_fraction_or_none(ok):
    assert SolverConfigSchema(min_renewable_fraction=ok).min_renewable_fraction == ok


def test_the_input_defaults_off():
    assert SolverConfigSchema().min_renewable_fraction is None
    assert SolverConfig().min_renewable_fraction is None


# ── the KPI, hand-computed ────────────────────────────────────────────────

def _dispatched() -> pypsa.Network:
    """The fixture with a dispatch written by hand (no solve)."""
    n = _network()
    n.generators.loc["pv", "p_nom_opt"] = 20.0
    sns = n.snapshots
    n.loads_t.p = pd.DataFrame({"site": LOAD_MW}, index=sns)
    pv = pd.Series(PV_PU * 20.0, index=sns)                      # 160 MWh
    bess = pd.Series(0.0, index=sns)
    bess[PV_PU > 0] = -(20.0 - LOAD_MW)                          # charges 80 MWh
    night = [h for h in range(HOURS) if PV_PU[h] == 0][:7]
    bess.iloc[night] = LOAD_MW                                   # discharges 70 MWh
    diesel = (LOAD_MW - pv - bess).clip(lower=0.0)               # 160 − 70 = 90 MWh
    n.generators_t.p = pd.DataFrame({"pv": pv, "diesel": diesel})
    n.storage_units_t.p = pd.DataFrame({"bess": bess})
    return n


def test_the_kpi_equals_the_hand_computed_fraction():
    n = _dispatched()
    kpi = renewable_fraction(n)
    served = LOAD_MW * HOURS                                     # 240 MWh
    nonren = 90.0
    assert kpi["served_mwh"] == pytest.approx(served)
    assert kpi["nonrenewable_mwh"] == pytest.approx(nonren)
    assert kpi["renewable_mwh"] == pytest.approx(served - nonren)
    assert kpi["fraction"] == pytest.approx((served - nonren) / served)   # 0.625
    assert kpi["fraction"] == pytest.approx(0.625)


def test_the_kpi_counts_a_link_genset_from_a_fuel_bus_and_not_the_battery():
    n = _dispatched()
    n.add("Bus", "fuel", carrier="diesel")
    n.add("Generator", "tank", bus="fuel", carrier="diesel", p_nom=100.0)
    n.add("Link", "genset2", bus0="fuel", bus1="ac", carrier="diesel",
          efficiency=0.4, p_nom=25.0)
    # The Link genset takes 6 MWh of diesel's job: 15 MWh fuel × 0.4.
    n.links_t.p0 = pd.DataFrame({"genset2": 0.0}, index=n.snapshots)
    n.links_t.p0.iloc[7, 0] = 15.0   # 07:00, a diesel hour
    n.generators_t.p["tank"] = 0.0
    n.generators_t.p.iloc[7, n.generators_t.p.columns.get_loc("tank")] = 15.0
    n.generators_t.p.iloc[7, n.generators_t.p.columns.get_loc("diesel")] -= 6.0
    kpi = renewable_fraction(n)
    assert kpi["nonrenewable_mwh"] == pytest.approx(90.0)        # 84 + 6, fuel not counted twice
    assert kpi["fraction"] == pytest.approx(0.625)


def test_shed_load_is_not_served():
    n = _dispatched()
    kpi = renewable_fraction(n, unserved_mwh=40.0)
    assert kpi["served_mwh"] == pytest.approx(200.0)
    assert kpi["fraction"] == pytest.approx((200.0 - 90.0) / 200.0)


def test_the_kpi_is_unavailable_without_a_dispatch():
    kpi = renewable_fraction(_network())
    assert kpi["fraction"] is None and kpi["reason"]


# ── the LP row ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("f", [None, 0.0])
def test_off_leaves_the_lp_untouched(f):
    sentinel = object()
    assert _wrap_with_min_renewable_fraction(
        _network(), sentinel, SolverConfig(min_renewable_fraction=f)) is sentinel


def test_off_solve_is_identical_to_a_solve_without_the_field():
    a, b = _network(), _network()
    assert _solve(a)[0] == "ok"
    assert _solve(b, min_renewable_fraction=None)[0] == "ok"
    assert a.objective == b.objective
    pd.testing.assert_frame_equal(a.generators_t.p, b.generators_t.p)
    pd.testing.assert_frame_equal(a.storage_units_t.p, b.storage_units_t.p)


def test_the_row_forces_more_renewables_and_holds():
    free = _network()
    assert _solve(free)[0] == "ok"
    f_free = renewable_fraction(free)["fraction"]
    assert f_free < 0.8, f"fixture must start below the target, got {f_free}"
    n = _network()
    status, _, log = _solve(n, min_renewable_fraction=0.8)
    assert status == "ok", log
    assert renewable_fraction(n)["fraction"] >= 0.8 - 1e-6
    assert any(row_name(None) in line for line in log), log


def test_an_unreachable_target_is_infeasible_and_names_the_row():
    n = _network(pv_max=5.0)       # 40 MWh of PV against 240 MWh of load
    status, condition, log = _solve(n, min_renewable_fraction=0.9)
    assert status != "ok" and "infeasible" in str(condition).lower()
    hints = [line for line in log if line.startswith("[INFEASIBLE]")]
    assert any(row_name(None) in line for line in hints), "\n".join(log)


def test_one_row_per_investment_period():
    n = _network()
    n.set_investment_periods([2030, 2035])
    cfg = SolverConfig(min_renewable_fraction=0.5, multi_investment_periods=True)
    n.optimize.create_model(multi_investment_periods=True)
    fn = _wrap_with_min_renewable_fraction(n, None, cfg)
    fn(n, n.snapshots)
    names = set(n.model.constraints)
    assert {row_name(2030), row_name(2035)} <= names
    assert row_name(None) not in names
