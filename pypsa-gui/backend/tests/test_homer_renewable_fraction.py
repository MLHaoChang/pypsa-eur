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
from types import SimpleNamespace

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
    _AUX,
    _BUILT,
    _wrap_with_min_renewable_fraction,
    classify,
    infeasibility_lines,
    max_renewable_mwh,
    renewable_fraction,
    renewable_fraction_result,
    row_name,
    target_of,
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
    n.add(
        "Generator",
        "pv",
        bus="ac",
        carrier="solar",
        p_nom_extendable=True,
        p_nom_max=pv_max,
        capital_cost=60_000.0,
        p_max_pu=pd.Series(PV_PU, index=n.snapshots),
    )
    n.add(
        "Generator",
        "diesel",
        bus="ac",
        carrier="diesel",
        p_nom=LOAD_MW,
        marginal_cost=250.0,
    )
    n.add(
        "StorageUnit",
        "bess",
        bus="ac",
        carrier="battery",
        p_nom_extendable=True,
        max_hours=4.0,
        capital_cost=400_000.0,
        p_nom_max=100.0,
        efficiency_store=0.95,
        efficiency_dispatch=0.95,
        cyclic_state_of_charge=True,
    )
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
        SolverConfig(**cfg_kw),
        n,
        PyPSAService.get_lock(),
        threading.Event(),
        q,
        state_update=lambda **kw: sink.update(kw),
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
    pv = pd.Series(PV_PU * 20.0, index=sns)  # 160 MWh
    bess = pd.Series(0.0, index=sns)
    bess[PV_PU > 0] = -(20.0 - LOAD_MW)  # charges 80 MWh
    night = [h for h in range(HOURS) if PV_PU[h] == 0][:7]
    bess.iloc[night] = LOAD_MW  # discharges 70 MWh
    diesel = (LOAD_MW - pv - bess).clip(lower=0.0)  # 160 − 70 = 90 MWh
    n.generators_t.p = pd.DataFrame({"pv": pv, "diesel": diesel})
    n.storage_units_t.p = pd.DataFrame({"bess": bess})
    return n


def test_the_kpi_equals_the_hand_computed_fraction():
    n = _dispatched()
    kpi = renewable_fraction(n)
    served = LOAD_MW * HOURS  # 240 MWh
    nonren = 90.0
    assert kpi["served_mwh"] == pytest.approx(served)
    assert kpi["nonrenewable_mwh"] == pytest.approx(nonren)
    assert kpi["renewable_mwh"] == pytest.approx(served - nonren)
    assert kpi["fraction"] == pytest.approx((served - nonren) / served)  # 0.625
    assert kpi["fraction"] == pytest.approx(0.625)


def test_the_kpi_counts_a_link_genset_from_a_fuel_bus_and_not_the_battery():
    n = _dispatched()
    n.add("Bus", "fuel", carrier="diesel")
    n.add("Generator", "tank", bus="fuel", carrier="diesel", p_nom=100.0)
    n.add(
        "Link",
        "genset2",
        bus0="fuel",
        bus1="ac",
        carrier="diesel",
        efficiency=0.4,
        p_nom=25.0,
    )
    # The Link genset takes 6 MWh of diesel's job: 15 MWh fuel × 0.4.
    n.links_t.p0 = pd.DataFrame({"genset2": 0.0}, index=n.snapshots)
    n.links_t.p0.iloc[7, 0] = 15.0  # 07:00, a diesel hour
    n.generators_t.p["tank"] = 0.0
    n.generators_t.p.iloc[7, n.generators_t.p.columns.get_loc("tank")] = 15.0
    n.generators_t.p.iloc[7, n.generators_t.p.columns.get_loc("diesel")] -= 6.0
    kpi = renewable_fraction(n)
    assert kpi["nonrenewable_mwh"] == pytest.approx(
        90.0
    )  # 84 + 6, fuel not counted twice
    assert kpi["fraction"] == pytest.approx(0.625)


def test_shed_load_is_not_served():
    n = _dispatched()
    shed = pd.Series(0.0, index=n.snapshots)
    shed.iloc[:10] = 4.0  # 40 MWh shed
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
    assert (
        _wrap_with_min_renewable_fraction(
            _network(), sentinel, SolverConfig(min_renewable_fraction=f)
        )
        is sentinel
    )


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
    n = _network(pv_max=5.0)  # 40 MWh of PV against 240 MWh of load
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
MASTER_DEFAULT_CONFIG_SHA = (
    "9b89ba730791f6d73b395db5f74240d82204cc3c58ba5109b8212cd5385fb281"
)


def _cfg_sha(cfg) -> str:
    return hashlib.sha256(
        json.dumps(goal_free_config(cfg), sort_keys=True, default=str).encode()
    ).hexdigest()


@pytest.mark.parametrize("f", [None, 0.0])
def test_off_keeps_master_fingerprint_input(f):
    assert _cfg_sha(SolverConfig(min_renewable_fraction=f)) == MASTER_DEFAULT_CONFIG_SHA


def test_a_target_changes_the_fingerprint_input():
    assert (
        _cfg_sha(SolverConfig(min_renewable_fraction=0.6)) != MASTER_DEFAULT_CONFIG_SHA
    )


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
    status, _, log = _solve(
        n,
        min_renewable_fraction=0.5,
        multi_investment_periods=True,
        investment_periods=[2030, 2035],
        load_scalers={"2035": 1.5},
    )
    rows = [l for l in log if l.startswith("[RENEWABLE]")]
    assert any(
        row_name(2030) in l and "240.0 MWh electrical load" in l for l in rows
    ), rows
    assert any(
        row_name(2035) in l and "360.0 MWh electrical load" in l for l in rows
    ), rows


def test_a_time_varying_efficiency_reaches_the_row():
    n = _network()
    n.add("Bus", "fuel", carrier="diesel")
    n.add("Generator", "tank", bus="fuel", carrier="diesel", p_nom=100.0)
    n.add(
        "Link",
        "genset2",
        bus0="fuel",
        bus1="ac",
        p_nom=25.0,
        efficiency=pd.Series(np.linspace(0.3, 0.5, HOURS), index=n.snapshots),
    )
    n.optimize.create_model()
    _wrap_with_min_renewable_fraction(
        n, None, SolverConfig(min_renewable_fraction=0.5)
    )(n, n.snapshots)
    coeffs = set(
        np.round(np.ravel(n.model.constraints[row_name(None)].coeffs.values), 6)
    )
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
    other = pd.DataFrame(
        {"site": [1.0]}, index=pd.date_range("2031-01-01", periods=1, freq="h")
    )
    kpi = renewable_fraction_result(n, {"lost_load_t": other}, None)
    assert kpi["fraction"] is None and "another solve" in kpi["reason"]


# ── R10: each period on its own weightings ───────────────────────────────


def test_each_period_row_uses_only_its_own_weightings():
    n = _two_period(weights=(1.0, 2.0))
    n.optimize.create_model(multi_investment_periods=True)
    _wrap_with_min_renewable_fraction(
        n, None, SolverConfig(min_renewable_fraction=0.5, multi_investment_periods=True)
    )(n, n.snapshots)
    assert _rhs(n, row_name(2030)) == pytest.approx(0.5 * 240.0)
    assert _rhs(n, row_name(2035)) == pytest.approx(0.5 * 480.0)


def test_a_period_without_load_gets_no_row():
    n = _two_period()
    n.loads_t.p_set = pd.DataFrame(
        {
            "site": [
                LOAD_MW if p == 2030 else 0.0 for p in n.snapshots.get_level_values(0)
            ]
        },
        index=n.snapshots,
    )
    n.optimize.create_model(multi_investment_periods=True)
    _wrap_with_min_renewable_fraction(
        n, None, SolverConfig(min_renewable_fraction=0.5, multi_investment_periods=True)
    )(n, n.snapshots)
    assert row_name(2030) in n.model.constraints
    assert row_name(2035) not in n.model.constraints


# ── U6: the chat edit card and the panel use one label ───────────────────


def test_the_chat_edit_card_label_matches_the_panel():
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
    panel = re.search(r"MIN_RENEWABLE_FRACTION_LABEL = '([^']+)'", tsx).group(1)
    label, _unit, _words = _SOLVER_WORDS["min_renewable_fraction"]
    assert "min_renewable_fraction" in _SHARES  # the card shows it in %
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
    assert (
        "min_renewable_fraction" in str(exc.value) or "refuse" in str(exc.value).lower()
    )


@pytest.mark.parametrize("bad", [-0.1, 1.5])
def test_the_solver_config_put_refuses_out_of_range(client, bad):
    before = (
        client.get("/api/simulation/solver_config").json().get("min_renewable_fraction")
    )
    r = client.put(
        "/api/simulation/solver_config", json={"min_renewable_fraction": bad}
    )
    assert r.status_code == 422, r.text
    assert "min_renewable_fraction" in r.text
    after = (
        client.get("/api/simulation/solver_config").json().get("min_renewable_fraction")
    )
    assert after == before


