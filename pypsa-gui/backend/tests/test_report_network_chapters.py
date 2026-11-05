"""Study harness issue 07 / LC-21 option A: the network-study chapters.

A solved network-only project gets chapters from the same native reads the
Results tabs keep (`retained_results`, `project_summary`), so
`POST /reports/generate` no longer answers `no_evidence` for it. Each chapter
quotes at most `MAX_FACTS` numbers (AI-558).
"""
from __future__ import annotations

import pathlib
import tempfile

import pytest

from services.reports import network_chapters as NC
from services.reports.evidence import collect_evidence, flatten_numbers


def _sources(current=True):
    stats = [{"level_0": "Generator", "level_1": f"c{i}", "Optimal Capacity": 10.0 + i,
              "Installed Capacity": 1.0 + i} for i in range(9)]
    return {"current": current,
            "summary": {"counts": {"Bus": 2, "Generator": 9}, "time": {"snapshots": 4}, "capacity": {},
                        "solver_mode": "lopf"},
            "reads": {"results_summary": {"statistics": stats,
                                          "cost_breakdown": {"total": 120.0, "capex": 20.0, "opex": 100.0,
                                                             "fom": 0.0, "by_component": []}},
                      "results_emissions": {"emissions": {"total_tCO2": 3.5, "by_carrier": []}},
                      "results_dispatch": None, "results_prices": None,
                      "loadflow": {"status": "ok", "source": "lopf", "counts": {"ranked": 1},
                                   "top": [{"name": "L1", "max_loading_pct": 80.0}]}}}


def test_every_chapter_carries_at_most_twelve_numbers():
    ev = collect_evidence(study_report=None, eh_report=None, worksheet=None, network=_sources())
    for sid in NC.SECTIONS:
        section = ev.sections[sid]
        assert section.kind == "network"
        if section.payload:
            assert NC._count(section.payload) <= NC.MAX_FACTS, sid
    capacity = ev.sections[NC.CAPACITY].payload
    assert len(capacity["rows"]) == 6 and "omitted" in capacity  # 9 rows x 2 numbers: 6 fit
    assert ev.sections[NC.HEADLINE].payload["cost_eur"]["total"] == 120.0
    # every number the prose may quote is a fact the audit knows
    assert any(f.value == 120.0 for f in flatten_numbers(ev))


def test_a_missing_read_is_not_established_with_its_reason():
    ev = collect_evidence(study_report=None, eh_report=None, worksheet=None, network=_sources())
    assert ev.sections[NC.DISPATCH].status == "not_established"
    assert ev.sections[NC.DISPATCH].note == NC.NO_READ[NC.DISPATCH]
    assert NC.LOADFLOW_PROXY in ev.required_disclosures  # an LOPF load flow is a proxy


def test_stale_results_are_never_reported_as_current():
    ev = collect_evidence(study_report=None, eh_report=None, worksheet=None, network=_sources(current=False))
    assert ev.sections[NC.MODEL_SUMMARY].status == "ok"
    for sid in NC.SECTIONS[1:]:
        assert ev.sections[sid].status == "not_established" and ev.sections[sid].note == NC.NOT_CURRENT


def test_no_network_source_adds_no_chapter():
    ev = collect_evidence(study_report=None, eh_report=None, worksheet=None)
    assert not any(s.kind == "network" for s in ev.sections.values())


def test_limit_facts_keeps_whole_rows_and_words():
    out = NC.limit_facts({"a": 1, "rows": [{"x": 1, "y": 2}] * 10, "basis": "words"}, cap=5)
    assert out["a"] == 1 and len(out["rows"]) == 2 and out["basis"] == "words"
    assert NC._count(out) == 5 and "omitted" in out


@pytest.fixture
def _no_study(monkeypatch):
    import routers.report_jobs as jobs
    monkeypatch.setattr(jobs, "_current_study_report", lambda: None)


