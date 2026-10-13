"""
Chat harness issue 18: confirmation decisions as durable, fail-closed events.

`approval/asked {call_id, tool, tier, token6}` is appended when a card is
issued; `approval/decided {call_id, tool, outcome, by}` when the card is
answered, expires, is aborted or revoked, or when policy approves a tier
without a card (`AUTO_APPROVE_TIERS`, no `asked`). Outcomes are the closed set
`approved | rejected | expired | aborted | revoked | unavailable`; anything but
`approved` denies. Arguments are never repeated (the tool call holds them).

Fail closed: an approval that cannot be put on record does not run the tool
(`approval_unavailable`); a card whose `asked` cannot be recorded is never
shown.
"""
from __future__ import annotations

import json
import os
import threading
import time

import pytest

from harness import confirm as harness_confirm
from harness import session as harness_session


def _session(owner="u1"):
    from harness.session import ChatSession
    return ChatSession(model="m", owner_user_id=owner)


def _gate(session, *, tier="destructive", tool_name="delete_project", args=None, guided=False):
    """Run the gate in a thread; return (frames, collector, result holder, thread)."""
    frames, collector, out = [], [], {}

    def run():
        gen = harness_confirm._confirm_destructive_tool(
            session, tool_use_id="tu-7", tool_name=tool_name, args=args or {"name": "P"},
            tier=tier, tool_results_collector=collector, guided=guided)
        try:
            while True:
                frames.append(next(gen))
        except StopIteration as stop:
            out["proceed"] = stop.value

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return frames, collector, out, t


