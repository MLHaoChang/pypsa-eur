"""
Solve-status and MIP-gap guard (assistant-assurance 10, S2; GK B G2a–G2f).

The applied gap is pinned against solver_service's real solve path by spying on
the kwargs PyPSA's optimize receives, never by re-reading the classifier.
"""

from __future__ import annotations

import math
import queue
import threading

import pandas as pd
import pypsa
import pytest

from harness import solve_validity as sv


def _lp():
    n = pypsa.Network()
    n.set_snapshots(pd.RangeIndex(3))
    n.add("Bus", ["a", "b"])
    n.add("Load", "d", bus="a", p_set=[5.0, 8.0, 3.0])
    n.add("Generator", "g1", bus="a", p_nom=10, marginal_cost=10)
    n.add("Generator", "g2", bus="a", p_nom=10, marginal_cost=30)
    return n


def _cfg(**kw):
    base = {"solver_name": "highs", "solver_options": {}, "mip_gap": 0.02}
    base.update(kw)
    return base


# ── G2b: MIP-ness from the network, per component ────────────────────────────


def test_a_plain_lp_is_not_a_mip():
    assert sv.is_mip(_lp()) is False
    assert sv.classify(_lp(), _cfg()) == {
        "kind": "lp",
        "gap": 0.0,
        "optimal": True,
        "disclosure": None,
    }


def test_a_committable_generator_makes_a_mip():
    n = _lp()
    n.generators.loc["g1", "committable"] = True
    assert sv.is_mip(n) is True


def test_a_committable_link_makes_a_mip():
    n = _lp()
    n.add("Link", "k", bus0="a", bus1="b", p_nom=5, committable=True)
    assert sv.is_mip(n) is True


@pytest.mark.parametrize(
    "component, attr, kw",
    [
        ("Generator", "p_nom", {"bus": "a"}),
        ("Link", "p_nom", {"bus0": "a", "bus1": "b"}),
        ("StorageUnit", "p_nom", {"bus": "a"}),
        ("Store", "e_nom", {"bus": "a"}),
        ("Line", "s_nom", {"bus0": "a", "bus1": "b", "x": 0.1}),
        ("Transformer", "s_nom", {"bus0": "a", "bus1": "b", "x": 0.1}),
    ],
)
def test_an_extendable_modular_component_makes_a_mip_and_a_fixed_one_does_not(
    component, attr, kw
):
    fixed = _lp()
    fixed.add(component, "m", **kw, **{f"{attr}_mod": 5.0, f"{attr}_extendable": False})
    assert sv.is_mip(fixed) is False
    ext = _lp()
    ext.add(component, "m", **kw, **{f"{attr}_mod": 5.0, f"{attr}_extendable": True})
    assert sv.is_mip(ext) is True
    no_mod = _lp()
    no_mod.add(component, "m", **kw, **{f"{attr}_mod": 0.0, f"{attr}_extendable": True})
    assert sv.is_mip(no_mod) is False


# ── G2f: stochastic from the network ─────────────────────────────────────────


def test_a_scenario_network_is_stochastic_and_unsupported():
    n = _lp()
    n.set_scenarios({"x": 0.5, "y": 0.5})
    cls = sv.classify(n, _cfg())
    assert cls["kind"] == "stochastic" and cls["optimal"] is False
    assert sv.price_limit(cls) == sv.STOCHASTIC
    assert (
        sv.delta_limit(1.0, cls, 10.0, sv.classify(_lp(), _cfg()), 10.0)
        == sv.STOCHASTIC
    )


# ── G2a: the gap solver_service applied ──────────────────────────────────────


def _committable():
    n = _lp()
    n.generators.loc["g1", "committable"] = True
    return n


def _modular():
    n = _lp()
    n.add(
        "Generator",
        "m",
        bus="a",
        p_nom_extendable=True,
        p_nom_mod=5.0,
        p_nom_max=20.0,
        capital_cost=1.0,
        marginal_cost=5.0,
    )
    return n


@pytest.mark.parametrize(
    "net, cfg, expected",
    [
        (_committable, _cfg(), 0.02),
        (
            _committable,
            _cfg(solver_options={"mip_rel_gap": 0.005}),
            0.005,
        ),  # a user key wins
        (_committable, _cfg(solver_name="gurobi"), 0.02),
        (_committable, _cfg(solver_name="unknownsolver"), None),
        (_committable, _cfg(mip_gap=None), None),
        (_committable, _cfg(mip_gap=float("nan")), None),
        (_committable, _cfg(mip_gap=True), 1.0),  # solver_service passes float(True)
        (_committable, _cfg(mip_gap=-0.1), None),
        (_committable, _cfg(solver_options=None), 0.02),
        (_committable, _cfg(solver_options=[1]), None),
        (_committable, None, None),
        (
            _modular,
            _cfg(),
            None,
        ),  # solver_service passes no gap without a committable unit
        (_modular, _cfg(solver_options={"mip_rel_gap": 0.001}), 0.001),
    ],
)
def test_the_applied_gap(net, cfg, expected):
    assert sv.applied_gap(net(), cfg) == expected


