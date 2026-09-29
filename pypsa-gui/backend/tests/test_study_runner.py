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
    INTAKE,
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
        # What a real solve leaves behind that the result reads need: the
        # storage dispatch and the site bus price (asset economics reads both).
        n.buses_t.marginal_price = pd.DataFrame(
            {"site": n.links_t.marginal_cost["grid_import"], "grid": 0.0},
            index=n.snapshots)
        gen_p = {"grid": n.links_t.p0["grid_import"]}
        if "pv" in n.generators.index:
            gen_p["pv"] = 0.2 * n.generators_t.p_max_pu["pv"]
        n.generators_t.p = pd.DataFrame(gen_p, index=n.snapshots)
        if storage:
            n.storage_units["p_nom_opt"] = 0.3
            n.storage_units_t.p = pd.DataFrame(
                {"battery": [0.1 if i % 2 else -0.1 for i in range(len(n.snapshots))]},
                index=n.snapshots)
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
    # Refused before anything was registered: no base context, so no
    # campaign and no study record anywhere (gate S4 BC-S4-1).
    assert _base_ctx(registry_key_for, "run-budget") is None


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


# ── gate S4 binding conditions ───────────────────────────────────────────

def _names(session_local):
    from sqlalchemy import select

    from db.models import Project

    with session_local() as db:
        return {str(p.id): p.name for p in db.scalars(select(Project)).all()}


@pytest.mark.parametrize("path", ["full_run", "refused_run"])
def test_a_run_at_the_resident_cap_changes_no_user_project(
        client, api_project, studies_on, fake, _auth_db, registry_key_for,
        project_storage_dir, path):
    """
    BC-S4-1: no registration made for a study (the base context, each fork
    the queue registers) may evict — and so write back — a user context.
    Five visited user projects fill the cap; one holds an unsaved edit.
    """
    import pypsa

    from tests.study_s4_support import all_project_dirs, dir_hash

    _e, session_local = _auth_db
    assert PyPSAService.RESIDENT_CAP == 5
    for i in range(5):
        api_project(f"cap-user{i}")
    api_project("cap-src")
    edited = PyPSAService.get_context(registry_key_for("cap-user1"))
    assert edited is not None, "the fixture needs cap-user1 resident"
    edited.network.add("Bus", "unsaved_edit_bus")
    r = create_pack_study(client, "cap-src", "cap-base")
    assert r.status_code == 201, r.text
    sid = r.json()["study_id"]
    names = _names(session_local)
    before = {k: dir_hash(d) for k, d in all_project_dirs(session_local).items()
              if names[k] != "cap-base"}
    resident_before = set(PyPSAService._contexts)

    if path == "full_run":
        assert client.post(f"/api/projects/cap-base/studies/{sid}/run", json={}).status_code == 202
        assert wait_run(client, "cap-base", sid)["status"] == "done"
    else:
        r = client.post(f"/api/projects/cap-base/studies/{sid}/run", json={"budget_solves": 2})
        assert r.status_code == 409, r.text
        assert fake.calls == []
        # The checks come BEFORE any registration.
        assert registry_key_for("cap-base") not in PyPSAService._contexts

    after = all_project_dirs(session_local)
    changed = [names[k] for k, d in before.items() if dir_hash(after[k]) != d]
    assert changed == [], f"user projects rewritten by a study run: {changed}"
    assert resident_before <= set(PyPSAService._contexts), "a user context was evicted"
    still = PyPSAService.get_context(registry_key_for("cap-user1"))
    assert still is edited and "unsaved_edit_bus" in still.network.buses.index
    on_disk = pypsa.Network(str(project_storage_dir("cap-user1") / "network.nc"))
    assert "unsaved_edit_bus" not in on_disk.buses.index


