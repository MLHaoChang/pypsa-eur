"""
S4 M1: the option runner (plan S4 "Acceptance"; review v2 BC-5; gate S1, S3
carries), with the fake-solver pattern of `tests/test_adequacy_abort.py` —
`services.solver_service.run_simulation` replaced by a function that writes a
dispatch and returns ("ok", "optimal"), so the queue, the forks, the campaign,
the mesh and the result reads are all real.
"""
from __future__ import annotations

import json
import threading

import pandas as pd
import pytest

from services.adequacy import campaign
from services.pypsa_service import PyPSAService
from services.study import runner as R
from services.study import tariff as T
from tests.study_s4_support import (
    OPTIONS,
    create_pack_study,
    enable_studies,
    wait_run,
)


@pytest.fixture
def studies_on(monkeypatch):
    yield from enable_studies(monkeypatch)


class FakeSolver:
    """Writes sizes and a link dispatch; records what it was asked to solve."""

    def __init__(self, on_call=None):
        self.calls: list[tuple[str, object, int]] = []
        self.on_call = on_call

    def __call__(self, config, n, lock, stop_event, log_queue, state_update=None):
        storage = len(n.storage_units)
        self.calls.append((n.name, config, storage))
        load = n.loads_t.p_set["site_load"]
        shave = 0.3 if storage else 0.0
        n.links_t.p0 = pd.DataFrame({"grid_import": (load - shave).clip(lower=0.0),
                                     "grid_export": 0.0}, index=n.snapshots)
        if storage:
            n.storage_units["p_nom_opt"] = 0.3
        n.generators["p_nom_opt"] = n.generators["p_nom"]
        if "pv" in n.generators.index:
            n.generators.loc["pv", "p_nom_opt"] = 0.5
        n.links["p_nom_opt"] = n.links["p_nom"]
        n._objective = 1000.0 + len(self.calls)
        if self.on_call is not None:
            self.on_call(len(self.calls))
        return "ok", "optimal"


@pytest.fixture
def fake(monkeypatch):
    from services import solver_service

    solver = FakeSolver()
    monkeypatch.setattr(solver_service, "run_simulation", solver)
    return solver


def _base_ctx(registry_key_for, base):
    return PyPSAService.get_context(registry_key_for(base))


def _fork_rows(project_row, base):
    return {o: project_row(f"{base}-opt-{o}") for o in OPTIONS}


def _setup(client, api_project, name):
    api_project(f"{name}-src")
    r = create_pack_study(client, f"{name}-src", name)
    assert r.status_code == 201, r.text
    return r.json()["study_id"]


# ── five forks, five solves, one campaign on the study's context ─────────

