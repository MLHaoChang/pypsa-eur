"""Study harness issue 21 §2: create at grant and hand over to the new
project (`services.run_handover`). GK B A1-A3, R1, R2 and A4 (copies)."""
from __future__ import annotations

import hashlib
import json
import multiprocessing
import threading
import uuid

import pytest
from fastapi import HTTPException

from db.models import Project
from db import session as db_session
from harness import durable
from services import project_registry, run_handover, upload_service
from services.pypsa_service import PyPSAService

PARAMS = {"site_type": "hospital", "connection_mw": 12.0}


@pytest.fixture
def host(tmp_path, tmp_projects_dir, seeded_identity):
    host_dir = tmp_path / "hostproj"
    host_dir.mkdir()
    uid = upload_service.add_upload("hostproj", b"year,peak (kW)\n2027,3000\n", "site.csv", "text/csv",
                                    project_dir=host_dir).file_id
    tasks = tmp_path / "assistant_tasks"
    tasks.mkdir()
    return {"dir": host_dir, "upload": uid, "task": tasks / "host-task.json",
            "owner": str(seeded_identity["user_id"])}


def _grant(host, start, **kw):
    args = dict(owner_id=host["owner"], origin={"project_id": "host-project", "task_id": "host-task"},
                name=f"Intake {uuid.uuid4().hex[:6]}", params=PARAMS, digest="d" * 64, decision_id="decision-1",
                host_name="hostproj", host_dir=host["dir"], uploads=[host["upload"]], start=start)
    args.update(kw)
    return run_handover.hand_over(host["task"], **args)


def _projects(name):
    with db_session.SessionLocal() as db:
        return db.query(Project).filter(Project.name == name).count()


class Start:
    def __init__(self, fail=0):
        self.calls, self.fail, self.ctxs = 0, fail, []

    def __call__(self, row, ctx):
        self.calls += 1
        self.ctxs.append(ctx)
        if self.calls <= self.fail:
            raise RuntimeError("crash before the task started")
        return f"task-on-{row.id}"


def test_grant_creates_one_project_with_the_params_and_copies_uploads(host):
    start = Start()
    intent = _grant(host, start)
    assert intent["state"] == "started" and intent["origin"]["task_id"] == "host-task"
    assert intent["project_id"] == intent["planned_project_id"]
    assert intent["task_id"] == f"task-on-{intent['project_id']}"
    with db_session.SessionLocal() as db:
        row = db.get(Project, uuid.UUID(intent["project_id"]))
        new_dir = project_registry.project_dir(row)
    meta = json.loads((new_dir / "eh_template.json").read_text())
    assert meta["params"]["connection_mw"] == 12.0 and meta["params"]["site_type"] == "hospital"
    assert sorted(meta["entered"]) == ["connection_mw", "site_type"]
    assert str(start.ctxs[0].project_uuid) == intent["project_id"]
    blob = upload_service.get_upload_bytes(row.name, host["upload"], project_dir=new_dir)
    original = upload_service.get_upload_bytes("hostproj", host["upload"], project_dir=host["dir"])
    assert hashlib.sha256(blob).hexdigest() == hashlib.sha256(original).hexdigest()
    assert intent["uploads"] == [{"upload_id": host["upload"], "sha256": hashlib.sha256(original).hexdigest()}]


def test_a_repeated_grant_creates_no_second_project(host):
    start = Start()
    first = _grant(host, start, name="Once only")
    again = _grant(host, start, name="Once only")
    assert again == first and start.calls == 1 and _projects("Once only") == 1


def test_a_grant_with_other_inputs_after_hand_over_is_refused(host):
    _grant(host, Start())
    with pytest.raises(run_handover.HandoverError) as exc:
        _grant(host, Start(), digest="e" * 64)
    assert exc.value.kind == "handover_conflict"


def test_two_concurrent_grants_create_one_project(host):
    start, results, errors = Start(), [], []
    barrier = threading.Barrier(2)

    def go():
        barrier.wait()
        try:
            results.append(_grant(host, start, name="Raced", lock_timeout=30))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and len(results) == 2 and results[0] == results[1]
    assert start.calls == 1 and _projects("Raced") == 1


def _hold(path, ready, release):
    with durable.file_lock(path, timeout=5):
        ready.set()
        release.wait(30)


def test_a_lock_held_by_another_process_refuses_the_grant(host):
    spawn = multiprocessing.get_context("spawn")
    ready, release = spawn.Event(), spawn.Event()
    proc = spawn.Process(target=_hold, args=(str(run_handover.lock_path(host["task"])), ready, release))
    proc.start()
    try:
        assert ready.wait(30)
        with pytest.raises(run_handover.HandoverError) as exc:
            _grant(host, Start(), name="Locked out", lock_timeout=0.2)
        assert exc.value.kind == "handover_busy"
        assert _projects("Locked out") == 0 and run_handover.read_intent(host["task"]) is None
    finally:
        release.set()
        proc.join(30)


