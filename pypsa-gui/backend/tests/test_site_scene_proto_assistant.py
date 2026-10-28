"""
SC-00 N, D11 / V64 / V67 / V91: the bounded site context and a scripted
native journey through the REAL turn loop (`harness.loop.run_turn`) on the
existing FakeProvider. No paid call, no tool registered, no job enqueued.
"""
from __future__ import annotations

import json

import pytest

from harness.providers.fake import FakeProvider
from services import chat_service
from services.solve_queue import solve_queue
from tests.site_scene_proto import anchor, assistant_context as ac
from tests.site_scene_proto import flow_verdicts as fv

MESH_MARKER = "SC00N-MESH-VERTEX-BUFFER"


def _request_text(req) -> str:
    """Every text the provider sees: system blocks and message contents, unescaped."""
    texts = [b.get("text", "") for b in req.system_blocks]
    for m in req.messages:
        content = m.get("content")
        if isinstance(content, str):
            texts.append(content)
        else:
            texts += [b.get("text", "") if isinstance(b, dict) else str(b) for b in content or []]
    return "\n".join(texts) + json.dumps(req.messages, default=str)


def _tool_names(req) -> list[str]:
    return sorted(t["name"] for t in req.tools)


def _turn(message, *, ui_context=None, blocks=None):
    session = chat_service.ChatSession()
    provider = FakeProvider([{"blocks": blocks or [{"type": "text", "text": "ok"}]},
                             {"blocks": [{"type": "text", "text": "done"}]}])
    frames = list(chat_service.run_turn(session, message, provider=provider, ui_context=ui_context))
    return provider, frames


def _ctx(tmp_path):
    doc = anchor.read_anchor(tmp_path)
    state = dict(saved=True, can_edit=True, anchor_confirmed=anchor.admits_creation(doc), site="none")
    verdicts = fv.actions(**state, boundary=False, buildings=0, targets=0, native_runs=0, pending_proposal=False)
    ctx = {"project": "campus-demo", "site": None, "revision": doc["revision"],
           "anchor": "confirmed" if anchor.admits_creation(doc) else "missing", "part": "map",
           "entry": fv.entry(**state)["label"], "buildings": 0, "equipment": 0, "targets": 0}
    return ac.block(ctx, verdicts)


def test_the_block_is_closed_scalar_and_bounded(tmp_path):
    text = _ctx(tmp_path)
    assert len(text.encode()) <= ac.MAX_BLOCK_BYTES
    assert "entry: Create 3D site" in text and "open_site_creation: blocked site_anchor_missing" in text
    with pytest.raises(ValueError, match="closed"):
        ac.block({"mesh": "x"}, [])
    with pytest.raises(ValueError, match="scalar"):
        ac.block({"project": [[0, 0], [1, 1]]}, [])
    with pytest.raises(ValueError, match="scalar"):
        ac.block({"project": "x" * (ac.MAX_VALUE_CHARS + 1)}, [])


def test_the_cap_drops_whole_lines_and_says_so():
    verdicts = [{"action": f"action_{i:03d}", "available": False, "blocker": {"code": "c" * 60}} for i in range(40)]
    text = ac.block({"project": "p"}, verdicts)
    assert len(text.encode()) <= ac.MAX_BLOCK_BYTES
    kept = [line for line in text.splitlines() if line.startswith("- ")]
    assert all(line.endswith("c" * 60) for line in kept)  # no value cut mid-way
    assert f"omitted actions: {40 - len(kept)}" in text


def test_site_scene_ui_context_reaches_no_provider_today_and_adds_no_tool():
    # SC-07 wires the block; until then an unknown ui_context key is dropped by compose.
    base, _ = _turn("hello", ui_context={"panel": "horizon"})
    leaked, _ = _turn("hello", ui_context={"panel": "horizon", "site_scene": {"mesh": MESH_MARKER}})
    assert MESH_MARKER not in _request_text(leaked.requests[0])
    assert _tool_names(leaked.requests[0]) == _tool_names(base.requests[0])
    assert not [n for n in _tool_names(base.requests[0]) if "scene" in n]


def test_scripted_guided_site_setup_journey_is_read_only(tmp_path):
    # V91: the companion reads current native prerequisites and offers the next in-place task.
    solve_queue.reset_for_tests()
    try:
        block = _ctx(tmp_path)
        reply = ("Next: add site coordinates in Project & site, then Create 3D site. "
                 "Nothing runs until you apply a reviewed proposal.")
        provider, frames = _turn(f"{block}\n\nHelp me set up a 3D campus.", blocks=[{"type": "text", "text": reply}])
        assert len(provider.requests) == 1  # one scripted response, no tool round
        assert block in _request_text(provider.requests[0])
        assert MESH_MARKER not in _request_text(provider.requests[0])
        names = [e for e, _ in frames]
        assert not [n for n in names if n.startswith("tool")]
        assert solve_queue.list_jobs() == []  # V67: no worker, no solve, no charge
        assert anchor.read_anchor(tmp_path)["revision"] == 0  # no write
    finally:
        solve_queue.reset_for_tests()


def test_an_unknown_scene_tool_call_is_refused_not_executed(tmp_path):
    # Denial / unknown effect: a model asking for a scene apply tool that does not exist changes nothing.
    solve_queue.reset_for_tests()
    try:
        call = {"type": "tool_use", "id": "tu-1", "name": "site_scene_apply", "input": {"proposal_id": "p1"}}
        provider, frames = _turn("apply it", blocks=[call])
        results = [b for m in provider.requests[-1].messages for b in (m.get("content") or [])
                   if isinstance(b, dict) and b.get("type") == "tool_result"]
        assert results and all(r.get("is_error") for r in results)
        assert not [e for e, _ in frames if e == "tool_pending_confirmation"]
        assert solve_queue.list_jobs() == []
        assert not (tmp_path / "sites").exists() and anchor.read_anchor(tmp_path)["revision"] == 0
    finally:
        solve_queue.reset_for_tests()
