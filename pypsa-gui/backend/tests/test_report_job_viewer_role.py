# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""Project-viewer-role 02, slice 2: a report job reads its requester's role
again before it saves.

An editor who starts a generate, a regenerate or a template-plan job and is
made a viewer, or removed, while it runs gets nothing saved: no report
directory, no new version, the meta untouched, no figures, and the job's
marker still ends. A role that cannot be read refuses (fail closed). A job
with no requester (local mode) is not judged.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from db.models import ProjectMembership
from services import project_write_gate
from services.llm_fake import FakeProvider
from services.project_write_gate import KIND
from services.reports import report_job, store
from services.reports.assemble import EXECUTIVE_SUMMARY_ID
from services.reports.report_job import (
    _read_job_marker,
    prepare_mapping_job,
    run_mapping_job,
    run_report_job,
    start_report_job,
)
from tests import test_viewer_role_gate as gate_fixtures
from tests import test_report_job as report_fixtures
from tests.test_report_job import _PNG, _PROFILE, _draft, _job
from tests.test_viewer_role_gate import _set_role

baseline = gate_fixtures.baseline
helpers_state = gate_fixtures.helpers_state
world = gate_fixtures.world
evidence = report_fixtures.evidence


def _script():
    return [
        _draft(EXECUTIVE_SUMMARY_ID, "Summary."),
        _draft("frontier", "Frontier."),
        _draft("fmea_top", "Modes."),
    ]


class _CueOnFirstSend(FakeProvider):
    """Runs `cue` when the job sends its first section: mid-run."""

    def __init__(self, turns, cue):
        super().__init__(turns)
        self.cue = cue

    def stream(self, request):
        if self.cue is not None:
            cue, self.cue = self.cue, None
            cue()
        yield from super().stream(request)


def _demote(world):
    return lambda: _set_role(
        world["session_local"], world["project"], world["editor"], "viewer"
    )


def _remove(world):
    def remove():
        import uuid

        with world["session_local"]() as db:
            db.query(ProjectMembership).filter_by(
                project_id=uuid.UUID(str(world["project"])), user_id=world["editor"]
            ).delete()
            db.commit()

    return remove


def _requester(world):
    return {"user_id": world["editor"], "project_id": world["project"]}


def _run_threaded(tmp_path, provider, **kw) -> dict:
    """Through `start_report_job`, so the on-disk marker is written and must end."""
    state: dict = {}
    start_report_job(
        project_name="demo",
        project_dir=tmp_path,
        evidence=kw.pop("evidence"),
        figure_pngs={"fmea_pareto": _PNG},
        profile=_PROFILE,
        provider=provider,
        language="en",
        title="T",
        lock=threading.Lock(),
        state=state,
        **kw,
    )
    record = state["report_job"]
    record["thread"].join(timeout=20)
    assert not record["thread"].is_alive()
    return record


@pytest.mark.parametrize("change", [_demote, _remove], ids=["demoted", "removed"])
def test_a_generate_whose_requester_loses_edit_mid_run_writes_no_report(
    world, tmp_path, evidence, change
):
    record = _run_threaded(
        tmp_path,
        _CueOnFirstSend(_script(), change(world)),
        evidence=evidence,
        **_requester(world),
    )
    assert record["status"] == "failed" and record["error_kind"] == KIND, record
    assert record["error"] == project_write_gate.REFUSAL and record["version"] is None
    assert not (tmp_path / "reports").exists() or not any(
        (tmp_path / "reports").iterdir()
    )
    assert store.list_reports(tmp_path) == []
    assert _read_job_marker(tmp_path) is None  # the marker still ends


def test_a_regenerate_whose_requester_loses_edit_mid_run_writes_no_version(
    world, tmp_path, evidence
):
    v1 = run_report_job(_job(tmp_path, evidence, FakeProvider(_script())))
    report_dir = tmp_path / "reports" / v1.report_id
    meta = (report_dir / "meta.json").read_bytes()
    files = sorted(p.relative_to(report_dir) for p in report_dir.rglob("*"))

    provider = _CueOnFirstSend(
        [_draft("fmea_top", "Modes, rewritten.")], _demote(world)
    )
    record = _run_threaded(
        tmp_path,
        provider,
        evidence=evidence,
        base_document=v1,
        regenerate_section="fmea_top",
        instruction="shorter",
        **_requester(world),
    )
    assert record["status"] == "failed" and record["error_kind"] == KIND, record
    assert (report_dir / "meta.json").read_bytes() == meta
    assert sorted(p.relative_to(report_dir) for p in report_dir.rglob("*")) == files
    assert store.load_meta(tmp_path, v1.report_id).latest_version == 1
    assert _read_job_marker(tmp_path) is None


