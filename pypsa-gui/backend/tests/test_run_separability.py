"""The separability matrix (W0, workspace-reconciliation §11.1).

Each row of `services.run_separability.MATRIX` is proven here or by the native
test it names. The verdict functions are the server's own start checks, reached
through the preflight's gates, so they never wait on the network lock.

Tests named `*_current_behaviour` record a known gap as it stands. They are
evidence, not spec, and the fix PR flips them knowingly: the island study
re-sizes on its own solve (#448 §3.1's fixed-size island mode), and the campus
study reads the saved file (a save step before it can join the one Run).
"""
from __future__ import annotations

import contextlib
import contextvars
import pathlib
import queue
import re
import threading
import time

import pandas as pd
import pypsa
import pytest

from routers import simulation as sim_router
from services import run_separability as RS
from services.pypsa_service import PyPSAService
from tests import test_campus_electrical_service as _ces
from tests import test_campus_island_service as _cis
from tests import test_project_flow as _pf

site = _pf.site
edit = _pf.edit
set_cfg = _pf.set_cfg
user_and_db = _ces.user_and_db
hub = _ces.hub

BACKEND = pathlib.Path(__file__).resolve().parents[1]
KEY = "w0"                                   # the eh_readiness cache key


def _verdicts(client, bound_ctx, project_storage_dir, name, *kinds) -> dict:
    with bound_ctx(client):
        return RS.verdicts(project_storage_dir(name), kinds, cache_key=KEY)


def _flow_verdict(flow, kind) -> dict:
    return {**flow["verdicts"][kind], "deferred": flow["deferred"]}


# ── the matrix itself ───────────────────────────────────────────────────────

def test_every_row_is_classified_and_only_supported_or_bundled_rows_run():
    assert {"optimisation", "study", "loadflow", "sclopf", "campus_electrical",
            "control_checks", "contingencies", "dynamics", "process_detail",
            "fixed_sizes"} <= set(RS.MATRIX)
    for kind, row in RS.MATRIX.items():
        assert row.kind == kind
        assert row.classification in ("supported", "bundled", "unsupported", "unknown")
        if row.classification in ("unsupported", "unknown"):
            assert not row.runnable_in_run and not RS.runnable(kind), kind
        if row.runnable_in_run:
            assert row.evidence, f"{kind} is runnable without evidence"
    assert not RS.runnable("no_such_kind")


def test_every_named_test_exists():
    for row in RS.MATRIX.values():
        for ref in row.evidence:
            path, name = ref.split("::")
            src = (BACKEND / path).read_text("utf-8")
            assert re.search(rf"^def {name}\(", src, re.MULTILINE), ref


def test_a_row_that_is_not_runnable_never_reads_ready(tmp_path):
    kinds = tuple(k for k, r in RS.MATRIX.items() if not r.runnable_in_run)
    out = RS.verdicts(tmp_path, kinds, cache_key=KEY)
    for kind in kinds:
        assert out[kind]["ready"] is False
        assert out[kind]["blocker"]["code"] == f"kind_{RS.MATRIX[kind].classification}"


# ── verdicts are the server's own checks ────────────────────────────────────

def _assert_golden(client, bound_ctx, project_storage_dir, name):
    """The verdicts are the flow's (master's B1 payload), blocker included."""
    flow = _pf._flow(client, name)
    v = _verdicts(client, bound_ctx, project_storage_dir, name, "optimisation", "study", "sclopf")
    assert v["optimisation"] == _flow_verdict(flow, "optimisation") == v["sclopf"]
    if flow["verdicts"]["study"] is None:
        assert v["study"]["blocker"]["code"] == "no_study_request"
    else:
        assert v["study"] == _flow_verdict(flow, "study")
    assert flow["run_ready"] is (v["optimisation"]["ready"] and (
        flow["verdicts"]["study"] is None or v["study"]["ready"]))
    assert v["optimisation"]["ready"] is client.post("/api/simulation/preflight").json()["ok"]
    return v


@pytest.mark.parametrize("tid", _pf.R4_TEMPLATES)
def test_the_optimisation_verdict_is_the_preflights(client, tmp_projects_dir, bound_ctx,
                                                     project_storage_dir, tid):
    """Golden, on every EH template."""
    kw = {"json": {"site_type": "industrial"}} if tid == "eh_site" else {}
    _assert_golden(client, bound_ctx, project_storage_dir,
                   _pf._create(client, tid, "golden", **kw))


def test_the_verdicts_on_a_classic_network_without_a_template(client, site, bound_ctx,
                                                              project_storage_dir):
    import project_templates._build as classic

    v = _assert_golden(client, bound_ctx, project_storage_dir,
                       site(classic.build_ieee14(), name="ieee14"))
    assert v["study"]["ready"] is False


