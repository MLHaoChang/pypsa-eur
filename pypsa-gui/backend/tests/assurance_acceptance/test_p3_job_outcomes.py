"""A3.1/A3.3: blocking foreground-job outcomes come from an independent read.

Issue 04, slice S1 (GK B (24) conditions J1-J8). The outcome of
run_simulation, run_ac_pf_stage, wait_for_job and abort_simulation is
classified by `harness.outcomes` from the live solver record and the run's
evidence marker, never from the handler's own status fields (#1079: an
unreadable outcome is never success). Nonblocking jobs stay pending on
live-voice issue 11.
"""

import threading
import time

import pytest

from harness import job_adapters as ja, outcomes
from services import run_evidence
from services.pypsa_service import PyPSAService
from tests.assurance_acceptance.support import OMIT, tool_turn, text_turn


@pytest.fixture(autouse=True)
def _reset_jobs():
    ja._reset_for_tests()
    outcomes._reset_for_tests()
    yield
    ja._reset_for_tests()
    outcomes._reset_for_tests()


@pytest.fixture
def fg_job(baseline):
    """A registered foreground solve bound to the baseline project's solver record."""
    ctx = PyPSAService.get_active_context()
    worker = threading.Thread(target=lambda: None)
    with ctx.solver_state_lock:
        ctx.solver_state.update(
            status="running",
            kind="lopf",
            objective=None,
            condition=None,
            thread=worker,
            started_at=time.time(),
        )
    job_id = ja.register_foreground_solve(
        tool_name="run_simulation",
        tool_use_id="tu-run",
        owner_user_id=None,
        project_uuid=ctx.project_uuid,
        kind="lopf",
        ctx=ctx,
    )
    token = run_evidence.start_foreground_run(
        ctx, job_id=job_id, tool_name="run_simulation", kind="lopf"
    )
    assert token is not None, "the run's evidence marker is the independent run-id read"
    outcomes.note_submission(
        job_id, ctx, token
    )  # what the loop does when it mints the handle

    def settle(**fields):
        with ctx.solver_state_lock:
            ctx.solver_state.update(fields)

    return {"ctx": ctx, "job_id": job_id, "settle": settle, "token": token}


def _stream_wait(harness, job_id):
    # An ordinary turn: checked turns cannot run job tools (CHECKED_TOOLS).
    harness.monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_MODE", "disabled")
    frames = harness.run(
        [
            tool_turn(
                "wait_for_job", {"job_id": job_id, "timeout_seconds": 0}, call_id="wait"
            ),
            text_turn("Done."),
        ],
        assurance=OMIT,
        message="Wait for the solve.",
    )
    return next(d["result"] for e, d in frames if e == "tool_result")


def test_wait_on_a_failed_job_reads_failed_in_an_ordinary_turn(
    fg_job, harness, monkeypatch
):
    fg_job["settle"](status="failed", condition="infeasible")
    result = _stream_wait(harness, fg_job["job_id"])
    assert result["_outcome"]["outcome"] == "failed"
    assert result["_outcome"]["job_id"] == fg_job["job_id"]


def _project(fg_job, tool="wait_for_job"):
    args = {"job_id": fg_job["job_id"]} if tool == "wait_for_job" else {}
    return outcomes.project(tool, args, "tu-run")


# J1: the handler's own fields never decide the outcome.
def test_a_handler_claiming_completed_reads_running_while_the_solver_runs(
    fg_job, harness, monkeypatch
):
    monkeypatch.setattr(
        ja,
        "wait_foreground_job",
        lambda job_id, *a, **k: {
            "job": {"id": job_id, "status": "completed"},
            "terminal": True,
            "completed": True,
        },
    )
    result = _stream_wait(harness, fg_job["job_id"])
    assert result["completed"] is True  # what the handler said
    assert result["_outcome"]["outcome"] == "running"


@pytest.mark.parametrize(
    "status,expected", [("queued", "submitted"), ("running", "running")]
)
def test_a_just_submitted_solve_is_never_completed(fg_job, status, expected):
    fg_job["settle"](status=status)
    for tool in ("run_simulation", "wait_for_job"):
        assert _project(fg_job, tool)["outcome"] == expected


# J2: an unreadable input is uncertain, one test per input.
def test_a_raising_solver_read_is_uncertain(fg_job, monkeypatch):
    fg_job["settle"](status="completed")
    monkeypatch.setattr(
        PyPSAService,
        "get_active_context",
        staticmethod(lambda: (_ for _ in ()).throw(RuntimeError("gone"))),
    )
    out = _project(fg_job)
    assert (out["outcome"], out["reason"]) == ("uncertain", "read_failed")


def test_a_raising_run_record_read_is_uncertain(fg_job, monkeypatch):
    fg_job["settle"](status="completed")
    monkeypatch.setattr(
        run_evidence,
        "_load_run",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk")),
    )
    out = _project(fg_job)
    assert (out["outcome"], out["reason"]) == ("uncertain", "read_failed")