def test_a_mip_with_no_recorded_gap_is_not_optimal_and_says_so():
    cls = sv.classify(_modular(), _cfg())
    assert cls == {
        "kind": "mip",
        "gap": None,
        "optimal": False,
        "disclosure": "Integer (MIP) solve; the relative optimality gap was not recorded.",
    }


def test_the_disclosure_names_the_gap():
    assert sv.classify(_committable(), _cfg())["disclosure"] == (
        "Integer (MIP) solve to a relative optimality gap of 0.02; "
        "costs may exceed the optimum by up to that share."
    )


def test_a_gap_at_the_tolerance_is_optimal_and_one_ulp_above_is_not():
    at = sv.classify(_committable(), _cfg(mip_gap=sv.SOLVE_GAP_TOLERANCE))
    above = sv.classify(
        _committable(), _cfg(mip_gap=math.nextafter(sv.SOLVE_GAP_TOLERANCE, 1.0))
    )
    assert at["optimal"] is True and above["optimal"] is False
    assert sv.SOLVE_GAP_TOLERANCE == 1e-6


# ── G2c: the band covers both runs ───────────────────────────────────────────

LP = {"kind": "lp", "gap": 0.0, "optimal": True, "disclosure": None}
MIP1 = {"kind": "mip", "gap": 0.01, "optimal": False, "disclosure": "x"}
MIP2 = {"kind": "mip", "gap": 0.02, "optimal": False, "disclosure": "x"}
TIGHT = {"kind": "mip", "gap": 1e-6, "optimal": True, "disclosure": "x"}
UNREC = {"kind": "mip", "gap": None, "optimal": False, "disclosure": "x"}


def test_two_lps_have_no_band():
    assert sv.band(LP, 1000.0, LP, 900.0) == 0.0
    assert sv.delta_limit(1e-12, LP, 1000.0, LP, 900.0) is None


def test_the_band_adds_both_gaps_on_absolute_objectives():
    # 0.01·|-1000| + 0.02·|500| = 10 + 10 = 20
    assert sv.band(MIP1, -1000.0, MIP2, 500.0) == 20.0
    # an LP side counts 0; a gap within tolerance counts 0
    assert sv.band(MIP1, 1000.0, LP, 1e9) == 10.0
    assert sv.band(TIGHT, 1e9, LP, 1e9) == 0.0


def test_a_delta_at_the_band_is_limited_and_one_ulp_above_is_not():
    assert sv.delta_limit(10.0, MIP1, 1000.0, LP, 0.0) == sv.DELTA_IN_GAP
    assert sv.delta_limit(-10.0, MIP1, 1000.0, LP, 0.0) == sv.DELTA_IN_GAP
    assert sv.delta_limit(math.nextafter(10.0, math.inf), MIP1, 1000.0, LP, 0.0) is None


def test_an_unrecorded_gap_or_unknown_objective_establishes_no_sign():
    assert sv.delta_limit(1e6, UNREC, 1000.0, LP, 0.0) == sv.GAP_NOT_RECORDED
    assert sv.delta_limit(1e6, MIP1, float("nan"), LP, 0.0) == sv.GAP_NOT_RECORDED
    assert sv.delta_limit(1e6, MIP1, None, LP, 0.0) == sv.GAP_NOT_RECORDED
    assert sv.delta_limit(1e6, MIP1, True, LP, 0.0) == sv.GAP_NOT_RECORDED


def test_prices_on_a_mip_are_not_marginal_and_on_an_lp_are_untouched():
    assert sv.price_limit(LP) is None
    assert sv.price_limit(MIP1) == sv.DUALS_NOT_MARGINAL
    assert sv.price_limit(TIGHT) == sv.DUALS_NOT_MARGINAL


# ── Pinned against the real solve path (G2a, G2d) ────────────────────────────


def _solve(n, cfg_kw, monkeypatch):
    from routers import simulation as sim_router
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig, run_simulation
    from tests.conftest import install_network_into_backend

    calls = []
    acc = pypsa.optimization.OptimizationAccessor
    real = acc.__call__

    def spy(self, *a, **k):
        calls.append(k)
        return real(self, *a, **k)

    monkeypatch.setattr(acc, "__call__", spy)
    install_network_into_backend(n)
    cfg = SolverConfig(**cfg_kw)
    sim_router._state["solver_config"] = cfg
    live = PyPSAService.get_network()
    log = queue.SimpleQueue()
    status, cond = run_simulation(
        cfg,
        live,
        PyPSAService.get_lock(),
        threading.Event(),
        log,
        state_update=lambda **k: None,
    )
    lines = []
    while not log.empty():
        lines.append(str(log.get()))
    assert (status, cond) == ("ok", "optimal"), (status, cond, lines[-8:])
    assert len(calls) == 1
    return live, cfg, calls[0]


