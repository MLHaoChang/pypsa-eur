"""
The solve-time lost-production slack and its capture (grid-to-process ticket 08, `services/solver/lost_production.py`).

A product bus with a `lost_production_value` and no hand-built shortfall Generator gets a transient `__lost_<bus>`
for the solve; its dispatch is captured into the solve's `last_lost_load` record before it is removed, and every
reader of the shortfall reads it through one accessor (`process_products.shortfalls`). The gatekeeper's conditions,
each pinned here:

1. one accessor, and nothing else reads the shortfall directly (a source scan);
2. the capture survives save and reload, keyed to its solve (stale after an edit, never current);
3. energy flows close with the shortfall entering as unserved product;
4. the ledger lines from the capture equal those from a hand-built slack on the same plan;
5. a hand-built slack wins, and no capture is made for its bus.
"""
from __future__ import annotations

import pathlib
import queue
import re
import sys
import threading

import numpy as np
import pandas as pd
import pypsa
import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_ammonia as A  # noqa: E402
import eh_templates as T  # noqa: E402

from services.adequacy.slack import lost_slack_name  # noqa: E402
from services.process_products import shortfalls  # noqa: E402
from services.solver import lost_production as LP  # noqa: E402

TID = "eh_ammonia_staged"


# ── the slack, unit ────────────────────────────────────────────────────────


def _toy(value=150.0, buy_cost=100.0, hand_built=False):
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2035-01-01", periods=4, freq="h"))
    n.add("Bus", "nh3", carrier="NH3", unit="MWh")
    n.buses["lost_production_value"] = value
    n.add("Load", "delivery", bus="nh3", p_set=[10.0, 20.0, 30.0, 40.0])
    n.add("Generator", "grey", bus="nh3", p_nom=100.0, marginal_cost=buy_cost)
    n.generators["process_role"] = ""
    n.generators.loc["grey", "process_role"] = "buy_in"
    if hand_built:
        n.add("Generator", "short", bus="nh3", p_nom=100.0, marginal_cost=value)
        n.generators.loc["short", "process_role"] = "shortfall"
    return n


def test_a_bus_value_adds_a_priced_bounded_slack_and_the_restore_captures_and_removes_it():
    n = _toy()
    undo, captured = [], {}
    LP.apply(n, lambda _m: None, undo, captured)
    name = lost_slack_name("nh3")
    assert n.generators.at[name, "process_role"] == "shortfall"
    assert n.generators.at[name, "marginal_cost"] == 150.0
    bound = n.get_switchable_as_dense("Generator", "p_max_pu")[name] * n.generators.at[name, "p_nom"]
    assert bound.to_numpy() == pytest.approx([10.0, 20.0, 30.0, 40.0])
    n.generators_t.p = pd.DataFrame({name: [0.0, 5.0, 0.0, 1.0]}, index=n.snapshots)
    ((_kind, restore),) = undo
    restore()
    assert name not in n.generators.index and LP.CARRIER not in n.carriers.index
    cap = captured["lost_production"]
    assert cap["p_t"]["nh3"].tolist() == [0.0, 5.0, 0.0, 1.0]
    assert cap["value"] == {"nh3": 150.0}
    LP.stamp(n, captured)                       # once restored: keyed to what the solve leaves
    assert cap["key"]["nh3"] == LP.capture_key(n, "nh3", 150.0)


def test_a_hand_built_shortfall_wins_and_no_slack_is_added():
    n = _toy(hand_built=True)
    undo, captured = [], {}
    LP.apply(n, lambda _m: None, undo, captured)
    assert lost_slack_name("nh3") not in n.generators.index and undo == [] and captured == {}


def test_a_value_at_or_below_the_buy_ins_dense_cost_is_refused():
    """The buy-in's cost is a series (its CO2): the highest snapshot decides, never the scalar."""
    n = _toy(value=150.0, buy_cost=100.0)
    n.generators_t.marginal_cost["grey"] = [100.0, 120.0, 160.0, 100.0]
    assert [b for b, _why in LP.refusals(n)] == ["nh3"]
    with pytest.raises(LP.LostProductionRefused):
        LP.apply(n, lambda _m: None, [], {})
    from services.validation_service import _check_lost_production_value

    (issue,) = _check_lost_production_value(n)
    assert (issue.severity, issue.code, issue.name) == ("error", "lost_production_value_below_buy_in", "nh3")


