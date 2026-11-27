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
from services import chat_tools, run_evidence
from services.solve_queue import solve_queue
from services.pypsa_service import PyPSAService
from tests.assurance_acceptance.support import OMIT, tool_turn, text_turn


OWNER_ACTING = object()


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


# ── S7: wait_for_job on study (`ad:`), report (`rpt:`) and queue jobs ──────
#
# The outcome is job_adapters' neutral view (`job_status`: the identity-bound
# `_snapshot_bound_job` read, or the queue route's own ACL read), never the
# wait handler's returned fields. GK B (74) S7 conditions 1-6.


def _native(ctx, family, status="running", worker=None, owner=OWNER_ACTING):
    """A registered study or report job bound to the active project's record."""
    worker = worker or threading.Thread(target=lambda: None)  # never started: exited
    record = {
        "status": status,
        "thread": worker,
        "stop_event": threading.Event(),
        "started_at": time.time(),
    }
    owner = chat_tools.acting_user_id() if owner is OWNER_ACTING else owner
    project = str(ctx.project_uuid)
    with ctx.solver_state_lock:
        if family == "ad":
            ctx.solver_state["fmea_sweep"] = {**record, "stage": "sweep", "iterations": []}
        else:
            ctx.solver_state["report_job"] = {
                **record,
                "report_id": "rep-1",
                "mode": "generate",
                "section": "summary",
                "error": None,
            }
    if family == "ad":
        return ja.register_study_job(
            study_key="fmea_sweep",
            tool_name="run_fmea_sweep",
            tool_use_id="tu-ad",
            owner_user_id=owner,
            project_uuid=project,
        )
    return ja.register_report_job(
        tool_name="generate_report",
        tool_use_id="tu-rpt",
        report_id="rep-1",
        owner_user_id=owner,
        project_uuid=project,
    )


def _set(ctx, family, status):
    with ctx.solver_state_lock:
        ctx.solver_state["fmea_sweep" if family == "ad" else "report_job"]["status"] = (
            status
        )


def _queued(baseline, status=None):
    ctx, _project_id, path = baseline
    solve_queue.pause()
    job = solve_queue.enqueue(
        "Tool Baseline",
        project_key=ctx.registry_key,
        storage_dir=str(path),
    )
    if status is not None:
        job.status = status
    return str(job.id)


def _wait(job_id):
    return outcomes.project("wait_for_job", {"job_id": job_id}, "tu")


def test_wait_on_a_study_or_queue_job_is_pending_not_completed(baseline):
    ctx = baseline[0]
    for job_id, expected in (
        (_native(ctx, "ad"), "running"),
        (_native(ctx, "rpt"), "running"),
        (_queued(baseline), "submitted"),
    ):
        out = _wait(job_id)
        assert (out["outcome"], out["reason"], out["job_id"]) == (
            expected,
            None,
            job_id,
        )


@pytest.mark.parametrize(
    "family,status,expected",
    [
        ("ad", "done", ("completed", None)),
        ("ad", "met", ("completed", None)),
        ("ad", "failed", ("failed", None)),
        ("ad", "aborted", ("cancelled", None)),
        ("ad", "refused", ("failed", "job_refused")),
        ("ad", "not_confident", ("partial", "target_not_met")),
        ("ad", "budget_exhausted", ("partial", "target_not_met")),
        ("ad", "unreachable", ("partial", "target_not_met")),
        ("ad", "half_done", ("uncertain", "status_unknown")),
        ("rpt", "done", ("completed", None)),
        ("rpt", "failed", ("failed", None)),
        ("rpt", "aborted", ("cancelled", None)),
        ("rpt", "queued", ("submitted", None)),
        ("rpt", "half_done", ("uncertain", "status_unknown")),
    ],
)
def test_wait_on_a_finished_study_or_report_reads_its_own_outcome(
    baseline, family, status, expected
):
    ctx = baseline[0]
    job_id = _native(ctx, family)
    _set(ctx, family, status)
    out = _wait(job_id)
    assert (out["outcome"], out["reason"]) == expected
    assert out["job_id"] == job_id and out["version"] == outcomes.VERSION


