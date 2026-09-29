"""
WP13 (backend) — the round-trip chat tools: `import_edited_report` (write),
`diff_report_versions` (read), `export_report_pdf` (write),
`list_report_roundtrips` (read).

Each is a thin wrapper over the WP13 routes (`routers/reports.py`) called
in-process through `_route(...)` for the active project, exactly as the
WP6/WP11 report tools wrap theirs. Pinned here: registration parity and the
safety tier of each, the route map (real routes, present in the inventory),
CHATBOT.md rows, the manifest rows, and the dispatch of each tool through
the SAME handlers the HTTP tests cover — with the route's own `error_kind`s
passing through. WP12's reader/merge are the same fakes the route tests use.
"""
from __future__ import annotations

import inspect
import json
import pathlib
import subprocess

import pytest
from fastapi import HTTPException
from pydantic import BaseModel, Field

from models.energy_hub import ReferenceDesignReport, SectionState, empty_section_map
from models.report import Block, Paragraph, ReportDocument, Section
from services import chat_tools as T, chat_tools_schema as S, upload_service
from services.adequacy import eh_report as R
from services.reports import pdf, roundtrip_service, store
from services.reports.docx_reader import TemplateReadError
from services.reports.docx_writer import DOCX_MIME, render_document_docx

ROUNDTRIP_TOOLS = {
    "import_edited_report": "write",
    "diff_report_versions": "read",
    "export_report_pdf": "write",
    "list_report_roundtrips": "read",
}
PROJECT = "report-roundtrip-tools"
HAS_PENDING = "pending_instruction" in Section.model_fields


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


@pytest.fixture
def project(client, api_project, project_storage_dir, session_ctx):
    """A real project that is also the ACTIVE one for the tools on this thread."""
    from services.pypsa_service import PyPSAService

    name = api_project(PROJECT)
    token = PyPSAService.bind_request_context(session_ctx(client))
    try:
        yield project_storage_dir(name)
    finally:
        PyPSAService.reset_request_context(token)


def _store_eh_report() -> None:
    from routers.simulation import _state

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
    R.store_eh_report(_state, report)


def _evidence_report() -> str:
    _store_eh_report()
    from routers.reports import CreateReportBody, create_report as _h

    out = T._route(_h, CreateReportBody(mode="evidence_only", title="Tool report"),
                   project=T._report_project())
    return out["report_id"]


def _upload(project_dir, data: bytes, *, kind: str = "report_roundtrip",
            filename: str = "edited.docx") -> str:
    meta = upload_service.add_upload(PROJECT, data, filename, DOCX_MIME, kind=kind,
                                     project_dir=project_dir)
    return meta.file_id


def _docx(project_dir, rid: str) -> bytes:
    return render_document_docx(store.load_report(project_dir, rid), figure_bytes={})


def _result(doc: ReportDocument, *, changed: str, comment_on: str | None = None) -> RoundTripResult:
    return RoundTripResult(
        sections=[RoundTripSection(
            section_id=s.section_id, heading=s.heading,
            blocks=[Paragraph(md="Edited in Word.")] if s.section_id == changed else list(s.blocks),
            changed=s.section_id == changed,
            comments=["shorten this"] if s.section_id == comment_on else [])
            for s in doc.sections],
        unmatched=["stray"], accepted_tracked_changes=1)


def _fake_merge(base: ReportDocument, result) -> ReportDocument:
    by_id = {s.section_id: s for s in result.sections if s.section_id}
    sections = []
    for section in base.sections:
        got = by_id.get(section.section_id)
        update: dict = {}
        if got is not None and got.changed:
            update.update(source="user_edit", blocks=list(got.blocks))
        if got is not None and got.comments and HAS_PENDING:
            update["pending_instruction"] = "; ".join(got.comments)
        sections.append(section.model_copy(update=update) if update else section)
    return base.model_copy(update={"sections": sections, "version": base.version + 1})


@pytest.fixture
def fake_roundtrip(monkeypatch):
    def install(result):
        def read(data, base):
            if isinstance(result, Exception):
                raise result
            return result
        monkeypatch.setattr(roundtrip_service, "_read_edited_docx", lambda: read)
        monkeypatch.setattr(roundtrip_service, "_merge_round_trip", lambda: _fake_merge)
    return install


# ── registration ────────────────────────────────────────────────────────────

def test_every_roundtrip_tool_is_registered_with_its_tier():
    for name, tier in ROUNDTRIP_TOOLS.items():
        assert any(t["name"] == name for t in S.TOOLS), f"{name} missing from TOOLS"
        assert callable(T.DISPATCHERS.get(name)), f"{name} missing from DISPATCHERS"
        assert name in S.TOOL_ROUTES, f"{name} missing from TOOL_ROUTES"
        assert S.safety_tier_for(name) == tier, f"{name}: tier {S.safety_tier_for(name)}"