def test_a_bus_with_a_hand_built_shortfall_is_never_refused_for_its_value():
    """No slack is added there, so its bus value prices nothing in the LP and blocks nothing."""
    n = _toy(value=50.0, buy_cost=100.0, hand_built=True)
    assert LP.refusals(n) == []


def test_a_safety_unit_off_the_critical_bus_is_warned_and_a_string_false_is_not_critical():
    from services.validation_service import _check_safety_tier_off_critical_bus

    n = pypsa.Network()
    n.add("Bus", ["grid", "nh3"])
    n.add("Link", "unit", bus0="grid", bus1="nh3")
    n.links["criticality_tier"] = "safety"
    n.buses["eh_critical"] = ["False", "False"]
    (issue,) = _check_safety_tier_off_critical_bus(n)
    assert (issue.severity, issue.code, issue.name) == ("warning", "safety_tier_off_critical_bus", "unit")
    n.buses.at["grid", "eh_critical"] = True
    assert _check_safety_tier_off_critical_bus(n) == []
    n.links["criticality_tier"] = "process_critical"
    n.buses.at["grid", "eh_critical"] = False
    assert _check_safety_tier_off_critical_bus(n) == []


def test_the_accessor_reads_the_capture_and_marks_it_stale_after_any_input_edit_never_current():
    n = _toy()
    captured = {"lost_production": {"p_t": pd.DataFrame({"nh3": [0.0, 5.0, 0.0, 1.0]}, index=n.snapshots),
                                    "value": {"nh3": 150.0}, "key": {}}}
    LP.stamp(n, captured)
    (s,) = shortfalls(n, captured)
    assert (s.name, s.source, s.flag, s.value) == ("__lost_nh3", "capture", None, 150.0)
    for edit in (lambda m: m.buses.__setitem__("lost_production_value", 200.0),       # the value
                 lambda m: m.loads_t.p_set.__setitem__("delivery", [1.0, 2.0, 3.0, 4.0]),  # a load
                 lambda m: m.generators.__setitem__("p_nom", 90.0)):                   # an asset
        m = n.copy()
        edit(m)
        (s,) = shortfalls(m, captured)
        assert s.flag == "lost_production_capture_stale" and s.value == 150.0


def test_with_no_capture_the_shortfall_is_unavailable_never_zero():
    (s,) = shortfalls(_toy(), None)
    assert s.p is None and s.flag == "lost_production_capture_missing"


def test_nothing_but_the_accessor_reads_the_shortfall():
    """The four readers (process view, energy flows, compare economics, the ledger) take the shortfall from
    `process_products.shortfalls`. Outside the modules that must see the Generator itself (the template validator
    and the solve, which run while it exists, and the value flows' ownership), nothing finds a shortfall by role,
    by name or by its value."""
    allowed = {"services/process_products.py", "services/process_template_rules.py",
               "services/solver/process_uc.py", "services/solver/lost_production.py",
               "services/adequacy/slack.py"}
    pattern = re.compile(r"""product_role_generators\([^)]*["']shortfall|\bSHORTFALL\b|\blost_production_value\(|"""
                         r"""["']__lost_""")
    hits = []
    for path in [*(BACKEND / "services").rglob("*.py"), *(BACKEND / "routers").rglob("*.py")]:
        rel = path.relative_to(BACKEND).as_posix()
        if rel in allowed:
            continue
        for i, line in enumerate(path.read_text().splitlines(), 1):
            if pattern.search(line) and not line.lstrip().startswith("#"):
                hits.append(f"{rel}:{i}: {line.strip()}")
    assert hits == []


# ── the A template, solved both ways ───────────────────────────────────────

_SOLVES: dict[str, tuple] = {}


def _half_buy_in(n):
    for link in [a for a in n.links.index if a.startswith("electrolyser")]:
        n.remove("Link", link)
    n.generators_t.p_max_pu["__buy_nh3"] *= 0.5