def test_the_solver_config_put_stores_a_fraction(client):
    r = client.put(
        "/api/simulation/solver_config", json={"min_renewable_fraction": 0.8}
    )
    assert r.status_code == 200, r.text
    assert (
        client.get("/api/simulation/solver_config").json()["min_renewable_fraction"]
        == 0.8
    )


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
    lines = infeasibility_lines(
        _network(pv_max=5.0), SolverConfig(min_renewable_fraction=0.9)
    )
    assert len(lines) == 1 and lines[0].startswith("Minimum renewable fraction:")
    assert row_name(None) in lines[0]
    assert "216.0 MWh" in lines[0] and "at most 40.0 MWh" in lines[0], lines[0]
    assert "None" not in lines[0]


# ── horizon totals and partial dispatch (mutant killers) ─────────────────


def _dispatched_two_period() -> pypsa.Network:
    """The hand dispatch in 2030 and 2035; 2035 lasts 10 years, 2030 5."""
    one = _dispatched()
    n = _network()
    n.set_investment_periods([2030, 2035])
    n.investment_period_weightings.loc[:, "years"] = [5.0, 10.0]
    # The objective weighting differs: the KPI must read `generators`.
    n.snapshot_weightings.loc[:, "objective"] = 7.0
    sns = n.snapshots
    n.loads_t.p = pd.DataFrame({"site": LOAD_MW}, index=sns)
    gp = pd.concat([one.generators_t.p, one.generators_t.p])
    gp.index = sns
    gp.loc[2035, "diesel"] = 0.0  # 2035 runs on renewables only
    n.generators_t.p = gp
    return n


def test_horizon_totals_weight_each_period_by_its_years():
    kpi = renewable_fraction(_dispatched_two_period())
    by = {p["period"]: p for p in kpi["per_period"]}
    assert by[2030]["fraction"] == pytest.approx(0.625)
    assert by[2035]["fraction"] == pytest.approx(1.0)
    assert kpi["served_mwh"] == pytest.approx(240.0 * 5 + 240.0 * 10)
    assert kpi["nonrenewable_mwh"] == pytest.approx(90.0 * 5)
    assert kpi["fraction"] == pytest.approx(1 - 450.0 / 3600.0)


def test_a_generator_added_after_the_solve_reads_as_zero():
    n = _dispatched()
    n.add("Generator", "late", bus="ac", carrier="diesel", p_nom=5.0)
    n.generators_t.p = n.generators_t.p.drop(columns=["late"], errors="ignore")
    assert renewable_fraction(n)["fraction"] == pytest.approx(0.625)


def test_no_load_dispatch_is_unavailable_even_with_generator_dispatch():
    n = _dispatched()
    n.loads_t.p = pd.DataFrame(index=n.snapshots)
    kpi = renewable_fraction(n)
    assert kpi["fraction"] is None and "not been solved" in kpi["reason"]


# ── fail closed (GK B addition to R2/R3) ─────────────────────────────────


def _with_fuel(n, *links):
    n.add("Bus", "fuel", carrier="diesel")
    n.add("Generator", "tank", bus="fuel", carrier="diesel", p_nom=100.0)
    for name, b0, b1, extra in links:
        n.add("Link", name, bus0=b0, bus1=b1, p_nom=25.0, efficiency=0.4, **extra)
    n.generators_t.p["tank"] = 0.0
    n.generators_t.p.iloc[7, n.generators_t.p.columns.get_loc("tank")] = 15.0
    return n


def test_an_import_whose_upstream_bus_feeds_another_link_fails_closed():
    """The fuel bus feeds the genset Link into the site AND a Link into a
    second non-electrical bus that reaches the site again: not attributable."""
    n = _dispatched()
    n.add("Bus", "dayTank", carrier="diesel")
    n = _with_fuel(
        n,
        ("genset2", "fuel", "ac", {}),
        ("transfer", "fuel", "dayTank", {}),
        ("genset3", "dayTank", "ac", {}),
    )
    kpi = renewable_fraction(n)
    assert kpi["fraction"] is None and "tank" in kpi["reason"], kpi


def test_a_link_genset_with_an_unlisted_carrier_is_counted():
    n = _dispatched()
    n = _with_fuel(n, ("unit-7", "fuel", "ac", {"carrier": "xyz-machine"}))
    n.links_t.p0 = pd.DataFrame({"unit-7": 0.0}, index=n.snapshots)
    n.links_t.p0.iloc[7, 0] = 15.0
    n.generators_t.p.iloc[7, n.generators_t.p.columns.get_loc("diesel")] -= 6.0
    kpi = renewable_fraction(n)
    assert kpi["nonrenewable_mwh"] == pytest.approx(90.0) and kpi[
        "fraction"
    ] == pytest.approx(0.625)


def test_a_multi_port_output_into_the_site_fails_closed():
    n = _dispatched()
    n.add("Bus", "heat", carrier="heat")
    n = _with_fuel(n, ("chp", "fuel", "heat", {"bus2": "ac", "efficiency2": 0.3}))
    kpi = renewable_fraction(n)
    assert kpi["fraction"] is None and "tank" in kpi["reason"], kpi


def test_a_two_link_chain_into_the_site_fails_closed():
    """GK B F1'' (#1423): gas Gen → Link → mid → Link "xyz" (η 0.4) → site.
    One hop from the fuel bus never touches electricity; the walk must
    follow the chain, or the KPI reads 1.0."""
    n = _dispatched()
    n.add("Bus", "mid", carrier="gas")
    n = _with_fuel(n, ("pipe", "fuel", "mid", {}), ("xyz", "mid", "ac", {}))
    assert classify(n)["unattributed_generators"] == ["tank"]
    kpi = renewable_fraction(n)
    assert kpi["fraction"] is None and "tank" in kpi["reason"], kpi


def test_gas_through_smr_and_a_fuel_cell_fails_closed():
    """GK B F1'' (#1423): gas → SMR → H2 bus (with an H2 tank) → fuel cell →
    site. The H2 Store does not launder the gas into renewable electricity."""
    n = _dispatched()
    n.add("Bus", "h2", carrier="H2")
    n.add("Store", "h2tank", bus="h2", carrier="H2", e_nom=200.0)
    n = _with_fuel(
        n,
        ("SMR", "fuel", "h2", {}),
        ("fuel cell", "h2", "ac", {}),
    )
    assert classify(n)["unattributed_generators"] == ["tank"]
    kpi = renewable_fraction(n)
    assert kpi["fraction"] is None and "tank" in kpi["reason"], kpi


def test_fuel_that_only_reaches_heat_is_not_electricity():
    n = _dispatched()
    n.add("Bus", "heat", carrier="heat")
    n = _with_fuel(n, ("boiler", "fuel", "heat", {}))
    assert renewable_fraction(n)["fraction"] == pytest.approx(0.625)


def test_the_row_warns_about_unattributed_generators():
    n = _network()
    n.add("Bus", "heat", carrier="heat")
    n.add("Bus", "fuel", carrier="diesel")
    n.add("Generator", "tank", bus="fuel", carrier="diesel", p_nom=100.0)
    n.add(
        "Link", "chp", bus0="fuel", bus1="heat", bus2="ac", efficiency2=0.3, p_nom=5.0
    )
    n.optimize.create_model()
    q: queue.SimpleQueue = queue.SimpleQueue()
    _wrap_with_min_renewable_fraction(
        n, None, SolverConfig(min_renewable_fraction=0.5), q
    )(n, n.snapshots)
    assert any("WARNING" in l and "tank" in l for l in _drain(q))


# ── R13: a fuel Store feeding a genset fails closed; a battery does not ──


def test_a_diesel_store_feeding_a_genset_fails_closed():
    n = _dispatched()
    n.add("Bus", "tankbus", carrier="diesel")
    n.add(
        "Store",
        "daytank",
        bus="tankbus",
        carrier="diesel",
        e_nom=500.0,
        e_initial=500.0,
    )
    n.add("Link", "genset_s", bus0="tankbus", bus1="ac", p_nom=25.0, efficiency=0.4)
    n.links_t.p0 = pd.DataFrame({"genset_s": 0.0}, index=n.snapshots)
    n.links_t.p0.iloc[7, 0] = 15.0
    kpi = renewable_fraction(n)
    assert kpi["fraction"] is None and "genset_s" in kpi["reason"], kpi


def test_a_battery_store_with_electrical_inflow_reads_as_storage():
    n = _dispatched()
    n.add("Bus", "batt", carrier="battery")
    n.add("Store", "cells", bus="batt", carrier="battery", e_nom=100.0)
    n.add("Link", "charger", bus0="ac", bus1="batt", p_nom=10.0, efficiency=0.95)
    n.add("Link", "discharger", bus0="batt", bus1="ac", p_nom=10.0, efficiency=0.95)
    n.links_t.p0 = pd.DataFrame({"charger": 0.0, "discharger": 0.0}, index=n.snapshots)
    n.links_t.p0.iloc[20, 1] = 5.0
    assert classify(n)["unattributed_links"] == []
    assert renewable_fraction(n)["fraction"] == pytest.approx(0.625)