def _wait_for_card(frames, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for event, payload in frames:
            if event == "tool_pending_confirmation":
                return payload
        time.sleep(0.01)
    raise AssertionError(f"no card: {frames}")


def _records(session):
    from harness import approvals
    return approvals.read(session)


# ── the user's decision ────────────────────────────────────────────────────

@pytest.mark.parametrize("decision,outcome", [("approve", "approved"), ("deny", "rejected")])
def test_a_card_records_asked_and_the_users_decision(decision, outcome):
    session = _session()
    frames, collector, out, t = _gate(session, args={"name": "secret-project"})
    card = _wait_for_card(frames)
    session.record_decision(card["confirmation_token"], decision)
    t.join(5)
    assert out["proceed"] is (decision == "approve")
    asked, decided = _records(session)
    assert asked["type"] == "approval/asked"
    assert asked["call_id"] == "tu-7" and asked["tool"] == "delete_project"
    assert asked["tier"] == "destructive"
    assert asked["token6"] == card["confirmation_token"][:6]
    assert decided == {**decided, "type": "approval/decided", "call_id": "tu-7",
                       "tool": "delete_project", "outcome": outcome, "by": "user"}
    # arguments are never repeated in the log
    assert "secret-project" not in json.dumps([asked, decided])


def test_an_expired_card_is_recorded_as_expired(monkeypatch):
    monkeypatch.setattr(harness_session, "CONFIRMATION_TTL_SECONDS", 0.2)
    session = _session()
    frames, collector, out, t = _gate(session)
    _wait_for_card(frames)
    t.join(5)
    assert out["proceed"] is False
    assert _records(session)[-1]["outcome"] == "expired"
    assert _records(session)[-1]["by"] == "ttl"


def test_an_aborted_turn_records_aborted():
    session = _session()
    frames, collector, out, t = _gate(session)
    _wait_for_card(frames)
    session.abort_event.set()
    t.join(5)
    assert out["proceed"] is False
    assert (_records(session)[-1]["outcome"], _records(session)[-1]["by"]) == ("aborted", "session")


def test_a_revoked_card_is_recorded_and_released_at_once():
    session = _session()
    frames, collector, out, t = _gate(session)
    _wait_for_card(frames)
    started = time.monotonic()
    session.revoke_confirmations()
    t.join(5)
    assert time.monotonic() - started < 2.0          # not left to time out
    assert out["proceed"] is False
    assert ("tool_error", {"tool_use_id": "tu-7", "tool_name": "delete_project",
            "error_kind": "confirmation_revoked",
            "message": "revoked on confirmation for 'delete_project'"}) in frames
    assert (_records(session)[-1]["outcome"], _records(session)[-1]["by"]) == ("revoked", "system")


def test_rebind_scope_revokes_open_cards(monkeypatch):
    """`durable_conversation.rebind_scope` used to clear open cards without a
    record, leaving the waiting turn to time out as 'expired'."""
    import inspect
    from services import durable_conversation

    src = inspect.getsource(durable_conversation.rebind_scope)
    assert "revoke_confirmations()" in src
    assert "pending_confirmations.clear()" not in src


# ── the policy path ────────────────────────────────────────────────────────

def test_an_auto_approved_tier_records_a_policy_decision_without_asked(monkeypatch):
    monkeypatch.setattr(harness_confirm, "AUTO_APPROVE_TIERS", frozenset({"execution"}))
    session = _session()
    frames, collector, out, t = _gate(session, tier="execution", tool_name="run_simulation")
    t.join(5)
    assert out["proceed"] is True and frames == []
    (decided,) = _records(session)
    assert (decided["type"], decided["outcome"], decided["by"]) == ("approval/decided", "approved", "policy")
    assert decided["call_id"] == "tu-7" and decided["tier"] == "execution"


def test_a_read_tier_tool_records_nothing():
    session = _session()
    frames, collector, out, t = _gate(session, tier="read", tool_name="list_projects")
    t.join(5)
    assert out["proceed"] is True and _records(session) == []


# ── fail closed ────────────────────────────────────────────────────────────

def _break_writes(monkeypatch):
    from harness import approvals

    def broken(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(approvals, "_append", broken)


def test_an_approval_that_cannot_be_recorded_does_not_run(monkeypatch):
    from harness import approvals

    session = _session()
    frames, collector, out, t = _gate(session)
    card = _wait_for_card(frames)
    _break_writes(monkeypatch)                     # asked was written; decided cannot be
    session.record_decision(card["confirmation_token"], "approve")
    t.join(5)
    assert out["proceed"] is False
    errors = [p for e, p in frames if e == "tool_error"]
    assert errors and errors[0]["error_kind"] == "approval_unavailable"
    assert collector[-1] == {"type": "tool_result", "tool_use_id": "tu-7",
                             "is_error": True, "content": "approval_unavailable"}


def test_a_card_whose_asked_cannot_be_recorded_is_never_shown(monkeypatch):
    _break_writes(monkeypatch)
    session = _session()
    frames, collector, out, t = _gate(session)
    t.join(5)
    assert out["proceed"] is False
    assert not any(e == "tool_pending_confirmation" for e, _ in frames)
    assert [p["error_kind"] for e, p in frames if e == "tool_error"] == ["approval_unavailable"]
    assert session.pending_confirmations == {}


def test_a_policy_approval_that_cannot_be_recorded_does_not_run(monkeypatch):
    monkeypatch.setattr(harness_confirm, "AUTO_APPROVE_TIERS", frozenset({"execution"}))
    _break_writes(monkeypatch)
    session = _session()
    frames, collector, out, t = _gate(session, tier="execution", tool_name="run_simulation")
    t.join(5)
    assert out["proceed"] is False
    assert [p["error_kind"] for e, p in frames if e == "tool_error"] == ["approval_unavailable"]


def test_a_denial_that_cannot_be_recorded_still_denies(monkeypatch):
    session = _session()
    frames, collector, out, t = _gate(session)
    card = _wait_for_card(frames)
    _break_writes(monkeypatch)
    session.record_decision(card["confirmation_token"], "deny")
    t.join(5)
    assert out["proceed"] is False
    assert [p["error_kind"] for e, p in frames if e == "tool_error"] == ["confirmation_denied"]


# ── the store ──────────────────────────────────────────────────────────────

def test_the_log_is_owner_and_session_bound_under_app_data():
    import app_paths
    from harness import approvals

    session = _session(owner="u1")
    frames, collector, out, t = _gate(session)
    card = _wait_for_card(frames)
    session.record_decision(card["confirmation_token"], "approve")
    t.join(5)
    path = approvals.path_for(session)
    assert app_paths.app_data_dir().resolve() in path.resolve().parents
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert oct(path.parent.stat().st_mode & 0o777) == "0o700"
    other = _session(owner="u2")
    assert approvals.read(other) == []
    assert all(json.loads(line)["v"] == 1 for line in path.read_text().splitlines())


def test_the_outcomes_are_a_closed_set():
    from harness import approvals

    assert approvals.OUTCOMES == frozenset(
        {"approved", "rejected", "expired", "aborted", "revoked", "unavailable"})
    with pytest.raises(ValueError):
        approvals.decided(_session(), call_id="x", tool="t", tier="destructive",
                          outcome="maybe", by="user")


def test_the_new_error_kinds_are_registered():
    path = os.path.join(os.path.dirname(__file__), "..", "..", "tool-error-kinds.json")
    kinds = json.load(open(path))["kinds"]
    assert "approval_unavailable" in kinds and "confirmation_revoked" in kinds


# ── links ──────────────────────────────────────────────────────────────────

def test_a_preview_confirmation_records_the_preview_id_not_the_diff():
    session = _session()
    args = {"preview_id": "3f2b1c4e-0000-4000-8000-000000000001", "project_id": "p",
            "changes_total": 1, "reviewed_changes": [{"component": "SECRET-DIFF"}]}
    frames, collector, out, t = _gate(session, tool_name="apply_project_changes", args=args)
    card = _wait_for_card(frames)
    session.record_decision(card["confirmation_token"], "approve")
    t.join(5)
    asked, decided = _records(session)
    assert decided["preview_id"] == args["preview_id"]
    assert "SECRET-DIFF" not in json.dumps([asked, decided])


def test_a_checked_turn_links_the_decision_to_its_operation():
    class _Turn:
        operation_contract = {"operation_id": "8c1d6a3e-1111-4222-8333-444455556666"}

    session = _session()
    session.assurance_turn = _Turn()
    frames, collector, out, t = _gate(session, tier="write", tool_name="update_component",
                                      guided=True)
    card = _wait_for_card(frames)
    session.record_decision(card["confirmation_token"], "approve")
    t.join(5)
    assert _records(session)[-1]["operation_id"] == "8c1d6a3e-1111-4222-8333-444455556666"