def test_five_options_five_forks_five_solves_charged_on_the_base_context(
        client, api_project, studies_on, fake, project_row, project_storage_dir,
        registry_key_for, session_ctx):
    sid = _setup(client, api_project, "run-five")
    r = client.post(f"/api/projects/run-five/studies/{sid}/run",
                    json={"fidelity": "quick_screen"})
    assert r.status_code == 202, r.text
    assert r.json()["solves_charged"] == 5
    rec = wait_run(client, "run-five", sid)
    assert rec["status"] == "done", rec

    # Five forks, children of the base, each solved once with its OWN config.
    base = project_row("run-five")
    forks = _fork_rows(project_row, "run-five")
    assert all(f is not None and f.parent_project_id == base.id for f in forks.values())
    assert sorted(c[0] for c in fake.calls) == sorted(f"run-five-opt-{o}" for o in OPTIONS)
    for _name, cfg, _storage in fake.calls:
        assert cfg.solve_strategy == "full" and cfg.sclopf is False
        assert cfg.demand_charge["import_links"] == ["grid_import"]
        assert cfg.demand_charge["billing_period"] == "month"
        assert cfg.discount_rate == 0.07
    storage_by_fork = {c[0]: c[2] for c in fake.calls}
    assert storage_by_fork["run-five-opt-none"] == 0          # omitted, not zeroed
    assert all(storage_by_fork[f"run-five-opt-{o}"] == 1 for o in OPTIONS if o != "none")
    for o, row in forks.items():
        meta = json.loads((project_storage_dir(row.name) / "metadata.json").read_text())
        assert meta["owner_study_id"] == sid, o               # survived the queue save
        assert not (project_storage_dir(row.name) / "studies").exists()

    # The campaign: charged on the BASE context, five solves, then closed.
    assert rec["campaign"]["entries"] == [
        {"study": "decision_study", "solves_charged": 5, "at": rec["campaign"]["entries"][0]["at"]}]
    assert rec["campaign"]["active"] is False
    fg = session_ctx(client)
    assert fg is not _base_ctx(registry_key_for, "run-five")
    assert R._on_ctx(fg, campaign.status)["active"] is False

    # Findings: every option ok, the baseline billed, `none` has no storage.
    study = client.get(f"/api/projects/run-five/studies/{sid}").json()
    findings = json.loads((project_storage_dir("run-five") / study["findings_ref"]).read_text())
    assert findings["options_status"] == "ok" and findings["pending_options"] == []
    by_id = {o["option_id"]: o for o in findings["options"]}
    assert by_id["none"]["sizes"] == []
    assert by_id["bess_2h"]["sizes"][0]["p_nom_opt"] == pytest.approx(0.3)
    assert by_id["bess_2h"]["sizes"][0]["e_nom_opt"] == pytest.approx(0.6)
    assert findings["baseline"]["bill"] is not None
    assert sorted(study["option_projects"]) == sorted(str(f.id) for f in forks.values())
    assert study["fidelity_last_run"] == "quick_screen"


def test_the_bridge_reads_the_forks_config_and_equals_the_bill(
        client, api_project, studies_on, fake):
    """
    Gate S3 [S4]/N4: `demand_charge_eur` from the objective bridge, computed
    with the config the fork solved under, equals the bill's demand charge.
    """
    sid = _setup(client, api_project, "run-bridge")
    assert client.post(f"/api/projects/run-bridge/studies/{sid}/run", json={}).status_code == 202
    rec = wait_run(client, "run-bridge", sid)
    for o in OPTIONS:
        d = rec["details"][o]
        assert d["demand_charge_eur"] is not None and d["demand_charge_eur"] > 0, o
        assert d["demand_charge_eur"] == pytest.approx(d["bill"]["by_component"]["demand"]), o


def test_an_open_agent_campaign_is_charged_not_replaced(
        client, api_project, studies_on, fake, registry_key_for):
    sid = _setup(client, api_project, "run-agent")
    ctx = R.base_context(__import__("services.project_registry", fromlist=["x"]).find_project(
        *_db_user(client), "run-agent"))
    R._on_ctx(ctx, campaign.start, "agent objective", 20)
    try:
        assert client.post(f"/api/projects/run-agent/studies/{sid}/run", json={}).status_code == 202
        wait_run(client, "run-agent", sid)
        snap = R._on_ctx(ctx, campaign.status)
        assert snap["active"] is True and snap["spent_solves"] == 5
        assert [e["study"] for e in snap["entries"]] == ["decision_study"]
    finally:
        R._on_ctx(ctx, campaign.end)


def _db_user(client):
    from db.session import SessionLocal
    from db.models import User
    from sqlalchemy import select

    db = SessionLocal()
    user = db.scalars(select(User)).first()
    return db, user


# ── budget ───────────────────────────────────────────────────────────────

def test_a_budget_of_two_refuses_before_the_first_solve(
        client, api_project, studies_on, fake, project_row, registry_key_for):
    sid = _setup(client, api_project, "run-budget")
    r = client.post(f"/api/projects/run-budget/studies/{sid}/run", json={"budget_solves": 2})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["error_kind"] == "campaign_budget_exhausted"
    assert "5 solve(s)" in detail["message"] and "2 of 2 left" in detail["message"]
    assert fake.calls == []
    assert all(v is None for v in _fork_rows(project_row, "run-budget").values())
    ctx = _base_ctx(registry_key_for, "run-budget")
    assert R._on_ctx(ctx, campaign.status)["active"] is False
    assert not (ctx.solver_state.get("decision_study") or {}).get("status")