def test_findings_record_the_ledger_the_forks_were_built_from(
        client, api_project, studies_on, monkeypatch, project_storage_dir):
    """BC-S4-2: a ledger edit during a run is never attributed to its results."""
    import pypsa

    from services import solver_service

    holder = {}

    def after(k):
        if k == 1:
            holder["put"] = client.put(
                f"/api/projects/lh-base/studies/{holder['sid']}/ledger",
                json={"rows": [{"key": "battery_storage_eur_per_kwh", "value": 100.0,
                                "unit": "EUR/kWh"}]}).status_code

    monkeypatch.setattr(solver_service, "run_simulation", FakeSolver(on_call=after))
    api_project("lh-src")
    sid = create_pack_study(client, "lh-src", "lh-base").json()["study_id"]
    holder["sid"] = sid
    assert client.post(f"/api/projects/lh-base/studies/{sid}/run", json={}).status_code == 202
    wait_run(client, "lh-base", sid)
    assert holder["put"] == 200
    study = client.get(f"/api/projects/lh-base/studies/{sid}").json()
    findings = json.loads((project_storage_dir("lh-base") / study["findings_ref"]).read_text())
    n = pypsa.Network(str(project_storage_dir("lh-base-opt-bess_2h") / "network.nc"))
    assert findings["hashes"]["ledger_hash"] == n.meta["decision_study_pack"]["ledger_hash"]
    assert study["stale"] is True
    assert "ledger_changed_during_run" in study["stale_reasons"]


def test_the_budget_follows_the_current_option_count(
        client, api_project, studies_on, fake):
    """BC-S4-3: enabling PV after creation adds an option; the run still runs."""
    api_project("pv-src")
    intake = {**INTAKE, "pv": {"enabled": False}}
    r = create_pack_study(client, "pv-src", "pv-base", intake=intake)
    sid = r.json()["study_id"]
    assert r.json()["budget"]["solves_max"] == 4
    r = client.patch(f"/api/projects/pv-base/studies/{sid}", json={
        "step": "pv", "intake": {"pv": {"enabled": True, "kind": "rooftop"}}})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/projects/pv-base/studies/{sid}/run", json={})
    assert r.status_code == 202, r.text
    assert r.json()["solves_charged"] == 5
    assert wait_run(client, "pv-base", sid)["status"] == "done"
    assert len(fake.calls) == 5


def test_the_battery_asset_economics_arrive_with_fom(
        client, api_project, studies_on, fake):
    """BC-S4-5: read under the real per-class keys, never an empty list."""
    sid = _setup(client, api_project, "run-econ")
    client.post(f"/api/projects/run-econ/studies/{sid}/run", json={})
    rec = wait_run(client, "run-econ", sid)
    econ = rec["details"]["bess_2h"]["asset_economics"]
    assert econ is not None, rec["details"]["bess_2h"].get("asset_economics_unavailable")
    [battery] = econ["storage_units"]
    assert battery["name"] == "battery"
    assert battery["fom_cost_eur"] == pytest.approx(0.3 * 213.9279 * 1000 * 0.3375 / 100, rel=1e-3)
    assert [g["name"] for g in rec["details"]["bess_pv_2h"]["asset_economics"]["generators"]] == ["pv"]


def test_a_size_at_its_bound_is_flagged_by_the_shared_classifier(
        client, api_project, studies_on, monkeypatch):
    from services import solver_service

    class AtBound(FakeSolver):
        def __call__(self, config, n, *a, **k):
            out = super().__call__(config, n, *a, **k)
            if len(n.storage_units):
                n.storage_units["p_nom_opt"] = n.storage_units["p_nom_max"]
            return out

    monkeypatch.setattr(solver_service, "run_simulation", AtBound())
    sid = _setup(client, api_project, "run-bound")
    client.post(f"/api/projects/run-bound/studies/{sid}/run", json={})
    rec = wait_run(client, "run-bound", sid)
    assert "size_at_upper_bound:battery" in rec["details"]["bess_1h"]["caveats"]
    assert rec["details"]["bess_1h"]["sizing"]["battery"]["binding_constraint"] == "at_upper_bound"
    assert rec["details"]["none"]["caveats"] == []


# ── S4 re-gate binding conditions (BC-S4-v2-1, BC-S4-v2-2) ────────────────
# A run registers its base project on the study's behalf, exempt from the
# resident cap. When the run ends (done, refused after registering, or the
# worker never starting) that registration must be undone: the exemption
# lifted AND the context dropped, unless a SESSION has the project open.
# The worker thread inherits the request's context, which is bound to the
# base, so "open" cannot be read from the request scope.

def _left_behind(registry_key_for, base):
    key = registry_key_for(base)
    return {"exempt": key in PyPSAService._study_owned,
            "resident": key in PyPSAService._contexts}


def _user_contexts():
    return sum(1 for k in PyPSAService._contexts if k not in PyPSAService._study_owned)