@pytest.mark.parametrize(
    "status,expected",
    [
        ("running", ("running", None)),
        ("completed", ("completed", None)),
        ("failed", ("failed", None)),
        ("aborted", ("cancelled", None)),
        ("interrupted", ("uncertain", "interrupted")),
        ("half_done", ("uncertain", "status_unknown")),
    ],
)
def test_wait_on_a_queue_job_reads_the_queue_route(baseline, status, expected):
    job_id = _queued(baseline, status)
    out = _wait(job_id)
    assert (out["outcome"], out["reason"], out["job_id"]) == (*expected, job_id)


def test_every_neutral_outcome_has_one_tool_outcome():
    # Condition 1: a neutral outcome added to job_adapters fails here first.
    assert set(outcomes.NEUTRAL_OUTCOMES) == set(ja.OUTCOMES)
    for neutral, (outcome, _reason) in outcomes.NEUTRAL_OUTCOMES.items():
        assert outcome in outcomes.OUTCOMES, neutral
    assert outcomes.NEUTRAL_OUTCOMES["succeeded"] == ("completed", None)
    assert outcomes.NEUTRAL_OUTCOMES["unknown"][0] == "uncertain"


def test_a_study_whose_worker_still_runs_after_aborted_is_cancel_requested(baseline):
    # An abort route may flip `aborted` before its worker leaves native code.
    ctx = baseline[0]
    release = threading.Event()
    worker = threading.Thread(target=release.wait, daemon=True)
    worker.start()
    try:
        job_id = _native(ctx, "ad", worker=worker)
        _set(ctx, "ad", "aborted")
        out = _wait(job_id)
        assert (out["outcome"], out["reason"]) == ("cancel_requested", "stop_unconfirmed")
    finally:
        release.set()
        worker.join(1)
    assert (_wait(job_id)["outcome"], _wait(job_id)["reason"]) == ("cancelled", None)


def test_cancelled_is_read_again_before_it_is_believed(baseline, monkeypatch):
    # Condition 5: the first read says cancelled; the re-read decides.
    ctx = baseline[0]
    job_id = _native(ctx, "ad", status="aborted")
    real, reads = ja.job_status, []

    def first_cancelled_then_done(*a, **k):
        view = real(*a, **k)
        reads.append(view["outcome"])
        if len(reads) > 1:
            view = {**view, "status": "done", "outcome": "succeeded"}
        return view

    monkeypatch.setattr(ja, "job_status", first_cancelled_then_done)
    out = _wait(job_id)
    assert len(reads) == 2
    assert (out["outcome"], out["reason"]) == ("completed", None)


def test_a_study_replaced_by_a_newer_run_is_uncertain(baseline):
    ctx = baseline[0]
    job_id = _native(ctx, "ad")
    _set(ctx, "ad", "done")
    _native(ctx, "ad", status="done")  # a newer run owns the record now
    out = _wait(job_id)
    assert (out["outcome"], out["reason"], out["job_id"]) == (
        "uncertain",
        "status_unknown",
        job_id,
    )


def test_a_job_of_another_project_is_uncertain(baseline, monkeypatch):
    ctx = baseline[0]
    job_id = _native(ctx, "ad", status="done")
    monkeypatch.setattr(ja._STUDIES[job_id], "project_uuid", "other-project")
    out = _wait(job_id)
    assert (out["outcome"], out["reason"], out["job_id"]) == (
        "uncertain",
        "other_project",
        job_id,
    )


@pytest.mark.parametrize(
    "job_id", ["ad:unknown", "rpt:unknown", "4b1f0c6e-0000-4000-8000-000000000000"]
)
def test_an_unknown_handle_is_uncertain_and_still_named(baseline, job_id):
    # Condition 4: the queue handle (a UUID) is named whichever way the read goes.
    out = _wait(job_id)
    assert (out["outcome"], out["reason"], out["job_id"]) == (
        "uncertain",
        "job_not_found",
        job_id,
    )


