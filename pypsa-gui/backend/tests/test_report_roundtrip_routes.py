"""
WP13 (backend) — the round trip over HTTP.

  POST /api/projects/{name}/uploads?kind=report_roundtrip              the edited copy as an upload
  POST /api/projects/{name}/reports/{id}/roundtrip {file_id, bind_as_template}
                                                                        → new version + RoundTripResult
  GET  /api/projects/{name}/reports/{id}/versions/{a}/diff/{b}          per-section change
  GET  /api/projects/{name}/reports/capabilities                        {pdf: bool}
  POST /api/projects/{name}/reports/{id}/export {format: pdf}           501 / 200 / 500
  POST /api/projects/{name}/reports/{id}/sections/{sid}/regenerate      honours pending_instruction

WP12's reader and merge (`services.reports.roundtrip`) are reached only
through the accessors `services.reports.roundtrip_service` exposes
(`_read_edited_docx`, `_merge_round_trip`); here they are replaced with
fakes shaped exactly like the pinned `RoundTripResult`, so the routes are
pinned whether or not that module is in the checkout. The PDF legs replace
`subprocess.run` with a fake that writes a `.pdf` (200) or exits non-zero
(500) — `soffice` cannot load a `.docx` in this container.
"""
from __future__ import annotations

import json
import subprocess
import time
import uuid as _uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

import main
from models.energy_hub import ReferenceDesignReport, SectionState, empty_section_map
from models.report import Block, Paragraph, ReportDocument, Section
from services.adequacy import eh_report as R
from services.llm_fake import FakeProvider
from services.llm_provider import LLMEvent
from services.reports import pdf, roundtrip_service, store
from services.reports.docx_reader import TemplateReadError
from services.reports.docx_writer import DOCX_MIME, render_document_docx
from tests.conftest import attach_session

HAS_PENDING = "pending_instruction" in Section.model_fields


# ── the pinned WP12 shapes, as fakes ────────────────────────────────────────

class RoundTripSection(BaseModel):
    section_id: str | None
    heading: str
    blocks: list[Block] = Field(default_factory=list)
    changed: bool = False
    comments: list[str] = Field(default_factory=list)


class RoundTripResult(BaseModel):
    sections: list[RoundTripSection] = Field(default_factory=list)
    unmatched: list[str] = Field(default_factory=list)
    comments_global: list[str] = Field(default_factory=list)
    accepted_tracked_changes: int = 0


def _fake_merge(base: ReportDocument, result: RoundTripResult) -> ReportDocument:
    """The pinned merge rule, minimally: changed → user_edit + blocks; comments → pending."""
    by_id = {s.section_id: s for s in result.sections if s.section_id}
    sections = []
    for section in base.sections:
        got = by_id.get(section.section_id)
        if got is None:
            sections.append(section)
            continue
        update: dict = {}
        if got.changed:
            update.update(source="user_edit", blocks=list(got.blocks))
        if got.comments and HAS_PENDING:
            update["pending_instruction"] = "; ".join(got.comments)
        sections.append(section.model_copy(update=update) if update else section)
    return base.model_copy(update={"sections": sections, "version": base.version + 1})


class _Fakes:
    def __init__(self, result: RoundTripResult | Exception) -> None:
        self.result = result
        self.read_calls: list[tuple[int, str]] = []
        self.merge_calls: list[int] = []

    def read(self, data: bytes, base: ReportDocument):
        self.read_calls.append((len(data), base.report_id))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def merge(self, base: ReportDocument, result):
        self.merge_calls.append(base.version)
        return _fake_merge(base, result)


@pytest.fixture
def fake_roundtrip(monkeypatch):
    def install(result: RoundTripResult | Exception) -> _Fakes:
        fakes = _Fakes(result)
        monkeypatch.setattr(roundtrip_service, "_read_edited_docx", lambda: fakes.read)
        monkeypatch.setattr(roundtrip_service, "_merge_round_trip", lambda: fakes.merge)
        return fakes
    return install