def test_every_roundtrip_tool_maps_to_a_real_route():
    expected = {
        "import_edited_report": [("POST", "/api/projects/{name}/reports/{report_id}/roundtrip")],
        "diff_report_versions": [
            ("GET", "/api/projects/{name}/reports/{report_id}/versions/{a}/diff/{b}")],
        "export_report_pdf": [("POST", "/api/projects/{name}/reports/{report_id}/export")],
        "list_report_roundtrips": [("GET", "/api/projects/{name}/uploads")],
    }
    for name, routes in expected.items():
        assert S.TOOL_ROUTES[name] == routes, f"{name}: {S.TOOL_ROUTES[name]}"


def test_the_new_routes_are_in_the_inventory_fixture():
    text = (pathlib.Path(__file__).resolve().parent / "fixtures"
            / "route_inventory_phase0.txt").read_text("utf-8")
    rows = {tuple(line.split("->", 1)[0].split()[:2]) for line in text.splitlines() if "->" in line}
    for method, path in (
        ("POST", "/api/projects/{name}/reports/{report_id}/roundtrip"),
        ("GET", "/api/projects/{name}/reports/{report_id}/versions/{a}/diff/{b}"),
        ("GET", "/api/projects/{name}/reports/capabilities"),
    ):
        assert (method, path) in rows, f"{method} {path} not in the inventory"


def test_schema_optional_fields_all_have_python_defaults():
    for name in ROUNDTRIP_TOOLS:
        sch = next(t for t in S.TOOLS if t["name"] == name)["input_schema"]
        sig = inspect.signature(T.DISPATCHERS[name])
        for field in sch["properties"]:
            assert field in sig.parameters, f"{name}: {field!r} is not a parameter"
            if field not in sch["required"]:
                assert sig.parameters[field].default is not inspect.Parameter.empty, (
                    f"{name}: optional {field!r} has no Python default")
    assert set(next(t for t in S.TOOLS if t["name"] == "import_edited_report")["input_schema"]["required"]) == {"report_id", "file_id"}
    assert set(next(t for t in S.TOOLS if t["name"] == "diff_report_versions")["input_schema"]["required"]) == {"report_id", "a", "b"}


def test_the_roundtrip_tools_are_not_lock_gated_by_the_chat_seam():
    assert not (set(ROUNDTRIP_TOOLS) & T._lock_gated_tool_names())


def test_chatbot_md_documents_every_roundtrip_tool():
    text = (pathlib.Path(__file__).resolve().parents[2] / "CHATBOT.md").read_text("utf-8")
    for name in ROUNDTRIP_TOOLS:
        assert f"`{name}`" in text, f"CHATBOT.md does not document {name}"
    assert "report_roundtrip" in text and "pending_instruction" in text
    assert "pdf_not_available" in text


def test_the_manifest_classifies_every_roundtrip_kind():
    manifest = json.loads((pathlib.Path(__file__).resolve().parents[2]
                           / "tool-error-kinds.json").read_text("utf-8"))["kinds"]
    for kind in ("roundtrip_unreadable", "roundtrip_not_a_report", "report_version_not_found",
                 "pdf_not_available", "pdf_conversion_failed"):
        assert kind in manifest, f"{kind} is not classified in tool-error-kinds.json"
        assert manifest[kind]["surface"] == "inline"


# ── dispatch ────────────────────────────────────────────────────────────────

def test_list_report_roundtrips_lists_only_that_kind(project):
    assert T.list_report_roundtrips() == []
    rid = _evidence_report()
    fid = _upload(project, _docx(project, rid))
    _upload(project, b"PK\x03\x04" + b"t" * 40, kind="report_template", filename="t.docx")
    listed = T.list_report_roundtrips()
    assert [u["file_id"] for u in listed] == [fid]
    assert listed[0]["kind"] == "report_roundtrip" and listed[0]["filename"] == "edited.docx"
    assert set(listed[0]) >= {"file_id", "filename", "mime", "kind", "size_kb", "uploaded_at"}
    assert [u["file_id"] for u in T.list_report_templates()] != [fid]


