"""The fixed-size run (slice F1, modular-cards §4: Capacity › sizing off).

`SolverConfig.fixed_sizes` dispatches on fixed sizes through
`adequacy.sweep.freeze_capacities` (extendable stays on, each size clamped).
The source is the last sizing run's `*_nom_opt` ("capacity_run") or the
configured nameplate ("nameplate"). A sizing run records its sizes in
`n.meta["sizing_record"]`; "capacity_run" refuses when the network's sizes no
longer match that record (gatekeeper, 2026-10-21: a nameplate run, a results
load or another fixed run may have overwritten them).
"""
from __future__ import annotations

import queue
import threading

import numpy as np
import pandas as pd
import pypsa
import pytest

import routers.simulation as sim_router
from services.pypsa_service import PyPSAService

H = 24
EPS = 1.001e-6          # the freeze's +1e-6 MW headroom, plus float noise


def _plan_net(*, periods: tuple[int, ...] = ()) -> pypsa.Network:
    """A site that sizes PV, a battery and a gas peaker against a load."""
    n = pypsa.Network()
    idx = pd.date_range("2030-01-01", periods=H, freq="h")
    if periods:
        n.set_snapshots(pd.MultiIndex.from_product([list(periods), idx]))
        n.investment_periods = list(periods)
    else:
        n.set_snapshots(idx)
    n.add("Carrier", ["AC", "solar", "gas", "battery"])
    n.add("Bus", "site", carrier="AC")
    k = np.arange(len(n.snapshots)) % H
    growth = (np.asarray(n.snapshots.get_level_values(0)) == periods[-1]) + 1.0 \
        if periods else 1.0                               # the last period's load doubles
    n.add("Load", "load", bus="site",
          p_set=pd.Series((10.0 + 5.0 * (k >= 17)) * growth, index=n.snapshots))
    pv = pd.Series(np.clip(np.sin(np.pi * (k - 6) / 12), 0, None), index=n.snapshots)
    builds = periods or (None,)
    for i, year in enumerate(builds):
        sfx = "" if year is None else f"_{year}"
        kw = {} if year is None else {"build_year": year, "lifetime": 40}
        n.add("Generator", f"pv{sfx}", bus="site", carrier="solar", p_nom_extendable=True,
              capital_cost=40.0 + 10 * i, p_max_pu=pv, **kw)
        n.add("Generator", f"gas{sfx}", bus="site", carrier="gas", p_nom_extendable=True,
              capital_cost=30.0, marginal_cost=90.0, **kw)
        n.add("StorageUnit", f"bess{sfx}", bus="site", carrier="battery", p_nom_extendable=True,
              capital_cost=25.0, max_hours=4.0, cyclic_state_of_charge=True, **kw)
    n.generators["p_nom"] = 5.0                           # the nameplate
    n.generators.loc[n.generators.carrier == "gas", "p_nom"] = 40.0
    n.storage_units["p_nom"] = 2.0
    n.generators["p_nom_max"] = 100.0
    n.storage_units["p_nom_max"] = 100.0
    return n


def _install(n):
    from tests.conftest import install_network_into_backend

    install_network_into_backend(n)
    return PyPSAService.get_network()


def _solve(n=None, **cfg_kw):
    """`run_simulation` on the installed network; returns (status, condition,
    state, log lines)."""
    from services.solver_service import SolverConfig, run_simulation

    n = n if n is not None else PyPSAService.get_network()
    cfg = SolverConfig(**cfg_kw)
    sim_router._state["solver_config"] = cfg
    log: queue.SimpleQueue = queue.SimpleQueue()
    state: dict = {}
    status, cond = run_simulation(cfg, n, PyPSAService.get_lock(), threading.Event(), log,
                                  state_update=lambda **k: state.update(k))
    lines = []
    while not log.empty():
        lines.append(str(log.get()))
    return status, cond, state, lines