@pytest.fixture
def same_org_other_user(_auth_db, seeded_identity):
    from db.models import OrgMembership, User
    from services.auth_service import hash_password

    _engine, session_local = _auth_db
    with session_local() as db:
        u = User(
            id=_uuid.uuid4(),
            email=f"colleague-{_uuid.uuid4().hex[:6]}@example.com",
            password_hash=hash_password("irrelevant"),
            status="active",
            is_super_admin=False,
            created_at=datetime.now(tz=UTC),
        )
        db.add(u)
        db.flush()
        db.add(OrgMembership(id=_uuid.uuid4(), user_id=u.id,
                             org_id=seeded_identity["org_id"], role="admin"))
        db.commit()
        uid = u.id
    with TestClient(main.app) as c:
        yield attach_session(c, session_local, uid)


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


def _report(client, name: str, session_state=None, *, title: str = "Round-trip report") -> str:
    if session_state is not None:
        _store_eh_report(session_state(client))
    r = client.post(f"/api/projects/{name}/reports",
                    json={"mode": "evidence_only", "title": title})
    assert r.status_code == 200, r.text[:300]
    return r.json()["report_id"]


def _upload(client, name: str, data: bytes, *, kind: str | None = "report_roundtrip",
            filename: str = "edited.docx", mime: str = DOCX_MIME) -> dict:
    params = {} if kind is None else {"kind": kind}
    r = client.post(f"/api/projects/{name}/uploads", params=params,
                    files={"file": (filename, data, mime)})
    assert r.status_code == 200, r.text[:300]
    return r.json()


def _docx_for(client, name: str, rid: str) -> bytes:
    doc = ReportDocument.model_validate(client.get(f"/api/projects/{name}/reports/{rid}").json())
    return render_document_docx(doc, figure_bytes={})


def _result_for(doc: dict, *, changed: str, comment_on: str | None = None,
                comment: str = "shorten this") -> RoundTripResult:
    sections = []
    for s in doc["sections"]:
        sid = s["section_id"]
        sections.append(RoundTripSection(
            section_id=sid, heading=s["heading"],
            blocks=[Paragraph(md="Edited by the user in Word.")] if sid == changed
            else list(s["blocks"]),
            changed=sid == changed,
            comments=[comment] if sid == comment_on else [],
        ))
    return RoundTripResult(sections=sections, unmatched=["A stray paragraph"],
                           comments_global=[], accepted_tracked_changes=2)


def _draft(section_id: str, *paragraphs: str) -> dict:
    text = json.dumps({"section_id": section_id, "paragraphs": list(paragraphs),
                       "bullets": []})
    return {"events": [LLMEvent(type="text_delta", text=text)],
            "blocks": [{"type": "text", "text": text}]}


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


# ── the upload kind ─────────────────────────────────────────────────────────

def test_a_docx_uploaded_as_report_roundtrip_lists_under_that_kind(client, api_project):
    name = api_project("rt-upload")
    rid = _report(client, name)
    meta = _upload(client, name, _docx_for(client, name, rid))
    assert meta["kind"] == "report_roundtrip" and meta["mime"] == DOCX_MIME
    only = client.get(f"/api/projects/{name}/uploads", params={"kind": "report_roundtrip"})
    assert only.status_code == 200, only.text
    assert [u["file_id"] for u in only.json()] == [meta["file_id"]]
    assert client.get(f"/api/projects/{name}/uploads",
                      params={"kind": "report_template"}).json() == []
    # A user upload re-sent as a round trip is promoted, like a template.
    doc = ReportDocument.model_validate(client.get(f"/api/projects/{name}/reports/{rid}").json())
    other = render_document_docx(doc.model_copy(update={"title": "Another copy"}), figure_bytes={})
    plain = _upload(client, name, other, kind=None, filename="p.docx")
    assert plain["kind"] == "user_upload"
    again = _upload(client, name, other, filename="p.docx")
    assert again["file_id"] == plain["file_id"] and again["kind"] == "report_roundtrip"
    # Never demoted: re-sent as a template it stays a round-trip copy.
    third = _upload(client, name, other, kind="report_template", filename="p.docx")
    assert third["kind"] == "report_roundtrip"


# ── POST …/roundtrip ────────────────────────────────────────────────────────

