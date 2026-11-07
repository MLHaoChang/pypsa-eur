"""AA-05 Y1–Y7: a declared summary admits its listed sections' facts.

GK B (33) conditions (`.scratch/assistant-assurance/issues/
05-verified-artifacts.md`):

* Y1: a section declared a summary admits the evidence facts of the
  sections it lists, one level, never their prose; an unknown id refuses
  `summary_section_unknown` at declaration.
* Y2: the widening lives in `report_job.admitted_facts`, so the assistant's
  strict generate and stage 7 admit the same set.
* Y5: the strict summary's note and the sealed manifest name the listed
  sections. Y6: a declaration changes the stored version's hash.
* Y7: a human (non-strict) job's output is the same with and without one.
"""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from services import chat_tools as T
from services.llm_fake import FakeProvider
from services.reports import report_job, store
from services.reports.assemble import EXECUTIVE_SUMMARY_ID
from tests import test_chat_report_tools as _tools, test_workflow_tools as _workflow
from tests.test_chat_report_tools import (
    PROJECT,
    _draft,
    _install_provider,
    _poll,
    _store_eh_report,
)

project = _tools.project
helpers_state = _workflow.helpers_state

SECTIONS = [EXECUTIVE_SUMMARY_ID, "fmea_top"]
# 0.5 MWh is fmea_top's delta_eue_mwh: in its slice, not in the summary's.
LISTED_FACT = "The top failure mode adds 0.5 MWh of unserved energy."
# 4,700 EUR is no fact anywhere: prose copied from fmea_top stays withheld.
COPIED_PROSE = "Generator g saves 4,700 EUR a year."
DECLARED = [{"section_id": EXECUTIVE_SUMMARY_ID, "sections": ["fmea_top"]}]


def _script(summary: str) -> list[dict]:
    return [
        _draft(EXECUTIVE_SUMMARY_ID, summary),
        _draft("fmea_top", "Generator g dominates.", LISTED_FACT, COPIED_PROSE),
    ]


def _assistant(monkeypatch, summary: str, summaries=None) -> str:
    _store_eh_report()
    _install_provider(monkeypatch, FakeProvider(_script(summary)))
    rid = T.generate_report(sections=SECTIONS, summaries=summaries)["report_id"]
    assert _poll()["status"] == "done"
    return rid


def _section(rid: str, sid: str) -> dict:
    return T.get_report(rid, section_id=sid)["sections"][0]


def _paragraphs(section: dict) -> list[str]:
    return [b["md"] for b in section["blocks"] if b["type"] == "paragraph"]


def _sidecar(project_dir, meta: dict) -> dict:
    return json.loads(
        (project_dir / "assistant_artifacts" / f"{meta['file_id']}.json").read_text()
    )


# ── Y3: what a declared summary admits ───────────────────────────────────────


def test_a_summary_quoting_a_listed_sections_fact_is_verified(project, monkeypatch):
    rid = _assistant(monkeypatch, LISTED_FACT, DECLARED)
    summary = _section(rid, EXECUTIVE_SUMMARY_ID)
    assert _paragraphs(summary) == [LISTED_FACT]
    assert "withheld" not in summary.get("audit", {})
    # Y5: the summary never draws on another section silently.
    fmea = _section(rid, "fmea_top")
    assert summary["note"].endswith(
        f"This summary may quote numbers from: {fmea['heading']}."
    )


def test_the_same_fact_without_a_declaration_is_withheld(project, monkeypatch):
    rid = _assistant(monkeypatch, LISTED_FACT)
    summary = _section(rid, EXECUTIVE_SUMMARY_ID)
    assert _paragraphs(summary) == []
    assert summary["audit"]["unverified"] == ["0.5 MWh"]
    assert "may quote numbers from" not in (summary.get("note") or "")


def test_a_summary_listing_another_section_does_not_admit_fmea(project, monkeypatch):
    # Listing the summary itself admits no other section's facts (one level).
    rid = _assistant(
        monkeypatch,
        LISTED_FACT,
        [{"section_id": EXECUTIVE_SUMMARY_ID, "sections": [EXECUTIVE_SUMMARY_ID]}],
    )
    summary = _section(rid, EXECUTIVE_SUMMARY_ID)
    assert _paragraphs(summary) == []
    assert summary["audit"]["unverified"] == ["0.5 MWh"]


def test_a_listed_summarys_own_list_is_not_followed(project, monkeypatch):
    # executive_summary lists another section that itself lists fmea_top: one
    # level only, so fmea_top's 0.5 MWh stays withheld in the summary.
    _store_eh_report()
    _install_provider(monkeypatch, FakeProvider(_script(LISTED_FACT)))
    probe = T.generate_report(sections=SECTIONS)["report_id"]
    assert _poll()["status"] == "done"
    middle = next(
        s["section_id"]
        for s in T.get_report(probe)["sections"]
        if s["section_id"] not in SECTIONS
    )
    rid = _assistant(
        monkeypatch,
        LISTED_FACT,
        [
            {"section_id": EXECUTIVE_SUMMARY_ID, "sections": [middle]},
            {"section_id": middle, "sections": ["fmea_top"]},
        ],
    )
    summary = _section(rid, EXECUTIVE_SUMMARY_ID)
    assert _paragraphs(summary) == []
    assert summary["audit"]["unverified"] == ["0.5 MWh"]


