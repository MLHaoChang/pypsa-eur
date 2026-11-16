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


def _worker(ctx, family, *, honours_stop=True, aborted="aborted", key=None):
    """A fake native worker: it stops when its record's stop event is set."""
    stop, release = threading.Event(), threading.Event()

    def run():
        # One that ignores its stop keeps running until the test releases it.
        while not (stop if honours_stop else release).wait(0.01):
            pass
        if honours_stop:
            with ctx.solver_state_lock:
                (ctx.solver_state[key] if key else _record(ctx, family))["status"] = (
                    aborted
                )

    thread = threading.Thread(target=run, daemon=True)
    thread.release = release
    return thread, stop


def _start(
    ctx, family, *, honours_stop=True, study_key="fmea_sweep", ends_as="aborted"
):
    owner = chat_tools.acting_user_id()
    project = str(ctx.project_uuid)
    thread, stop = _worker(
        ctx,
        family,
        honours_stop=honours_stop,
        aborted=ends_as,
        key=study_key if family == "ad" else None,
    )
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
    "input_revision_note",
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


# ── GK B A1: 4 families x {view, cancel} x {own, foreign actor, foreign project} ──


def _foreign_project_job(ctx, path, family):
    """A job of a project the acting user is not on."""
    if family == "sq":
        solve_queue.pause()
        job = solve_queue.enqueue(
            "Elsewhere",
            project_key=f"{uuid.uuid4()}:{uuid.uuid4()}",
            storage_dir=str(path),
        )
        return str(job.id), None
    kwargs = dict(
        tool_use_id="tu",
        owner_user_id=chat_tools.acting_user_id(),
        project_uuid=str(uuid.uuid4()),
    )
    if family == "fg":
        return ja.register_foreground_solve(
            tool_name="run_simulation", kind="lopf", **kwargs
        ), None
    if family == "ad":
        return ja.register_study_job(
            study_key="fmea_sweep", tool_name="run_fmea_sweep", **kwargs
        ), None
    return ja.register_report_job(
        tool_name="generate_report", report_id="rep-1", **kwargs
    ), None


FOREIGN_PROJECT_KIND = {
    "fg": (409, "job_project_mismatch"),
    "ad": (409, "job_project_mismatch"),
    "rpt": (409, "job_project_mismatch"),
    "sq": (404, "job_not_found"),
}


@pytest.mark.parametrize("call", ["view", "cancel"])
@pytest.mark.parametrize("family", ["fg", "ad", "rpt", "sq"])
@pytest.mark.parametrize("who", ["own", "foreign_actor", "foreign_project"])
def test_acl_matrix(baseline, second_identity, family, call, who):
    ctx, _project_id, path = baseline
    own = chat_tools.acting_user_id()
    fn = (
        ja.job_status
        if call == "view"
        else (
            lambda j, actor_id: ja.cancel_job(j, actor_id=actor_id, settle_seconds=0.5)
        )
    )
    started = []
    try:
        if who == "foreign_project":
            job, _ = _foreign_project_job(ctx, path, family)
            status, kind = FOREIGN_PROJECT_KIND[family]
            actor = own
        else:
            if family == "sq":
                job, _ = _queue(ctx, path)
            else:
                started.append(_start(ctx, family))
                job = started[0][0]
            actor = own if who == "own" else str(second_identity["user_id"])
            status, kind = (None, None) if who == "own" else (404, "job_not_found")
        if status is None:
            out = fn(job, actor_id=actor)
            view = out if call == "view" else out["job"]
            assert view["id"] == job
            if call == "cancel":
                assert out["cancel"] == "cancelled"
            return
        with pytest.raises(HTTPException) as refused:
            fn(job, actor_id=actor)
        assert refused.value.status_code == status
        assert refused.value.detail["error_kind"] == kind
        if started:
            assert not started[0][
                2
            ].is_set()  # nothing was sent to a job not the actor's
        if family == "sq":
            assert solve_queue.get_job(uuid.UUID(job))["status"] == "queued"
    finally:
        _stop_all(started)


# ── GK B A2: the ack reports what really happened ─────────────────────────