def test_roundtrip_writes_the_next_version_and_binds_the_file_as_template(
        client, api_project, project_storage_dir, session_state, fake_roundtrip):
    name = api_project("rt-ok")
    rid = _report(client, name, session_state)
    v1 = client.get(f"/api/projects/{name}/reports/{rid}").json()
    up = _upload(client, name, _docx_for(client, name, rid))
    fakes = fake_roundtrip(_result_for(v1, changed="fmea_top", comment_on="certification"))

    r = client.post(f"/api/projects/{name}/reports/{rid}/roundtrip", json={"file_id": up["file_id"]})
    assert r.status_code == 200, r.text[:400]
    body = r.json()
    assert body["report_id"] == rid and body["version"] == 2
    assert body["template_file_id"] == up["file_id"]
    result = body["result"]
    assert result["accepted_tracked_changes"] == 2 and result["unmatched"] == ["A stray paragraph"]
    assert [s["section_id"] for s in result["sections"] if s["changed"]] == ["fmea_top"]
    assert len(fakes.read_calls) == 1 and fakes.read_calls[0][1] == rid
    assert fakes.read_calls[0][0] == up["size"], "the upload's bytes reach the reader"
    assert fakes.merge_calls == [1]

    v2 = client.get(f"/api/projects/{name}/reports/{rid}").json()
    assert v2["version"] == 2 and v2["template_file_id"] == up["file_id"]
    by_id = {s["section_id"]: s for s in v2["sections"]}
    assert by_id["fmea_top"]["source"] == "user_edit"
    assert by_id["fmea_top"]["blocks"][0] == {"type": "paragraph", "md": "Edited by the user in Word."}
    if HAS_PENDING:
        assert by_id["certification"]["pending_instruction"] == "shorten this"
    # The version file of v1 is untouched.
    v1_again = client.get(f"/api/projects/{name}/reports/{rid}/versions/1").json()
    assert v1_again == v1
    meta = store.load_meta(project_storage_dir(name), rid)
    assert meta.latest_version == 2 and meta.template_mode == "untagged"
    assert meta.mapping_plan is None
    listed = client.get(f"/api/projects/{name}/uploads", params={"kind": "report_roundtrip"}).json()
    assert [u["file_id"] for u in listed] == [up["file_id"]]


def test_roundtrip_without_binding_keeps_the_template_as_it_was(
        client, api_project, session_state, fake_roundtrip):
    name = api_project("rt-nobind")
    rid = _report(client, name, session_state)
    v1 = client.get(f"/api/projects/{name}/reports/{rid}").json()
    up = _upload(client, name, _docx_for(client, name, rid))
    fake_roundtrip(_result_for(v1, changed="fmea_top"))
    r = client.post(f"/api/projects/{name}/reports/{rid}/roundtrip",
                    json={"file_id": up["file_id"], "bind_as_template": False})
    assert r.status_code == 200, r.text[:400]
    assert r.json()["template_file_id"] is None and r.json()["version"] == 2
    assert client.get(f"/api/projects/{name}/reports/{rid}").json()["template_file_id"] is None


def test_roundtrip_404s(client, api_project, session_state, fake_roundtrip):
    name = api_project("rt-404")
    rid = _report(client, name, session_state)
    up = _upload(client, name, _docx_for(client, name, rid))
    fake_roundtrip(RoundTripResult())
    r = client.post(f"/api/projects/{name}/reports/ffffffffffffffff/roundtrip",
                    json={"file_id": up["file_id"]})
    assert r.status_code == 404 and r.json()["detail"]["error_kind"] == "report_not_found"
    r = client.post(f"/api/projects/{name}/reports/{rid}/roundtrip", json={"file_id": "nope"})
    assert r.status_code == 404, r.text[:200]
    assert r.json()["detail"]["error_kind"] == "upload_not_found"
    r = client.post(f"/api/projects/{name}/reports/not-an-id/roundtrip",
                    json={"file_id": up["file_id"]})
    assert r.status_code == 400 and r.json()["detail"]["error_kind"] == "invalid_report_id"
    # Nothing was written by any of these.
    assert client.get(f"/api/projects/{name}/reports/{rid}").json()["version"] == 1