def test_a_broken_network_blocks_the_optimisation_with_the_flows_blocker(
        client, site, edit, bound_ctx, project_storage_dir):
    name = site()
    with edit() as n:
        n.loads.loc["it", "p_set"] = float("nan")
    v = _assert_golden(client, bound_ctx, project_storage_dir, name)
    assert v["optimisation"]["ready"] is False and v["optimisation"]["blocker"]["section"] == "loads"


def test_the_study_verdict_is_the_start_refusal(client, tmp_projects_dir, bound_ctx,
                                                project_storage_dir):
    """The refusal `test_r4_a_study_start_refusal_blocks_run` builds."""
    import json

    name = _pf._create(client, "eh_datacenter", "refused")
    path = project_storage_dir(name) / "eh_template.json"
    meta = json.loads(path.read_text())
    meta["pack_overrides"] = {**meta["pack_overrides"], "import_energy_mwh_per_year": 1000.0}
    path.write_text(json.dumps(meta))
    grid = next(r for r in client.get("/api/network/links").json() if r["name"] == "grid_import")
    assert client.put("/api/network/links/grid_import",
                      json={**grid, "p_min_pu": -1.0}).status_code == 200
    v = _assert_golden(client, bound_ctx, project_storage_dir, name)
    assert v["optimisation"]["ready"] is True
    assert v["study"]["ready"] is False and v["study"]["blocker"]["section"] == "scenarios"


def test_a_study_cannot_drop_its_mandatory_stages():
    from services.adequacy.eh_study import REQUIRED_STAGES, StageSelectionError, validate_stages

    assert set(REQUIRED_STAGES) == {"apply_pack", "ens_solve"}
    with pytest.raises(StageSelectionError):
        validate_stages(["ens_solve", "assemble"])
    with pytest.raises(StageSelectionError):
        validate_stages(["apply_pack", "assemble"])


@contextlib.contextmanager
def _lock_held_elsewhere(client, bound_ctx):
    held, release = threading.Event(), threading.Event()

    def holder():
        with PyPSAService.get_lock():
            held.set()
            release.wait(10)

    with bound_ctx(client):
        ctx = contextvars.copy_context()
    t = threading.Thread(target=lambda: ctx.run(holder), daemon=True)
    t.start()
    assert held.wait(5)
    try:
        yield
    finally:
        release.set()
        t.join(5)


def test_verdicts_defer_instead_of_waiting_on_a_held_lock(client, tmp_projects_dir, bound_ctx,
                                                          project_storage_dir):
    """The gate's precondition: a verdict never waits on the network lock."""
    name = _pf._create(client, "eh_h2_hub", "busy")
    with _lock_held_elsewhere(client, bound_ctx), bound_ctx(client):
        t0 = time.perf_counter()
        alone = RS.verdicts(project_storage_dir(name), ("optimisation", "study"), cache_key=KEY)
        lf = RS.verdicts(project_storage_dir(name), ("loadflow",), cache_key=KEY)
        both = RS.verdicts(project_storage_dir(name), ("optimisation", "loadflow"), cache_key=KEY)
        assert time.perf_counter() - t0 < 3.0          # the flow's own budget
    for v in (*alone.values(), *lf.values(), *both.values()):
        assert v["ready"] is False and v["deferred"] is True


def test_verdicts_defer_while_a_solver_worker_is_alive(client, site, bound_ctx, monkeypatch,
                                                       project_storage_dir):
    name = site()
    monkeypatch.setattr(sim_router, "_solver_in_flight", lambda: True)
    for kinds in (("optimisation",), ("loadflow",)):
        v = _verdicts(client, bound_ctx, project_storage_dir, name, *kinds)
        assert v[kinds[0]]["deferred"] is True


# ── the load flow: bundled with a dispatch ──────────────────────────────────

