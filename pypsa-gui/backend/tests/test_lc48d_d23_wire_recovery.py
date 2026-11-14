"""LC-48d, the LC-D23 recovery rows: history repair, approval resume, profile recovery, goal switch.

The seam is ``run_turn`` with the real ``AnthropicProvider`` over a mocked HTTP stream, as
in ``test_lc43_d23_wire_gates.py`` (whose helpers this reuses), plus the copilot's
workflow and task tools and the ``/confirm`` route function.

Owner decisions: LC48D-REPAIR-1 (repair before send), LC48D-APPROVAL-1 (410, ask again),
LC48D-GOAL-1 (refuse, name the active one).

Hand oracle on the baseline project (G1 20 MW):
- history repair: a stored assistant ``tool_use`` (id ``lost_1``) with no result is sent
  with a synthetic error result ``interrupted_tool_call`` first in the next user message
  (this turn's "go"); the stored history still has no result for ``lost_1``; G1 stays
  20 MW and ``lc43_gates.state`` is unchanged;
- approval resume: a card shown before a restart, approved after it (on a fresh session
  or through ``/confirm`` on the gone session), is 410 confirmation_lost_on_restart with
  the hint "ask again"; G1 stays 20 MW; a token this process never minted still reads
  404 (unknown_confirmation_token, unknown_session);
- profile recovery: after profile_switch_requires_new_chat, a new chat sends the turn
  and G1 reads 35 MW; after unknown_profile_id, resending on the same session with its
  own profile sends the turn and G1 reads 35 MW;
- goal switch: starting ``run-study`` while ``build-network`` is active is 409
  switch_in_progress naming it (step ``orient``) and the session keeps it; starting
  ``build-network`` again restarts it; after end_workflow ``run-study`` starts. Starting another task while one runs is 409
  switch_in_progress naming it (status running); a repeated start of the same plan is not
  refused; once the running task is cancelled the other starts.
"""

# ruff: noqa: F811 -- shared public harness fixtures
import pytest

from harness import session as harness_session
from routers import chat as chat_router
from services import assistant_tasks as tasks
from services import chat_service, chat_tools
from tests.lc43_gates import refused, state
from tests.test_lc43_d23_wire_gates import EDIT, _session, _turn, p_nom
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401

LOST = {"type": "tool_use", "id": "lost_1", "name": "update_component", "input": EDIT}


def _after(messages, tool_use_id):
    """The message right after the assistant message that called ``tool_use_id``."""
    for i, msg in enumerate(messages):
        content = msg.get("content")
        if (
            msg["role"] == "assistant"
            and isinstance(content, list)
            and any(b.get("id") == tool_use_id for b in content)
        ):
            return messages[i + 1] if i + 1 < len(messages) else None
    raise AssertionError(f"no assistant message calls {tool_use_id}")


def test_an_unpaired_tool_use_is_answered_before_send_and_history_is_kept(baseline):
    session = _session()
    session.append_history_message({"role": "user", "content": "raise G1"})
    session.append_history_message({"role": "assistant", "content": [LOST]})
    kept = state(baseline[2])

    frames, bodies = _turn(session, [])

    assert frames[-1][0] == "turn_done", frames[-3:]
    sent = _after(bodies[0]["messages"], "lost_1")
    assert sent["role"] == "user"
    assert sent["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "lost_1",
        "is_error": True,
        "content": "interrupted_tool_call",
    }
    assert any(
        b.get("type") == "text" and "go" in b.get("text", "") for b in sent["content"]
    )
    stored = _after(list(session.messages), "lost_1")
    assert not any(
        isinstance(b, dict) and b.get("tool_use_id") == "lost_1"
        for b in (stored["content"] if isinstance(stored["content"], list) else [])
    ), stored
    assert p_nom() == 20.0 and state(baseline[2]) == kept