def test_the_row_warns_about_a_fuel_store_genset():
    n = _network()
    n.add("Bus", "tankbus", carrier="diesel")
    n.add(
        "Store",
        "daytank",
        bus="tankbus",
        carrier="diesel",
        e_nom=500.0,
        e_initial=500.0,
    )
    n.add("Link", "genset_s", bus0="tankbus", bus1="ac", p_nom=25.0, efficiency=0.4)
    n.optimize.create_model()
    q: queue.SimpleQueue = queue.SimpleQueue()
    _wrap_with_min_renewable_fraction(
        n, None, SolverConfig(min_renewable_fraction=0.5), q
    )(n, n.snapshots)
    assert any("WARNING" in l and "genset_s" in l for l in _drain(q))


# ── mutation-gap closers (01a survivors) ─────────────────────────────────


def test_a_target_given_as_text_is_read_as_a_number():
    assert target_of(SimpleNamespace(min_renewable_fraction="0.5")) == 0.5


def test_a_bus_with_a_none_carrier_is_electrical():
    n = _network()
    n.buses["carrier"] = n.buses["carrier"].astype(object)
    n.buses.loc["ac", "carrier"] = None
    c = classify(n)
    assert c["nonren_generators"] == ["diesel"] and c["loads"] == ["site"]


def test_a_lost_production_slack_is_neither_non_renewable_nor_unattributed():
    n = _network()
    n.add("Generator", "__lost_ac", bus="ac", carrier="process", p_nom=5.0)
    n.add("Bus", "h2", carrier="H2")
    n.add("Generator", "__lost_h2", bus="h2", carrier="process", p_nom=5.0)
    n.add("Link", "fuel cell", bus0="h2", bus1="ac", p_nom=5.0, efficiency=0.5)
    c = classify(n)
    assert c["nonren_generators"] == ["diesel"]
    assert c["unattributed_generators"] == []


def test_a_genset_bus_reached_only_by_a_link_from_the_site_is_not_a_supply_bus():
    n = _network()
    n.add("Bus", "genbus", carrier="AC")
    n.add("Generator", "genset_b", bus="genbus", carrier="diesel", p_nom=10.0)
    n.add("Link", "feeder", bus0="ac", bus1="genbus", p_nom=10.0)
    c = classify(n)
    assert "genset_b" in c["nonren_generators"]
    assert c["source_links"] == []


def test_a_boiler_beside_a_genset_on_the_fuel_bus_is_not_counted():
    n = _dispatched()
    n.add("Bus", "heat", carrier="heat")
    n = _with_fuel(n, ("genset2", "fuel", "ac", {}), ("boiler", "fuel", "heat", {}))
    n.links_t.p0 = pd.DataFrame({"genset2": 0.0, "boiler": 0.0}, index=n.snapshots)
    n.links_t.p0.loc[n.snapshots[7], "genset2"] = 15.0
    n.links_t.p0.loc[n.snapshots[9], "boiler"] = 10.0
    n.generators_t.p.iloc[7, n.generators_t.p.columns.get_loc("diesel")] -= 6.0
    assert classify(n)["source_links"] == ["genset2"]
    kpi = renewable_fraction(n)
    assert kpi["nonrenewable_mwh"] == pytest.approx(90.0), kpi


def test_chained_grid_supply_buses_count_the_import_once():
    n = _network()
    n.add("Bus", "gridA", carrier="AC")
    n.add("Bus", "gridB", carrier="AC")
    n.add("Generator", "supplyA", bus="gridA", carrier="grid", p_nom=50.0)
    n.add("Generator", "supplyB", bus="gridB", carrier="grid", p_nom=50.0)
    n.add("Link", "a_to_b", bus0="gridA", bus1="gridB", p_nom=50.0)
    n.add("Link", "b_to_site", bus0="gridB", bus1="ac", p_nom=50.0)
    assert classify(n)["source_links"] == ["b_to_site"]


def test_a_slack_on_a_heat_bus_does_not_change_what_is_served():
    n = _dispatched()
    n.add("Bus", "heat", carrier="heat")
    n.add("Generator", "__voll_heatload", bus="heat", carrier="heat", p_nom=50.0)
    n.generators_t.p["__voll_heatload"] = 5.0
    kpi = renewable_fraction(n)
    assert kpi["served_mwh"] == pytest.approx(240.0)
    assert kpi["fraction"] == pytest.approx(0.625)


def test_the_reach_counts_only_renewables_on_electrical_buses():
    n = _network(pv_max=5.0)
    n.add("Generator", "__voll_site", bus="ac", carrier="load", p_nom=1000.0)
    n.add("Bus", "heat", carrier="heat")
    n.add("Generator", "solar_thermal", bus="heat", carrier="solar", p_nom=50.0)
    assert max_renewable_mwh(n) == {None: pytest.approx(40.0)}


def _grid_supply(n):
    """An electrical grid bus with a grid Generator and an import Link `poc`
    into the site; the grid runs 10 MW at 07:00 and replaces 9.8 MW of diesel."""
    n.add("Bus", "grid", carrier="AC")
    n.add("Generator", "supply", bus="grid", carrier="grid", p_nom=50.0)
    n.add("Link", "poc", bus0="grid", bus1="ac", p_nom=50.0, efficiency=0.98)
    n.generators_t.p["supply"] = 0.0
    n.generators_t.p.iloc[7, n.generators_t.p.columns.get_loc("supply")] = 10.0
    n.generators_t.p.iloc[7, n.generators_t.p.columns.get_loc("diesel")] -= 9.8
    return n


def test_the_kpi_counts_a_dispatched_grid_import_link():
    n = _grid_supply(_dispatched())
    n.links_t.p0 = pd.DataFrame({"poc": 0.0}, index=n.snapshots)
    n.links_t.p0.iloc[7, 0] = 10.0
    kpi = renewable_fraction(n)
    assert kpi["nonrenewable_mwh"] == pytest.approx(90.0), kpi
    assert kpi["fraction"] == pytest.approx(0.625)


def test_a_grid_bus_with_a_second_path_through_a_fuel_bus_fails_closed():
    n = _grid_supply(_dispatched())
    n.add("Bus", "mid", carrier="gas")
    n.add("Link", "to_mid", bus0="grid", bus1="mid", p_nom=10.0)
    n.add("Link", "mid_genset", bus0="mid", bus1="ac", p_nom=10.0)
    kpi = renewable_fraction(n)
    assert kpi["fraction"] is None and "supply" in kpi["reason"], kpi


def test_a_counted_source_link_with_an_electrical_bus2_fails_closed():
    n = _dispatched()
    n.add("Bus", "aux", carrier="AC")
    n = _with_fuel(n, ("genset2", "fuel", "ac", {"bus2": "aux", "efficiency2": 0.1}))
    assert classify(n)["source_links"] == ["genset2"]
    kpi = renewable_fraction(n)
    assert kpi["fraction"] is None and "tank" in kpi["reason"], kpi


def _store_genset(n, bus_carrier, store_carrier, *, p0=15.0, out="ac"):
    n.add("Bus", "tankbus", carrier=bus_carrier)
    n.add(
        "Store",
        "daytank",
        bus="tankbus",
        carrier=store_carrier,
        e_nom=500.0,
        e_initial=500.0,
    )
    n.add("Link", "genset_s", bus0="tankbus", bus1=out, p_nom=25.0, efficiency=0.4)
    n.links_t.p0 = pd.DataFrame({"genset_s": 0.0}, index=n.snapshots)
    n.links_t.p0.iloc[7, 0] = p0
    return n


def test_a_biogas_store_on_a_gas_bus_does_not_fail_closed():
    kpi = renewable_fraction(_store_genset(_dispatched(), "gas", "biogas"))
    assert kpi["fraction"] == pytest.approx(0.625), kpi


def test_a_store_without_a_carrier_on_a_biogas_bus_does_not_fail_closed():
    kpi = renewable_fraction(_store_genset(_dispatched(), "biogas", ""))
    assert kpi["fraction"] == pytest.approx(0.625), kpi


def test_a_diesel_store_feeding_only_a_boiler_does_not_fail_closed():
    n = _dispatched()
    n.add("Bus", "heat", carrier="heat")
    kpi = renewable_fraction(_store_genset(n, "diesel", "diesel", out="heat"))
    assert kpi["fraction"] == pytest.approx(0.625), kpi


def test_an_idle_fuel_store_genset_does_not_fail_closed():
    kpi = renewable_fraction(_store_genset(_dispatched(), "diesel", "diesel", p0=0.0))
    assert kpi["fraction"] == pytest.approx(0.625), kpi


def test_an_idle_unattributed_generator_does_not_fail_closed():
    n = _dispatched()
    n.add("Bus", "heat", carrier="heat")
    n = _with_fuel(n, ("chp", "fuel", "heat", {"bus2": "ac", "efficiency2": 0.3}))
    n.generators_t.p["tank"] = 0.0
    assert classify(n)["unattributed_generators"] == ["tank"]
    assert renewable_fraction(n)["fraction"] == pytest.approx(0.625)


# ── which dispatch is missing ────────────────────────────────────────────


def test_generators_without_dispatch_read_as_not_solved():
    n = _network()
    n.loads_t.p = pd.DataFrame({"site": LOAD_MW}, index=n.snapshots)
    kpi = renewable_fraction(n)
    assert kpi["fraction"] is None and "not been solved" in kpi["reason"], kpi


