# ruff: noqa: F811 -- imported pytest fixtures
"""LC-45: a profile generation and a decision study run as `start_task` plan
steps, so the conversation continues by itself and the model never polls.

The real follower runs (short poll, short cap); only the native reads under it
(the queue job, the pending artifacts, the study record) are faked."""

import pytest
from fastapi import HTTPException

from harness import continuation
from services import chat_tools
from tests.test_assistant_tasks_study_brief import (  # noqa: F401
    brief,
    dispatch,
    session,
    stored,
    tool_result,
    wait_until,
)
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(continuation, "QUEUE_POLL_SECONDS", 0.05)

    def poll(*a, **k):
        raise AssertionError("polled")

    for name in ("solve_queue_list", "get_study_findings"):
        monkeypatch.setitem(chat_tools.DISPATCHERS, name, poll)


def _wait_done(root, task_id, n=0):
    assert wait_until(
        lambda: stored(root, task_id)["steps"][n]["status"] != "waiting", 30
    )
    return stored(root, task_id)["steps"][n]


# ── profile generation (L1, L2, L10) ─────────────────────────────────────────

ARGS = {"project_id": "p", "technology": "solar", "source": "synthetic", "points": []}


def _profile(monkeypatch, request_id, job, artifacts):
    """A one-step profile plan; `job` is the native queue row, `artifacts` the pending list."""
    import routers.solve_queue as sq

    steps = [{"id": "gen", "tool": "generate_renewable_profile", "args": ARGS}]
    task = chat_tools.start_task(
        title="Profile", request_id=request_id, steps=steps, brief=brief(steps)
    )
    monkeypatch.setitem(
        chat_tools.DISPATCHERS,
        "generate_renewable_profile",
        lambda **k: {
            "id": "job-1",
            "kind": "profile_gen",
            "project_key": "org:uuid-1",
            "status": "queued",
        },
    )
    monkeypatch.setattr(sq, "_visible_job_or_404", lambda db, user, job_id: job)
    seen = []

    def pending(project_id):
        seen.append(project_id)
        return {"pending": artifacts}

    monkeypatch.setattr(chat_tools, "list_pending_profiles", pending)
    return task, seen


def _artifact(job_id="job-1", **kw):
    return {"job_id": job_id, "source": "synthetic", "technology": "solar", **kw}


def test_profile_job_completes_the_step_from_its_own_artifact(
    session, baseline, monkeypatch
):
    _, _, root = baseline
    task, seen = _profile(
        monkeypatch, "lc45-p1", {"status": "completed"}, [_artifact()]
    )
    frames, results = dispatch(session, monkeypatch, "generate_renewable_profile", ARGS)
    assert not [p for e, p in frames if e == "tool_error"], frames
    assert tool_result(results)["waiting"] is True
    assert _wait_done(root, task["task_id"])["status"] == "completed"
    assert seen and set(seen) == {"p"}  # read from the project the call named


def test_a_failed_profile_job_fails_the_step_with_its_own_error(
    session, baseline, monkeypatch
):
    _, _, root = baseline
    task, _ = _profile(
        monkeypatch,
        "lc45-p2",
        {"status": "failed", "error": "ERA5 needs CDS credentials"},
        [],
    )
    dispatch(session, monkeypatch, "generate_renewable_profile", ARGS)
    step = _wait_done(root, task["task_id"])
    assert step["status"] == "failed" and "ERA5 needs CDS credentials" in str(step)


@pytest.mark.parametrize(
    "artifacts",
    [
        [_artifact("earlier-job")],  # an earlier run's artifact (L1)
        [_artifact(source="atlite")],  # not the source asked for
        [_artifact(technology="wind")],  # not the technology asked for
        [],
    ],
)
def test_a_completed_profile_job_without_its_own_artifact_is_uncertain(
    session, baseline, monkeypatch, artifacts
):
    _, _, root = baseline
    task, _ = _profile(monkeypatch, "lc45-p3", {"status": "completed"}, artifacts)
    dispatch(session, monkeypatch, "generate_renewable_profile", ARGS)
    assert _wait_done(root, task["task_id"])["status"] == "uncertain"


