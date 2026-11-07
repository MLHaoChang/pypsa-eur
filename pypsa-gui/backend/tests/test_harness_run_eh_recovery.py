"""Study harness 07 B3a (GK B (32) L4-L6, L9): reconciling an uncertain EH
step from its own study record, and recovering a waiting EH step whose
follower was lost (a dead thread, a takeover, a restart)."""

import json
import threading
import time
import types

import pytest

from harness import job_adapters, run_eh
from services import assistant_tasks, run_envelope
from services.pypsa_service import PyPSAService
from tests.test_harness_run_eh import (
    PROJECT,
    ctx_for,
    eh_row,
    record,
    settled,
    ticket_task,
)

INSTANCE = "i1"


def waiting(path, lease_owner=INSTANCE, lease_until=None, **job):
    """The ticket task's EH step as `run_eh.wait` leaves it, with the run's lease."""
    task = json.loads(path.read_text())
    task["steps"][0]["job"].update(
        kind=run_eh.STUDY, started_at=1.0, call_id="a1", **job
    )
    task["run"]["runner"] = {
        "lease_owner": lease_owner,
        "lease_until": time.time() + 600 if lease_until is None else lease_until,
    }
    path.write_text(json.dumps(task))
    return task


@pytest.fixture
def no_dispatch(monkeypatch):
    """L9: recovery never starts the study again (chat tool or service)."""
    from services import chat_tools
    from services.adequacy import eh_study

    def boom(*a, **k):
        raise AssertionError("the EH study was started again")

    monkeypatch.setattr(chat_tools, "run_eh_study", boom)
    monkeypatch.setattr(eh_study, "run_eh_study", boom)


# ── Item 2: reconcile from the step's own record (L4) ───────────────────────


def registered(
    monkeypatch, ctx, *, project=PROJECT, key=run_eh.STUDY, begun=1.0, ctx_id=None
):
    """The step's study handle, its activity binding, and `ctx` resident."""
    monkeypatch.setitem(
        job_adapters._STUDIES,
        "ad:1",
        types.SimpleNamespace(study_key=key, project_uuid=project),
    )
    monkeypatch.setitem(
        job_adapters._ACTIVITY_BINDINGS,
        "ad:1",
        (id(ctx) if ctx_id is None else ctx_id, key, None, begun),
    )
    monkeypatch.setattr(
        PyPSAService, "list_ids", classmethod(lambda cls: ["org:" + PROJECT])
    )
    monkeypatch.setattr(PyPSAService, "get_context", classmethod(lambda cls, k: ctx))


def uncertain(path):
    task = waiting(path)
    task["steps"][0]["status"] = "uncertain"
    path.write_text(json.dumps(task))
    return task


def test_its_own_done_record_reconciles_and_settles_the_reservation_once(
    tmp_path, monkeypatch
):
    path, _ = ticket_task(tmp_path)
    task = uncertain(path)
    ctx = ctx_for(tmp_path, eh_study=record())
    registered(monkeypatch, ctx)
    found, result = assistant_tasks._native_evidence(path, task, task["steps"][0])
    assert found and result == {
        "job_id": "ad:1",
        "final_status": "completed",
        "study": "eh_study",
        "started_at": 1.0,
        "solves": 1,
    }
    assert eh_row(path)["solves"] == 1
    assert (
        task["run"]["budget"]["used"]["solves"] == 2
    )  # the caller's task, which it saves
    # L4: a second reconcile and a late settle release nothing more.
    assert run_eh.evidence(path, task, task["steps"][0])[0]
    monkeypatch.setattr(assistant_tasks, "settle_job", lambda *a: None)
    run_eh.settle(
        path,
        ("t1", "solve.2", "a1"),
        "ad:1",
        record(
            stages=[
                {
                    "stage": "ens_solve",
                    "status": "run",
                    "note": None,
                    "solves_charged": 5,
                }
            ]
        ),
        1.0,
    )
    assert eh_row(path)["solves"] == 1
    assert run_envelope.solves_used(run_envelope._load(path)) == 2


@pytest.mark.parametrize(
    "rec",
    [
        record("failed"),
        record("running"),
        record(started=2.0),
        None,
        record(report=False),
        record(
            stages=[
                {
                    "stage": "mc",
                    "status": "skipped",
                    "solves_charged": 0,
                    "note": "not run: budget",
                }
            ]
        ),
    ],
    ids=["failed", "running", "replaced", "missing", "no_report", "skipped_stage"],
)
def test_only_a_completing_own_record_is_evidence(tmp_path, monkeypatch, rec):
    path, _ = ticket_task(tmp_path)
    task = uncertain(path)
    ctx = ctx_for(tmp_path, **({"eh_study": rec} if rec is not None else {}))
    registered(monkeypatch, ctx)
    assert assistant_tasks._native_evidence(path, task, task["steps"][0]) == (
        False,
        None,
    )
    assert "solves" not in eh_row(path)  # nothing released on a refusal


