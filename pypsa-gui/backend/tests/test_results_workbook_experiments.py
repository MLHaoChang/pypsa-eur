"""
The results workbook's experiment cases (results-workbook 02, R5, RW-SCEN-1):
each completed network case of the acting user's own experiments on the open
base is a Scenarios column, read from its own saved network only after that
network's exact input fingerprint and its run evidence match the case's
receipt. The ledger copies are read under `experiments._LOCK`; every case
network is loaded after it is released.

The base (tests.test_workflow_tools.baseline) serves 5 MW for 2 h from G1 at
10 EUR/MWh; a case at c EUR/MWh has opex = 10 c EUR (the hand oracle).
"""
# ruff: noqa: F811 -- imported pytest fixtures
from __future__ import annotations

import pytest

from services.finance.export_xlsx import NOT_ESTABLISHED
from services.results_workbook import scenarios as S
from tests.test_experiment_comparison import completed_case
from tests.test_results_workbook_scenarios import _export, _header, _kpi_row, _sheet, _texts
from tests.test_sensitivity_comparison import opex, solve
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401

OPEX = "Opex (M EUR), horizon"


@pytest.fixture
def solved_base(baseline):
    _ctx, base_id, root = baseline
    solve(base_id)
    return base_id, root


def _runs(ws) -> dict[str, str]:
    """Run name → its Results text, from the sheet's run table."""
    head = next(r for r in range(1, ws.max_row + 1) if ws.cell(r, 1).value == "Run")
    out = {}
    for r in range(head + 1, ws.max_row + 1):
        if ws.cell(r, 1).value is None:
            break
        out[ws.cell(r, 1).value] = ws.cell(r, 2).value
    return out


def test_a_completed_experiment_case_is_one_column_never_a_sensitivity_run(client, solved_base):
    completed_case(20, "Gas high")
    _wb, ws = _sheet(_export(client))
    assert ws is not None
    runs = _runs(ws)
    assert runs["Gas high: case"] == "current"
    assert not any(str(t).startswith("exp-") for t in _texts(ws))
    _h, cols = _header(ws)
    assert ws.cell(_kpi_row(ws, OPEX), cols["Gas high: case"]).value == pytest.approx(opex(20), rel=1e-9)
    assert ws.cell(_kpi_row(ws, OPEX), cols["Δ Gas high: case − base"]).value.startswith("=")
    assert NOT_ESTABLISHED != ws.cell(_kpi_row(ws, OPEX), cols["Gas high: case"]).value


def test_both_cases_of_a_two_case_plan_are_columns(client, solved_base):
    from tests.test_experiment_two_case import arm, finish, prepared_pair
    eid = prepared_pair()
    session, state = arm(eid)
    finish(session, finish(session, state, eid), eid)
    _wb, ws = _sheet(_export(client))
    _h, cols = _header(ws)
    row = _kpi_row(ws, OPEX)
    assert ws.cell(row, cols["Shared two-case plan: low"]).value == pytest.approx(opex(20), rel=1e-9)
    assert ws.cell(row, cols["Shared two-case plan: high"]).value == pytest.approx(opex(30), rel=1e-9)
    assert not any(str(t).startswith("exp-") for t in _texts(ws))


def test_the_case_project_name_marker_is_exactly_the_engines_name():
    from harness import experiment_execution as engine
    from harness.experiment_two_case import select_case
    eid = "0123abcd-0123-4567-89ab-0123456789ab"
    one = {"experiment_id": eid, "cases": [{"id": "c" * 32}]}
    pair = {"experiment_id": eid, "cases": [{"id": "low"}, {"id": "high-2_x"}],
            "preflight": None}
    names = [engine._name(one), *(engine._name(select_case(pair, c["id"])) for c in pair["cases"])]
    assert all(engine.is_case_project_name(n) for n in names), names
    for other in ("exp-", "exp-" + "0" * 32, "Exp-" + "0" * 32 + "-case", "exp-" + "0" * 32 + "-3case", "exp-" + "A" * 32 + "-case", "exp-" + "0" * 32 + "-" + "c" * 25,
                  " exp-" + "0" * 32 + "-case", "exp-" + "0" * 31 + "-case", "Gas 5", None):
        assert not engine.is_case_project_name(other), other


# ── scope: only the acting user's own experiments on this base (GK B (11) 1) ──

