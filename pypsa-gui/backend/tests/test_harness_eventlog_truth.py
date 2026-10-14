"""
Chat harness issue 13 (part b): the session event log is the source of truth.

* The provider history is the log's projection: every change to
  `ChatSession.messages` is an event applied by the same fold step
  `derive_messages` uses, so the deque IS `derive_messages(events)`, redaction
  included. Nothing else in the backend changes it.
* A session that is not in memory (a restart, an eviction) is rebuilt from its
  log, tool calls and results included, and goes on writing the same log.
* The pending-turn WAL is retired: `turn/start` is on disk before the model
  is called, and a turn that started and never ended is what `/history`
  reports as interrupted, once, for the caller's sessions on this project.
* `chat.jsonl` is a projection: each record is a `turn/record` event first,
  then the line.
"""
from __future__ import annotations

import ast
import pathlib
import uuid

import pypsa
import pytest

from harness import eventlog
from services import chat_service

BACKEND = pathlib.Path(__file__).resolve().parents[1]
BIG_SECRET = "sk-ant-api03-" + "y" * 40


@pytest.fixture(autouse=True)
def _reset_chat_sessions():
    chat_service._reset_sessions_for_tests()
    yield
    chat_service._reset_sessions_for_tests()


@pytest.fixture
def tools(monkeypatch):
    from services import chat_tools
    monkeypatch.setitem(chat_tools.DISPATCHERS, "list_projects",
                        lambda **kw: {"rows": [{"name": "P1"}]})


def _tool_turn(name, args, tool_id="tu_1"):
    return {"blocks": [{"type": "tool_use", "id": tool_id, "name": name, "input": args}],
            "usage": {"input_tokens": 10, "output_tokens": 5}}


def _text_turn(text="ok"):
    return {"blocks": [{"type": "text", "text": text}],
            "usage": {"input_tokens": 3, "output_tokens": 2}}


def _turn(session, message, *turns, on_stream=None):
    from harness.providers.fake import FakeProvider

    class Watching(FakeProvider):
        def stream(self, request):
            if on_stream is not None:
                on_stream()
            yield from super().stream(request)
    return list(chat_service.run_turn(session, message, provider=Watching(list(turns))))


def _events(session):
    return eventlog.read_events(eventlog.path_for(session))


@pytest.fixture
def project(install_network, tmp_projects_dir, monkeypatch):
    """A bound project with a chat.jsonl home; returns its context."""
    from routers import projects as projects_router
    from services.pypsa_service import PyPSAService
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)
    name = f"Truth{uuid.uuid4().hex[:6]}"
    n = pypsa.Network()
    n.add("Bus", "B1")
    install_network(n, name=name)
    (tmp_projects_dir / name).mkdir(exist_ok=True)
    return PyPSAService.get_active_context()


def _owned(seeded_identity, sid=None):
    return chat_service.get_or_create_session(
        sid or f"truth-{uuid.uuid4().hex[:8]}", owner_user_id=str(seeded_identity["user_id"]))


# ── the projection the provider is sent ────────────────────────────────────

def test_the_history_is_the_fold_of_the_log_secrets_redacted(tools):
    from harness.session import ChatSession
    session = ChatSession(model="m", owner_user_id="u1")
    _turn(session, f"my key is {BIG_SECRET}", _tool_turn("list_projects", {}), _text_turn("noted"))
    _turn(session, "and again", _text_turn("ok"))
    assert list(session.messages) == eventlog.derive_messages(_events(session))
    assert BIG_SECRET not in repr(list(session.messages))       # the model sees what is logged


_MUTATORS = {"append", "appendleft", "extend", "extendleft", "clear", "pop", "popleft",
             "insert", "remove", "rotate"}
_FOLDS = {"trim_session_messages", "cut_turns"}