@pytest.mark.parametrize(
    "where",
    [
        "other_project_handle",
        "other_key",
        "other_started_at",
        "other_ctx",
        "no_handle",
        "ctx_not_resident",
        "ctx_of_another_project",
    ],
)
def test_the_match_is_on_project_ctx_job_and_started_at(tmp_path, monkeypatch, where):
    path, _ = ticket_task(tmp_path)
    task = uncertain(path)
    ctx = ctx_for(tmp_path, eh_study=record())
    registered(
        monkeypatch,
        ctx,
        project="22222222-2222-2222-2222-222222222222"
        if where == "other_project_handle"
        else PROJECT,
        key="investment_case" if where == "other_key" else run_eh.STUDY,
        begun=2.0 if where == "other_started_at" else 1.0,
        ctx_id=id(object()) if where == "other_ctx" else None,
    )
    if where == "no_handle":
        job_adapters._STUDIES.pop("ad:1")
    if where == "ctx_not_resident":
        monkeypatch.setattr(PyPSAService, "list_ids", classmethod(lambda cls: []))
    if where == "ctx_of_another_project":
        ctx.project_uuid = "22222222-2222-2222-2222-222222222222"
    assert assistant_tasks._native_evidence(path, task, task["steps"][0]) == (
        False,
        None,
    )


def test_an_eh_step_never_reconciles_from_the_solve_run_record(tmp_path, monkeypatch):
    """L9 mutant (the generic run_evidence fallback put back for eh_study):
    a completed solve run under the step's job id is no EH evidence."""
    from services import run_evidence

    path, _ = ticket_task(tmp_path)
    task = uncertain(path)
    monkeypatch.setattr(run_evidence, "foreground_run_id", lambda job_id: "r1")
    monkeypatch.setattr(
        run_evidence,
        "_load_run",
        lambda *a: {
            "status": "completed",
            "job_id": "ad:1",
            "metrics": {"objective": {"value": 1.0}},
            "fingerprint": "f",
        },
    )
    assert assistant_tasks._native_evidence(path, task, task["steps"][0]) == (
        False,
        None,
    )


def test_resolve_task_completes_the_step_from_its_own_record(tmp_path, monkeypatch):
    path, _ = ticket_task(tmp_path)
    uncertain(path)
    ctx = ctx_for(tmp_path, eh_study=record())
    registered(monkeypatch, ctx)
    monkeypatch.setattr(
        assistant_tasks, "_read", lambda task_id: (path, json.loads(path.read_text()))
    )
    monkeypatch.setattr(assistant_tasks, "_advance_run", lambda *a, **k: None)
    monkeypatch.setattr(assistant_tasks, "_view", lambda task, fresh=False: task)
    view = assistant_tasks.resolve_task_step("t1", "completed")
    step = view["steps"][0]
    assert step["status"] == "completed" and step["result"]["started_at"] == 1.0
    stored = json.loads(path.read_text())
    assert (
        stored["run"]["budget"]["used"]["solves"] == 2
    )  # not overwritten by the stale copy
    assert eh_row(path)["solves"] == 1


# ── Item 3: recovery (L5, L6) ───────────────────────────────────────────────


def test_a_dead_follower_is_settled_from_its_own_record(
    tmp_path, monkeypatch, no_dispatch
):
    path, ticket = ticket_task(tmp_path)
    waiting(path, deadline_at=time.time() + 600)
    calls = settled(monkeypatch)
    assert (
        run_eh.recover(path, ctx_for(tmp_path, eh_study=record()), instance=INSTANCE)
        == 1
    )
    assert calls[0][1:5] == (ticket, "ad:1", "done", "completed")
    assert eh_row(path)["solves"] == 1


def test_a_restart_settles_uncertain_and_keeps_the_whole_reservation(
    tmp_path, monkeypatch, no_dispatch
):
    """L9 mutant (a lost record re-dispatches the study): `no_dispatch`."""
    path, ticket = ticket_task(tmp_path)
    waiting(
        path,
        lease_owner="old-process",
        lease_until=time.time() - 1,
        deadline_at=time.time() + 600,
    )
    calls = settled(monkeypatch)
    assert run_eh.recover(path, None, instance=INSTANCE, lapsed_ok=True) == 1
    assert calls[0][3:5] == ("unknown", "uncertain")
    assert eh_row(path)["solves"] == 2


def test_the_boot_sweep_leaves_a_step_another_live_lease_holds(
    tmp_path, monkeypatch, no_dispatch
):
    path, _ = ticket_task(tmp_path)
    waiting(path, lease_owner="other", deadline_at=time.time() - 1)
    calls = settled(monkeypatch)
    assert run_eh.recover(path, None, instance=INSTANCE, lapsed_ok=True) == 0
    assert (
        run_eh.recover(path, ctx_for(tmp_path), instance=INSTANCE) == 0
    )  # a tick without the lease
    assert calls == [] and "solves" not in eh_row(path)