@pytest.mark.parametrize(
    "family,done", [("fg", "completed"), ("ad", "done"), ("rpt", "done")]
)
def test_a_job_that_succeeds_before_the_abort_lands_reads_succeeded(
    baseline, family, done
):
    ctx, *_ = baseline
    started = [_start(ctx, family, honours_stop=False)]
    job, thread, stop = started[0]

    def finish_first():
        # The worker ends on its own the moment the stop is asked for.
        stop.wait(2)
        with ctx.solver_state_lock:
            _record(ctx, family)["status"] = done

    racer = threading.Thread(target=finish_first, daemon=True)
    racer.start()
    # For fg the stop also flips the record to `aborted`; the worker then
    # finishes first, as a solve past its last iteration does.
    try:
        out = ja.cancel_job(job, actor_id=chat_tools.acting_user_id(), settle_seconds=2)
    finally:
        racer.join(2)
        _stop_all(started)
    assert out["cancel"] == "finished_first"
    assert (out["job"]["outcome"], out["job"]["status"]) == ("succeeded", done)


def test_the_settle_bound_is_a_named_constant_and_a_slow_worker_reads_cancel_requested(
    baseline, monkeypatch
):
    ctx, *_ = baseline
    monkeypatch.setattr(ja, "CANCEL_SETTLE_SECONDS", 0.1)
    started = [_start(ctx, "ad", honours_stop=False)]
    try:
        t0 = time.monotonic()
        out = ja.cancel_job(started[0][0], actor_id=chat_tools.acting_user_id())
        assert out["cancel"] == "cancel_requested" and time.monotonic() - t0 < 1.5
    finally:
        _stop_all(started)


# ── GK B A3: an unrecognised status fails closed ──────────────────────────


@pytest.mark.parametrize("family", ["fg", "ad", "rpt", "sq"])
def test_an_unrecognised_status_is_never_a_success(baseline, family):
    ctx, _project_id, path = baseline
    started = []
    try:
        if family == "sq":
            job, native = _queue(ctx, path)
            native.status = "half_done"
        else:
            started.append(_start(ctx, family))
            job = started[0][0]
            with ctx.solver_state_lock:
                _record(ctx, family)["status"] = "half_done"
        view = ja.job_status(job, actor_id=chat_tools.acting_user_id())
        assert view["status"] == "half_done"
        assert view["outcome"] == "unknown" and view["result_revision"] is None
        # Neither running nor finished: nothing proves either.
        assert view["terminal"] is False
    finally:
        _stop_all(started)


# ── GK B A4: the input revision is the one captured at start ──────────────


@pytest.mark.parametrize("family", ["fg", "ad", "rpt"])
def test_an_edit_mid_job_does_not_move_the_input_revision(baseline, family):
    ctx, *_ = baseline
    started = [_start(ctx, family)]
    try:
        at_start = ctx.network_revision
        ctx.network_revision += 3  # three edits while the job runs
        view = ja.job_status(started[0][0], actor_id=chat_tools.acting_user_id())
        assert view["input_revision"] == at_start
        assert view["input_revision_note"] == "network_revision at handoff"
    finally:
        _stop_all(started)


def test_a_queue_job_names_why_it_has_no_input_revision(baseline):
    ctx, _project_id, path = baseline
    job, _ = _queue(ctx, path)
    view = ja.job_status(job, actor_id=chat_tools.acting_user_id())
    assert (
        view["input_revision"] is None
        and "saved project file" in view["input_revision_note"]
    )


# ── GK B A5: wait_for_job outputs are unchanged ───────────────────────────

WAIT_KEYS = {
    "job",
    "terminal",
    "completed",
    "timed_out",
    "wait_cancelled",
    "waited_seconds",
    "note",
}
WAIT_JOB_KEYS = {
    "fg": {
        "id",
        "family",
        "status",
        "kind",
        "tool_name",
        "tool_use_id",
        "objective",
        "condition",
        "running",
        "monitor",
    },
    "ad": {
        "id",
        "family",
        "study_key",
        "status",
        "stage",
        "progress",
        "tool_name",
        "tool_use_id",
        "error",
        "running",
        "monitor",
    },
    "rpt": {
        "id",
        "family",
        "status",
        "report_id",
        "mode",
        "section",
        "version",
        "tool_name",
        "tool_use_id",
        "error",
        "running",
        "monitor",
    },
}


