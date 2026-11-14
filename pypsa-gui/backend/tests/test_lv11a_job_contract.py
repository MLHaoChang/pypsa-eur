"""Live-voice 11a: one neutral job view and a verified cancel for every family.

Families: foreground simulation (``fg:``), adequacy/study mesh (``ad:``, the five
STUDY_KEYS plus investment and business case), report jobs (``rpt:``) and the solve
queue (UUIDs; GridSpine is a queue kind). No real solves: a fake worker thread waits
on the record's own stop event and flips its status, as the native workers do.

Hand oracle per family: a running job reads ``terminal: false, outcome: None`` with
cursor 1; the same read again keeps cursor 1; a phase change moves it to 2; a cancel
reports ``cancelled`` only once the worker has flipped to its aborted status, and
``cancel_requested`` while it has not; a finished job is ``already_finished`` with
nothing sent; a foreign actor reads 404 ``job_not_found``; another project's handle
reads 409 ``job_project_mismatch``.
"""

# ruff: noqa: F811 -- shared public harness fixtures
from __future__ import annotations

import threading
import time
import uuid

import pytest
from fastapi import HTTPException

from harness import job_adapters as ja
from services import chat_tools
from services.solve_queue import KIND_GRIDSPINE, solve_queue
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401

NATIVE = {
    # family: its solver_state key (None: the top-level foreground record)
    "fg": None,
    "ad": "fmea_sweep",
    "rpt": "report_job",
}


@pytest.fixture(autouse=True)
def _reset():
    ja._reset_for_tests()
    yield
    ja._reset_for_tests()


def _record(ctx, family):
    key = NATIVE[family]
    return ctx.solver_state if key is None else ctx.solver_state[key]


def _worker(ctx, family, *, honours_stop=True, aborted="aborted"):
    """A fake native worker: it stops when its record's stop event is set."""
    stop, release = threading.Event(), threading.Event()

    def run():
        # One that ignores its stop keeps running until the test releases it.
        while not (stop if honours_stop else release).wait(0.01):
            pass
        if honours_stop:
            with ctx.solver_state_lock:
                _record(ctx, family)["status"] = aborted

    thread = threading.Thread(target=run, daemon=True)
    thread.release = release
    return thread, stop


def _start(ctx, family, *, honours_stop=True, study_key="fmea_sweep"):
    owner = chat_tools.acting_user_id()
    project = str(ctx.project_uuid)
    thread, stop = _worker(ctx, family, honours_stop=honours_stop)
    record = {
        "status": "running",
        "thread": thread,
        "stop_event": stop,
        "started_at": time.time(),
    }
    with ctx.solver_state_lock:
        if family == "fg":
            ctx.solver_state.update(record, kind="lopf", objective=None, condition=None)
        elif family == "ad":
            ctx.solver_state[study_key] = {**record, "stage": "sweep", "iterations": []}
        else:
            ctx.solver_state["report_job"] = {
                **record,
                "report_id": "rep-1",
                "mode": "generate",
                "section": "summary",
                "error": None,
            }
    thread.start()
    if family == "fg":
        job = ja.register_foreground_solve(
            tool_name="run_simulation",
            tool_use_id="tu-fg",
            owner_user_id=owner,
            project_uuid=project,
            kind="lopf",
        )
    elif family == "ad":
        job = ja.register_study_job(
            study_key=study_key,
            tool_name="run_fmea_sweep",
            tool_use_id="tu-ad",
            owner_user_id=owner,
            project_uuid=project,
        )
    else:
        job = ja.register_report_job(
            tool_name="generate_report",
            tool_use_id="tu-rpt",
            report_id="rep-1",
            owner_user_id=owner,
            project_uuid=project,
        )
    return job, thread, stop


def _stop_all(started):
    for _job, thread, stop in started:
        stop.set()
        thread.release.set()
        thread.join(1)


def _queue(ctx, path, kind="solve"):
    solve_queue.pause()
    job = solve_queue.enqueue(
        "Tool Baseline", project_key=ctx.registry_key, storage_dir=str(path), kind=kind
    )
    return str(job.id), job


FIELDS = {
    "contract",
    "id",
    "family",
    "kind",
    "tool_use_id",
    "owner_user_id",
    "project_id",
    "status",
    "phase",
    "progress",
    "cursor",
    "created_at",
    "started_at",
    "updated_at",
    "finished_at",
    "input_revision",
    "result_revision",
    "warnings",
    "terminal",
    "outcome",
    "detail",
}


