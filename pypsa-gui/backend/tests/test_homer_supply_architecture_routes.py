"""
homer-parity 03a: the supply-architecture comparison routes, worker and
throw-away forks, with a fake `run_simulation` (the queue, the forks, the
campaign, the study slot and the reads are real). Gate letters are GK B's
plan conditions.
"""
from __future__ import annotations

import threading
import time

import pandas as pd
import pypsa
import pytest

from services.pypsa_service import PyPSAService
from services.study import runner as R
from tests.study_s4_support import enable_studies

ARCHS = 14     # PV, battery, genset, grid: 15 combinations less the battery alone


@pytest.fixture
def studies_on(monkeypatch):
    yield from enable_studies(monkeypatch)


def _site() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=4, freq="h"))
    for c in ("AC", "solar", "battery", "diesel", "grid", "grid_import"):
        n.add("Carrier", c)
    n.add("Bus", "site", carrier="AC", v_nom=0.4)
    n.add("Bus", "grid", carrier="AC", v_nom=0.4)
    n.add("Load", "site_load", bus="site", p_set=1.0)
    n.add("Generator", "pv", bus="site", carrier="solar", p_nom_extendable=True,
          p_nom_max=10.0, overnight_cost=800_000.0, lifetime=25, discount_rate=0.07)
    n.generators_t.p_max_pu["pv"] = [0.0, 0.5, 1.0, 0.5]
    n.add("StorageUnit", "battery", bus="site", carrier="battery", p_nom_extendable=True,
          p_nom_max=10.0, max_hours=2, overnight_cost=500_000.0, lifetime=15,
          discount_rate=0.07)
    n.add("Generator", "genset", bus="site", carrier="diesel", p_nom_extendable=True,
          p_nom_max=10.0, marginal_cost=250.0, overnight_cost=300_000.0, lifetime=20,
          discount_rate=0.07)
    n.add("Generator", "grid", bus="grid", carrier="grid", p_nom=5.0, marginal_cost=150.0)
    n.add("Link", "grid_import", bus0="grid", bus1="site", carrier="grid_import", p_nom=5.0)
    return n


class ArchSolver:
    """Sizes what the fork has; a genset alone is infeasible."""

    def __init__(self, gate: threading.Event | None = None, fail: str | None = None):
        self.calls: list[set[str]] = []
        self.gate, self.fail = gate, fail

    def __call__(self, config, n, lock, stop_event, log_queue, state_update=None):
        names = set(n.generators.index) | set(n.storage_units.index) | set(n.links.index)
        self.calls.append(names)
        if self.gate is not None:
            self.gate.wait(30)
        gens = set(n.generators.index)
        if gens == {"genset"}:
            return "warning", "infeasible"
        if self.fail is not None and gens == {self.fail}:
            raise RuntimeError("solver crashed")
        ext = n.generators.index[n.generators.p_nom_extendable]
        n.generators["p_nom_opt"] = n.generators["p_nom"]
        n.generators.loc[ext, "p_nom_opt"] = 1.0
        n.storage_units["p_nom_opt"] = 0.5
        n.links["p_nom_opt"] = n.links["p_nom"]
        p = {g: 0.0 for g in gens}
        src = "pv" if "pv" in gens else ("grid" if "grid" in gens else "genset")
        p[src] = 1.0
        n.generators_t.p = pd.DataFrame(p, index=n.snapshots)
        n._objective = 100.0 * len(names)
        return "ok", "optimal"


@pytest.fixture
def session_local(_auth_db):
    return _auth_db[1]


@pytest.fixture
def arch_solver(monkeypatch):
    from services import solver_service

    solver = ArchSolver()
    monkeypatch.setattr(solver_service, "run_simulation", solver)
    return solver


def _project(client, install_network, name: str) -> str:
    install_network(_site(), name=name)
    r = client.post(f"/api/projects/{name}", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/projects/{name}/studies/", json={
        "question_id": "homer_supply", "name": "Supply architectures"})
    assert r.status_code == 201, r.text
    return r.json()["study_id"]