def test_a_queue_job_of_another_project_is_uncertain(baseline, monkeypatch):
    # GK B (74) (a): the queue's ACL is the org's, so a job the actor may see
    # can be another project's; the open project's registry key binds it.
    job_id = _queued(baseline, "completed")
    assert ja.job_status(job_id, actor_id=chat_tools.acting_user_id())["outcome"] == (
        "succeeded"
    )
    monkeypatch.setattr(
        type(baseline[0]), "registry_key", property(lambda self: "org:another-project")
    )
    out = _wait(job_id)
    assert (out["outcome"], out["reason"], out["job_id"]) == (
        "uncertain",
        "other_project",
        job_id,
    )


def test_a_queue_cancel_is_confirmed_without_a_worker_thread(baseline):
    # GK B (74) (b): a queue job binds no worker, so `_worker_exited` is True;
    # the queue flips `aborted` only once its worker returned.
    job_id = _queued(baseline, "aborted")
    assert ja._worker_exited(job_id) is True
    assert (_wait(job_id)["outcome"], _wait(job_id)["reason"]) == ("cancelled", None)


def test_a_queue_job_the_actor_may_not_see_is_uncertain(baseline, monkeypatch):
    import routers.solve_queue as route

    job_id = _queued(baseline, "completed")

    def unauthenticated(*a, **k):
        raise AssertionError("a queue read without an actor must not run")

    monkeypatch.setattr(route, "_visible_job_or_404", unauthenticated)
    token = chat_tools._ACTING_USER_ID.set(None)
    try:
        out = _wait(job_id)
    finally:
        chat_tools._ACTING_USER_ID.reset(token)
    assert (out["outcome"], out["reason"], out["job_id"]) == (
        "uncertain",
        "job_not_found",
        job_id,
    )


def test_a_failing_read_is_uncertain(baseline, monkeypatch):
    ctx = baseline[0]
    job_id = _native(ctx, "rpt", status="done")

    def boom(*a, **k):
        raise RuntimeError("store down")

    monkeypatch.setattr(ja, "job_status", boom)
    out = _wait(job_id)
    assert (out["outcome"], out["reason"], out["job_id"]) == (
        "uncertain",
        "read_failed",
        job_id,
    )


@pytest.mark.parametrize(
    "read,expected",
    [
        (None, ("uncertain", "read_failed")),
        ({"error": "x"}, ("uncertain", "read_failed")),
        ({"id": "ad:other", "same_project": True, "outcome": "succeeded"}, ("uncertain", "other_job")),
        ({"id": "ad:j", "outcome": "succeeded"}, ("uncertain", "other_project")),
        ({"id": "ad:j", "same_project": False, "outcome": "succeeded"}, ("uncertain", "other_project")),
        ({"id": "ad:j", "same_project": True, "outcome": None, "status": "running"}, ("running", None)),
        ({"id": "ad:j", "same_project": True, "outcome": None, "status": "queued"}, ("submitted", None)),
        ({"id": "ad:j", "same_project": True, "outcome": "succeeded"}, ("completed", None)),
        ({"id": "ad:j", "same_project": True, "outcome": None, "status": "paused"}, ("uncertain", "status_unknown")),
        ({"id": "ad:j", "same_project": True, "outcome": "later_name"}, ("uncertain", "status_unknown")),
        ({"id": "ad:j", "same_project": True, "outcome": "cancelled"}, ("cancel_requested", "stop_unconfirmed")),
        (
            {"id": "ad:j", "same_project": True, "outcome": "cancelled", "worker_exited": True},
            ("cancelled", None),
        ),
    ],
)
def test_classify_job_is_pure_and_fails_closed(read, expected):
    out = outcomes.classify_job("ad:j", read)
    assert (out["outcome"], out["reason"]) == expected


def test_a_wait_reads_running_whatever_the_handler_returns(baseline, harness, monkeypatch):
    # Condition 2: the handler's own fields never reach the outcome.
    from services import chat_tools as ct

    job_id = _native(baseline[0], "ad")
    monkeypatch.setitem(
        ct.DISPATCHERS,
        "wait_for_job",
        lambda **_: {"job": {"status": "done", "outcome": "succeeded"}, "completed": True},
    )
    result = _stream_wait(harness, job_id)
    assert result["completed"] is True  # the handler's word stays as it said
    assert (result["_outcome"]["outcome"], result["_outcome"]["job_id"]) == (
        "running",
        job_id,
    )


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
