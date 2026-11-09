# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
# ruff: noqa: F811 -- imported pytest fixtures
"""SC-07 S5b amendments (GK B (37) 1a, 2a): the step's kind in the task view, and
cancel_task cancelling the previews its own completed prepare steps made."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from services import assistant_tasks as tasks, chat_tools
from services.pypsa_service import PyPSAService
from services.site_scene import chat
from tests.test_sc07_scene_tools import _save
from tests.test_sc07_task_step import (  # noqa: F401
    PREPARE,
    REFS,
    dispatch,
    geo,
    plan,
    prepared,
    refused,
    session,
    spy,
    step,
)


def _status(pid):
    with chat.scope() as (_db, _user, project, owner):
        return chat._proposal_status(project, owner, "campus", pid)["status"]


# ── 1a: next_step.error_kind ────────────────────────────────────────────────


def test_a_refused_apply_shows_its_stored_kind(session, monkeypatch):
    plan(request_id="kind")
    args = prepared(session, monkeypatch)
    ticket = tasks.begin_step(session, "site_scene_apply", args)
    tasks.finish_step(ticket, error="HTTPException", error_kind="proposal_expired")
    view = chat_tools.get_task(session.task_id)
    assert view["next_step"]["error_kind"] == "proposal_expired"


def test_a_step_with_no_kind_has_no_key(session, monkeypatch):
    plan(request_id="nokind")
    args = prepared(session, monkeypatch)
    ticket = tasks.begin_step(session, "site_scene_apply", args)
    tasks.finish_step(ticket, error="TimeoutError")
    view = chat_tools.get_task(session.task_id)
    assert view["next_step"]["error"] and "error_kind" not in view["next_step"]


# ── 2a: cancel_task cancels its own open previews ──────────────────────────


def test_a_denied_card_then_cancel_task_leaves_the_old_preview_unusable(
    session, monkeypatch, spy
):
    plan(request_id="deny")
    args = prepared(session, monkeypatch)
    _, results, asked = dispatch(session, monkeypatch, "site_scene_apply", args, "deny")
    assert asked and results[-1]["is_error"] and step(session)["status"] == "pending"
    assert _status(args["proposal_id"]) == "ready"
    view = chat_tools.cancel_task(session.task_id)
    assert view["status"] == "cancelled" and "previews_not_cancelled" not in view
    assert _status(args["proposal_id"]) == "cancelled"
    refused("proposal_cancelled", chat.apply, **args)
    assert spy == []


def test_another_tasks_preview_and_a_users_own_survive(session, monkeypatch):
    # The user's own preview, made outside any task, with the same owner.
    own = chat.prepare(**PREPARE)
    plan(request_id="first")
    first = prepared(session, monkeypatch)
    first_task = session.task_id
    session.task_id = None
    plan(request_id="second")
    second = prepared(session, monkeypatch)
    chat_tools.cancel_task(first_task)
    assert _status(first["proposal_id"]) == "cancelled"
    assert _status(second["proposal_id"]) == "ready"
    assert _status(own["apply_with"]["args"]["proposal_id"]) == "ready"


def test_an_applied_preview_of_the_task_stays_applied(session, monkeypatch):
    # A step after the apply keeps the task open once the apply lands.
    chat_tools.start_task(
        title="Generate, then read",
        request_id="applied",
        steps=[
            {"id": "prep", "tool": "site_scene_prepare", "args": PREPARE},
            {"id": "apply", "tool": "site_scene_apply", "args": REFS},
            {"id": "read", "tool": "site_scene_read", "args": {"site_id": "campus"}},
        ],
    )
    args = prepared(session, monkeypatch)
    _, results, _ = dispatch(session, monkeypatch, "site_scene_apply", args, "approve")
    assert not results[-1].get("is_error"), results[-1]
    assert _status(args["proposal_id"]) == "applied"
    assert chat_tools.cancel_task(session.task_id)["status"] == "cancelled"
    assert _status(args["proposal_id"]) == "applied"


def test_a_prepare_step_not_completed_is_not_cancelled(session, monkeypatch):
    plan(request_id="incomplete")
    args = prepared(session, monkeypatch)
    path, task = tasks._read(session.task_id)
    next(s for s in task["steps"] if s["id"] == "prep")["status"] = "uncertain"
    tasks._save(path, task)
    chat_tools.cancel_task(session.task_id)
    assert _status(args["proposal_id"]) == "ready"


def test_a_failing_preview_cancel_still_cancels_the_task_and_says_so(
    session, monkeypatch
):
    plan(request_id="raise")
    args = prepared(session, monkeypatch)

    def broken(*_a, **_k):
        raise RuntimeError("store unavailable")

    task_id = session.task_id  # cancel_task lets go of the session's task
    with monkeypatch.context() as patched:
        patched.setattr(chat, "cancel", broken)
        view = chat_tools.cancel_task(task_id)
    assert view["status"] == "cancelled"
    assert view["previews_not_cancelled"] == ["prep"]
    assert tasks._read(task_id)[1]["status"] == "cancelled"
    assert _status(args["proposal_id"]) == "ready"  # not cancelled, and said so above


@pytest.mark.parametrize("answer", ["applying", "not_found"])
def test_a_preview_cancel_answered_but_not_cancelled_is_said(
    session, monkeypatch, answer
):
    """GK B F1: an apply in flight (`applying`, which a failed apply puts back to `ready`) or a
    lookup that missed (`not_found`) returns without raising, and the preview is not cancelled."""
    plan(request_id=f"answer-{answer}")
    prepared(session, monkeypatch)
    task_id = session.task_id

    def answered(_site_id, proposal_id, **_k):
        return {"proposal_id": proposal_id, "status": answer, "changed": False}

    with monkeypatch.context() as patched:
        patched.setattr(chat, "cancel", answered)
        view = chat_tools.cancel_task(task_id)
    assert view["status"] == "cancelled"
    assert view["previews_not_cancelled"] == ["prep"]


def test_a_run_bound_cancel_reaches_the_tasks_own_project(
    session, monkeypatch, client, install_network, project_row
):
    """GK B F1: under a run context bound to the task's project while another project is in the
    foreground, the preview is cancelled in the task's project, not looked up in the other one."""
    plan(request_id="bound")
    args = prepared(session, monkeypatch)
    task_id = session.task_id
    geo = PyPSAService.get_active_context()
    bound = SimpleNamespace(
        loaded_project=geo.loaded_project, project_uuid=geo.project_uuid
    )
    _save(client, install_network, "other")
    other = SimpleNamespace(
        loaded_project="other", project_uuid=str(project_row("other").id)
    )
    token = tasks.RUN_CONTEXT.set(bound)
    try:
        with monkeypatch.context() as patched:
            patched.setattr(
                PyPSAService, "get_active_context", classmethod(lambda _cls: other)
            )
            view = chat_tools.cancel_task(task_id)
    finally:
        tasks.RUN_CONTEXT.reset(token)
    assert view["status"] == "cancelled" and "previews_not_cancelled" not in view
    with chat.scope(project_uuid=bound.project_uuid) as (_db, _user, project, owner):
        status = chat._proposal_status(project, owner, "campus", args["proposal_id"])
    assert status["status"] == "cancelled"


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "not a dict",
        {},
        {"apply_with": {"args": ["campus", "p1"]}},
        {"apply_with": {"args": {"site_id": 1}}},
        {"apply_with": {"args": {"site_id": 1, "proposal_id": "p1"}}},
        {"apply_with": {"args": {"site_id": "campus", "proposal_id": 1}}},
    ],
)
def test_a_prepare_result_without_refs_is_skipped(bad):
    task = {
        "steps": [
            {
                "id": "p",
                "tool": "site_scene_prepare",
                "status": "completed",
                "result": bad,
            }
        ]
    }
    assert tasks._own_previews(task) == []