@pytest.mark.parametrize("family", ["fg", "ad", "rpt"])
def test_wait_for_job_keeps_its_shape(baseline, family):
    """The keys master's `_job_view` / `_study_job_view` / `_report_job_view` return."""
    ctx, *_ = baseline
    started = [_start(ctx, family)]
    try:
        out = chat_tools.wait_for_job(started[0][0], 0)
        assert set(out) - {"triage"} == WAIT_KEYS
        assert set(out["job"]) == WAIT_JOB_KEYS[family]
        assert out["timed_out"] and not out["terminal"]
    finally:
        _stop_all(started)


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


# ── Spec review: the ack is worker-verified ───────────────────────────────


def test_an_aborted_record_whose_worker_still_runs_is_only_cancel_requested(baseline):
    """routers.simulation.abort marks the record `aborted` at once; the solver may
    still be in native code. That is not yet `cancelled`."""
    ctx, *_ = baseline
    started = [_start(ctx, "fg", honours_stop=False)]
    try:
        out = ja.cancel_job(
            started[0][0], actor_id=chat_tools.acting_user_id(), settle_seconds=0.2
        )
        assert out["job"]["status"] == "aborted" and started[0][1].is_alive()
        assert out["cancel"] == "cancel_requested"
    finally:
        _stop_all(started)


def test_a_worker_that_dies_without_a_status_after_the_stop_is_origin_lost(baseline):
    ctx, *_ = baseline
    started = [_start(ctx, "ad", honours_stop=False)]
    job, thread, stop = started[0]

    def die_on_stop():
        stop.wait(2)
        thread.release.set()  # the worker exits, its record still says running

    threading.Thread(target=die_on_stop, daemon=True).start()
    try:
        out = ja.cancel_job(job, actor_id=chat_tools.acting_user_id(), settle_seconds=2)
        assert out["cancel"] == "origin_lost"
        assert out["job"]["outcome"] == "unknown" and out["job"]["terminal"] is False
    finally:
        _stop_all(started)


def test_a_queue_read_without_an_actor_is_job_not_found(baseline):
    ctx, _project_id, path = baseline
    job, _ = _queue(ctx, path)
    for call in (ja.job_status, ja.cancel_job):
        with pytest.raises(HTTPException) as refused:
            call(job, actor_id=None)
        assert refused.value.status_code == 404
        assert refused.value.detail["error_kind"] == "job_not_found"


# ── GK B A7: mutant pins ──────────────────────────────────────────────────


def test_a_study_worker_that_exits_just_after_flipping_is_cancelled_within_the_bound(
    baseline,
):
    """The status reads `aborted` before the worker leaves: the ack waits for the exit."""
    ctx, *_ = baseline
    owner = chat_tools.acting_user_id()
    stop, gone = threading.Event(), threading.Event()

    def run():
        stop.wait(5)
        with ctx.solver_state_lock:
            ctx.solver_state["fmea_sweep"]["status"] = "aborted"
        time.sleep(0.3)  # the closing restore

    worker = threading.Thread(target=run, daemon=True)
    with ctx.solver_state_lock:
        ctx.solver_state["fmea_sweep"] = {
            "status": "running",
            "thread": worker,
            "stop_event": stop,
            "started_at": time.time(),
            "stage": "sweep",
            "iterations": [],
        }
    worker.start()
    job = ja.register_study_job(
        study_key="fmea_sweep",
        tool_name="run_fmea_sweep",
        tool_use_id="tu",
        owner_user_id=owner,
        project_uuid=str(ctx.project_uuid),
    )
    try:
        out = ja.cancel_job(job, actor_id=owner, settle_seconds=3)
        assert out["cancel"] == "cancelled" and not worker.is_alive()
    finally:
        stop.set()
        gone.set()
        worker.join(2)


def test_a_record_with_no_worker_thread_is_verified_by_its_status(baseline):
    """A foreground record bound by `started_at` alone: the record is the only witness."""
    ctx, *_ = baseline
    owner = chat_tools.acting_user_id()
    with ctx.solver_state_lock:
        ctx.solver_state.update(
            status="running",
            thread=None,
            stop_event=threading.Event(),
            started_at=time.time(),
            kind="lopf",
        )
    job = ja.register_foreground_solve(
        tool_name="run_simulation",
        tool_use_id="tu",
        owner_user_id=owner,
        project_uuid=str(ctx.project_uuid),
        kind="lopf",
    )
    out = ja.cancel_job(job, actor_id=owner, settle_seconds=1)
    assert out["cancel"] == "cancelled" and out["job"]["status"] == "aborted"