def test_a_network_without_generators_reads_its_served_load():
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=HOURS, freq="h"))
    n.add("Bus", "ac", carrier="AC")
    n.add("Load", "site", bus="ac", p_set=LOAD_MW)
    n.loads_t.p = pd.DataFrame({"site": LOAD_MW}, index=n.snapshots)
    kpi = renewable_fraction(n)
    assert kpi["served_mwh"] == pytest.approx(240.0)
    assert kpi["fraction"] == pytest.approx(1.0), kpi


def test_no_electrical_load_is_nothing_served_not_unsolved():
    n = _dispatched()
    n.remove("Load", "site")
    n.loads_t.p = pd.DataFrame(index=n.snapshots)
    kpi = renewable_fraction(n)
    assert kpi["fraction"] is None and "no electricity served" in kpi["reason"], kpi


def test_a_load_missing_from_the_dispatch_reads_as_not_solved():
    n = _dispatched()
    n.add("Load", "site2", bus="ac", p_set=1.0)
    kpi = renewable_fraction(n)
    assert kpi["fraction"] is None and "not been solved" in kpi["reason"], kpi


def test_the_kpi_applies_the_generator_weightings():
    n = _dispatched()
    n.snapshot_weightings.loc[:, "generators"] = 2.0
    kpi = renewable_fraction(n)
    assert kpi["served_mwh"] == pytest.approx(480.0)
    assert kpi["nonrenewable_mwh"] == pytest.approx(180.0)
    assert kpi["fraction"] == pytest.approx(0.625)


# ── the result payload ───────────────────────────────────────────────────


def test_the_result_without_a_capture_is_the_kpi():
    kpi = renewable_fraction_result(_dispatched(), None, 0.5)
    assert kpi["fraction"] == pytest.approx(0.625) and kpi["target"] == 0.5


def test_a_capture_without_lost_load_is_the_kpi():
    kpi = renewable_fraction_result(_dispatched(), {"other": 1}, None)
    assert kpi["fraction"] == pytest.approx(0.625), kpi


def test_a_capture_that_is_not_a_frame_is_refused():
    kpi = renewable_fraction_result(_dispatched(), {"lost_load_t": [0.0] * HOURS}, None)
    assert kpi["fraction"] is None and "another solve" in kpi["reason"], kpi


# ── the LP row: gross import and inactive assets ─────────────────────────


def _row_terms(n, name) -> set[tuple[str, str]]:
    """The (variable, asset) pairs of one LP row."""
    m = n.model
    labels = np.ravel(m.constraints[name].vars.values)
    out = set()
    for lab in labels[labels >= 0]:
        var, coord = m.variables.get_label_position(int(lab))
        out.add((var, str(coord["name"])))
    return out


def _with_row(n, f=0.5):
    n.optimize.create_model()
    q: queue.SimpleQueue = queue.SimpleQueue()
    _wrap_with_min_renewable_fraction(
        n, None, SolverConfig(min_renewable_fraction=f), q
    )(n, n.snapshots)
    return _drain(q)


def _bidirectional_grid(n):
    n.add(
        "Generator",
        "grid",
        bus="ac",
        carrier="grid",
        p_nom=50.0,
        p_min_pu=-1.0,
        marginal_cost=100.0,
    )
    return n


def test_a_bidirectional_grid_generator_is_counted_gross():
    n = _bidirectional_grid(_network())
    _with_row(n)
    aux = f"{_AUX}-Generator-p"
    defn = n.model.constraints[f"{aux}-def"]
    assert set(np.ravel(defn.sign.values)) == {">="}
    assert (aux, "grid") in _row_terms(n, row_name(None))
    assert ("Generator-p", "grid") not in _row_terms(n, row_name(None))


def test_a_mixed_fleet_keeps_the_grid_and_the_diesel_in_the_row():
    n = _bidirectional_grid(_network())
    _with_row(n)
    terms = _row_terms(n, row_name(None))
    assert {(f"{_AUX}-Generator-p", "grid"), ("Generator-p", "diesel")} <= terms


def test_a_fleet_whose_only_non_renewable_is_a_bidirectional_grid_builds():
    n = _bidirectional_grid(_network())
    n.remove("Generator", "diesel")
    _with_row(n)
    assert _row_terms(n, row_name(None)) == {(f"{_AUX}-Generator-p", "grid")}


def test_inactive_assets_are_left_out_of_the_row():
    n = _network()
    n.generators.loc["diesel", "active"] = False
    n.add("Bus", "grid", carrier="AC")
    n.add("Generator", "supply", bus="grid", carrier="grid", p_nom=50.0)
    n.add("Link", "poc", bus0="grid", bus1="ac", p_nom=50.0, active=False)
    n.add("Generator", "__voll_site", bus="ac", carrier="load", p_nom=1e3, active=False)
    n.add("Generator", "diesel2", bus="ac", carrier="diesel", p_nom=LOAD_MW)
    _with_row(n)
    assert _row_terms(n, row_name(None)) == {("Generator-p", "diesel2")}


# ── the period in the log and the hint ───────────────────────────────────


def _no_load_in_2035(n):
    n.loads_t.p_set = pd.DataFrame(
        {
            "site": [
                LOAD_MW if p == 2030 else 0.0 for p in n.snapshots.get_level_values(0)
            ]
        },
        index=n.snapshots,
    )
    return n


def test_the_log_names_the_period_without_load():
    n = _no_load_in_2035(_two_period())
    n.optimize.create_model(multi_investment_periods=True)
    q: queue.SimpleQueue = queue.SimpleQueue()
    _wrap_with_min_renewable_fraction(
        n,
        None,
        SolverConfig(min_renewable_fraction=0.5, multi_investment_periods=True),
        q,
    )(n, n.snapshots)
    log = _drain(q)
    assert any("period 2035 has no electrical load" in l for l in log), log
    assert not any("None" in l for l in log), log


def test_the_log_names_a_single_period_without_load_all():
    n = _network()
    n.loads.loc["site", "p_set"] = 0.0
    log = _with_row(n)
    assert any("period all has no electrical load" in l for l in log), log
    assert not any("None" in l for l in log), log


def test_the_hint_names_each_period_with_its_own_need_and_reach():
    n = _two_period(weights=(1.0, 2.0))
    n.generators.loc["pv", "p_nom_max"] = 5.0
    assert max_renewable_mwh(n) == {
        2030: pytest.approx(40.0),
        2035: pytest.approx(80.0),
    }
    lines = infeasibility_lines(n, SolverConfig(min_renewable_fraction=0.9))
    assert len(lines) == 2, lines
    assert " in 2030" in lines[0] and "216.0 MWh" in lines[0], lines[0]
    assert "at most 40.0 MWh" in lines[0], lines[0]
    assert " in 2035" in lines[1] and "432.0 MWh" in lines[1], lines[1]
    assert "at most 80.0 MWh" in lines[1], lines[1]


def test_a_period_without_load_gets_no_hint():
    n = _no_load_in_2035(_two_period())
    lines = infeasibility_lines(n, SolverConfig(min_renewable_fraction=0.9))
    assert len(lines) == 1 and " in 2030" in lines[0], lines


# ── review: served = electrical load − every electrical slack ───────────


def _with_heat_load(n):
    n.add("Bus", "heat", carrier="heat")
    n.add("Load", "hotwater", bus="heat", p_set=5.0)
    return n


def test_shed_heat_in_the_capture_does_not_reduce_electricity_served():
    n = _with_heat_load(_dispatched())
    shed = pd.DataFrame({"site": 0.0, "hotwater": 5.0}, index=n.snapshots)
    shed.iloc[:10, 0] = 4.0  # 40 MWh of electricity shed, 120 MWh of heat
    kpi = renewable_fraction_result(n, {"lost_load_t": shed}, None)
    assert kpi["served_mwh"] == pytest.approx(200.0), kpi
    assert kpi["fraction"] == pytest.approx((200.0 - 90.0) / 200.0), kpi


def test_electrical_demand_response_in_the_capture_is_not_served():
    n = _with_heat_load(_dispatched())
    dsr = pd.DataFrame({"ac": 0.0, "heat": 3.0}, index=n.snapshots)
    dsr.iloc[:10, 0] = 2.0  # 20 MWh responded on the site bus
    kpi = renewable_fraction_result(n, {"dsr_t": dsr}, None)
    assert kpi["served_mwh"] == pytest.approx(220.0), kpi


def _with_cooling(n):
    n.add("Bus", "dc_mv", carrier="AC")
    n.add("Load", "cooling", bus="dc_mv", p_set=2.0)
    n.loads_t.p["cooling"] = 2.0  # 48 MWh: 288 MWh in all
    return n


def test_dc_curtailment_in_the_capture_is_not_served():
    n = _with_cooling(_dispatched())
    curt = pd.Series(0.0, index=n.snapshots)
    curt.iloc[:10] = 1.0  # 10 MWh curtailed
    cap = {"dc_curtailment": {"p_t": curt, "capture_failed": False}}
    kpi = renewable_fraction_result(n, cap, None)
    assert kpi["served_mwh"] == pytest.approx(278.0), kpi