def test_roundtrip_unreadable_and_not_a_report_are_400s(
        client, api_project, session_state, fake_roundtrip):
    name = api_project("rt-400")
    rid = _report(client, name, session_state)
    up = _upload(client, name, _docx_for(client, name, rid))

    fake_roundtrip(TemplateReadError("not a Word document"))
    r = client.post(f"/api/projects/{name}/reports/{rid}/roundtrip", json={"file_id": up["file_id"]})
    assert r.status_code == 400, r.text[:300]
    assert r.json()["detail"]["error_kind"] == "roundtrip_unreadable"
    assert "not a Word document" in r.json()["detail"]["message"]

    fake_roundtrip(RoundTripResult(
        sections=[RoundTripSection(section_id=None, heading="Free text", changed=True)],
        unmatched=["Free text"]))
    r = client.post(f"/api/projects/{name}/reports/{rid}/roundtrip", json={"file_id": up["file_id"]})
    assert r.status_code == 400, r.text[:300]
    assert r.json()["detail"]["error_kind"] == "roundtrip_not_a_report"
    assert client.get(f"/api/projects/{name}/reports/{rid}").json()["version"] == 1


def test_roundtrip_is_refused_under_a_foreign_lock(
        client, api_project, session_state, fake_roundtrip, same_org_other_user):
    name = api_project("rt-lock")
    rid = _report(client, name, session_state)
    v1 = client.get(f"/api/projects/{name}/reports/{rid}").json()
    up = _upload(client, name, _docx_for(client, name, rid))
    fake_roundtrip(_result_for(v1, changed="fmea_top"))
    assert client.post(f"/api/projects/{name}/lock").status_code == 200
    r = same_org_other_user.post(f"/api/projects/{name}/reports/{rid}/roundtrip",
                                 json={"file_id": up["file_id"]})
    assert r.status_code == 409, r.text[:200]
    assert r.json()["detail"]["error_kind"] == "project_locked"
    assert same_org_other_user.get(
        f"/api/projects/{name}/reports/{rid}/versions/1/diff/1").status_code == 200
    r = client.post(f"/api/projects/{name}/reports/{rid}/roundtrip", json={"file_id": up["file_id"]})
    assert r.status_code == 200, r.text[:200]


def test_roundtrip_is_409_while_a_report_job_is_running(
        client, api_project, session_state, fake_roundtrip):
    from services.pypsa_service import PyPSAService

    name = api_project("rt-inflight")
    rid = _report(client, name, session_state)
    v1 = client.get(f"/api/projects/{name}/reports/{rid}").json()
    up = _upload(client, name, _docx_for(client, name, rid))
    fake_roundtrip(_result_for(v1, changed="fmea_top"))
    state = session_state(client)
    with PyPSAService.get_solver_state_lock():
        state["report_job"] = {"status": "running", "report_id": rid, "thread": None}
    try:
        r = client.post(f"/api/projects/{name}/reports/{rid}/roundtrip",
                        json={"file_id": up["file_id"]})
    finally:
        with PyPSAService.get_solver_state_lock():
            state["report_job"] = None
    assert r.status_code == 409, r.text[:200]
    assert r.json()["detail"]["error_kind"] == "report_job_in_flight"
    assert client.get(f"/api/projects/{name}/reports/{rid}").json()["version"] == 1


def test_roundtrip_without_wp12_is_500_tool_error_before_anything_is_claimed(
        client, api_project, session_state, monkeypatch):
    name = api_project("rt-nomodule")
    rid = _report(client, name, session_state)
    up = _upload(client, name, _docx_for(client, name, rid))

    def missing():
        raise ImportError("No module named 'services.reports.roundtrip'")
    monkeypatch.setattr(roundtrip_service, "_roundtrip_module", missing)
    r = client.post(f"/api/projects/{name}/reports/{rid}/roundtrip", json={"file_id": up["file_id"]})
    assert r.status_code == 500, r.text[:300]
    assert r.json()["detail"]["error_kind"] == "tool_error"
    assert "roundtrip" in r.json()["detail"]["message"]
    assert client.get(f"/api/projects/{name}/reports/{rid}").json()["version"] == 1