def _retag(root, experiment_id, **fields):
    """Rewrite a stored record's (and its ledger's) owner_id/project_id so that
    it stays internally consistent: a record of another project, or another
    user's record on this shared project."""
    import json

    from harness import experiment_execution as engine
    from services import experiments as ex
    path = root / "experiments" / (experiment_id + ".json")
    record = json.loads(path.read_text())
    record.update(fields)
    if "project_id" in fields:
        record["preflight"]["project_id"] = fields["project_id"]
    record["operations"] = [{"kind": "single-network-case-ledger", "version": 1,
                             "preflight_sha256": ex._digest(record["preflight"])}]
    path.write_text(json.dumps(record))
    ledger_path = engine._path(root, experiment_id)
    ledger = json.loads(ledger_path.read_text())
    ledger.update(fields, preflight_sha256=ex._digest(record["preflight"]))
    ledger_path.write_text(json.dumps(ledger))


def test_another_users_or_another_projects_experiments_never_appear(client, solved_base, second_identity):
    import uuid
    _base_id, root = solved_base
    completed_case(20, "Mine")
    theirs, _ = completed_case(30, "Theirs")
    elsewhere, _ = completed_case(40, "Elsewhere")
    _retag(root, theirs, owner_id=str(second_identity["user_id"]))
    _retag(root, elsewhere, project_id=str(uuid.uuid4()))
    _wb, ws = _sheet(_export(client))
    texts = _texts(ws)
    assert _runs(ws)["Mine: case"] == "current"
    assert not any("Theirs" in t or "Elsewhere" in t or t.startswith("exp-") for t in texts)
    # Their case projects are still the base's children: counted, never named.
    assert "2 experiment case project(s) not shown: not a case of your own experiments on this base" in texts


def test_a_case_whose_ledger_cannot_be_read_is_unavailable_and_its_child_not_counted(client, solved_base):
    from harness import experiment_execution as engine
    _base_id, root = solved_base
    eid, _ = completed_case(20, "Corrupt")
    engine._path(root, eid).write_text("{not json")
    _wb, ws = _sheet(_export(client))
    texts = _texts(ws)
    assert _runs(ws)["Corrupt: case"] == S.EXPERIMENT_REASONS["case_unavailable"]
    # Its child is this case's, shown once as unavailable: not another user's.
    assert not any("not a case of your own experiments" in t for t in texts)


# ── baseline checks against the open base (2) and the reason vocabulary (3) ──

def test_a_case_of_a_changed_base_is_baseline_changed(client, solved_base):
    from harness import sensitivity_comparison as sc
    from tests.test_sensitivity_comparison import edit_saved
    base_id, _root = solved_base
    completed_case(20, "Before")
    edit_saved(base_id, lambda ctx: ctx.network.loads.loc.__setitem__(("L1", "p_set"), 6))
    solve(base_id)
    _wb, ws = _sheet(_export(client))
    assert _runs(ws)["Before: case"] == sc._REASONS["baseline_changed"]
    _h, cols = _header(ws)
    assert ws.cell(_kpi_row(ws, OPEX), cols["Before: case"]).value == NOT_ESTABLISHED


def test_without_current_base_results_every_case_is_baseline_unavailable(client, solved_base):
    from harness import sensitivity_comparison as sc
    from services.pypsa_service import PyPSAService
    completed_case(20, "Done")
    bad, _ = completed_case(1, "Broken", path="Generator.G1.p_nom", succeeds=False)
    PyPSAService.get_active_context().network.loads.loc["L1", "p_set"] = 6   # unsaved: stale results
    r = client.get("/api/results/workbook.xlsx", params={"stored": "true"})
    assert r.status_code == 200, r.text[:300]
    _wb, ws = _sheet(r.content)
    runs = _runs(ws)
    assert runs["Done: case"].startswith(sc._REASONS["baseline_unavailable"])
    assert runs["Broken: case"].startswith(sc._REASONS["baseline_unavailable"])


def _recorded_baseline_run(ctx) -> str:
    """A completed baseline run with evidence, as
    test_experiment_comparison.test_referenced_baseline_evidence_loss_... makes it."""
    import time

    from services import chat_service, chat_tools, run_evidence
    session = chat_service.ChatSession()
    session.wait_for_decision = lambda _: "approve"
    frames = list(chat_service._dispatch_real_tool_call(
        session, {"id": "baseline", "name": "run_simulation", "input": {}}, []))
    result = next(p["result"] for event, p in frames if event == "tool_result")
    assert chat_tools.wait_for_job(result["job_id"], 25)["completed"]
    run_id = run_evidence.foreground_run_id(result["job_id"])
    deadline = time.monotonic() + 10
    while run_evidence.get_run_evidence(run_id)["status"] == "running" and time.monotonic() < deadline:
        time.sleep(0.02)
    assert run_evidence.get_run_evidence(run_id)["evidence_available"]
    chat_tools.save_project(ctx.loaded_project)
    return run_id