def test_a_generate_of_an_editor_who_stays_one_is_saved(world, tmp_path, evidence):
    record = _run_threaded(
        tmp_path, FakeProvider(_script()), evidence=evidence, **_requester(world)
    )
    assert record["status"] == "done" and record["version"] == 1, record
    assert len(store.list_reports(tmp_path)) == 1


def test_a_report_job_whose_role_cannot_be_read_refuses(
    world, tmp_path, evidence, monkeypatch
):
    from db import session as db_session

    def broken():
        raise RuntimeError("database gone")

    def break_db():
        monkeypatch.setattr(db_session, "SessionLocal", broken)

    job = _job(
        tmp_path, evidence, _CueOnFirstSend(_script(), break_db), **_requester(world)
    )
    with pytest.raises(project_write_gate.RequesterViewOnly):
        run_report_job(job)
    assert job.record["status"] == "failed" and job.record["error_kind"] == KIND
    assert store.list_reports(tmp_path) == []


def test_a_report_job_with_no_requester_is_not_judged(tmp_path, evidence, monkeypatch):
    monkeypatch.setattr(
        project_write_gate,
        "requester_may_not_write",
        lambda *_a: pytest.fail("a local-mode job is not judged") or True,
    )
    monkeypatch.setattr(
        report_job.project_write_gate,
        "check_requester",
        lambda u, p: None if (u, p) == (None, None) else pytest.fail((u, p)),
    )
    job = _job(tmp_path, evidence, FakeProvider(_script()))
    run_report_job(job)
    assert job.record["status"] == "done"


def test_a_template_plan_whose_requester_loses_edit_stores_no_plan(
    world, tmp_path, evidence, monkeypatch
):
    from services.reports import templates

    v1 = run_report_job(_job(tmp_path, evidence, FakeProvider(_script())))
    meta_path = tmp_path / "reports" / v1.report_id / "meta.json"
    meta = meta_path.read_bytes()

    def propose(provider, **_kw):
        _demote(world)()
        return {"headings": []}

    monkeypatch.setattr(templates, "_propose_mapping", lambda: propose)
    monkeypatch.setattr(
        templates, "sanitise_plan", lambda raw, outline, doc: ({"p": 1}, [])
    )
    job = prepare_mapping_job(
        project_name="demo",
        project_dir=tmp_path,
        document=v1,
        outline=SimpleNamespace(),
        profile=_PROFILE,
        provider=None,
        language="en",
        **_requester(world),
    )
    with pytest.raises(project_write_gate.RequesterViewOnly):
        run_mapping_job(job)
    assert job.record["status"] == "failed" and job.record["error_kind"] == KIND
    assert meta_path.read_bytes() == meta


def test_a_template_plan_of_an_editor_is_stored(world, tmp_path, evidence, monkeypatch):
    from services.reports import templates

    v1 = run_report_job(_job(tmp_path, evidence, FakeProvider(_script())))
    monkeypatch.setattr(
        templates, "_propose_mapping", lambda: lambda provider, **_kw: {}
    )
    monkeypatch.setattr(
        templates, "sanitise_plan", lambda raw, outline, doc: ({"p": 1}, [])
    )
    job = prepare_mapping_job(
        project_name="demo",
        project_dir=tmp_path,
        document=v1,
        outline=SimpleNamespace(),
        profile=_PROFILE,
        provider=None,
        language="en",
        **_requester(world),
    )
    assert run_mapping_job(job) == {"p": 1}
    assert job.record["status"] == "done"


# ── every starter names the requester and the project row ───────────────────


