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
import hashlib
import json

from services.reliability_goal import goal_free_config
from services.solver.renewable_fraction import (
    _wrap_with_min_renewable_fraction,
    classify,
    infeasibility_lines,
    renewable_fraction,
    renewable_fraction_result,
    row_name,
)
from services.solver_service import SolverConfig, run_simulation

HOURS = 24
LOAD_MW = 10.0
# PV shape: 0 at night, 1 from 08:00 to 15:59 (8 h).
PV_PU = np.array([1.0 if 8 <= h < 16 else 0.0 for h in range(HOURS)])


def _network(*, pv_max: float = 100.0) -> pypsa.Network:
    """PV (extendable), a diesel genset, a battery: the 3-asset fixture. One
    day, so the LP periodizes the per-year capital costs by 24/8760. Storage is dear enough that the
    free optimum runs diesel all night (fraction 8/24 ≈ 0.33)."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=HOURS, freq="h"))
    for c in ("solar", "diesel", "battery"):
        n.add("Carrier", c)
    n.add("Bus", "ac", carrier="AC")
    n.add("Load", "site", bus="ac", p_set=LOAD_MW)
    n.add("Generator", "pv", bus="ac", carrier="solar", p_nom_extendable=True,
          p_nom_max=pv_max, capital_cost=60_000.0, p_max_pu=pd.Series(PV_PU, index=n.snapshots))
    n.add("Generator", "diesel", bus="ac", carrier="diesel", p_nom=LOAD_MW,
          marginal_cost=250.0)
    n.add("StorageUnit", "bess", bus="ac", carrier="battery", p_nom_extendable=True,
          max_hours=4.0, capital_cost=400_000.0, p_nom_max=100.0, efficiency_store=0.95,
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
    sink: dict = {}
    status, condition = run_simulation(
        SolverConfig(**cfg_kw), n, PyPSAService.get_lock(), threading.Event(),
        q, state_update=lambda **kw: sink.update(kw),
    )
    _SINK.clear()
    _SINK.update(sink)
    return status, condition, _drain(q)


_SINK: dict = {}


# ── the input ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [-0.1, 1.5, float("nan"), float("inf"), "x"])
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
    shed = pd.Series(0.0, index=n.snapshots)
    shed.iloc[:10] = 4.0                                         # 40 MWh shed
    kpi = renewable_fraction(n, unserved_t=shed)
    assert kpi["served_mwh"] == pytest.approx(200.0)
    assert kpi["fraction"] == pytest.approx((200.0 - 90.0) / 200.0)


def test_the_kpi_is_unavailable_without_a_dispatch():
    kpi = renewable_fraction(_network())
    assert kpi["fraction"] is None and kpi["reason"]


# ── the LP row ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("f", [None, 0.0, float("inf"), float("nan")])
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


# ── R1: off is byte-identical ─────────────────────────────────────────────

# sha256 of master's (1dffd76c) `goal_free_config(SolverConfig())`, the input
# to the network fingerprint and the investment-case digest.
MASTER_DEFAULT_CONFIG_SHA = "9b89ba730791f6d73b395db5f74240d82204cc3c58ba5109b8212cd5385fb281"


def _cfg_sha(cfg) -> str:
    return hashlib.sha256(json.dumps(goal_free_config(cfg), sort_keys=True,
                                     default=str).encode()).hexdigest()


@pytest.mark.parametrize("f", [None, 0.0])
def test_off_keeps_master_fingerprint_input(f):
    assert _cfg_sha(SolverConfig(min_renewable_fraction=f)) == MASTER_DEFAULT_CONFIG_SHA


def test_a_target_changes_the_fingerprint_input():
    assert _cfg_sha(SolverConfig(min_renewable_fraction=0.6)) != MASTER_DEFAULT_CONFIG_SHA


def test_off_solve_settings_carry_no_new_key():
    assert _solve(_network())[0] == "ok"
    assert "min_renewable_fraction" not in _SINK["last_solve_settings"]
    assert _solve(_network(), min_renewable_fraction=0.5)[0] == "ok"
    assert _SINK["last_solve_settings"]["min_renewable_fraction"] == 0.5


# ── R3: grid import, however modelled; storage is not a source ───────────

def test_grid_import_generator_is_non_renewable():
    n = _network()
    n.add("Generator", "grid", bus="ac", carrier="grid", p_nom=50.0, p_min_pu=-1.0)
    assert "grid" in classify(n)["nonren_generators"]


def test_grid_import_link_from_an_electrical_grid_bus_is_counted_once():
    n = _network()
    n.add("Bus", "grid", carrier="AC")
    n.add("Generator", "supply", bus="grid", carrier="grid", p_nom=50.0)
    n.add("Link", "poc", bus0="grid", bus1="ac", p_nom=50.0, efficiency=0.98)
    c = classify(n)
    assert c["source_links"] == ["poc"]
    assert "supply" not in c["nonren_generators"]


def test_a_link_fed_through_storage_is_not_a_source():
    n = _network()
    n.add("Bus", "h2", carrier="H2")
    n.add("Store", "tank", bus="h2", e_nom=100.0)
    n.add("Link", "fuelcell", bus0="h2", bus1="ac", p_nom=5.0, efficiency=0.5)
    n.add("Link", "electrolyser", bus0="ac", bus1="h2", p_nom=5.0, efficiency=0.7)
    assert classify(n)["source_links"] == []


# ── R4: the row reads what the LP sees ───────────────────────────────────

def _two_period(weights=(1.0, 1.0)) -> pypsa.Network:
    n = _network()
    n.set_investment_periods([2030, 2035])
    for P, w in zip((2030, 2035), weights):
        n.snapshot_weightings.loc[P, :] = w
    # A time-series load: the solve's load scalers act on `loads_t.p_set` only.
    n.loads_t.p_set = pd.DataFrame({"site": LOAD_MW}, index=n.snapshots)
    return n


def _rhs(n, name) -> float:
    return float(np.ravel(n.model.constraints[name].rhs.values)[0])


def test_the_row_uses_the_scaled_load():
    n = _two_period()
    status, _, log = _solve(n, min_renewable_fraction=0.5, multi_investment_periods=True,
                            investment_periods=[2030, 2035], load_scalers={"2035": 1.5})
    rows = [l for l in log if l.startswith("[RENEWABLE]")]
    assert any(row_name(2030) in l and "240.0 MWh electrical load" in l for l in rows), rows
    assert any(row_name(2035) in l and "360.0 MWh electrical load" in l for l in rows), rows


def test_a_time_varying_efficiency_reaches_the_row():
    n = _network()
    n.add("Bus", "fuel", carrier="diesel")
    n.add("Generator", "tank", bus="fuel", carrier="diesel", p_nom=100.0)
    n.add("Link", "genset2", bus0="fuel", bus1="ac", p_nom=25.0,
          efficiency=pd.Series(np.linspace(0.3, 0.5, HOURS), index=n.snapshots))
    n.optimize.create_model()
    _wrap_with_min_renewable_fraction(n, None, SolverConfig(min_renewable_fraction=0.5))(n, n.snapshots)
    coeffs = set(np.round(np.ravel(n.model.constraints[row_name(None)].coeffs.values), 6))
    assert {0.3, 0.5} <= coeffs


# ── R5: a slack lets the LP shed instead ─────────────────────────────────

def test_with_a_voll_slack_the_target_is_met_by_shedding():
    n = _network(pv_max=5.0)
    status, _, log = _solve(n, min_renewable_fraction=0.9, voll=10_000.0)
    assert status == "ok", log
    kpi = renewable_fraction_result(n, _SINK.get("last_lost_load"), 0.9)
    assert kpi["served_mwh"] < LOAD_MW * HOURS - 1.0, kpi
    assert kpi["fraction"] >= 0.9 - 1e-6, kpi


# ── R6: nothing served ───────────────────────────────────────────────────

def test_nothing_served_is_none_with_a_reason():
    n = _dispatched()
    n.loads_t.p.loc[:, :] = 0.0
    kpi = renewable_fraction(n)
    assert kpi["fraction"] is None and "served" in kpi["reason"]


def test_a_capture_from_another_solve_is_refused():
    n = _dispatched()
    other = pd.DataFrame({"site": [1.0]}, index=pd.date_range("2031-01-01", periods=1, freq="h"))
    kpi = renewable_fraction_result(n, {"lost_load_t": other}, None)
    assert kpi["fraction"] is None and "another solve" in kpi["reason"]


# ── R10: each period on its own weightings ───────────────────────────────

def test_each_period_row_uses_only_its_own_weightings():
    n = _two_period(weights=(1.0, 2.0))
    n.optimize.create_model(multi_investment_periods=True)
    _wrap_with_min_renewable_fraction(
        n, None, SolverConfig(min_renewable_fraction=0.5, multi_investment_periods=True))(n, n.snapshots)
    assert _rhs(n, row_name(2030)) == pytest.approx(0.5 * 240.0)
    assert _rhs(n, row_name(2035)) == pytest.approx(0.5 * 480.0)


def test_a_period_without_load_gets_no_row():
    n = _two_period()
    n.loads_t.p_set = pd.DataFrame({"site": [LOAD_MW if p == 2030 else 0.0
                                             for p in n.snapshots.get_level_values(0)]},
                                   index=n.snapshots)
    n.optimize.create_model(multi_investment_periods=True)
    _wrap_with_min_renewable_fraction(
        n, None, SolverConfig(min_renewable_fraction=0.5, multi_investment_periods=True))(n, n.snapshots)
    assert row_name(2030) in n.model.constraints
    assert row_name(2035) not in n.model.constraints


# ── U6: the chat edit card and the panel use one label ───────────────────

def test_the_chat_edit_card_label_matches_the_panel():
    import re
    from pathlib import Path

    from harness.edit_card import _SHARES, _SOLVER_WORDS

    tsx = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages" / "solver"
           / "OffGridConstraints.tsx").read_text()
    panel = re.search(r"MIN_RENEWABLE_FRACTION_LABEL = '([^']+)'", tsx).group(1)
    label, _unit, _words = _SOLVER_WORDS["min_renewable_fraction"]
    assert "min_renewable_fraction" in _SHARES          # the card shows it in %
    assert panel == f"{label} (%)"


# ── R7: every settings path refuses an out-of-range value ────────────────

@pytest.mark.parametrize("bad", [-0.1, 1.5])
def test_the_chat_settings_tool_refuses_out_of_range(bad):
    from services import chat_tools

    with pytest.raises(ValidationError):
        chat_tools.update_solver_config({"min_renewable_fraction": bad})


@pytest.mark.parametrize("bad", [-0.1, 1.5])
def test_the_chat_edit_card_preview_refuses_out_of_range(bad):
    from harness.edit_card import _solver_preview

    with pytest.raises(Exception) as exc:
        _solver_preview({"partial": {"min_renewable_fraction": bad}})
    assert "min_renewable_fraction" in str(exc.value) or "refuse" in str(exc.value).lower()


@pytest.mark.parametrize("bad", [-0.1, 1.5])
def test_the_solver_config_put_refuses_out_of_range(client, bad):
    before = client.get("/api/simulation/solver_config").json().get("min_renewable_fraction")
    r = client.put("/api/simulation/solver_config", json={"min_renewable_fraction": bad})
    assert r.status_code == 422, r.text
    assert "min_renewable_fraction" in r.text
    after = client.get("/api/simulation/solver_config").json().get("min_renewable_fraction")
    assert after == before


def test_the_solver_config_put_stores_a_fraction(client):
    r = client.put("/api/simulation/solver_config", json={"min_renewable_fraction": 0.8})
    assert r.status_code == 200, r.text
    assert client.get("/api/simulation/solver_config").json()["min_renewable_fraction"] == 0.8


# ── classification edges (mutant killers) ────────────────────────────────

def test_a_bus_with_no_carrier_is_electrical():
    n = _network()
    n.buses.loc["ac", "carrier"] = ""
    c = classify(n)
    assert c["nonren_generators"] == ["diesel"] and c["loads"] == ["site"]


def test_a_genset_behind_a_line_counts_at_the_generator():
    """A bus tied by a Line is part of the served network, not a supply bus."""
    n = _network()
    n.add("Bus", "yard", carrier="AC")
    n.add("Line", "tie", bus0="yard", bus1="ac", x=0.1, s_nom=50.0)
    n.add("Generator", "genset_yard", bus="yard", carrier="diesel", p_nom=10.0)
    n.add("Link", "aux", bus0="yard", bus1="ac", p_nom=1.0)
    c = classify(n)
    assert "genset_yard" in c["nonren_generators"] and c["source_links"] == []


def test_no_infeasibility_hint_when_off():
    assert infeasibility_lines(_network(pv_max=5.0), SolverConfig()) == []


def test_the_infeasibility_hint_names_need_and_reach():
    lines = infeasibility_lines(_network(pv_max=5.0), SolverConfig(min_renewable_fraction=0.9))
    assert len(lines) == 1 and lines[0].startswith("Minimum renewable fraction:")
    assert row_name(None) in lines[0]
    assert "216.0 MWh" in lines[0] and "40.0 MWh" in lines[0], lines[0]