def test_a_failed_dc_curtailment_capture_makes_the_fraction_unavailable():
    n = _with_cooling(_dispatched())
    cap = {"dc_curtailment": {"p_t": None, "capture_failed": True}}
    kpi = renewable_fraction_result(n, cap, None)
    assert kpi["fraction"] is None and "curtailment" in kpi["reason"], kpi


def test_committed_demand_response_counts_without_a_capture():
    from services.commercial.settlement_inputs import commit_dsr

    n = _dispatched()
    dsr = pd.DataFrame({"ac": 0.0}, index=n.snapshots)
    dsr.iloc[:10, 0] = 2.0
    commit_dsr(n, dsr, 20.0)
    kpi = renewable_fraction_result(n, None, None)
    assert kpi["served_mwh"] == pytest.approx(220.0), kpi


def test_a_demand_response_capture_from_another_solve_is_refused():
    n = _dispatched()
    other = pd.DataFrame(
        {"ac": [1.0]}, index=pd.date_range("2031-01-01", periods=1, freq="h")
    )
    kpi = renewable_fraction_result(n, {"dsr_t": other}, None)
    assert kpi["fraction"] is None and "another solve" in kpi["reason"], kpi


# ── review: rolling and myopic are refused at preflight ──────────────────


@pytest.mark.parametrize("strategy", ["rolling", "myopic"])
def test_rolling_and_myopic_are_preflight_errors(strategy):
    from services.validation_service import validate_for_run

    cfg = SolverConfig(min_renewable_fraction=0.5, solve_strategy=strategy)
    codes = {i.code for i in validate_for_run(_network(), cfg) if i.severity == "error"}
    assert "min_renewable_fraction_unsupported_strategy" in codes
    cfg.min_renewable_fraction = None
    codes = {i.code for i in validate_for_run(_network(), cfg) if i.severity == "error"}
    assert "min_renewable_fraction_unsupported_strategy" not in codes


# ── review: a battery behind one bidirectional Link is storage ───────────


@pytest.mark.parametrize("series", [False, True])
def test_a_battery_behind_one_bidirectional_link_reads_as_storage(series):
    n = _dispatched()
    n.add("Bus", "batt", carrier="battery")
    n.add("Store", "cells", bus="batt", carrier="battery", e_nom=100.0)
    n.add(
        "Link",
        "inverter",
        bus0="batt",
        bus1="ac",
        p_nom=10.0,
        efficiency=0.95,
        p_min_pu=0.0 if series else -1.0,
    )
    if series:
        n.links_t.p_min_pu = pd.DataFrame({"inverter": -1.0}, index=n.snapshots)
    n.links_t.p0 = pd.DataFrame({"inverter": 0.0}, index=n.snapshots)
    n.links_t.p0.iloc[20, 0] = 5.0
    n.links_t.p0.iloc[10, 0] = -5.0
    assert classify(n)["unattributed_links"] == []
    assert renewable_fraction(n)["fraction"] == pytest.approx(0.625)


# ── review: the target keeps its precision in the text ───────────────────


def test_the_hint_prints_the_target_without_rounding():
    lines = infeasibility_lines(
        _network(pv_max=5.0), SolverConfig(min_renewable_fraction=0.825)
    )
    assert "target of 82.5%" in lines[0], lines[0]
    lines = infeasibility_lines(
        _network(pv_max=5.0), SolverConfig(min_renewable_fraction=0.9)
    )
    assert "target of 90%" in lines[0], lines[0]


def test_the_row_log_prints_the_target_without_rounding():
    log = _with_row(_network(), f=0.82555)
    assert any("Minimum renewable fraction 82.555% (row" in l for l in log), log


# ── GK F1 (R13): a fuel Store fails closed whatever its bus's carrier ────


def _dsl_store_genset(n):
    """Bus `dsl` with no carrier (PyPSA reads it as AC) holds a diesel Store;
    a 10 MW genset Link runs from it into the site."""
    n.add("Bus", "dsl")
    n.add("Store", "dsltank", bus="dsl", carrier="diesel", e_nom=500.0, e_initial=500.0)
    n.add("Link", "genset_d", bus0="dsl", bus1="ac", p_nom=10.0, efficiency=1.0)
    return n


def test_a_diesel_store_on_a_bus_without_a_carrier_fails_closed():
    n = _dsl_store_genset(_dispatched())
    n.links_t.p0 = pd.DataFrame({"genset_d": 0.0}, index=n.snapshots)
    n.links_t.p0.iloc[7, 0] = 10.0
    n.generators_t.p.iloc[7, n.generators_t.p.columns.get_loc("diesel")] -= 10.0
    kpi = renewable_fraction(n)
    assert kpi["fraction"] is None and "genset_d" in kpi["reason"], kpi


def test_the_row_warns_about_a_diesel_store_on_a_bus_without_a_carrier():
    log = _with_row(_dsl_store_genset(_network()))
    assert any("WARNING" in l and "genset_d" in l for l in log), log


def _site_diesel_store(n):
    n.add("Store", "sitetank", bus="ac", carrier="diesel", e_nom=500.0, e_initial=500.0)
    return n


def test_a_diesel_store_on_the_site_bus_fails_closed():
    n = _site_diesel_store(_dispatched())
    n.stores_t.p = pd.DataFrame({"sitetank": 0.0}, index=n.snapshots)
    n.stores_t.p.iloc[7, 0] = 10.0  # discharges 10 MWh into the site
    n.generators_t.p.iloc[7, n.generators_t.p.columns.get_loc("diesel")] -= 10.0
    kpi = renewable_fraction(n)
    assert kpi["fraction"] is None and "sitetank" in kpi["reason"], kpi


def test_the_row_warns_about_a_diesel_store_on_the_site_bus():
    log = _with_row(_site_diesel_store(_network()))
    assert any("WARNING" in l and "sitetank" in l for l in log), log


@pytest.mark.parametrize("bus_carrier", ["battery", None])
def test_a_battery_store_on_its_own_bus_is_not_refused(bus_carrier):
    n = _dispatched()
    n.add("Bus", "batt", **({"carrier": bus_carrier} if bus_carrier else {}))
    n.add("Store", "cells", bus="batt", carrier="battery", e_nom=100.0)
    n.add("Link", "inverter", bus0="batt", bus1="ac", p_nom=10.0, p_min_pu=-1.0)
    n.links_t.p0 = pd.DataFrame({"inverter": 0.0}, index=n.snapshots)
    n.links_t.p0.iloc[20, 0] = 5.0
    c = classify(n)
    assert c["unattributed_links"] == [] and c["unattributed_stores"] == []
    assert renewable_fraction(n)["fraction"] == pytest.approx(0.625)


def test_a_battery_store_on_the_site_bus_is_not_refused():
    n = _dispatched()
    n.add("Store", "cells", bus="ac", carrier="li-ion", e_nom=100.0)
    n.stores_t.p = pd.DataFrame({"cells": 0.0}, index=n.snapshots)
    n.stores_t.p.iloc[20, 0] = 5.0
    c = classify(n)
    assert c["unattributed_links"] == [] and c["unattributed_stores"] == []
    assert renewable_fraction(n)["fraction"] == pytest.approx(0.625)
    assert not any("WARNING" in l for l in _with_row(_site_battery(_network())))


def _site_battery(n):
    n.add("Store", "cells", bus="ac", carrier="battery", e_nom=100.0)
    return n


# ── N3: a fuel StorageUnit fails closed like a fuel Store ────────────────


def test_a_diesel_storage_unit_on_the_site_bus_fails_closed():
    n = _dispatched()
    n.add("StorageUnit", "dieselsu", bus="ac", carrier="diesel", p_nom=10.0)
    n.storage_units_t.p["dieselsu"] = 0.0
    n.storage_units_t.p.iloc[7, n.storage_units_t.p.columns.get_loc("dieselsu")] = 10.0
    n.generators_t.p.iloc[7, n.generators_t.p.columns.get_loc("diesel")] -= 10.0
    kpi = renewable_fraction(n)
    assert kpi["fraction"] is None and "dieselsu" in kpi["reason"], kpi
    log = _with_row(_network().copy())
    assert not any("WARNING" in l for l in log), log
    n2 = _network()
    n2.add("StorageUnit", "dieselsu", bus="ac", carrier="diesel", p_nom=10.0)
    assert any("WARNING" in l and "dieselsu" in l for l in _with_row(n2))


def test_a_diesel_storage_unit_feeding_a_genset_link_fails_closed():
    n = _dispatched()
    n.add("Bus", "tankbus", carrier="diesel")
    n.add("StorageUnit", "tank_su", bus="tankbus", carrier="diesel", p_nom=25.0)
    n.add("Link", "genset_s", bus0="tankbus", bus1="ac", p_nom=25.0, efficiency=0.4)
    n.links_t.p0 = pd.DataFrame({"genset_s": 0.0}, index=n.snapshots)
    n.links_t.p0.iloc[7, 0] = 15.0
    kpi = renewable_fraction(n)
    assert kpi["fraction"] is None and "genset_s" in kpi["reason"], kpi