def _poll(client, name, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        body = client.get(f"/api/projects/{name}/reports/generate/status").json()
        if body.get("status") != "running":
            return body
        time.sleep(0.02)
    raise AssertionError("the report job did not finish")


def test_generate_and_regenerate_name_the_caller_and_the_project_row(
    client, api_project, session_state, seeded_identity, _auth_db, monkeypatch
):
    from db.models import Project
    from tests.test_report_job_http import _install_provider, _store_eh_report

    name = api_project("gen-viewer-role")
    _store_eh_report(session_state(client))
    with _auth_db[1]() as db:
        pid = str(db.query(Project).filter_by(name=name).one().id)
    asked = []
    real = project_write_gate.requester_may_not_write

    def spy(user_id, project_id):
        asked.append((str(user_id), str(project_id)))
        return real(user_id, project_id)

    monkeypatch.setattr(project_write_gate, "requester_may_not_write", spy)
    _install_provider(
        monkeypatch,
        FakeProvider(
            [_draft(EXECUTIVE_SUMMARY_ID, "Summary."), _draft("fmea_top", "Modes.")]
        ),
    )
    rid = client.post(
        f"/api/projects/{name}/reports/generate",
        json={"sections": [EXECUTIVE_SUMMARY_ID, "fmea_top"]},
    ).json()["report_id"]
    assert _poll(client, name)["status"] == "done"
    _install_provider(monkeypatch, FakeProvider([_draft("fmea_top", "Shorter.")]))
    r = client.post(
        f"/api/projects/{name}/reports/{rid}/sections/fmea_top/regenerate", json={}
    )
    assert r.status_code == 200, r.text[:300]
    assert _poll(client, name)["status"] == "done"
    me = str(seeded_identity["user_id"])
    assert asked == [(me, pid), (me, pid)]


def test_a_refused_generate_over_http_reports_the_kind_and_writes_nothing(
    client, api_project, session_state, monkeypatch
):
    from tests.test_report_job_http import _install_provider, _store_eh_report

    name = api_project("gen-viewer-refused")
    _store_eh_report(session_state(client))
    monkeypatch.setattr(project_write_gate, "requester_may_not_write", lambda *_a: True)
    _install_provider(
        monkeypatch,
        FakeProvider(
            [_draft(EXECUTIVE_SUMMARY_ID, "Summary."), _draft("fmea_top", "Modes.")]
        ),
    )
    r = client.post(
        f"/api/projects/{name}/reports/generate",
        json={"sections": [EXECUTIVE_SUMMARY_ID, "fmea_top"]},
    )
    assert r.status_code == 200, r.text[:300]
    body = _poll(client, name)
    assert body["status"] == "failed" and body["error_kind"] == KIND, body
    assert client.get(f"/api/projects/{name}/reports").json() in ([], {"reports": []})


def test_the_harness_report_stage_names_the_runs_owner(monkeypatch, tmp_path):
    """Stage 7 calls `report_jobs._start` directly: it passes the task's owner."""
    import json

    from harness import run_report
    from routers import report_jobs
    from services import chat_tools, run_profiles

    project = SimpleNamespace(uuid="p-1", name="demo", directory=tmp_path)
    ctx = SimpleNamespace(project_uuid="p-1")
    task = {
        "project_id": "p-1",
        "owner_id": "u-7",
        "status": "running",
        "run": {"profile": "x", "status": "running"},
        "steps": [{"id": "report", "status": "pending"}],
    }
    path = tmp_path / "task.json"
    path.write_text(json.dumps(task))
    seen = {}
    monkeypatch.setattr(chat_tools, "_report_project", lambda: project)
    monkeypatch.setattr(run_profiles, "resolve", lambda _p: {})
    monkeypatch.setattr(report_jobs, "_resolve_profile", lambda: None)
    monkeypatch.setattr(report_jobs, "_provider_or_400", lambda _l: None)
    monkeypatch.setattr(report_jobs, "_evidence_for", lambda *_a, **_k: (None, {}))
    monkeypatch.setattr(run_report, "harness_source", lambda *_a: {})

    def start(project_, **kw):
        seen.update(kw)
        return "r-1"

    monkeypatch.setattr(report_jobs, "_start", start)
    run_report.start(path, ctx, {"id": "report"}, instance="i")
    assert seen["user_id"] == "u-7"
