"""
WP3 — `services/reports/report_job.py`: the generation job over the fake
provider, run synchronously through the worker function and once on a real
thread.

Starts from the evidence-only document (WP5), adds prose per section in
front of the code blocks, audits every number, records the profile that
wrote it, keeps a failing section's code blocks with the failure in its note,
honours the stop event between sections, and saves a new version on
regenerate with every other section byte-identical.
"""
from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import pytest

from models.energy_hub import ReferenceDesignReport, SectionState, empty_section_map
from services.llm_fake import FakeProvider
from services.llm_provider import LLMEvent
from services.reports import store
from services.reports.assemble import EXECUTIVE_SUMMARY_ID
from services.reports.evidence import collect_evidence, evidence_hash
from services.reports.report_job import (
    ReportJobInFlight,
    prepare_report_job,
    run_report_job,
    start_report_job,
)

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
_PROFILE = SimpleNamespace(id="fake-profile", model="fake-model", max_output_tokens=None)


def _eh_report() -> dict:
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
    sections["frontier"] = SectionState(status="ok", payload={
        "points": [{"target_permyriad": 10.0, "status": "optimal",
                    "point": {"total_system_cost_eur": 1_234_567.89,
                              "achieved_ens_mwh": 1.0, "achieved_shed_hours": 2.0}}],
        "knee_index": 0, "period_basis": "annual", "voll_eur_per_mwh": 150.0,
    })
    report = ReferenceDesignReport(
        archetype="strong_grid", pack_hash="p", assumptions_hash="a",
        sections=sections, ens_cap_permyriad=10.0, mc_lole_h=3.21,
        cost_at_target_eur=1_234_567.89, period_basis="single_period",
    )
    return report.model_dump(mode="json")


@pytest.fixture
def evidence():
    return collect_evidence(study_report=None, eh_report=_eh_report(), worksheet=None)


def _turn(payload: dict | str) -> dict:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return {"events": [LLMEvent(type="text_delta", text=text)],
            "blocks": [{"type": "text", "text": text}]}


def _draft(section_id: str, *paragraphs: str, bullets: list[str] | None = None) -> dict:
    return _turn({"section_id": section_id, "paragraphs": list(paragraphs),
                  "bullets": bullets or []})


def _job(tmp_path, evidence, provider, **kw):
    kw.setdefault("figure_pngs", {"fmea_pareto": _PNG})
    kw.setdefault("title", "Client report")
    return prepare_report_job(
        project_name="demo", project_dir=tmp_path, evidence=evidence,
        profile=_PROFILE, provider=provider, language="en", **kw)


def _by_id(doc):
    return {s.section_id: s for s in doc.sections}


# ── the happy path ──────────────────────────────────────────────────────────

def test_every_target_section_gets_prose_before_its_code_blocks(tmp_path, evidence):
    provider = FakeProvider([
        _draft(EXECUTIVE_SUMMARY_ID, "The plan meets 3.2 h/yr at 1,234,568 €.",
               bullets=["MC LOLE 3.21 h/yr"]),
        _draft("frontier", "The frontier has one point at 10 ‱."),   # document order
        _draft("fmea_top", "One residual mode dominates: generator g, rank 1."),
    ])
    job = _job(tmp_path, evidence, provider)
    doc = run_report_job(job)

    assert job.record["status"] == "done"
    assert job.record["report_id"] == doc.report_id and job.record["version"] == 1
    assert job.record["progress"] == {"done": 3, "total": 3, "current": None}
    assert doc.mode == "generated" and doc.version == 1
    assert doc.profile_id == "fake-profile" and doc.model == "fake-model"
    assert doc.evidence_hash == evidence_hash(evidence)
    assert doc.language == "en" and doc.title == "Client report"

    by_id = _by_id(doc)
    for sid in (EXECUTIVE_SUMMARY_ID, "fmea_top", "frontier"):
        s = by_id[sid]
        assert s.source == "llm" and s.status == "ok", sid
        types = [b.type for b in s.blocks]
        first_code = next(i for i, t in enumerate(types) if t not in ("paragraph", "bullets"))
        assert types[0] == "paragraph"
        assert all(t in ("paragraph", "bullets") for t in types[:first_code]), types
        assert all(t not in ("paragraph", "bullets") for t in types[first_code:]), types
    # Prose first, then the code blocks the evidence-only section already had.
    fmea = by_id["fmea_top"]
    assert [b.type for b in fmea.blocks][1:] == ["table_ref", "figure_ref", "callout"]
    assert fmea.blocks[0].md == "One residual mode dominates: generator g, rank 1."
    summary = by_id[EXECUTIVE_SUMMARY_ID]
    assert summary.blocks[1].type == "bullets" and summary.blocks[1].items == ["MC LOLE 3.21 h/yr"]
    # The audit ran on the prose: the rounded LOLE and the cost verify.
    assert {v.path for v in summary.audit.verified} >= {
        "/headline/mc_lole_h", "/headline/cost_at_target_eur"}
    assert summary.audit.unverified == []
    # Sections that were not targets stay code.
    assert by_id["pipeline"].source == "code"
    assert by_id["certification"].source == "code" and by_id["certification"].status == "skipped"

    # Saved: v1 + meta + the figure file, readable back.
    stored = store.load_report(tmp_path, doc.report_id)
    assert stored == doc
    meta_raw = json.loads((tmp_path / "reports" / doc.report_id / "meta.json").read_text())
    assert meta_raw["mode"] == "generated" and meta_raw["profile_id"] == "fake-profile"
    assert meta_raw["generation"]["repairs"] == 0
    assert meta_raw["generation"]["prose_failures"] == []
    assert (tmp_path / "reports" / doc.report_id / "figures" / "fmea_pareto.png").is_file()


