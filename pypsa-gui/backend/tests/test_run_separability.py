"""The separability matrix (W0, workspace-reconciliation §11.1).

Each row of `services.run_separability.MATRIX` is proven here or by the native
test it names. The verdict functions are the server's own start checks, reached
through the preflight's gates, so they never wait on the network lock.

Tests named `*_current_behaviour` record a known gap as it stands (the island
study re-sizes; the campus run reads the saved file). They are evidence, not
spec: the fix PR (#448 §3.1's fixed-size island mode) flips them knowingly.
"""
from __future__ import annotations

import contextlib
import contextvars
import pathlib
import queue
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


# ── the matrix itself ───────────────────────────────────────────────────────

def test_every_row_is_classified_and_only_supported_or_bundled_rows_run():
    assert {"optimisation", "study", "loadflow", "sclopf", "campus_electrical",
            "control_checks", "contingencies", "dynamics", "process_detail",
            "fixed_sizes", "grid_scorecard"} <= set(RS.MATRIX)
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
            assert f"def {name}(" in src, ref


def test_a_row_that_is_not_runnable_never_reads_ready():
    for kind, row in RS.MATRIX.items():
        if not row.runnable_in_run:
            v = RS.verdict(kind)
            assert v["ready"] is False and v["blocker"]["code"] == f"kind_{row.classification}"


# ── verdicts are the server's own checks ────────────────────────────────────

def _run_ready(client, bound_ctx, name, project_storage_dir):
    with bound_ctx(client):
        return (RS.verdict("optimisation"),
                RS.verdict("study", project_dir=project_storage_dir(name)))


@pytest.mark.parametrize("tid", _pf.R4_TEMPLATES)
def test_the_optimisation_verdict_is_the_preflights(client, tmp_projects_dir, bound_ctx,
                                                     project_storage_dir, tid):
    """Golden: on every template, each verdict equals the flow's (master's)."""
    kw = {"json": {"site_type": "industrial"}} if tid == "eh_site" else {}
    name = _pf._create(client, tid, "golden", **kw)
    flow = _pf._flow(client, name)
    opt, study = _run_ready(client, bound_ctx, name, project_storage_dir)
    assert opt["ready"] is flow["verdicts"]["optimisation"]["ready"]
    assert study["ready"] is flow["verdicts"]["study"]["ready"]
    assert opt["ready"] is client.post("/api/simulation/preflight").json()["ok"]


def test_a_broken_network_blocks_the_optimisation_with_the_validators_code(client, site,
                                                                           edit, bound_ctx):
    name = site()
    with edit() as n:
        n.loads.loc["it", "p_set"] = float("nan")
    flow = _pf._flow(client, name)
    with bound_ctx(client):
        v = RS.verdict("optimisation")
    assert v["ready"] is False and flow["verdicts"]["optimisation"]["ready"] is False
    issues = client.post("/api/simulation/preflight").json()["issues"]
    assert v["blocker"]["code"] == next(i["code"] for i in issues if i["severity"] == "error")


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
    opt, study = _run_ready(client, bound_ctx, name, project_storage_dir)
    assert opt["ready"] is True
    assert study["ready"] is False and study["blocker"]["code"] == "eh_study_refused"
    assert _pf._flow(client, name)["verdicts"]["study"]["ready"] is False


def test_a_project_without_a_template_has_no_study_to_run(client, site, bound_ctx,
                                                          project_storage_dir):
    name = site()
    with bound_ctx(client):
        v = RS.verdict("study", project_dir=project_storage_dir(name))
    assert v["ready"] is False and v["blocker"]["code"] == "no_study_request"


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
        answers = [RS.verdict("optimisation"),
                   RS.verdict("study", project_dir=project_storage_dir(name)),
                   RS.verdict("loadflow"),
                   RS.verdict("loadflow", with_optimisation=True)]
        assert time.perf_counter() - t0 < 1.0
    for v in answers:
        assert v["ready"] is False and v["deferred"] is True


def test_verdicts_defer_while_a_solver_worker_is_alive(client, site, bound_ctx, monkeypatch):
    site()
    monkeypatch.setattr(sim_router, "_solver_in_flight", lambda: True)
    with bound_ctx(client):
        for kind in ("optimisation", "loadflow"):
            assert RS.verdict(kind)["deferred"] is True


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
        v = RS.verdict("loadflow")
    assert v["ready"] is False and v["blocker"]["code"] == "loadflow_needs_dispatch"
    assert "Run /run first" in v["blocker"]["message"]

    assert client.post("/api/simulation/run").status_code == 200
    _wait_idle(client)
    with bound_ctx(client):
        assert RS.verdict("loadflow")["ready"] is True

    with edit() as n:                                   # a topology edit after the solve
        n.add("Load", "l2", bus="a", p_set=1.0)
    with bound_ctx(client):
        v = RS.verdict("loadflow")
    assert v["ready"] is False and "topology" in v["blocker"]["message"]
    # The route refuses with the same words.
    r = client.post("/api/simulation/run_ac_pf")
    assert r.status_code == 400 and r.json()["detail"] == v["blocker"]["message"]