# ── the diff ────────────────────────────────────────────────────────────────

def test_diff_marks_changed_added_removed_and_unchanged_on_stored_versions(
        client, api_project, project_storage_dir, session_state):
    name = api_project("rt-diff")
    rid = _report(client, name, session_state)
    pdir = project_storage_dir(name)
    v1 = store.load_report(pdir, rid)
    ids = [s.section_id for s in v1.sections]
    assert "fmea_top" in ids and len(ids) >= 3
    removed_id = next(sid for sid in ids if sid != "fmea_top")
    sections = []
    for s in v1.sections:
        if s.section_id == removed_id:
            continue
        if s.section_id == "fmea_top":
            s = s.model_copy(update={"source": "user_edit",
                                     "blocks": [Paragraph(md="Edited in Word.")]})
        sections.append(s)
    sections.append(Section(section_id="appendix_user", heading="User appendix",
                            source="user_edit", status="ok",
                            blocks=[Paragraph(md="New text.")]))
    assert store.save_version(pdir, v1.model_copy(update={"sections": sections})) == 2

    r = client.get(f"/api/projects/{name}/reports/{rid}/versions/1/diff/2")
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    assert body["a"] == 1 and body["b"] == 2
    rows = {row["section_id"]: row for row in body["sections"]}
    assert set(rows) == set(ids) | {"appendix_user"}
    assert rows["fmea_top"]["change"] == "changed"
    assert rows["fmea_top"]["source_a"] == "code" and rows["fmea_top"]["source_b"] == "user_edit"
    assert rows[removed_id]["change"] == "removed" and rows[removed_id]["source_b"] is None
    assert rows["appendix_user"]["change"] == "added" and rows["appendix_user"]["source_a"] is None
    assert rows["appendix_user"]["heading"] == "User appendix"
    for sid in ids:
        if sid not in (removed_id, "fmea_top"):
            assert rows[sid]["change"] == "unchanged", sid
    for row in body["sections"]:
        assert set(row) == {"section_id", "heading", "change", "source_a", "source_b",
                            "pending_instruction", "comments"}
        assert row["pending_instruction"] is None and row["comments"] == []
    # Order: the union in a's order, then b's additions.
    assert [row["section_id"] for row in body["sections"]] == ids + ["appendix_user"]
    # The same version against itself is all unchanged; the reverse swaps added/removed.
    same = client.get(f"/api/projects/{name}/reports/{rid}/versions/2/diff/2").json()
    assert {row["change"] for row in same["sections"]} == {"unchanged"}
    rev = {row["section_id"]: row for row in
           client.get(f"/api/projects/{name}/reports/{rid}/versions/2/diff/1").json()["sections"]}
    assert rev[removed_id]["change"] == "added" and rev["appendix_user"]["change"] == "removed"


def test_diff_404s(client, api_project, session_state):
    name = api_project("rt-diff-404")
    rid = _report(client, name, session_state)
    r = client.get(f"/api/projects/{name}/reports/{rid}/versions/1/diff/2")
    assert r.status_code == 404, r.text[:200]
    assert r.json()["detail"]["error_kind"] == "report_version_not_found"
    r = client.get(f"/api/projects/{name}/reports/{rid}/versions/0/diff/1")
    assert r.status_code == 404 and r.json()["detail"]["error_kind"] == "report_version_not_found"
    r = client.get(f"/api/projects/{name}/reports/ffffffffffffffff/versions/1/diff/1")
    assert r.status_code == 404 and r.json()["detail"]["error_kind"] == "report_not_found"
    r = client.get(f"/api/projects/{name}/reports/nope/versions/1/diff/1")
    assert r.status_code == 400 and r.json()["detail"]["error_kind"] == "invalid_report_id"


# ── capabilities ────────────────────────────────────────────────────────────