def test_an_unreadable_artifact_listing_is_uncertain(session, baseline, monkeypatch):
    _, _, root = baseline
    task, _ = _profile(monkeypatch, "lc45-p4", {"status": "completed"}, [])

    def boom(project_id):
        raise HTTPException(403, "no")

    monkeypatch.setattr(chat_tools, "list_pending_profiles", boom)
    dispatch(session, monkeypatch, "generate_renewable_profile", ARGS)
    assert _wait_done(root, task["task_id"])["status"] == "uncertain"


def test_an_unknown_job_kind_is_never_completed(monkeypatch):
    import routers.solve_queue as sq

    monkeypatch.setattr(
        sq, "_visible_job_or_404", lambda db, user, job_id: {"status": "completed"}
    )
    monkeypatch.setattr(continuation, "_queue_run", lambda job: {"x": 1})
    status, _, result = continuation.queue_outcome(
        [{"job_id": "j", "project_id": "p", "kind": "mystery"}]
    )
    assert status == "uncertain" and result["cases"][0]["status"] == "unknown"


# ── decision study (L3, L4, L5, L6, L10) ─────────────────────────────────────

S1 = {"project": "p", "study_id": "s1"}


def _study(monkeypatch, request_id, records):
    """A two-step plan; `records` maps a stage to a list of successive records (the last repeats)."""
    steps = [
        {
            "id": "options",
            "tool": "run_decision_study",
            "args": {**S1, "stage": "options"},
        },
        {
            "id": "tornado",
            "tool": "run_decision_study",
            "args": {**S1, "stage": "tornado"},
        },
    ]
    task = chat_tools.start_task(
        title="Decision study", request_id=request_id, steps=steps, brief=brief(steps)
    )
    monkeypatch.setitem(
        chat_tools.DISPATCHERS,
        "run_decision_study",
        lambda **k: {"stage": k["stage"], "run": {"status": "running"}},
    )
    queue = {stage: list(recs) for stage, recs in records.items()}

    def record(job):
        recs = queue[job["kind"].removeprefix("study_")]
        return dict(recs.pop(0) if len(recs) > 1 else recs[0])

    monkeypatch.setattr(continuation, "_study_record", record)
    return task


def _run(session, monkeypatch, stage):
    return dispatch(session, monkeypatch, "run_decision_study", {**S1, "stage": stage})


def _rec(status="done", started=1.0, **kw):
    """A record in the shape the runners save: one option that solved, a tornado that is ok."""
    return {
        "status": status,
        "started_at": started,
        "options": ["a"],
        "solved": ["a"],
        "pending": [],
        "robustness": {"status": "ok"},
        **kw,
    }


def test_both_stages_continue_without_a_poll(session, baseline, monkeypatch):
    _, _, root = baseline
    task = _study(
        monkeypatch,
        "lc45-s1",
        {"options": [_rec("running"), _rec()], "tornado": [_rec("running"), _rec()]},
    )
    _run(session, monkeypatch, "options")
    assert _wait_done(root, task["task_id"], 0)["status"] == "completed"
    _run(session, monkeypatch, "tornado")
    assert _wait_done(root, task["task_id"], 1)["status"] == "completed"


@pytest.mark.parametrize(
    "extra",
    [
        {"pending": ["b"]},  # an option the run did not reach
        {"options": ["a", "b"]},  # an option row that failed: not in `solved`
    ],
)
def test_a_done_run_with_unsolved_options_is_partial_not_completed(
    session, baseline, monkeypatch, extra
):
    _, _, root = baseline
    task = _study(
        monkeypatch, "lc45-s2", {"options": [_rec(**extra)], "tornado": [_rec()]}
    )
    _run(session, monkeypatch, "options")
    step = _wait_done(root, task["task_id"], 0)
    assert step["status"] == "uncertain"


@pytest.mark.parametrize(
    "status, error", [("failed", "solver died"), ("aborted", None)]
)
def test_a_failed_or_aborted_run_fails_the_step_with_the_record_reason(
    session, baseline, monkeypatch, status, error
):
    _, _, root = baseline
    task = _study(
        monkeypatch,
        "lc45-s3-" + status,
        {"options": [_rec(status, error=error)], "tornado": [_rec()]},
    )
    _run(session, monkeypatch, "options")
    step = _wait_done(root, task["task_id"], 0)
    assert step["status"] == "failed" and (error or "aborted") in str(step)