def test_only_prepare_steps_name_previews_to_cancel():
    refs = {"apply_with": {"args": {"site_id": "campus", "proposal_id": "p1"}}}
    task = {
        "steps": [
            {
                "id": "a",
                "tool": "site_scene_apply",
                "status": "completed",
                "result": refs,
            },
            {
                "id": "p",
                "tool": "site_scene_prepare",
                "status": "completed",
                "result": refs,
            },
        ]
    }
    assert tasks._own_previews(task) == [("p", "campus", "p1")]


# ── UX (14) ask 2: the edit's receipt rows in the task view ────────────────


def _edited(receipt, next_tool="site_scene_apply"):
    return {
        "task_id": "t",
        "title": "Edit",
        "project_id": "p",
        "status": "completed",
        "steps": [
            {
                "id": "e",
                "tool": "update_component",
                "status": "completed",
                "result": {"_edit_receipt": receipt},
            },
            {
                "id": "x",
                "tool": next_tool,
                "status": "failed",
                "args": {},
                "result": {"_edit_receipt": receipt},
            },
        ],
    }


def test_a_task_view_carries_each_edits_rows_beside_its_words():
    row = {
        "component_class": "Load",
        "name": "it",
        "field": "p_set",
        "label": "Active power",
        "role": "typed",
        "unit": "MW",
    }
    view = tasks._view(
        _edited({"says": "it p_set 3 → 2 MW", "rows": [row], "digest": "d"})
    )
    assert view["changes"] == ["it p_set 3 → 2 MW"]
    assert view["change_edits"] == [
        {"rows": [row]}
    ]  # completed steps only, rows and sets only


def test_a_large_receipt_leaves_its_rows_out_and_keeps_its_words():
    rows = [{"field": "p_set", "label": "x" * 50, "role": "typed", "unit": "MW"}] * 60
    view = tasks._view(_edited({"says": "many", "rows": rows}))
    assert view["changes"] == ["many"]
    assert "change_edits" not in view


@pytest.mark.parametrize("next_tool", ["update_component", "run_simulation"])
def test_rows_ride_only_on_a_task_stopped_at_a_scene_step(next_tool):
    """Only the partial-mapping line reads the rows, so every other `_task` a tool result carries
    stays as before: the rows would push a result past the 4000-char cap into a spill (oj07)."""
    row = {
        "component_class": "Load",
        "name": "it",
        "field": "p_set",
        "label": "Active power",
    }
    view = tasks._view(_edited({"says": "it p_set 3 → 2 MW", "rows": [row]}, next_tool))
    assert view["changes"] == ["it p_set 3 → 2 MW"]
    assert "change_edits" not in view
