# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""live-voice 16: a plan step whose job was stopped on purpose (native
`aborted`) settles `cancelled`, not `failed`
(`.scratch/live-voice/issues/16-cancelled-plan-step-status.md`).

C1 the settle mapping; C2 an old file's failed+aborted step still reads
failed; C3 every reader treats a cancelled step as a failed one; C4 the
campus-study reader; C5 the stored error text per path."""

# ruff: noqa: F811 -- imported pytest fixtures
import json
import time
import uuid

import pytest

from services import assistant_tasks as tasks, chat_tools
from tests.test_assistant_tasks_study_brief import brief, refused, session  # noqa: F401
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401

STOPPED = tasks.STOPPED_ERROR


# ── C1: the settle mapping, on plain dicts ──────────────────────────────────


def _waiting():
    step = {
        "id": "solve",
        "tool": "run_simulation",
        "status": "waiting",
        "job": {"job_id": "j1", "continuation": "armed"},
    }
    return {"task_id": "t", "status": "waiting_job", "steps": [step]}, step


@pytest.mark.parametrize(
    "native, status, result, want",
    [
        ("aborted", "failed", None, "cancelled"),
        ("failed", "failed", None, "failed"),
        ("unknown", "failed", None, "failed"),
        ("aborted", "uncertain", None, "uncertain"),
        ("completed", "completed", None, "completed"),
        (
            "aborted",
            "failed",
            {"cases": [{"status": "aborted"}, {"status": "aborted"}]},
            "cancelled",
        ),
        (
            "aborted",
            "failed",
            {"cases": [{"status": "failed"}, {"status": "aborted"}]},
            "failed",
        ),
        (
            "aborted",
            "failed",
            {"cases": [{"status": "completed"}, {"status": "aborted"}]},
            "failed",
        ),
        ("aborted", "failed", {"cases": []}, "failed"),  # no case is no proof of a stop
    ],
)
def test_c1_settle_maps_only_a_stop_to_cancelled(native, status, result, want):
    task, step = _waiting()
    tasks._settle(task, step, native, status, result)
    assert step["status"] == want
    assert step["job"]["outcome"] == native


def test_c1_a_cancelled_step_keeps_its_native_outcome_and_withdraws_its_continuation():
    task, step = _waiting()
    tasks._settle(task, step, "aborted", "failed", None)
    assert step["status"] == "cancelled"
    assert step["job"]["outcome"] == "aborted"
    assert step["job"]["continuation"] == "withdrawn"
    assert step["error"] == STOPPED
    assert task["status"] == "ready"  # the step is cancelled, never the plan


def test_c1_a_failed_step_keeps_its_old_error():
    task, step = _waiting()
    tasks._settle(task, step, "failed", "failed", None)
    assert step["error"] == "The solve ended failed; inspect its log before retrying."


# ── shared: a briefed solve step settled through the real task file ─────────


def _solve_plan(request_id):
    steps = [{"id": "solve", "tool": "run_simulation", "args": {}}]
    return chat_tools.start_task(
        title="Solve", request_id=request_id, steps=steps, brief=brief(steps)
    )


def _settle_solve(session, native="aborted", status="failed"):
    ticket = tasks.begin_step(session, "run_simulation", {})
    job_id = "fg:" + uuid.uuid4().hex
    path = tasks.await_job(ticket, job_id, session.session_id)
    return tasks.settle_job(path, ticket, job_id, native, status)


# ── C2: an old file's failed + aborted step still reads failed ───────────────


def test_c2_an_old_failed_step_with_an_aborted_job_reads_failed(session):
    task = _solve_plan("lv16-old")
    _settle_solve(session, "failed", "failed")
    path, saved = tasks._read(task["task_id"])
    step = saved["steps"][0]
    step["job"]["outcome"] = "aborted"  # as stored before issue 16
    step["error"] = "The solve ended aborted; inspect its log before retrying."
    path.write_text(json.dumps(saved))
    view = chat_tools.get_task(task["task_id"])
    assert view["next_step"]["status"] == "failed"
    assert view["next_step"]["correction"] == {
        "triage": list(tasks._TRIAGE),
        "then": "resolve_task_step(retry) after the user approves the fix",
    }
    assert view["next_step"]["correction"] != tasks.STOPPED_CORRECTION


# ── C3: each reader treats cancelled as failed ──────────────────────────────


def test_c3_a_cancelled_step_offers_the_stopped_correction(session):
    task = _solve_plan("lv16-correction")
    view = _settle_solve(session)
    assert view["next_step"]["status"] == "cancelled"
    assert view["next_step"]["error"] == STOPPED
    assert view["next_step"]["correction"] == tasks.STOPPED_CORRECTION
    assert (
        chat_tools.get_task(task["task_id"])["next_step"]["correction"]
        == tasks.STOPPED_CORRECTION
    )


def test_c3_a_cancelled_scene_step_carries_the_edit_rows():
    row = {
        "component_class": "Load",
        "name": "it",
        "field": "p_set",
        "label": "Active power",
    }
    receipt = {"says": "it p_set 3 → 2 MW", "rows": [row]}
    view = tasks._view(
        {
            "task_id": "t",
            "title": "Edit",
            "project_id": "p",
            "status": "ready",
            "steps": [
                {
                    "id": "e",
                    "tool": "update_component",
                    "status": "completed",
                    "result": {"_edit_receipt": receipt},
                },
                {
                    "id": "x",
                    "tool": tasks.SCENE_APPLY,
                    "status": "cancelled",
                    "args": {},
                },
            ],
        }
    )
    assert view["change_edits"] == [{"rows": [row]}]


def test_c3_retry_reopens_a_cancelled_step(session):
    task = _solve_plan("lv16-retry")
    _settle_solve(session)
    view = chat_tools.resolve_task_step(task["task_id"], "retry")
    assert (
        view["next_step"]["status"] == "pending" and view["next_step"]["retries"] == 1
    )
    stored = tasks._read(task["task_id"])[1]["steps"][0]
    assert stored["status"] == "pending" and "error" not in stored


def test_c3_a_cancelled_step_at_the_retry_limit_stops(session):
    task = _solve_plan("lv16-limit")
    for attempt in range(3):
        view = _settle_solve(session)
        if attempt < 2:
            chat_tools.resolve_task_step(task["task_id"], "retry")
    assert (
        view["next_step"]["status"] == "cancelled" and view["next_step"]["retries"] == 2
    )
    assert view["next_step"]["correction"] == {
        "stop": "retry_limit",
        "then": "explain what failed; the user decides: fix the inputs, or cancel_task",
    }
    path, _ = tasks._read(task["task_id"])
    before = path.read_bytes()
    refused(
        "task_step_retry_limit", chat_tools.resolve_task_step, task["task_id"], "retry"
    )
    assert path.read_bytes() == before


def test_c3_a_cancelled_effectful_step_needs_a_retry_before_it_runs(session):
    _solve_plan("lv16-gate")
    _settle_solve(session)
    refused("task_step_needs_retry", tasks.begin_step, session, "run_simulation", {})
    refused("task_step_needs_retry", tasks.admit, session, "run_simulation", {})


def test_c3_a_cancelled_study_step_fails_its_explore_case(tmp_path):
    from harness import run_explore

    folder = tmp_path / "project" / "assistant_tasks"
    folder.mkdir(parents=True)
    path = folder / "t-1.json"
    lease = {"lease_owner": "a", "lease_until": time.time() + 90}
    case = {
        "name": "bess",
        "engine": "study",
        "status": "running",
        "deadline_epoch": time.time() + 3600,
        "solves": {"reserved": 1},
    }
    step = {
        "id": "explore.1",
        "tool": "run_decision_study",
        "status": "cancelled",
        "error": STOPPED,
        "explore": {"case": "bess", "phase": "run"},
    }
    path.write_text(
        json.dumps(
            {
                "version": 3,
                "task_id": "t-1",
                "project_id": "p",
                "steps": [step],
                "run": {
                    "profile": "eh_site@1",
                    "stage": "explore",
                    "runner": lease,
                    "cases": [case],
                    "ledger": [],
                    "research": {"rows": []},
                },
            }
        )
    )
    assert run_explore.advance(path, boot="a", clock=time.time) is True
    saved = json.loads(path.read_text())
    assert saved["run"]["cases"][0]["status"] == "failed"
    assert (
        saved["run"]["cases"][0]["reason"] == f"run_decision_study cancelled: {STOPPED}"
    )


# ── C4: the campus-study reader ──────────────────────────────────────────────


def test_c4_a_cancelled_campus_step_reads_its_aborted_outcome():
    from services import run_actions, run_gates

    step = {
        "id": "solve.3",
        "tool": run_actions.CAMPUS,
        "native": run_actions.KIND,
        "status": "cancelled",
        "job": {"outcome": "aborted"},
    }
    env = run_gates.Env(
        task={"steps": [step], "run": {}}, ctx=None, project=None, project_dir=None
    )
    assert run_gates._dc_campus_study(env) == "aborted"


# ── C5: the stored error text per path ──────────────────────────────────────


def test_c5_a_queue_whose_jobs_were_all_aborted_settles_cancelled(
    session, baseline, monkeypatch
):
    from harness import continuation

    _, project_id, _ = baseline
    task = chat_tools.start_task(
        title="Queue",
        request_id="lv16-queue",
        steps=[
            {
                "id": "enq",
                "tool": "solve_queue_enqueue",
                "args": {"project_id": project_id},
            }
        ],
    )
    ticket = tasks.begin_step(
        session, "solve_queue_enqueue", {"project_id": project_id}
    )
    jobs = [
        {"job_id": "q1", "name": "A", "project_id": project_id},
        {"job_id": "q2", "name": "B", "project_id": project_id},
    ]
    path = tasks.await_job(ticket, None, session.session_id, jobs=jobs)
    monkeypatch.setattr(continuation, "_queue_job_status", lambda job: "aborted")
    assert continuation.queue_outcome(jobs)[:2] == ("failed", "aborted")
    continuation.settle_queue_from_record(path, ticket)
    step = tasks._read(task["task_id"])[1]["steps"][0]
    assert step["status"] == "cancelled"
    assert step["error"] == STOPPED
    assert step["job"]["outcome"] == "aborted"
    assert [c["status"] for c in step["job"]["cases"]] == ["aborted", "aborted"]


def test_c5_a_study_abort_settles_cancelled_with_the_studys_own_text(tmp_path):
    from harness import run_eh
    from services.run_profiles import STAGES
    from tests.test_harness_run_eh import record, ticket_task

    path, ticket = ticket_task(tmp_path)
    saved = json.loads(path.read_text())  # what the settle's own view reads
    saved.update(title="Run", brief={"objective": "x", "cards": [], "cases": []})
    saved["run"].update(mode="step", stages={s: {"status": "pending"} for s in STAGES})
    saved["run"]["stages"]["solve"]["status"] = "active"
    saved["steps"][0]["args"] = {}
    path.write_text(json.dumps(saved))
    aborted = dict(record("aborted"), error="stopped by the user")
    run_eh.settle(path, ticket, "ad:1", aborted, 1.0)
    step = json.loads(path.read_text())["steps"][0]
    assert step["status"] == "cancelled"
    assert step["job"]["outcome"] == "aborted"
    # run_eh overwrites the settle's neutral text with its own.
    assert step["error"] == "The study ended aborted: stopped by the user"
