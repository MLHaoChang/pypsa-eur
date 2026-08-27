"""
Session <-> profile binding (Task 7, spec section "Turn path").

Covers: session_init reports the resolved profile; a cross-wire profile
switch mid-session is refused with a typed frame (never a 4xx — the SSE
client discards non-2xx bodies); a same-wire rebind is allowed; the legacy
`model` field still resolves a profile; the persisted turn record carries
`profile_id` and GET /history rehydration resolves it back into a bound
session, dropping blocks that don't replay on the resolved wire; and the A8
rate-limit fallback generalises to `profile.fallback_model`.

Uses `services.llm_fake.FakeProvider` throughout — either injected directly
via `run_turn(..., provider=...)` (Task-7-only scenarios: turn persistence,
A8 fallback) or, for scenarios that must go through the router's binding
logic, via a monkeypatch of `chat_service._provider_for_profile` (mirrors how
`_build_anthropic_client` is already the sanctioned monkeypatch surface for
the zero-config path).

Fixtures follow test_llm_config.py's `appdata` pattern (isolated
PYPSAGUI_APP_DATA_DIR per test, so `llm-profiles.json` never bleeds across
tests) and test_chat_e2e.py's `install_network` / `tmp_projects_dir` /
`client` fixtures for the router-level + persistence scenarios.
"""
from __future__ import annotations

import json

import pytest

from services import chat_service


# ─────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def appdata(tmp_path, monkeypatch):
    """Isolated `PYPSAGUI_APP_DATA_DIR` so llm-profiles.json never bleeds
    across tests (same pattern as test_llm_config.py)."""
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture()
def openai_profile(appdata):
    """A saved ollama-like openai-wire profile, alongside the two builtins."""
    from services import llm_config

    profile = llm_config.LLMProfile(
        id="ollama-like", label="Ollama (local)", preset="custom", wire="openai",
        base_url="http://localhost:11434/v1", model="qwen3:8b",
        tools=True, vision=False, auth="none",
        fallback_model=None, max_output_tokens=None,
    )
    llm_config.save_profiles([profile], "anthropic-sonnet")
    return profile


@pytest.fixture(autouse=True)
def _reset_chat_sessions():
    chat_service._reset_sessions_for_tests()
    yield
    chat_service._reset_sessions_for_tests()


@pytest.fixture()
def fake_provider_for_profile(monkeypatch):
    """
    Monkeypatch `chat_service._provider_for_profile` so a real `/stream` call
    driven through `router/chat.py` (no `client=`/`provider=` injection seam
    available at that layer) resolves to a scripted `FakeProvider` regardless
    of which profile the router bound — the same "module attribute is the
    patch surface" doctrine `_build_anthropic_client` already documents.

    Returns a setter: call it with a `FakeProvider`-shaped turns list before
    each POST /stream that should actually run a (fake) model turn.
    """
    from services.llm_fake import FakeProvider

    state: dict = {}

    def _fake(profile, client=None):
        return state["provider"], None

    monkeypatch.setattr(chat_service, "_provider_for_profile", _fake)

    def _script(turns):
        state["provider"] = FakeProvider(turns)
        return state["provider"]

    return _script


def _parse_sse(raw: bytes) -> list[tuple[str, dict]]:
    """Parse an SSE byte stream into [(event_name, payload_dict), ...]."""
    out: list[tuple[str, dict]] = []
    text = raw.decode("utf-8")
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        event = None
        data_lines: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data_lines.append(line.split(":", 1)[1].strip())
        if event is None:
            continue
        payload = json.loads("\n".join(data_lines)) if data_lines else {}
        out.append((event, payload))
    return out


def _write_chat_jsonl(projects_dir, project, records):
    """Write turn records (one JSON object per line) into a project's chat.jsonl."""
    proj = projects_dir / project
    proj.mkdir(parents=True, exist_ok=True)
    path = proj / "chat.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")
    return path


# ─────────────────────────────────────────────────────────────────────────
# session_init reports the resolved (active) profile
# ─────────────────────────────────────────────────────────────────────────


def test_session_binds_active_profile_and_session_init_reports_it(appdata, client):
    """POST /stream with profile_id omitted binds the zero-config active
    profile and reports it in session_init."""
    resp = client.post(
        "/api/chat/stream",
        json={
            "session_id": "sess-bind-1",
            "script": [{"type": "session_done"}],
        },
    )
    assert resp.status_code == 200
    frames = _parse_sse(resp.content)
    first_event, first_payload = frames[0]
    assert first_event == "session_init"
    assert first_payload["profile_id"] == "anthropic-sonnet"
    assert first_payload["profile_label"] == "Claude Sonnet"
    # model key stays (pinned by test_chat_sse.py / e2e_chat_service.sh)
    assert first_payload["model"] == chat_service.DEFAULT_MODEL

    sess = chat_service.get_session("sess-bind-1")
    assert sess is not None
    assert sess.profile_id == "anthropic-sonnet"
    assert sess.bound_wire == "anthropic"