@pytest.mark.parametrize(
    "terminal, step_status", [("done", "completed"), ("aborted", "failed")]
)
def test_a_run_followed_while_running_settles_only_at_its_terminal_state(
    session, baseline, monkeypatch, terminal, step_status
):
    """Running (a stop may be pending: the runners keep `running`) -> terminal: settles only then."""
    _, _, root = baseline
    seen = []
    steps = [
        {
            "id": "options",
            "tool": "run_decision_study",
            "args": {**S1, "stage": "options"},
        }
    ]
    task = chat_tools.start_task(
        title="Abort", request_id="lc45-s9-" + terminal, steps=steps, brief=brief(steps)
    )
    monkeypatch.setitem(
        chat_tools.DISPATCHERS,
        "run_decision_study",
        lambda **k: {"stage": k["stage"], "run": {"status": "running"}},
    )
    script = ["running", "running", "running", terminal]

    def record(job):
        status = script.pop(0) if len(script) > 1 else script[0]
        seen.append(status)
        return _rec(status)

    monkeypatch.setattr(continuation, "_study_record", record)
    _run(session, monkeypatch, "options")
    step = _wait_done(root, task["task_id"], 0)
    assert step["status"] == step_status
    assert seen.count("running") >= 3 and seen[-1] == terminal
    if terminal == "aborted":
        assert "aborted" in str(step)


def _saved_tornado(monkeypatch, status, error=None, **record):
    """A tornado record as the runner saves it: the dict `tornado_runner._save` hands the store."""
    from types import SimpleNamespace

    from services.study import tornado_runner as T

    saved = {}
    monkeypatch.setattr(
        T.store, "save_aux", lambda base_dir, study_id, name, data: saved.update(data)
    )
    base = {"started_at": 1.0, "variants": []}
    T._save(
        None,
        "s1",
        SimpleNamespace(findings={}),
        {**base, **record},
        None,
        status,
        error,
        [],
    )
    return saved


@pytest.mark.parametrize(
    "status, error, record, native, reason",
    [
        ("aborted", None, {"budget_exhausted": True}, "partial", "budget"),
        ("aborted", "solve_deadline_exceeded: too slow", {}, "partial", "deadline"),
        ("aborted", None, {}, "aborted", "aborted"),  # a user's abort
        ("failed", "RuntimeError: boom", {}, "failed", "boom"),
        (
            "done",
            None,
            {},
            "partial",
            "tornado not established",
        ),  # no robustness result
    ],
)
def test_a_saved_tornado_maps_to_the_step_state_the_user_needs(
    monkeypatch, status, error, record, native, reason
):
    saved = _saved_tornado(monkeypatch, status, error, **record)
    monkeypatch.setattr(continuation, "_study_record", lambda job: saved)
    got, why = continuation._study_state({"kind": "study_tornado", "attempt": 1.0})
    assert got == native and reason in (why or "")


def test_an_options_run_stopped_at_its_deadline_is_partial(monkeypatch):
    saved = _rec(
        "failed", error="solve_deadline_exceeded: wait ran out (option a)", solved=[]
    )
    monkeypatch.setattr(continuation, "_study_record", lambda job: saved)
    got, why = continuation._study_state({"kind": "study_options", "attempt": 1.0})
    assert got == "partial" and "deadline" in why


def test_a_malformed_record_settles_the_followed_step_uncertain(
    session, baseline, monkeypatch
):
    """N1b: the real follower, one malformed record: uncertain, never left waiting."""
    _, _, root = baseline
    bad = {**_rec("running"), "robustness": "ok"}
    task = _study(
        monkeypatch,
        "lc45-s10",
        {"options": [bad, {**bad, "status": "done"}], "tornado": [_rec()]},
    )
    _run(session, monkeypatch, "options")
    assert _wait_done(root, task["task_id"], 0)["status"] == "uncertain"


def test_a_harness_run_step_keeps_its_own_judge_not_the_plan_follower():
    """LC-45: the two new wait tools wait on their job in a plan only; a run's
    step settles from the run's own judge (`run_explore`), as before."""
    from services import assistant_tasks as T

    started = {"stage": "options", "run": {"status": "running"}}
    for tool in sorted(T.PLAN_ONLY_WAIT_TOOLS):
        assert T._outcome(tool, started, None)[0] == "uncertain"
        assert T._outcome(tool, started, None, run=True)[0] == "completed"
    # the sweep and the enqueue are unchanged in a run
    assert T._outcome("solve_queue_enqueue", started, None, run=True)[0] == "uncertain"