def test_a_crash_after_creation_resumes_from_the_intent(host):
    start = Start(fail=1)
    with pytest.raises(RuntimeError):
        _grant(host, start, name="Crashed")
    assert run_handover.read_intent(host["task"])["state"] == "created"
    intent = _grant(host, start, name="Crashed")
    assert intent["state"] == "started" and start.calls == 2 and _projects("Crashed") == 1


def test_a_create_cut_off_midway_is_named_never_deleted_or_redone(host, monkeypatch):
    from routers import projects

    def broken(*a, **k):
        raise RuntimeError("cut off")
    real = projects._finish_created_project
    monkeypatch.setattr(projects, "_finish_created_project", broken)
    with pytest.raises(RuntimeError):
        _grant(host, Start(), name="Half made")
    monkeypatch.setattr(projects, "_finish_created_project", real)
    with pytest.raises(run_handover.HandoverError) as exc:
        _grant(host, Start(), name="Half made")
    assert exc.value.kind == "handover_incomplete" and exc.value.extra["project_name"] == "Half made"
    assert _projects("Half made") == 1


# ── R1: the route's own checks ──────────────────────────────────────────────


def test_bad_params_refuse_as_the_route_does_and_create_nothing(host):
    with pytest.raises(HTTPException) as exc:
        _grant(host, Start(), name="Too big", params={"connection_mw": 2000.0})
    assert exc.value.status_code == 422 and exc.value.detail["field"] == "connection_mw"
    assert _projects("Too big") == 0


def test_no_create_permission_refuses_as_the_route_does(host, monkeypatch):
    monkeypatch.setattr(project_registry, "_org_id_or_none", lambda db, user: None)
    with pytest.raises(HTTPException) as exc:
        _grant(host, Start(), name="Not allowed")
    assert exc.value.status_code == 403 and _projects("Not allowed") == 0


# ── R2 / A3: the foreground never moves ─────────────────────────────────────


def test_the_active_context_is_unchanged_across_the_hand_over(host):
    before = PyPSAService.get_active_context()
    uuid_before, network_before = before.project_uuid, before.network
    start = Start()
    intent = _grant(host, start)
    after = PyPSAService.get_active_context()
    assert after is before and after.project_uuid == uuid_before and after.network is network_before
    assert str(start.ctxs[0].project_uuid) == intent["project_id"] and start.ctxs[0] is not before


def test_the_run_starts_on_the_new_project_and_the_host_ends_handed_over(host):
    from services import assistant_tasks, run_profiles

    host_task = {"task_id": "host-task", "project_id": "host-project", "owner_id": host["owner"],
                 "title": "Site study", "status": "ready", "steps": [], "version": 3,
                 "brief": {"objective": "Size the site", "cards": [], "cases": []},
                 "run": {"profile": "eh_site@1", "mode": "stepwise", "status": "running", "stage": "intake",
                         "intake": {"rows": [{"param": "connection_mw", "value": 12.0}]}}}
    host["task"].write_text(json.dumps(host_task))
    before = PyPSAService.get_active_context()
    start = assistant_tasks.handover_starter(host_task, run_profiles.resolve("eh_site@1"), host["owner"])
    intent = _grant(host, start, name="Started there")
    assert PyPSAService.get_active_context() is before
    with db_session.SessionLocal() as db:
        new_dir = project_registry.project_dir(db.get(Project, uuid.UUID(intent["project_id"])))
    task = json.loads((new_dir / "assistant_tasks" / (intent["task_id"] + ".json")).read_text())
    assert task["project_id"] == intent["project_id"] and task["owner_id"] == host["owner"]
    assert task["run"]["origin"] == {"project_id": "host-project", "task_id": "host-task"}
    assert task["run"]["intake"] == [{"param": "connection_mw", "value": 12.0}]
    assert task.get("requirements") is None  # GK B (20) Q1: the new project has no thread
    ended = assistant_tasks.end_handed_over(host["task"], intent)
    assert (ended["status"], ended["run"]["reason"]) == ("completed", "handed_over")
    assert ended["run"]["handed_over"]["task_id"] == intent["task_id"]


def test_requirements_moved_since_the_brief_refuse_the_grant_and_create_nothing(host):
    def moved():
        raise run_handover.HandoverError("task_requirements_moved", "The confirmed requirements changed.")
    with pytest.raises(run_handover.HandoverError) as exc:
        _grant(host, Start(), name="Moved", precheck=moved)
    assert exc.value.kind == "task_requirements_moved"
    assert _projects("Moved") == 0 and run_handover.read_intent(host["task"]) is None