# ─────────────────────────────────────────────────────────────────────────
# Cross-wire switch mid-session -> typed frame, no 4xx
# ─────────────────────────────────────────────────────────────────────────


def test_cross_wire_switch_mid_session_emits_typed_frame(
    appdata, openai_profile, client, fake_provider_for_profile,
):
    from services.llm_provider import LLMEvent

    fake_provider_for_profile([{
        "events": [LLMEvent(type="text_delta", text="hi")],
        "blocks": [{"type": "text", "text": "hi"}],
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }])
    r1 = client.post(
        "/api/chat/stream",
        json={"session_id": "sess-cw", "message": "hello"},
    )
    assert r1.status_code == 200
    sess = chat_service.get_session("sess-cw")
    assert sess is not None
    assert sess.bound_wire == "anthropic"
    assert sess.profile_id == "anthropic-sonnet"
    with sess._lock:
        before_msgs = list(sess.messages)
    assert before_msgs, "the first turn should have appended messages"

    r2 = client.post(
        "/api/chat/stream",
        json={
            "session_id": "sess-cw",
            "profile_id": openai_profile.id,
            "message": "switch to ollama please",
        },
    )
    # NEVER a 4xx — the SSE client discards non-2xx bodies.
    assert r2.status_code == 200
    frames = _parse_sse(r2.content)
    names = [n for n, _ in frames]
    assert names[:2] == ["error", "session_done"]
    assert frames[0][1]["error_kind"] == "profile_switch_requires_new_chat"

    # The session stays bound to its ORIGINAL profile/wire, untouched.
    assert sess.bound_wire == "anthropic"
    assert sess.profile_id == "anthropic-sonnet"
    with sess._lock:
        after_msgs = list(sess.messages)
    assert after_msgs == before_msgs


# ─────────────────────────────────────────────────────────────────────────
# Same-wire rebind is allowed
# ─────────────────────────────────────────────────────────────────────────


def test_same_wire_rebind_updates_model(appdata, client):
    r1 = client.post(
        "/api/chat/stream",
        json={"session_id": "sess-rebind", "script": [{"type": "session_done"}]},
    )
    assert r1.status_code == 200
    sess = chat_service.get_session("sess-rebind")
    assert sess is not None
    assert sess.model == chat_service.DEFAULT_MODEL
    assert sess.profile_id == "anthropic-sonnet"

    r2 = client.post(
        "/api/chat/stream",
        json={
            "session_id": "sess-rebind",
            "profile_id": "anthropic-opus",
            "script": [{"type": "session_done"}],
        },
    )
    assert r2.status_code == 200
    names = [n for n, _ in _parse_sse(r2.content)]
    assert "error" not in names

    assert sess.model == chat_service.OPUS_MODEL
    assert sess.profile_id == "anthropic-opus"
    assert sess.bound_wire == "anthropic"


# ─────────────────────────────────────────────────────────────────────────
# Legacy `model` field still works
# ─────────────────────────────────────────────────────────────────────────


def test_legacy_model_field_still_works(appdata, client):
    r = client.post(
        "/api/chat/stream",
        json={
            "session_id": "sess-legacy",
            "model": chat_service.OPUS_MODEL,
            "script": [{"type": "session_done"}],
        },
    )
    assert r.status_code == 200
    sess = chat_service.get_session("sess-legacy")
    assert sess is not None
    assert sess.profile_id == "anthropic-opus"
    assert sess.bound_wire == "anthropic"
    assert sess.model == chat_service.OPUS_MODEL


# ─────────────────────────────────────────────────────────────────────────
# Turn record carries profile_id; GET /history rehydration binds it back
# ─────────────────────────────────────────────────────────────────────────


