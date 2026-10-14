"""
The one per-call dispatch path (MCP build step 3, `.scratch/harness/mcp-spec.md`
§Build order and B.1).

`loop.dispatch_tool_call` is the single entry a caller drives for one tool
call: the offered-tools allowlist (`tool_not_offered`), then
`_dispatch_real_tool_call` (handler lookup, checked-mode guard, validators,
the confirmation card with its approval records, the handler). The turn loop
calls it for every tool use; local MCP exposure (build step 4) will call the
same function, so an external caller cannot skip a step.

These tests pin the behaviour through that entry before and after the move:
the allowlist refusal, the card and its approval records (issue 18), the
Expert/Guided tiers, auto-approve by policy, and Q20 (an external tool that is
not read-only is carded in Expert too).
"""
from __future__ import annotations

import json
import pathlib
import sys
import threading
import time

import pytest

from harness import approvals, catalogue
from harness import confirm as harness_confirm
from services import chat_service, chat_tools

BACKEND = pathlib.Path(__file__).resolve().parents[1]
STUB = BACKEND / "smoke" / "mcp_stub_server.py"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    from harness import mcp_client
    from services import dirty_state
    chat_service._reset_sessions_for_tests()
    mcp_client.reset_for_tests()
    monkeypatch.setattr(dirty_state, "mark_dirty", lambda: None)
    monkeypatch.setattr(dirty_state, "bump_revision", lambda: None)
    yield
    mcp_client.reset_for_tests()
    chat_service._reset_sessions_for_tests()


@pytest.fixture
def probes(monkeypatch):
    """Declare three probe tools (read, write, destructive) like real ones:
    a catalogue entry with its Safety marker and a dispatcher. Returns the
    list of calls the handlers saw."""
    calls: list[str] = []
    entries = []
    for name, tier in (("probe_read", "read"), ("probe_write", "write"),
                       ("probe_wipe", "destructive")):
        monkeypatch.setitem(chat_tools.DISPATCHERS, name,
                            lambda _n=name, **kw: (calls.append(_n), {"ok": _n})[1])
        entries.append({"name": name, "description": f"Test probe. Safety: {tier}",
                        "input_schema": {"type": "object", "properties": {}, "required": []}})
    monkeypatch.setattr(catalogue, "TOOLS", [*catalogue.TOOLS, *entries])
    return calls


def _session():
    from harness.session import ChatSession
    return ChatSession(model="m", owner_user_id="u1")


def _drive(session, name, *, offered, guided=False, decision=None):
    """Drive one call through `dispatch_tool_call` on a thread; answer the
    card with `decision` if one appears. Returns (frames, collector)."""
    from harness.loop import dispatch_tool_call
    frames: list[tuple[str, dict]] = []
    collector: list[dict] = []

    def run():
        for frame in dispatch_tool_call(session, {"id": "tu_1", "name": name, "input": {}},
                                        collector, offered_tool_names=offered, guided=guided):
            frames.append(frame)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    deadline = time.monotonic() + 10
    while t.is_alive() and time.monotonic() < deadline:
        card = next((p for e, p in frames if e == "tool_pending_confirmation"), None)
        if card is not None and decision is not None:
            session.record_decision(card["confirmation_token"], decision)
            decision = None
        time.sleep(0.01)
    t.join(5)
    assert not t.is_alive(), f"dispatch did not finish: {frames}"
    return frames, collector


def _kinds(frames):
    return [e for e, _ in frames]


def _errors(frames):
    return [p["error_kind"] for e, p in frames if e == "tool_error"]


# ── the allowlist is the first step ────────────────────────────────────────

def test_an_unoffered_tool_is_refused_before_anything_else(probes):
    session = _session()
    frames, collector = _drive(session, "probe_wipe", offered={"probe_read"})
    assert _kinds(frames) == ["tool_error"] and _errors(frames) == ["tool_not_offered"]
    assert collector == [{"type": "tool_result", "tool_use_id": "tu_1", "is_error": True,
                          "content": "tool_not_offered"}]
    assert probes == [] and approvals.read(session) == []


