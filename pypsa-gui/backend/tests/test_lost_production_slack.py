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

import io
import pathlib
import pickle
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
    assert cap["value"] == {"nh3": 150.0} and cap["key"]["nh3"] == LP.capture_key(n, "nh3", 150.0)


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


def test_the_accessor_reads_the_capture_and_marks_it_stale_after_an_edit_never_current():
    n = _toy()
    cap = {"lost_production": {"p_t": pd.DataFrame({"nh3": [0.0, 5.0, 0.0, 1.0]}, index=n.snapshots),
                               "value": {"nh3": 150.0}, "key": {"nh3": LP.capture_key(n, "nh3", 150.0)}}}
    (s,) = shortfalls(n, cap)
    assert (s.name, s.source, s.flag, s.value) == ("__lost_nh3", "capture", None, 150.0)
    n.buses.at["nh3", "lost_production_value"] = 200.0            # an edit since the solve
    (s,) = shortfalls(n, cap)
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
def test_the_capture_survives_save_and_reload_and_reads_stale_after_an_edit(reset_backend, tmp_path):
    from routers.projects import _RESULTS_STATE_SCHEMA, _safe_unpickle_results, _unwrap_results_state

    n, state, cfg = _solve("bus_value", _bus_value)
    before_lost, before_ic = _lost(n, state), _case_lines(n, state, cfg, "__lost_nh3")
    blob = pickle.dumps({"__schema__": _RESULTS_STATE_SCHEMA, "data": {"last_lost_load": state["last_lost_load"]}})
    path = tmp_path / "network.nc"
    n.export_to_netcdf(path)
    n2 = pypsa.Network(str(path))
    loaded = {"last_lost_load": _unwrap_results_state(_safe_unpickle_results(blob))["last_lost_load"]}
    assert n2.buses.at["nh3", "lost_production_value"] == pytest.approx(A.LOST_EUR_PER_MWH)
    after = _lost(n2, loaded)
    assert (after["t"], after["eur"]) == (pytest.approx(before_lost["t"], rel=1e-12),
                                          pytest.approx(before_lost["eur"], rel=1e-12))
    after_ic = _case_lines(n2, loaded, cfg, "__lost_nh3")
    assert after_ic.keys() == before_ic.keys()
    assert all(after_ic[k][0] == pytest.approx(before_ic[k][0], rel=1e-12) for k in before_ic)
    n2.buses.at["nh3", "lost_production_value"] = 2 * A.LOST_EUR_PER_MWH       # an edit: never current
    assert _lost(n2, loaded)["eur_flag"] == "lost_production_capture_stale"