def test_capabilities_reports_pdf_from_which(client, api_project, monkeypatch):
    name = api_project("rt-caps")
    monkeypatch.setattr(pdf.shutil, "which", lambda cmd: "/usr/bin/soffice" if cmd == "soffice" else None)
    r = client.get(f"/api/projects/{name}/reports/capabilities")
    assert r.status_code == 200, r.text
    assert r.json() == {"pdf": True}
    monkeypatch.setattr(pdf.shutil, "which", lambda cmd: "/usr/bin/libreoffice" if cmd == "libreoffice" else None)
    assert client.get(f"/api/projects/{name}/reports/capabilities").json() == {"pdf": True}
    monkeypatch.setattr(pdf.shutil, "which", lambda cmd: None)
    assert client.get(f"/api/projects/{name}/reports/capabilities").json() == {"pdf": False}
    # The literal segment is not read as a report id.
    assert client.get(f"/api/projects/{name}/reports").json() == []


# ── export: format ──────────────────────────────────────────────────────────

def _fake_soffice(*, returncode: int = 0, stderr: str = "", write: bool = True,
                  payload: bytes = b"%PDF-1.4\n% fake\n%%EOF\n"):
    calls: list[list[str]] = []

    def run(args, **kwargs):
        calls.append(list(args))
        assert args[1:4] == ["--headless", "--convert-to", "pdf"]
        outdir = Path(args[args.index("--outdir") + 1])
        source = Path(args[-1])
        assert source.is_file() and source.suffix == ".docx"
        if write:
            (outdir / (source.stem + ".pdf")).write_bytes(payload)
        return subprocess.CompletedProcess(args, returncode, stdout="", stderr=stderr)

    run.calls = calls
    return run


def test_export_pdf_is_501_without_soffice_and_docx_is_the_default(
        client, api_project, session_state, monkeypatch):
    name = api_project("rt-pdf-501")
    rid = _report(client, name, session_state)
    monkeypatch.setattr(pdf.shutil, "which", lambda cmd: None)
    r = client.post(f"/api/projects/{name}/reports/{rid}/export", json={"format": "pdf"})
    assert r.status_code == 501, r.text[:300]
    assert r.json()["detail"]["error_kind"] == "pdf_not_available"
    r = client.post(f"/api/projects/{name}/reports/{rid}/export", json={})
    assert r.status_code == 200 and r.json()["mime"] == DOCX_MIME
    assert r.json()["filename"].endswith(".docx")
    r = client.post(f"/api/projects/{name}/reports/{rid}/export", json={"format": "odt"})
    assert r.status_code == 422


def test_export_pdf_saves_an_agent_export_with_the_pdf_mime(
        client, api_project, session_state, monkeypatch):
    name = api_project("rt-pdf-200")
    rid = _report(client, name, session_state)
    monkeypatch.setattr(pdf.shutil, "which", lambda cmd: "/usr/bin/soffice")
    run = _fake_soffice()
    monkeypatch.setattr(pdf.subprocess, "run", run)
    r = client.post(f"/api/projects/{name}/reports/{rid}/export",
                    json={"format": "pdf", "filename": "client-copy"})
    assert r.status_code == 200, r.text[:300]
    meta = r.json()
    assert meta["kind"] == "agent_export" and meta["mime"] == "application/pdf"
    assert meta["filename"] == "client-copy.pdf"
    assert len(run.calls) == 1 and run.calls[0][0] == "/usr/bin/soffice"
    blob = client.get(f"/api/projects/{name}/uploads/{meta['file_id']}/blob")
    assert blob.status_code == 200 and blob.content.startswith(b"%PDF-")
    # The same bytes again dedup to the same upload (its first name kept)...
    r = client.post(f"/api/projects/{name}/reports/{rid}/export", json={"format": "pdf"})
    assert r.status_code == 200 and r.json()["file_id"] == meta["file_id"]
    # ...and different bytes get the default name.
    monkeypatch.setattr(pdf.subprocess, "run", _fake_soffice(payload=b"%PDF-1.4\n% other\n"))
    r = client.post(f"/api/projects/{name}/reports/{rid}/export", json={"format": "pdf"})
    assert r.status_code == 200 and r.json()["filename"] == f"report_{rid}_v1.pdf"
    assert r.json()["file_id"] != meta["file_id"]