def _bus_value(n, *, keep_hand_built=False):
    _half_buy_in(n)
    if not keep_hand_built:
        n.remove("Generator", "__lost_nh3")
        n.generators_t.p_max_pu.drop(columns="__lost_nh3", inplace=True, errors="ignore")
    n.buses["lost_production_value"] = float("nan")
    n.buses.at["nh3", "lost_production_value"] = A.LOST_EUR_PER_MWH


def _solve(key, edit):
    if key not in _SOLVES:
        from services.pypsa_service import PyPSAService
        from services.solver_service import SolverConfig, run_simulation
        import routers.simulation as sim_router
        from tests.conftest import install_network_into_backend
        from tests.test_eh_ammonia_staged import _ic_config

        raw = _ic_config(_half_buy_in)            # the value flows of the same assets either way
        n = T.BUILDERS[TID]()
        edit(n)
        install_network_into_backend(n)
        cfg = SolverConfig(**raw)
        sim_router._state["solver_config"] = cfg
        n = PyPSAService.get_network()
        state: dict = {}
        status, cond = run_simulation(cfg, n, PyPSAService.get_lock(), threading.Event(), queue.SimpleQueue(),
                                      state_update=lambda **k: state.update(k))
        assert status in ("ok", "optimal"), (status, cond)
        _SOLVES[key] = (n, state, cfg)
    return _SOLVES[key]


def _live(n, accessor, attr, source="lopf"):
    return getattr(getattr(n, accessor), attr)


def _case_lines(n, state, cfg, key):
    from models.finance import FinanceInputs
    from services.results.finance_case import build_finance_case

    case = build_finance_case(n, cfg, FinanceInputs.model_validate(cfg.finance), result_df=_live,
                              lost_load=state.get("last_lost_load"))
    return {(t.first_year, ln.key): (ln.amount, ln.stream, ln.esc_class) for t in case.templates
            for ln in t.lines if key in ln.key}


def _ledger_lines(n, state, cfg, asset):
    from services.results.value_flows import value_flow_ledger

    _inputs, _vf, ledger, _check = value_flow_ledger(n, cfg, result_df=_live, lost_load=state.get("last_lost_load"))
    return {(p, ln.payer, ln.payee, ln.value_stream, ln.basis): ln.amount
            for p, lines in ledger.periods.items() for ln in lines if ln.asset == asset}


def _lost(n, state):
    from services.results.process_view import compute_process_view

    (nh3,) = [p for p in compute_process_view(n, state.get("last_lost_load"))["products"] if p["bus"] == "nh3"]
    return nh3["lost"]


@pytest.mark.live_solve
def test_the_capture_gives_the_same_ledger_lines_and_lost_tonnes_as_a_hand_built_slack(reset_backend):
    n_h, s_h, cfg_h = _solve("hand_built", _half_buy_in)
    n_b, s_b, cfg_b = _solve("bus_value", _bus_value)
    assert "__lost_nh3" not in n_b.generators.index                 # transient: gone after the solve
    assert s_b["last_lost_load"]["lost_production"]["p_t"].columns.tolist() == ["nh3"]
    hand, bus = _case_lines(n_h, s_h, cfg_h, "__lost_nh3"), _case_lines(n_b, s_b, cfg_b, "__lost_nh3")
    assert hand and hand.keys() == bus.keys()
    for k in hand:
        assert bus[k][0] == pytest.approx(hand[k][0], rel=1e-6)
        assert bus[k][1:] == hand[k][1:] == ("lost_production", "fuel")
    lgh, lgb = _ledger_lines(n_h, s_h, cfg_h, "__lost_nh3"), _ledger_lines(n_b, s_b, cfg_b, "__lost_nh3")
    assert lgh and lgh.keys() == lgb.keys()                 # payer, payee (offtaker), stream and cash basis
    assert all(k[2:] == ("offtaker", "lost_production", "cash") for k in lgh)
    for k in lgh:
        assert lgb[k] == pytest.approx(lgh[k], rel=1e-6)
    lh, lb = _lost(n_h, s_h), _lost(n_b, s_b)
    assert lb["t"] == pytest.approx(lh["t"], rel=1e-6) and lb["eur"] == pytest.approx(lh["eur"], rel=1e-6)
    assert lb["eur_flag"] is None