def test_import_edited_report_writes_the_next_version(project, fake_roundtrip):
    rid = _evidence_report()
    base = store.load_report(project, rid)
    fid = _upload(project, _docx(project, rid))
    fake_roundtrip(_result(base, changed="fmea_top", comment_on="certification"))
    out = T.import_edited_report(rid, fid)
    assert out["report_id"] == rid and out["version"] == 2
    assert out["template_file_id"] == fid
    assert out["result"]["accepted_tracked_changes"] == 1
    assert [s["section_id"] for s in out["result"]["sections"] if s["changed"]] == ["fmea_top"]
    assert "fmea_top" in out["message"] and "stray" in json.dumps(out["result"]["unmatched"])
    doc = store.load_report(project, rid)
    assert doc.version == 2 and doc.template_file_id == fid
    section = next(s for s in doc.sections if s.section_id == "fmea_top")
    assert section.source == "user_edit"
    # bind_as_template false leaves the binding alone.
    out = T.import_edited_report(rid, fid, bind_as_template=False)
    assert out["version"] == 3 and out["template_file_id"] == fid  # already bound by v2
    assert store.load_report(project, rid).template_file_id == fid


def test_import_edited_report_errors_pass_through(project, fake_roundtrip):
    rid = _evidence_report()
    fid = _upload(project, _docx(project, rid))
    fake_roundtrip(TemplateReadError("garbage"))
    with pytest.raises(HTTPException) as exc:
        T.import_edited_report(rid, fid)
    assert exc.value.status_code == 400
    assert exc.value.detail["error_kind"] == "roundtrip_unreadable"
    fake_roundtrip(RoundTripResult(sections=[RoundTripSection(section_id=None, heading="x")]))
    with pytest.raises(HTTPException) as exc:
        T.import_edited_report(rid, fid)
    assert exc.value.detail["error_kind"] == "roundtrip_not_a_report"
    with pytest.raises(HTTPException) as exc:
        T.import_edited_report(rid, "nope")
    assert exc.value.status_code == 404 and exc.value.detail["error_kind"] == "upload_not_found"
    with pytest.raises(HTTPException) as exc:
        T.import_edited_report("ffffffffffffffff", fid)
    assert exc.value.detail["error_kind"] == "report_not_found"


def test_diff_report_versions_on_stored_versions(project):
    rid = _evidence_report()
    v1 = store.load_report(project, rid)
    sections = [s.model_copy(update={"source": "user_edit", "blocks": [Paragraph(md="Edited.")]})
                if s.section_id == "fmea_top" else s for s in v1.sections]
    store.save_version(project, v1.model_copy(update={"sections": sections}))
    out = T.diff_report_versions(rid, 1, 2)
    assert out["a"] == 1 and out["b"] == 2
    rows = {r["section_id"]: r for r in out["sections"]}
    assert rows["fmea_top"]["change"] == "changed"
    assert rows["fmea_top"]["source_a"] == "code" and rows["fmea_top"]["source_b"] == "user_edit"
    assert all(r["change"] == "unchanged" for sid, r in rows.items() if sid != "fmea_top")
    assert out["changed"] == ["fmea_top"] and out["added"] == [] and out["removed"] == []
    with pytest.raises(HTTPException) as exc:
        T.diff_report_versions(rid, 1, 9)
    assert exc.value.status_code == 404
    assert exc.value.detail["error_kind"] == "report_version_not_found"


def test_export_report_pdf_501_and_200(project, monkeypatch):
    rid = _evidence_report()
    monkeypatch.setattr(pdf.shutil, "which", lambda cmd: None)
    with pytest.raises(HTTPException) as exc:
        T.export_report_pdf(rid)
    assert exc.value.status_code == 501 and exc.value.detail["error_kind"] == "pdf_not_available"

    monkeypatch.setattr(pdf.shutil, "which", lambda cmd: "/usr/bin/soffice")

    def run(args, **kwargs):
        outdir = pathlib.Path(args[args.index("--outdir") + 1])
        (outdir / (pathlib.Path(args[-1]).stem + ".pdf")).write_bytes(b"%PDF-1.4 fake")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
    monkeypatch.setattr(pdf.subprocess, "run", run)
    out = T.export_report_pdf()  # the newest report
    assert out["report_id"] == rid and out["mime"] == "application/pdf"
    assert out["kind"] == "agent_export" and out["filename"] == f"report_{rid}_v1.pdf"
    assert "file strip" in out["message"]
    assert upload_service.get_upload_bytes(PROJECT, out["file_id"], project_dir=project) == b"%PDF-1.4 fake"

    def failing(args, **kwargs):
        return subprocess.CompletedProcess(args, 77, stdout="", stderr="Error: cannot load")
    monkeypatch.setattr(pdf.subprocess, "run", failing)
    with pytest.raises(HTTPException) as exc:
        T.export_report_pdf(rid, version=1, filename="again")
    assert exc.value.status_code == 500
    assert exc.value.detail["error_kind"] == "pdf_conversion_failed"
    assert "cannot load" in exc.value.detail["message"]