def test_turn_record_carries_profile_id_and_rehydration_binds(
    appdata, openai_profile, tmp_projects_dir, install_network, client, monkeypatch,
):
    from routers import projects as projects_router
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)
    from services.llm_provider import LLMEvent
    from services.llm_fake import FakeProvider

    import pypsa
    n = pypsa.Network()
    n.add("Bus", "B1")
    install_network(n, name="ProfileBindProj")
    (tmp_projects_dir / "ProfileBindProj").mkdir(exist_ok=True)

    session = chat_service.ChatSession()
    session.profile_id = openai_profile.id
    session.bound_wire = openai_profile.wire
    session.model = openai_profile.model

    fake = FakeProvider([{
        "events": [LLMEvent(type="text_delta", text="hi")],
        "blocks": [{"type": "text", "text": "hi"}],
        "usage": {"input_tokens": 3, "output_tokens": 2},
    }])
    list(chat_service.run_turn(session, "hello", provider=fake))

    chat_path = tmp_projects_dir / "ProfileBindProj" / "chat.jsonl"
    assert chat_path.exists()
    rec = json.loads(chat_path.read_text(encoding="utf-8").strip().splitlines()[0])
    assert rec["profile_id"] == openai_profile.id

    persisted_id = session.session_id
    chat_service._reset_sessions_for_tests()

    r = client.get("/api/chat/history")
    assert r.status_code == 200
    body = r.json()
    assert body["last_session_id"] == persisted_id
    assert body["turns"][-1]["profile_id"] == openai_profile.id

    minted = chat_service.get_session(persisted_id)
    assert minted is not None
    assert minted.profile_id == openai_profile.id
    assert minted.bound_wire == "openai"


# ─────────────────────────────────────────────────────────────────────────
# Rehydration into an openai-wire profile drops non-portable blocks
# ─────────────────────────────────────────────────────────────────────────


def test_rehydration_into_openai_wire_drops_thinking_blocks(
    appdata, openai_profile, tmp_projects_dir, install_network, client, monkeypatch,
):
    from routers import projects as projects_router
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)

    import pypsa
    n = pypsa.Network()
    n.add("Bus", "B1")
    install_network(n, name="OpenAIRehydrateProj")

    _write_chat_jsonl(tmp_projects_dir, "OpenAIRehydrateProj", [{
        "ts": 1.0,
        "session_id": "sess-thinking-drop",
        "model": openai_profile.model,
        "profile_id": openai_profile.id,
        "user": "explain the dispatch",
        "assistant": [
            {"type": "thinking", "thinking": "internal reasoning",
             "signature": "sig-abc"},
            {"type": "text", "text": "the visible answer"},
        ],
        "usage": {},
    }])

    chat_service._reset_sessions_for_tests()
    r = client.get("/api/chat/history")
    assert r.status_code == 200

    sess = chat_service.get_session("sess-thinking-drop")
    assert sess is not None
    assert sess.profile_id == openai_profile.id
    assert sess.bound_wire == "openai"
    with sess._lock:
        msgs = list(sess.messages)
    assistant_msgs = [m for m in msgs if m["role"] == "assistant"]
    assert len(assistant_msgs) == 1
    block_types = [b.get("type") for b in assistant_msgs[0]["content"]]
    assert "thinking" not in block_types
    assert "text" in block_types


# ─────────────────────────────────────────────────────────────────────────
# A8 — fallback generalises to profile.fallback_model
# ─────────────────────────────────────────────────────────────────────────


def test_a8_fallback_uses_profile_fallback_model(appdata, monkeypatch):
    from services import llm_config
    from services.llm_fake import FakeProvider
    from services.llm_provider import LLMEvent, ProviderError

    profile = llm_config.LLMProfile(
        id="custom-fb", label="Custom FB", preset="custom", wire="anthropic",
        base_url=None, model="primary-model", tools=True, vision=False,
        auth="none", fallback_model="fallback-model", max_output_tokens=None,
    )
    llm_config.save_profiles([profile], "anthropic-sonnet")

    # Small + zero-delay retry budget so the fallback fires on the first
    # rate_limited without any real backoff sleep.
    monkeypatch.setattr(chat_service, "MAX_STREAM_RETRIES", 0)
    monkeypatch.setattr(chat_service, "BASE_STREAM_RETRY_DELAY", 0.0)
    monkeypatch.setattr(chat_service, "MAX_STREAM_RETRY_DELAY", 0.0)

    session = chat_service.ChatSession(model="primary-model")
    session.profile_id = "custom-fb"

    fake = FakeProvider([
        ProviderError("rate_limited", "busy on primary"),
        {
            "events": [LLMEvent(type="text_delta", text="ok")],
            "blocks": [{"type": "text", "text": "ok"}],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        },
    ])

    events = list(chat_service.run_turn(session, "hi", provider=fake))
    names = [n for n, _ in events]
    assert names.count("model_fallback") == 1
    assert "error" not in names

    fb = next(p for n, p in events if n == "model_fallback")
    assert fb["from_model"] == "primary-model"
    assert fb["to_model"] == "fallback-model"
    assert fb["profile_id"] == "custom-fb"
    assert session.model == "fallback-model"
