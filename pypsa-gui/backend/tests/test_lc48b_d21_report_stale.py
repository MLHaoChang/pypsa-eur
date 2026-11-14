"""LC-48b, LC-D21.stale_report: a report written before its study changed is not exported.

Owner decision LC48B-STALE-1: exporting a report version whose study changed since it
was written is 409 ``report_stale`` until the report is generated again. "Changed" is the
comparison the viewer already shows ("evidence changed since vN"): the version's stored
``evidence_hash`` against the hash of the project's evidence now.

The seam is the copilot's report tools over one stored study report and a scripted model,
as in ``test_lc06_d21_reports.py`` (archetype ``strong_grid``, ENS cap 10, MC LOLE 3.21
h/yr). The study changing is a new study result stored the way a study run stores it
(``eh_report.store_eh_report``), the same archetype with MC LOLE 4.50 h/yr.

Hand oracle:
- right after generation, the export is an ``agent_export`` chip;
- after the study changes, exporting that report (by id, and the newest by default) is
  409 report_stale, and ``lc43_gates.state`` (no new upload, no new version) is as before;
- an edit to the network alone does not change the study's evidence, so it leaves the
  export open;
- a report generated after the change exports, and its headline reads 4.50 h/yr.
"""

# ruff: noqa: F811 -- shared public harness fixtures
import logging

import pytest
from fastapi import HTTPException

import routers.reports as reports_router
from models.energy_hub import ReferenceDesignReport, SectionState, empty_section_map
from services import chat_tools as T
from services.adequacy import eh_report as R
from services.llm_fake import FakeProvider
from services.pypsa_service import PyPSAService
from services.reports import store
from tests.lc43_gates import refused, state
from tests.test_chat_report_tools import (  # noqa: F401
    PROJECT,
    _install_provider,
    _poll,
    _script,
    project,
)
from tests.test_lc06_d21_reports import SECTIONS, _generated
from tests.test_workflow_tools import helpers_state  # noqa: F401


def _study_rerun(mc_lole_h: float) -> None:
    """The study is run again and stores its new result, as the study route does."""
    from routers.simulation import _state

    sections = empty_section_map(default="skipped")
    sections["fmea_top"] = SectionState(
        status="ok",
        payload={
            "top": [
                {
                    "rank": 1,
                    "mode_id": "gen:g:forced_outage",
                    "component_class": "Generator",
                    "name": "g",
                    "failure_class": "A",
                    "occurrence_per_year": 1.0,
                    "occurrence_basis": "FOR",
                    "severity_eur": 10.0,
                    "criticality_eur_per_year": 10.0,
                    "delta_eue_mwh": 0.5,
                    "engine": "copt",
                    "fidelity": "analytic_convolution",
                }
            ],
            "classes_included": ["A"],
            "note": "Link-primary residual risk",
        },
    )
    R.store_eh_report(
        _state,
        ReferenceDesignReport(
            archetype="strong_grid",
            pack_hash="p",
            assumptions_hash="a",
            sections=sections,
            ens_cap_permyriad=10.0,
            mc_lole_h=mc_lole_h,
        ),
    )


def test_a_report_exports_until_its_study_changes(project, monkeypatch):
    report_id = _generated(monkeypatch)
    assert T.export_report_docx(report_id)["kind"] == "agent_export"
    # A network edit alone is not a change of the study's evidence.
    gas = float(PyPSAService.get_network().generators.at["gas", "p_nom"])
    T.update_component("Generator", "gas", {"p_nom": gas + 1})
    assert (
        T.export_report_docx(report_id, filename="after-edit")["kind"] == "agent_export"
    )

    _study_rerun(4.50)
    before = state(project)

    refused(
        lambda: T.export_report_docx(report_id, filename="stale"), 409, "report_stale"
    )
    refused(lambda: T.export_report_docx(), 409, "report_stale")
    assert state(project) == before


def test_a_report_generated_after_the_change_exports_the_new_figure(
    project, client, monkeypatch
):
    old = _generated(monkeypatch)
    _study_rerun(4.50)
    refused(lambda: T.export_report_docx(old), 409, "report_stale")

    _install_provider(monkeypatch, FakeProvider(_script()))
    T.generate_report(sections=SECTIONS)
    assert _poll()["status"] == "done"
    new = next(r["report_id"] for r in T.list_reports() if r["report_id"] != old)

    assert T.export_report_docx(new)["kind"] == "agent_export"
    headline = T.get_report_table(new, "headline")["items"]
    assert ["MC LOLE (h/yr)", "4.50 h/yr"] in headline, headline
    refused(lambda: T.export_report_docx(old), 409, "report_stale")


def _reason(call) -> str:
    """The ``reason`` of a 409 report_stale refusal."""
    with pytest.raises(HTTPException) as failure:
        call()
    detail = failure.value.detail
    assert (
        failure.value.status_code == 409 and detail["error_kind"] == "report_stale"
    ), detail
    assert detail["hint"] == "generate the report again, then export", detail
    return detail["reason"]


def test_a_report_of_a_project_that_is_not_open_is_not_compared(
    project, client, api_project, monkeypatch
):
    """GK B F1 on #1514: the evidence is the session's, so another open project
    is no measure of this one's study."""
    report_id = _generated(monkeypatch)
    before = state(project)
    api_project("other-open")  # the session now holds another project
    exported = client.post(
        f"/api/projects/{PROJECT}/reports/{report_id}/export", json={}
    )
    assert exported.status_code == 409, exported.text[:300]
    detail = exported.json()["detail"]
    assert (detail["error_kind"], detail["reason"]) == (
        "report_stale",
        "project_not_open",
    )
    assert state(project) == before


def test_an_unreadable_evidence_read_refuses_with_its_reason(
    project, monkeypatch, caplog
):
    """GK B F2: a raising read is a stated refusal, never a 500 or an unrelated 404.
    GK B F4: the failure is logged as a warning with its traceback."""
    report_id = _generated(monkeypatch)
    before = state(project)

    def broken(_project):
        raise HTTPException(404, {"error_kind": "unrelated", "message": "gone"})

    monkeypatch.setattr(reports_router, "_current_evidence", broken)
    assert _reason(lambda: T.export_report_docx(report_id)) == "evidence_unreadable"
    monkeypatch.setattr(
        reports_router,
        "_current_evidence",
        lambda _p: (_ for _ in ()).throw(RuntimeError("disk")),
    )
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=reports_router.logger.name):
        assert _reason(lambda: T.export_report_docx(report_id)) == "evidence_unreadable"
    logged = [r for r in caplog.records if "stale check" in r.getMessage()]
    assert logged and logged[-1].levelno == logging.WARNING
    assert logged[-1].exc_info and logged[-1].exc_info[0] is RuntimeError
    assert state(project) == before


def test_a_version_without_an_evidence_hash_is_refused(project, monkeypatch):
    """GK B F3: no stored hash cannot be compared, so it is not exported."""
    report_id = _generated(monkeypatch)
    doc = store.load_report(project, report_id)
    store.save_version(project, doc.model_copy(update={"evidence_hash": ""}))
    before = state(project)
    assert _reason(lambda: T.export_report_docx(report_id)) == "no_evidence_hash"
    assert state(project) == before