def _errors(**cfg_kw) -> set[str]:
    from services.solver_service import SolverConfig
    from services.validation_service import validate_for_run

    return {i.code for i in validate_for_run(PyPSAService.get_network(), SolverConfig(**cfg_kw))
            if i.severity == "error"}


def _warnings(**cfg_kw) -> set[str]:
    from services.solver_service import SolverConfig
    from services.validation_service import validate_for_run

    return {i.code for i in validate_for_run(PyPSAService.get_network(), SolverConfig(**cfg_kw))
            if i.severity == "warning"}


def _opt(n) -> dict[str, float]:
    return {**n.generators.p_nom_opt.to_dict(), **n.storage_units.p_nom_opt.to_dict()}


def _bounds(n) -> dict:
    return {a: getattr(n, a)[["p_nom_min", "p_nom_max", "p_nom_extendable"]].copy()
            for a in ("generators", "storage_units")}


def _same_bounds(a, b):
    for k in a:
        pd.testing.assert_frame_equal(a[k], b[k])


# ── the source must still be the sizing run's ───────────────────────────────

@pytest.mark.live_solve
def test_capacity_run_after_a_nameplate_run_is_refused(reset_backend):
    """After a "nameplate" run `*_nom_opt` holds the nameplate; "capacity_run"
    must not silently mean nameplate."""
    n = _install(_plan_net())
    assert _solve()[0] in ("ok", "optimal")
    assert _solve(fixed_sizes="nameplate")[0] in ("ok", "optimal")
    assert "fixed_sizes_source_changed" in _errors(fixed_sizes="capacity_run")
    status, cond, _, _ = _solve(n, fixed_sizes="capacity_run")
    assert (status, cond) == ("error", "validation_failed")


@pytest.mark.live_solve
def test_a_sizing_run_records_its_sizes_and_a_new_sizing_run_restores_the_source(reset_backend):
    n = _install(_plan_net())
    assert "fixed_sizes_no_capacity_run" in _errors(fixed_sizes="capacity_run")
    _solve()
    rec = n.meta["sizing_record"]
    assert set(rec) >= {"revision", "solved_at", "sha256", "sizes"}
    _solve(fixed_sizes="nameplate")
    assert n.meta["sizing_record"] == rec                    # a fixed run keeps it
    _solve()
    assert "fixed_sizes_source_changed" not in _errors(fixed_sizes="capacity_run")


@pytest.mark.live_solve
def test_capacity_run_reproduces_the_plans_sizes_after_a_cost_edit(reset_backend):
    n = _install(_plan_net())
    _solve()
    plan = _opt(n)
    before = _bounds(n)
    with PyPSAService.get_lock():
        n.generators.loc["gas", "marginal_cost"] = 20.0      # a cost edit keeps the source
    status, _, state, lines = _solve(fixed_sizes="capacity_run")
    assert status in ("ok", "optimal")
    for k, v in plan.items():
        assert _opt(n)[k] == pytest.approx(v, abs=EPS), k
    _same_bounds(before, _bounds(n))
    sizing = state["sizing"]
    assert sizing["mode"] == "fixed" and sizing["source"] == "capacity_run"
    assert sizing["source_revision"] == n.meta["sizing_record"]["revision"]
    assert sizing["source_solved_at"] == n.meta["sizing_record"]["solved_at"]
    # A second fixed run on the same source is still allowed.
    assert "fixed_sizes_source_changed" not in _errors(fixed_sizes="capacity_run")


@pytest.mark.live_solve
def test_a_sizing_run_reports_sizing_on(reset_backend):
    _install(_plan_net())
    state = _solve()[2]
    assert state["sizing"] == {"mode": "optimised"}


@pytest.mark.live_solve
def test_nameplate_pins_at_p_nom_and_restores_the_bounds(reset_backend):
    n = _install(_plan_net())
    before = _bounds(n)
    status, _, state, _ = _solve(fixed_sizes="nameplate")
    assert status in ("ok", "optimal")
    assert _opt(n) == pytest.approx({**n.generators.p_nom.to_dict(),
                                     **n.storage_units.p_nom.to_dict()}, abs=EPS)
    _same_bounds(before, _bounds(n))
    assert state["sizing"] == {"mode": "fixed", "source": "nameplate"}
    assert "sizing_record" not in n.meta                     # not a sizing run


