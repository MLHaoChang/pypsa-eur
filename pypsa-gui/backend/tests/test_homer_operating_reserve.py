"""homer-parity 01b: the operating reserve (per-snapshot dispatchable headroom). GK B plan conditions O1–O9."""

from __future__ import annotations

import queue

import numpy as np
import pandas as pd
import pypsa
import pytest
from pydantic import ValidationError

from models.schemas import SolverConfigSchema
from services.reliability_goal import goal_free_config, invalid_goal_fields
from services.solver.operating_reserve import (
    ROW,
    _wrap_with_operating_reserve,
    assets,
    infeasibility_lines,
    operating_reserve_result,
    shares_of,
)
from services.solver_service import SolverConfig, build_lp_chain

HOURS = 24
LOAD_MW = 10.0
PV_PU = np.array([1.0 if 8 <= h < 16 else 0.0 for h in range(HOURS)])
NOON = 12


def _network(*, committable=True, extendable=False, battery=False) -> pypsa.Network:
    """PV (fixed 30 MW, cheap) and a diesel genset; the free optimum runs the genset only at night."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=HOURS, freq="h"))
    for c in ("solar", "diesel", "battery"):
        n.add("Carrier", c)
    n.add("Bus", "ac", carrier="AC")
    n.add("Load", "site", bus="ac", p_set=LOAD_MW)
    n.add("Generator", "pv", bus="ac", carrier="solar", p_nom=30.0, marginal_cost=0.0,
          p_max_pu=pd.Series(PV_PU, index=n.snapshots))
    kw = dict(bus="ac", carrier="diesel", marginal_cost=250.0)
    if committable:
        kw.update(committable=True, p_min_pu=0.3, p_nom=12.0, start_up_cost=10.0)
    elif extendable:
        kw.update(p_nom_extendable=True, capital_cost=1_000.0)
    else:
        kw.update(p_nom=12.0)
    n.add("Generator", "diesel", **kw)
    if battery:
        n.add("StorageUnit", "bess", bus="ac", carrier="battery", p_nom=10.0, max_hours=1.0,
              efficiency_dispatch=0.9, efficiency_store=0.9, cyclic_state_of_charge=True)
    return n


def _solve(n, shares=None):
    q: queue.SimpleQueue = queue.SimpleQueue()
    fn = _wrap_with_operating_reserve(n, None, SolverConfig(**(shares or {})), q)
    status, _ = n.optimize(solver_name="highs", extra_functionality=fn)
    assert status == "ok"
    out = []
    while not q.empty():
        out.append(str(q.get_nowait()))
    return out


ON = {"reserve_share_load": 0.2}


# ── O1: off is byte-identical ───────────────────────────────────────────────


@pytest.mark.parametrize("off", [None, 0.0])
def test_off_changes_no_fingerprint_and_adds_no_row(off):
    base = goal_free_config(SolverConfig())
    cfg = SolverConfig(reserve_share_load=off, reserve_share_pv=off, reserve_share_wind=off)
    assert goal_free_config(cfg) == base
    assert shares_of(cfg) is None
    assert _wrap_with_operating_reserve(None, None, cfg) is None  # the user's fn, unchanged
    n = _network()
    n.optimize.create_model()
    fn = build_lp_chain(n, cfg, queue.SimpleQueue())
    if fn is not None:
        fn(n, n.snapshots)
    assert ROW not in n.model.constraints


@pytest.mark.parametrize("key", ["reserve_share_load", "reserve_share_pv", "reserve_share_wind"])
def test_on_enters_the_fingerprint(key):
    on = goal_free_config(SolverConfig(**{key: 0.1}))
    assert on != goal_free_config(SolverConfig()) and on[key] == 0.1


# ── O9: refusals through one validator ──────────────────────────────────────


@pytest.mark.parametrize("bad", [-0.1, 1.5, float("nan"), float("inf")])
@pytest.mark.parametrize("key", ["reserve_share_load", "reserve_share_pv", "reserve_share_wind"])
def test_the_schema_refuses_out_of_range_and_non_finite(key, bad):
    with pytest.raises(ValidationError):
        SolverConfigSchema(**{key: bad})


@pytest.mark.parametrize("key", ["reserve_share_load", "reserve_share_pv", "reserve_share_wind"])
def test_a_load_or_import_names_an_invalid_share(key):
    assert invalid_goal_fields({key: 1.5}) == {key: 1.5}
    assert invalid_goal_fields({key: 0.5}) == {}


# ── the rows ────────────────────────────────────────────────────────────────


def test_one_row_per_snapshot():
    """O9: per snapshot, never one summed row."""
    n = _network()
    n.optimize.create_model()
    _wrap_with_operating_reserve(n, None, SolverConfig(**ON))(n, n.snapshots)
    assert n.model.constraints[ROW].shape == (HOURS,)


def test_a_committable_genset_must_run_at_an_hour_it_otherwise_would_not():
    """The acceptance case (Q3 (a)): at noon PV covers the load, so the free solve keeps the genset off; a 20 %
    load reserve needs 2 MW of headroom, which only a committed genset holds."""
    free = _network()
    _solve(free)
    assert free.generators_t.status.at[free.snapshots[NOON], "diesel"] == 0
    n = _network()
    _solve(n, ON)
    t = n.snapshots[NOON]
    assert n.generators_t.status.at[t, "diesel"] == 1
    # hand check: headroom 12·1 − p ≥ 0.2·10
    assert 12.0 - n.generators_t.p.at[t, "diesel"] >= 2.0 - 1e-6
    res = operating_reserve_result(n, shares_of(SolverConfig(**ON)))
    assert res["reason"] is None, res
    assert all(r["held_mw"] >= r["required_mw"] - 1e-6 for r in res["per_snapshot"])
    assert res["per_snapshot"][NOON]["required_mw"] == pytest.approx(2.0)


def test_a_non_committable_genset_is_sized_for_the_reserve():
    """In an LP an idle unit already holds its capacity, so the reserve binds on capacity: at night the
    genset serves 10 MW and must keep 5 MW spare, so p_nom_opt = 15 MW."""
    n = _network(committable=False, extendable=True)
    _solve(n, {"reserve_share_load": 0.5})
    assert n.generators.at["diesel", "p_nom_opt"] == pytest.approx(15.0, rel=1e-6)
    res = operating_reserve_result(n, (0.5, 0.0, 0.0))
    night = str(n.snapshots[0])
    assert night in res["binding"], res["binding"]


def test_pv_output_raises_the_requirement_by_its_dispatched_output():
    """Q1 (a): b·PV p(t), the dispatched output."""
    n = _network()
    _solve(n, {"reserve_share_pv": 0.1})
    res = operating_reserve_result(n, (0.0, 0.1, 0.0))
    t = n.snapshots[NOON]
    assert res["per_snapshot"][NOON]["required_mw"] == pytest.approx(0.1 * n.generators_t.p.at[t, "pv"])
    assert res["per_snapshot"][0]["required_mw"] == 0.0


def test_storage_holds_reserve_up_to_its_state_of_charge():
    """O4: r ≤ p_nom·p_max_pu − p_dispatch + p_store and r ≤ p_store + η·soc(t)/w(t)."""
    n = _network(committable=False, battery=True)
    sns = n.snapshots
    n.snapshot_weightings.loc[:, "stores"] = 2.0
    n.generators_t.p = pd.DataFrame({"pv": 0.0, "diesel": 0.0}, index=sns)
    n.generators.loc["diesel", "p_nom"] = 0.0
    n.loads_t.p = pd.DataFrame({"site": LOAD_MW}, index=sns)
    n.storage_units_t.p_dispatch = pd.DataFrame({"bess": 1.0}, index=sns)
    n.storage_units_t.p_store = pd.DataFrame({"bess": 0.0}, index=sns)
    n.storage_units_t.state_of_charge = pd.DataFrame({"bess": 4.0}, index=sns)
    res = operating_reserve_result(n, (0.5, 0.0, 0.0))
    # power 10 − 1 = 9; energy 0.9·4/2 = 1.8
    assert res["per_snapshot"][0]["held_mw"] == pytest.approx(1.8)


def test_a_battery_carries_the_reserve_in_the_lp():
    n = _network(committable=False, battery=True)
    n.generators.loc["diesel", "p_nom"] = 10.0
    _solve(n, {"reserve_share_load": 0.3})
    res = operating_reserve_result(n, (0.3, 0.0, 0.0))
    assert res["reason"] is None
    assert all(r["held_mw"] >= r["required_mw"] - 1e-6 for r in res["per_snapshot"])
    # at night the diesel serves the full 10 MW (no headroom): the 3 MW are the battery's
    assert any(r["held_mw"] >= 3.0 - 1e-6 and r["binding"] for r in res["per_snapshot"][:8])


def test_slacks_and_withdrawing_generators_hold_no_headroom():
    """O3: VoLL slack and a withdrawing Generator are never headroom."""
    n = _network()
    n.add("Generator", "__voll_ac", bus="ac", carrier="load_shedding", p_nom=100.0, marginal_cost=1e4)
    n.add("Generator", "dump", bus="ac", carrier="diesel", p_nom=5.0, sign=-1.0)
    sel = assets(n)
    assert sel["dispatchable"] == ["diesel"], sel
    assert sel["pv"] == ["pv"] and sel["wind"] == []


def test_a_store_battery_is_listed_as_not_counted():
    n = _network()
    n.add("Bus", "batt", carrier="battery")
    n.add("Store", "cells", bus="batt", e_nom=10.0)
    n.add("Link", "discharger", bus0="batt", bus1="ac", p_nom=5.0)
    assert assets(n)["not_counted"] == ["cells"]


def test_binding_is_by_slack_on_a_milp():
    """O2: a committable unit makes the solve a MILP; binding is read from the solved values."""
    n = _network()
    _solve(n, ON)
    res = operating_reserve_result(n, (0.2, 0.0, 0.0))
    for r in res["per_snapshot"]:
        slack = r["held_mw"] - r["required_mw"]
        assert r["binding"] == (r["required_mw"] > 0 and slack <= 1e-6 * r["required_mw"] + 1e-6)


def test_a_missing_dispatch_column_is_unknown_not_zero():
    """O6."""
    n = _network()
    _solve(n, ON)
    n.generators_t.p = n.generators_t.p.drop(columns="diesel")
    res = operating_reserve_result(n, (0.2, 0.0, 0.0))
    assert res["per_snapshot"] == [] and "diesel" in res["reason"]


def test_off_result_says_so():
    n = _network()
    _solve(n)
    assert operating_reserve_result(n, None)["reason"] is not None


# ── O7: the infeasible line ─────────────────────────────────────────────────


def test_the_infeasible_line_names_the_snapshots_only_when_capacity_cannot_cover_the_load_share():
    n = _network()
    lines = infeasibility_lines(n, SolverConfig(reserve_share_load=1.0))  # 10 MW reserve > 12 − ... no: 12 ≥ 10
    assert lines and "may contribute" in lines[0]
    n.generators.loc["diesel", "p_nom"] = 5.0
    lines = infeasibility_lines(n, SolverConfig(reserve_share_load=1.0))
    assert "needs 10.0 MW" in lines[0] and "2030-01-01" in lines[0] and ROW in lines[0], lines
    assert infeasibility_lines(n, SolverConfig()) == []


# ── O8: every solve path builds its LP from the same chain ──────────────────


def test_the_lp_chain_carries_the_reserve():
    n = _network()
    n.optimize.create_model()
    build_lp_chain(n, SolverConfig(**ON), queue.SimpleQueue())(n, n.snapshots)
    assert n.model.constraints[ROW].shape == (HOURS,)