def test_a_solved_network_only_project_is_reportable(client, api_project, session_ctx, monkeypatch, _no_study):
    from services import workflow_tools
    from services.llm_fake import FakeProvider
    from tests.test_report_job_http import _install_provider, _poll

    name = api_project("gen-network")
    _install_provider(monkeypatch, FakeProvider([]))
    ctx = session_ctx(client)
    assert client.post(f"/api/projects/{name}/reports/generate", json={}).json()["detail"]["error_kind"] \
        == "no_evidence"  # never solved: still nothing to report on
    n = ctx.network
    assert n.optimize(solver_name="highs")[1] == "optimal"
    ctx.solver_state.update(status="completed", condition="optimal")
    workflow_tools._record_solve_inputs(n, ctx.solver_state["solver_config"])
    r = client.post(f"/api/projects/{name}/reports/generate", json={})
    assert r.status_code == 200, r.text[:300]
    assert r.json()["status"] == "running"
    _poll(client, name)  # the fake provider has no prose to give: the job ends; the route took the evidence
    # The evidence-only report (no model) carries the chapters in the document.
    made = client.post(f"/api/projects/{name}/reports", json={})
    assert made.status_code == 200, made.text[:300]
    doc = client.get(f"/api/projects/{name}/reports/{made.json()['report_id']}").json()
    by_id = {s["section_id"]: s for s in doc["sections"]}
    assert by_id[NC.HEADLINE]["status"] == "ok" and by_id[NC.CAPACITY]["category"] == "investment"
    assert by_id[NC.DISPATCH]["status"] == "ok"


def test_a_project_edited_since_its_solve_is_refused_as_no_evidence(client, api_project, session_ctx,
                                                                    monkeypatch, _no_study):
    """GK B C10: solved, then edited. The old results are not current, and the
    model summary alone is not evidence, so the route answers the existing
    `no_evidence` (no new kind) naming the remedy; it never reports the old
    numbers as current. Solving again makes it reportable."""
    from services import workflow_tools
    from services.llm_fake import FakeProvider
    from tests.test_report_job_http import _install_provider, _poll

    name = api_project("edited-network")
    _install_provider(monkeypatch, FakeProvider([]))
    ctx = session_ctx(client)
    n = ctx.network

    def solve():
        assert n.optimize(solver_name="highs")[1] == "optimal"
        ctx.solver_state.update(status="completed", condition="optimal")
        workflow_tools._record_solve_inputs(n, ctx.solver_state["solver_config"])
    solve()
    gen = n.generators.index[0]
    n.generators.at[gen, "marginal_cost"] = float(n.generators.at[gen, "marginal_cost"]) + 7.0
    assert workflow_tools.solve_freshness().get("results_current") is not True
    r = client.post(f"/api/projects/{name}/reports/generate", json={})
    assert r.status_code == 400 and r.json()["detail"]["error_kind"] == "no_evidence"
    assert "Solve the network" in r.json()["detail"]["message"]
    solve()
    assert client.post(f"/api/projects/{name}/reports/generate", json={}).status_code == 200
    _poll(client, name)  # the job ends before the test does

# ── strict narrative (a run's report): admitted facts only ─────────────────


def _strict_job(tmp_path, provider, strict=True):
    from tests.test_report_job import _PROFILE
    from services.reports.report_job import prepare_report_job

    ev = collect_evidence(study_report=None, eh_report=None, worksheet=None, network=_sources())
    return prepare_report_job(project_name="demo", project_dir=tmp_path, evidence=ev, profile=_PROFILE,
                              provider=provider, language="en", sections=[NC.HEADLINE], title="Run report",
                              figure_pngs={}, strict=strict)


