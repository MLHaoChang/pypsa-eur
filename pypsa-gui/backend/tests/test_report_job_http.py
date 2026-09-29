"""
WP3 — the generation routes (`routers/report_jobs.py`) over the fake provider.

  POST /api/projects/{name}/reports/generate                          → {status: running, report_id}
  GET  /api/projects/{name}/reports/generate/status                   → the record (204 never run)
  POST /api/projects/{name}/reports/generate/abort                    → idempotent, 404 never run
  POST /api/projects/{name}/reports/{report_id}/sections/{sid}/regenerate → version+1

The provider is the one `chat_service._provider_for_profile` would build for
the active profile; the tests replace the factory the route looks up so no
key and no network are involved. The result is readable through WP1's GET
and exportable through WP5's export.
"""
from __future__ import annotations

import json
import threading
import time

import pytest

from models.energy_hub import ReferenceDesignReport, SectionState, empty_section_map
from services.adequacy import eh_report as R
from services.llm_fake import FakeProvider
from services.llm_provider import LLMEvent
from services.reports.assemble import EXECUTIVE_SUMMARY_ID


def _store_eh_report(state: dict) -> None:
    sections = empty_section_map(default="skipped")
    sections["fmea_top"] = SectionState(status="ok", payload={
        "top": [{"rank": 1, "mode_id": "gen:g:forced_outage",
                 "component_class": "Generator", "name": "g",
                 "failure_class": "A", "occurrence_per_year": 1.0,
                 "occurrence_basis": "FOR", "severity_eur": 10.0,
                 "criticality_eur_per_year": 10.0, "delta_eue_mwh": 0.5,
                 "engine": "copt", "fidelity": "analytic_convolution"}],
        "classes_included": ["A"], "note": "Link-primary residual risk",
    })
    report = ReferenceDesignReport(
        archetype="strong_grid", pack_hash="p", assumptions_hash="a",
        sections=sections, ens_cap_permyriad=10.0, mc_lole_h=3.21,
    )
    R.store_eh_report(state, report)


def _draft(section_id: str, *paragraphs: str) -> dict:
    text = json.dumps({"section_id": section_id, "paragraphs": list(paragraphs),
                       "bullets": []})
    return {"events": [LLMEvent(type="text_delta", text=text)],
            "blocks": [{"type": "text", "text": text}]}


def _script() -> list[dict]:
    return [_draft(EXECUTIVE_SUMMARY_ID, "The plan meets 3.2 h/yr."),
            _draft("fmea_top", "Generator g dominates.")]


def _install_provider(monkeypatch, provider):
    import routers.report_jobs as jobs
    monkeypatch.setattr(jobs, "_provider_for_profile", lambda profile: (provider, None))
    return provider