def test_the_fmea_top_slice_sent_to_the_provider_has_no_frontier_key(tmp_path, evidence):
    provider = FakeProvider([
        _draft(EXECUTIVE_SUMMARY_ID, "Summary."),
        _draft("frontier", "Frontier."),
        _draft("fmea_top", "Modes."),
    ])
    run_report_job(_job(tmp_path, evidence, provider))
    assert len(provider.requests) == 3
    fmea_req = provider.requests[2]
    body = fmea_req.messages[0]["content"]
    text = body if isinstance(body, str) else body[0]["text"]
    assert '"section_id":"fmea_top"' in text.replace(" ", "")
    assert '"points"' not in text and '"knee_index"' not in text
    assert "gen:g:forced_outage" in text
    # And the frontier request carries no FMEA rows.
    body = provider.requests[1].messages[0]["content"]
    text = body if isinstance(body, str) else body[0]["text"]
    assert '"knee_index"' in text and "gen:g:forced_outage" not in text


# ── failures, disclosures, repairs ──────────────────────────────────────────

def test_a_failing_section_keeps_its_code_blocks_and_records_the_failure(tmp_path, evidence):
    provider = FakeProvider([
        _draft(EXECUTIVE_SUMMARY_ID, "Summary."),
        _turn("prose first"), _draft("frontier", "Frontier."),   # frontier: one repair
        _turn("not json"), _turn("still not json"),   # fmea_top: two failures
    ])
    job = _job(tmp_path, evidence, provider)
    doc = run_report_job(job)
    assert job.record["status"] == "done"
    by_id = _by_id(doc)
    fmea = by_id["fmea_top"]
    assert fmea.source == "code" and fmea.status == "ok"
    assert [b.type for b in fmea.blocks] == ["table_ref", "figure_ref", "callout"]
    assert fmea.note and "prose not established" in fmea.note
    assert "fake-profile" in fmea.note and "fake-model" in fmea.note
    assert by_id["frontier"].source == "llm"
    meta_raw = json.loads((tmp_path / "reports" / doc.report_id / "meta.json").read_text())
    assert meta_raw["generation"]["repairs"] == 2
    assert [f["section_id"] for f in meta_raw["generation"]["prose_failures"]] == ["fmea_top"]
    assert job.record["repairs"] == 2
    assert job.record["prose_failures"][0]["section_id"] == "fmea_top"


def test_required_disclosures_stay_as_callouts_when_the_prose_omits_them(tmp_path, evidence):
    assert evidence.required_disclosures, "the fixture carries disclosures"
    provider = FakeProvider([
        _draft(EXECUTIVE_SUMMARY_ID, "A summary that repeats nothing."),
        _draft("frontier", "Frontier."),
        _draft("fmea_top", "Modes."),
    ])
    doc = run_report_job(_job(tmp_path, evidence, provider))
    summary = _by_id(doc)[EXECUTIVE_SUMMARY_ID]
    callouts = [b.text for b in summary.blocks if b.type == "callout" and b.kind == "disclosure"]
    for line in evidence.required_disclosures:
        assert line in callouts, line
    fmea = _by_id(doc)["fmea_top"]
    assert any(b.type == "callout" and "Link-primary residual risk" in b.text
               for b in fmea.blocks)


def test_a_provider_error_is_recorded_per_section_not_fatal(tmp_path, evidence):
    from services.llm_provider import ProviderError
    provider = FakeProvider([
        ProviderError("rate_limited", "slow down"),
        _draft("frontier", "Frontier."),
        _draft("fmea_top", "Modes."),
    ])
    job = _job(tmp_path, evidence, provider)
    doc = run_report_job(job)
    assert job.record["status"] == "done"
    summary = _by_id(doc)[EXECUTIVE_SUMMARY_ID]
    assert summary.source == "code"
    assert "rate_limited" in (summary.note or "")


# ── abort ───────────────────────────────────────────────────────────────────