# ── abort ────────────────────────────────────────────────────────────────

def test_abort_after_the_first_solve_names_the_rest_and_removes_their_forks(
        client, api_project, studies_on, monkeypatch, project_row, project_storage_dir,
        registry_key_for):
    from services import solver_service

    holder = {}

    def after(k):
        if k == 1:
            ctx = PyPSAService.get_context(holder["key"])
            ctx.solver_state["decision_study"]["stop_event"].set()

    solver = FakeSolver(on_call=after)
    monkeypatch.setattr(solver_service, "run_simulation", solver)
    sid = _setup(client, api_project, "run-abort")
    holder["key"] = registry_key_for("run-abort")
    assert client.post(f"/api/projects/run-abort/studies/{sid}/run", json={}).status_code == 202
    rec = wait_run(client, "run-abort", sid)
    assert rec["status"] == "aborted"
    assert len(solver.calls) == 1
    study = client.get(f"/api/projects/run-abort/studies/{sid}").json()
    findings = json.loads((project_storage_dir("run-abort") / study["findings_ref"]).read_text())
    assert findings["options_status"] == "not_established"
    assert findings["pending_options"] == OPTIONS[1:]
    assert findings["completeness"]["options"] == "not_established"
    forks = _fork_rows(project_row, "run-abort")
    assert forks["none"] is not None
    assert all(forks[o] is None for o in OPTIONS[1:]), "unsolved forks must be removed"
    assert study["option_projects"] == [str(forks["none"].id)]
    # Idempotent after the fact.
    r = client.post(f"/api/projects/run-abort/studies/{sid}/run/abort")
    assert r.status_code == 200 and r.json()["aborting"] is False


# ── re-run, mesh, delete ─────────────────────────────────────────────────

def test_a_rerun_replaces_the_forks_without_a_409(
        client, api_project, studies_on, fake, project_row):
    sid = _setup(client, api_project, "run-twice")
    assert client.post(f"/api/projects/run-twice/studies/{sid}/run", json={}).status_code == 202
    wait_run(client, "run-twice", sid)
    first = {o: r.id for o, r in _fork_rows(project_row, "run-twice").items()}
    r = client.post(f"/api/projects/run-twice/studies/{sid}/run", json={})
    assert r.status_code == 202, r.text
    assert wait_run(client, "run-twice", sid)["status"] == "done"
    second = {o: r.id for o, r in _fork_rows(project_row, "run-twice").items()}
    assert all(second[o] is not None and second[o] != first[o] for o in OPTIONS)
    assert len(fake.calls) == 10


def test_a_second_run_while_one_is_live_is_409_through_the_base_context_mesh(
        client, api_project, studies_on, monkeypatch, registry_key_for):
    from services import solver_service

    release = threading.Event()
    solver = FakeSolver(on_call=lambda k: release.wait(30))
    monkeypatch.setattr(solver_service, "run_simulation", solver)
    sid = _setup(client, api_project, "run-mesh")
    try:
        assert client.post(f"/api/projects/run-mesh/studies/{sid}/run", json={}).status_code == 202
        r = client.post(f"/api/projects/run-mesh/studies/{sid}/run", json={})
        assert r.status_code == 409, r.text
        assert "a decision study" in r.json()["detail"]["message"]
        r = client.delete(f"/api/projects/run-mesh/studies/{sid}")
        assert r.status_code == 409
    finally:
        client.post(f"/api/projects/run-mesh/studies/{sid}/run/abort")
        release.set()
    wait_run(client, "run-mesh", sid)