def _line_network() -> pypsa.Network:
    """Two AC buses on a line: a slack generator feeds a load."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=3, freq="h"))
    n.add("Carrier", "AC")
    n.add("Bus", "a", v_nom=20.0, x=5.0, y=52.0, carrier="AC")
    n.add("Bus", "b", v_nom=20.0, x=5.01, y=52.01, carrier="AC")
    n.add("Generator", "g", bus="a", p_nom=50.0, marginal_cost=10.0, control="Slack", carrier="AC")
    n.add("Line", "ab", bus0="a", bus1="b", x=0.5, r=0.05, s_nom=40.0)
    n.add("Load", "l", bus="b", p_set=10.0, q_set=3.0)
    return n


def _wait_idle(client, timeout=60.0) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = client.get("/api/simulation/status").json()
        if st.get("status") != "running":
            return st
        time.sleep(0.2)
    raise AssertionError("the run did not finish")


def test_loadflow_needs_a_fresh_dispatch(client, site, edit, bound_ctx):
    site(_line_network(), name="lf")
    with bound_ctx(client):
        v = RS.loadflow_verdict()
    assert v["ready"] is False and v["blocker"]["code"] == "ac_pf_not_solved"

    assert client.post("/api/simulation/run").status_code == 200
    _wait_idle(client)
    with bound_ctx(client):
        assert RS.loadflow_verdict()["ready"] is True

    with edit() as n:                                   # a topology edit after the solve
        n.add("Load", "l2", bus="a", p_set=1.0)
    with bound_ctx(client):
        v = RS.loadflow_verdict()
    assert v["ready"] is False and v["blocker"]["code"] == "ac_pf_stale_dispatch"
    # The route refuses with the same words.
    r = client.post("/api/simulation/run_ac_pf")
    assert r.status_code == 400 and r.json()["detail"] == v["blocker"]["message"]


def test_loadflow_in_a_run_that_optimises_is_the_optimisations_verdict(
        client, site, edit, bound_ctx, project_storage_dir):
    name = site(_line_network(), name="lf2")
    v = _verdicts(client, bound_ctx, project_storage_dir, name, "loadflow")
    assert v["loadflow"]["ready"] is False                         # no dispatch yet
    v = _verdicts(client, bound_ctx, project_storage_dir, name, "optimisation", "loadflow")
    assert v["loadflow"] == v["optimisation"] and v["loadflow"]["ready"] is True
    with edit() as n:
        n.loads.loc["l", "p_set"] = float("nan")
    v = _verdicts(client, bound_ctx, project_storage_dir, name, "optimisation", "loadflow")
    assert v["loadflow"] == v["optimisation"] and v["loadflow"]["ready"] is False


def test_the_load_flow_overwrites_the_lp_flows(client, site, bound_ctx):
    """Shared state: Stage 2 rewrites the LP's flows and voltages in place; the
    LP's are kept aside in `lopf_results`."""
    site(_line_network(), name="lf3")
    assert client.post("/api/simulation/run").status_code == 200
    _wait_idle(client)
    with bound_ctx(client):
        n = PyPSAService.get_network()
        lp_p0 = n.lines_t.p0["ab"].copy()
        lp_v = n.buses_t.v_mag_pu.copy()
    assert client.post("/api/simulation/run_ac_pf").status_code == 200
    st = _wait_idle(client)
    assert st["status"] == "completed", st
    with bound_ctx(client):
        n = PyPSAService.get_network()
        assert (n.lines_t.q0["ab"].abs() > 1e-6).all()            # the LP has no reactive flow
        assert not n.lines_t.p0["ab"].equals(lp_p0)               # losses: p0 moved
        assert (n.buses_t.v_mag_pu["b"] < 1.0 - 1e-6).all()       # a voltage drop to the load
        assert lp_v.empty or not n.buses_t.v_mag_pu.equals(lp_v)
        assert PyPSAService.get_solver_state()["lopf_results"] is not None


# ── the optimisation's implicit sub-step ────────────────────────────────────

def _solve(n, *, operational: bool) -> dict:
    from services.solver_service import SolverConfig, run_simulation
    from tests.conftest import install_network_into_backend

    install_network_into_backend(n)
    cfg = SolverConfig()
    sim_router._state["solver_config"] = cfg
    live = PyPSAService.get_network()
    if operational:
        live._ic_operational = True
    state: dict = {}
    status, _ = run_simulation(cfg, live, PyPSAService.get_lock(), threading.Event(),
                               queue.SimpleQueue(), state_update=lambda **k: state.update(k))
    assert status in ("ok", "optimal")
    return state


@pytest.mark.live_solve
def test_the_binary_check_runs_after_a_plan_and_not_on_an_operational_solve(reset_backend):
    """The optimisation's implicit sub-step: the UC check runs after a planning
    solve and is skipped on an operational one (`_ic_operational`, set by the
    sweep's freeze). F1 splits this flag (gatekeeper, 2026-10-21)."""
    from tests.test_process_uc_solve_path import _process_net

    assert _solve(_process_net(load=1.0, store=False), operational=False)[
        "last_process_uc_check"]["flagged"] is True
    assert _solve(_process_net(load=1.0, store=False), operational=True).get(
        "last_process_uc_check") is None


# ── SCLOPF: an option of the optimisation ───────────────────────────────────