def test_an_injected_extra_claim_is_withheld_and_every_kept_number_is_audited(tmp_path):
    from services.llm_fake import FakeProvider
    from services.reports.report_job import run_report_job
    from tests.test_report_job import _draft

    provider = FakeProvider([_draft(NC.HEADLINE, "The study's total cost is 120 EUR.",
                                    "Emissions reach 3.5 tCO2 over the horizon.",
                                    "The site will save 4,700 EUR a year.",  # injected: no such fact
                                    bullets=["Capex is 20 EUR.", "Payback in 7 years."])])
    doc = run_report_job(_strict_job(tmp_path, provider))
    section = {s.section_id: s for s in doc.sections}[NC.HEADLINE]
    text = [b.md for b in section.blocks if b.type == "paragraph"]
    bullets = [i for b in section.blocks if b.type == "bullets" for i in b.items]
    assert text == ["The study's total cost is 120 EUR.", "Emissions reach 3.5 tCO2 over the horizon."]
    assert bullets == ["Capex is 20 EUR."]
    assert "2 statement(s) withheld" in section.note
    assert {"4,700 EUR", "7"} <= set(section.audit.unverified)
    assert all(v.path.startswith(f"/sections/{NC.HEADLINE}/") for v in section.audit.verified)


def test_a_number_from_another_section_is_not_admitted(tmp_path):
    from services.llm_fake import FakeProvider
    from services.reports.report_job import run_report_job
    from tests.test_report_job import _draft

    # 15.0 is a capacity row's number: true in the evidence, not this section's fact.
    provider = FakeProvider([_draft(NC.HEADLINE, "The largest unit is 15 MW.")])
    doc = run_report_job(_strict_job(tmp_path, provider))
    section = {s.section_id: s for s in doc.sections}[NC.HEADLINE]
    assert not [b for b in section.blocks if b.type == "paragraph"]


def test_the_reports_panel_path_keeps_flag_only(tmp_path):
    from services.llm_fake import FakeProvider
    from services.reports.report_job import run_report_job
    from tests.test_report_job import _draft

    provider = FakeProvider([_draft(NC.HEADLINE, "The site will save 4,700 EUR a year.")])
    doc = run_report_job(_strict_job(tmp_path, provider, strict=False))
    section = {s.section_id: s for s in doc.sections}[NC.HEADLINE]
    assert [b.md for b in section.blocks if b.type == "paragraph"] == ["The site will save 4,700 EUR a year."]
    assert section.audit.unverified == ["4,700 EUR"]


def test_a_statement_the_audit_cannot_read_is_withheld(tmp_path, monkeypatch):
    """C5: fail closed. A mutant that keeps the statement when the audit
    raises goes red here."""
    from services.llm_fake import FakeProvider
    from services.reports import report_job
    from tests.test_report_job import _draft

    def broken(paragraphs, facts):
        raise ValueError("cannot parse")

    monkeypatch.setattr(report_job, "audit", broken)
    provider = FakeProvider([_draft(NC.HEADLINE, "The study's total cost is 120 EUR.")])
    doc = report_job.run_report_job(_strict_job(tmp_path, provider))
    section = {s.section_id: s for s in doc.sections}[NC.HEADLINE]
    assert not [b for b in section.blocks if b.type in ("paragraph", "bullets")]
    assert "withheld" in (section.note or "")


# C5: the Reports panel's path is byte-identical to master's (fixture written
# on master 3f39808c9 by this same function).

def human_doc():
    from services.llm_fake import FakeProvider
    from services.reports.evidence import collect_evidence
    from services.reports.report_job import prepare_report_job, run_report_job
    from tests.test_report_job import _PROFILE, _draft, _eh_report

    ev = collect_evidence(study_report=None, eh_report=_eh_report(), worksheet=None)
    provider = FakeProvider([_draft("executive_summary", "The plan meets 3.2 h/yr.", "It saves 4,700 EUR."),
                             _draft("fmea_top", "Generator g dominates.", bullets=["Rank 99 mode."])])
    with tempfile.TemporaryDirectory() as d:
        job = prepare_report_job(project_name="demo", project_dir=pathlib.Path(d), evidence=ev, profile=_PROFILE,
                                 provider=provider, language="en", sections=["executive_summary", "fmea_top"],
                                 title="Client report", figure_pngs={})
        doc = run_report_job(job)
    return [{"id": s.section_id, "blocks": [b.model_dump(mode="json") for b in s.blocks],
             "audit": s.audit.model_dump(mode="json"), "note": s.note, "source": s.source} for s in doc.sections
            if s.section_id in ("executive_summary", "fmea_top")]