@pytest.mark.parametrize("family", ["fg", "ad", "rpt"])
def test_a_running_native_job_reads_the_neutral_view(baseline, family):
    ctx, project_id, _ = baseline
    started = [_start(ctx, family)]
    try:
        job = started[0][0]
        view = ja.job_status(job, actor_id=chat_tools.acting_user_id())
        assert set(view) == FIELDS
        assert view["contract"] == ja.CONTRACT_VERSION == 1
        assert view["id"] == job and view["family"] == ja._family_of(job)
        assert view["project_id"] == str(ctx.project_uuid)
        assert view["owner_user_id"] == chat_tools.acting_user_id()
        assert (view["status"], view["terminal"], view["outcome"]) == (
            "running",
            False,
            None,
        )
        assert view["cursor"] == 1 and view["result_revision"] is None
        assert view["input_revision"] == ctx.network_revision
        assert view["finished_at"] is None and view["started_at"] is not None
        # The same read again does not move the cursor: a monitor dedupes on it.
        again = ja.job_status(job, actor_id=chat_tools.acting_user_id())
        assert again["cursor"] == 1 and again["updated_at"] == view["updated_at"]
    finally:
        _stop_all(started)


def test_a_phase_change_moves_the_cursor_and_success_carries_a_result_revision(
    baseline,
):
    ctx, *_ = baseline
    started = [_start(ctx, "ad")]
    actor = chat_tools.acting_user_id()
    try:
        job = started[0][0]
        assert ja.job_status(job, actor_id=actor)["cursor"] == 1
        with ctx.solver_state_lock:
            ctx.solver_state["fmea_sweep"]["stage"] = "restore"
        moved = ja.job_status(job, actor_id=actor)
        assert (moved["cursor"], moved["phase"]) == (2, "restore")
        with ctx.solver_state_lock:
            ctx.solver_state["fmea_sweep"]["status"] = "done"
        done = ja.job_status(job, actor_id=actor)
        assert (done["cursor"], done["terminal"], done["outcome"]) == (
            3,
            True,
            "succeeded",
        )
        assert done["finished_at"] is not None
        assert (
            done["result_revision"]
            == f"fmea_sweep:{ctx.solver_state['fmea_sweep']['started_at']}"
        )
    finally:
        _stop_all(started)


@pytest.mark.parametrize(
    "family,aborted", [("fg", "aborted"), ("ad", "aborted"), ("rpt", "aborted")]
)
def test_cancel_is_verified_by_the_worker(baseline, family, aborted):
    ctx, *_ = baseline
    started = [_start(ctx, family)]
    actor = chat_tools.acting_user_id()
    try:
        out = ja.cancel_job(started[0][0], actor_id=actor, settle_seconds=2)
        assert out["cancel"] == "cancelled", out["job"]["status"]
        assert out["job"]["outcome"] == "cancelled" and out["job"]["status"] == aborted
        assert started[0][
            2
        ].is_set()  # the record's own stop event, through the existing abort
    finally:
        _stop_all(started)


@pytest.mark.parametrize("family", ["ad", "rpt"])
def test_a_worker_that_has_not_stopped_yet_is_cancel_requested(baseline, family):
    ctx, *_ = baseline
    started = [_start(ctx, family, honours_stop=False)]
    try:
        out = ja.cancel_job(
            started[0][0], actor_id=chat_tools.acting_user_id(), settle_seconds=0.2
        )
        assert out["cancel"] == "cancel_requested"
        assert out["job"]["terminal"] is False and out["job"]["status"] == "running"
        assert started[0][2].is_set()
    finally:
        _stop_all(started)


@pytest.mark.parametrize("family", ["fg", "ad", "rpt"])
def test_a_finished_job_is_already_finished_and_nothing_is_sent(baseline, family):
    ctx, *_ = baseline
    started = [_start(ctx, family)]
    try:
        with ctx.solver_state_lock:
            _record(ctx, family)["status"] = "completed" if family == "fg" else "done"
        out = ja.cancel_job(started[0][0], actor_id=chat_tools.acting_user_id())
        assert (
            out["cancel"] == "already_finished" and out["job"]["outcome"] == "succeeded"
        )
        assert not started[0][2].is_set()
    finally:
        _stop_all(started)


def test_a_cancel_never_stops_a_newer_run_that_replaced_the_handle(baseline):
    ctx, *_ = baseline
    first = _start(ctx, "ad")
    second = _start(ctx, "ad")  # a new sweep replaced the record the first handle bound
    try:
        out = ja.cancel_job(first[0], actor_id=chat_tools.acting_user_id())
        assert out["cancel"] == "origin_lost"
        assert (
            not second[2].is_set()
            and ctx.solver_state["fmea_sweep"]["status"] == "running"
        )
    finally:
        _stop_all([first, second])