def test_prose_copied_from_a_listed_section_is_still_withheld(project, monkeypatch):
    rid = _assistant(monkeypatch, COPIED_PROSE, DECLARED)
    summary = _section(rid, EXECUTIVE_SUMMARY_ID)
    assert _paragraphs(summary) == []
    assert summary["audit"]["unverified"] == ["4,700 EUR"]
    assert "4,700" not in json.dumps(summary["blocks"])


def test_a_number_a_listed_sections_prose_already_shows_is_still_withheld(
    project, client, monkeypatch
):
    # A human (non-strict) report keeps fmea_top's unchecked 4,700 EUR in its
    # prose; a strict regenerate of the declared summary copying it withholds it.
    _store_eh_report()
    _install_provider(monkeypatch, FakeProvider(_script("The plan meets 3.2 h/yr.")))
    started = client.post(
        f"/api/projects/{PROJECT}/reports/generate",
        json={"sections": SECTIONS, "summaries": DECLARED},
    )
    assert started.status_code == 200, started.text
    assert _poll()["status"] == "done"
    rid = started.json()["report_id"]
    assert COPIED_PROSE in _paragraphs(_section(rid, "fmea_top"))
    _install_provider(
        monkeypatch, FakeProvider([_draft(EXECUTIVE_SUMMARY_ID, COPIED_PROSE)])
    )
    T.regenerate_report_section(rid, EXECUTIVE_SUMMARY_ID)
    assert _poll()["status"] == "done"
    summary = _section(rid, EXECUTIVE_SUMMARY_ID)
    assert _paragraphs(summary) == []
    assert summary["audit"]["unverified"] == ["4,700 EUR"]


def test_a_non_summary_section_gains_nothing(project, monkeypatch):
    rid = _assistant(monkeypatch, LISTED_FACT, DECLARED)
    fmea = _section(rid, "fmea_top")
    # Its own fact is kept; the copied prose is withheld as without a declaration.
    assert _paragraphs(fmea) == ["Generator g dominates.", LISTED_FACT]
    assert fmea["audit"]["unverified"] == ["4,700 EUR"]
    assert "may quote numbers from" not in (fmea.get("note") or "")


def _spy(monkeypatch) -> dict[str, list[set[str]]]:
    seen: dict[str, list[set[str]]] = {}
    real = report_job.admitted_facts

    def spy(job, section):
        out = real(job, section)
        seen.setdefault(section.section_id, []).append({f.path for f in out})
        return out

    monkeypatch.setattr(report_job, "admitted_facts", spy)
    return seen


def test_the_admitted_set_is_the_union_one_level_and_nothing_else(project, monkeypatch):
    seen = _spy(monkeypatch)
    _assistant(monkeypatch, LISTED_FACT)
    own, fmea = seen.pop(EXECUTIVE_SUMMARY_ID)[0], seen.pop("fmea_top")[0]
    _assistant(monkeypatch, LISTED_FACT, DECLARED)
    payload = {p for p in fmea if p.startswith("/sections/fmea_top/payload")}
    assert payload and seen[EXECUTIVE_SUMMARY_ID][0] == own | payload
    assert seen["fmea_top"][0] == fmea


# ── Y1: declaration ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "summaries",
    [
        [{"section_id": EXECUTIVE_SUMMARY_ID, "sections": ["no_such_section"]}],
        [{"section_id": "no_such_section", "sections": ["fmea_top"]}],
        DECLARED + DECLARED,
    ],
)
def test_an_unknown_or_repeated_declaration_refuses_before_any_job(
    project, monkeypatch, summaries
):
    _store_eh_report()
    _install_provider(monkeypatch, FakeProvider(_script(LISTED_FACT)))
    with pytest.raises(HTTPException) as exc:
        T.generate_report(sections=SECTIONS, summaries=summaries)
    assert exc.value.status_code == 400
    assert exc.value.detail["error_kind"] == "summary_section_unknown"
    assert T.list_reports() == [] or all(
        r.get("latest_version") is None for r in T.list_reports()
    )


def test_a_stored_declaration_naming_a_missing_section_refuses(project, monkeypatch):
    doc = store.load_report(project, _assistant(monkeypatch, LISTED_FACT))
    broken = doc.model_copy(
        update={
            "sections": [
                s.model_copy(update={"summarises": ["gone"]})
                if s.section_id == EXECUTIVE_SUMMARY_ID
                else s
                for s in doc.sections
            ]
        }
    )
    with pytest.raises(report_job.SummarySectionUnknown):
        report_job.declare_summaries(broken, None)