@pytest.mark.live_solve
@pytest.mark.parametrize("how", ["abort", "error"])
def test_a_failed_fixed_run_keeps_the_previous_sizes_and_bounds(reset_backend, monkeypatch, how):
    import services.solver_service as SS
    from services.solver.runtime import SolveAborted

    n = _install(_plan_net())
    _solve()
    plan, before = _opt(n), _bounds(n)
    real = SS._check_stop

    def stop(stop_event, phase, where):
        if where == "before LP solve":
            if how == "abort":
                raise SolveAborted(where)
            raise RuntimeError("boom")
        return real(stop_event, phase, where)

    monkeypatch.setattr(SS, "_check_stop", stop)
    status, _, _, _ = _solve(fixed_sizes="nameplate")
    assert status == ("aborted" if how == "abort" else "error")
    assert _opt(n) == pytest.approx(plan, abs=1e-9)
    _same_bounds(before, _bounds(n))
    assert not hasattr(n, "_fixed_design") and not hasattr(n, "_ic_operational")


@pytest.mark.parametrize("exc", ["binding", "other"])
def test_a_raise_at_the_commercial_binding_leaves_no_freeze_behind(reset_backend, monkeypatch,
                                                                   exc):
    """Every exit between the freeze and the guarded restore undoes it."""
    import services.solver_service as SS
    from services.commercial.connection import CommercialBindingError

    n = _install(_plan_net())
    before = _bounds(n)

    def boom(*_a, **_k):
        raise (CommercialBindingError("refused") if exc == "binding" else RuntimeError("boom"))

    monkeypatch.setattr(SS, "apply_commercial_for_solve", boom)
    status, cond, _, _ = _solve(fixed_sizes="nameplate")
    assert status == "error"
    _same_bounds(before, _bounds(n))
    assert not hasattr(n, "_fixed_design")


# ── refusals ────────────────────────────────────────────────────────────────

def test_capacity_run_on_an_unsolved_network_is_refused(reset_backend):
    _install(_plan_net())
    assert "fixed_sizes_no_capacity_run" in _errors(fixed_sizes="capacity_run")
    assert "fixed_sizes_no_capacity_run" not in _errors(fixed_sizes="nameplate")


@pytest.mark.parametrize("cfg,code", [
    ({"solve_strategy": "myopic", "multi_investment_periods": True}, "fixed_sizes_with_myopic"),
    ({"solve_strategy": "rolling"}, "fixed_sizes_with_rolling"),
    ({"mode": "pf"}, "fixed_sizes_pf_mode"),
])
def test_strategies_that_freeze_on_their_own_are_refused(reset_backend, cfg, code):
    _install(_plan_net())
    assert code in _errors(fixed_sizes="nameplate", **cfg)
    assert code not in _errors(**cfg)


def test_an_empty_nameplate_is_refused_and_a_zero_candidate_is_named(reset_backend):
    n = _install(_plan_net())
    with PyPSAService.get_lock():
        n.generators.loc["gas", "p_nom"] = 0.0
    assert "fixed_sizes_zero_nameplate" in _warnings(fixed_sizes="nameplate")
    with PyPSAService.get_lock():
        n.generators["p_nom"] = 0.0
        n.storage_units["p_nom"] = 0.0
    assert "fixed_sizes_nameplate_empty" in _errors(fixed_sizes="nameplate")


