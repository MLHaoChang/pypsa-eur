"""homer-parity 01b: the operating reserve (per-snapshot dispatchable headroom). GK B plan conditions O1–O9."""

from __future__ import annotations

import math
import queue

import numpy as np
import pandas as pd
import pypsa
import pytest
from pydantic import ValidationError

from models.schemas import SolverConfigSchema
from services.reliability_goal import goal_free_config, invalid_goal_fields
from services.solver.operating_reserve import (
    _AUX,
    _label,
    ROW,
    SHARES,
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
    n.add(
        "Generator",
        "pv",
        bus="ac",
        carrier="solar",
        p_nom=30.0,
        marginal_cost=0.0,
        p_max_pu=pd.Series(PV_PU, index=n.snapshots),
    )
    kw = dict(bus="ac", carrier="diesel", marginal_cost=250.0)
    if committable:
        kw.update(committable=True, p_min_pu=0.3, p_nom=12.0, start_up_cost=10.0)
    elif extendable:
        kw.update(p_nom_extendable=True, capital_cost=1_000.0)
    else:
        kw.update(p_nom=12.0)
    n.add("Generator", "diesel", **kw)
    if battery:
        n.add(
            "StorageUnit",
            "bess",
            bus="ac",
            carrier="battery",
            p_nom=10.0,
            max_hours=1.0,
            efficiency_dispatch=0.9,
            efficiency_store=0.9,
            cyclic_state_of_charge=True,
        )
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
    cfg = SolverConfig(
        reserve_share_load=off, reserve_share_pv=off, reserve_share_wind=off
    )
    assert goal_free_config(cfg) == base
    assert shares_of(cfg) is None
    assert (
        _wrap_with_operating_reserve(None, None, cfg) is None
    )  # the user's fn, unchanged
    n = _network()
    n.optimize.create_model()
    fn = build_lp_chain(n, cfg, queue.SimpleQueue())
    if fn is not None:
        fn(n, n.snapshots)
    assert ROW not in n.model.constraints


@pytest.mark.parametrize(
    "key", ["reserve_share_load", "reserve_share_pv", "reserve_share_wind"]
)
def test_on_enters_the_fingerprint(key):
    on = goal_free_config(SolverConfig(**{key: 0.1}))
    assert on != goal_free_config(SolverConfig()) and on[key] == 0.1


# ── O9: refusals through one validator ──────────────────────────────────────


@pytest.mark.parametrize("bad", [-0.1, 1.5, float("nan"), float("inf")])
@pytest.mark.parametrize(
    "key", ["reserve_share_load", "reserve_share_pv", "reserve_share_wind"]
)
def test_the_schema_refuses_out_of_range_and_non_finite(key, bad):
    with pytest.raises(ValidationError):
        SolverConfigSchema(**{key: bad})


@pytest.mark.parametrize(
    "key", ["reserve_share_load", "reserve_share_pv", "reserve_share_wind"]
)
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
    night = _label(n.snapshots[0])
    assert night in res["binding"], res["binding"]


def test_pv_output_raises_the_requirement_by_its_dispatched_output():
    """Q1 (a): b·PV p(t), the dispatched output."""
    n = _network()
    _solve(n, {"reserve_share_pv": 0.1})
    res = operating_reserve_result(n, (0.0, 0.1, 0.0))
    t = n.snapshots[NOON]
    assert res["per_snapshot"][NOON]["required_mw"] == pytest.approx(
        0.1 * n.generators_t.p.at[t, "pv"]
    )
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
    assert any(
        r["held_mw"] >= 3.0 - 1e-6 and r["binding"] for r in res["per_snapshot"][:8]
    )


def test_slacks_and_withdrawing_generators_hold_no_headroom():
    """O3: VoLL slack and a withdrawing Generator are never headroom."""
    n = _network()
    n.add(
        "Generator",
        "__voll_ac",
        bus="ac",
        carrier="load_shedding",
        p_nom=100.0,
        marginal_cost=1e4,
    )
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
        assert r["binding"] == (
            r["required_mw"] > 0 and slack <= 1e-6 * r["required_mw"] + 1e-6
        )


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
    lines = infeasibility_lines(
        n, SolverConfig(reserve_share_load=1.0)
    )  # 10 MW reserve > 12 − ... no: 12 ≥ 10
    assert lines and "may contribute" in lines[0]
    n.generators.loc["diesel", "p_nom"] = 5.0
    lines = infeasibility_lines(n, SolverConfig(reserve_share_load=1.0))
    assert (
        "needs 10.0 MW" in lines[0] and "2030-01-01" in lines[0] and ROW in lines[0]
    ), lines
    assert infeasibility_lines(n, SolverConfig()) == []


# ── O8: every solve path builds its LP from the same chain ──────────────────


def test_the_lp_chain_carries_the_reserve():
    n = _network()
    n.optimize.create_model()
    build_lp_chain(n, SolverConfig(**ON), queue.SimpleQueue())(n, n.snapshots)
    assert n.model.constraints[ROW].shape == (HOURS,)


# ── review fixes ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "carrier, kind", [("pv_rooftop", "pv"), ("PVrooftop", "pv"), ("onwind", "wind")]
)
def test_vre_carriers_match_by_substring_like_the_kpi(carrier, kind):
    """O3/O5: the same carrier match as the renewable KPI, so rooftop PV is never dispatchable."""
    n = _network()
    n.add("Carrier", carrier)
    n.add("Generator", "vre", bus="ac", carrier=carrier, p_nom=5.0)
    sel = assets(n)
    assert "vre" in sel[kind] and "vre" not in sel["dispatchable"], sel


def test_run_of_river_is_not_dispatchable():
    n = _network()
    n.add("Generator", "river", bus="ac", carrier="ror_alpine", p_nom=5.0)
    assert "river" not in assets(n)["dispatchable"]


def test_a_withdrawing_renewable_carrier_generator_is_not_headroom():
    """O3: an export modelled as a withdrawing biomass Generator is a sink, never headroom."""
    n = _network()
    n.add(
        "Generator",
        "export",
        bus="ac",
        carrier="biomass",
        p_nom=5.0,
        p_min_pu=-1.0,
        p_max_pu=0.0,
    )
    assert "export" not in assets(n)["dispatchable"]