def test_a_changed_recorded_baseline_run_is_baseline_changed(client, baseline):
    """A plan on a recorded baseline run keeps `_case`'s check of that run's evidence."""
    from harness import sensitivity_comparison as sc
    ctx, _base_id, root = baseline
    run_id = _recorded_baseline_run(ctx)
    completed_case(20, "Pinned", baseline_run_id=run_id)
    assert _runs(_sheet(_export(client))[1])["Pinned: case"] == "current"
    (root / "run_evidence" / run_id / "sections/summary.json").write_text("{}")
    assert _runs(_sheet(_export(client))[1])["Pinned: case"] == sc._REASONS["baseline_changed"]


def test_failed_and_not_completed_cases_say_so_on_the_sheet_and_the_view_sheets(client, solved_base):
    from services.results_workbook.scenarios import EXPERIMENT_REASONS
    from tests.test_experiment_case_advancement import prepared
    completed_case(1, "Infeasible", path="Generator.G1.p_nom", succeeds=False)
    prepared()                                              # prepared, never run
    r = client.get("/api/results/workbook.xlsx", params={"views": "capex"})
    assert r.status_code == 200, r.text[:300]
    _wb, ws = _sheet(r.content)
    runs = _runs(ws)
    assert runs["Infeasible: case"] == EXPERIMENT_REASONS["case_failed"]
    assert runs["One checked case: cost"] == EXPERIMENT_REASONS["case_not_completed"]
    note = _wb["Capacity expansion"]["A2"].value
    assert f"Infeasible: case: not comparable with the base ({EXPERIMENT_REASONS['case_failed']})" in note


# ── the lock, no writes, and a change between verify and load (5, a, b, c) ──

def test_every_case_load_is_outside_the_lock_and_nothing_is_written(client, solved_base, monkeypatch):
    from services import experiments as ex, run_evidence, workflow_tools as wt
    _base_id, root = solved_base
    completed_case(20, "First")
    completed_case(30, "Second")
    files = [*(root / "experiments").glob("*.json"), *(root / "experiment_execution").glob("*.json")]
    before = {p: p.read_bytes() for p in files}
    held = {"_load_context": [], "_load_run": [], "gather_experiments": []}

    def spy(module, name):
        real = getattr(module, name)

        def wrapper(*args, **kwargs):
            held[name].append(ex._LOCK._is_owned())
            return real(*args, **kwargs)
        monkeypatch.setattr(module, name, wrapper)
    spy(wt, "_load_context")
    spy(run_evidence, "_load_run")
    spy(S, "gather_experiments")
    _wb, ws = _sheet(_export(client))
    assert _runs(ws)["First: case"] == _runs(ws)["Second: case"] == "current"
    # The route enters the gather without the lock; every load in it (those in
    # `_observe` too: two per case) runs without it.
    assert held["gather_experiments"] == [False]
    assert len(held["_load_context"]) >= 4 and len(held["_load_run"]) >= 4
    assert not any(held["_load_context"]) and not any(held["_load_run"])
    assert {p: p.read_bytes() for p in files} == before


def _between_verify_and_load(monkeypatch, change):
    """Run `change(record)` after a case's receipt is observed on its ledger
    copy and before its network is loaded."""
    from harness import experiment_comparison as xc
    real = xc._verify

    def verify(root, record, ledger, out):
        data = real(root, record, ledger, out)
        change(record, data)
        return data
    monkeypatch.setattr(xc, "_verify", verify)