def test_a_stopped_cancel_returns_at_once_as_cancel_requested(baseline):
    ctx, *_ = baseline
    started = [_start(ctx, "ad", honours_stop=False)]
    stop = threading.Event()
    stop.set()
    try:
        t0 = time.monotonic()
        out = ja.cancel_job(
            started[0][0],
            actor_id=chat_tools.acting_user_id(),
            settle_seconds=5,
            stop=stop,
        )
        assert out["cancel"] == "cancel_requested" and time.monotonic() - t0 < 2
    finally:
        _stop_all(started)


def test_an_unexpected_queue_abort_error_is_raised_not_swallowed(baseline, monkeypatch):
    ctx, _project_id, path = baseline
    job, _ = _queue(ctx, path)

    def boom(*_a):
        raise HTTPException(500, "solver host gone")

    monkeypatch.setattr(chat_tools, "solve_queue_abort", boom)
    with pytest.raises(HTTPException) as raised:
        ja.cancel_job(job, actor_id=chat_tools.acting_user_id(), settle_seconds=0)
    assert raised.value.status_code == 500


def test_an_unknown_actor_is_the_acting_refusal_not_a_silent_none(baseline):
    """`_queue_job` re-raises everything but the ACL's 404 (an unknown user is 401)."""
    ctx, _project_id, path = baseline
    job, _ = _queue(ctx, path)
    with pytest.raises(HTTPException) as refused:
        ja.job_status(job, actor_id=str(uuid.uuid4()))
    assert refused.value.status_code == 401


def test_a_handle_without_a_project_reads_on_any_project(baseline):
    """The mismatch guard needs both projects named; a handle minted with none reads."""
    ctx, *_ = baseline
    started = [_start(ctx, "ad")]
    try:
        ja._STUDIES[started[0][0]].project_uuid = None
        view = ja.job_status(started[0][0], actor_id=chat_tools.acting_user_id())
        assert view["status"] == "running" and view["project_id"] is None
    finally:
        _stop_all(started)


def test_a_context_without_a_project_does_not_refuse(baseline, monkeypatch):
    ctx, *_ = baseline
    started = [_start(ctx, "ad")]
    try:
        monkeypatch.setattr(ctx, "project_uuid", None)
        view = ja.job_status(started[0][0], actor_id=chat_tools.acting_user_id())
        assert view["status"] == "running"
    finally:
        _stop_all(started)


def test_warnings_carry_the_record_and_the_observer(baseline):
    from harness import completion_observer

    ctx, *_ = baseline
    owner = chat_tools.acting_user_id()
    started = [_start(ctx, "ad")]
    try:
        with ctx.solver_state_lock:
            ctx.solver_state["fmea_sweep"]["warnings"] = ["two contingencies skipped"]
        completion_observer.note_incomplete(owner, str(ctx.project_uuid))
        view = ja.job_status(started[0][0], actor_id=owner)
        assert view["warnings"] == [
            "two contingencies skipped",
            "activity_completion_observation_incomplete",
        ]
    finally:
        _stop_all(started)


def test_phase_and_finished_at_come_from_the_record(baseline):
    ctx, *_ = baseline
    actor = chat_tools.acting_user_id()
    started = [_start(ctx, family) for family in ("fg", "rpt")]
    try:
        fg, rpt = (ja.job_status(s[0], actor_id=actor) for s in started)
        assert (fg["phase"], rpt["phase"]) == ("lopf", "summary")
        with ctx.solver_state_lock:
            ctx.solver_state["report_job"].update(status="done", finished_at=123.0)
        assert ja.job_status(started[1][0], actor_id=actor)["finished_at"] == 123.0
    finally:
        _stop_all(started)


def test_finished_at_is_the_first_terminal_read(baseline):
    ctx, *_ = baseline
    actor = chat_tools.acting_user_id()
    started = [_start(ctx, "ad")]
    try:
        with ctx.solver_state_lock:
            ctx.solver_state["fmea_sweep"]["status"] = "done"
        first = ja.job_status(started[0][0], actor_id=actor)["finished_at"]
        time.sleep(0.01)
        with ctx.solver_state_lock:
            ctx.solver_state["fmea_sweep"]["stage"] = "restored"
        later = ja.job_status(started[0][0], actor_id=actor)
        assert later["cursor"] == 2 and later["finished_at"] == first
    finally:
        _stop_all(started)


