"""
Improvement #20 / QA #10 — pending-turn WAL and corruption signalling.

Two defects, one file, because both are about what `chat.jsonl` can no longer
hide:

  * `read_all_turns` skips an unparseable line and says nothing. A transcript
    that lost a turn to a torn write reads as a transcript that never had it,
    so a reload silently shows a shorter conversation than the one the user
    had. The skip count has to reach the caller.

  * A turn that dies between "the user pressed Send" and "the turn completed"
    leaves NO trace at all — `append_turn` only runs on the success path. A
    crash mid-response loses the user's own message, and the reload cannot
    even report that something was lost. A pending record written at turn
    start closes that: it survives the crash precisely because the cleanup
    that removes it never ran.
"""
from __future__ import annotations

import json
import pathlib

import pypsa
import pytest

from services import chat_service
from services.project_context import ProjectContext


@pytest.fixture(autouse=True)
def _reset_chat_sessions():
    chat_service._reset_sessions_for_tests()
    yield
    chat_service._reset_sessions_for_tests()


def _make_bound_ctx(name: str, projects_dir: pathlib.Path) -> ProjectContext:
    n = pypsa.Network()
    n.add("Bus", "B1")
    ctx = ProjectContext(network=n, loaded_project=name)
    chat_service.get_persist_path(ctx)
    (projects_dir / name).mkdir(parents=True, exist_ok=True)
    return ctx


# ── corruption signalling ───────────────────────────────────────────────────


def test_read_all_turns_with_gap_counts_the_lines_it_skipped(
    tmp_projects_dir, monkeypatch,
):
    """
    A torn line between two good ones yields two turns AND a gap count of 1.

    The count is the whole point: `read_all_turns` already skips the line, so
    a test that only asserted on the surviving turns would pass against the
    unfixed code.
    """
    from routers import projects as projects_router
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)

    ctx = _make_bound_ctx("gapproj", tmp_projects_dir)
    path = chat_service.get_persist_path(ctx)
    assert path is not None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"user": "first"}) + "\n"
        + '{"user": "torn half of a rec'  + "\n"
        + json.dumps({"user": "third"}) + "\n",
        encoding="utf-8",
    )

    turns, gap = chat_service.read_all_turns_with_gap(ctx)

    assert [t["user"] for t in turns] == ["first", "third"]
    assert gap == 1


def test_read_all_turns_with_gap_reports_zero_on_a_clean_transcript(
    tmp_projects_dir, monkeypatch,
):
    """A healthy file must not report a gap — no false 'history damaged' banner."""
    from routers import projects as projects_router
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)

    ctx = _make_bound_ctx("cleanproj", tmp_projects_dir)
    chat_service.append_turn(ctx, {"user": "one"})
    chat_service.append_turn(ctx, {"user": "two"})

    turns, gap = chat_service.read_all_turns_with_gap(ctx)

    assert [t["user"] for t in turns] == ["one", "two"]
    assert gap == 0


def test_chat_history_surfaces_the_gap_count(
    install_network, tmp_projects_dir, monkeypatch, client,
):
    """
    GET /history must report `history_gap` so the panel can tell the user its
    transcript is incomplete rather than quietly rendering a shorter one.
    """
    from routers import projects as projects_router
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)

    n = pypsa.Network()
    n.add("Bus", "B1")
    install_network(n, name="GapHistory")
    (tmp_projects_dir / "GapHistory").mkdir(exist_ok=True)

    from services.pypsa_service import PyPSAService
    ctx = PyPSAService.get_active_context()
    path = chat_service.get_persist_path(ctx)
    assert path is not None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"session_id": "s1", "user": "kept", "model": "m"}) + "\n"
        + "{not json at all\n",
        encoding="utf-8",
    )

    r = client.get("/api/chat/history")

    assert r.status_code == 200
    body = r.json()
    assert len(body["turns"]) == 1
    assert body["history_gap"] == 1


# ── pending-turn WAL ────────────────────────────────────────────────────────
#
# Minimal local Anthropic fakes. Deliberately not imported from
# test_chat_e2e: a cross-test-module import couples two files whose fixtures
# differ, and this file needs only the one-text-block shape.


class _Block:
    def __init__(self, btype, **fields):
        self.type = btype
        for k, v in fields.items():
            setattr(self, k, v)


class _Usage:
    input_tokens = 10
    output_tokens = 2
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0


class _FinalMessage:
    def __init__(self, text):
        self.content = [_Block("text", text=text)]
        self.usage = _Usage()


class _Stream:
    def __init__(self, text, on_enter=None):
        self._text = text
        self._on_enter = on_enter

    def __enter__(self):
        if self._on_enter is not None:
            self._on_enter()
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        yield _Block("text", text=self._text)

    def get_final_message(self):
        return _FinalMessage(self._text)


class _Messages:
    def __init__(self, client):
        self._client = client

    def stream(self, **kwargs):
        return _Stream(self._client.reply, on_enter=self._client.on_stream)


class FakeClient:
    """One-text-turn Anthropic stand-in with a mid-turn observation hook."""

    def __init__(self, reply="ok", on_stream=None):
        self.reply = reply
        self.on_stream = on_stream
        self.messages = _Messages(self)


def test_begin_pending_turn_makes_the_users_message_recoverable(
    tmp_projects_dir, monkeypatch,
):
    """The record written at turn start must be readable back verbatim."""
    from routers import projects as projects_router
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)

    ctx = _make_bound_ctx("walproj", tmp_projects_dir)
    chat_service.begin_pending_turn(
        ctx, {"session_id": "s1", "user": "size the battery", "model": "m"},
    )

    recovered = chat_service.read_pending_turn(ctx)

    assert recovered is not None
    assert recovered["user"] == "size the battery"
    assert recovered["session_id"] == "s1"