def test_a_network_changed_after_the_ledger_copy_is_excluded(client, solved_base, monkeypatch):
    """The ledger is rewritten (a cancel) and the case's saved network changed
    after the copy was read: the case is shown as its copy says, with no figures
    from the changed network."""
    from harness import sensitivity_comparison as sc
    from routers import projects
    from services import chat_tools, project_registry, workflow_tools as wt
    _base_id, root = solved_base
    eid, done = completed_case(20, "Moved")
    ledger = root / "experiment_execution" / (eid + ".json")
    rewritten = []

    def change(record, data):
        before = ledger.read_bytes()
        chat_tools.DISPATCHERS["cancel_experiment"](experiment_id=eid)
        rewritten.append(ledger.read_bytes() != before)
        with chat_tools._acting() as (db, user):
            child = project_registry.resolve_project(db, user, done["case_project_id"])
            ctx = wt._load_context(child)
            ctx.network.generators.at["G1", "p_nom"] += 1e-11
            projects._save_context(ctx, child.name, storage_dir=project_registry.project_dir(child),
                                   db=db, user=user, project_row=child)
    _between_verify_and_load(monkeypatch, change)
    _wb, ws = _sheet(_export(client))
    assert rewritten == [True]
    assert _runs(ws)["Moved: case"] == sc._REASONS["case_inputs_changed"]
    _h, cols = _header(ws)
    assert ws.cell(_kpi_row(ws, OPEX), cols["Moved: case"]).value == NOT_ESTABLISHED


def test_run_evidence_changed_after_the_receipt_is_evidence_tampered(client, solved_base, monkeypatch):
    import json

    from services.results_workbook.scenarios import EXPERIMENT_REASONS
    _base_id, root = solved_base
    _eid, done = completed_case(20, "Tampered")
    from services import chat_tools, project_registry
    with chat_tools._acting() as (db, user):
        directory = project_registry.project_dir(project_registry.resolve_project(db, user, done["case_project_id"]))
    manifest = directory / "run_evidence" / done["run_id"] / "manifest.json"

    def change(record, data):
        body = json.loads(manifest.read_text())
        body["metrics"]["objective"]["unit"] = "USD"
        manifest.write_text(json.dumps(body))
    _between_verify_and_load(monkeypatch, change)
    _wb, ws = _sheet(_export(client))
    assert _runs(ws)["Tampered: case"] == EXPERIMENT_REASONS["evidence_tampered"]
    _h, cols = _header(ws)
    assert ws.cell(_kpi_row(ws, OPEX), cols["Tampered: case"]).value == NOT_ESTABLISHED


# ── access first (6), the same filling (7) ──────────────────────────────

def test_a_case_the_user_cannot_open_is_counted_and_nothing_of_it_is_read(
        client, solved_base, second_identity, monkeypatch):
    import builtins
    import pathlib
    import uuid

    from db.models import Project
    from db.session import SessionLocal
    from harness import experiment_execution as engine
    from services import chat_tools, project_registry, run_evidence, workflow_tools as wt
    completed_case(20, "Visible")
    _eid, hidden = completed_case(30, "Hidden")
    with chat_tools._acting() as (db, user):
        directory = project_registry.project_dir(project_registry.resolve_project(db, user, hidden["case_project_id"]))
    with SessionLocal() as db:
        row = db.get(Project, uuid.UUID(hidden["case_project_id"]))
        row.org_id = uuid.UUID(str(second_identity["org_id"]))
        db.commit()
    touched = []

    def watch(module, name, key=lambda *a, **k: a[0] if a else None):
        real = getattr(module, name)

        def wrapper(*args, **kwargs):
            touched.append((name, key(*args, **kwargs)))
            return real(*args, **kwargs)
        monkeypatch.setattr(module, name, wrapper)
    watch(wt, "_load_context", key=lambda project: str(project.id))
    watch(run_evidence, "_load_run", key=lambda root, pid, run_id: pid)
    watch(engine, "_observe", key=lambda record, ledger: ledger.get("case_project_id"))
    for name in ("open", "read_bytes", "read_text", "exists", "is_file", "is_dir", "stat", "iterdir", "glob"):
        watch(pathlib.Path, name, key=lambda self, *a, **k: str(self))
    watch(builtins, "open", key=lambda file, *a, **k: str(file))
    _wb, ws = _sheet(_export(client))
    texts = _texts(ws)
    assert "1 scenario(s) or experiment case(s) not shown: no access" in texts
    assert not any("Hidden" in t for t in texts) and _runs(ws)["Visible: case"] == "current"
    assert not [t for t in touched if t[1] == hidden["case_project_id"]]
    assert not [t for t in touched if str(t[1]).startswith(str(directory))]


def test_a_case_whose_dispatch_the_routes_would_not_serve_is_unsolved(client, solved_base, monkeypatch):
    from harness import sensitivity_comparison as sc
    from routers import results as results_router
    from services.pypsa_service import PyPSAService
    completed_case(20, "Unserved")
    active = PyPSAService.get_network()
    real = results_router._dispatch_ready
    monkeypatch.setattr(results_router, "_dispatch_ready", lambda n: real(n) if n is active else False)
    _wb, ws = _sheet(_export(client))
    assert _runs(ws)["Unserved: case"] == sc._REASONS["case_unsolved"]
    _h, cols = _header(ws)
    assert ws.cell(_kpi_row(ws, OPEX), cols["Unserved: case"]).value == NOT_ESTABLISHED


