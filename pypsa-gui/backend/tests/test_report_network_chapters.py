"""Study harness issue 07 / LC-21 option A: the network-study chapters.

A solved network-only project gets chapters from the same native reads the
Results tabs keep (`retained_results`, `project_summary`), so
`POST /reports/generate` no longer answers `no_evidence` for it. Each chapter
quotes at most `MAX_FACTS` numbers (AI-558).
"""
from __future__ import annotations

import pytest

from services.reports import evidence as E
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