def test_another_study_on_the_base_context_refuses_the_run(
        client, api_project, studies_on, fake, registry_key_for):
    sid = _setup(client, api_project, "run-other")
    ctx = _base_ctx(registry_key_for, "run-other")
    if ctx is None:
        from services import project_registry

        db, user = _db_user(client)
        ctx = R.base_context(project_registry.find_project(db, user, "run-other"))
    release = threading.Event()
    t = threading.Thread(target=release.wait, daemon=True)
    t.start()
    ctx.solver_state["mc"] = {"status": "running", "thread": t}
    try:
        r = client.post(f"/api/projects/run-other/studies/{sid}/run", json={})
        assert r.status_code == 409, r.text
        assert "a sequential-MC study" in r.json()["detail"]["message"]
    finally:
        release.set()
        t.join(5)
        ctx.solver_state["mc"] = None
    assert fake.calls == []


def test_delete_cascades_only_to_verified_forks(
        client, api_project, studies_on, fake, project_row, project_storage_dir):
    sid = _setup(client, api_project, "run-del")
    assert client.post(f"/api/projects/run-del/studies/{sid}/run", json={}).status_code == 202
    wait_run(client, "run-del", sid)
    # A decoy: a user's own scenario of the base, whose uuid is written into
    # the study's list (as a copied or edited record could carry it).
    r = client.post("/api/projects/run-del/scenarios", json={"name": "run-del-mine"})
    assert r.status_code == 201, r.text
    decoy = project_row("run-del-mine")
    path = project_storage_dir("run-del") / "studies" / f"{sid}.json"
    data = json.loads(path.read_text())
    data["option_projects"].append(str(decoy.id))
    path.write_text(json.dumps(data))

    r = client.delete(f"/api/projects/run-del/studies/{sid}")
    assert r.status_code == 204, r.text
    assert all(v is None for v in _fork_rows(project_row, "run-del").values())
    assert project_row("run-del-mine") is not None, "a user project was deleted"
    assert project_storage_dir("run-del-mine").exists()
    assert not path.exists()
    assert not (path.parent / f"{sid}.findings.json").exists()


# ── routes: 404s and the lock ────────────────────────────────────────────

def test_run_routes_404(client, other_org_client, api_project, studies_on, fake):
    sid = _setup(client, api_project, "run-404")
    unknown = "0" * 32
    assert client.post(f"/api/projects/run-404/studies/{unknown}/run", json={}).status_code == 404
    assert client.get(f"/api/projects/run-404/studies/{sid}/run").status_code == 404
    assert client.post(f"/api/projects/run-404/studies/{sid}/run/abort").status_code == 404
    for method, url in (("post", f"/api/projects/run-404/studies/{sid}/run"),
                        ("get", f"/api/projects/run-404/studies/{sid}/run"),
                        ("post", f"/api/projects/run-404/studies/{sid}/run/abort")):
        r = getattr(other_org_client, method)(url, **({"json": {}} if method == "post" and url.endswith("/run") else {}))
        assert r.status_code == 404, (url, r.status_code)
    assert fake.calls == []


def test_every_run_handler_declares_access_and_refuses_when_disabled():
    import inspect

    from routers import studies as S
    from routers.deps import ProjectAccessDep

    for fn in (S.run_study, S.get_study_run, S.abort_study_run):
        params = inspect.signature(fn).parameters
        assert params["project"].default is ProjectAccessDep, fn.__name__
        assert "_refuse_unless_enabled()" in inspect.getsource(fn), fn.__name__
    for fn in (S.run_study, S.abort_study_run):
        assert "_enforce_project_lock(" in inspect.getsource(fn), fn.__name__


def test_bill_is_a_bill_calculator_figure(client, api_project, studies_on, fake):
    sid = _setup(client, api_project, "run-bill")
    client.post(f"/api/projects/run-bill/studies/{sid}/run", json={})
    rec = wait_run(client, "run-bill", sid)
    bill = T.Bill.model_validate(rec["details"]["none"]["bill"])
    assert bill.engine == "bill_calculator" and bill.annual_bill is not None