@pytest.mark.parametrize(
    "study_key,tool",
    [
        ("investment_case", "abort_investment_case"),
        ("business_case", "abort_business_case"),
        ("mc", "abort_adequacy_study"),
    ],
)
def test_each_study_key_takes_its_own_existing_abort(
    baseline, monkeypatch, study_key, tool
):
    ctx, *_ = baseline
    calls = []
    monkeypatch.setattr(chat_tools, tool, lambda *a: calls.append(a) or {})
    started = [_start(ctx, "ad", honours_stop=False, study_key=study_key)]
    try:
        out = ja.cancel_job(
            started[0][0], actor_id=chat_tools.acting_user_id(), settle_seconds=0
        )
        assert out["cancel"] == "cancel_requested"
        assert calls == ([(study_key,)] if tool == "abort_adequacy_study" else [()])
    finally:
        _stop_all(started)


@pytest.mark.parametrize("family", ["fg", "ad", "rpt"])
def test_a_foreign_actor_reads_and_cancels_nothing(baseline, family):
    ctx, *_ = baseline
    started = [_start(ctx, family)]
    try:
        for call in (ja.job_status, ja.cancel_job):
            with pytest.raises(HTTPException) as refused:
                call(started[0][0], actor_id=str(uuid.uuid4()))
            assert refused.value.status_code == 404
            assert refused.value.detail["error_kind"] == "job_not_found"
        assert not started[0][2].is_set()
    finally:
        _stop_all(started)


def test_another_projects_handle_reads_project_mismatch(baseline):
    ctx, *_ = baseline
    job = ja.register_study_job(
        study_key="fmea_sweep",
        tool_name="run_fmea_sweep",
        tool_use_id="tu",
        owner_user_id=chat_tools.acting_user_id(),
        project_uuid=str(uuid.uuid4()),
    )
    for call in (ja.job_status, ja.cancel_job):
        with pytest.raises(HTTPException) as refused:
            call(job, actor_id=chat_tools.acting_user_id())
        assert refused.value.status_code == 409
        assert refused.value.detail["error_kind"] == "job_project_mismatch"


@pytest.mark.parametrize("kind", ["solve", KIND_GRIDSPINE])
def test_a_queue_job_reads_the_neutral_view_and_cancels(baseline, kind):
    ctx, _project_id, path = baseline
    job_id, job = _queue(ctx, path, kind)
    actor = chat_tools.acting_user_id()
    view = ja.job_status(job_id, actor_id=actor)
    assert set(view) == FIELDS
    assert (view["family"], view["kind"], view["status"]) == (
        "solve_queue",
        kind,
        "queued",
    )
    assert (view["terminal"], view["outcome"], view["cursor"]) == (False, None, 1)
    assert view["progress"] == {"position": view["detail"]["position"]}
    out = ja.cancel_job(job_id, actor_id=actor, settle_seconds=1)
    assert out["cancel"] == "cancelled"
    assert (out["job"]["status"], out["job"]["outcome"]) == ("aborted", "cancelled")
    assert out["job"]["cursor"] == 2
    again = ja.cancel_job(job_id, actor_id=actor)
    assert again["cancel"] == "already_finished"


def test_a_queue_job_that_completed_carries_its_result_revision(baseline):
    ctx, _project_id, path = baseline
    job_id, job = _queue(ctx, path)
    job.status, job.finished_at = "completed", 123.0
    view = ja.job_status(job_id, actor_id=chat_tools.acting_user_id())
    assert (
        view["outcome"] == "succeeded"
        and view["result_revision"] == f"queue:{job_id}@123.0"
    )


def test_an_unknown_queue_id_is_not_found(baseline):
    for job_id in (str(uuid.uuid4()), "not-a-job", ""):
        with pytest.raises(HTTPException) as refused:
            ja.job_status(job_id, actor_id=chat_tools.acting_user_id())
        assert refused.value.status_code == 404


def test_every_native_terminal_status_maps_to_one_outcome():
    for family, statuses in (
        (ja.FOREGROUND_FAMILY, ja._FG_TERMINAL),
        (ja.STUDY_FAMILY, ja._STUDY_TERMINAL),
        (ja.REPORT_FAMILY, ja._REPORT_TERMINAL),
    ):
        for status in statuses:
            assert ja._outcome(family, status) in ja.OUTCOMES - {"unknown"}, (
                family,
                status,
            )
    assert ja._outcome(ja.STUDY_FAMILY, "met") == "succeeded"
    assert ja._outcome(ja.STUDY_FAMILY, "unreachable") == "not_met"
    # Shared with LOOP_STATUSES, but keep their own outcome.
    assert ja._outcome(ja.STUDY_FAMILY, "aborted") == "cancelled"
    assert ja._outcome(ja.STUDY_FAMILY, "failed") == "failed"
    assert ja._outcome(ja.FOREGROUND_FAMILY, "running") is None
    assert ja._outcome(ja.FOREGROUND_FAMILY, "unknown") == "unknown"
