"""
WP0 — ``export_eh_report_docx``: the stored ``ReferenceDesignReport`` as an
agent-export ``.docx`` chip in the chat file strip.
"""
from __future__ import annotations

import io

import pytest
from docx import Document
from fastapi import HTTPException

from models.energy_hub import (
    ReferenceDesignReport,
    SectionState,
    empty_section_map,
)
from services import chat_tools, upload_service
from services.adequacy import eh_report as R
from services.reports.docx_writer import DOCX_MIME

from tests.conftest import build_network


@pytest.fixture
def install_with_uploads(tmp_projects_dir, install_network):
    n = build_network()
    install_network(n, name="P")
    (tmp_projects_dir / "P").mkdir(parents=True, exist_ok=True)
    (tmp_projects_dir / "P" / "network.nc").write_bytes(b"")
    return tmp_projects_dir / "P"


def _store_report() -> None:
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
        sections=sections, ens_cap_permyriad=10.0,
    )
    R.store_eh_report(_state, report)


def test_no_stored_report_is_a_named_error(install_with_uploads):
    with pytest.raises(HTTPException) as exc:
        chat_tools.export_eh_report_docx()
    assert exc.value.status_code == 404
    assert exc.value.detail["error_kind"] == "eh_report_not_found"
    assert "run_eh_study" in exc.value.detail["message"]


def test_stored_report_becomes_a_docx_export_chip(install_with_uploads):
    _store_report()
    meta = chat_tools.export_eh_report_docx()
    assert meta["kind"] == "agent_export"
    assert meta["mime"] == DOCX_MIME
    assert meta["filename"].endswith(".docx")
    blob = upload_service.get_upload_bytes("P", meta["file_id"])
    doc = Document(io.BytesIO(blob))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "Residual failure modes" in text
    # The one established section carried a ranking → its figure is embedded.
    assert len(doc.inline_shapes) == 1


def test_custom_filename_gets_the_docx_suffix(install_with_uploads):
    _store_report()
    meta = chat_tools.export_eh_report_docx(filename="client-report")
    assert meta["filename"] == "client-report.docx"


def test_no_active_project_raises_400(tmp_projects_dir, monkeypatch):
    from services.pypsa_service import PyPSAService
    monkeypatch.setattr(PyPSAService, "get_loaded_project", staticmethod(lambda: None))
    with pytest.raises(HTTPException) as exc:
        chat_tools.export_eh_report_docx()
    assert exc.value.status_code == 400


def test_tool_is_registered_with_a_write_tier():
    from services import chat_tools_schema as S
    tool = next(t for t in S.TOOLS if t["name"] == "export_eh_report_docx")
    assert S.safety_tier_for(tool["name"]) == "write"
    assert "export_eh_report_docx" in chat_tools.DISPATCHERS