def test_a_regenerate_keeps_the_declaration(project, monkeypatch):
    rid = _assistant(monkeypatch, LISTED_FACT, DECLARED)
    _install_provider(
        monkeypatch, FakeProvider([_draft(EXECUTIVE_SUMMARY_ID, LISTED_FACT)])
    )
    T.regenerate_report_section(rid, EXECUTIVE_SUMMARY_ID)
    assert _poll()["status"] == "done"
    summary = _section(rid, EXECUTIVE_SUMMARY_ID)
    assert _paragraphs(summary) == [LISTED_FACT]
    assert "withheld" not in summary.get("audit", {})


# ── Y2: one path, both strict callers ────────────────────────────────────────


def test_stage_7_admits_the_same_set_through_the_same_path(project, monkeypatch):
    seen = _spy(monkeypatch)
    _assistant(monkeypatch, LISTED_FACT, DECLARED)
    assistant = seen.pop(EXECUTIVE_SUMMARY_ID)
    from routers import report_jobs

    _store_eh_report()
    _install_provider(monkeypatch, FakeProvider(_script(LISTED_FACT)))
    proj = T._report_project()
    llm = report_jobs._resolve_profile()
    evidence, figures = report_jobs._evidence_for(proj)
    # The kwargs harness/run_report.start passes, with a caller's declaration.
    report_jobs._start(
        proj,
        evidence=evidence,
        figure_pngs=figures,
        profile=llm,
        provider=report_jobs._provider_or_400(llm),
        language="en",
        sections=SECTIONS,
        strict=True,
        summaries={EXECUTIVE_SUMMARY_ID: ["fmea_top"]},
    )
    assert _poll()["status"] == "done"
    assert seen[EXECUTIVE_SUMMARY_ID] == assistant
    assert any(p.startswith("/sections/fmea_top/payload") for p in assistant[0])


def test_run_report_start_passes_the_callers_declaration(monkeypatch):
    import inspect

    from harness import run_report

    assert inspect.signature(run_report.start).parameters["summaries"].default is None
    source = inspect.getsource(run_report.start)
    assert "summaries=summaries" in source


# ── Y5/Y6: provenance and identity ───────────────────────────────────────────


def test_the_sealed_manifest_names_the_listed_sections(project, monkeypatch):
    rid = _assistant(monkeypatch, LISTED_FACT, DECLARED)
    record = _sidecar(project, T.export_report_docx(rid))
    assert record["manifest"]["dataset"]["summaries"] == {
        EXECUTIVE_SUMMARY_ID: ["fmea_top"]
    }


def test_an_undeclared_export_manifest_has_no_summaries_key(project, monkeypatch):
    rid = _assistant(monkeypatch, LISTED_FACT)
    assert (
        "summaries"
        not in _sidecar(project, T.export_report_docx(rid))["manifest"]["dataset"]
    )


def test_a_different_declaration_is_a_different_version_hash(
    project, monkeypatch, tmp_path
):
    doc = store.load_report(project, _assistant(monkeypatch, LISTED_FACT))
    declared = report_job.declare_summaries(doc, {EXECUTIVE_SUMMARY_ID: ["fmea_top"]})
    hashes = []
    for i, d in enumerate((doc, declared)):
        root = tmp_path / str(i)
        store.create_report(root, d)
        hashes.append(store.version_sha256(root, d.report_id, d.version))
    assert hashes[0] != hashes[1]


# ── Y7: the human route ──────────────────────────────────────────────────────


def _human(client, monkeypatch, body: dict) -> list[dict]:
    _store_eh_report()
    _install_provider(monkeypatch, FakeProvider(_script(LISTED_FACT)))
    started = client.post(
        f"/api/projects/{PROJECT}/reports/generate", json={"sections": SECTIONS, **body}
    )
    assert started.status_code == 200, started.text
    assert _poll()["status"] == "done"
    rid = started.json()["report_id"]
    sections = T.get_report(rid)["sections"]
    return [{k: v for k, v in s.items() if k != "summarises"} for s in sections]


def test_a_human_job_is_the_same_with_and_without_a_declaration(
    project, client, monkeypatch
):
    plain = _human(client, monkeypatch, {})
    declared = _human(client, monkeypatch, {"summaries": DECLARED})
    assert json.dumps(plain, sort_keys=True) == json.dumps(declared, sort_keys=True)


def test_the_human_route_refuses_an_unknown_declaration(project, client, monkeypatch):
    _store_eh_report()
    _install_provider(monkeypatch, FakeProvider(_script(LISTED_FACT)))
    response = client.post(
        f"/api/projects/{PROJECT}/reports/generate",
        json={
            "sections": SECTIONS,
            "summaries": [
                {"section_id": EXECUTIVE_SUMMARY_ID, "sections": ["no_such_section"]}
            ],
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"]["error_kind"] == "summary_section_unknown"