@pytest.mark.live_solve
def test_a_topology_edit_drops_the_source(reset_backend):
    """Any component added or removed (extendable or not) since the sizing run."""
    from services.solver.fixed_sizes import source_problem

    n = _install(_plan_net())
    _solve()
    with PyPSAService.get_lock():
        n.add("Bus", "spare", carrier="AC")              # no dispatch column, not extendable
    assert source_problem(n)[0] == "fixed_sizes_no_capacity_run"
    with PyPSAService.get_lock():
        n.remove("Bus", "spare")
    assert source_problem(n) is None                     # the same network again
    with PyPSAService.get_lock():
        n.add("Generator", "wind", bus="site", carrier="solar", p_nom_extendable=True,
              capital_cost=50.0, p_nom_max=10.0)
    assert "fixed_sizes_no_capacity_run" in _errors(fixed_sizes="capacity_run")


@pytest.mark.live_solve
def test_the_record_round_trips_through_netcdf(reset_backend, tmp_path):
    import json

    n = _install(_plan_net())
    _solve()
    json.dumps(n.meta["sizing_record"], allow_nan=False)        # strict JSON
    path = str(tmp_path / "n.nc")
    n.export_to_netcdf(path)
    m = pypsa.Network(path)
    assert m.meta["sizing_record"] == n.meta["sizing_record"]


def test_the_new_codes_file_to_scenarios():
    from services import flow_sections as F

    for code in ("fixed_sizes_no_capacity_run", "fixed_sizes_source_changed",
                 "fixed_sizes_with_myopic", "fixed_sizes_with_rolling", "fixed_sizes_pf_mode",
                 "fixed_sizes_nameplate_empty", "fixed_sizes_zero_nameplate",
                 "fixed_sizes_unknown"):
        assert F.file_issue(code, "", "", frozenset()) == "scenarios", code


def test_an_unknown_source_is_refused_at_the_api_and_by_the_validator(reset_backend):
    from pydantic import ValidationError

    from models.schemas import SolverConfigSchema

    with pytest.raises(ValidationError):
        SolverConfigSchema(fixed_sizes="whatever")
    _install(_plan_net())
    assert "fixed_sizes_unknown" in _errors(fixed_sizes="whatever")


# ── multi-period and N-1 ────────────────────────────────────────────────────

@pytest.mark.live_solve
def test_two_periods_two_vintages_reproduce_the_plan(reset_backend):
    n = _install(_plan_net(periods=(2030, 2040)))
    assert _solve(multi_investment_periods=True)[0] in ("ok", "optimal")
    plan = _opt(n)
    assert any(v > 1e-3 for k, v in plan.items() if k.endswith("2040"))
    status, _, _, _ = _solve(multi_investment_periods=True, fixed_sizes="capacity_run")
    assert status in ("ok", "optimal")
    for k, v in plan.items():
        assert _opt(n)[k] == pytest.approx(v, abs=EPS), k


def _line_plan() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=6, freq="h"))
    n.add("Carrier", ["AC", "gas"])
    for b in ("a", "b", "c"):
        n.add("Bus", b, carrier="AC", v_nom=20.0)
    n.add("Line", "ab", bus0="a", bus1="b", x=0.1, s_nom=30.0)
    n.add("Line", "bc", bus0="b", bus1="c", x=0.1, s_nom=30.0)
    n.add("Line", "ca", bus0="c", bus1="a", x=0.1, s_nom=30.0)
    n.add("Generator", "g_a", bus="a", carrier="gas", p_nom_extendable=True,
          capital_cost=10.0, marginal_cost=10.0)
    n.add("Generator", "g_c", bus="c", carrier="gas", p_nom_extendable=True,
          capital_cost=20.0, marginal_cost=30.0)
    n.add("Load", "l", bus="b", p_set=25.0)
    n.generators["p_nom_max"] = 100.0
    return n


@pytest.mark.live_solve
def test_sclopf_on_fixed_sizes_reproduces_the_secure_plan(reset_backend):
    n = _install(_line_plan())
    cfg = {"sclopf": True, "sclopf_include_all_lines": True}
    assert _solve(**cfg)[0] in ("ok", "optimal")
    plan = n.generators.p_nom_opt.to_dict()
    status, _, _, _ = _solve(fixed_sizes="capacity_run", **cfg)
    assert status in ("ok", "optimal")
    assert n.generators.p_nom_opt.to_dict() == pytest.approx(plan, abs=EPS)


