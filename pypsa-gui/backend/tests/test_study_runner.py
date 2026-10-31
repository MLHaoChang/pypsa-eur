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
from tests.study_s4_support import (
    INTAKE,
    OPTIONS,
    create_pack_study,
    enable_studies,
    record_engine_solve,
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
        # U2 WP6: the pack writes no Link price; the import price is the one
        # the engine materialises from the fork's commercial config. U2 WP8:
        # and the engine's solve record, so the fork is valued and billed by
        # the engine like a real solve's (`record_engine_solve`).
        site_price = record_engine_solve(n, config.commercial)
        n.buses_t.marginal_price = pd.DataFrame(
            {"site": site_price, "grid": 0.0}, index=n.snapshots)
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
        # U2 WP6 (C6): the engine's commercial config, no GS demand charge.
        assert not hasattr(cfg, "demand_charge")   # U2 WP10: the field is gone
        assert cfg.commercial["poc_link"] == "grid_import"
        assert [i["unit"] for i in cfg.commercial["import_tariff"]["items"]
                if i["id"] == "demand"] == ["per_kw_month"]
        assert cfg.discount_rate == 0.07
    storage_by_fork = {c[0]: c[2] for c in fake.calls}
    assert storage_by_fork["run-five-opt-none"] == 0          # omitted, not zeroed
    assert all(storage_by_fork[f"run-five-opt-{o}"] == 1 for o in OPTIONS if o != "none")
    for o, row in forks.items():
        meta = json.loads((project_storage_dir(row.name) / "metadata.json").read_text())
        assert meta["owner_study_id"] == sid, o               # survived the queue save
        assert not (project_storage_dir(row.name) / "studies").exists()

    # The campaign: charged on the BASE context, five solves, then closed.
    # The decision study captures no network revision (LC-37 captures one only
    # for the chat-gated studies), so its freshness is unknown, never False.
    assert rec["campaign"]["entries"] == [
        {"study": "decision_study", "solves_charged": 5, "at": rec["campaign"]["entries"][0]["at"],
         "network_revision": None, "edited_since_study": None}]
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
    Gate S3 [S4]/N4, U2 WP6: the run's `demand_charge_eur` is the amount the
    ENGINE's solve committed (`engine_adapter.demand_charge_eur`), never GS's
    bridge term. U2 WP8: the fake solver leaves the engine's solve record
    (its peaks read from the written dispatch), so the committed amount is
    established and equals the engine bill's demand line; a record without
    the peaks is null with the reason (ADR-0001, never 0). The real engine
    solve is `test_u2_wp6_lp_switch.py::
    test_the_run_records_the_engines_committed_demand_charge`.
    """
    sid = _setup(client, api_project, "run-bridge")
    assert client.post(f"/api/projects/run-bridge/studies/{sid}/run", json={}).status_code == 202
    rec = wait_run(client, "run-bridge", sid)
    assert rec["export_series"]["id"].endswith(f":{sid}:export")
    for o in OPTIONS:
        d = rec["details"][o]
        assert d["bill"]["by_component"]["demand"] > 0, o
        assert d["demand_charge_eur"] == pytest.approx(d["bill"]["by_component"]["demand"],
                                                       rel=1e-12), o
        assert "demand_charge_unavailable" not in d, o


def test_a_solve_without_the_engines_peaks_records_no_demand_charge(monkeypatch):
    """ADR-0001: no committed peak, no amount — null with the reason, never 0."""
    from services.study import engine_adapter as A
    from services.study import packs
    from services.study import questions as Q
    from tests.golden import site_fixture as sf
    from tests.u2_targets import bound_option

    ledger = sf.site_ledger()
    m, compiled = bound_option(sf.site_intake(), ledger, "none", sf.site_library())
    load = m.loads_t.p_set["site_load"]
    m.links_t.p0 = pd.DataFrame({"grid_import": load, "grid_export": 0.0}, index=m.snapshots)
    assert A.demand_charge_eur(m, compiled) is None      # dispatched, never solved
    d = R._read_option(m, packs.option_solver_config(ledger, compiled),
                       Q.option(Q.BESS_AT_SITE, "none"), "full_study", 2020, compiled=compiled)
    assert d["demand_charge_eur"] is None
    assert d["demand_charge_unavailable"] == "demand_charge_not_established"


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


# ── an accepted run never reads as "never run" ───────────────────────────
#
# CI, 13 Oct (#177, #164): `qa_decision_study` aborted its second run, and
# `GET /run` then answered 404 `study_never_run` until the driver gave up, with
# no findings. The run had been accepted (202), so the only way to that state
# is a `_finish` that raised: the worker published its fallback "failed" on the
# live record (which `GET /run` only serves while running) and nothing was
# written to disk. Removing an unsolved fork caught `ForkError` alone, so any
# other error there (an `OSError` from the tree removal, a locked database)
# sank the whole record.

def _abort_after_the_first_solve(client, api_project, monkeypatch, registry_key_for, base):
    from services import solver_service

    holder = {}

    def after(k):
        if k == 1:
            ctx = PyPSAService.get_context(holder["key"])
            ctx.solver_state["decision_study"]["stop_event"].set()

    monkeypatch.setattr(solver_service, "run_simulation", FakeSolver(on_call=after))
    sid = _setup(client, api_project, base)
    holder["key"] = registry_key_for(base)
    assert client.post(f"/api/projects/{base}/studies/{sid}/run", json={}).status_code == 202
    return sid


def test_a_fork_that_will_not_go_away_does_not_sink_the_runs_record(
        client, api_project, studies_on, monkeypatch, project_storage_dir, registry_key_for):
    import routers.projects as projects_router

    def stuck(target):
        raise OSError(39, "Directory not empty", str(target))

    monkeypatch.setattr(projects_router, "_force_rmtree", stuck)
    sid = _abort_after_the_first_solve(client, api_project, monkeypatch, registry_key_for,
                                       "rm-stuck")
    rec = wait_run(client, "rm-stuck", sid)
    assert rec["status"] == "aborted"
    assert rec["pending"] == OPTIONS[1:]
    assert rec["fork_removal_refused"] == ["fork_removal_failed:OSError"] * len(OPTIONS[1:])
    study = client.get(f"/api/projects/rm-stuck/studies/{sid}").json()
    assert study["findings_ref"] is not None
    findings = json.loads((project_storage_dir("rm-stuck") / study["findings_ref"]).read_text())
    assert findings["pending_options"] == OPTIONS[1:]


def test_a_run_whose_finish_fails_reads_failed_not_never_run(
        client, api_project, studies_on, monkeypatch, registry_key_for):
    def boom(*_a, **_k):
        raise RuntimeError("the findings could not be built")

    monkeypatch.setattr(R.run_hashes, "intake_hash", boom)
    sid = _abort_after_the_first_solve(client, api_project, monkeypatch, registry_key_for,
                                       "fin-fails")
    rec = wait_run(client, "fin-fails", sid)
    assert rec["status"] == "failed"
    assert "finishing the run failed" in rec["error"]
    assert "the findings could not be built" in rec["error"]
    assert rec["solved"] == ["none"] and rec["pending"] == OPTIONS[1:]
    # The abort route reads the same record: the run is over, not unknown.
    r = client.post(f"/api/projects/fin-fails/studies/{sid}/run/abort")
    assert r.status_code == 200 and r.json() == {"status": "failed", "aborting": False}
    assert PyPSAService._study_owned == set(), sorted(PyPSAService._study_owned)


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


def test_every_option_bill_is_the_engines_on_its_solved_meter(
        client, api_project, studies_on, fake, project_storage_dir):
    """
    U2 WP8, gate C6: every option bill the run records is
    `engine_adapter.bill` on the fork the engine solved, with the compiled
    config that solve priced (read back from the fork's own solver config):
    the tariff engine's, its total established.
    """
    import pypsa

    from models.commercial import CommercialConfig
    from models.study import Bill
    from services.study import compile as C
    from services.study import engine_adapter as A

    sid = _setup(client, api_project, "run-bill")
    client.post(f"/api/projects/run-bill/studies/{sid}/run", json={})
    rec = wait_run(client, "run-bill", sid)
    for o in OPTIONS:
        bill = Bill.model_validate(rec["details"][o]["bill"])
        assert bill.engine == "tariff_engine" and bill.annual_bill is not None, (o, bill.unavailable)
        fork = project_storage_dir(f"run-bill-opt-{o}")
        n = pypsa.Network(str(fork / "network.nc"))
        cfg = json.loads((fork / "solver_config.json").read_text())
        compiled = C.CompiledCommercial(
            config=CommercialConfig.model_validate(cfg["commercial"]),
            item_component={"energy": "energy", "network:energy:0": "network",
                            "demand": "demand", "fixed": "fixed"},
            tariff_meta={"currency": "EUR", "currency_year": 2020, "billing_period": "month"})
        again = A.bill(n, compiled)
        assert again.total == pytest.approx(bill.total, rel=1e-12), o
        for key in ("energy", "demand", "fixed", "network", "export_credit"):
            assert getattr(again.by_component, key) == pytest.approx(
                getattr(bill.by_component, key), rel=1e-12, abs=1e-9), (o, key)


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


# ── U2 WP8: the production run on the engine and the pack (real LP) ───────

@pytest.mark.live_solve
def test_the_production_run_bills_on_the_engine_and_seeds_from_the_pack(
        client, api_project, studies_on, project_row, project_storage_dir):
    """
    Gates C6 and C4 on the site golden through the production runner and the
    queue (the real LP, PV off): every bill the run records is the tariff
    engine's on its solved fork, established, and equals WP0's (GS's bill of
    the same dispatch) at the frozen tolerances; each fork's solver config
    carries the finance inputs beside the commercial config; the study's
    ledger is the pinned pack's, its rule rows (21-34) name the guided study
    as their author, and the case reads them (no fallback to the guided
    defaults) and equals WP0's NPV.
    """
    from services.study import library as L
    from tests.golden import site_fixture as SF
    from tests.u2_targets import DE, WP0, close

    intake = {**SF.site_intake(), "pv": {"enabled": False}}
    api_project("wp8-run-src")
    r = create_pack_study(client, "wp8-run-src", "wp8-run", intake=intake)
    assert r.status_code == 201, r.text
    sid = r.json()["study_id"]
    ledger = client.get(f"/api/projects/wp8-run/studies/{sid}/ledger").json()["ledger"]
    assert ledger["ledger_version"] == L.load_defaults().version
    rows = {row["key"]: row for row in ledger["rows"]}
    assert rows["salvage_rule"]["source"] == (
        f"guided study rule guided.salvage.annuity_pv_remaining_life; not in pack "
        f"{L.load_defaults().version}")
    assert client.post(f"/api/projects/wp8-run/studies/{sid}/run", json={}).status_code == 202
    rec = wait_run(client, "wp8-run", sid, timeout=1200.0)
    assert rec["status"] == "done", rec
    run = client.get(f"/api/projects/wp8-run/studies/{sid}/run").json()
    # Money-year 01: the pin's seed tariff is WP0's with every money figure x
    # the manifest's exact factor, and the technology rows keep their values,
    # so the LP dispatches as in WP0 and each bill component is WP0's x factor.
    from decimal import Decimal

    from services.library.defaults_pack.loader import VERSIONS_DIR

    manifest = json.loads((VERSIONS_DIR / L.PACK_VERSION / "manifest.json").read_text("utf-8"))
    factor = Decimal(manifest["money_year_conversion"]["factor"])

    def in_pin_money(v):
        return float(Decimal(repr(v)) * factor)

    tol = WP0["tolerances"]
    for option in ("none", "bess_2h"):
        bill = run["details"][option]["bill"]
        assert bill["engine"] == "tariff_engine" and bill["unavailable"] == {}, bill["unavailable"]
        assert bill["currency_year"] == L.load_defaults().pack.finance.currency_year
        want = WP0["seed_bills"][DE][option]
        assert close(bill["total"], in_pin_money(want["total"]), rel=tol["arithmetic_rel"])
        for key, value in want["by_component"].items():
            if key != "unavailable":
                assert close(bill["by_component"][key], in_pin_money(value),
                             rel=tol["arithmetic_rel"], abs_=tol["arithmetic_abs"]), (option, key)
        cfg = json.loads((project_storage_dir(f"wp8-run-opt-{option}")
                          / "solver_config.json").read_text())
        assert cfg["finance"]["price_basis"] == "real" and cfg["commercial"]["value_flows"]
    case = client.get(f"/api/projects/wp8-run/studies/{sid}/options/bess_2h/case").json()
    assert case["engine"] == "finance_engine" and case["status"] == "ok"
    assert "finance_rules_from_guided_defaults" not in case["honesty_notes"]
    # WP0's NPV re-priced on the pin: the bill savings x factor, the capex, FOM,
    # replacements and salvage (technology rows, values kept) as WP0's. The
    # same cash flows at x1 rebuild WP0's own NPV, which pins the rebuild.
    g = WP0["golden_s5"]["bess_2h"]
    rate, years = g["discount_rate"], g["horizon_years"]

    def npv(savings):
        flows = [-g["capex_total"]] + [savings - g["fom_annual"] - g["vom_annual"]] * years
        for year, cost in g["replacements"].items():
            flows[int(year)] -= cost
        flows[years] += g["salvage_eur"]
        return sum(v / (1 + rate) ** t for t, v in enumerate(flows))

    assert close(npv(g["savings_annual"]), g["npv"], rel=1e-9)
    assert close(case["kpis"]["capex_total"], g["capex_total"], rel=1e-9)
    assert close(case["kpis"]["npv"], npv(in_pin_money(g["savings_annual"])), rel=1e-9)


def test_a_study_seeded_from_the_legacy_library_still_loads_and_runs(
        client, api_project, studies_on, fake, project_storage_dir):
    """
    U2 WP8 (gate C4): new studies seed from the pinned pack, but a study
    stored with a ledger seeded from the legacy `study_library` (rows 1-20,
    no rule rows 21-34) still reads, runs and values its cases — on the SAME
    guided rules, disclosed `finance_rules_from_guided_defaults` — and a
    re-seed moves it onto the pack, keeping the user's rows.
    """
    from services.study import library as L
    from services.study import store

    sid = _setup(client, api_project, "run-legacy")
    base = project_storage_dir("run-legacy")
    study = store.load_study(base, sid)
    # U2 WP10: the legacy library is deleted; its ledger is frozen (gate C8 a).
    from tests.u2_targets import legacy_ledger

    legacy = legacy_ledger("de")
    assert legacy.ledger_version == L.LIBRARY_VERSION
    tariff_id = (study.intake.get("tariff") or {}).get("tariff_id", "de_industrial_illustrative")
    assert next(r for r in legacy.rows if r.key == "tariff").technical_name == tariff_id
    # Stored before money-year 01, the study's money was the ledger's (2020).
    store.save_study(base, study.model_copy(update={"ledger": legacy,
                                                    "ledger_version": legacy.ledger_version,
                                                    "currency_year": 2020}))
    led = client.get(f"/api/projects/run-legacy/studies/{sid}/ledger").json()["ledger"]
    assert led["ledger_version"] == L.LIBRARY_VERSION
    r = client.put(f"/api/projects/run-legacy/studies/{sid}/ledger", json={"rows": [
        {"key": "battery_storage_eur_per_kwh", "value": 300.0, "unit": "EUR/kWh"}]})
    assert r.status_code == 200, r.text
    assert client.post(f"/api/projects/run-legacy/studies/{sid}/run", json={}).status_code == 202
    assert wait_run(client, "run-legacy", sid)["status"] == "done"
    case = client.get(f"/api/projects/run-legacy/studies/{sid}/options/bess_2h/case")
    assert case.status_code == 200, case.text
    assert case.json()["engine"] == "finance_engine"
    assert "finance_rules_from_guided_defaults" in case.json()["honesty_notes"]

    r = client.put(f"/api/projects/run-legacy/studies/{sid}/ledger", json={"reseed": True})
    assert r.status_code == 200, r.text
    led = r.json()["ledger"]
    assert led["ledger_version"] == L.load_defaults().version
    rows = {row["key"]: row for row in led["rows"]}
    assert rows["battery_storage_eur_per_kwh"]["value"] == 300.0
    assert rows["battery_storage_eur_per_kwh"]["provenance"] == "user"
    assert rows["salvage_rule"]["source"].startswith("guided study rule ")
    # The ledger the run was built on moved: the case says so, never mixes.
    case = client.get(f"/api/projects/run-legacy/studies/{sid}/options/bess_2h/case")
    assert case.status_code == 409
    assert case.json()["detail"]["error_kind"] == "ledger_changed_since_run"


def test_a_study_seeded_on_an_older_pack_runs_on_its_own_tariff(
        client, api_project, studies_on, fake, project_row, project_storage_dir):
    """
    Money-year issue 01 (GK B condition 3, plan A C3/C4): a study stored
    with a ledger seeded on 2026-10-05 (2020 EUR), or with a legacy
    `LIBRARY_VERSION` one, still runs and values its cases after the pin
    moved to the 2025 pack. Its seed tariff is read from the pack the ledger
    names (`library.library_for_ledger`), never the pin's 2025 one: nothing
    is refused ``currency_year_mixed``, and the run's bill is the one the
    2026-10-05 defaults give on the same solved meter, while a ledger seeded
    on the pin bills exactly the HICP factor more.
    """
    import json
    from decimal import Decimal

    from services.library.defaults_pack.loader import VERSIONS_DIR
    from services.study import engine_adapter as A
    from services.study import library as L
    from services.study import packs
    from services.study import questions as Q
    from services.study import runner as R
    from services.study import store
    from tests.u2_targets import legacy_ledger

    manifest = json.loads((VERSIONS_DIR / L.PACK_VERSION / "manifest.json").read_text("utf-8"))
    factor = float(Decimal(manifest["money_year_conversion"]["factor"]))
    old, pin = L.load_defaults(L.WP0_PACK_VERSION), L.load_defaults()
    for name in ("run-1005", "run-legacy-lv"):
        sid = _setup(client, api_project, name)
        base = project_storage_dir(name)
        study = store.load_study(base, sid)
        question = Q.get_question(study.question_id)
        if name == "run-1005":
            ledger = L.seed_ledger(question, study.intake, old)
        else:
            ledger = legacy_ledger("de")
            assert ledger.ledger_version == L.LIBRARY_VERSION
        store.save_study(base, study.model_copy(update={
            "ledger": ledger, "ledger_version": ledger.ledger_version, "currency_year": 2020}))
        r = client.post(f"/api/projects/{name}/studies/{sid}/run", json={})
        assert r.status_code == 202, r.text
        rec = wait_run(client, name, sid)
        assert rec["status"] == "done", rec
        run = client.get(f"/api/projects/{name}/studies/{sid}/run").json()
        bill = run["details"]["none"]["bill"]
        assert bill["unavailable"] == {}, bill["unavailable"]
        case = client.get(f"/api/projects/{name}/studies/{sid}/options/bess_2h/case")
        assert case.status_code == 200, case.text
        assert case.json()["engine"] == "finance_engine"
        if name != "run-1005":
            continue
        n, _hash = R.fork_network_from_disk(project_row(f"{name}-opt-none"))

        def components(led, lib, n=n, intake=study.intake):
            c = A.bill(n, packs.option_commercial(
                intake, led, lib, n.snapshots, export_series=run.get("export_series")))
            return {k: getattr(c.by_component, k) for k in A.BILL_COMPONENT_KEYS}

        on_old = components(ledger, old)
        on_pin = components(L.seed_ledger(question, study.intake, pin), pin)
        assert any(v for v in on_old.values())
        for k, v in on_old.items():
            if v is None:
                assert on_pin[k] is None, k
                continue
            assert bill["by_component"][k] == pytest.approx(v, rel=1e-9, abs=1e-9), k
            assert on_pin[k] == pytest.approx(v * factor, rel=1e-9, abs=1e-9), k


def test_a_reset_row_on_an_older_ledger_comes_back_in_its_own_money_year(
        client, api_project, studies_on, fake, project_storage_dir):
    """
    Money-year issue 01 (GK B plan A C1): resetting rows of a stored ledger
    seeded on 2026-10-05 brings them back from 2026-10-05 (2020 EUR), never
    the pin's 2025 rows into a 2020 ledger; the ledger keeps its version and
    still runs. A re-seed in the same request resets from the re-seeded
    ledger's defaults (the pin).
    """
    from services.study import library as L
    from services.study import questions as Q
    from services.study import store

    keys = ("battery_storage_eur_per_kwh", "demand_charge_price")
    sid = _setup(client, api_project, "reset-1005")
    base = project_storage_dir("reset-1005")
    study = store.load_study(base, sid)
    question = Q.get_question(study.question_id)
    old, pin = (L.seed_ledger(question, study.intake, lib)
                for lib in (L.load_defaults(L.WP0_PACK_VERSION), L.load_defaults()))
    seed = {r.key: r for r in old.rows if r.key in keys}
    # The pin relabels the battery row 2025 (MONEY-YEAR-1) and converts the demand charge.
    assert all((seed[k].value, seed[k].currency_year) != next(
        (r.value, r.currency_year) for r in pin.rows if r.key == k) for k in keys)
    store.save_study(base, study.model_copy(update={
        "ledger": old, "ledger_version": old.ledger_version, "currency_year": 2020}))
    url = f"/api/projects/reset-1005/studies/{sid}/ledger"
    r = client.put(url, json={"rows": [{"key": k, "value": 1.0, "unit": seed[k].unit} for k in keys]})
    assert r.status_code == 200, r.text
    r = client.put(url, json={"reset": list(keys)})
    assert r.status_code == 200, r.text
    led = r.json()["ledger"]
    assert led["ledger_version"] == old.ledger_version
    rows = {row["key"]: row for row in led["rows"]}
    for k in keys:
        assert rows[k]["value"] == seed[k].value and rows[k]["provenance"] == "library", k
        assert rows[k]["currency_year"] == seed[k].currency_year == 2020, k
    assert client.post(f"/api/projects/reset-1005/studies/{sid}/run", json={}).status_code == 202
    assert wait_run(client, "reset-1005", sid)["status"] == "done"
    case = client.get(f"/api/projects/reset-1005/studies/{sid}/options/bess_2h/case")
    assert case.status_code == 200, case.text

    r = client.put(url, json={"reseed": True, "reset": list(keys)})
    assert r.status_code == 200, r.text
    led = r.json()["ledger"]
    assert led["ledger_version"] == pin.ledger_version
    rows = {row["key"]: row for row in led["rows"]}
    for k in keys:
        want = next(r for r in pin.rows if r.key == k)
        assert (rows[k]["value"], rows[k]["currency_year"]) == (want.value, want.currency_year), k
    # The study stays in 2020 (follow-up issue): its case is refused, and the
    # refusal names the setting that fixes it (GK B, money-year 01).
    assert client.post(f"/api/projects/reset-1005/studies/{sid}/run", json={}).status_code == 202
    assert wait_run(client, "reset-1005", sid)["status"] == "done"
    case = client.get(f"/api/projects/reset-1005/studies/{sid}/options/bess_2h/case")
    assert case.status_code == 422, case.text
    detail = case.json()["detail"]
    assert detail["error_kind"] == "currency_year_mixed"
    assert "set the study's currency year to 2025 in its settings" in detail["message"]


def test_a_ledger_naming_an_unshipped_pack_is_refused_never_read_on_the_pin(
        client, api_project, studies_on, fake, project_storage_dir):
    """
    Money-year issue 01 (GK B plan A C2): a stored ledger whose defaults
    this build does not ship is refused by name (run 409, ledger read 500),
    never priced on the pin.
    """
    from services.study import store

    sid = _setup(client, api_project, "unknown-pack")
    base = project_storage_dir("unknown-pack")
    study = store.load_study(base, sid)
    label = "generic-defaults 2099-01-01 000000000000"
    ledger = study.ledger.model_copy(update={"ledger_version": label})
    store.save_study(base, study.model_copy(update={"ledger": ledger, "ledger_version": label}))
    r = client.post(f"/api/projects/unknown-pack/studies/{sid}/run", json={})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["error_kind"] == "ledger_defaults_unknown"
    assert label in r.json()["detail"]["message"]
    r = client.put(f"/api/projects/unknown-pack/studies/{sid}/ledger",
                   json={"reset": ["battery_storage_eur_per_kwh"]})
    assert r.status_code == 500, r.text
    assert r.json()["detail"]["code"] == "study_library_unreadable"
    assert label in r.json()["detail"]["message"]