def test_a_charging_battery_does_not_count_its_cancelled_charge_twice():
    """O4: charging 10 MW from empty (η 0.9/0.9, w 1, soc(t) = 9): cancelling the charge frees 10 MW, and the
    step then ends empty, so 10 MW, not 10 + 0.9·9."""
    n = _network(committable=False, battery=True)
    sns = n.snapshots
    n.generators.loc["diesel", "p_nom"] = 0.0
    n.generators_t.p = pd.DataFrame({"pv": 0.0, "diesel": 0.0}, index=sns)
    n.loads_t.p = pd.DataFrame({"site": LOAD_MW}, index=sns)
    n.storage_units_t.p_dispatch = pd.DataFrame({"bess": 0.0}, index=sns)
    n.storage_units_t.p_store = pd.DataFrame({"bess": 10.0}, index=sns)
    n.storage_units_t.state_of_charge = pd.DataFrame({"bess": 9.0}, index=sns)
    res = operating_reserve_result(n, (1.0, 0.0, 0.0))
    assert res["per_snapshot"][0]["held_mw"] == pytest.approx(10.0)


def test_a_charging_battery_in_the_lp_holds_what_the_route_reports():
    n = _network(committable=False, battery=True)
    n.generators.loc["diesel", "p_nom"] = 12.0
    _solve(n, {"reserve_share_load": 0.2})
    res = operating_reserve_result(n, (0.2, 0.0, 0.0))
    assert res["reason"] is None
    assert all(r["held_mw"] >= r["required_mw"] - 1e-5 for r in res["per_snapshot"])


def test_a_committable_extendable_genset_holds_status_times_its_size():
    """A unit both committable and extendable: h ≤ p_nom·p_max_pu − p and h ≤ p_nom_max·status·p_max_pu − p."""
    n = _network(committable=False)
    n.remove("Generator", "diesel")
    n.add(
        "Generator",
        "diesel",
        bus="ac",
        carrier="diesel",
        marginal_cost=250.0,
        committable=True,
        p_nom_extendable=True,
        p_nom_max=40.0,
        p_min_pu=0.3,
        capital_cost=1_000.0,
    )
    _solve(n, {"reserve_share_load": 0.5})
    assert n.generators.at["diesel", "p_nom_opt"] == pytest.approx(15.0, rel=1e-5)
    t = n.snapshots[NOON]
    assert (
        n.generators_t.status.at[t, "diesel"] == 1
    )  # the reserve makes it run under the sun too
    res = operating_reserve_result(n, (0.5, 0.0, 0.0))
    assert all(r["held_mw"] >= r["required_mw"] - 1e-5 for r in res["per_snapshot"])


def test_a_unit_holds_reserve_only_in_the_periods_it_is_in_service():
    """A genset built in 2040 holds nothing in 2030 (build year and lifetime, per period)."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.set_investment_periods([2030, 2040])
    n.add("Bus", "ac", carrier="AC")
    n.add("Load", "site", bus="ac", p_set=LOAD_MW)
    n.add(
        "Generator",
        "old",
        bus="ac",
        carrier="diesel",
        p_nom=5.0,
        build_year=2020,
        lifetime=15,
    )
    n.add(
        "Generator",
        "new",
        bus="ac",
        carrier="diesel",
        p_nom=30.0,
        build_year=2040,
        lifetime=30,
    )
    lines = infeasibility_lines(n, SolverConfig(reserve_share_load=1.0))
    assert "2030 2030-01-01 00:00" in lines[0], lines  # 2030: only 'old' (5 MW) < 10 MW
    assert "2040" not in lines[0].split(" the reserve needs")[0], (
        lines
    )  # 2040: 'new' (30 MW) covers it


def test_off_adds_no_auxiliary_variable_and_on_does():
    n = _network(battery=True)
    n.optimize.create_model()
    fn = build_lp_chain(n, SolverConfig(), queue.SimpleQueue())
    if fn is not None:
        fn(n, n.snapshots)
    assert _AUX not in n.model.variables
    m = _network(battery=True)
    m.optimize.create_model()
    build_lp_chain(m, SolverConfig(**ON), queue.SimpleQueue())(m, m.snapshots)
    assert _AUX in m.model.variables


def _run(n, **cfg):
    import threading

    from services.pypsa_service import PyPSAService
    from services.solver_service import run_simulation

    PyPSAService.set_network(n)
    q: queue.SimpleQueue = queue.SimpleQueue()
    sink: dict = {}
    status, _ = run_simulation(
        SolverConfig(**cfg),
        n,
        PyPSAService.get_lock(),
        threading.Event(),
        q,
        state_update=lambda **kw: sink.update(kw),
    )
    log = []
    while not q.empty():
        log.append(str(q.get_nowait()))
    return status, sink, log


def test_last_solve_settings_carry_the_shares_only_when_on():
    """O1: an off solve's settings are byte-identical to before 01b."""
    status, sink, log = _run(_network())
    assert status == "ok", log[-5:]
    assert not any(k.startswith("reserve_share") for k in sink["last_solve_settings"])
    status, sink, log = _run(_network(), **ON)
    assert status == "ok", log[-5:]
    assert sink["last_solve_settings"]["reserve_share_load"] == 0.2