# ── the operational flag split (gatekeeper (a)) ─────────────────────────────

@pytest.mark.live_solve
def test_a_fixed_run_performs_the_binary_check_and_leaves_the_plan(reset_backend):
    from tests.test_process_uc_solve_path import _process_net

    n = _install(_process_net(load=1.0, store=False))
    n.links.loc["hb", ["p_nom_extendable", "capital_cost", "p_nom_max"]] = [True, 1.0, 20.0]
    _solve()
    plan = n.links.p_nom_opt.to_dict()
    status, _, state, _ = _solve(fixed_sizes="capacity_run")
    assert status in ("ok", "optimal")
    assert state["last_process_uc_check"] is not None
    assert n.links.p_nom_opt.to_dict() == pytest.approx(plan, abs=EPS)


def test_the_sweep_stays_operational_and_f1_carries_the_fixed_design():
    from services.adequacy.sweep import FIXED_DESIGN_ATTR, freeze_capacities

    n = _plan_net()
    undo = freeze_capacities(n)
    assert n._ic_operational is True and not hasattr(n, FIXED_DESIGN_ATTR)
    undo()
    assert not hasattr(n, "_ic_operational")
    undo = freeze_capacities(n, source="nom", transient=False)
    assert not hasattr(n, "_ic_operational")
    assert getattr(n, FIXED_DESIGN_ATTR)["sizes"]["generators/gas"] == 40.0
    undo()
    assert not hasattr(n, FIXED_DESIGN_ATTR)


@pytest.mark.live_solve
def test_repeated_capacity_runs_freeze_at_the_record_and_never_drift(reset_backend):
    """Gatekeeper (2): each run freezes at the recorded vector, so the +1e-6
    a run may build never accumulates into a refusal."""
    n = _install(_plan_net())
    _solve()
    rec = dict(n.meta["sizing_record"]["sizes"])
    for _ in range(3):
        assert _solve(fixed_sizes="capacity_run")[0] in ("ok", "optimal")
    assert "fixed_sizes_source_changed" not in _errors(fixed_sizes="capacity_run")
    with PyPSAService.get_lock():
        n.generators.loc["gas", "p_nom_min"] = 0.0      # the bounds are the user's again
    from services.adequacy.sweep import freeze_capacities

    undo = freeze_capacities(n, sizes=n.meta["sizing_record"]["sizes"], transient=False)
    assert n.generators.at["gas", "p_nom_min"] == pytest.approx(rec["generators/gas"], abs=0)
    undo()


def test_the_island_uc_runs_on_a_fixed_design_and_not_on_an_operational_solve():
    from services.adequacy.sweep import FIXED_DESIGN_ATTR
    from services.solver.island import dispatch_only, linearised_uc
    from tests.test_island_study import _committed_genset, _hub, _uc, _units

    island = _uc(_units(gensets={"g": _committed_genset()}))
    n = _hub(load=10.0, gensets=[("g", 6.0)])
    setattr(n, FIXED_DESIGN_ATTR, {"source": "opt", "sizes": {}})
    assert dispatch_only(n, island) == "dispatch-only: sizing off (fixed sizes)"
    with linearised_uc(n, island) as kw:
        assert kw == {"linearized_unit_commitment": True}
    n = _hub(load=10.0, gensets=[("g", 6.0)])
    n._ic_operational = True
    with linearised_uc(n, island) as kw:
        assert kw == {}


# ── the commercial records (gatekeeper, option A) ───────────────────────────

def _edge():
    from tests.fixtures.investment_case.edge_15min import build_edge_15min

    return _install(build_edge_15min())


def _fee_commercial():
    from tests.test_connection_agreement import _agreement, _conn, _fee

    return _conn(_agreement(fee=_fee(500.0)))