def test_a_missing_run_record_is_uncertain(fg_job, monkeypatch):
    fg_job["settle"](status="completed")
    monkeypatch.setattr(run_evidence, "_load_run", lambda *a, **k: None)
    out = _project(fg_job)
    assert (out["outcome"], out["reason"]) == ("uncertain", "no_run_record")


def test_a_missing_submission_record_is_uncertain(fg_job):
    fg_job["settle"](status="completed")
    outcomes._reset_for_tests()
    out = _project(fg_job)
    assert (out["outcome"], out["reason"]) == ("uncertain", "no_submission")


def test_an_unknown_solver_status_is_uncertain(fg_job):
    fg_job["settle"](status="idle")
    assert _project(fg_job)["outcome"] == "uncertain"


# J3: completed needs this call's own job; each mismatch alone is uncertain.
def test_the_matching_completed_job_is_completed(fg_job):
    fg_job["settle"](status="completed")
    assert _project(fg_job)["outcome"] == "completed"


def test_another_projects_job_is_uncertain(fg_job, monkeypatch):
    fg_job["settle"](status="completed")
    monkeypatch.setattr(fg_job["ctx"], "project_uuid", "another-project")
    out = _project(fg_job)
    assert (out["outcome"], out["reason"]) == ("uncertain", "other_project")


def test_another_context_is_uncertain(fg_job, monkeypatch):
    fg_job["settle"](status="completed")
    real = outcomes.read_live
    monkeypatch.setattr(outcomes, "read_live", lambda e: {**real(e), "ctx": -1})
    out = _project(fg_job)
    assert (out["outcome"], out["reason"]) == ("uncertain", "other_context")


def test_an_unrelated_terminal_job_cannot_complete_this_one(fg_job):
    fg_job["settle"](status="completed", thread=threading.Thread(target=lambda: None))
    out = _project(fg_job)
    assert (out["outcome"], out["reason"]) == ("uncertain", "other_job")


def test_a_newer_start_of_the_same_run_id_is_uncertain(fg_job, monkeypatch):
    fg_job["settle"](status="completed")
    real = run_evidence._load_run
    monkeypatch.setattr(
        run_evidence,
        "_load_run",
        lambda *a, **k: {**real(*a, **k), "started_at": "2099-01-01T00:00:00Z"},
    )
    out = _project(fg_job)
    assert (out["outcome"], out["reason"]) == ("uncertain", "other_start")


def test_a_run_record_of_another_job_is_uncertain(fg_job, monkeypatch):
    fg_job["settle"](status="completed")
    real = run_evidence._load_run
    monkeypatch.setattr(
        run_evidence,
        "_load_run",
        lambda *a, **k: {**real(*a, **k), "job_id": "fg:other"},
    )
    out = _project(fg_job)
    assert (out["outcome"], out["reason"]) == ("uncertain", "other_run")


def test_wait_on_a_study_or_queue_job_is_pending_not_completed(baseline):
    for job_id in ("ad:x", "rpt:x", "4b1f0c6e-0000-4000-8000-000000000000"):
        out = outcomes.project("wait_for_job", {"job_id": job_id}, "tu")
        assert (out["outcome"], out["reason"]) == ("uncertain", "adapter_pending")


# J4: abort is a request until the solver reads aborted.
@pytest.mark.parametrize(
    "status,expected",
    [
        ("running", "cancel_requested"),
        ("aborted", "cancelled"),
        ("completed", "completed"),
        ("failed", "failed"),
    ],
)
def test_abort_reads_the_solver_not_the_request(fg_job, status, expected):
    fg_job["settle"](status=status)
    out = _project(fg_job, "abort_simulation")
    assert (out["outcome"], out["job_id"]) == (expected, fg_job["job_id"])


def test_a_failed_abort_read_is_uncertain(fg_job, monkeypatch):
    fg_job["settle"](status="aborted")
    monkeypatch.setattr(outcomes, "read_live", lambda e: {"error": "OSError"})
    assert _project(fg_job, "abort_simulation")["outcome"] == "uncertain"


def test_abort_with_no_solve_of_ours_is_uncertain(fg_job):
    fg_job["settle"](status="aborted", thread=threading.Thread(target=lambda: None))
    out = _project(fg_job, "abort_simulation")
    assert (out["outcome"], out["job_id"]) == ("uncertain", None)


# J5: a successful return over a failed or partial job is failed or partial.
@pytest.mark.parametrize("tool", ["run_simulation", "wait_for_job"])
def test_failed_job_reads_failed(fg_job, tool):
    fg_job["settle"](status="failed")
    assert _project(fg_job, tool)["outcome"] == "failed"