def test_every_myopic_horizon_builds_the_reserve_rows():
    """O8: the myopic path chains the same wrapper; the row is built in each period's LP."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.set_investment_periods([2030, 2040])
    n.add("Bus", "ac", carrier="AC")
    n.add("Load", "site", bus="ac", p_set=LOAD_MW)
    n.add(
        "Generator",
        "diesel",
        bus="ac",
        carrier="diesel",
        p_nom_extendable=True,
        capital_cost=100.0,
        marginal_cost=250.0,
        lifetime=50,
        build_year=2030,
        p_nom_max=100.0,
    )
    status, _, log = _run(
        n,
        solve_strategy="myopic",
        multi_investment_periods=True,
        reserve_share_load=0.5,
    )
    assert status == "ok", [l for l in log if "ERROR" in l or "rror" in l][:6]
    assert sum(f"(row {ROW}," in l for l in log) == 2, [
        l for l in log if "Operating reserve" in l
    ]


# ── mutant killers: the LP coefficients, the result reader, the asset sets ──


def _terms(m, con, t, name=None):
    """{(variable, name): coefficient} of one row of ``con`` (duplicates summed, zeros dropped), its rhs and sign."""
    c = m.constraints[con]
    at = {"snapshot": t} | ({} if name is None else {"name": name})
    lhs = c.lhs.sel(at)
    out: dict = {}
    for v, k in zip(lhs.vars.values.ravel(), lhs.coeffs.values.ravel()):
        if v != -1:
            var, pos = m.variables.get_label_position(int(v))
            out[(var, pos.get("name"))] = out.get((var, pos.get("name")), 0.0) + k
    terms = {key: k for key, k in out.items() if k != 0}
    return terms, float(c.rhs.sel(at)), str(c.sign.sel(at).item())


def _rows(n, shares):
    q: queue.SimpleQueue = queue.SimpleQueue()
    n.optimize.create_model()
    cfg = dict(
        zip(("reserve_share_load", "reserve_share_pv", "reserve_share_wind"), shares)
    )
    _wrap_with_operating_reserve(n, None, SolverConfig(**cfg), q)(n, n.snapshots)
    out = []
    while not q.empty():
        out.append(str(q.get_nowait()))
    return n.model, out


def _mixed_fleet() -> pypsa.Network:
    """One unit of each kind (fixed, extendable, committable, both), PV, wind and two batteries."""
    n = _network(committable=False)
    n.remove("Generator", "diesel")
    n.add("Carrier", "onwind")
    n.add("Generator", "wt", bus="ac", carrier="onwind", p_nom=5.0)
    d = dict(bus="ac", carrier="diesel", marginal_cost=250.0)
    n.add("Generator", "fix", p_nom=12.0, p_max_pu=0.5, **d)
    n.add("Generator", "ext", p_nom=5.0, p_nom_extendable=True, p_max_pu=0.8, **d)
    n.add("Generator", "uc", p_nom=6.0, committable=True, p_max_pu=0.9, **d)
    n.add(
        "Generator",
        "both",
        p_nom=4.0,
        p_nom_max=20.0,
        p_nom_extendable=True,
        committable=True,
        p_max_pu=0.7,
        **d,
    )
    s = dict(bus="ac", carrier="battery", max_hours=1.0)
    n.add(
        "StorageUnit",
        "bess",
        p_nom=10.0,
        p_max_pu=0.5,
        efficiency_dispatch=0.9,
        efficiency_store=0.8,
        **s,
    )
    n.add(
        "StorageUnit",
        "flex",
        p_nom_extendable=True,
        p_max_pu=0.6,
        capital_cost=1.0,
        **s,
    )
    n.snapshot_weightings.loc[:, "stores"] = 2.0
    n.snapshot_weightings.loc[n.snapshots[0], "stores"] = 0.0
    return n


def test_a_mixed_fleet_puts_each_unit_kind_and_pv_and_wind_in_the_row_with_its_own_coefficient():
    m, _ = _rows(_mixed_fleet(), (0.2, 0.3, 0.4))
    t = _mixed_fleet().snapshots[NOON]
    terms, rhs, sign = _terms(m, ROW, t)
    assert sign == ">=" and rhs == pytest.approx(
        0.2 * LOAD_MW - 12.0 * 0.5
    )  # only 'fix' is a constant
    assert terms == pytest.approx(
        {
            ("Generator-p", "pv"): -0.3,
            ("Generator-p", "wt"): -0.4,
            ("Generator-p", "fix"): -1.0,
            ("Generator-p", "ext"): -1.0,
            ("Generator-p", "uc"): -1.0,
            ("Generator-p_nom", "ext"): 0.8,
            ("Generator-status", "uc"): 6.0 * 0.9,
            ("operating_reserve-Generator-h", "both"): 1.0,
            (_AUX, "bess"): 1.0,
            (_AUX, "flex"): 1.0,
        }
    )
    cap, _, _ = _terms(m, "operating_reserve-Generator-h-capacity", t, "both")
    assert cap[("Generator-p_nom", "both")] == pytest.approx(-0.7)


def test_a_battery_row_bounds_its_power_by_p_max_pu_and_its_energy_by_both_efficiencies():
    m, _ = _rows(_mixed_fleet(), (0.2, 0.0, 0.0))
    sns = _mixed_fleet().snapshots
    terms, rhs, sign = _terms(m, f"{_AUX}-power-fixed", sns[NOON], "bess")
    assert sign == "<=" and rhs == pytest.approx(10.0 * 0.5)
    assert set(terms) == {
        (_AUX, "bess"),
        ("StorageUnit-p_dispatch", "bess"),
        ("StorageUnit-p_store", "bess"),
    }
    terms, rhs, sign = _terms(m, f"{_AUX}-power-ext", sns[NOON], "flex")
    assert sign == "<=" and rhs == 0.0
    assert terms[("StorageUnit-p_nom", "flex")] == pytest.approx(-0.6)
    terms, _, _ = _terms(m, f"{_AUX}-energy", sns[NOON], "bess")
    assert terms == pytest.approx(
        {
            (_AUX, "bess"): 1.0,
            ("StorageUnit-p_store", "bess"): -(1.0 - 0.9 * 0.8),
            ("StorageUnit-state_of_charge", "bess"): -0.9 / 2.0,
        }
    )
    # a snapshot of zero stores weighting counts no stored energy (never an infinite coefficient)
    terms, _, _ = _terms(m, f"{_AUX}-energy", sns[0], "bess")
    assert ("StorageUnit-state_of_charge", "bess") not in terms


def _only_vre(*carriers) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    n.add("Bus", "ac", carrier="AC")
    n.add("Load", "site", bus="ac", p_set=LOAD_MW)
    n.add(
        "Generator",
        "__voll_ac",
        bus="ac",
        carrier="load_shedding",
        p_nom=100.0,
        marginal_cost=1e4,
    )
    for c in carriers:
        n.add("Generator", c, bus="ac", carrier=c, p_nom=5.0)
    return n


@pytest.mark.parametrize(
    "carrier, shares, row",
    [
        ("solar", (0.2, 0.0, 0.0), False),  # no PV share: nothing to cover
        ("solar", (0.0, 0.0, 0.1), False),  # a wind share with no wind
        ("solar", (0.0, 0.1, 0.0), True),  # a PV share with PV: a row with no holder
        ("onwind", (0.2, 0.0, 0.0), False),
        ("onwind", (0.0, 0.1, 0.0), False),
        ("onwind", (0.0, 0.0, 0.1), True),
    ],
)
def test_a_site_with_no_holder_adds_a_row_only_when_a_share_meets_its_own_output(
    carrier, shares, row
):
    m, log = _rows(_only_vre(carrier), shares)
    assert (ROW in m.constraints) is row
    assert any("nothing to hold" in line for line in log) is not row


def test_a_committable_extendable_unit_without_p_nom_max_is_named_in_a_warning():
    n = _mixed_fleet()
    _, log = _rows(n, (0.2, 0.0, 0.0))
    assert not any("WARNING" in line for line in log), log
    n = _mixed_fleet()
    n.add(
        "Generator",
        "open",
        bus="ac",
        carrier="diesel",
        p_nom_extendable=True,
        committable=True,
    )
    _, log = _rows(n, (0.2, 0.0, 0.0))
    warn = [line for line in log if "WARNING" in line]
    assert len(warn) == 1 and "open" in warn[0] and "both" not in warn[0], log


def _set_frames(n, p, **su):
    sns = n.snapshots
    n.generators_t.p = pd.DataFrame(p, index=sns)
    n.loads_t.p = pd.DataFrame({"site": LOAD_MW}, index=sns)
    for k, v in su.items():
        setattr(n.storage_units_t, k, pd.DataFrame(v, index=sns))


def test_a_committable_unit_holds_status_times_its_available_capacity_in_the_result():
    n = _network()
    n.generators.loc["diesel", "p_max_pu"] = 0.5
    _set_frames(n, {"pv": 0.0, "diesel": 1.0})
    n.generators_t.status = pd.DataFrame(
        {"diesel": [0.0, 1.0] * (HOURS // 2)}, index=n.snapshots
    )
    held = [
        r["held_mw"]
        for r in operating_reserve_result(n, (0.2, 0.0, 0.0))["per_snapshot"]
    ]
    assert held[:2] == pytest.approx([-1.0, 12.0 * 0.5 - 1.0])


def test_a_committable_unit_with_no_status_is_unknown():
    n = _network()
    _set_frames(n, {"pv": 0.0, "diesel": 1.0})
    n.generators_t.status = pd.DataFrame(index=n.snapshots)
    assert "diesel" in operating_reserve_result(n, (0.2, 0.0, 0.0))["reason"]


@pytest.mark.parametrize("missing", ["p_dispatch", "p_store", "state_of_charge"])
def test_a_battery_missing_any_of_its_three_frames_is_unknown(missing):
    n = _network(committable=False, battery=True)
    frames = {k: {"bess": 1.0} for k in ("p_dispatch", "p_store", "state_of_charge")}
    frames[missing] = {}
    _set_frames(n, {"pv": 0.0, "diesel": 0.0}, **frames)
    assert "bess" in operating_reserve_result(n, (0.2, 0.0, 0.0))["reason"]


def test_a_battery_holds_at_most_its_available_power_and_no_energy_at_a_zero_weight_snapshot():
    """Power 10·0.5 − 1 = 4 < energy 0.9·9; at w = 0 the energy term is 0."""
    n = _network(committable=False, battery=True)
    n.generators.loc["diesel", "p_nom"] = 0.0
    n.storage_units.loc["bess", "p_max_pu"] = 0.5
    n.snapshot_weightings.loc[n.snapshots[0], "stores"] = 0.0
    _set_frames(
        n,
        {"pv": 0.0, "diesel": 0.0},
        p_dispatch={"bess": 1.0},
        p_store={"bess": 0.0},
        state_of_charge={"bess": 9.0},
    )
    held = [
        r["held_mw"]
        for r in operating_reserve_result(n, (0.2, 0.0, 0.0))["per_snapshot"]
    ]
    assert held[:2] == pytest.approx([0.0, 4.0])


def test_a_fixed_genset_holds_only_its_available_share_in_the_result():
    n = _network(committable=False)
    n.generators.loc["diesel", "p_max_pu"] = 0.5
    _set_frames(n, {"pv": 0.0, "diesel": 1.0})
    res = operating_reserve_result(n, (0.2, 0.0, 0.0))
    assert res["per_snapshot"][0]["held_mw"] == pytest.approx(12.0 * 0.5 - 1.0)


def test_an_off_share_does_not_need_the_output_it_would_read():
    n = _network(committable=False)
    _set_frames(n, {"diesel": 1.0})
    assert operating_reserve_result(n, (0.2, 0.0, 0.0))["reason"] is None


@pytest.mark.parametrize("nan_in", ["pv", "diesel"])
def test_a_nan_in_the_required_or_the_held_side_is_reported(nan_in):
    n = _network(committable=False)
    _set_frames(n, {"pv": 1.0, "diesel": 1.0})
    n.generators_t.p.iloc[0, n.generators_t.p.columns.get_loc(nan_in)] = np.nan
    res = operating_reserve_result(n, (0.2, 0.1, 0.0))
    assert res["reason"] == "the dispatch has missing values (NaN)"


_R = 5e-7  # a load at which held = required + tolerance is exact in floating point


@pytest.mark.parametrize(
    "load, held, binds",
    [
        (0.0, 0.0, False),  # nothing required never binds
        (10.0, 10.5, False),  # slack 0.5 MW is far from binding
        (1000.0, 1000.0005, True),  # within 1e-6·1000 + 1e-6
        (_R, _R + (1e-6 * _R + 1e-6), True),  # exactly on the tolerance
    ],
)
def test_binding_is_slack_at_most_the_relative_plus_absolute_tolerance(
    load, held, binds
):
    n = _network(committable=False)
    n.loads.loc["site", "p_set"] = load
    n.generators.loc["diesel", "p_nom"] = held
    _set_frames(n, {"pv": 0.0, "diesel": 0.0})
    r = operating_reserve_result(n, (1.0, 0.0, 0.0))["per_snapshot"][0]
    assert r["binding"] is binds, r


def test_the_infeasible_line_compares_the_load_share_not_the_whole_load():
    n = _network(committable=False)
    n.generators.loc["diesel", "p_nom"] = 6.0
    assert (
        "may contribute"
        in infeasibility_lines(n, SolverConfig(reserve_share_load=0.5))[0]
    )


def test_the_infeasible_line_allows_the_absolute_tolerance():
    n = _only_vre("solar")
    n.loads.loc["site", "p_set"] = 1e-6
    assert (
        "may contribute"
        in infeasibility_lines(n, SolverConfig(reserve_share_load=1.0))[0]
    )


@pytest.mark.parametrize("short, more", [(3, ""), (4, " and 1 more")])
def test_the_infeasible_line_shows_three_snapshots_and_counts_the_rest(short, more):
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.add("Bus", "ac", carrier="AC")
    n.add(
        "Load",
        "site",
        bus="ac",
        p_set=pd.Series([LOAD_MW] * short + [0.0] * (4 - short), index=n.snapshots),
    )
    n.add("Generator", "diesel", bus="ac", carrier="diesel", p_nom=5.0)
    line = infeasibility_lines(n, SolverConfig(reserve_share_load=1.0))[0]
    assert f"2030-01-01 02:00{more} the reserve needs" in line, line


def test_a_generator_of_sign_zero_holds_no_headroom():
    n = _network()
    n.add("Generator", "null", bus="ac", carrier="diesel", p_nom=5.0, sign=0.0)
    assert assets(n)["dispatchable"] == ["diesel"]


def test_only_a_store_off_the_electrical_buses_that_feeds_them_is_not_counted():
    n = _network()
    n.add("Bus", "heat", carrier="heat")
    n.add("Store", "tank", bus="heat", e_nom=10.0)
    assert assets(n)["not_counted"] == []  # no Link at all
    n.add("Bus", "ac2", carrier="AC")
    n.add("Bus", "batt", carrier="battery")
    n.add("Store", "cap", bus="ac", e_nom=1.0)
    n.add("Store", "cells", bus="batt", e_nom=10.0)
    n.add("Link", "tie", bus0="ac", bus1="ac2", p_nom=5.0)
    n.add("Link", "discharger", bus0="batt", bus1="ac", p_nom=5.0)
    assert assets(n)["not_counted"] == ["cells"]


def test_wind_is_a_wind_carrier_that_is_not_also_pv():
    n = _network()
    for c in ("biomass", "solar-wind"):
        n.add("Carrier", c)
        n.add("Generator", c, bus="ac", carrier=c, p_nom=5.0)
    sel = assets(n)
    assert sel["wind"] == [] and "solar-wind" in sel["pv"], sel


# ── 01b-2 (UX 21 ask 4): the chat edit card and the panel use one label ──


def test_the_chat_edit_card_reserve_labels_match_the_panel():
    import re
    from pathlib import Path

    from harness.edit_card import _SHARES, _SOLVER_WORDS

    tsx = (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "pages"
        / "solver"
        / "OffGridConstraints.tsx"
    ).read_text()
    block = re.search(r"RESERVE_LABELS = \{(.*?)\} as const", tsx, re.S).group(1)
    panel = dict(re.findall(r"(reserve_share_\w+): '([^']+)'", block))
    assert sorted(panel) == sorted(SHARES)
    for key, shown in panel.items():
        label, _unit, _words = _SOLVER_WORDS[key]
        assert key in _SHARES  # the card shows it in %
        assert shown == f"{label} (%)"


@pytest.mark.parametrize("short", [True, False], ids=["names-hours", "may-contribute"])
def test_the_infeasible_card_hint_is_plain_words(short):
    """UX 21 ask 3: the failure card's hint opens with the label (as its constraint), carries no engine row name
    and ends in one period; the shortfall names the hours."""
    import queue

    from services.failure_taxonomy import classify_failure
    from services.solver.diagnostics import _diagnose_infeasibility

    n = _network()
    if short:
        n.generators.loc["diesel", "p_nom"] = 5.0
    _diagnose_infeasibility(
        n, SolverConfig(reserve_share_load=1.0), queue.SimpleQueue()
    )
    card = classify_failure("warning", "infeasible")
    hints = [
        h
        for h in card.get("constraint_hints", [])
        if h["constraint"] == "Operating reserve"
    ]
    assert len(hints) == 1, card
    text = hints[0]["text"]
    assert hints[0]["setting"] == "offgrid"
    assert ROW not in text and "reserve_share" not in text and "row " not in text, text
    assert text.endswith(".") and not text.endswith(".."), text
    assert ("at " in text and "MW" in text) if short else "may contribute" in text, text


# ── GK B review 2: F1, F2 and the surviving mutants ─────────────────────────


def _charging_battery(**frames):
    """The default battery with η_store 0.9, η_dispatch 0.95, and no other holder."""
    n = _network(committable=False, battery=True)
    n.generators.loc["diesel", "p_nom"] = 0.0
    n.storage_units.loc["bess", "efficiency_dispatch"] = 0.95
    _set_frames(n, {"pv": 0.0, "diesel": 0.0}, **frames)
    return n


def test_cancelling_a_charge_needs_the_energy_it_would_have_stored():
    """F1: w 3, p_store 10, p_dispatch 8.55, soc(t) 0. Cancelling x MW of the charge still leaves the step's end
    w·η_s·x short, so r ≤ soc(t)/(w·η_s) = 0, not p_store + η_d(soc/w − η_s·p_store) = 1.45."""
    n = _charging_battery(
        p_dispatch={"bess": 8.55}, p_store={"bess": 10.0}, state_of_charge={"bess": 0.0}
    )
    n.snapshot_weightings.loc[:, "stores"] = 3.0
    held = operating_reserve_result(n, (1.0, 0.0, 0.0))["per_snapshot"][0]["held_mw"]
    assert held == pytest.approx(0.0)
    # soc(t) 6: soc/(w·η_s) = 6/2.7 < 10 + 0.95·(2 − 9) = 3.35
    n.storage_units_t.state_of_charge.loc[:, "bess"] = 6.0
    held = operating_reserve_result(n, (1.0, 0.0, 0.0))["per_snapshot"][0]["held_mw"]
    assert held == pytest.approx(6.0 / 2.7)


def test_the_lp_bounds_the_cancelled_charge_by_the_energy_at_the_end_of_the_step():
    """F1 in the LP: w·η_s·r ≤ soc(t), one row per snapshot and StorageUnit; at w = 0 it bounds nothing."""
    m, _ = _rows(_mixed_fleet(), (0.2, 0.0, 0.0))
    sns = _mixed_fleet().snapshots
    con = f"{_AUX}-energy-cancel"
    assert m.constraints[con].shape == (HOURS, 2)
    terms, rhs, sign = _terms(m, con, sns[NOON], "bess")
    assert sign == "<=" and rhs == 0.0
    assert terms == pytest.approx(
        {(_AUX, "bess"): 2.0 * 0.8, ("StorageUnit-state_of_charge", "bess"): -1.0}
    )
    terms, _, _ = _terms(m, con, sns[0], "bess")
    assert (_AUX, "bess") not in terms


def _snapshot_of(m, var, con, t, name):
    """The snapshot of the ``var`` term in row (t, name) of ``con``."""
    lhs = m.constraints[con].lhs.sel(snapshot=t, name=name)
    out = []
    for v in lhs.vars.values.ravel():
        if v != -1:
            vn, pos = m.variables.get_label_position(int(v))
            if vn == var:
                out.append(pos["snapshot"])
    return out


@pytest.mark.parametrize("con", [f"{_AUX}-energy", f"{_AUX}-energy-cancel"])
def test_the_energy_rows_read_the_state_of_charge_at_the_end_of_their_own_step(con):
    """M1: the row at t reads soc(t), never soc(t−1) (which would count the step's planned dispatch twice)."""
    n = _mixed_fleet()
    m, _ = _rows(n, (0.2, 0.0, 0.0))
    for t in (n.snapshots[1], n.snapshots[NOON], n.snapshots[-1]):
        assert _snapshot_of(m, "StorageUnit-state_of_charge", con, t, "bess") == [t]


def test_a_charging_battery_holds_more_than_its_spare_discharge_power():
    """M2: p_nom 10, p_dispatch 0, p_store 5: cancelling the charge adds 5 MW to the 10 MW of spare power."""
    n = _charging_battery(
        p_dispatch={"bess": 0.0}, p_store={"bess": 5.0}, state_of_charge={"bess": 40.0}
    )
    held = operating_reserve_result(n, (1.0, 0.0, 0.0))["per_snapshot"][0]["held_mw"]
    assert held == pytest.approx(15.0)


def test_a_battery_short_of_power_holds_no_negative_reserve():
    """M3: p_dispatch above what p_max_pu now allows holds 0 (r ≥ 0 in the LP), never a negative MW."""
    n = _charging_battery(
        p_dispatch={"bess": 8.0}, p_store={"bess": 0.0}, state_of_charge={"bess": 40.0}
    )
    n.storage_units.loc["bess", "p_max_pu"] = 0.5
    held = operating_reserve_result(n, (1.0, 0.0, 0.0))["per_snapshot"][0]["held_mw"]
    assert held == 0.0


def test_a_committable_extendable_unit_without_p_nom_max_holds_nothing_in_the_lp_or_the_result():
    """F2: no finite p_nom_max, so neither its headroom nor its output enters the row, and the result counts 0."""
    n = _mixed_fleet()
    n.add(
        "Generator",
        "open",
        bus="ac",
        carrier="diesel",
        p_nom_extendable=True,
        committable=True,
    )
    m, _ = _rows(n, (0.2, 0.0, 0.0))
    terms, _, _ = _terms(m, ROW, n.snapshots[NOON])
    assert not [k for k in terms if k[1] == "open"], terms
    n = _network(committable=False)
    n.add(
        "Generator",
        "open",
        bus="ac",
        carrier="diesel",
        p_nom_extendable=True,
        committable=True,
    )
    n.generators.loc["open", "p_nom_opt"] = 20.0
    _set_frames(n, {"pv": 0.0, "diesel": 4.0, "open": 6.0})
    n.generators_t.status = pd.DataFrame({"open": 1.0}, index=n.snapshots)
    held = operating_reserve_result(n, (0.2, 0.0, 0.0))["per_snapshot"][0]["held_mw"]
    assert held == pytest.approx(12.0 - 4.0)


@pytest.mark.parametrize(
    "p_nom, p_nom_max, short", [(0.0, 20.0, False), (20.0, 5.0, True)]
)
def test_the_infeasible_line_sizes_an_extendable_unit_by_its_p_nom_max(
    p_nom, p_nom_max, short
):
    """M5: an extendable unit can hold up to p_nom_max, whatever its starting p_nom."""
    n = _network(committable=False)
    n.generators.loc["diesel", ["p_nom", "p_nom_max", "p_nom_extendable"]] = [
        p_nom,
        p_nom_max,
        True,
    ]
    line = infeasibility_lines(n, SolverConfig(reserve_share_load=1.0))[0]
    assert ("may contribute" not in line) is short, line


@pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan")])
def test_a_non_finite_share_is_zero(bad):
    """M4: `shares_of` also reads plain dicts, which no schema has checked."""
    assert shares_of({"reserve_share_load": bad, "reserve_share_pv": 0.1}) == (
        0.0,
        0.1,
        0.0,
    )
    assert shares_of({"reserve_share_wind": bad}) is None


# ── 01d: the units holding the reserve at each binding hour ─────────────────


def test_holders_name_the_units_and_sum_to_the_held_reserve():
    n = _network(committable=False, battery=True)
    _solve(n, ON)
    res = operating_reserve_result(n, (0.2, 0.0, 0.0))
    binding = [r for r in res["per_snapshot"] if r["binding"]]
    assert binding, "the case needs a binding hour"
    for r in binding:
        hs = r["holders"]
        assert hs and {h["kind"] for h in hs} <= {"generator", "storage_unit"}
        assert math.fsum(h["held_mw"] for h in hs) == r["held_mw"]
        assert all(h["held_mw"] > 0 for h in hs)
        assert [h["held_mw"] for h in hs] == sorted(
            (h["held_mw"] for h in hs), reverse=True
        )
    assert "diesel" in {h["name"] for r in binding for h in r["holders"]}


def test_a_battery_is_named_with_the_energy_it_can_deliver():
    n = _network(committable=False, battery=True)
    sns = n.snapshots
    n.snapshot_weightings.loc[:, "stores"] = 2.0
    n.generators_t.p = pd.DataFrame({"pv": 0.0, "diesel": 0.0}, index=sns)
    n.generators.loc["diesel", "p_nom"] = 0.0
    n.loads_t.p = pd.DataFrame({"site": LOAD_MW}, index=sns)
    n.storage_units_t.p_dispatch = pd.DataFrame({"bess": 1.0}, index=sns)
    n.storage_units_t.p_store = pd.DataFrame({"bess": 0.0}, index=sns)
    n.storage_units_t.state_of_charge = pd.DataFrame({"bess": 4.0}, index=sns)
    res = operating_reserve_result(n, (0.18, 0.0, 0.0))  # required 1.8 = what it holds
    r = res["per_snapshot"][0]
    assert r["binding"]
    assert r["holders"] == [
        {"name": "bess", "kind": "storage_unit", "held_mw": pytest.approx(1.8)}
    ]


def test_non_binding_hours_carry_no_holders():
    n = _network(committable=False, battery=True)
    _solve(n, ON)
    res = operating_reserve_result(n, (0.2, 0.0, 0.0))
    quiet = [r for r in res["per_snapshot"] if not r["binding"]]
    assert quiet and all("holders" not in r for r in quiet)


def test_a_committable_unit_that_is_off_holds_nothing():
    n = _network()
    _solve(n, ON)
    res = operating_reserve_result(n, (0.2, 0.0, 0.0))
    st = n.generators_t.status["diesel"]
    for r, (t, on) in zip(res["per_snapshot"], st.items()):
        if r["binding"] and not on:
            assert "diesel" not in {h["name"] for h in r["holders"]}


def test_no_holders_when_the_dispatch_is_missing():
    n = _network(committable=False)
    _solve(n, ON)
    del n.generators_t.p["diesel"]
    res = operating_reserve_result(n, (0.2, 0.0, 0.0))
    assert res["reason"] and not any("holders" in r for r in res["per_snapshot"])


def _frozen(n, diesel_p, status=None):
    """Hand-set dispatch (no solve): pv idle, diesel at `diesel_p`, load LOAD_MW."""
    sns = n.snapshots
    n.generators_t.p = pd.DataFrame({"pv": 0.0, "diesel": diesel_p}, index=sns)
    n.loads_t.p = pd.DataFrame({"site": LOAD_MW}, index=sns)
    if status is not None:
        n.generators_t.status = pd.DataFrame({"diesel": status}, index=sns)


def test_the_units_add_up_to_held_when_one_is_large_and_one_tiny():
    """H1: held is the exactly rounded sum of the units, so the units add up to held_mw with `==`,
    with a 1e6 MW unit beside a 1e-3 MW one."""
    n = _network(committable=False)
    n.add(
        "Generator", "tiny", bus="ac", carrier="diesel", p_nom=1e-3, marginal_cost=250.0
    )
    n.generators.loc["diesel", "p_nom"] = 1e6
    sns = n.snapshots
    n.generators_t.p = pd.DataFrame({"pv": 0.0, "diesel": 0.0, "tiny": 0.0}, index=sns)
    n.loads_t.p = pd.DataFrame({"site": LOAD_MW}, index=sns)
    held = 1e6 + 1e-3
    res = operating_reserve_result(n, (held / LOAD_MW, 0.0, 0.0))["per_snapshot"][0]
    assert res["binding"]
    assert sum(h["held_mw"] for h in res["holders"]) == pytest.approx(
        res["held_mw"], rel=1e-12
    )
    assert [h["name"] for h in res["holders"]] == ["diesel", "tiny"]


def test_equal_holders_are_ordered_by_name():
    n = _network(committable=False)
    n.add(
        "Generator",
        "alpha",
        bus="ac",
        carrier="diesel",
        p_nom=12.0,
        marginal_cost=250.0,
    )
    n.generators.loc["diesel", "p_nom"] = 12.0
    sns = n.snapshots
    n.generators_t.p = pd.DataFrame({"pv": 0.0, "diesel": 0.0, "alpha": 0.0}, index=sns)
    n.loads_t.p = pd.DataFrame({"site": LOAD_MW}, index=sns)
    r = operating_reserve_result(n, (2.4, 0.0, 0.0))["per_snapshot"][
        0
    ]  # required 24 = 12 + 12
    assert r["binding"]
    assert [(h["name"], h["held_mw"]) for h in r["holders"]] == [
        ("alpha", 12.0),
        ("diesel", 12.0),
    ]
    assert {h["kind"] for h in r["holders"]} == {"generator"}


def test_a_committable_unit_that_is_off_is_not_a_holder_and_an_on_one_is():
    n = _network()
    _frozen(n, 0.0, status=np.array([0.0] * 12 + [1.0] * 12))
    out = operating_reserve_result(n, (0.6, 0.0, 0.0))  # required 6 MW
    off, on = out["per_snapshot"][0], out["per_snapshot"][12]
    # off: no headroom at all, so the hour binds (6 MW required, 0 held) and names nobody
    assert off["held_mw"] == 0.0 and off["binding"] and off["holders"] == []
    assert on["held_mw"] == 12.0
    out = operating_reserve_result(
        n, (1.2, 0.0, 0.0)
    )  # required 12 MW = diesel's headroom
    assert out["per_snapshot"][12]["holders"] == [
        {"name": "diesel", "kind": "generator", "held_mw": 12.0}
    ]
    assert out["per_snapshot"][0]["holders"] == []


def test_a_unit_missing_from_the_dispatch_gives_a_reason_and_no_holders():
    n = _network(committable=False, battery=True)
    sns = n.snapshots
    _frozen(n, 0.0)
    n.storage_units_t.p_dispatch = pd.DataFrame({"bess": 0.0}, index=sns)
    n.storage_units_t.p_store = pd.DataFrame({"bess": 0.0}, index=sns)
    # no state_of_charge column for the battery: it was added after the solve
    res = operating_reserve_result(n, (0.2, 0.0, 0.0))
    assert "bess" in res["reason"]
    assert res["per_snapshot"] == []


# ── 01d: the chat read ──────────────────────────────────────────────────────


def _payload(n_binding, n_holders):
    rows = [
        {"snapshot": f"h{i}", "required_mw": 2.0, "held_mw": 2.0, "binding": False}
        for i in range(3)
    ]
    for i in range(n_binding):
        hs = [
            {"name": f"u{k}", "kind": "generator", "held_mw": float(n_holders - k)}
            for k in range(n_holders)
        ]
        rows.append(
            {
                "snapshot": f"b{i}",
                "required_mw": 2.0,
                "held_mw": 2.0,
                "binding": True,
                "holders": hs,
            }
        )
    return {
        "shares": {"load": 0.1, "pv": 0, "wind": 0},
        "not_counted": [],
        "per_snapshot": rows,
        "binding": [r["snapshot"] for r in rows if r["binding"]],
        "reason": None,
    }


def test_the_chat_read_lists_binding_hours_only_with_true_totals():
    from services.chat_tools import _operating_reserve_page

    out = _operating_reserve_page(_payload(30, 8))
    assert out["hours_total"] == 33 and out["binding_hours_total"] == 30
    assert len(out["binding_hours"]) == 24 and out["offset"] == 0
    assert all(r["binding"] for r in out["binding_hours"])
    assert all(
        len(r["holders"]) == 5 and r["holders_total"] == 8 for r in out["binding_hours"]
    )
    assert "per_snapshot" not in out and "binding" not in out
    nxt = _operating_reserve_page(_payload(30, 8), offset=24)
    assert [r["snapshot"] for r in nxt["binding_hours"]] == [
        f"b{i}" for i in range(24, 30)
    ]
    assert len(_operating_reserve_page(_payload(30, 1), limit=3)["binding_hours"]) == 3


def test_the_chat_tool_answers_the_reserve_goal(monkeypatch):
    """The assistant reads the reserve through get_results (the row that said it could not is gone)."""
    from routers import results as results_router
    from services import chat_tools

    assert "operating_reserve" in chat_tools._RESULTS_ENUM
    monkeypatch.setattr(results_router, "get_operating_reserve", lambda: _payload(2, 2))
    monkeypatch.setattr(chat_tools, "_with_freshness", lambda r: r)
    out = chat_tools.get_results("operating_reserve")
    assert out["binding_hours_total"] == 2
    assert out["binding_hours"][0]["holders"][0]["name"] == "u0"
    off = _payload(0, 0) | {
        "shares": None,
        "reason": "the operating reserve was off for this solve",
    }
    monkeypatch.setattr(results_router, "get_operating_reserve", lambda: off)
    assert chat_tools.get_results("operating_reserve")["shares"] is None


def test_solver_noise_over_the_cap_is_no_holder_and_a_real_overdraw_is_one():
    n = _network(committable=False)
    sns = n.snapshots
    n.add("Generator", "b", bus="ac", carrier="diesel", p_nom=5.0, marginal_cost=250.0)
    # diesel runs 1e-7 over its 12 MW cap (noise); b runs 2 MW over its 5 MW cap (a real overdraw)
    n.generators_t.p = pd.DataFrame(
        {"pv": 0.0, "diesel": 12.0 + 1e-7, "b": 7.0}, index=sns
    )
    n.loads_t.p = pd.DataFrame({"site": LOAD_MW}, index=sns)
    row = operating_reserve_result(n, (-0.2, 0.0, 0.0))["per_snapshot"][0]
    assert row["held_mw"] == -2.0 and not row["binding"]
    n.generators_t.p["b"] = 5.0
    row = operating_reserve_result(n, (0.0, 0.0, 0.0))["per_snapshot"][0]
    assert row["held_mw"] == 0.0
    n.loads_t.p = pd.DataFrame({"site": 1.0}, index=sns)
    n.generators.loc["pv", "p_nom"] = 0.0
    row = operating_reserve_result(n, (1.0, 0.0, 0.0))["per_snapshot"][
        0
    ]  # required 1 MW, held 0
    assert row["binding"] and row["holders"] == []


def test_a_negative_pmin_unit_holds_what_its_cap_leaves_over_its_dispatch():
    n = _network(committable=False)
    sns = n.snapshots
    n.generators.loc["diesel", "p_min_pu"] = -1.0
    n.generators_t.p = pd.DataFrame({"pv": 0.0, "diesel": -4.0}, index=sns)
    n.loads_t.p = pd.DataFrame({"site": LOAD_MW}, index=sns)
    row = operating_reserve_result(n, (1.6, 0.0, 0.0))["per_snapshot"][
        0
    ]  # 12 - (-4) = 16 = required
    assert row["binding"] and row["held_mw"] == 16.0
    assert row["holders"] == [{"name": "diesel", "kind": "generator", "held_mw": 16.0}]


def test_holders_are_present_exactly_on_binding_rows_including_a_near_binding_hour():
    n = _network(committable=False)
    sns = n.snapshots
    n.generators.loc["diesel", "p_min_pu"] = -1.0
    # odd hours: held 12 = required 12 (binds); even hours: held 12.012 vs required 12 (a near miss, no bind)
    n.generators_t.p = pd.DataFrame(
        {"pv": 0.0, "diesel": [-0.012 if t % 2 == 0 else 0.0 for t in range(HOURS)]},
        index=sns,
    )
    n.loads_t.p = pd.DataFrame({"site": LOAD_MW}, index=sns)
    rows = operating_reserve_result(n, (1.2, 0.0, 0.0))["per_snapshot"]
    assert [r["binding"] for r in rows] == [t % 2 == 1 for t in range(HOURS)]
    assert all(("holders" in r) == r["binding"] for r in rows)


def test_an_explicit_limit_is_clamped_to_the_page_size_in_the_chat_read():
    from services.chat_tools import _operating_reserve_page

    out = _operating_reserve_page(_payload(40, 1), limit=1000)
    assert len(out["binding_hours"]) == 24 and out["binding_hours_total"] == 40
    assert len(_operating_reserve_page(_payload(40, 1), limit=0)["binding_hours"]) == 1


def test_the_units_add_up_with_values_whose_naive_sum_depends_on_the_order():
    """1e16 + 1 + 1: a plain float sum (numpy's) loses both ones; the exactly rounded sum keeps them.
    (Python's builtin sum is compensated since 3.12 and agrees with fsum here.)"""
    assert float(np.array([1e16, 1.0, 1.0]).sum()) != math.fsum([1e16, 1.0, 1.0])
    n = _network(committable=False)
    sns = n.snapshots
    n.generators.loc["diesel", "p_nom"] = 0.0
    for name, size in (("a_big", 1e16), ("b_one", 1.0), ("c_one", 1.0)):
        n.add(
            "Generator",
            name,
            bus="ac",
            carrier="diesel",
            p_nom=size,
            marginal_cost=250.0,
        )
    n.generators_t.p = pd.DataFrame(
        {"pv": 0.0, "diesel": 0.0, "a_big": 0.0, "b_one": 0.0, "c_one": 0.0}, index=sns
    )
    n.loads_t.p = pd.DataFrame({"site": LOAD_MW}, index=sns)
    row = operating_reserve_result(n, ((1e16 + 2.0) / LOAD_MW, 0.0, 0.0))[
        "per_snapshot"
    ][0]
    assert row["binding"] and row["held_mw"] == 1e16 + 2.0
    assert [h["name"] for h in row["holders"]] == ["a_big", "b_one", "c_one"]
    assert math.fsum(h["held_mw"] for h in row["holders"]) == row["held_mw"]


def test_a_real_overdraw_is_listed_with_a_negative_amount_and_the_sum_still_holds():
    n = _network(committable=False)
    sns = n.snapshots
    n.add("Generator", "b", bus="ac", carrier="diesel", p_nom=5.0, marginal_cost=250.0)
    n.generators_t.p = pd.DataFrame(
        {"pv": 0.0, "diesel": 12.0 + 1e-3, "b": 0.0}, index=sns
    )
    n.loads_t.p = pd.DataFrame({"site": LOAD_MW}, index=sns)
    row = operating_reserve_result(n, (0.4999, 0.0, 0.0))["per_snapshot"][
        0
    ]  # required 4.999
    assert row["binding"]
    assert [h["name"] for h in row["holders"]] == ["b", "diesel"]
    assert row["holders"][0]["held_mw"] == 5.0
    assert row["holders"][1]["held_mw"] == pytest.approx(-1e-3, rel=1e-6)
    assert math.fsum(h["held_mw"] for h in row["holders"]) == row["held_mw"]