@pytest.mark.live_solve
def test_a_hand_built_slack_wins_and_no_capture_is_made_for_its_bus(reset_backend):
    n, state, _cfg = _solve("both", lambda n: _bus_value(n, keep_hand_built=True))
    assert not (state.get("last_lost_load") or {}).get("lost_production")
    (s,) = [x for x in shortfalls(n, state.get("last_lost_load")) if x.bus == "nh3"]
    assert (s.name, s.source) == ("__lost_nh3", "generator")


@pytest.mark.live_solve
def test_energy_flows_close_with_the_captured_shortfall_as_unserved_product(reset_backend):
    from services.results.energy_flows import energy_flows

    n, state, _cfg = _solve("bus_value", _bus_value)
    lost_mwh = {}
    for y in A.YEARS:
        out = energy_flows(n, y, result_df=_live, lost_load=state.get("last_lost_load"))
        assert out["status"] == "ok", out.get("flags")
        c = out["conservation"]
        assert abs(c["residual_mwh"]) <= 1e-6 * max(c["generation_mwh"], c["destinations_mwh"], 1.0)
        lost_mwh[y] = out["unserved_product_mwh"].get("nh3_delivery", 0.0)
    cap = state["last_lost_load"]["lost_production"]["p_t"]["nh3"]
    wg = n.snapshot_weightings.generators
    for y in A.YEARS:
        assert lost_mwh[y] == pytest.approx(float((cap.loc[y] * wg.loc[y]).sum()), rel=1e-9)
        assert lost_mwh[y] > 0


@pytest.mark.live_solve
def test_the_capture_survives_a_real_project_save_and_load_and_reads_stale_after_an_edit(
        client, install_network, api_project):
    """Solve through the app, save the project, load another one and this one back: the restored record gives the
    same lost tonnes, EUR and IC line, keyed current; an edit after that reads it stale."""
    import time

    import routers.simulation as sim_router
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig
    from tests.test_eh_ammonia_staged import _ic_config

    other = api_project("lp-other")
    n = T.BUILDERS[TID]()
    _bus_value(n)
    install_network(n)
    cfg = SolverConfig(**_ic_config(_half_buy_in))
    sim_router._state["solver_config"] = cfg
    name = "lp-capture"
    assert client.post(f"/api/projects/{name}", params={"force": True, "rebind": True}).status_code == 200
    assert client.post("/api/simulation/run").status_code == 200
    deadline, st = time.monotonic() + 1800, {}
    while time.monotonic() < deadline:
        st = client.get("/api/simulation/status").json()
        if st.get("status") not in ("running", "queued", "starting", None):
            break
        time.sleep(1.0)
    assert st.get("status") == "completed", st

    def lost():
        body = client.get("/api/results/process_view").json()
        return next(p for p in body["products"] if p["bus"] == "nh3")["lost"]

    def ic():
        return _case_lines(PyPSAService.get_network(), sim_router._state, sim_router._state["solver_config"],
                           "__lost_nh3")

    before_lost, before_ic = lost(), ic()
    assert before_lost["t"] > 0 and before_lost["eur_flag"] is None and before_ic
    assert client.post(f"/api/projects/{name}").status_code == 200
    assert client.get(f"/api/projects/{other}").status_code == 200
    assert client.get(f"/api/projects/{name}").status_code == 200
    after_lost, after_ic = lost(), ic()
    assert after_lost["eur_flag"] is None                     # keyed current after the reload
    assert after_lost["t"] == pytest.approx(before_lost["t"], rel=1e-12)
    assert after_lost["eur"] == pytest.approx(before_lost["eur"], rel=1e-12)
    assert after_ic.keys() == before_ic.keys()
    assert all(after_ic[k][0] == pytest.approx(before_ic[k][0], rel=1e-12) for k in before_ic)
    PyPSAService.get_network().loads_t.p_set.iloc[0, 0] += 1.0          # an edit: never current
    assert lost()["eur_flag"] == "lost_production_capture_stale"