def test_abort_between_sections_leaves_later_sections_code_only(tmp_path, evidence):
    stop = threading.Event()

    class StopAfterFirst(FakeProvider):
        def stream(self, request):
            yield from super().stream(request)
            stop.set()

    provider = StopAfterFirst([_draft(EXECUTIVE_SUMMARY_ID, "Summary.")])
    job = _job(tmp_path, evidence, provider)
    job.stop_event = stop
    doc = run_report_job(job)
    assert job.record["status"] == "aborted"
    assert job.record["progress"]["done"] == 1
    by_id = _by_id(doc)
    assert by_id[EXECUTIVE_SUMMARY_ID].source == "llm"
    assert by_id["fmea_top"].source == "code" and by_id["frontier"].source == "code"
    assert "aborted" in (by_id["fmea_top"].note or "")
    # The partial document is saved all the same.
    assert store.load_report(tmp_path, doc.report_id).version == 1


# ── regenerate ──────────────────────────────────────────────────────────────

def test_regenerate_one_section_bumps_the_version_and_leaves_the_rest_identical(
        tmp_path, evidence):
    provider = FakeProvider([
        _draft(EXECUTIVE_SUMMARY_ID, "Summary."),
        _draft("frontier", "Frontier."),
        _draft("fmea_top", "Modes."),
    ])
    v1 = run_report_job(_job(tmp_path, evidence, provider))

    provider2 = FakeProvider([_draft("fmea_top", "Modes, rewritten shorter.")])
    job = _job(tmp_path, evidence, provider2, base_document=v1,
               regenerate_section="fmea_top", instruction="shorter")
    v2 = run_report_job(job)
    assert job.record["status"] == "done" and job.record["version"] == 2
    assert v2.version == 2 and v2.report_id == v1.report_id
    assert len(provider2.requests) == 1
    body = provider2.requests[0].messages[0]["content"]
    text = body if isinstance(body, str) else body[0]["text"]
    assert "shorter" in text
    assert _by_id(v2)["fmea_top"].blocks[0].md == "Modes, rewritten shorter."
    for s1, s2 in zip(v1.sections, v2.sections):
        if s1.section_id == "fmea_top":
            continue
        assert s1.model_dump_json() == s2.model_dump_json(), s1.section_id
    assert v2.tables == v1.tables and v2.figures == v1.figures
    assert store.load_meta(tmp_path, v1.report_id).latest_version == 2
    assert store.load_report(tmp_path, v1.report_id, 1) == v1


def test_an_unknown_section_is_refused_before_anything_runs(tmp_path, evidence):
    with pytest.raises(KeyError):
        _job(tmp_path, evidence, FakeProvider([]), sections=["nope"])


# ── the thread ──────────────────────────────────────────────────────────────

def test_start_report_job_runs_on_a_daemon_thread_and_refuses_a_second(tmp_path, evidence):
    hold = threading.Event()

    class Holding(FakeProvider):
        def stream(self, request):
            hold.wait(timeout=10)
            yield from super().stream(request)

    provider = Holding([
        _draft(EXECUTIVE_SUMMARY_ID, "Summary."),
        _draft("frontier", "Frontier."),
        _draft("fmea_top", "Modes."),
    ])
    state: dict = {}
    lock = threading.Lock()
    report_id = start_report_job(
        project_name="demo", project_dir=tmp_path, evidence=evidence,
        figure_pngs={}, profile=_PROFILE, provider=provider, language="en",
        sections=None, title="T", lock=lock, state=state)
    record = state["report_job"]
    try:
        assert record["status"] == "running" and record["report_id"] == report_id
        assert record["thread"].daemon and record["thread"].name == "report-generate"
        assert record["profile_id"] == "fake-profile" and record["model"] == "fake-model"
        with pytest.raises(ReportJobInFlight):
            start_report_job(
                project_name="demo", project_dir=tmp_path, evidence=evidence,
                figure_pngs={}, profile=_PROFILE, provider=FakeProvider([]),
                language="en", sections=None, title="T", lock=lock, state=state)
    finally:
        hold.set()
    record["thread"].join(timeout=10)
    assert record["status"] == "done", record.get("error")
    assert record["version"] == 1
    assert store.load_report(tmp_path, report_id).mode == "generated"
    # A finished job frees the slot.
    provider2 = FakeProvider([_draft(EXECUTIVE_SUMMARY_ID, "S."), _draft("frontier", "F."),
                              _draft("fmea_top", "M.")])
    rid2 = start_report_job(
        project_name="demo", project_dir=tmp_path, evidence=evidence,
        figure_pngs={}, profile=_PROFILE, provider=provider2, language="en",
        sections=None, title="T", lock=lock, state=state)
    deadline = time.time() + 10
    while state["report_job"]["status"] == "running" and time.time() < deadline:
        time.sleep(0.01)
    assert state["report_job"]["status"] == "done" and rid2 != report_id