@pytest.mark.parametrize("bus", ["ac", "dcbus"])
def test_a_battery_storage_unit_is_not_refused(bus):
    n = _dispatched()
    if bus != "ac":
        n.add("Bus", bus)
        n.add("Link", "pcs", bus0=bus, bus1="ac", p_nom=10.0, p_min_pu=-1.0)
    n.add("StorageUnit", "bess2", bus=bus, carrier="Li-ion", p_nom=10.0)
    c = classify(n)
    assert c["unattributed_stores"] == [] and c["unattributed_links"] == []
    assert renewable_fraction(n)["fraction"] == pytest.approx(0.625)


# ── GK2 B1: every left-hand term of the row carries the weighting ────────


def _row_coeffs(n, name) -> dict[tuple[str, str], set[float]]:
    m = n.model
    con = m.constraints[name]
    labels = np.ravel(con.vars.values)
    coeffs = np.ravel(con.coeffs.values)
    out: dict[tuple[str, str], set[float]] = {}
    for lab, co in zip(labels, coeffs):
        if lab < 0:
            continue
        var, coord = m.variables.get_label_position(int(lab))
        out.setdefault((var, str(coord["name"])), set()).add(round(float(co), 6))
    return out


def test_each_row_term_carries_the_generator_weighting():
    n = _network()
    n.add("Bus", "grid", carrier="AC")
    n.add("Generator", "supply", bus="grid", carrier="grid", p_nom=50.0)
    n.add("Link", "poc", bus0="grid", bus1="ac", p_nom=50.0, efficiency=0.98)
    n.add("Generator", "__voll_site", bus="ac", carrier="load", p_nom=1e3)
    n.snapshot_weightings.loc[:, "generators"] = 2.0
    assert classify(n)["slack_generators"] == ["__voll_site"]
    _with_row(n, f=0.5)
    co = _row_coeffs(n, row_name(None))
    assert co[("Generator-p", "diesel")] == {2.0}
    assert co[("Link-p", "poc")] == {round(2.0 * 0.98, 6)}
    assert co[("Generator-p", "__voll_site")] == {1.0}  # 2 × (1 − 0.5)
    assert _rhs(n, row_name(None)) == pytest.approx(0.5 * 2.0 * 240.0)


# ── GK2 B2: the KPI reads the time-varying efficiency ────────────────────


def test_the_kpi_counts_a_source_link_at_its_time_varying_efficiency():
    n = _dispatched()
    n.add("Bus", "fuel", carrier="diesel")
    n.add("Generator", "tank", bus="fuel", carrier="diesel", p_nom=100.0)
    n.add(
        "Link",
        "genset2",
        bus0="fuel",
        bus1="ac",
        p_nom=25.0,
        efficiency=pd.Series(0.4, index=n.snapshots),
    )
    n.links.loc["genset2", "efficiency"] = 0.9  # static: must not be read
    n.links_t.p0 = pd.DataFrame({"genset2": 0.0}, index=n.snapshots)
    n.links_t.p0.iloc[7, 0] = 15.0  # 6 MWh at η(t) = 0.4
    n.generators_t.p["tank"] = 0.0
    n.generators_t.p.iloc[7, n.generators_t.p.columns.get_loc("tank")] = 15.0
    n.generators_t.p.iloc[7, n.generators_t.p.columns.get_loc("diesel")] -= 6.0
    kpi = renewable_fraction(n)
    assert kpi["nonrenewable_mwh"] == pytest.approx(90.0), kpi


# ── GK2 B3: an inactive Load is not served ───────────────────────────────


def _with_inactive_load(n):
    n.add("Load", "mothballed", bus="ac", p_set=30.0, active=False)
    return n


def test_an_inactive_load_is_not_in_the_kpi():
    n = _with_inactive_load(_dispatched())
    n.loads_t.p["mothballed"] = 30.0
    assert classify(n)["loads"] == ["site"]
    kpi = renewable_fraction(n)
    assert kpi["served_mwh"] == pytest.approx(240.0), kpi
    assert kpi["fraction"] == pytest.approx(0.625), kpi


def test_an_inactive_load_is_not_in_the_row():
    n = _with_inactive_load(_network())
    _with_row(n, f=0.5)
    assert _rhs(n, row_name(None)) == pytest.approx(0.5 * 240.0)


# ── GK F2/F3 (R9, R4): the hint and the row read the scaled load ─────────


def test_the_infeasibility_hint_names_the_scaled_need():
    n = _two_period()
    n.generators.loc["pv", "p_nom_max"] = 12.0
    status, condition, log = _solve(
        n,
        min_renewable_fraction=0.6,
        multi_investment_periods=True,
        investment_periods=[2030, 2035],
        load_scalers={"2035": 1.5},
    )
    assert status != "ok" and "infeasible" in str(condition).lower(), log
    hints = [l for l in log if l.startswith("[INFEASIBLE]") and "renewable" in l]
    h2035 = [l for l in hints if " in 2035" in l]
    assert h2035 and "needs 216.0 MWh" in h2035[0], hints
    assert any(" in 2030" in l and "needs 144.0 MWh" in l for l in hints), hints


def test_a_solve_with_the_target_off_clears_the_previous_solves_need():
    """F2′: the stash of the need the rows were built on is another solve's
    after a solve with the target off; the hint must not read it."""
    n = _network()
    _with_row(n, f=0.5)  # first solve: the stash holds 0.5 × 24 × LOAD_MW
    assert getattr(n, _BUILT)["demand"], getattr(n, _BUILT)
    n.loads.loc["site", "p_set"] = 2 * LOAD_MW
    off = _wrap_with_min_renewable_fraction(
        n, None, SolverConfig(min_renewable_fraction=None)
    )
    assert off is None  # second solve, target off: the LP is untouched
    assert getattr(n, _BUILT, None) is None
    assert infeasibility_lines(n, SolverConfig(min_renewable_fraction=None)) == []
    # Read with the old target, the hint uses the network's load, not the
    # stale need of the first solve.
    lines = infeasibility_lines(n, SolverConfig(min_renewable_fraction=0.5))
    assert lines and f"needs {0.5 * 2 * LOAD_MW * HOURS:,.1f} MWh" in lines[0], lines


def test_the_row_uses_the_load_scaled_by_carrier():
    n = _two_period()
    status, _, log = _solve(
        n,
        min_renewable_fraction=0.6,
        multi_investment_periods=True,
        investment_periods=[2030, 2035],
        load_scalers_by_carrier={"electrical": {"2035": 1.5}},
    )
    assert status == "ok", log
    rows = [l for l in log if l.startswith("[RENEWABLE]")]
    assert any(
        row_name(2030) in l and "≤ 96.0 MWh of 240.0 MWh electrical load" in l
        for l in rows
    ), rows
    assert any(
        row_name(2035) in l and "≤ 144.0 MWh of 360.0 MWh electrical load" in l
        for l in rows
    ), rows
    assert renewable_fraction(n)["fraction"] == pytest.approx(0.6, abs=1e-6)


# ── GK F4 (R7): project load and bundle import drop an invalid target ────

_BAD_STORED = [1.5, float("nan"), float("inf"), -0.1, "x"]


def _stored(v) -> str:
    # json.dumps writes NaN / Infinity, as a foreign solver_config.json may.
    return json.dumps({"min_renewable_fraction": v})


@pytest.mark.parametrize("bad", _BAD_STORED)
def test_the_loader_drops_an_invalid_target(bad):
    from routers.projects import _solver_config_from_dict

    cfg = _solver_config_from_dict(json.loads(_stored(bad)))
    assert cfg.min_renewable_fraction is None
    assert _cfg_sha(cfg) == MASTER_DEFAULT_CONFIG_SHA


@pytest.mark.parametrize("bad", [1.5, float("nan")])
def test_loading_a_project_with_an_invalid_target_drops_and_names_it(
    client, api_project, project_storage_dir, bad
):
    name = api_project("rf-load")
    (project_storage_dir(name) / "solver_config.json").write_text(_stored(bad))
    r = client.get(f"/api/projects/{name}")
    assert r.status_code == 200, r.text[:300]
    dropped = r.json()["dropped_goal"]
    assert [d["field"] for d in dropped] == ["min_renewable_fraction"], dropped
    live = client.get("/api/simulation/solver_config").json()
    assert live["min_renewable_fraction"] is None


@pytest.mark.parametrize("bad", [1.5, float("nan")])
def test_a_bundle_with_an_invalid_target_imports_and_names_the_drop(
    client, api_project, project_storage_dir, bad
):
    import io
    import zipfile

    nc = (project_storage_dir(api_project("rf-src")) / "network.nc").read_bytes()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("network.nc", nc)
        zf.writestr("solver_config.json", _stored(bad))
        zf.writestr("metadata.json", json.dumps({"name": "rf-import"}))
    r = client.post(
        "/api/projects/import_bundle",
        params={"name": "rf-import"},
        files={"file": ("g.zip", buf.getvalue(), "application/zip")},
    )
    assert r.status_code in (200, 201), r.text[:300]
    dropped = r.json()["dropped_goal"]
    assert [d["field"] for d in dropped] == ["min_renewable_fraction"], dropped
    live = client.get("/api/simulation/solver_config").json()
    assert live["min_renewable_fraction"] is None