def test_an_approval_lost_on_restart_is_410_ask_again(baseline, monkeypatch):
    monkeypatch.setattr(harness_session, "CONFIRMATION_TTL_SECONDS", 0.3)
    kept = state(baseline[2])
    before = _session()
    frames, _bodies = _turn(before, [("update_component", EDIT)], decision=None)
    (card,) = [d for e, d in frames if e == "tool_pending_confirmation"]
    token = card["confirmation_token"]
    assert len(token) == 32 and token.startswith(harness_session.BOOT_PREFIX)
    # The restart: a new process, with its own prefix and none of the old sessions.
    monkeypatch.setattr(
        harness_session, "BOOT_PREFIX", "0" * 8 if token[0] != "0" else "1" * 8
    )

    restarted = _session()
    refused(
        lambda: restarted.record_decision(token, "approve"),
        410,
        "confirmation_lost_on_restart",
    )
    gone = "f" * 32
    with pytest.raises(chat_router.HTTPException) as lost:
        chat_router.chat_confirm(
            gone,
            chat_router.ConfirmRequest(token=token, decision="approve"),
            actor=None,
        )
    assert lost.value.status_code == 410
    assert lost.value.detail["error_kind"] == "confirmation_lost_on_restart"
    assert lost.value.detail["hint"] == "ask again"

    ours = harness_session.BOOT_PREFIX + "a" * 24
    refused(
        lambda: restarted.record_decision(ours, "approve"),
        404,
        "unknown_confirmation_token",
    )
    refused(
        lambda: chat_router.chat_confirm(
            gone, chat_router.ConfirmRequest(token=ours, decision="approve"), actor=None
        ),
        404,
        "unknown_session",
    )
    assert p_nom() == 20.0 and state(baseline[2]) == kept


def test_after_a_refused_switch_a_new_chat_recovers(baseline):
    refused_session = _session()
    frames, bodies = _turn(
        refused_session, [("update_component", EDIT)], wire_conflict=True
    )
    assert (
        bodies == []
        and frames[0][1]["error_kind"] == "profile_switch_requires_new_chat"
    )
    assert p_nom() == 20.0

    frames, bodies = _turn(_session(), [("update_component", EDIT)])
    assert bodies and frames[-1][0] == "turn_done", frames[-3:]
    assert p_nom() == 35.0


def test_after_a_deleted_profile_picking_one_and_resending_recovers(baseline):
    session = _session()
    frames, bodies = _turn(
        session, [("update_component", EDIT)], unknown_profile_id="gone"
    )
    assert bodies == [] and frames[0][1]["error_kind"] == "unknown_profile_id"
    assert p_nom() == 20.0

    frames, bodies = _turn(session, [("update_component", EDIT)])
    assert bodies and frames[-1][0] == "turn_done", frames[-3:]
    assert p_nom() == 35.0


@pytest.fixture
def chat(baseline):
    session = chat_service.ChatSession()
    chat_tools.set_chat_session(session)
    return session


def test_a_workflow_switch_is_refused_naming_the_active_one(chat):
    chat_tools.start_workflow("build-network")
    refused(lambda: chat_tools.start_workflow("run-study"), 409, "switch_in_progress")
    with pytest.raises(chat_router.HTTPException) as switch:
        chat_tools.start_workflow("run-study")
    assert switch.value.detail["active"] == {
        "kind": "workflow",
        "id": "build-network",
        "step": "orient",
    }
    assert chat.workflow == {"id": "build-network", "step": "orient"}
    # The active workflow again is no switch: it restarts at its first step.
    assert chat_tools.start_workflow("build-network")["step"] == "orient"

    chat_tools.end_workflow()
    assert chat_tools.start_workflow("run-study")["workflow"] == "run-study"
    assert chat.workflow["id"] == "run-study"


WAIT = [{"id": "wait", "tool": "wait_for_job", "args": {"job_id": "job"}}]


def test_a_task_switch_is_refused_while_one_runs(chat):
    first = chat_tools.start_task(
        title="Wait for result", request_id="first", steps=WAIT
    )
    tasks.begin_step(chat, "wait_for_job", {"job_id": "job"})

    with pytest.raises(chat_router.HTTPException) as switch:
        chat_tools.start_task(title="Other", request_id="second", steps=WAIT)
    assert switch.value.status_code == 409
    assert switch.value.detail["error_kind"] == "switch_in_progress"
    assert switch.value.detail["active"] == {
        "kind": "task",
        "id": first["task_id"],
        "title": "Wait for result",
        "status": "running",
    }
    assert chat.task_id == first["task_id"]
    again = chat_tools.start_task(
        title="Wait for result", request_id="first", steps=WAIT
    )
    assert again["task_id"] == first["task_id"]

    tasks.cancel_task(first["task_id"])
    second = chat_tools.start_task(title="Other", request_id="second", steps=WAIT)
    assert chat.task_id == second["task_id"] != first["task_id"]