@pytest.mark.parametrize("tool", ["run_ac_pf_stage", "wait_for_job"])
@pytest.mark.parametrize(
    "ok,total,expected",
    [
        (3, 4, "partial"),
        (4, 4, "completed"),
        (None, 4, "uncertain"),
        (5, 4, "uncertain"),
    ],
)
def test_ac_pf_convergence_counts_decide_partial(fg_job, tool, ok, total, expected):
    fg_job["settle"](
        status="completed",
        kind="ac_pf",
        ac_pf_converged_count=ok,
        ac_pf_total_snapshots=total,
    )
    assert _project(fg_job, tool)["outcome"] == expected


def test_wait_on_a_partial_ac_pf_reads_partial_in_an_ordinary_turn(
    fg_job, harness, monkeypatch
):
    fg_job["settle"](
        status="completed",
        kind="ac_pf",
        ac_pf_converged_count=1,
        ac_pf_total_snapshots=2,
    )
    assert _stream_wait(harness, fg_job["job_id"])["_outcome"]["outcome"] == "partial"


# J7: the pure classifier, one row per state and per refusal.
_EXP = {
    "job_id": "fg:a",
    "ctx": 1,
    "project": "p",
    "worker": "w",
    "run_id": "fg-a",
    "started_at": "t",
}
_READ = {
    "ctx": 1,
    "project": "p",
    "worker": "w",
    "status": "completed",
    "kind": "lopf",
    "run": {"run_id": "fg-a", "job_id": "fg:a", "started_at": "t"},
}


@pytest.mark.parametrize(
    "change,abort,expected",
    [
        ({}, False, ("completed", None)),
        ({"status": "queued"}, False, ("submitted", None)),
        ({"status": "running"}, False, ("running", None)),
        ({"status": "running"}, True, ("cancel_requested", None)),
        ({"status": "queued"}, True, ("cancel_requested", None)),
        ({"status": "aborted"}, False, ("cancelled", None)),
        ({"status": "failed"}, False, ("failed", None)),
        ({"status": None}, False, ("uncertain", "status_unknown")),
        (
            {"kind": "ac_pf", "ac_pf_converged_count": 1, "ac_pf_total_snapshots": 2},
            False,
            ("partial", "snapshots_not_converged"),
        ),
        (
            {"kind": "ac_pf", "ac_pf_converged_count": 2, "ac_pf_total_snapshots": 2},
            False,
            ("completed", None),
        ),
        ({"kind": "ac_pf"}, False, ("uncertain", "convergence_unknown")),
        (
            {
                "kind": "ac_pf",
                "ac_pf_converged_count": True,
                "ac_pf_total_snapshots": 1,
            },
            False,
            ("uncertain", "convergence_unknown"),
        ),
        ({"error": "OSError"}, False, ("uncertain", "read_failed")),
        ({"ctx": 2}, False, ("uncertain", "other_context")),
        ({"project": "q"}, False, ("uncertain", "other_project")),
        ({"worker": "v"}, False, ("uncertain", "other_job")),
        ({"run": None}, False, ("uncertain", "no_run_record")),
        (
            {"run": {**_READ["run"], "run_id": "fg-b"}},
            False,
            ("uncertain", "other_run"),
        ),
        (
            {"run": {**_READ["run"], "job_id": "fg:b"}},
            False,
            ("uncertain", "other_run"),
        ),
        (
            {"run": {**_READ["run"], "started_at": "u"}},
            False,
            ("uncertain", "other_start"),
        ),
    ],
)
def test_classify_table(change, abort, expected):
    verdict = outcomes.classify("wait_for_job", _EXP, {**_READ, **change}, abort=abort)
    assert (verdict["outcome"], verdict["reason"]) == expected


@pytest.mark.parametrize("field", ["ctx", "project", "worker", "run_id"])
def test_classify_refuses_a_submission_missing_an_identity_field(field):
    verdict = outcomes.classify("wait_for_job", {**_EXP, field: None}, _READ)
    assert verdict["outcome"] == "uncertain"


def test_classify_without_a_submission_or_read_is_uncertain():
    assert outcomes.classify("wait_for_job", None, _READ)["outcome"] == "uncertain"
    assert outcomes.classify("wait_for_job", _EXP, None)["outcome"] == "uncertain"


def test_the_outcome_survives_truncation_of_a_long_result(fg_job, harness, monkeypatch):
    # GK B (25) #1150: a result over 4000 chars becomes a preview; the outcome leads it.
    monkeypatch.setattr(
        ja,
        "wait_foreground_job",
        lambda job_id, *a, **k: {
            "job": {"id": job_id, "status": "completed"},
            "completed": True,
            "note": "x" * 5000,
        },
    )
    fg_job["settle"](status="failed")
    result = _stream_wait(harness, fg_job["job_id"])
    assert result["_truncated"] is True
    assert result["preview"].startswith(
        '{"_outcome": {"version": 1, "outcome": "failed"'
    )