def test_a_finished_run_leaves_nothing_exempt_or_loaded(
        client, api_project, studies_on, fake, registry_key_for):
    sid = _setup(client, api_project, "rel-done")
    assert client.post(f"/api/projects/rel-done/studies/{sid}/run", json={}).status_code == 202
    assert wait_run(client, "rel-done", sid)["status"] == "done"
    assert PyPSAService._study_owned == set(), sorted(PyPSAService._study_owned)
    assert _left_behind(registry_key_for, "rel-done") == {"exempt": False, "resident": False}
    assert _user_contexts() <= PyPSAService.RESIDENT_CAP


def test_a_run_refused_when_its_campaign_cannot_start_releases_the_base(
        client, api_project, studies_on, fake, monkeypatch, registry_key_for):
    sid = _setup(client, api_project, "rel-camp")

    def refuse(*_a, **_k):
        raise campaign.CampaignError("no campaign today")

    monkeypatch.setattr(campaign, "start", refuse)
    r = client.post(f"/api/projects/rel-camp/studies/{sid}/run", json={})
    assert r.status_code == 422, r.text
    assert fake.calls == []
    assert _left_behind(registry_key_for, "rel-camp") == {"exempt": False, "resident": False}


def test_a_run_whose_worker_cannot_start_releases_the_base(
        client, api_project, studies_on, fake, monkeypatch, registry_key_for):
    sid = _setup(client, api_project, "rel-thread")
    original = threading.Thread.start

    def start(self):
        if self.name.startswith("decision-study-"):
            raise RuntimeError("cannot start a thread")
        return original(self)

    monkeypatch.setattr(threading.Thread, "start", start)
    with pytest.raises(RuntimeError):
        client.post(f"/api/projects/rel-thread/studies/{sid}/run", json={})
    assert fake.calls == []
    assert _left_behind(registry_key_for, "rel-thread") == {"exempt": False, "resident": False}


def test_a_run_reports_done_only_after_it_has_released_everything(
        client, api_project, studies_on, fake, monkeypatch, registry_key_for):
    """
    Gate S6 (full-suite failure of the test above): the run published its
    terminal status BEFORE its `finally` released the base, so a poll could
    read "done" while the base was still exempt from the cap. Widen that
    window (a slow session probe inside the release) and read at once.
    """
    import time

    slow = PyPSAService._session_active_keys.__func__

    def probe(cls):
        time.sleep(1.0)
        return slow(cls)

    monkeypatch.setattr(PyPSAService, "_session_active_keys", classmethod(probe))
    sid = _setup(client, api_project, "rel-late")
    assert client.post(f"/api/projects/rel-late/studies/{sid}/run", json={}).status_code == 202
    assert wait_run(client, "rel-late", sid)["status"] == "done"
    assert PyPSAService._study_owned == set(), sorted(PyPSAService._study_owned)
    assert _left_behind(registry_key_for, "rel-late") == {"exempt": False, "resident": False}


class _WatchedSet(set):
    """Records, at each discard, whether `_registry_lock` was held."""

    def __init__(self, *a):
        super().__init__(*a)
        self.discards: list[bool] = []

    def discard(self, key):
        self.discards.append(PyPSAService._registry_lock._is_owned())
        super().discard(key)


@pytest.mark.parametrize("in_use", [False, True])
def test_releasing_the_base_unmarks_and_drops_in_one_critical_section(monkeypatch, in_use):
    """
    Gate S6 re-gate BC-S6-v2-2: between a separate unmark and drop the base
    is resident but no longer exempt, so a concurrent registration at the cap
    could evict it and write it back (BC-S4-1 in a two-statement window).
    Both must happen under ONE hold of `_registry_lock`: the drop runs with
    the lock held and finds the mark already gone.
    """
    key = "org:release-atomic"
    watched = _WatchedSet()
    monkeypatch.setattr(PyPSAService, "_study_owned", watched)
    monkeypatch.setattr(PyPSAService, "_session_active_keys",
                        classmethod(lambda cls: {key} if in_use else set()))
    seen: list[tuple[bool, bool]] = []
    real_drop = PyPSAService.drop.__func__

    def drop(cls, project_id):
        seen.append((cls._registry_lock._is_owned(), project_id in cls._study_owned))
        real_drop(cls, project_id)

    monkeypatch.setattr(PyPSAService, "drop", classmethod(drop))
    PyPSAService.mark_study_owned(key)
    R._release_base(key, True)
    assert key not in PyPSAService._study_owned
    assert watched.discards == [True]
    assert seen == ([] if in_use else [(True, False)])