def _poll(client, name: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    body = None
    while time.time() < deadline:
        r = client.get(f"/api/projects/{name}/reports/generate/status")
        assert r.status_code == 200, r.text
        body = r.json()
        if body.get("status") != "running":
            return body
        time.sleep(0.02)
    raise AssertionError(f"report job never finished: {body!r}")


class _Holding(FakeProvider):
    def __init__(self, turns, hold: threading.Event):
        super().__init__(turns)
        self.hold = hold

    def stream(self, request):
        self.hold.wait(timeout=10)
        yield from super().stream(request)


# ── generate → status → done ────────────────────────────────────────────────

def test_status_is_204_before_any_run(client, api_project):
    name = api_project("gen-204")
    r = client.get(f"/api/projects/{name}/reports/generate/status")
    assert r.status_code == 204, r.text
    r = client.post(f"/api/projects/{name}/reports/generate/abort")
    assert r.status_code == 404
    assert r.json()["detail"]["error_kind"] == "report_job_not_found"


def test_generate_runs_to_done_and_the_report_is_readable_and_exportable(
        client, api_project, project_storage_dir, session_state, monkeypatch):
    name = api_project("gen-ok")
    _store_eh_report(session_state(client))
    provider = _install_provider(monkeypatch, FakeProvider(_script()))

    # Named sections: the fixture network also yields an on-demand COPT
    # screen (an `ok` adequacy surface), so the default target list is
    # longer than this script; the unit tests cover the default.
    r = client.post(f"/api/projects/{name}/reports/generate",
                    json={"title": "Client report", "language": "en",
                          "sections": [EXECUTIVE_SUMMARY_ID, "fmea_top"]})
    assert r.status_code == 200, r.text[:400]
    assert r.json()["status"] == "running"
    rid = r.json()["report_id"]

    body = _poll(client, name)
    assert body["status"] == "done", body
    assert body["report_id"] == rid and body["version"] == 1
    assert "thread" not in body and "stop_event" not in body
    assert body["progress"] == {"done": 2, "total": 2, "current": None}
    assert body["profile_id"] and body["model"]
    assert len(provider.requests) == 2
    # No tools, stable system block, on the profile's model.
    assert provider.requests[0].tools == [] and provider.requests[0].tools_stable is True
    assert provider.requests[0].model == body["model"]

    doc = client.get(f"/api/projects/{name}/reports/{rid}")
    assert doc.status_code == 200, doc.text[:200]
    doc = doc.json()
    assert doc["mode"] == "generated" and doc["version"] == 1
    assert doc["title"] == "Client report"
    by_id = {s["section_id"]: s for s in doc["sections"]}
    assert by_id["fmea_top"]["source"] == "llm"
    assert by_id["fmea_top"]["blocks"][0] == {"type": "paragraph", "md": "Generator g dominates."}
    assert by_id[EXECUTIVE_SUMMARY_ID]["audit"]["verified"][0]["path"] == "/headline/mc_lole_h"
    assert (project_storage_dir(name) / "reports" / rid / "figures" / "fmea_pareto.png").is_file()
    listed = client.get(f"/api/projects/{name}/reports").json()
    assert [m["report_id"] for m in listed] == [rid] and listed[0]["mode"] == "generated"

    exp = client.post(f"/api/projects/{name}/reports/{rid}/export", json={})
    assert exp.status_code == 200, exp.text[:300]
    assert exp.json()["kind"] == "agent_export"


def test_a_second_generate_while_running_is_409_and_abort_ends_it(
        client, api_project, session_state, monkeypatch):
    name = api_project("gen-busy")
    _store_eh_report(session_state(client))
    hold = threading.Event()
    _install_provider(monkeypatch, _Holding(_script(), hold))

    r = client.post(f"/api/projects/{name}/reports/generate", json={})
    assert r.status_code == 200, r.text[:300]
    rid = r.json()["report_id"]

    r2 = client.post(f"/api/projects/{name}/reports/generate", json={})
    assert r2.status_code == 409, r2.text[:300]
    assert r2.json()["detail"]["error_kind"] == "report_job_in_flight"

    st = client.get(f"/api/projects/{name}/reports/generate/status").json()
    assert st["status"] == "running" and st["report_id"] == rid

    ab = client.post(f"/api/projects/{name}/reports/generate/abort")
    assert ab.status_code == 200, ab.text
    assert ab.json() == {"status": "running", "aborting": True}
    hold.set()
    body = _poll(client, name)
    assert body["status"] == "aborted", body
    # Idempotent once finished.
    ab2 = client.post(f"/api/projects/{name}/reports/generate/abort")
    assert ab2.status_code == 200 and ab2.json()["aborting"] is False
    # The slot is free again; the partial report exists.
    assert client.get(f"/api/projects/{name}/reports/{rid}").status_code == 200


def test_regenerate_one_section_saves_version_2(
        client, api_project, session_state, monkeypatch):
    name = api_project("gen-regen")
    _store_eh_report(session_state(client))
    _install_provider(monkeypatch, FakeProvider(_script()))
    rid = client.post(f"/api/projects/{name}/reports/generate",
                      json={"sections": [EXECUTIVE_SUMMARY_ID, "fmea_top"]}).json()["report_id"]
    assert _poll(client, name)["status"] == "done"

    provider = _install_provider(monkeypatch, FakeProvider([
        _draft("fmea_top", "Generator g dominates, said shorter.")]))
    r = client.post(f"/api/projects/{name}/reports/{rid}/sections/fmea_top/regenerate",
                    json={"instruction": "shorter"})
    assert r.status_code == 200, r.text[:300]
    assert r.json()["status"] == "running" and r.json()["report_id"] == rid
    body = _poll(client, name)
    assert body["status"] == "done" and body["version"] == 2, body
    assert len(provider.requests) == 1
    doc = client.get(f"/api/projects/{name}/reports/{rid}").json()
    assert doc["version"] == 2
    by_id = {s["section_id"]: s for s in doc["sections"]}
    assert by_id["fmea_top"]["blocks"][0]["md"] == "Generator g dominates, said shorter."
    v1 = client.get(f"/api/projects/{name}/reports/{rid}/versions/1").json()
    assert v1["sections"][0] == doc["sections"][0]  # the summary is untouched

    missing = client.post(f"/api/projects/{name}/reports/{rid}/sections/nope/regenerate",
                          json={})
    assert missing.status_code == 404
    assert missing.json()["detail"]["error_kind"] == "report_section_not_found"
    gone = client.post(f"/api/projects/{name}/reports/ffffffffffffffff/sections/fmea_top/regenerate",
                       json={})
    assert gone.status_code == 404
    assert gone.json()["detail"]["error_kind"] == "report_not_found"


def test_generate_without_any_evidence_is_400_no_evidence(client, api_project, monkeypatch):
    name = api_project("gen-empty")
    _install_provider(monkeypatch, FakeProvider([]))
    # A loaded network already yields an on-demand COPT screen, which IS an
    # adequacy surface; an empty session is one with no write-up at all.
    import routers.report_jobs as jobs
    monkeypatch.setattr(jobs, "_current_study_report", lambda: None)
    r = client.post(f"/api/projects/{name}/reports/generate", json={})
    assert r.status_code == 400, r.text[:300]
    assert r.json()["detail"]["error_kind"] == "no_evidence"
    assert client.get(f"/api/projects/{name}/reports").json() == []
    assert client.get(f"/api/projects/{name}/reports/generate/status").status_code == 204


def test_a_provider_that_cannot_be_built_is_400_with_the_seam_kind(
        client, api_project, session_state, monkeypatch):
    name = api_project("gen-nokey")
    _store_eh_report(session_state(client))
    import routers.report_jobs as jobs
    monkeypatch.setattr(jobs, "_provider_for_profile", lambda profile: (None, "missing_api_key"))
    r = client.post(f"/api/projects/{name}/reports/generate", json={})
    assert r.status_code == 400, r.text[:300]
    assert r.json()["detail"]["error_kind"] == "missing_api_key"


@pytest.mark.parametrize("body", [{"sections": ["nope"]}, {"sections": []}])
def test_an_unknown_or_empty_section_list_is_refused(client, api_project, session_state,
                                                     monkeypatch, body):
    name = api_project("gen-sections")
    _store_eh_report(session_state(client))
    _install_provider(monkeypatch, FakeProvider([]))
    r = client.post(f"/api/projects/{name}/reports/generate", json=body)
    assert r.status_code in (404, 422), r.text[:300]
    if r.status_code == 404:
        assert r.json()["detail"]["error_kind"] == "report_section_not_found"


def test_generate_is_refused_under_a_foreign_lock(client, api_project, session_state,
                                                  monkeypatch, _auth_db, seeded_identity):
    import uuid as _uuid
    from datetime import datetime, UTC

    from fastapi.testclient import TestClient

    import main
    from db.models import OrgMembership, User
    from services.auth_service import hash_password
    from tests.conftest import attach_session

    name = api_project("gen-lock")
    _store_eh_report(session_state(client))
    _install_provider(monkeypatch, FakeProvider([]))
    assert client.post(f"/api/projects/{name}/lock").status_code == 200

    _engine, session_local = _auth_db
    with session_local() as db:
        u = User(id=_uuid.uuid4(), email=f"col-{_uuid.uuid4().hex[:6]}@example.com",
                 password_hash=hash_password("irrelevant"), status="active",
                 is_super_admin=False, created_at=datetime.now(tz=UTC))
        db.add(u)
        db.flush()
        db.add(OrgMembership(id=_uuid.uuid4(), user_id=u.id,
                             org_id=seeded_identity["org_id"], role="admin"))
        db.commit()
        uid = u.id
    with TestClient(main.app) as c:
        other = attach_session(c, session_local, uid)
        r = other.post(f"/api/projects/{name}/reports/generate", json={})
        assert r.status_code == 409, r.text[:300]
        assert r.json()["detail"]["error_kind"] == "project_locked"