@pytest.mark.parametrize("fingerprint", ["abc123", None])
def test_a_foreground_success_names_the_solve_fingerprint(baseline, fingerprint):
    ctx, *_ = baseline
    started = [_start(ctx, "fg")]
    try:
        ctx.network.meta.pop("assistant_solve_input_fingerprint", None)
        if fingerprint:
            ctx.network.meta["assistant_solve_input_fingerprint"] = fingerprint
        with ctx.solver_state_lock:
            ctx.solver_state["status"] = "completed"
        view = ja.job_status(started[0][0], actor_id=chat_tools.acting_user_id())
        assert view["result_revision"] == (
            f"solve:{fingerprint}" if fingerprint else None
        )
    finally:
        _stop_all(started)


@pytest.mark.parametrize("record_id,expected", [("rep-2", "rep-2"), (None, "rep-1")])
def test_a_report_success_names_the_record_report_first(baseline, record_id, expected):
    ctx, *_ = baseline
    started = [_start(ctx, "rpt")]
    try:
        with ctx.solver_state_lock:
            ctx.solver_state["report_job"].update(
                status="done", version=4, report_id=record_id
            )
        view = ja.job_status(started[0][0], actor_id=chat_tools.acting_user_id())
        assert view["result_revision"] == f"report:{expected}@4"
    finally:
        _stop_all(started)


def test_a_non_string_id_is_not_found(baseline):
    for job_id in (None, 7):
        with pytest.raises(HTTPException) as refused:
            ja.job_status(job_id, actor_id=chat_tools.acting_user_id())
        assert refused.value.detail["error_kind"] == "job_not_found"


def test_the_foreground_phase_is_the_records_kind_then_the_handles(baseline):
    ctx, *_ = baseline
    actor = chat_tools.acting_user_id()
    started = [_start(ctx, "fg")]
    try:
        with ctx.solver_state_lock:
            ctx.solver_state["kind"] = "ac_pf"
        assert ja.job_status(started[0][0], actor_id=actor)["phase"] == "ac_pf"
        with ctx.solver_state_lock:
            ctx.solver_state["kind"] = None
        assert ja.job_status(started[0][0], actor_id=actor)["phase"] == "lopf"
    finally:
        _stop_all(started)


@pytest.mark.parametrize("family", ["ad", "rpt"])
def test_a_verified_cancel_returns_without_waiting_out_the_bound(baseline, family):
    ctx, *_ = baseline
    started = [_start(ctx, family)]
    try:
        t0 = time.monotonic()
        out = ja.cancel_job(
            started[0][0], actor_id=chat_tools.acting_user_id(), settle_seconds=5
        )
        assert out["cancel"] == "cancelled" and time.monotonic() - t0 < 2
    finally:
        _stop_all(started)


def test_a_job_that_finished_first_returns_without_waiting_out_the_bound(baseline):
    ctx, *_ = baseline
    started = [_start(ctx, "ad", ends_as="done")]  # it completes as the stop lands
    try:
        t0 = time.monotonic()
        out = ja.cancel_job(
            started[0][0], actor_id=chat_tools.acting_user_id(), settle_seconds=5
        )
        assert (
            out["cancel"] == "finished_first" and out["job"]["outcome"] == "succeeded"
        )
        assert time.monotonic() - t0 < 2
    finally:
        _stop_all(started)


# ── GK B on #1543 ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "study_key", ["fmea_sweep", "mc", "investment_case", "business_case"]
)
def test_each_study_key_stops_its_own_record_only(baseline, study_key):
    ctx, *_ = baseline
    other = threading.Event()
    with ctx.solver_state_lock:
        ctx.solver_state["frontier"] = {"status": "running", "stop_event": other}
    started = [_start(ctx, "ad", study_key=study_key)]
    try:
        out = ja.cancel_job(
            started[0][0], actor_id=chat_tools.acting_user_id(), settle_seconds=2
        )
        assert out["cancel"] == "cancelled" and started[0][2].is_set()
        assert not other.is_set()
    finally:
        _stop_all(started)