def test_a_valid_stored_target_loads_unchanged():
    from routers.projects import _solver_config_from_dict

    assert (
        _solver_config_from_dict({"min_renewable_fraction": 0.6}).min_renewable_fraction
        == 0.6
    )
    assert (
        _solver_config_from_dict({"min_renewable_fraction": 0}).min_renewable_fraction
        == 0
    )


@pytest.mark.parametrize("off", [float("nan"), float("inf")])
def test_a_non_finite_target_is_off_in_the_fingerprint(off):
    assert (
        _cfg_sha(SolverConfig(min_renewable_fraction=off)) == MASTER_DEFAULT_CONFIG_SHA
    )


# ── RENFRAC-ALL-1 / R14: a share of all electricity used on site ─────────


def _site_with_conversion() -> pypsa.Network:
    """A 2 MW AC load, PV and a grid import on the site bus, an electrolyser
    into an H2 offtake and a heat pump into a heat load (no path back)."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=HOURS, freq="h"))
    n.add("Bus", "ac", carrier="AC")
    n.add("Bus", "h2", carrier="H2")
    n.add("Bus", "heat", carrier="heat")
    n.add("Load", "site", bus="ac", p_set=2.0)
    n.add("Load", "offtake", bus="h2", p_set=4.2)
    n.add("Load", "space", bus="heat", p_set=6.0)
    n.add("Generator", "pv", bus="ac", carrier="solar", p_nom=20.0)
    n.add("Generator", "grid", bus="ac", carrier="grid", p_nom=20.0, marginal_cost=50.0)
    n.add("Link", "electrolyser", bus0="ac", bus1="h2", p_nom=10.0, efficiency=0.7)
    n.add("Link", "heat_pump", bus0="ac", bus1="heat", p_nom=5.0, efficiency=3.0)
    return n


def _dispatch_conversion(n):
    """6 MW to the electrolyser, 2 MW to the heat pump, 2 MW of load: 10 MW
    used, half PV and half grid (f = 0.5 by hand)."""
    sns = n.snapshots
    n.loads_t.p = pd.DataFrame({"site": 2.0, "offtake": 4.2, "space": 6.0}, index=sns)
    n.generators_t.p = pd.DataFrame({"pv": 5.0, "grid": 5.0}, index=sns)
    n.links_t.p0 = pd.DataFrame({"electrolyser": 6.0, "heat_pump": 2.0}, index=sns)
    return n


def test_conversion_links_are_classified_as_use():
    c = classify(_site_with_conversion())
    assert c["conversion_links"] == ["electrolyser", "heat_pump"]
    assert c["storage_links"] == [] and c["ambiguous_links"] == []


def test_the_kpi_counts_what_converters_draw_as_used():
    kpi = renewable_fraction(_dispatch_conversion(_site_with_conversion()))
    assert kpi["served_mwh"] == pytest.approx(240.0), kpi  # 48 load + 192 drawn
    assert kpi["conversion_mwh"] == pytest.approx(192.0), kpi
    assert kpi["nonrenewable_mwh"] == pytest.approx(120.0), kpi
    assert kpi["fraction"] == pytest.approx(0.5), kpi
    assert kpi["note"], kpi


def test_the_row_puts_the_conversion_draw_on_the_left():
    n = _site_with_conversion()
    _with_row(n, f=0.6)
    co = _row_coeffs(n, row_name(None))
    assert co[("Link-p", "electrolyser")] == {-0.4}
    assert co[("Link-p", "heat_pump")] == {-0.4}
    assert co[("Generator-p", "grid")] == {1.0}
    assert _rhs(n, row_name(None)) == pytest.approx(0.4 * 48.0)


def test_the_draw_term_carries_the_generator_weighting():
    n = _site_with_conversion()
    n.snapshot_weightings.loc[:, "generators"] = 2.0
    _with_row(n, f=0.6)
    co = _row_coeffs(n, row_name(None))
    assert co[("Link-p", "electrolyser")] == {-0.8}
    n = _dispatch_conversion(_site_with_conversion())
    n.snapshot_weightings.loc[:, "generators"] = 2.0
    assert renewable_fraction(n)["conversion_mwh"] == pytest.approx(384.0)


def test_an_inactive_converter_is_not_counted():
    n = _site_with_conversion()
    n.links.loc["heat_pump", "active"] = False
    assert classify(n)["conversion_links"] == ["electrolyser"]
    n = _dispatch_conversion(n)
    assert renewable_fraction(n)["conversion_mwh"] == pytest.approx(144.0)
    _with_row(_site_with_conversion(), f=0.6)  # builds with both


def test_a_second_electrical_input_counts_its_draw():
    """A DAC-like Link: bus0 heat, bus1 CO2, electricity drawn on bus2."""
    n = _dispatch_conversion(_site_with_conversion())
    n.add("Bus", "co2", carrier="co2")
    n.add("Load", "co2_sink", bus="co2", p_set=0.0)
    n.add(
        "Link", "dac", bus0="heat", bus1="co2", bus2="ac", efficiency2=-0.5, p_nom=4.0
    )
    assert classify(n)["conversion_inputs"]["dac"] == [2]
    n.links_t.p0["dac"] = 0.0
    n.links_t.p0.iloc[3, n.links_t.p0.columns.get_loc("dac")] = 4.0  # 2 MWh drawn
    kpi = renewable_fraction(n)
    assert kpi["conversion_mwh"] == pytest.approx(194.0), kpi
    m = _site_with_conversion()
    m.add("Bus", "co2", carrier="co2")
    m.add(
        "Link", "dac", bus0="heat", bus1="co2", bus2="ac", efficiency2=-0.5, p_nom=4.0
    )
    _with_row(m, f=0.6)
    assert _row_coeffs(m, row_name(None))[("Link-p", "dac")] == {-0.2}  # −0.4 × 0.5


def test_a_battery_charger_is_not_use():
    n = _dispatched()
    n.add("Bus", "batt", carrier="battery")
    n.add("Store", "cells", bus="batt", carrier="battery", e_nom=100.0)
    n.add("Link", "charger", bus0="ac", bus1="batt", p_nom=10.0, efficiency=0.95)
    n.add("Link", "discharger", bus0="batt", bus1="ac", p_nom=10.0, efficiency=0.95)
    n.add("Bus", "batt2")
    n.add("Bus", "cellbus2", carrier="battery")
    n.add("Store", "cells2", bus="cellbus2", carrier="battery", e_nom=100.0)
    n.add("Link", "bidir", bus0="ac", bus1="cellbus2", p_nom=10.0, p_min_pu=-1.0)
    c = classify(n)
    assert c["conversion_links"] == [] and c["ambiguous_links"] == []
    assert set(c["storage_links"]) == {"charger", "bidir"}
    n.links_t.p0 = pd.DataFrame(
        {"charger": 5.0, "discharger": 0.0, "bidir": 3.0}, index=n.snapshots
    )
    assert renewable_fraction(n)["served_mwh"] == pytest.approx(240.0)


def test_an_h2_storage_loop_is_not_use():
    n = _dispatched()
    n.add("Bus", "h2", carrier="H2")
    n.add("Store", "h2tank", bus="h2", carrier="H2", e_nom=200.0)
    n.add("Link", "electrolyser", bus0="ac", bus1="h2", p_nom=10.0, efficiency=0.7)
    n.add("Link", "fuel cell", bus0="h2", bus1="ac", p_nom=5.0, efficiency=0.5)
    c = classify(n)
    assert c["conversion_links"] == [] and c["storage_links"] == ["electrolyser"]
    n.links_t.p0 = pd.DataFrame(
        {"electrolyser": 4.0, "fuel cell": 0.0}, index=n.snapshots
    )
    kpi = renewable_fraction(n)
    assert kpi["served_mwh"] == pytest.approx(240.0) and kpi[
        "fraction"
    ] == pytest.approx(0.625)


def _ambiguous_h2(n):
    n.add("Bus", "h2", carrier="H2")
    n.add("Store", "h2tank", bus="h2", carrier="H2", e_nom=200.0)
    n.add("Load", "offtake", bus="h2", p_set=1.0)
    n.add("Link", "electrolyser", bus0="ac", bus1="h2", p_nom=10.0, efficiency=0.7)
    n.add("Link", "fuel cell", bus0="h2", bus1="ac", p_nom=5.0, efficiency=0.5)
    return n


def test_an_h2_bus_with_an_offtake_and_a_fuel_cell_fails_closed():
    n = _ambiguous_h2(_dispatched())
    n.loads_t.p["offtake"] = 1.0
    assert classify(n)["ambiguous_links"] == ["electrolyser"]
    n.links_t.p0 = pd.DataFrame(
        {"electrolyser": 4.0, "fuel cell": 0.0}, index=n.snapshots
    )
    kpi = renewable_fraction(n)
    assert kpi["fraction"] is None and "electrolyser" in kpi["reason"], kpi
    # R15: the reason says why in plain words, not a generic "not solved".
    for got in (kpi, renewable_fraction_result(n, None, 0.5)):
        reason = got["reason"]
        assert got["fraction"] is None, got
        assert (
            "electrolyser feeds a bus with both a load and a path back to "
            "electricity (ambiguous conversion)" in reason
        ), reason
        assert "cannot be attributed" in reason, reason
        assert "not evaluated" not in reason and "not been solved" not in reason
    log = _with_row(_ambiguous_h2(_network()))
    assert any("WARNING" in l and "electrolyser" in l for l in log), log


def _h2_with_negative_offtake(n, *, fuel_cell: bool, **offtake):
    """An electrolyser into an H2 bus whose offtake is a Generator that
    withdraws (``offtake`` sets sign or p_min_pu/p_max_pu), with or without a
    fuel cell back to electricity."""
    n.add("Bus", "h2", carrier="H2")
    n.add("Generator", "offtake", bus="h2", carrier="H2", p_nom=5.0, **offtake)
    n.add("Link", "electrolyser", bus0="ac", bus1="h2", p_nom=10.0, efficiency=0.7)
    if fuel_cell:
        n.add("Link", "fuel cell", bus0="h2", bus1="ac", p_nom=5.0, efficiency=0.5)
    return n


_WITHDRAWS = [
    pytest.param({"sign": -1.0}, id="sign"),
    pytest.param({"p_min_pu": -1.0, "p_max_pu": 0.0}, id="p_min_pu"),
]


@pytest.mark.parametrize("offtake", _WITHDRAWS)
def test_a_negative_generator_offtake_with_a_fuel_cell_fails_closed(offtake):
    """R16: a Generator that withdraws is a sink, like an active Load."""
    n = _h2_with_negative_offtake(_dispatched(), fuel_cell=True, **offtake)
    c = classify(n)
    assert c["ambiguous_links"] == ["electrolyser"], c
    assert c["conversion_links"] == [] and c["storage_links"] == []
    n.links_t.p0 = pd.DataFrame(
        {"electrolyser": 4.0, "fuel cell": 0.0}, index=n.snapshots
    )
    kpi = renewable_fraction(n)
    assert kpi["fraction"] is None and "ambiguous conversion" in kpi["reason"], kpi


def test_a_negative_generator_offtake_given_in_time_is_a_sink():
    n = _h2_with_negative_offtake(_network(), fuel_cell=True)
    n.generators_t.p_min_pu["offtake"] = -1.0
    n.generators_t.p_max_pu["offtake"] = 0.0
    assert classify(n)["ambiguous_links"] == ["electrolyser"]


@pytest.mark.parametrize("offtake", _WITHDRAWS)
def test_a_negative_generator_offtake_without_a_fuel_cell_is_use(offtake):
    n = _h2_with_negative_offtake(_dispatched(), fuel_cell=False, **offtake)
    c = classify(n)
    assert c["conversion_links"] == ["electrolyser"], c
    assert c["ambiguous_links"] == [] and c["storage_links"] == []


@pytest.mark.parametrize(
    "gen",
    [
        pytest.param(
            {"name": "__voll_h2", "carrier": "load_shedding", "p_nom": 1e3},
            id="slack",
        ),
        pytest.param(
            {"name": "__voll_h2", "carrier": "load_shedding", "sign": -1.0},
            id="slack-withdrawing",
        ),
        pytest.param(
            {"name": "h2_import", "carrier": "H2", "p_nom": 5.0}, id="positive"
        ),
    ],
)
def test_a_slack_or_positive_generator_on_the_h2_bus_is_not_a_sink(gen):
    n = _dispatched()
    n.add("Bus", "h2", carrier="H2")
    n.add("Link", "electrolyser", bus0="ac", bus1="h2", p_nom=10.0, efficiency=0.7)
    n.add("Link", "fuel cell", bus0="h2", bus1="ac", p_nom=5.0, efficiency=0.5)
    gen = dict(gen)
    n.add("Generator", gen.pop("name"), bus="h2", **gen)
    c = classify(n)
    # Storage; or, with a positive non-renewable H2 Generator, the H2 bus is
    # a supply bus and the electrolyser exports into it. Never ambiguous.
    assert c["ambiguous_links"] == [], c


def test_a_reversible_converter_is_storage_or_ambiguous_never_use():
    """R14g: a Link that can run negative is its own path back, so a counted
    conversion Link always has p0 ≥ 0 and no gross auxiliary is needed."""
    n = _network()
    n.add("Bus", "heat", carrier="heat")
    n.add("Link", "hp", bus0="ac", bus1="heat", p_nom=5.0, p_min_pu=-1.0)
    assert classify(n)["storage_links"] == ["hp"]
    n.add("Load", "space", bus="heat", p_set=1.0)
    c = classify(n)
    assert c["ambiguous_links"] == ["hp"] and c["conversion_links"] == []


def test_an_export_link_is_not_use():
    n = _network()
    n.add("Bus", "grid", carrier="AC")
    n.add("Link", "export_ac", bus0="ac", bus1="grid", p_nom=10.0)
    n.add("Bus", "market", carrier="grid")
    n.add("Generator", "market_supply", bus="market", carrier="grid", p_nom=50.0)
    n.add("Link", "import", bus0="market", bus1="ac", p_nom=10.0)
    n.add("Link", "export_mkt", bus0="ac", bus1="market", p_nom=10.0)
    n.add("Bus", "sold", carrier="export")
    n.add("Link", "export_sold", bus0="ac", bus1="sold", p_nom=10.0)
    c = classify(n)
    assert c["conversion_links"] == [] and c["ambiguous_links"] == []


def test_a_renewable_electrolyser_lets_more_diesel_serve_the_load():
    """R14d: the draw relaxes the row, so with an electrolyser (fed by PV,
    serving an H2 offtake) the LP may run more diesel for the AC load."""

    def diesel_mwh(with_electrolyser: bool) -> float:
        n = _network()
        if with_electrolyser:
            n.add("Bus", "h2", carrier="H2")
            n.add(
                "Load",
                "offtake",
                bus="h2",
                p_set=pd.Series(PV_PU * 7.0, index=n.snapshots),
            )
            n.add(
                "Link", "electrolyser", bus0="ac", bus1="h2", p_nom=10.0, efficiency=0.7
            )
        status, _, log = _solve(n, min_renewable_fraction=0.8)
        assert status == "ok", log
        return float(n.generators_t.p["diesel"].sum())

    assert diesel_mwh(True) > diesel_mwh(False) + 1.0


def test_dumping_renewables_into_an_unused_converter_buys_allowance():
    """R14j, documented and not guarded: PV run into an electrolyser whose H2
    only fills an unbounded Store raises the fraction at the same diesel."""
    n = _dispatched()
    before = renewable_fraction(n)["fraction"]
    n.add("Bus", "h2", carrier="H2")
    n.add("Store", "dump", bus="h2", carrier="H2", e_nom_extendable=True)
    n.add("Link", "electrolyser", bus0="ac", bus1="h2", p_nom=10.0)
    assert classify(n)["conversion_links"] == ["electrolyser"]
    n.links_t.p0 = pd.DataFrame({"electrolyser": 0.0}, index=n.snapshots)
    n.links_t.p0.iloc[10, 0] = 8.0  # 8 MWh more PV, dumped
    n.generators_t.p.iloc[10, n.generators_t.p.columns.get_loc("pv")] += 8.0
    kpi = renewable_fraction(n)
    assert kpi["fraction"] > before and kpi["note"], kpi


def _templates():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "project_templates"))
    import eh_templates as T

    return T


# What each GUI site template's classification must list (R14 per template).
TEMPLATE_CONVERSION = {
    "eh_h2_hub": {"ambiguous": {"electrolyser"}},
    "eh_h2_hub_staged": {"ambiguous": {"electrolyser"}},
}


@pytest.mark.parametrize(
    "tid",
    sorted(
        [
            "eh_datacenter",
            "eh_datacenter_staged",
            "eh_h2_hub",
            "eh_h2_hub_staged",
            "eh_microgrid",
            "eh_microgrid_staged",
            "eh_ammonia_staged",
            "eh_ipp_hybrid",
        ]
    ),
)
def test_each_site_template_classifies_its_conversion_links(tid):
    T = _templates()
    n = T.BUILDERS[tid]()
    c = classify(n)
    conv, amb = set(c["conversion_links"]), set(c["ambiguous_links"])
    if tid in ("eh_h2_hub", "eh_h2_hub_staged"):
        # Electrolyser into an H2 bus with both an offtake and a fuel cell.
        assert {l for l in n.links.index if l.startswith("electrolyser")} <= amb, c
        assert conv == set(), c
    elif tid == "eh_ammonia_staged":
        # No fuel cell: the electrolysers feed H2 into Haber-Bosch, which
        # also draws electricity; both are use.
        expected = {l for l in n.links.index if l.startswith("electrolyser")}
        assert expected and expected | {"haber_bosch"} <= conv, c
        assert amb == set(), c
    else:
        assert conv == set() and amb == set(), c


def test_the_concentrator_process_links_are_use():
    import sys
    from pathlib import Path

    T = _templates()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "project_templates"))
    import eh_concentrator as C

    n = C.build_eh_concentrator_staged(T, years=C.YEARS[:1], time_base="week")
    c = classify(n)
    flag = n.links["eh_process"].astype(object).fillna(False)
    process = {l for l in n.links.index if flag[l] is True or flag[l] == 1}
    assert process and process <= set(c["conversion_links"]), c
    assert c["ambiguous_links"] == []