def test_past_its_deadline_the_step_is_uncertain_with_the_whole_reservation(
    tmp_path, monkeypatch, no_dispatch
):
    path, ticket = ticket_task(tmp_path)
    waiting(path, deadline_at=time.time() - 1)
    calls = settled(monkeypatch)
    # Even with its own record still running: the deadline is the bound.
    alive = threading.Event()
    worker = threading.Thread(target=alive.wait, daemon=True)
    worker.start()
    try:
        ctx = ctx_for(tmp_path, eh_study={**record("running"), "thread": worker})
        assert run_eh.recover(path, ctx, instance=INSTANCE) == 1
    finally:
        alive.set()
    assert calls[0][3:5] == ("timed_out", "uncertain")
    assert eh_row(path)["solves"] == 2 and not run_eh.following(ticket)


def test_a_step_stored_without_a_deadline_never_times_out(
    tmp_path, monkeypatch, no_dispatch
):
    """L6: an older step goes to its own record, never to `timed_out`."""
    path, _ = ticket_task(tmp_path)
    waiting(path)  # no deadline_at
    calls = settled(monkeypatch)
    assert (
        run_eh.recover(
            path,
            ctx_for(tmp_path, eh_study=record()),
            instance=INSTANCE,
            now=time.time() + 10 * run_eh.MAX_FOLLOW_SECONDS,
        )
        == 1
    )
    assert calls[0][3:5] == ("done", "completed")


def test_the_wait_records_a_deadline(tmp_path, monkeypatch):
    path, ticket = ticket_task(tmp_path)
    monkeypatch.setattr(
        assistant_tasks, "_read", lambda task_id: (path, json.loads(path.read_text()))
    )
    monkeypatch.setattr(assistant_tasks, "await_job", lambda *a: path)
    monkeypatch.setattr(run_eh, "_follow", lambda *a: None)
    before = time.time()
    assert run_eh.wait(ticket, "ad:1", "s1", ctx_for(tmp_path), 1.0)
    deadline = json.loads(path.read_text())["steps"][0]["job"]["deadline_at"]
    assert (
        before + run_eh.MAX_FOLLOW_SECONDS
        <= deadline
        <= time.time() + run_eh.MAX_FOLLOW_SECONDS
    )


def test_two_concurrent_recoveries_start_one_follower(
    tmp_path, monkeypatch, no_dispatch
):
    """L5 (and its L9 mutant, the lock removed): a tick and a takeover racing
    on one running step start exactly one follower."""
    path, ticket = ticket_task(tmp_path)
    waiting(path, deadline_at=time.time() + 600)
    settled(monkeypatch)
    alive = threading.Event()
    worker = threading.Thread(target=alive.wait, daemon=True)
    worker.start()
    ctx = ctx_for(tmp_path, eh_study={**record("running"), "thread": worker})
    real_record = run_eh._record

    def slow(c):
        time.sleep(0.05)  # widens the window between the check and the registration
        return real_record(c)

    monkeypatch.setattr(run_eh, "_record", slow)
    started = []
    real_start = run_eh._start_follower
    monkeypatch.setattr(
        run_eh, "_start_follower", lambda *a: started.append(a) or real_start(*a)
    )
    barrier = threading.Barrier(2)

    def race():
        barrier.wait()
        run_eh.recover(path, ctx, instance=INSTANCE)

    racers = [threading.Thread(target=race) for _ in range(2)]
    try:
        for r in racers:
            r.start()
        for r in racers:
            r.join(5)
        assert len(started) == 1 and run_eh.following(ticket)
    finally:
        with ctx.solver_state_lock:
            ctx.solver_state["eh_study"] = (
                record()
            )  # the study ends; the follower settles and exits
        alive.set()
        monkeypatch.setattr(run_eh._WAKE, "wait", lambda *a: None)
        run_eh._FOLLOWERS[ticket].join(5)
    assert not run_eh.following(ticket)


def test_a_live_follower_is_left_alone(tmp_path, monkeypatch, no_dispatch):
    path, ticket = ticket_task(tmp_path)
    waiting(path, deadline_at=time.time() - 1)
    calls = settled(monkeypatch)
    stop = threading.Event()
    follower = threading.Thread(target=stop.wait, daemon=True)
    follower.start()
    monkeypatch.setitem(run_eh._FOLLOWERS, ticket, follower)
    try:
        assert run_eh.recover(path, ctx_for(tmp_path), instance=INSTANCE) == 0
    finally:
        stop.set()
    assert calls == []


def test_a_lost_eh_step_is_the_runners_never_reconcile_losts(tmp_path):
    """`_reconcile_lost` leaves a waiting EH step to `run_eh.recover`, which
    settles its reservation too."""
    path, _ = ticket_task(tmp_path)
    task = waiting(path)
    assistant_tasks._reconcile_lost(path, task)
    assert json.loads(path.read_text())["steps"][0]["status"] == "waiting"