def test_nothing_but_the_log_changes_a_sessions_history():
    """A tripwire: outside `harness/eventlog.py` no production code mutates a
    `.messages` deque, or trims or cuts one, by hand."""
    offenders = []
    for path in sorted(BACKEND.rglob("*.py")):
        rel = path.relative_to(BACKEND)
        if rel.parts[0] in {"tests", "smoke", ".pixi", "alembic"} or rel == pathlib.Path("harness/eventlog.py"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if (isinstance(func, ast.Attribute) and func.attr in _MUTATORS
                    and isinstance(func.value, ast.Attribute) and func.value.attr == "messages"):
                offenders.append(f"{rel}:{node.lineno} .messages.{func.attr}")
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name in _FOLDS and node.args and isinstance(node.args[0], ast.Attribute) \
                    and node.args[0].attr == "messages":
                offenders.append(f"{rel}:{node.lineno} {name}(….messages)")
    assert offenders == []


# ── a session not in memory is rebuilt from its log ────────────────────────

def test_a_damaged_log_falls_back_to_the_transcript(client, seeded_identity, project, tools):
    """Issue 13b: the log is the source of truth only while it can be read.
    A broken line that is not a torn tail must not become a shorter history.
    The session is rebuilt from chat.jsonl, which still has both turns."""
    session = _owned(seeded_identity)
    _turn(session, "first", _text_turn("one"))
    _turn(session, "second", _text_turn("two"))
    path = eventlog.path_for(session)
    lines = path.read_text(encoding="utf-8").splitlines()
    second = next(i for i, line in enumerate(lines) if '"user": "second"' in line)
    lines.insert(second, "{not-json")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    chat_service._reset_sessions_for_tests()

    body = client.get("/api/chat/history").json()
    assert [t["user"] for t in body["turns"]] == ["first", "second"]
    assert body["pending_turn"] is None
    rebuilt = chat_service.get_session(session.session_id)
    users = [m["content"] for m in rebuilt.messages if m.get("role") == "user"]
    assert users == ["first", "second"]
    assert eventlog.path_for(rebuilt).read_bytes() == path.read_bytes()


def test_a_session_not_in_memory_is_rebuilt_from_its_log(client, seeded_identity, project, tools):
    session = _owned(seeded_identity)
    _turn(session, "list my projects", _tool_turn("list_projects", {}), _text_turn("one: P1"))
    before = list(session.messages)
    assert any(b.get("type") == "tool_result" for m in before if isinstance(m["content"], list)
               for b in m["content"])
    chat_service._reset_sessions_for_tests()                     # the process restarted

    body = client.get("/api/chat/history").json()
    assert body["last_session_id"] == session.session_id
    rebuilt = chat_service.get_session(session.session_id)
    assert list(rebuilt.messages) == before                       # tool calls and results too

    _turn(rebuilt, "and now?", _text_turn("still P1"))           # the same log goes on
    path = eventlog.path_for(rebuilt)
    lines = path.read_text().splitlines()
    assert sum('"format"' in line for line in lines) == 1
    seqs = [e["seq"] for e in eventlog.read_events(path)]
    assert seqs == sorted(set(seqs))
    assert list(rebuilt.messages) == eventlog.derive_messages(eventlog.read_events(path))


# ── the WAL is retired ─────────────────────────────────────────────────────

def test_the_turn_is_on_disk_while_the_model_streams_and_no_pending_file(project):
    from harness.session import ChatSession
    session = ChatSession(model="m", owner_user_id="u1")
    seen = []
    _turn(session, "how big is it?", _text_turn("small"),
          on_stream=lambda: seen.append([e["type"] for e in _events(session)]))
    assert seen and seen[0][-1] == "turn/start"
    start = next(e for e in _events(session) if e["type"] == "turn/start")
    assert start["user"] == "how big is it?" and start["project"] == eventlog.project_key(project)
    chat_path = chat_service.get_persist_path(project)
    assert not chat_path.with_suffix(chat_path.suffix + ".pending").exists()
    assert not hasattr(chat_service, "begin_pending_turn")


def _crashed_turn(seeded_identity, ctx, text):
    """A turn that started and never ended, its process gone."""
    session = _owned(seeded_identity)
    eventlog.turn_start(session, text, ctx)
    chat_service._reset_sessions_for_tests()
    return session


def test_history_reports_an_interrupted_turn_once_and_closes_it(client, seeded_identity, project):
    session = _crashed_turn(seeded_identity, project, "never answered")
    first = client.get("/api/chat/history").json()
    second = client.get("/api/chat/history").json()
    pending = first["pending_turn"]
    assert pending is not None and pending["user"] == "never answered"
    assert pending["session_id"] == session.session_id and isinstance(pending["ts"], float)
    assert second["pending_turn"] is None
    assert first["turns"] == []                                   # reported, not promoted
    events = eventlog.read_events(eventlog.path_for(session))
    assert events[-1] == {**events[-1], "type": "turn/end", "reason": "interrupted"}


def test_history_leaves_a_live_turn_alone(client, seeded_identity, project):
    session = _owned(seeded_identity, "live-session")
    eventlog.turn_start(session, "still thinking", project)
    session._turn_in_flight = True
    body = client.get("/api/chat/history").json()
    assert body["pending_turn"] is None
    assert _events(session)[-1]["type"] == "turn/start"          # not closed under it


def test_an_interrupted_turn_elsewhere_is_not_reported_here(client, seeded_identity, project, install_network):
    _crashed_turn(seeded_identity, project, "on the first project")
    n = pypsa.Network()
    n.add("Bus", "B1")
    install_network(n, name=f"Other{uuid.uuid4().hex[:6]}")
    assert client.get("/api/chat/history").json()["pending_turn"] is None


# ── chat.jsonl is a projection ─────────────────────────────────────────────

def test_chat_jsonl_is_the_projection_of_the_log(project, tools):
    from harness.session import ChatSession
    session = ChatSession(model="m", owner_user_id="u1")
    _turn(session, "first", _tool_turn("list_projects", {}), _text_turn("one"))
    _turn(session, "second", _text_turn("two"))
    records = chat_service.read_all_turns(project)
    assert [r["user"] for r in records] == ["first", "second"]
    assert records == eventlog.project_chat(_events(session))
    kinds = [e["type"] for e in _events(session)]
    assert kinds.index("turn/record") < kinds.index("turn/end")


def test_what_cannot_be_redacted_is_never_logged_but_the_history_still_moves(monkeypatch):
    from harness import history
    from harness.session import ChatSession

    session = ChatSession(model="m", owner_user_id="u1")
    session.append_history_message({"role": "user", "content": "first"})

    def broken(*a, **k):
        raise RuntimeError("redactor down")
    monkeypatch.setattr(history, "_redact_for_persist", broken)
    session.append_history_message({"role": "user", "content": f"key {BIG_SECRET}"})
    assert BIG_SECRET not in eventlog.path_for(session).read_text()
    assert len(session.messages) == 2
