# ruff: noqa: F401, F811 -- imported shared fixtures.
"""Study harness issue 07 B3b: the eh_site pilot end to end, and the three
defects driving it found (GK B (33)).

- The plan-review grant runs inside the host's chat turn, whose turn cell
  outranks a request binding: the hand-over binds its own cell, so the new
  project's task and envelope are fingerprinted on the new project and the
  host's profiles and solver config are never reset by its setup.
- The hand-over saves the created project once, so the run's first solve
  write-back leaves its input fingerprint as it was (a template ships no
  user_ts; the save backs the network's series into it).
- The run's own solve job is its queued solve step's, never the solve
  stage's EH study's.
"""

from __future__ import annotations

import dataclasses
import json
import uuid

import pandas as pd

from db import session as db_session
from db.models import Project
from harness import run_loops
from services import (
    project_registry,
    run_envelope,
    run_gates,
    run_handover,
    run_profiles,
    workflow_tools,
)
from services.pypsa_service import PyPSAService
from services.study.runner import base_context
from tests.test_assistant_tasks_study_brief import baseline, helpers_state, session
from tests.test_harness_envelope import profile
from tests.test_harness_run_skeleton import site
from tests.test_harness_runner import fake_report_provider, task_path
from tests.test_run_intake_grant import _new_task, _start, host


def _in_a_chat_turn(ctx):
    """The host's chat turn, as `routers/chat.py` binds it: the request
    context and the turn cell that outranks any later request binding."""
    token = PyPSAService.bind_request_context(ctx)
    cell = PyPSAService.bind_turn_cell()

    def leave():
        PyPSAService._turn_cell.reset(cell)
        PyPSAService.reset_request_context(token)

    return leave


def _grant(host, session, site, name):
    view = _start(host, name)
    run_envelope.decide(
        session,
        view["task_id"],
        "unattended",
        intake_digest=view["run"]["intake"]["digest"],
    )
    intent = run_handover.read_intent(task_path(site[1], view))
    path, task = _new_task(intent)
    with db_session.SessionLocal() as db:
        row = db.get(Project, uuid.UUID(intent["project_id"]))
    return path, task, base_context(row), row


# ── The hand-over binds the new project, never the host ─────────────────────


def test_a_grant_inside_the_hosts_chat_turn_fingerprints_the_new_project_and_leaves_the_host(
    host, session, site
):
    front = PyPSAService.get_active_context()
    front.user_ts[("loads", "p_set", "L1")] = pd.Series(
        [1.0, 2.0], index=front.network.snapshots[:2]
    )
    front.solver_state["solver_config"] = dataclasses.replace(
        front.solver_state["solver_config"], voll=1234.0
    )
    before = workflow_tools._network_input_fingerprint(front)
    leave = _in_a_chat_turn(front)
    try:
        path, task, ctx, _ = _grant(host, session, site, f"Pilot {uuid.uuid4().hex[:6]}")
    finally:
        leave()
    # The host keeps its profiles and its config: the new project's setup
    # reset only its own.
    assert list(front.user_ts) == [("loads", "p_set", "L1")]
    assert front.solver_state["solver_config"].voll == 1234.0
    assert workflow_tools._network_input_fingerprint(front) == before
    # The run and its grant start from the new project's inputs.
    new = workflow_tools._network_input_fingerprint(ctx)
    envelope = json.loads(run_envelope.path_for(path).read_text())
    assert new != before
    assert task["fingerprint"] == envelope["fingerprint"] == new
    assert envelope["chain"][-1]["fingerprint"] == new


def test_the_created_project_is_saved_so_a_later_save_keeps_its_fingerprint(
    host, session, site
):
    """A solve write-back is a save with `persist_user_ts`: it must not move
    the inputs the run's grant and brief were fingerprinted on."""
    from routers import projects as project_routes

    leave = _in_a_chat_turn(PyPSAService.get_active_context())
    try:
        path, task, ctx, row = _grant(host, session, site, f"Pilot {uuid.uuid4().hex[:6]}")
    finally:
        leave()
    assert ctx.user_ts  # the template's series, as a first save stores them
    granted = task["fingerprint"]
    token = PyPSAService.bind_request_context(ctx)
    try:
        project_routes._save_context(
            ctx,
            row.name,
            expect=row.name,
            persist_user_ts=True,
            storage_dir=project_registry.project_dir(row),
        )
    finally:
        PyPSAService.reset_request_context(token)
    assert workflow_tools._network_input_fingerprint(ctx) == granted


# ── The run's own solve job is its queued solve's ───────────────────────────


def _two_solve_steps():
    return {
        "task_id": "t",
        "steps": [
            {"id": "solve.1", "tool": "solve_queue_enqueue", "job": {"job_id": "q-1"}},
            {"id": "solve.2", "tool": "run_eh_study", "job": {"job_id": "ad:eh-1"}},
        ],
    }


def test_the_l2_history_names_the_queued_solve_never_the_eh_study():
    assert run_loops.job_of(_two_solve_steps()) == "q-1"


def test_the_solve_facts_read_the_queued_solve_never_the_eh_studys_job(monkeypatch):
    from routers import solve_queue

    asked = []

    def visible(db, user, job_id):
        asked.append(job_id)
        return {"job_id": job_id, "status": "completed", "condition": "optimal", "project_key": "k"}

    monkeypatch.setattr(solve_queue, "_visible_job_or_404", visible)
    monkeypatch.setattr(project_registry, "registry_key", lambda row: "k")
    env = run_gates.Env(task=_two_solve_steps(), ctx=None, project=object(), project_dir=None)
    assert run_gates._solve_state(env) == "completed"
    assert asked == ["q-1"]