def _url(name, sid, tail=""):
    return f"/api/projects/{name}/studies/{sid}/supply-architectures{tail}"


def _wait(client, name, sid, timeout=90.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(_url(name, sid))
        if r.status_code == 200 and r.json().get("status") != "running":
            return r.json()
        time.sleep(0.1)
    raise AssertionError("the comparison did not finish")


def _var_rows(session_local, name):
    from sqlalchemy import select

    from db.models import Project

    with session_local() as db:
        return [p.name for p in db.scalars(select(Project)).all()
                if p.name.startswith(f"{name}-var-")]


def test_never_run_is_404(client, studies_on, install_network):
    sid = _project(client, install_network, "sa-never")
    r = client.get(_url("sa-never", sid))
    assert r.status_code == 404
    assert r.json()["detail"]["error_kind"] == "supply_architectures_never_run"
    assert client.post(_url("sa-never", sid, "/abort")).status_code == 404


def test_one_row_per_architecture_infeasible_listed_forks_deleted(
        client, studies_on, install_network, arch_solver, session_local):
    sid = _project(client, install_network, "sa-all")
    r = client.post(_url("sa-all", sid), json={})
    assert r.status_code == 202, r.text
    assert r.json()["solves_estimated"] == ARCHS
    rec = _wait(client, "sa-all", sid)
    assert rec["status"] == "done", rec
    assert rec["schema_version"] == 1 and rec["stale"] is False
    rows = rec["rows"]
    assert len(rows) == ARCHS == len(arch_solver.calls)
    genset = next(r for r in rows if r["architecture_id"] == "genset")
    assert genset["status"] == "infeasible"                # listed, not dropped
    assert genset["total_cost_eur_per_year"] is None
    # the fake finds a genset with no other source infeasible, battery or not;
    # both trail the solved rows in architecture order
    assert [(r["architecture_id"], r["status"]) for r in rows[-2:]] == [
        ("genset", "infeasible"), ("battery+genset", "infeasible")]
    ok = [r for r in rows if r["status"] == "ok"]
    assert len(ok) == ARCHS - 2
    costs = [r["total_cost_eur_per_year"] for r in ok]
    assert costs == sorted(costs)
    assert rec["best_architecture_id"] == ok[0]["architecture_id"]
    pvb = next(r for r in rows if r["architecture_id"] == "pv+battery")
    assert pvb["capex_eur"] == pytest.approx(800_000.0 + 500_000.0 * 0.5)
    assert pvb["renewable_fraction"] == 1.0
    # each fork held only its architecture's assets (omitted, not zeroed)
    assert {"pv", "battery"} in arch_solver.calls
    assert rec["solves_charged"] == ARCHS
    assert _var_rows(session_local, "sa-all") == [] and rec["forks_left"] == []


def test_named_architectures_only(client, studies_on, install_network, arch_solver):
    sid = _project(client, install_network, "sa-named")
    r = client.post(_url("sa-named", sid), json={"architectures": ["grid", "pv+battery"]})
    assert r.status_code == 202, r.text
    rec = _wait(client, "sa-named", sid)
    assert [r["architecture_id"] for r in rec["rows"]] == ["grid", "pv+battery"]
    bad = client.post(_url("sa-named", sid), json={"architectures": ["wind"]})
    assert bad.status_code == 422
    assert bad.json()["detail"]["error_kind"] == "unknown_architecture"


def test_budget_caps_the_run(client, studies_on, install_network, arch_solver):
    sid = _project(client, install_network, "sa-budget")
    r = client.post(_url("sa-budget", sid), json={"budget_solves": 2})
    assert r.status_code == 409
    assert r.json()["detail"]["error_kind"] == "campaign_budget_exhausted"
    assert arch_solver.calls == []


def test_an_edited_base_reads_stale(client, studies_on, install_network, arch_solver,
                                    project_storage_dir):
    """G1: the record pins the base's input fingerprint."""
    sid = _project(client, install_network, "sa-stale")
    client.post(_url("sa-stale", sid), json={"architectures": ["grid"]})
    assert _wait(client, "sa-stale", sid)["stale"] is False
    path = project_storage_dir("sa-stale") / "network.nc"
    n = pypsa.Network()
    with PyPSAService.get_netcdf_io_lock():
        PyPSAService.import_network_from_netcdf(n, path)
    n.generators.loc["genset", "marginal_cost"] = 999.0
    n.export_to_netcdf(path)
    assert client.get(_url("sa-stale", sid)).json()["stale"] is True


def test_unknown_schema_version_reads_not_evaluated(client, studies_on, install_network,
                                                    arch_solver, project_storage_dir):
    """G9."""
    from services.study import store

    sid = _project(client, install_network, "sa-schema")
    client.post(_url("sa-schema", sid), json={"architectures": ["grid"]})
    rec = _wait(client, "sa-schema", sid)
    base_dir = project_storage_dir("sa-schema")
    store.save_aux(base_dir, sid, "supply_architectures", {**rec, "schema_version": 99})
    got = client.get(_url("sa-schema", sid)).json()
    assert got["status"] == "not_evaluated" and got["reason"] == "unknown_schema_version"


def test_a_solver_exception_fails_its_row_and_leaves_no_fork(
        client, studies_on, install_network, monkeypatch, session_local):
    """G6/G7: a crash in one solve is that row's `failed`; its fork is gone."""
    from services import solver_service

    monkeypatch.setattr(solver_service, "run_simulation", ArchSolver(fail="grid"))
    sid = _project(client, install_network, "sa-crash")
    client.post(_url("sa-crash", sid), json={"architectures": ["grid", "pv"]})
    rec = _wait(client, "sa-crash", sid)
    assert rec["status"] == "done"
    by = {r["architecture_id"]: r for r in rec["rows"]}
    assert by["grid"]["status"] == "failed" and by["pv"]["status"] == "ok"
    assert _var_rows(session_local, "sa-crash") == []


def test_a_worker_exception_leaves_no_fork(client, studies_on, install_network,
                                           arch_solver, monkeypatch, session_local):
    """G7: an exception between solves fails the record; no fork is left."""
    from services.study import supply_architectures as SA

    real = SA.row_of
    calls = []

    def boom(arch, n):
        calls.append(arch.architecture_id)
        if len(calls) == 2:
            raise RuntimeError("mid-run")
        return real(arch, n)

    monkeypatch.setattr(SA, "row_of", boom)
    sid = _project(client, install_network, "sa-boom")
    client.post(_url("sa-boom", sid), json={"architectures": ["grid", "pv", "genset+grid"]})
    rec = _wait(client, "sa-boom", sid)
    assert rec["status"] == "failed" and "mid-run" in rec["error"]
    assert _var_rows(session_local, "sa-boom") == []


def test_abort_marks_the_rest_not_reached_and_one_slot_at_a_time(
        client, studies_on, install_network, monkeypatch, session_local,
        registry_key_for):
    """G6/G7/G8: while it runs, a second comparison, a tornado or a run is
    refused (one study slot), the study cannot be deleted; an abort leaves
    the rows not reached as not_reached and no fork behind."""
    from services import solver_service

    gate = threading.Event()
    solver = ArchSolver(gate=gate)
    monkeypatch.setattr(solver_service, "run_simulation", solver)
    sid = _project(client, install_network, "sa-abort")
    assert client.post(_url("sa-abort", sid),
                       json={"architectures": ["grid", "pv", "genset+grid"]}).status_code == 202
    deadline = time.time() + 30
    while not solver.calls and time.time() < deadline:
        time.sleep(0.05)
    again = client.post(_url("sa-abort", sid), json={})
    assert again.status_code == 409
    assert again.json()["detail"]["error_kind"] == "study_running"
    ctx = PyPSAService.get_context(registry_key_for("sa-abort"))
    with pytest.raises(R.RunRefused) as busy:      # what the run and the tornado call
        R._refuse_busy(ctx)
    assert busy.value.code == "study_running"
    assert client.delete(f"/api/projects/sa-abort/studies/{sid}").status_code == 409
    live = client.get(_url("sa-abort", sid)).json()
    assert live["status"] == "running" and live["kind"] == "supply_architectures"
    ab = client.post(_url("sa-abort", sid, "/abort"))
    assert ab.status_code == 200 and ab.json()["aborting"] is True
    gate.set()
    rec = _wait(client, "sa-abort", sid)
    assert rec["status"] == "aborted"
    assert [r["status"] for r in rec["rows"]].count("not_reached") == 2
    assert _var_rows(session_local, "sa-abort") == [] and rec["forks_left"] == []


def test_unsaved_base_is_refused(client, studies_on, install_network, arch_solver,
                                 registry_key_for, monkeypatch):
    from services import dirty_state

    sid = _project(client, install_network, "sa-dirty")
    monkeypatch.setattr(dirty_state, "is_dirty", lambda ctx=None: True)
    r = client.post(_url("sa-dirty", sid), json={})
    assert r.status_code == 409
    assert r.json()["detail"]["error_kind"] == "baseline_not_saved"


def test_other_org_cannot_read_or_start(client, studies_on, install_network,
                                        other_org_client):
    sid = _project(client, install_network, "sa-acl")
    assert other_org_client.get(_url("sa-acl", sid)).status_code == 404
    assert other_org_client.post(_url("sa-acl", sid), json={}).status_code == 404


# ── the chat tools (G10) ──────────────────────────────────────────────────

def test_chat_tools_are_registered_with_their_tiers_and_gates():
    from harness import catalogue, job_holds
    from harness.confirm import AUTO_APPROVE_TIERS, _confirm_tiers
    from services import chat_tools

    tiers = {"compare_supply_architectures": "execution_long_running",
             "get_supply_architectures": "read"}
    for name, tier in tiers.items():
        assert name in chat_tools.DISPATCHERS and catalogue.TOOL_ROUTES[name]
        assert catalogue.safety_tier_for(name) == tier
    # the start asks for a card wherever the tornado's start does; the read never
    for guided in (True, False):
        gated = {n for n, t in tiers.items()
                 if t in _confirm_tiers(guided) and t not in AUTO_APPROVE_TIERS}
        start_gated = catalogue.safety_tier_for("run_decision_study") in _confirm_tiers(guided)
        assert gated == ({"compare_supply_architectures"} if start_gated else set())
    assert job_holds.JOB_KINDS["compare_supply_architectures"] == job_holds.INPROC_SOLVER


def test_chat_tools_start_and_read_the_table(client, studies_on, install_network,
                                             arch_solver):
    from services import chat_tools

    sid = _project(client, install_network, "sa-chat")
    out = chat_tools.compare_supply_architectures("sa-chat", sid,
                                                  architectures=["genset", "pv+battery"])
    assert out["action"] == "start" and out["solves_estimated"] == 2
    deadline = time.time() + 60
    while time.time() < deadline:
        got = chat_tools.get_supply_architectures("sa-chat", sid)
        if "running" not in got:
            break
        time.sleep(0.1)
    assert got["status"] == "done" and got["stale"] is False
    assert [(r["architecture_id"], r["status"]) for r in got["rows"]] == [
        ("pv+battery", "ok"), ("genset", "infeasible")]
    assert got["rows"][0]["sizes"] == {"pv": 1.0, "battery": 0.5}
    assert got["rows"][1]["total_cost_eur_per_year"] is None
    assert got["best_architecture_id"] == "pv+battery"
    assert chat_tools.compare_supply_architectures("sa-chat", sid, action="abort") == {
        "action": "abort", "status": "done", "aborting": False}


def test_chat_tool_bad_action_is_typed(client, studies_on):
    from fastapi import HTTPException

    from services import chat_tools

    with pytest.raises(HTTPException) as exc:
        chat_tools.compare_supply_architectures("p", "s", action="everything")
    assert exc.value.detail["error_kind"] == "supply_architectures_action_invalid"
