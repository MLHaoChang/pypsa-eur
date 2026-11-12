"""Study harness 14: eh_ipp_hybrid@1 on the shipped template, as the run's own
project (GK B (52) I2): the run solves, then `run_investment_case` waits on its
job and settles from the case's own record, spending no solve; and the
follower's, the recovery's and `finish_step`'s rules for that step, on a
record the test controls (no LP). The readers and the profile are pinned in
`test_run_profile_ipp.py`."""
from __future__ import annotations
import json, sys
from pathlib import Path
import pypsa, pytest
from fastapi.testclient import TestClient  # noqa: F401
from harness import runner as harness_runner
from services import chat_tools, flow_service, project_registry, run_profiles
from services.pypsa_service import PyPSAService
from tests.test_assistant_tasks_study_brief import helpers_state, session  # noqa: F401
from tests.test_harness_envelope import envelope_of, granted, profile  # noqa: F401
from tests.test_harness_run_loops import Script, call, go, rated, refused, say  # noqa: F401
from tests.test_harness_run_report_handoff import fake_report  # noqa: F401
from tests.test_harness_run_skeleton import RUN
from tests.test_harness_runner import detached, fake_report_provider  # noqa: F401

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_templates as T  # noqa: E402
REF = "eh_ipp_hybrid@1"
TEMPLATE = "eh_ipp_hybrid"
FIXTURES = (granted, rated)

@pytest.fixture
def baseline(client, session_ctx):
    """The shipped template created through the route as a user does (the
    Library price series materialised, the template's own solver and
    finance settings), then bound as the run's project."""
    name = "IPP hybrid"
    response = client.post(f"/api/projects/from_template/{TEMPLATE}", params={"name": name})
    assert response.status_code == 200, response.text
    # Saved once, as the run's hand-over does (`run_handover`): a template ships
    # no user_ts, and the first solve's write-back would read as an input change.
    response = client.post(f"/api/projects/{name}")
    assert response.status_code == 200, response.text
    ctx = session_ctx(client)
    binding = PyPSAService.bind_request_context(ctx)
    cell = PyPSAService.bind_turn_cell()
    try:
        with chat_tools._acting() as (db, user):
            project = project_registry.resolve_project(db, user, name)
            path = project_registry.project_dir(project)
        yield ctx, str(project.id), path
    finally:
        PyPSAService._turn_cell.reset(cell)
        PyPSAService.reset_request_context(binding)


@pytest.fixture
def site(baseline):
    _, project_id, root = baseline
    (root / flow_service.TEMPLATE_FILE).write_text(json.dumps({"id": TEMPLATE}))
    return project_id, root


# The fixtures this file's tests take by name (imported above, used here).
FIXTURES = (granted, rated)


# ── a run on the shipped template (real runner, real solve, real case) ───────


def _case_chapter(record, project_id):
    from harness import run_report

    doc = run_report.saved(record, project_id)
    sections = {sec.section_id: sec for sec in doc.sections}
    gaps = [str(row[0]) for row in doc.tables["harness_gaps"].rows if row]
    return sections, gaps


def test_a_run_solves_then_waits_on_the_case_which_spends_no_solve(granted, rated, site):
    """I2 on the real runner: the queued solve, then the case step waits and
    settles completed from the case's own record; the report carries the
    chapter; the case is not counted a solve."""
    from harness import run_ic
    from services import run_envelope

    task, path, _ = granted(run={**RUN, "profile": REF})
    script = Script()  # never consulted: no L2 round on a passing gate
    record = go(harness_runner.Runner("a", agent_provider=script), path, task)
    run = record["run"]
    assert run["status"] == "completed" and run["stage"] == "handoff", (
        run["status"],
        run["stage"],
        [(s["id"], s["status"], s.get("error")) for s in record["steps"]],
    )
    assert script.requests == []
    solve = {s["id"]: s for s in record["steps"] if s["id"].startswith("solve.")}
    assert [(s["tool"], s["status"]) for s in solve.values()] == [
        ("solve_queue_enqueue", "completed"),
        (run_ic.TOOL, "completed"),
    ]  # the solve stage ran the case after the queued solve
    queue, case = solve.values()
    assert list(solve) == ["solve.1", "solve.2"]
    # The step waited (a job), and settled from the case's own record.
    assert case["job"]["kind"] == run_ic.STUDY and case["job"]["outcome"] == "done"
    own = _ctx().solver_state["investment_case"]
    assert own["status"] == "done" and own["started_at"] == case["job"]["started_at"]
    assert case["result"] == {
        "job_id": case["job"]["job_id"],
        "final_status": "completed",
        "study": run_ic.STUDY,
        "started_at": own["started_at"],
        "solves": 0,
    }
    # The profile's gate read the case: the IRR rule passes on its figure, the
    # DSCR is not evaluated on the shipped no-debt case.
    rules = {r["rule"]: r for r in run["stages"]["result_gate"]["gate"]["rules"]}
    assert rules["ipp.ic.pre_tax_irr"]["verdict"] == "pass"
    assert rules["ipp.ic.min_dscr"]["verdict"] == "not_evaluated"
    assert rules["ipp.export.limit"]["verdict"] == "pass"
    assert run["stages"]["explore"]["status"] == "skipped"
    # The report is ready and its gap list is what the report gate accepts.
    report = {r["rule"]: r["verdict"] for r in run["stages"]["report"]["gate"]["rules"]}
    assert report == {"rp.report.status": "pass", "rp.report.unsupported_listed": "pass"}
    sections, gaps = _case_chapter(record, site[0])
    PRINT.append((sorted(sections), {k: v.status for k, v in sections.items()}, gaps))
    # Acceptance 4: the case spent no solve: the one queued solve is the count.
    envelope = envelope_of(path)
    assert run_envelope.solves_used(envelope) == 1
    used = [(a["tool"]) for a in envelope["audit"] if a["event"] == "used"]
    assert used.count("solve_queue_enqueue") == 1 and used.count(run_ic.TOOL) == 1
    assert not any("reserved" in a for a in envelope["audit"])
    assert run["budget"]["used"]["solves"] == 1


PRINT: list = []


def _ctx():
    return PyPSAService.get_active_context()
