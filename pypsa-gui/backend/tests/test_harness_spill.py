"""
Chat harness issue 16: an oversized tool result is kept whole and the model
gets a preview plus a locator it can page with `read_result`, instead of a
cut it cannot recover from.

Re-scoped 2026-10-12 against master: never during a checked turn (the
assurance turn projects results itself), never for a tool that pages by
itself, `read_result` is a control tool so every toolset view keeps it, and
storage follows the owner-scoped app-data pattern (`harness/durable`).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


BIG = [{"bus": f"B{i}", "hour": i % 24, "p": i * 0.5} for i in range(4000)]


def _call(name, args, tid="tu_big"):
    return {"blocks": [{"type": "tool_use", "id": tid, "name": name, "input": args}],
            "usage": {"input_tokens": 1, "output_tokens": 1}}


def _close():
    return {"blocks": [{"type": "text", "text": "done"}],
            "usage": {"input_tokens": 1, "output_tokens": 1}}


@pytest.fixture
def big_tool(monkeypatch):
    from services import chat_tools
    monkeypatch.setitem(chat_tools.DISPATCHERS, "list_projects", lambda **kw: {"rows": BIG})


def _session(owner="u1"):
    from harness import loop
    return loop.ChatSession(model="m", owner_user_id=owner)


def _content(fake, i):
    """The tool_result content the model received on request i."""
    msg = fake.requests[i].messages[-1]
    return "".join(b["content"] for b in msg["content"] if b.get("type") == "tool_result")


def _spill_from(content: str) -> dict:
    body = content.split("\n", 1)[1].rsplit("\n", 1)[0]   # inside the untrusted fence
    return json.loads(body)["spill"]


# ── spilling ────────────────────────────────────────────────────────────────

def test_an_oversized_result_is_kept_whole_and_the_model_gets_a_locator(big_tool):
    from harness import loop, spill
    from harness.providers.fake import FakeProvider

    session = _session()
    fake = FakeProvider([_call("list_projects", {}), _close()])
    frames = list(loop.run_turn(session, "list", provider=fake))
    content = _content(fake, 1)
    assert "RESULT TRUNCATED" not in content
    info = _spill_from(content)
    assert info["read_with"] == "read_result"
    assert info["total_chars"] == len(json.dumps({"rows": BIG}, default=str))
    assert info["shape"].startswith("dict with keys: rows")
    assert len(content) < 4500                       # the preview fits the cap
    # the whole result is on disk, owner- and session-bound, mode 0700
    path = spill.path_for(session, info["locator"])
    assert json.loads(path.read_text()) == {"rows": BIG}
    assert oct(path.parent.stat().st_mode & 0o777) == "0o700"
    # the panel's frame is unchanged by spilling
    result = dict(frames)["tool_result"]["result"]
    assert "spill" not in json.dumps(result)


def test_a_small_result_is_not_spilled(monkeypatch):
    from harness import loop
    from harness.providers.fake import FakeProvider
    from services import chat_tools

    monkeypatch.setitem(chat_tools.DISPATCHERS, "list_projects", lambda **kw: {"rows": BIG[:3]})
    session = _session()
    fake = FakeProvider([_call("list_projects", {}), _close()])
    list(loop.run_turn(session, "list", provider=fake))
    assert '"spill"' not in _content(fake, 1)


def test_a_tool_that_pages_itself_is_never_spilled_and_the_marker_names_it(monkeypatch):
    from harness import spill
    big = {"rows": BIG}
    out = spill.shape_for_model(_session(), "get_study_evidence", "tu1", big)
    assert out is big                                     # untouched: the cut applies
    from harness.results import _truncation_marker
    assert "get_study_evidence" in spill.paging_hint("get_study_evidence")
    assert "get_study_evidence" not in _truncation_marker(10, 5)


def test_a_checked_turn_never_spills():
    from harness import spill
    session = _session()
    session.assurance_turn = object()
    big = {"rows": BIG}
    assert spill.shape_for_model(session, "list_projects", "tu1", big) is big


# ── reading it back ─────────────────────────────────────────────────────────

def _spilled(session):
    from harness import spill
    out = spill.shape_for_model(session, "list_projects", "tu1", {"rows": BIG, "unit": "MW"})
    return out["spill"]["locator"]


def _read(session, **kw):
    from services import chat_tools
    chat_tools.set_chat_session(session)
    return chat_tools.read_result(**kw)


def test_read_result_pages_a_list_by_pointer():
    session = _session()
    loc = _spilled(session)
    page = _read(session, locator=loc, pointer="/rows", offset=10, limit=5)
    assert page["items"] == BIG[10:15]
    assert page["total"] == 4000 and page["next_offset"] == 15
    last = _read(session, locator=loc, pointer="/rows", offset=3998, limit=5)
    assert last["items"] == BIG[3998:] and last["next_offset"] is None
    assert _read(session, locator=loc, pointer="/unit")["value"] == "MW"


def test_read_result_describes_a_large_object_instead_of_returning_it():
    session = _session()
    loc = _spilled(session)
    top = _read(session, locator=loc)
    assert top["keys"] == ["rows", "unit"]
    assert top["shape"]["rows"].startswith("list[4000]")


def test_a_page_is_shrunk_to_fit_the_result_cap():
    session = _session()
    loc = _spilled(session)
    page = _read(session, locator=loc, pointer="/rows", offset=0, limit=10_000)
    assert len(json.dumps(page)) <= 3500
    assert page["next_offset"] == len(page["items"])


@pytest.mark.parametrize("locator", ["../../etc/passwd", "a" * 32 + "/x", "", "ZZZ", None])
def test_a_bad_locator_is_invalid_tool_args(locator):
    from fastapi import HTTPException
    session = _session()
    with pytest.raises(HTTPException) as e:
        _read(session, locator=locator)
    assert e.value.detail["error_kind"] == "invalid_tool_args"


def test_a_locator_is_bound_to_its_owner_and_session():
    from fastapi import HTTPException
    first = _session(owner="u1")
    loc = _spilled(first)
    for other in (_session(owner="u2"), _session(owner="u1")):   # another owner; another session
        with pytest.raises(HTTPException) as e:
            _read(other, locator=loc)
        assert e.value.detail["error_kind"] == "result_unavailable"


def test_read_result_is_refused_in_a_checked_turn():
    from fastapi import HTTPException
    session = _session()
    loc = _spilled(session)
    session.assurance_turn = object()
    with pytest.raises(HTTPException) as e:
        _read(session, locator=loc)
    assert e.value.detail["error_kind"] == "result_unavailable"


def test_a_bad_pointer_is_invalid_tool_args():
    from fastapi import HTTPException
    session = _session()
    loc = _spilled(session)
    for pointer in ("rows", "/nope", "/rows/x", "/rows/99999"):
        with pytest.raises(HTTPException) as e:
            _read(session, locator=loc, pointer=pointer)
        assert e.value.detail["error_kind"] == "invalid_tool_args", pointer


# ── bounds and lifecycle ────────────────────────────────────────────────────

def test_the_per_session_cap_evicts_the_oldest_first(monkeypatch):
    from harness import spill
    session = _session()
    one = len(json.dumps({"rows": BIG}, default=str))
    monkeypatch.setattr(spill, "SESSION_CAP_BYTES", int(one * 2.5))
    locs = []
    for i in range(3):
        out = spill.shape_for_model(session, "list_projects", f"tu{i}", {"rows": BIG})
        locs.append(out["spill"]["locator"])
        os.utime(spill.path_for(session, locs[-1]), (1_000_000 + i, 1_000_000 + i))
    assert not spill.path_for(session, locs[0]).exists()
    assert spill.path_for(session, locs[1]).exists() and spill.path_for(session, locs[2]).exists()


def test_a_result_larger_than_the_item_cap_is_not_spilled(monkeypatch):
    from harness import spill
    monkeypatch.setattr(spill, "ITEM_CAP_BYTES", 1000)
    big = {"rows": BIG}
    assert spill.shape_for_model(_session(), "list_projects", "tu1", big) is big


def test_dropping_the_session_removes_its_spills():
    from harness import session as harness_session, spill
    session = harness_session.get_or_create_session(None, model="m", owner_user_id="u1")
    loc = _spilled(session)
    path = spill.path_for(session, loc)
    assert path.exists()
    harness_session.drop_session(session.session_id)
    assert not path.parent.exists()


def test_stale_session_directories_are_swept_on_write(monkeypatch):
    from harness import spill
    old = _session()
    loc = _spilled(old)
    stale_dir = spill.path_for(old, loc).parent
    os.utime(stale_dir, (1, 1))
    _spilled(_session())                           # any later write sweeps
    assert not stale_dir.exists()


def test_secrets_are_redacted_before_the_write():
    from harness import spill
    session = _session()
    big = {"rows": BIG, "note": "token=sk-ant-api03-" + "x" * 40}
    loc = spill.shape_for_model(session, "list_projects", "tu1", big)["spill"]["locator"]
    assert "sk-ant-api03" not in spill.path_for(session, loc).read_text()


# ── catalogue ───────────────────────────────────────────────────────────────

def test_read_result_is_a_read_tier_control_tool():
    from harness.catalogue import TOOL_ROUTES, TOOLS, safety_tier_for
    from harness.toolsets import CONTROL_TOOLS
    from services.chat_tools import DISPATCHERS

    assert "read_result" in {t["name"] for t in TOOLS} and "read_result" in DISPATCHERS
    assert safety_tier_for("read_result") == "read"
    assert TOOL_ROUTES["read_result"] == ["_service_call_"]
    assert "read_result" in CONTROL_TOOLS


def test_spill_storage_lives_under_the_app_data_dir():
    from harness import spill
    session = _session()
    loc = _spilled(session)
    root = Path(os.environ["PYPSAGUI_APP_DATA_DIR"]).resolve()
    assert root in spill.path_for(session, loc).resolve().parents


def test_a_self_paging_tools_cut_names_its_own_paging_argument(monkeypatch):
    from harness import loop
    from harness.providers.fake import FakeProvider
    from services import chat_tools

    monkeypatch.setitem(chat_tools.DISPATCHERS, "list_components", lambda **kw: {"rows": BIG})
    session = _session()
    fake = FakeProvider([_call("list_components", {"component_class": "Bus"}), _close()])
    list(loop.run_turn(session, "list", provider=fake))
    content = _content(fake, 1)
    assert '"spill"' not in content
    assert "call list_components again with its `offset` argument" in content