@pytest.mark.live_solve
def test_an_f1_run_commits_its_records_and_keeps_the_designs_fee(reset_backend):
    from services.commercial import connection as C

    n = _edge()
    com = _fee_commercial()
    assert _solve(commercial=com)[0] in ("ok", "optimal")
    fee = dict(n.meta[C.META_FEE])
    assert fee["size_mw"] == pytest.approx(float(n.links.at["import", "p_nom_opt"]))
    n.meta.pop(C.META_AGREEMENT)
    assert _solve(commercial=com, fixed_sizes="capacity_run")[0] in ("ok", "optimal")
    assert C.META_AGREEMENT in n.meta                           # an F1 run commits
    assert n.meta[C.META_FEE] == fee                            # the design's fee stands
    assert n.links.at["import", "p_nom_opt"] == pytest.approx(fee["size_mw"], abs=EPS)


@pytest.mark.live_solve
def test_an_f1_run_at_another_connection_size_does_not_state_the_fee(reset_backend):
    from services.commercial import connection as C
    from services.commercial.cost_rows import commercial_cost_terms

    n = _edge()
    com = _fee_commercial()
    _solve(commercial=com)
    with PyPSAService.get_lock():
        n.links.at["import", "p_nom"] = n.meta[C.META_FEE]["size_mw"] + 5.0
    assert _solve(commercial=com, fixed_sizes="nameplate")[0] in ("ok", "optimal")
    fee = n.meta[C.META_FEE]
    assert fee["not_established"] == "connection size changed since the design run"
    terms = commercial_cost_terms(n, com)
    assert "network_capacity_not_established" in terms["flags"]
    assert not any(label == "network_capacity" for label, *_ in terms["items"])


@pytest.mark.live_solve
def test_an_f1_run_under_a_changed_agreement_does_not_carry_the_old_fee(reset_backend):
    from services.commercial import connection as C
    from tests.test_connection_agreement import _agreement, _conn, _fee

    n = _edge()
    _solve(commercial=_fee_commercial())
    assert _solve(commercial=_conn(_agreement(fee=_fee(900.0))),
                  fixed_sizes="capacity_run")[0] in ("ok", "optimal")
    assert n.meta[C.META_FEE]["not_established"]


@pytest.mark.live_solve
def test_the_sweeps_freeze_still_skips_the_process_check(reset_backend):
    from services.adequacy.sweep import freeze_capacities
    from tests.test_process_uc_solve_path import _process_net

    n = _install(_process_net(load=1.0, store=False))
    undo = freeze_capacities(n)
    try:
        status, _, state, _ = _solve()
    finally:
        undo()
    assert status in ("ok", "optimal") and state.get("last_process_uc_check") is None


@pytest.mark.live_solve
def test_the_sweeps_operational_solve_still_commits_nothing(reset_backend):
    from services.adequacy.sweep import freeze_capacities
    from services.commercial import connection as C

    n = _edge()
    com = _fee_commercial()
    _solve(commercial=com)
    fee = dict(n.meta[C.META_FEE])
    n.meta.pop(C.META_AGREEMENT)
    undo = freeze_capacities(n)
    try:
        assert _solve(commercial=com)[0] in ("ok", "optimal")
    finally:
        undo()
    assert C.META_AGREEMENT not in n.meta and n.meta[C.META_FEE] == fee


@pytest.mark.live_solve
def test_a_nameplate_run_does_not_touch_the_sizing_record(reset_backend):
    n = _install(_plan_net())
    _solve()
    rec = n.meta["sizing_record"]
    _solve(fixed_sizes="nameplate")
    assert n.meta["sizing_record"] == rec


def test_the_fee_tolerance_is_the_source_checks():
    from services.commercial.connection import FEE_SIZE_TOLERANCE_MW
    from services.solver.fixed_sizes import TOLERANCE_MW

    assert FEE_SIZE_TOLERANCE_MW == TOLERANCE_MW
