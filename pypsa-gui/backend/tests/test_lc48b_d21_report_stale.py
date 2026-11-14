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
from models.energy_hub import ReferenceDesignReport, SectionState, empty_section_map
from services import chat_tools as T
from services.adequacy import eh_report as R
from services.llm_fake import FakeProvider
from services.pypsa_service import PyPSAService
from tests.lc43_gates import refused, state
from tests.test_chat_report_tools import _install_provider, _poll, _script, project  # noqa: F401
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