@pytest.mark.live_solve
@pytest.mark.parametrize(
    "net, passed", [(_committable, 0.02), (_modular, None), (_lp, None)]
)
def test_the_applied_gap_is_what_the_solver_received(
    net, passed, reset_backend, monkeypatch
):
    live, cfg, kwargs = _solve(
        net(), {"solver_name": "highs", "mip_gap": 0.02}, monkeypatch
    )
    received = (kwargs.get("solver_options") or {}).get("mip_rel_gap")
    assert received == passed
    assert sv.applied_gap(live, cfg) == received


@pytest.mark.live_solve
def test_a_mip_solve_on_master_writes_zero_prices_not_duals(reset_backend, monkeypatch):
    # HiGHS returns no duals for a MIP; PyPSA fills marginal_price with 0.0.
    live, cfg, _ = _solve(
        _committable(), {"solver_name": "highs", "mip_gap": 0.02}, monkeypatch
    )
    prices = live.buses_t.marginal_price
    assert not prices.empty and (prices.to_numpy() == 0.0).all()
    assert sv.price_limit(sv.classify(live, cfg)) == sv.DUALS_NOT_MARGINAL


# ── G2c: the band's objective is in user units ──────────────────────────────


@pytest.mark.live_solve
@pytest.mark.parametrize(
    "scale_kw",
    [
        {"user_objective_scale": 1.0},
        {"user_objective_scale": 1e-3},
        {"auto_objective_scale": True},
    ],
)
def test_the_captured_objective_is_in_user_units_under_any_scale(
    scale_kw, reset_backend, monkeypatch
):
    # Hand-worked: g1 (10 EUR/MWh) serves 5 + 8 + 3 = 16 MWh -> 160 EUR.
    from services.pypsa_service import PyPSAService

    live, _, _ = _solve(_lp(), {"solver_name": "highs", **scale_kw}, monkeypatch)
    ctx = PyPSAService.build_context()
    ctx.network = live
    captured = sv.capture(ctx)
    assert captured["kind"] == "lp"
    assert captured["objective"] == pytest.approx(160.0, rel=1e-9)


# ── Guards pinned one by one (G8) ────────────────────────────────────────────


@pytest.mark.parametrize(
    "cfg, expected",
    [
        (_cfg(solver_name=None), None),
        (_cfg(solver_options={"mip_rel_gap": True}), None),
        (_cfg(solver_options={"mip_rel_gap": "0.01"}), None),
        (_cfg(solver_options={"mip_rel_gap": float("inf")}), None),
        (_cfg(solver_options={"mip_rel_gap": 0.0}), 0.0),
    ],
)
def test_the_applied_gap_value_guards(cfg, expected):
    assert sv.applied_gap(_modular(), cfg) == expected


def test_the_applied_gap_is_a_float():
    got = sv.applied_gap(_modular(), _cfg(solver_options={"mip_rel_gap": 1}))
    assert got == 1.0 and type(got) is float


def test_a_component_table_without_mod_or_extendable_columns_is_not_integer():
    n = _lp()
    n.generators.loc["g1", "p_nom_extendable"] = True
    n.generators.loc["g1", "p_nom_mod"] = 5.0
    assert sv.is_mip(n) is True
    for drop in ("p_nom_mod", "p_nom_extendable"):
        m = _lp()
        m.generators.loc["g1", "p_nom_extendable"] = True
        m.generators.loc["g1", "p_nom_mod"] = 5.0
        m.components["Generator"].static.drop(columns=[drop], inplace=True)
        assert sv._modular(m) is False


def test_an_lp_side_needs_no_objective_in_the_band():
    assert sv.band(LP, None, MIP1, 1000.0) == 10.0


def test_a_zero_delta_between_two_lps_is_answered():
    assert sv.delta_limit(0.0, LP, 1.0, LP, 1.0) is None


class _Net:
    def __init__(self, objective):
        self.objective = objective


@pytest.mark.parametrize(
    "objective, expected",
    [
        (True, None),
        ("160", None),
        (float("inf"), None),
        (float("nan"), None),
        (None, None),
        (160, 160.0),
    ],
)
def test_the_objective_guards(objective, expected):
    got = sv._objective(_Net(objective))
    assert got == expected and (expected is None or type(got) is float)


def test_a_raw_delta_needs_both_records():
    lp = {**LP, "objective": 1.0}
    assert sv.raw_delta_limit(5.0, None, lp) == sv.GAP_NOT_RECORDED
    assert sv.raw_delta_limit(5.0, lp, None) == sv.GAP_NOT_RECORDED
    assert sv.raw_delta_limit(5.0, lp, lp) is None


@pytest.mark.parametrize(
    "raw, expected",
    [
        ({"solve": {"disclosure": "Gap 0.01."}}, ["Gap 0.01."]),
        ({"solve": {"disclosure": ""}}, []),
        ({"solve": {"disclosure": 5}}, []),
        ({"solve": {"disclosure": None}}, []),
        ({"solve": None}, []),
        ({}, []),
        (None, []),
    ],
)
def test_notes(raw, expected):
    assert sv.notes(raw) == expected