@pytest.mark.parametrize("family", ["fg", "ad", "rpt"])
def test_f1_a_run_that_replaced_the_handle_between_read_and_stop_survives(
    baseline, monkeypatch, family
):
    """The interleave: the read sees this handle's run; before the stop a new run
    of the same family replaces the record. Nothing reaches the new run."""
    ctx, *_ = baseline
    started = [_start(ctx, family)]
    real = ja.job_status
    newer = []

    def read_then_replace(job_id, *, actor_id):
        view = real(job_id, actor_id=actor_id)
        if not newer:
            started[0][1].release.set()
            started[0][2].set()  # this run ends ...
            started[0][1].join(1)
            newer.append(_start(ctx, family))  # ... and a new one takes the record
        return view

    monkeypatch.setattr(ja, "job_status", read_then_replace)
    try:
        out = ja.cancel_job(
            started[0][0], actor_id=chat_tools.acting_user_id(), settle_seconds=1
        )
        assert out["cancel"] == "origin_lost"
        assert not newer[0][2].is_set()
        assert _record(ctx, family)["status"] == "running"
    finally:
        _stop_all(started + newer)


def test_f2_a_queue_job_the_actor_may_see_but_not_stop_is_refused(
    baseline, monkeypatch
):
    import routers.solve_queue as sq_router

    ctx, _project_id, path = baseline
    job_id, job = _queue(ctx, path)
    monkeypatch.setattr(sq_router, "_may_abort", lambda *_a: False)
    out = ja.cancel_job(job_id, actor_id=chat_tools.acting_user_id(), settle_seconds=1)
    assert out["cancel"] == "refused"
    assert out["refusal"]["status"] == 404
    assert out["refusal"]["error_kind"] == "abort_refused"
    assert out["job"]["status"] == "queued" and job.status == "queued"


def test_f2_a_refused_queue_abort_carries_its_kind(baseline, monkeypatch):
    ctx, _project_id, path = baseline
    job_id, _ = _queue(ctx, path)

    def refuse(_job_id):
        raise HTTPException(409, {"error_kind": "job_locked", "message": "held"})

    monkeypatch.setattr(chat_tools, "solve_queue_abort", refuse)
    out = ja.cancel_job(job_id, actor_id=chat_tools.acting_user_id(), settle_seconds=0)
    assert out["cancel"] == "refused"
    assert out["refusal"] == {
        "error_kind": "job_locked",
        "status": 409,
        "message": "held",
    }


def test_f2_a_run_without_a_stop_event_is_refused_not_requested(baseline):
    ctx, *_ = baseline
    started = [_start(ctx, "ad")]
    try:
        with ctx.solver_state_lock:
            ctx.solver_state["fmea_sweep"]["stop_event"] = None
        out = ja.cancel_job(
            started[0][0], actor_id=chat_tools.acting_user_id(), settle_seconds=0
        )
        assert out["cancel"] == "refused"
        assert out["refusal"]["error_kind"] == "not_stoppable"
        assert out["job"]["status"] == "running"
    finally:
        _stop_all(started)


@pytest.mark.parametrize("asked", [3600.0, float("inf"), float("nan")])
def test_f3_the_settle_wait_is_capped(baseline, monkeypatch, asked):
    ctx, *_ = baseline
    monkeypatch.setattr(ja, "CANCEL_SETTLE_MAX_SECONDS", 0.2)
    monkeypatch.setattr(ja, "CANCEL_SETTLE_SECONDS", 0.1)
    started = [_start(ctx, "ad", honours_stop=False)]
    try:
        t0 = time.monotonic()
        out = ja.cancel_job(
            started[0][0], actor_id=chat_tools.acting_user_id(), settle_seconds=asked
        )
        assert out["cancel"] == "cancel_requested" and time.monotonic() - t0 < 1.5
    finally:
        _stop_all(started)


@pytest.mark.parametrize("family", ["fg", "ad", "rpt"])
def test_f4_an_ownerless_handle_is_viewable_but_not_cancellable(baseline, family):
    ctx, *_ = baseline
    started = [_start(ctx, family)]
    try:
        store = {"fg": ja._FOREGROUND, "ad": ja._STUDIES, "rpt": ja._REPORTS}[family]
        store[started[0][0]].owner_user_id = None
        actor = chat_tools.acting_user_id()
        assert ja.job_status(started[0][0], actor_id=actor)["status"] == "running"
        with pytest.raises(HTTPException) as refused:
            ja.cancel_job(started[0][0], actor_id=actor, settle_seconds=0)
        assert refused.value.status_code == 409
        assert refused.value.detail["error_kind"] == "job_owner_unknown"
        assert not started[0][2].is_set()
    finally:
        _stop_all(started)