@pytest.mark.parametrize("cfg,code", [
    ({"sclopf": True, "sclopf_include_all_lines": True, "transmission_losses": True},
     "sclopf_with_transmission_losses"),
    ({"sclopf": True, "sclopf_include_all_lines": True, "solve_strategy": "rolling"},
     "rolling_with_sclopf"),
    ({"sclopf": True}, "sclopf_no_branches"),
])
def test_sclopf_is_refused_where_the_engine_cannot_run_it(client, site, set_cfg, bound_ctx,
                                                          project_storage_dir, cfg, code):
    name = site(_line_network(), name="sc")
    set_cfg(**cfg)
    issues = client.post("/api/simulation/preflight").json()["issues"]
    assert code in {i["code"] for i in issues if i["severity"] == "error"}
    v = _verdicts(client, bound_ctx, project_storage_dir, name, "sclopf")
    assert v["sclopf"]["ready"] is False


def test_sclopf_on_a_line_network_is_ready(client, site, set_cfg, bound_ctx, project_storage_dir):
    name = site(_line_network(), name="sc_ok")
    set_cfg(sclopf=True, sclopf_include_all_lines=True)
    v = _verdicts(client, bound_ctx, project_storage_dir, name, "sclopf", "optimisation")
    assert v["sclopf"] == v["optimisation"] and v["sclopf"]["ready"] is True


# ── studies outside the one Run: current behaviour ──────────────────────────

def _no_network_lock(*_a, **_k):
    raise AssertionError("reached for the open network, its lock or its solver state")


def test_campus_run_reads_the_saved_file_without_the_network_lock_current_behaviour(
        hub, monkeypatch):
    """The campus study runs on the SAVED network.nc: it never reads the open
    network nor asks for its lock, so it cannot join the one Run until a save
    step exists (not #448 §3.1)."""
    from services import campus_electrical_service as ce

    assert ce.draft(hub)
    monkeypatch.setattr(PyPSAService, "get_lock", staticmethod(_no_network_lock))
    monkeypatch.setattr(PyPSAService, "get_network", staticmethod(_no_network_lock))
    out = ce.run(hub, {"k": 1, "pf": 0.95, "margin": 0.1, "n_minus_1": True})
    assert out["results"]["transformers"]


@pytest.fixture
def island_project(user_and_db):
    db, user = user_and_db
    from services import project_registry

    row = project_registry.create_root(db, user, "Island W0")
    n = _cis._hub(load=10.0, gensets=[("g", 20.0)])
    n.export_to_netcdf(str(project_registry.ensure_project_dir(row) / "network.nc"))
    return row


def test_the_island_study_resizes_on_its_own_solve_current_behaviour(island_project,
                                                                     monkeypatch):
    """#448 §3.1: the island study re-optimises the extendable BESS on its own
    HiGHS solve (10 MW at 100/MW for seamless) instead of reading the capacity
    plan's sizes, and reads neither the project's solver config nor the open
    network's lock."""
    from services import campus_electrical_service as ce

    ce.save_island(island_project, _cis._config())
    monkeypatch.setattr(PyPSAService, "get_lock", staticmethod(_no_network_lock))
    monkeypatch.setattr(PyPSAService, "get_solver_state", staticmethod(_no_network_lock))
    monkeypatch.setattr(PyPSAService, "get_network", staticmethod(_no_network_lock))
    out = ce.run_island_study(island_project)
    assert out["cost_of_seamless"] == pytest.approx(1000.0, abs=1e-4)


def test_connection_capacity_belongs_to_another_project_kind(hub):
    from fastapi import HTTPException

    from services import gridspine_service as gs

    with pytest.raises(HTTPException) as exc:
        gs.compute_capacity(hub, "mv", "load")
    assert "project" in str(exc.value.detail)


def test_the_dynamics_hand_off_belongs_to_another_project_kind(hub):
    """Frequency and time domain: no in-app simulation; the hand-off bundle
    and read-back live on a Planning → dynamics project."""
    from fastapi import HTTPException

    from services import gridspine_service as gs

    with pytest.raises(HTTPException) as exc:
        gs.export_handoff_bundle(hub, 0)
    assert "project" in str(exc.value.detail)


def test_process_modelling_is_unit_commitment_only():
    """Detailed process: the only process solve modes are unit-commitment ones."""
    import typing

    from models.schemas import SolverConfigSchema

    mode = SolverConfigSchema.model_fields["process_uc"].annotation
    assert set(typing.get_args(mode)) == {"auto", "linearised", "binary"}


def test_flipping_sizing_off_is_refused_by_preflight(client, site, edit):
    """Sizing off is not "extendable off": a never-built candidate at zero
    capacity is refused (modular-cards §4), hence F1."""
    name = site()
    with edit() as n:
        n.add("Generator", "pv_candidate", bus="site", carrier="gas", p_nom=0.0,
              p_nom_extendable=False, marginal_cost=0.0)
    issues = client.post("/api/simulation/preflight").json()["issues"]
    assert "generator_p_nom_invalid" in {i["code"] for i in issues if i["severity"] == "error"}
    assert _pf._flow(client, name)["verdicts"]["optimisation"]["ready"] is False