def test_the_reports_panel_document_equals_master():
    import json
    from pathlib import Path

    expected = json.loads((Path(__file__).parent / "fixtures" / "sh07_report_human_master.json").read_text())
    assert json.loads(json.dumps(human_doc(), sort_keys=True)) == expected


# GK B C9: a reader that raises (an error, a 404 or a 409) leaves its chapter
# not established, naming the failed read and what to do; every other chapter
# keeps its own read.
_RAISES = {"error": RuntimeError("boom"),
           "404": __import__("fastapi").HTTPException(404, {"error_kind": "project_not_found"}),
           "409": __import__("fastapi").HTTPException(409, {"error_kind": "project_busy"})}
_WHY = {"error": "RuntimeError", "404": "404 project_not_found", "409": "409 project_busy"}


def _read_with(monkeypatch, failing: str | None, how: str):
    from types import SimpleNamespace

    from services import project_summary, retained_results, workflow_tools
    from services.pypsa_service import PyPSAService
    good = _sources()
    ctx = SimpleNamespace(network=SimpleNamespace(meta={"assistant_solve_input_fingerprint": "f"}),
                          solver_state={}, project_uuid="p")
    monkeypatch.setattr(PyPSAService, "get_active_context", staticmethod(lambda: ctx))
    monkeypatch.setattr(workflow_tools, "solve_freshness", lambda: {"results_current": True})

    def summarise(*a, **k):
        if failing == NC.MODEL_SUMMARY:
            raise _RAISES[how]
        return good["summary"]
    monkeypatch.setattr(project_summary, "summarise", summarise)

    def adapter(section):
        def capture(token):
            if section == failing:
                raise _RAISES[how]
            return good["reads"].get(section)
        return SimpleNamespace(section=section, capture=capture)
    monkeypatch.setattr(retained_results, "ADAPTERS",
                        tuple(adapter(s) for s in ("loadflow", "results_summary", "results_dispatch",
                                                   "results_prices", "results_emissions")))
    return NC.read_sources(project_root="root")


@pytest.mark.parametrize("how", sorted(_RAISES))
@pytest.mark.parametrize("chapter, read", [(NC.MODEL_SUMMARY, NC.MODEL_SUMMARY), (NC.HEADLINE, "results_summary"),
                                           (NC.CAPACITY, "results_summary"), (NC.EMISSIONS, "results_emissions"),
                                           (NC.LOAD_FLOW, "loadflow"), (NC.DISPATCH, "results_dispatch"),
                                           (NC.PRICES, "results_prices")])
def test_an_unreadable_reader_leaves_its_chapter_not_established_with_a_hint(monkeypatch, chapter, read, how):
    sources = _read_with(monkeypatch, read, how)
    assert sources["failed"] == {read: _WHY[how]}
    ev = collect_evidence(study_report=None, eh_report=None, worksheet=None, network=sources)
    section = ev.sections[chapter]
    if chapter == NC.HEADLINE:  # its emissions read still stands: a partial chapter names the failed read
        assert section.status == "ok" and _WHY[how] in section.payload["not_read"]
        assert "generate the report again" in section.payload["not_read"]
        assert "cost_eur" not in section.payload or not section.payload["cost_eur"]
    else:
        assert section.status == "not_established" and not section.payload
        assert section.note == NC.NO_READ[chapter] + NC.READ_FAILED.format(why=_WHY[how])
        assert "generate the report again" in section.note
    untouched = _read_with(monkeypatch, None, how)
    assert untouched["failed"] == {} and ev.sections[NC.MODEL_SUMMARY if chapter != NC.MODEL_SUMMARY
                                                  else NC.CAPACITY].status == "ok"