def test_loadflow_in_a_run_that_optimises_is_the_optimisations_verdict(client, site, edit,
                                                                        bound_ctx):
    site(_line_network(), name="lf2")
    with bound_ctx(client):
        assert RS.verdict("loadflow")["ready"] is False             # no dispatch yet
        assert RS.verdict("loadflow", with_optimisation=True) == RS.verdict("optimisation")
    with edit() as n:
        n.loads.loc["l", "p_set"] = float("nan")
    with bound_ctx(client):
        both = RS.verdict("loadflow", with_optimisation=True)
        assert both == RS.verdict("optimisation") and both["ready"] is False


def test_the_load_flow_overwrites_the_lp_flows(client, site, bound_ctx):
    """Shared state: Stage 2 rewrites the LP's line flows in place; the LP's are
    kept aside in `lopf_results`."""
    site(_line_network(), name="lf3")
    assert client.post("/api/simulation/run").status_code == 200
    _wait_idle(client)
    with bound_ctx(client):
        lp_q0 = PyPSAService.get_network().lines_t.q0.copy()
    assert client.post("/api/simulation/run_ac_pf").status_code == 200
    st = _wait_idle(client)
    assert st["status"] == "completed", st
    with bound_ctx(client):
        n = PyPSAService.get_network()
        assert (n.lines_t.q0["ab"].abs() > 1e-6).all()
        assert lp_q0.empty or (lp_q0.abs() < 1e-9).all().all()
        assert PyPSAService.get_solver_state()["lopf_results"] is not None


# ── the optimisation's implicit sub-step ────────────────────────────────────

@pytest.mark.live_solve
def test_the_binary_check_is_skipped_on_an_operational_solve(reset_backend):
    """The UC check runs after a planning solve (`test_the_binary_check_flags_…`)
    and is skipped on an operational one (`_ic_operational`, set by the sweep's
    freeze). F1 splits this flag (gatekeeper, 2026-10-21)."""
    from services.solver_service import SolverConfig, run_simulation
    from tests.conftest import install_network_into_backend
    from tests.test_process_uc_solve_path import _process_net

    install_network_into_backend(_process_net(load=1.0, store=False))
    cfg = SolverConfig()
    sim_router._state["solver_config"] = cfg
    n = PyPSAService.get_network()
    n._ic_operational = True
    state: dict = {}
    status, _ = run_simulation(cfg, n, PyPSAService.get_lock(), threading.Event(),
                               queue.SimpleQueue(), state_update=lambda **k: state.update(k))
    assert status in ("ok", "optimal")
    assert state.get("last_process_uc_check") is None


# ── SCLOPF: an option of the optimisation ───────────────────────────────────

@pytest.mark.parametrize("cfg,code", [
    ({"sclopf": True, "sclopf_include_all_lines": True, "transmission_losses": True},
     "sclopf_with_transmission_losses"),
    ({"sclopf": True, "sclopf_include_all_lines": True, "solve_strategy": "rolling"},
     "rolling_with_sclopf"),
    ({"sclopf": True}, "sclopf_no_branches"),
])
def test_sclopf_is_refused_where_the_engine_cannot_run_it(client, site, set_cfg, bound_ctx,
                                                          cfg, code):
    site(_line_network(), name="sc")
    set_cfg(**cfg)
    issues = client.post("/api/simulation/preflight").json()["issues"]
    assert code in {i["code"] for i in issues if i["severity"] == "error"}
    with bound_ctx(client):
        assert RS.verdict("sclopf")["ready"] is False


def test_sclopf_on_a_line_network_is_ready(client, site, set_cfg, bound_ctx):
    site(_line_network(), name="sc_ok")
    set_cfg(sclopf=True, sclopf_include_all_lines=True)
    with bound_ctx(client):
        assert RS.verdict("sclopf") == RS.verdict("optimisation")


# ── studies outside the one Run: current behaviour ──────────────────────────

def _no_network_lock(*_a, **_k):
    raise AssertionError("took the open network's lock")


def test_campus_run_reads_the_saved_file_without_the_network_lock_current_behaviour(
        hub, monkeypatch):
    """The campus study runs on the SAVED network.nc and never asks for the open
    network's lock, so it cannot join the one Run until a save step exists."""
    from services import campus_electrical_service as ce

    _ces_draft = ce.draft(hub)
    assert _ces_draft
    monkeypatch.setattr(PyPSAService, "get_lock", staticmethod(_no_network_lock))
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
    out = ce.run_island_study(island_project)
    assert out["cost_of_seamless"] == pytest.approx(1000.0, abs=1e-4)


def test_connection_capacity_belongs_to_another_project_kind(hub):
    from fastapi import HTTPException

    from services import gridspine_service as gs

    with pytest.raises(HTTPException) as exc:
        gs.compute_capacity(hub, "mv", "load")
    assert "project" in str(exc.value.detail)