def test_export_pdf_is_500_pdf_conversion_failed_when_soffice_fails(
        client, api_project, session_state, monkeypatch):
    name = api_project("rt-pdf-500")
    rid = _report(client, name, session_state)
    monkeypatch.setattr(pdf.shutil, "which", lambda cmd: "/usr/bin/soffice")
    monkeypatch.setattr(pdf.subprocess, "run", _fake_soffice(
        returncode=1, stderr="Error: source file could not be loaded\nmore lines", write=False))
    r = client.post(f"/api/projects/{name}/reports/{rid}/export", json={"format": "pdf"})
    assert r.status_code == 500, r.text[:300]
    detail = r.json()["detail"]
    assert detail["error_kind"] == "pdf_conversion_failed"
    assert "source file could not be loaded" in detail["message"]
    # Exit 0 but no file is a failure too, not an empty upload.
    monkeypatch.setattr(pdf.subprocess, "run", _fake_soffice(returncode=0, write=False))
    r = client.post(f"/api/projects/{name}/reports/{rid}/export", json={"format": "pdf"})
    assert r.status_code == 500 and r.json()["detail"]["error_kind"] == "pdf_conversion_failed"
    assert client.get(f"/api/projects/{name}/uploads").json() == []


# ── regenerate honours pending_instruction ──────────────────────────────────

@pytest.mark.xfail(not HAS_PENDING, strict=False,
                   reason="Section.pending_instruction is WP12's additive field; absent in this checkout")
def test_regenerate_without_instruction_uses_and_clears_the_pending_one(
        client, api_project, project_storage_dir, session_state, monkeypatch):
    name = api_project("rt-regen")
    rid = _report(client, name, session_state)
    pdir = project_storage_dir(name)
    v1 = store.load_report(pdir, rid)
    sections = [s.model_copy(update={"pending_instruction": "shorten this"})
                if s.section_id == "fmea_top" else s for s in v1.sections]
    assert store.save_version(pdir, v1.model_copy(update={"sections": sections})) == 2
    assert client.get(f"/api/projects/{name}/reports/{rid}").json()["sections"][
        [s.section_id for s in v1.sections].index("fmea_top")]["pending_instruction"] == "shorten this"

    provider = _install_provider(monkeypatch, FakeProvider([_draft("fmea_top", "Shorter.")]))
    r = client.post(f"/api/projects/{name}/reports/{rid}/sections/fmea_top/regenerate", json={})
    assert r.status_code == 200, r.text[:300]
    body = _poll(client, name)
    assert body["status"] == "done" and body["version"] == 3, body
    assert len(provider.requests) == 1
    assert "shorten this" in json.dumps(provider.requests[0].messages, default=str)
    v3 = client.get(f"/api/projects/{name}/reports/{rid}").json()
    by_id = {s["section_id"]: s for s in v3["sections"]}
    assert by_id["fmea_top"]["pending_instruction"] is None
    assert by_id["fmea_top"]["blocks"][0]["md"] == "Shorter." and by_id["fmea_top"]["source"] == "llm"
    # v2 keeps its pending instruction (a version file is never rewritten).
    v2 = client.get(f"/api/projects/{name}/reports/{rid}/versions/2").json()
    assert {s["section_id"]: s for s in v2["sections"]}["fmea_top"]["pending_instruction"] == "shorten this"

    # An explicit instruction wins over a pending one, and the pending one is cleared too.
    sections = [s.model_copy(update={"pending_instruction": "make it longer"})
                if s.section_id == "fmea_top" else s
                for s in store.load_report(pdir, rid).sections]
    store.save_version(pdir, store.load_report(pdir, rid).model_copy(update={"sections": sections}))
    provider = _install_provider(monkeypatch, FakeProvider([_draft("fmea_top", "Explicit.")]))
    r = client.post(f"/api/projects/{name}/reports/{rid}/sections/fmea_top/regenerate",
                    json={"instruction": "use the explicit one"})
    assert r.status_code == 200 and _poll(client, name)["status"] == "done"
    sent = json.dumps(provider.requests[0].messages, default=str)
    assert "use the explicit one" in sent and "make it longer" not in sent
    latest = client.get(f"/api/projects/{name}/reports/{rid}").json()
    assert {s["section_id"]: s for s in latest["sections"]}["fmea_top"]["pending_instruction"] is None