def test_the_loop_refuses_an_unoffered_tool_with_the_same_frames(probes, tmp_projects_dir, install_network):
    import pypsa
    from harness.loop import _dispatch_tool_uses
    n = pypsa.Network()
    n.add("Bus", "B1")
    install_network(n, name="DispatchEntry")
    from services.pypsa_service import PyPSAService
    ctx = PyPSAService.get_active_context()
    collector: list[dict] = []
    gen = _dispatch_tool_uses(
        _session(), [{"id": "tu_1", "name": "probe_wipe", "input": {}}],
        tool_call_count=0, turn_ctx=ctx, turn_project_holder=[ctx.loaded_project],
        project_switched=lambda: False, tool_results_for_next_turn=collector,
        char_budget={"used": 0}, offered_tool_names={"probe_read"})
    frames = []
    try:
        while True:
            frames.append(next(gen))
    except StopIteration:
        pass
    direct, direct_collector = _drive(_session(), "probe_wipe", offered={"probe_read"})
    assert frames == direct and collector == direct_collector


# ── the card and its records (issue 18) ────────────────────────────────────

@pytest.mark.parametrize("decision, outcome, ran", [("approve", "approved", True),
                                                    ("deny", "rejected", False)])
def test_a_destructive_call_is_carded_and_recorded(probes, decision, outcome, ran):
    session = _session()
    frames, collector = _drive(session, "probe_wipe", offered={"probe_wipe"}, decision=decision)
    assert _kinds(frames)[:2] == ["tool_request", "tool_pending_confirmation"]
    assert (probes == ["probe_wipe"]) is ran
    asked, decided = approvals.read(session)
    assert asked["type"] == "approval/asked" and asked["tool"] == "probe_wipe"
    assert decided["outcome"] == outcome and decided["by"] == "user"
    if not ran:
        assert _errors(frames) == ["confirmation_denied"] and collector[0]["is_error"] is True


def test_policy_auto_approval_is_recorded_without_a_card(probes, monkeypatch):
    monkeypatch.setattr(harness_confirm, "AUTO_APPROVE_TIERS", frozenset({"destructive"}))
    session = _session()
    frames, _ = _drive(session, "probe_wipe", offered={"probe_wipe"})
    assert "tool_pending_confirmation" not in _kinds(frames)
    assert probes == ["probe_wipe"]
    [decided] = approvals.read(session)
    assert decided["outcome"] == "approved" and decided["by"] == "policy"


# ── the tiers per mode ─────────────────────────────────────────────────────

def test_a_built_in_write_runs_uncarded_in_expert(probes):
    session = _session()
    frames, _ = _drive(session, "probe_write", offered={"probe_write"})
    assert "tool_pending_confirmation" not in _kinds(frames) and probes == ["probe_write"]
    assert approvals.read(session) == []


def test_a_built_in_write_is_carded_in_guided(probes):
    session = _session()
    frames, _ = _drive(session, "probe_write", offered={"probe_write"}, guided=True, decision="approve")
    assert "tool_pending_confirmation" in _kinds(frames) and probes == ["probe_write"]


def test_a_read_is_never_carded(probes):
    session = _session()
    frames, collector = _drive(session, "probe_read", offered={"probe_read"}, guided=True)
    assert "tool_pending_confirmation" not in _kinds(frames) and probes == ["probe_read"]
    assert collector[0].get("is_error") is not True and approvals.read(session) == []


def test_q20_an_external_write_is_carded_in_expert(tmp_path, monkeypatch):
    from harness import mcp_client
    path = tmp_path / "mcp-servers.json"
    path.write_text(json.dumps({"servers": [{"id": "stub", "transport": "stdio",
                                             "command": sys.executable, "args": [str(STUB)]}]}))
    monkeypatch.setattr(mcp_client, "config_path", lambda: path)
    session = _session()
    offered = {t["name"] for t in mcp_client.session_tools(session)}
    frames, _ = _drive(session, "mcp__stub__set_value", offered=offered, decision="deny")
    card = next(p for e, p in frames if e == "tool_pending_confirmation")
    assert card["safety_tier"] == "write" and card["external"] == {"server": "stub"}
    assert _errors(frames) == ["confirmation_denied"]