# ── the shared cap (8) and unique names (9) ──────────────────────────────

def test_six_sensitivity_runs_and_four_cases_show_eight_and_count_two(client, solved_base, monkeypatch):
    import json

    from services import workflow_tools as wt
    from tests.test_sensitivity_comparison import sweep
    base_id, root = solved_base
    sweep(base_id, 1, 2, 3, 4)
    sweep(base_id, 5, 6)
    cases = {completed_case(20 + i, f"Case {i}")[0]: f"Case {i}" for i in range(4)}
    order = json.loads((root / "experiments" / "index.json").read_text())["order"]
    over = [eid for eid in order if eid in cases][2:]
    over_projects = {json.loads((root / "experiment_execution" / (eid + ".json")).read_text())["case_project_id"]
                     for eid in over}
    loaded = []
    real = wt._load_context
    monkeypatch.setattr(wt, "_load_context", lambda project: loaded.append(str(project.id)) or real(project))
    wb, ws = _sheet(_export(client))
    runs = _runs(ws)
    assert len(runs) == 1 + 8
    assert [n for n in runs if n.startswith("Case ")] == [cases[eid] + ": case" for eid in order if eid in cases][:2]
    texts = _texts(ws)
    assert any(t.startswith("2 scenario(s) or experiment case(s) not shown: the sheet compares up to 8") for t in texts)
    assert not any(cases[eid] in t for eid in over for t in texts)
    assert not over_projects & set(loaded)
    about = {wb["About"].cell(r, 1).value: wb["About"].cell(r, 2).value for r in range(1, wb["About"].max_row + 1)}
    assert about["Scenarios not shown"].startswith("2 not shown")


def _titled_case(value, title, request_id) -> str:
    """`completed_case` under a title another plan already has."""
    from services import chat_tools, experiments as ex
    from tests.test_experiment_case_advancement import arm, invoke
    plan = ex.start_experiment(title=title, request_id=request_id, cases=[{"id": "case", "changes": [
        {"path": "Generator.G1.marginal_cost", "value_type": "number", "value": value}]}])
    eid = ex.prepare_experiment(plan["experiment_id"])["experiment_id"]
    session, state = arm(eid)
    job = invoke(session, state["next_call"])["cases"][0]["job"]["id"]
    assert chat_tools.wait_for_job(job, 25)["completed"]
    chat_tools.DISPATCHERS["refresh_experiment"](experiment_id=eid)
    return eid


def test_run_names_are_unique(client, solved_base):
    from services import chat_tools
    base_id, _root = solved_base
    first, _ = completed_case(20, "Same")
    second = _titled_case(30, "Same", "same-again")
    out = chat_tools.run_sensitivity_sweep(base_id, [{"new_name": "Same: case", "changes": [
        {"component_class": "Generator", "names": ["G1"], "attribute": "marginal_cost", "value": 40}]}])
    assert chat_tools.wait_for_job(out["cases"][0]["job"]["id"], 25)["completed"]
    _wb, ws = _sheet(_export(client))
    _h, cols = _header(ws)
    names = [n for n in cols if not str(n).startswith("Δ")]
    assert len(names) == len(set(names)) == 4
    assert {"Same: case", f"Same: case ({first[:8]})", f"Same: case ({second[:8]})"} <= set(names)
    row = _kpi_row(ws, OPEX)
    values = {round(ws.cell(row, cols[n]).value * 1e6) for n in names[1:]}
    assert values == {200, 300, 400}


def test_an_unreadable_experiment_store_is_said_and_names_no_case(client, solved_base):
    _base_id, root = solved_base
    completed_case(20, "Lost")
    (root / "experiments" / "index.json").write_text("{corrupt")
    wb, ws = _sheet(_export(client))
    texts = _texts(ws)
    assert "Experiment cases not shown: the experiment store needs repair." in texts
    assert not any("Lost" in t or t.startswith("exp-") for t in texts)
    about = {wb["About"].cell(r, 1).value: wb["About"].cell(r, 2).value for r in range(1, wb["About"].max_row + 1)}
    assert about["Scenarios not shown"].startswith("1 not shown")