def test_clear_pending_turn_removes_the_record(tmp_projects_dir, monkeypatch):
    """A turn that finished must leave nothing for the reload to recover."""
    from routers import projects as projects_router
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)

    ctx = _make_bound_ctx("walclear", tmp_projects_dir)
    chat_service.begin_pending_turn(ctx, {"session_id": "s1", "user": "hi"})
    chat_service.clear_pending_turn(ctx)

    assert chat_service.read_pending_turn(ctx) is None


def test_clear_pending_turn_is_safe_when_none_was_written(
    tmp_projects_dir, monkeypatch,
):
    """
    The clear runs in a `finally` on every exit path, including ones that
    never reached the write (an unbound context, an early cap rejection). It
    must not raise there — a cleanup that throws would mask the real error.
    """
    from routers import projects as projects_router
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)

    ctx = _make_bound_ctx("walnoop", tmp_projects_dir)
    chat_service.clear_pending_turn(ctx)  # must not raise

    assert chat_service.read_pending_turn(ctx) is None


def test_run_turn_writes_the_pending_record_before_the_model_streams(
    install_network, tmp_projects_dir, monkeypatch,
):
    """
    The WAL is worthless if it is written after the risky part. Observe the
    on-disk record from INSIDE the streaming call — the exact window a crash
    would land in.
    """
    from routers import projects as projects_router
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)

    n = pypsa.Network()
    n.add("Bus", "B1")
    install_network(n, name="WalDuringStream")
    (tmp_projects_dir / "WalDuringStream").mkdir(exist_ok=True)

    from services.pypsa_service import PyPSAService
    ctx = PyPSAService.get_active_context()

    seen: list[dict | None] = []
    fc = FakeClient(reply="done", on_stream=lambda: seen.append(
        chat_service.read_pending_turn(ctx)
    ))

    session = chat_service.ChatSession()
    list(chat_service.run_turn(session, "how big is the network?", client=fc))

    assert seen and seen[0] is not None, (
        "no pending record on disk while the model was streaming — a crash "
        "in that window would lose the user's message with no trace"
    )
    assert seen[0]["user"] == "how big is the network?"


def test_a_completed_turn_leaves_no_pending_record(
    install_network, tmp_projects_dir, monkeypatch,
):
    """Once the turn is in chat.jsonl, the WAL entry must be gone."""
    from routers import projects as projects_router
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)

    n = pypsa.Network()
    n.add("Bus", "B1")
    install_network(n, name="WalCompleted")
    (tmp_projects_dir / "WalCompleted").mkdir(exist_ok=True)

    from services.pypsa_service import PyPSAService
    ctx = PyPSAService.get_active_context()

    session = chat_service.ChatSession()
    list(chat_service.run_turn(session, "hello", client=FakeClient("hi back")))

    assert chat_service.read_pending_turn(ctx) is None
    assert [t["user"] for t in chat_service.read_all_turns(ctx)] == ["hello"]


def test_chat_history_recovers_an_interrupted_turn_and_clears_it(
    install_network, tmp_projects_dir, monkeypatch, client,
):
    """
    The post-crash state is a pending record whose session no longer exists
    in memory. GET /history must hand it back once, then clear it — reporting
    it forever would turn one interruption into a permanent banner.
    """
    from routers import projects as projects_router
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)

    n = pypsa.Network()
    n.add("Bus", "B1")
    install_network(n, name="WalRecover")
    (tmp_projects_dir / "WalRecover").mkdir(exist_ok=True)

    from services.pypsa_service import PyPSAService
    ctx = PyPSAService.get_active_context()
    chat_service.append_turn(
        ctx, {"session_id": "s1", "user": "answered", "model": "m"},
    )
    # A crash leaves this behind precisely because the cleanup never ran.
    chat_service.begin_pending_turn(
        ctx, {"session_id": "s1", "user": "never answered", "model": "m"},
    )

    first = client.get("/api/chat/history").json()
    second = client.get("/api/chat/history").json()

    assert first["pending_turn"] is not None
    assert first["pending_turn"]["user"] == "never answered"
    assert second["pending_turn"] is None, (
        "a recovered turn was reported twice — the reload should clear it"
    )
    # The recovered message is NOT silently promoted into the transcript;
    # it was never answered, so it is reported, not rewritten as history.
    assert [t["user"] for t in second["turns"]] == ["answered"]


def test_chat_history_leaves_a_live_turns_pending_record_alone(
    install_network, tmp_projects_dir, monkeypatch, client,
):
    """
    A second tab polling /history while a turn is genuinely running must not
    report that turn as interrupted — and must not delete the WAL entry that
    protects it. The live session's in-flight flag is the discriminator.
    """
    from routers import projects as projects_router
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)

    n = pypsa.Network()
    n.add("Bus", "B1")
    install_network(n, name="WalLive")
    (tmp_projects_dir / "WalLive").mkdir(exist_ok=True)

    from services.pypsa_service import PyPSAService
    ctx = PyPSAService.get_active_context()

    session = chat_service.get_or_create_session("live-session")
    session._turn_in_flight = True
    chat_service.begin_pending_turn(
        ctx,
        {"session_id": "live-session", "user": "still thinking", "model": "m"},
    )

    body = client.get("/api/chat/history").json()

    assert body["pending_turn"] is None, (
        "a running turn was reported as interrupted"
    )
    assert chat_service.read_pending_turn(ctx) is not None, (
        "the running turn's WAL entry was deleted out from under it"
    )
