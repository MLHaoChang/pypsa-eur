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


# ─────────────────────────────────────────────────────────────────────────
# Fix round 1 — Finding 1: GET /history must not rebind an ALREADY-LIVE
# session's profile out from under it.
# ─────────────────────────────────────────────────────────────────────────


def test_history_does_not_rebind_an_already_live_session(
    appdata, tmp_projects_dir, install_network, client, monkeypatch,
):
    """
    Regression for Finding 1. `chat_history` used to write
    `sess.profile_id`/`bound_wire`/`model` UNCONDITIONALLY from the LATEST
    persisted turn's profile, even when `session_id` already named a
    resident, live `ChatSession` (e.g. a second tab GETting /history while
    the first tab's turn is bound to a profile the on-disk transcript
    doesn't reflect yet). That silently reverted a live session's binding
    mid-conversation.

    This test registers the session and binds it explicitly BEFORE calling
    GET /history, then persists a chat.jsonl record under the SAME
    session_id naming a DIFFERENT profile. The fix in routers/chat.py reads
    `session_was_already_registered = chat_service.get_session(...) is not
    None` before `get_or_create_session` (which registers the id as a side
    effect) and only applies the three profile-binding writes when the
    session was freshly minted by this GET.

    CRITICAL: deliberately does NOT call `_reset_sessions_for_tests()`
    between registering/binding the session and the GET call below — the
    module's autouse `_reset_chat_sessions` fixture only resets at test
    start/end, which is fine. The EXISTING rehydration test
    (`test_turn_record_carries_profile_id_and_rehydration_binds`) calls
    `_reset_sessions_for_tests()` right before its GET, which empties the
    registry first and makes `chat_history` always take the
    "freshly-minted" branch — that is exactly why it never caught this bug.
    """
    from services import llm_config

    import pypsa
    n = pypsa.Network()
    n.add("Bus", "B1")
    install_network(n, name="LiveSessProj")

    from routers import projects as projects_router
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)

    session_id = "sess-already-live"

    # Persist a turn record under the SAME session_id naming the SONNET
    # builtin profile — different from the OPUS binding the live session
    # below will carry.
    _write_chat_jsonl(tmp_projects_dir, "LiveSessProj", [{
        "ts": 1.0,
        "session_id": session_id,
        "model": chat_service.DEFAULT_MODEL,
        "profile_id": llm_config.BUILTIN_SONNET_ID,
        "user": "hello from disk",
        "assistant": [{"type": "text", "text": "hi from disk"}],
        "usage": {},
    }])

    # Register the session and bind it explicitly to OPUS, mimicking a live
    # `/stream` bind that ran AFTER the persisted record above (or is
    # simply ahead of what's durable yet — the router doesn't know which).
    live_session = chat_service.get_or_create_session(session_id)
    live_session.profile_id = llm_config.BUILTIN_OPUS_ID
    live_session.bound_wire = "anthropic"
    live_session.model = chat_service.OPUS_MODEL

    r = client.get("/api/chat/history")
    assert r.status_code == 200
    body = r.json()
    assert body["last_session_id"] == session_id

    # The live session must be UNCHANGED — still bound to opus, not
    # reverted to the sonnet profile named in chat.jsonl.
    sess = chat_service.get_session(session_id)
    assert sess is live_session
    assert sess.profile_id == llm_config.BUILTIN_OPUS_ID
    assert sess.bound_wire == "anthropic"
    assert sess.model == chat_service.OPUS_MODEL


# ─────────────────────────────────────────────────────────────────────────
# Fix round 2 — the round-1 fix still spanned TWO separate `_SESSIONS_LOCK`
# critical sections (the `get_session` probe, then `get_or_create_session`),
# leaving a microsecond gap where a concurrent `/stream` register-and-bind
# lands between them and still gets clobbered by the stale-transcript
# profile. This closes it into one critical section.
# ─────────────────────────────────────────────────────────────────────────


def test_history_closes_probe_to_create_race_window(
    appdata, tmp_projects_dir, install_network, client, monkeypatch,
):
    """
    Regression for the round-1 fix's remaining gap. `chat_history` used to
    read `session_was_already_registered` via a standalone
    `chat_service.get_session(...)` call (one lock acquisition), then
    separately call `chat_service.get_or_create_session(...)` (a SECOND lock
    acquisition). A concurrent `POST /stream` that registers AND binds the
    session in the gap between those two calls is invisible to the stale
    `session_was_already_registered = False` captured before it — so the
    guard still overwrites the freshly-bound live session with the profile
    named in the on-disk transcript.

    This can't be reproduced with real threads deterministically, so the
    race is injected at `llm_config.resolve_profile` — the one piece of
    work `chat_history` does, in BOTH the buggy and fixed implementations,
    strictly between reading `last_session_id` and touching the session
    registry. Simulating the concurrent `/stream` bind there stands in for
    it landing anywhere in the gap between the two calls it's meant to
    represent:

      * Old code: the probe (`get_session`) already ran and captured
        `False` BEFORE this injection point runs, so it's now stale --
        `get_or_create_session` (called after) finds the injected,
        already-bound session and the guard clobbers it. FAILS.
      * Fixed code: the injection still runs before the single combined
        call, but that call performs its OWN existence check under the
        SAME lock acquisition it creates under -- so it sees the injected
        session and correctly reports `created=False`, and the guard
        leaves it untouched. PASSES.

    Directly demonstrates why the fix must be ONE critical section: it is
    the only way for the existence-check to never be stale by construction,
    regardless of where in "between reading the transcript and touching the
    registry" a concurrent writer lands.
    """
    from services import llm_config

    import pypsa
    n = pypsa.Network()
    n.add("Bus", "B1")
    install_network(n, name="RaceGapProj")

    from routers import projects as projects_router
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)

    session_id = "sess-race-gap"

    # Persisted transcript names the SONNET builtin -- the profile a
    # freshly-minted session should adopt, and the profile that must NOT
    # land on a session a concurrent /stream already bound to OPUS.
    _write_chat_jsonl(tmp_projects_dir, "RaceGapProj", [{
        "ts": 1.0,
        "session_id": session_id,
        "model": chat_service.DEFAULT_MODEL,
        "profile_id": llm_config.BUILTIN_SONNET_ID,
        "user": "hello from disk",
        "assistant": [{"type": "text", "text": "hi from disk"}],
        "usage": {},
    }])

    real_resolve_profile = llm_config.resolve_profile
    injected = {"fired": False}

    def racing_resolve_profile(profile_id):
        # Fires exactly once, standing in for a concurrent POST /stream
        # that registers-and-binds `session_id` to OPUS in the gap between
        # `chat_history` reading the transcript and it touching the
        # session registry.
        if not injected["fired"]:
            injected["fired"] = True
            live = chat_service.get_or_create_session(session_id)
            live.profile_id = llm_config.BUILTIN_OPUS_ID
            live.bound_wire = "anthropic"
            live.model = chat_service.OPUS_MODEL
        return real_resolve_profile(profile_id)

    monkeypatch.setattr(llm_config, "resolve_profile", racing_resolve_profile)

    r = client.get("/api/chat/history")
    assert r.status_code == 200
    body = r.json()
    assert body["last_session_id"] == session_id
    assert injected["fired"]

    # The concurrently-bound OPUS session must survive untouched -- not
    # reverted to the SONNET profile named on disk.
    sess = chat_service.get_session(session_id)
    assert sess is not None
    assert sess.profile_id == llm_config.BUILTIN_OPUS_ID
    assert sess.bound_wire == "anthropic"
    assert sess.model == chat_service.OPUS_MODEL


# ─────────────────────────────────────────────────────────────────────────
# Fix round 1 — Finding 2: the A8 fallback flag must be TURN-scoped, not
# round-scoped (at most one downgrade per WHOLE turn, across every agentic
# round, not just within one outer-loop pass).
# ─────────────────────────────────────────────────────────────────────────


def test_a8_fallback_is_turn_scoped_across_multiple_rounds(
    appdata, install_network, monkeypatch,
):
    """
    Regression for Finding 2. `model_fallback_used` used to be initialised
    to `False` INSIDE the outer `while True:` loop (i.e. re-armed at the
    top of every agentic round), so "at most one downgrade per turn" only
    held in practice because the old hardcoded `session.model == OPUS_MODEL`
    guard happened to go false the instant the fallback fired. Once the
    guard reads `profile.fallback_model` instead, that coincidence is gone:
    a SECOND `rate_limited` later in the SAME turn would fire a SECOND
    `model_fallback` frame instead of surfacing as a terminal error.

    Script (four scripted provider turns, driving three agentic rounds):
      Round A — tool_use (read-tier, dispatched immediately) -> turn
                continues into round B.
      Round B, attempt 1 — rate_limited. Retries are exhausted immediately
                (MAX_STREAM_RETRIES=0) -> fires the ONE allowed A8 fallback
                (`model_fallback` #1), grants one extra attempt.
      Round B, fallback attempt — another tool_use (dispatched) -> turn
                continues into round C.
      Round C — rate_limited AGAIN. With the flag correctly turn-scoped,
                `model_fallback_used` is already True, so this must NOT
                fire a second fallback; it must be TERMINAL (an `error`
                frame, then `session_done`).

    With the bug (flag re-initialised inside the outer loop), round C would
    see `model_fallback_used == False` again and fire a second
    `model_fallback` frame instead of terminating — this test fails on
    exactly that difference (see the "Fix round 1" report section for the
    scratch-copy proof).
    """
    from services import llm_config
    from services.llm_fake import FakeProvider
    from services.llm_provider import LLMEvent, ProviderError

    import pypsa
    n = pypsa.Network()
    n.add("Bus", "B1")
    install_network(n, name=None)

    profile = llm_config.LLMProfile(
        id="custom-fb-multi", label="Custom FB Multi", preset="custom",
        wire="anthropic", base_url=None, model="primary-model", tools=True,
        vision=False, auth="none", fallback_model="fallback-model",
        max_output_tokens=None,
    )
    llm_config.save_profiles([profile], "anthropic-sonnet")

    # Small + zero-delay retry budget, same as the single-round A8 test
    # above, so both rate_limited failures resolve without real backoff.
    monkeypatch.setattr(chat_service, "MAX_STREAM_RETRIES", 0)
    monkeypatch.setattr(chat_service, "BASE_STREAM_RETRY_DELAY", 0.0)
    monkeypatch.setattr(chat_service, "MAX_STREAM_RETRY_DELAY", 0.0)

    session = chat_service.ChatSession(model="primary-model")
    session.profile_id = "custom-fb-multi"

    fake = FakeProvider([
        # Round A — tool_use, dispatched (read tier, no confirmation),
        # turn continues.
        {
            "events": [LLMEvent(type="tool_use_start", tool_use_id="tu-1",
                                 tool_name="list_components")],
            "blocks": [{"type": "tool_use", "id": "tu-1",
                        "name": "list_components",
                        "input": {"component_class": "Bus"}}],
            "usage": {"input_tokens": 5, "output_tokens": 5},
        },
        # Round B, attempt 1 — rate_limited; retries exhausted immediately
        # -> fires the ONE allowed fallback.
        ProviderError("rate_limited", "busy on primary"),
        # Round B, fallback attempt — another tool_use, dispatched, turn
        # continues.
        {
            "events": [LLMEvent(type="tool_use_start", tool_use_id="tu-2",
                                 tool_name="list_components")],
            "blocks": [{"type": "tool_use", "id": "tu-2",
                        "name": "list_components",
                        "input": {"component_class": "Bus"}}],
            "usage": {"input_tokens": 4, "output_tokens": 4},
        },
        # Round C — rate_limited AGAIN. Must be terminal, not a second
        # fallback.
        ProviderError("rate_limited", "busy on fallback too"),
    ])

    events = list(chat_service.run_turn(session, "hi", provider=fake))
    names = [n for n, _ in events]

    # At most one downgrade for the WHOLE turn, across every round.
    assert names.count("model_fallback") == 1
    # The second rate_limited must be TERMINAL: exactly one error frame,
    # and the turn ends via session_done (not a silent second fallback).
    assert names.count("error") == 1
    assert names[-1] == "session_done"

    fb = next(p for n, p in events if n == "model_fallback")
    assert fb["from_model"] == "primary-model"
    assert fb["to_model"] == "fallback-model"

    error_payload = next(p for n, p in events if n == "error")
    assert error_payload["error_kind"] == "rate_limited"

    # The fallback model is still what the turn ended up on — the second
    # rate_limited did not trigger any further model mutation.
    assert session.model == "fallback-model"


# ─────────────────────────────────────────────────────────────────────────
# Task 8 — capability-honest degradation: tools/vision enforcement, prompt
# split. Spec: .superpowers/sdd/2026-08-14-llm-provider-config-and-switching/
# task-8-brief.md.
# ─────────────────────────────────────────────────────────────────────────


def test_toolless_profile_sends_no_tools_and_trimmed_prompt(appdata):
    """
    `tools: false` on the bound profile -> the request carries `tools=[]`
    and `tools_stable=False`, the system prompt drops its tool-chaining half
    (no "CHAIN get_results" imperative), and `session_init.tool_count`
    reports the count ACTUALLY SENT (0), not `len(TOOLS)`.
    """
    from services import llm_config
    from services.llm_fake import FakeProvider
    from services.llm_provider import LLMEvent

    profile = llm_config.LLMProfile(
        id="toolless", label="Toolless Profile", preset="custom",
        wire="anthropic", base_url=None, model="claude-sonnet-5",
        tools=False, vision=True, auth="none", fallback_model=None,
        max_output_tokens=None,
    )
    llm_config.save_profiles([profile], "anthropic-sonnet")

    session = chat_service.ChatSession(model="claude-sonnet-5")
    session.profile_id = "toolless"

    fake = FakeProvider([{
        "events": [LLMEvent(type="text_delta", text="hi")],
        "blocks": [{"type": "text", "text": "hi"}],
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }])

    events = list(chat_service.run_turn(session, "hello", provider=fake))
    names = [n for n, _ in events]
    assert "error" not in names

    session_init = next(p for n, p in events if n == "session_init")
    assert session_init["tool_count"] == 0

    assert len(fake.requests) == 1
    req = fake.requests[0]
    assert req.tools == []
    assert req.tools_stable is False
    system_text = req.system_blocks[0]["text"]
    assert "CHAIN get_results" not in system_text
    assert "CHAIN get_results" not in system_text.replace("\n", " ")


def test_vision_false_blocks_replayed_image_blocks(appdata):
    """
    An image attached on an EARLIER turn is replayed into `messages` via
    session history. A `vision: false` profile must be blocked by that
    replay too, BEFORE any provider call — a check keyed only on this
    turn's own `attachment_file_ids` would miss it entirely.
    """
    from services import llm_config
    from services.llm_fake import FakeProvider

    profile = llm_config.LLMProfile(
        id="no-vision", label="No Vision Profile", preset="custom",
        wire="anthropic", base_url=None, model="claude-sonnet-5",
        tools=True, vision=False, auth="none", fallback_model=None,
        max_output_tokens=None,
    )
    llm_config.save_profiles([profile], "anthropic-sonnet")

    session = chat_service.ChatSession(model="claude-sonnet-5")
    session.profile_id = "no-vision"
    # Seed history with an image block from a "previous turn" — mirrors what
    # upload_service.build_multimodal_content_blocks produces.
    session.append_history_message({
        "role": "user",
        "content": [
            {"type": "image", "source": {"type": "base64",
                                          "media_type": "image/png",
                                          "data": "AAAA"}},
            {"type": "text", "text": "earlier: what is this?"},
        ],
    })
    session.append_history_message({
        "role": "assistant",
        "content": [{"type": "text", "text": "it looks like a bus diagram"}],
    })

    fake = FakeProvider([])  # must never be reached

    events = list(chat_service.run_turn(session, "and now?", provider=fake))
    names = [n for n, _ in events]
    assert names == ["session_init", "error", "session_done"]
    assert fake.requests == []

    error_payload = next(p for n, p in events if n == "error")
    assert error_payload["error_kind"] == "capability_unsupported"
    assert "No Vision Profile" in error_payload["message"]


def test_pdf_blocks_require_anthropic_wire_even_with_vision_true(appdata):
    """
    PDF (`document`) blocks are Anthropic-native: even with `vision: true`,
    a non-anthropic-wire profile can't process them -> capability_unsupported
    with a PDF-specific fixed message, distinct from the vision-off message.
    """
    from services import llm_config
    from services.llm_fake import FakeProvider

    profile = llm_config.LLMProfile(
        id="openai-vision", label="OpenAI Vision Profile", preset="custom",
        wire="openai", base_url="http://localhost:11434/v1", model="qwen3:8b",
        tools=True, vision=True, auth="none", fallback_model=None,
        max_output_tokens=None,
    )
    llm_config.save_profiles([profile], "anthropic-sonnet")

    session = chat_service.ChatSession(model="qwen3:8b")
    session.profile_id = "openai-vision"
    session.bound_wire = "openai"
    session.append_history_message({
        "role": "user",
        "content": [
            {"type": "document", "source": {"type": "base64",
                                             "media_type": "application/pdf",
                                             "data": "AAAA"}},
            {"type": "text", "text": "earlier: summarise this pdf"},
        ],
    })
    session.append_history_message({
        "role": "assistant",
        "content": [{"type": "text", "text": "ok"}],
    })

    fake = FakeProvider([])  # must never be reached

    events = list(chat_service.run_turn(session, "and now?", provider=fake))
    names = [n for n, _ in events]
    assert names == ["session_init", "error", "session_done"]
    assert fake.requests == []

    error_payload = next(p for n, p in events if n == "error")
    assert error_payload["error_kind"] == "capability_unsupported"
    assert "pdf" in error_payload["message"].lower()
    assert "OpenAI Vision Profile" in error_payload["message"]


def test_default_prompt_bytes_unchanged():
    """
    Fix round 2 (coordinator correction on top of Task 8 review finding 3):
    all five DEFAULT (tools-enabled) prompt constants —  `_DOMAIN_GUIDE`,
    `_SOLVER_ERROR_DECODER`, `_PRICE_CONGESTION_GUIDE`, `_NEXT_STEP_RUBRIC`,
    `_ASSISTANT_STANCE` — must be byte-identical to their pre-Task-8 HEAD
    (32a0949a) values, no exceptions. Fix round 1 repinned
    `_SOLVER_ERROR_DECODER` / `_NEXT_STEP_RUBRIC` against a deliberately
    REORDERED text (to get a clean FACTS/CHAINING split); the coordinator
    correctly rejected that — the default prompt every user gets must not
    change as a side effect of a refactor, full stop. Both constants are now
    restored to the exact pre-Task-8 literal in chat_service.py (verified
    directly, no longer built by concatenating halves); this test checks
    that restoration.

    This test checks the five constants THEMSELVES, not
    `_X_FACTS + _X_CHAINING` — for `_SOLVER_ERROR_DECODER` /
    `_NEXT_STEP_RUBRIC` the halves are now separate, tools-off-only
    constants that do NOT need to reassemble to the original (see
    test_solver_and_rubric_halves_cover_the_same_words below for their
    drift guard, and the comment above each constant in chat_service.py for
    why: a straight concatenation split can't pull a tool-naming clause out
    of the middle of the original string without reordering it, and
    reordering is exactly what's being ruled out here).

    Hashes captured at HEAD (32a0949a, before Task 8's split) via:
        pixi run -e test python -c "
        import hashlib
        from services import chat_service as cs
        for n in ['_DOMAIN_GUIDE', '_SOLVER_ERROR_DECODER',
                  '_PRICE_CONGESTION_GUIDE', '_NEXT_STEP_RUBRIC',
                  '_ASSISTANT_STANCE']:
            print(n, hashlib.sha256(getattr(cs, n).encode()).hexdigest())"
    Meaningful (not tautological) because every hash below was computed
    against the pre-Task-8 literal, independently of the current module.
    """
    import hashlib

    expected_sha256 = {
        "_DOMAIN_GUIDE": (
            "3e6f420d74fea27240186cc520718dd401fd7b18a6e0fef1262630f053256f8f"
        ),
        "_SOLVER_ERROR_DECODER": (
            "bd4de84083da126945d36c23a0e10bb0823d157ce8befb9aecf7c1b899c029db"
        ),
        "_PRICE_CONGESTION_GUIDE": (
            "a65668e11eee7e3f30007ffc91cc7f3197e2a6938c7e8ea6e1fd647b5aa25129"
        ),
        "_NEXT_STEP_RUBRIC": (
            "991709d96f9cb8b42db7b03d0b28cb5bc3aeeab7e6de57ab63dff962cc79fe1b"
        ),
        "_ASSISTANT_STANCE": (
            "1dd77952d0cdec42606c4d269adf31aae9d11ae8ef22cf83103ac0659028889b"
        ),
        # Task 11 — `_BASE_IDENTITY` joined the split for the same reason one
        # level down: it names no specific tool (so it never broke the
        # "tools-off names NO tool" rule) but still told a TOOLS-LESS model to
        # "use the provided tools". Split into FACTS + CHAINING, which DO
        # reassemble byte-identically here — unlike the solver/rubric pair,
        # the tool clause sits at the end, so no reordering was needed.
        # Hash captured from the pre-split literal the same way as the others.
        "_BASE_IDENTITY": (
            "aa4112c6f2a69e05304e044f4ed4c413545bd32ca9f76acd784282a1c10b12d2"
        ),
    }
    for name, expected_hash in expected_sha256.items():
        value = getattr(chat_service, name)
        actual_hash = hashlib.sha256(value.encode()).hexdigest()
        assert actual_hash == expected_hash, (
            f"{name} is no longer byte-identical to its pre-Task-8 HEAD "
            f"(32a0949a) value"
        )


def test_tools_off_prompt_does_not_tell_the_model_to_use_tools():
    """
    Task 11 — `_BASE_IDENTITY` used to say "Use the provided tools to answer
    questions and make changes" in EVERY prompt, including the tools-off one.
    It names no specific tool, so Task 8's "tools-off names NO tool" check
    passed it — but instructing a model with `tools=[]` to use tools is the
    same capability-dishonesty that check exists to prevent, just phrased
    generically. Task 8's review flagged it; this pins the fix.

    The tools-ON prompt must KEEP the instruction: it is correct there.
    """
    from services.chat_service import _build_system_prompt

    session = chat_service.ChatSession()
    off = _build_system_prompt(session, include_tools=False)
    on = _build_system_prompt(session, include_tools=True)

    assert "provided tools" not in off, (
        "tools-off prompt still instructs the model to use tools it does "
        f"not have: {off[:300]!r}"
    )
    # The identity itself survives — only the tool clause is dropped.
    assert "pypsa-gui assistant" in off
    assert "energy-system optimisation model" in off
    # And the tools-on prompt is unchanged in this respect.
    assert "provided tools" in on


def test_solver_and_rubric_halves_cover_the_same_words():
    """
    Fix round 2 — `_SOLVER_ERROR_DECODER_FACTS` / `_CHAINING` and
    `_NEXT_STEP_RUBRIC_FACTS` / `_CHAINING` are now SEPARATE from the
    byte-identical `_SOLVER_ERROR_DECODER` / `_NEXT_STEP_RUBRIC` constants
    above — reordered for a genuinely coherent tools-off split, no longer
    required to concatenate back to the original. That decoupling trades
    the old byte-identity guarantee for a DRIFT risk: someone edits the
    original constant and the halves silently stop covering the same
    content.

    Guard it without re-imposing ordering: the word MULTISET of
    FACTS + CHAINING must equal the word multiset of the original. This
    catches added/dropped/changed words while permitting the legitimate
    reordering the split needs.
    """
    import collections

    pairs = {
        "_SOLVER_ERROR_DECODER": (
            chat_service._SOLVER_ERROR_DECODER,
            chat_service._SOLVER_ERROR_DECODER_FACTS,
            chat_service._SOLVER_ERROR_DECODER_CHAINING,
        ),
        "_NEXT_STEP_RUBRIC": (
            chat_service._NEXT_STEP_RUBRIC,
            chat_service._NEXT_STEP_RUBRIC_FACTS,
            chat_service._NEXT_STEP_RUBRIC_CHAINING,
        ),
    }
    for name, (original, facts, chaining) in pairs.items():
        original_words = collections.Counter(original.split())
        halves_words = collections.Counter(facts.split()) + collections.Counter(
            chaining.split()
        )
        if original_words != halves_words:
            missing = original_words - halves_words  # in original, not halves
            extra = halves_words - original_words  # in halves, not original
            raise AssertionError(
                f"{name}_FACTS + {name}_CHAINING no longer covers the same "
                f"words as {name} — missing from halves: {dict(missing)}; "
                f"extra in halves: {dict(extra)}"
            )


def test_capability_unsupported_frames_carry_no_identifiers(appdata):
    """
    SECURITY (unowned finding recorded on master in b94eb245, binding here):
    no error frame may carry an IDENTIFIER — email, user/org/project id — or
    a full base_url (host:port maximum). Redaction is secrets-only by design
    and deliberately passes bare emails through, so `capability_unsupported`
    messages must be built from the capability name + profile LABEL only,
    never from `profile.base_url` / `profile.id` / any session/user id.

    The test profile's `base_url` deliberately carries a host:port an
    accidental `f"{profile.base_url}"` slip would leak.

    Fix round 1 (Task 8 review, finding 4): the original `uuid_re` here only
    matched the canonical HYPHENATED UUID shape. This codebase's own
    `session.session_id` is `uuid.uuid4().hex` — 32 bare hex chars, NO
    hyphens — so a leak of `session.session_id` into a frame message would
    have matched neither `uuid_re` nor the `"@"` check and passed silently.
    Broadened to also catch a bare 32-char hex run. See
    `test_default_prompt_bytes_unchanged`'s sibling discrimination note in
    the fix-round-1 report for the scratch-copy proof that this now catches
    what the old version missed.
    """
    import json
    import re

    from services import llm_config
    from services.llm_fake import FakeProvider

    profile = llm_config.LLMProfile(
        id="no-vision-2", label="Local Test Profile", preset="custom",
        wire="anthropic", base_url="http://internal.example.org:9999/v1",
        model="claude-sonnet-5", tools=True, vision=False, auth="none",
        fallback_model=None, max_output_tokens=None,
    )
    llm_config.save_profiles([profile], "anthropic-sonnet")

    session = chat_service.ChatSession(model="claude-sonnet-5")
    session.profile_id = "no-vision-2"
    session.append_history_message({
        "role": "user",
        "content": [
            {"type": "image", "source": {"type": "base64",
                                          "media_type": "image/png",
                                          "data": "AAAA"}},
            {"type": "text", "text": "earlier"},
        ],
    })
    session.append_history_message({
        "role": "assistant",
        "content": [{"type": "text", "text": "ok"}],
    })

    fake = FakeProvider([])
    events = list(chat_service.run_turn(session, "again", provider=fake))
    assert fake.requests == []

    hyphenated_uuid_re = re.compile(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    )
    # Fix round 1 (finding 4): this codebase's own identifiers
    # (session_id, session6, and every uuid.uuid4().hex-derived id) are bare
    # hex with NO hyphens — the hyphenated-only regex above would miss a
    # `session.session_id` leak entirely. 32 hex chars is the id length
    # (uuid4().hex); word-bounded so it doesn't false-positive on a longer
    # incidental hex run.
    bare_hex_uuid_re = re.compile(r"\b[0-9a-fA-F]{32}\b")
    error_payload = next(p for n, p in events if n == "error")
    blob = json.dumps(error_payload)
    assert "@" not in blob, blob
    assert not hyphenated_uuid_re.search(blob), blob
    assert not bare_hex_uuid_re.search(blob), blob
    assert "internal.example.org" not in blob, blob
    assert "9999" not in blob, blob


# ─────────────────────────────────────────────────────────────────────────
# Task 8, fix round 1 — independent review findings (chat_service.py half).
# ─────────────────────────────────────────────────────────────────────────


def test_unsupported_image_source_shape_blocked_not_silently_dropped(appdata):
    """
    Finding 1: only a base64-sourced `image` block translates on the openai
    wire (llm_openai_compat._to_openai_messages). A different source shape
    (e.g. a url source) must never reach that translator and get silently
    dropped there — the capability gate refuses it up front, before any
    provider call, exactly like the vision-off and PDF-on-non-anthropic
    checks already do.
    """
    from services import llm_config
    from services.llm_fake import FakeProvider

    profile = llm_config.LLMProfile(
        id="openai-vision-2", label="OpenAI Vision Profile 2", preset="custom",
        wire="openai", base_url="http://localhost:11434/v1", model="qwen3:8b",
        tools=True, vision=True, auth="none", fallback_model=None,
        max_output_tokens=None,
    )
    llm_config.save_profiles([profile], "anthropic-sonnet")

    session = chat_service.ChatSession(model="qwen3:8b")
    session.profile_id = "openai-vision-2"
    session.bound_wire = "openai"
    session.append_history_message({
        "role": "user",
        "content": [
            {"type": "image", "source": {"type": "url",
                                          "url": "https://example.com/x.png"}},
            {"type": "text", "text": "earlier: what is this?"},
        ],
    })
    session.append_history_message({
        "role": "assistant",
        "content": [{"type": "text", "text": "ok"}],
    })

    fake = FakeProvider([])  # must never be reached

    events = list(chat_service.run_turn(session, "and now?", provider=fake))
    names = [n for n, _ in events]
    assert names == ["session_init", "error", "session_done"]
    assert fake.requests == []

    error_payload = next(p for n, p in events if n == "error")
    assert error_payload["error_kind"] == "capability_unsupported"
    assert "OpenAI Vision Profile 2" in error_payload["message"]


def test_openai_vision_true_base64_image_is_not_blocked_by_the_gate(appdata):
    """
    The counterpart to the test above: a base64-sourced image on an
    openai-wire vision:true profile must NOT be refused by the gate — it is
    exactly the shape llm_openai_compat can translate. (The translation
    itself, and that it actually reaches the outbound wire payload as an
    `image_url` part, is proven at the provider-seam level by
    test_openai_compat_translates_base64_image_block_to_image_url in
    test_llm_provider_seam.py — FakeProvider here doesn't translate, it just
    proves the gate lets the turn through.)
    """
    from services import llm_config
    from services.llm_fake import FakeProvider
    from services.llm_provider import LLMEvent

    profile = llm_config.LLMProfile(
        id="openai-vision-3", label="OpenAI Vision Profile 3", preset="custom",
        wire="openai", base_url="http://localhost:11434/v1", model="qwen3:8b",
        tools=True, vision=True, auth="none", fallback_model=None,
        max_output_tokens=None,
    )
    llm_config.save_profiles([profile], "anthropic-sonnet")

    session = chat_service.ChatSession(model="qwen3:8b")
    session.profile_id = "openai-vision-3"
    session.bound_wire = "openai"
    session.append_history_message({
        "role": "user",
        "content": [
            {"type": "image", "source": {"type": "base64",
                                          "media_type": "image/png",
                                          "data": "AAAA"}},
            {"type": "text", "text": "earlier: what is this?"},
        ],
    })
    session.append_history_message({
        "role": "assistant",
        "content": [{"type": "text", "text": "ok"}],
    })

    fake = FakeProvider([{
        "events": [LLMEvent(type="text_delta", text="a diagram")],
        "blocks": [{"type": "text", "text": "a diagram"}],
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }])

    events = list(chat_service.run_turn(session, "and now?", provider=fake))
    names = [n for n, _ in events]
    assert "error" not in names
    assert len(fake.requests) == 1


def test_toolless_prompt_omits_all_tool_names_but_keeps_domain_facts():
    """
    Findings 2 + 3 together: with `include_tools=False` the rendered prompt
    must name ZERO tools — not just the four originally-split guides, but
    `_ASSISTANT_STANCE` too (it names ui_open_panel / ui_select_component /
    ui_open_asset_detail / ui_set_snapshot verbatim; Task 8 shipped claiming
    it carried no tool-chaining instructions, which was false). And the
    degenerate first split for `_SOLVER_ERROR_DECODER` /
    `_NEXT_STEP_RUBRIC` (bare headings, with all real content — the
    symptom→cause table, the rubric — pushed into the tools-only half) must
    be fixed: that domain content is useful to a tools-less model and stays
    present with tools off, even though the tool-calling imperatives that
    reference get_simulation_log_history / get_meta / get_solver_config do
    not.
    """
    prompt = chat_service._build_system_prompt(
        chat_service.ChatSession(), include_tools=False,
    )
    low = prompt.lower()
    for tool_name in (
        "ui_open_panel", "ui_select_component", "ui_open_asset_detail",
        "ui_set_snapshot", "get_simulation_log_history", "get_meta",
        "get_solver_config", "carrier_kpis", "cost_breakdown",
        "price_drivers", "line_duals", "upload_timeseries",
        "upload_load_profile", "upload_generator_profile",
        "generate_exemplary_timeseries",
        # Task 10 — the profile-awareness block is CHAINING-only (names
        # set_active_profile verbatim) and must vanish with tools off, same
        # as every other tool-naming guide.
        "set_active_profile",
    ):
        assert tool_name not in low, f"tools-off prompt leaks {tool_name!r}"
    # Domain content this review found was wrongly dropped must survive.
    assert "infeasible" in low
    assert "dim_0" in low
    assert "assign_duals" in low
    assert "clustering" in low or "sector coupling" in low or "co2 cap" in low
    assert "myopic" in low and "perfect" in low


# ─────────────────────────────────────────────────────────────────────────
# Task 10 — `set_active_profile` tool: confirmation-gated switching + the
# read-side profile-awareness prompt block. Spec:
# .superpowers/sdd/2026-08-14-llm-provider-config-and-switching/
# task-10-brief.md.
# ─────────────────────────────────────────────────────────────────────────


def test_set_active_profile_safety_tier_is_destructive():
    """
    `set_active_profile` must resolve to the `destructive` tier via
    `_safety_tier_for` — asserting the TIER itself (not merely that SOME
    confirmation card appeared) so a lost or mistyped `Safety:` marker on
    this tool fails this test loudly instead of silently falling back to
    the fail-open "read" default (no confirmation card at all).
    """
    assert chat_service._safety_tier_for("set_active_profile") == "destructive"


def _drive_destructive_turn(session, fake, message):
    """
    Run `chat_service.run_turn` on a background thread and block until a
    confirmation token appears in `session.pending_confirmations`. Mirrors
    `test_destructive_tool_blocks_until_approve` in test_chat_e2e.py — the
    canonical deny/approve drive pattern for a destructive-tier tool.

    Returns `(thread, streamed_events, done_event, token)`; the caller
    records a decision via `session.record_decision(token, ...)`, then
    `done_event.wait(...)` + `thread.join()`.
    """
    import contextvars
    import threading
    import time as _t

    streamed: list[tuple[str, dict]] = []
    done = threading.Event()

    def _run():
        for event in chat_service.run_turn(session, message, provider=fake):
            streamed.append(event)
        done.set()

    # Carry the acting-user contextvar across the thread boundary.
    #
    # conftest's autouse `_acting_user` fixture sets `_ACTING_USER_ID` on the
    # PYTEST thread. A manually-created `threading.Thread` starts with a FRESH
    # context, so without this copy any tool that calls `_acting()` sees no
    # acting user and fails closed with 401 `no_acting_user` — which is what
    # `set_active_profile`'s authorization check does, correctly. Production
    # has the same hazard and solves it the same way: `chat_service`'s tool
    # dispatch does `contextvars.copy_context()` before submitting to its
    # executor (see the comment there naming the identical failure).
    _ctx = contextvars.copy_context()
    thread = threading.Thread(target=lambda: _ctx.run(_run))
    thread.start()

    deadline = _t.monotonic() + 3.0
    token = None
    while _t.monotonic() < deadline:
        with session._lock:
            if session.pending_confirmations:
                token = next(iter(session.pending_confirmations))
                break
        _t.sleep(0.02)
    assert token, "pending confirmation token never appeared"
    return thread, streamed, done, token


@pytest.fixture()
def acting_super_admin(seeded_identity, _auth_db):
    """
    Promote the seeded acting user to super-admin for one test, then restore.

    The seeded identity is `is_super_admin=False` (conftest.py) — deliberately,
    since most tools are member-level. `set_active_profile` is NOT: it changes
    an INSTANCE-WIDE setting, so it carries the same super-admin gate as its
    HTTP twin `POST /chat/settings/llm/active`. Tests that exercise a
    SUCCESSFUL switch therefore have to say so explicitly, which is the point —
    the first cut of this tool had no role check at all and these very tests
    passed as a non-admin, which is exactly the privilege escalation review
    caught.
    """
    from db.models import User
    _engine, session_local = _auth_db
    with session_local() as db:
        user = db.get(User, seeded_identity["user_id"])
        was = user.is_super_admin
        user.is_super_admin = True
        db.commit()
    try:
        yield
    finally:
        with session_local() as db:
            user = db.get(User, seeded_identity["user_id"])
            user.is_super_admin = was
            db.commit()


def test_set_active_profile_refused_for_a_non_super_admin(appdata, monkeypatch):
    """
    THE AUTHORIZATION BOUNDARY. A plain member must not be able to change an
    instance-wide setting by asking the model and approving their own card.

    Confirmation-gating is not a role check: it stops the MODEL acting
    unintendedly, and `POST /{session_id}/confirm` validates only a
    session-scoped token — not who is clicking. Without the handler's own
    `is_super_admin` check this test passes a switch through, which is how
    the escalation existed.
    """
    from services import llm_config
    from services.llm_fake import FakeProvider
    from services.llm_provider import LLMEvent

    monkeypatch.setattr(chat_service, "CONFIRMATION_TTL_SECONDS", 1.0)
    assert llm_config.resolve_active().id == "anthropic-sonnet"

    session = chat_service.ChatSession(model=llm_config.DEFAULT_MODEL)
    fake = FakeProvider([
        {"events": [LLMEvent(type="tool_use_start", tool_use_id="t1",
                             tool_name="set_active_profile")],
         "blocks": [{"type": "tool_use", "id": "t1",
                     "name": "set_active_profile",
                     "input": {"profile_id": "anthropic-opus"}}],
         "usage": {}},
        {"events": [], "blocks": [{"type": "text", "text": "refused"}],
         "usage": {}},
    ])
    thread, streamed, done, token = _drive_destructive_turn(
        session, fake, "switch to opus",
    )
    session.record_decision(token, "approve")
    done.wait(5.0)
    thread.join()

    errs = [p for n, p in streamed if n == "tool_error"]
    assert any(e["error_kind"] == "not_authorized" for e in errs), (
        f"expected a not_authorized tool_error, got {errs!r}"
    )
    # The boundary held: the instance-wide setting is untouched.
    assert llm_config.resolve_active().id == "anthropic-sonnet"


def test_set_active_profile_approve_switches_active(
    appdata, monkeypatch, acting_super_admin,
):
    """Approve (as a super-admin) -> `llm_config.resolve_active().id` changes."""
    from services import llm_config
    from services.llm_fake import FakeProvider
    from services.llm_provider import LLMEvent

    monkeypatch.setattr(chat_service, "CONFIRMATION_TTL_SECONDS", 1.0)
    assert llm_config.resolve_active().id == "anthropic-sonnet"

    session = chat_service.ChatSession()
    fake = FakeProvider([
        {
            "events": [LLMEvent(type="tool_use_start", tool_use_id="tu-sap-1",
                                 tool_name="set_active_profile")],
            "blocks": [{"type": "tool_use", "id": "tu-sap-1",
                        "name": "set_active_profile",
                        "input": {"profile_id": "anthropic-opus"}}],
            "usage": {"input_tokens": 5, "output_tokens": 5},
        },
        {
            "events": [LLMEvent(type="text_delta", text="switched.")],
            "blocks": [{"type": "text", "text": "switched."}],
            "usage": {"input_tokens": 3, "output_tokens": 2},
        },
    ])

    thread, streamed, done, token = _drive_destructive_turn(
        session, fake, "switch to opus",
    )
    session.record_decision(token, "approve")
    done.wait(5.0)
    thread.join()

    names = [n for n, _ in streamed]
    assert "tool_pending_confirmation" in names
    assert "tool_result" in names
    result = next(p for n, p in streamed if n == "tool_result")
    assert result["result"]["ok"] is True
    assert result["result"]["active_profile_id"] == "anthropic-opus"
    assert llm_config.resolve_active().id == "anthropic-opus"


def test_set_active_profile_deny_leaves_active_unchanged(appdata, monkeypatch):
    """Deny -> the dispatcher never runs; the active profile is untouched."""
    from services import llm_config
    from services.llm_fake import FakeProvider
    from services.llm_provider import LLMEvent

    monkeypatch.setattr(chat_service, "CONFIRMATION_TTL_SECONDS", 1.0)
    assert llm_config.resolve_active().id == "anthropic-sonnet"

    session = chat_service.ChatSession()
    fake = FakeProvider([
        {
            "events": [LLMEvent(type="tool_use_start", tool_use_id="tu-sap-2",
                                 tool_name="set_active_profile")],
            "blocks": [{"type": "tool_use", "id": "tu-sap-2",
                        "name": "set_active_profile",
                        "input": {"profile_id": "anthropic-opus"}}],
            "usage": {"input_tokens": 5, "output_tokens": 5},
        },
        {
            "events": [LLMEvent(type="text_delta", text="ok, cancelled.")],
            "blocks": [{"type": "text", "text": "ok, cancelled."}],
            "usage": {"input_tokens": 3, "output_tokens": 2},
        },
    ])

    thread, streamed, done, token = _drive_destructive_turn(
        session, fake, "switch to opus",
    )
    session.record_decision(token, "deny")
    done.wait(5.0)
    thread.join()

    errs = [p for n, p in streamed if n == "tool_error"]
    assert any(e["error_kind"] == "confirmation_denied" for e in errs)
    assert llm_config.resolve_active().id == "anthropic-sonnet"


def test_set_active_profile_unknown_id_is_structured_tool_error(
    appdata, monkeypatch, acting_super_admin,
):
    """
    Unknown `profile_id` -> approve dispatches the handler, which raises a
    structured `HTTPException`; the turn surfaces
    `error_kind='unknown_profile_id'` as a `tool_error`, never an escaping
    exception, and the active profile is left unchanged.
    """
    from services import llm_config
    from services.llm_fake import FakeProvider
    from services.llm_provider import LLMEvent

    monkeypatch.setattr(chat_service, "CONFIRMATION_TTL_SECONDS", 1.0)
    assert llm_config.resolve_active().id == "anthropic-sonnet"

    session = chat_service.ChatSession()
    fake = FakeProvider([
        {
            "events": [LLMEvent(type="tool_use_start", tool_use_id="tu-sap-3",
                                 tool_name="set_active_profile")],
            "blocks": [{"type": "tool_use", "id": "tu-sap-3",
                        "name": "set_active_profile",
                        "input": {"profile_id": "does-not-exist"}}],
            "usage": {"input_tokens": 5, "output_tokens": 5},
        },
        {
            "events": [LLMEvent(type="text_delta", text="couldn't find that profile.")],
            "blocks": [{"type": "text", "text": "couldn't find that profile."}],
            "usage": {"input_tokens": 3, "output_tokens": 2},
        },
    ])

    thread, streamed, done, token = _drive_destructive_turn(
        session, fake, "switch to a bogus profile",
    )
    session.record_decision(token, "approve")
    done.wait(5.0)
    thread.join()

    errs = [p for n, p in streamed if n == "tool_error"]
    assert any(e["error_kind"] == "unknown_profile_id" for e in errs)
    assert llm_config.resolve_active().id == "anthropic-sonnet"


def test_system_prompt_names_active_profile_and_configured_labels(appdata):
    """
    Tools-on read-side awareness (Task 10): the rendered system prompt
    names the ACTIVE profile's label, the OTHER configured profile's label,
    and the `set_active_profile` switching procedure.
    """
    from services import llm_config

    assert llm_config.resolve_active().id == "anthropic-sonnet"
    prompt = chat_service._build_system_prompt(chat_service.ChatSession())
    assert "Claude Sonnet" in prompt  # active builtin's label
    assert "Claude Opus" in prompt  # the other configured profile's label
    assert "set_active_profile" in prompt
    assert "new chat" in prompt.lower()


# ─────────────────────────────────────────────────────────────────────────
# C-1 — `tools: false` was ADVERTISED but never ENFORCED.
#
# `profile.tools` was read only on OUTBOUND paths (request build, prompt
# trim, cache annotation). The dispatch loop iterated `tool_uses` with no
# capability check, so an endpoint that returns `tool_use` blocks despite
# being sent `tools=[]` had them executed — 87 of the 121 tools without a
# confirmation card, 31 of which mutate the user's projects, with every
# result streamed back to that endpoint.
#
# This is not hypothetical on this branch: the headline feature is pointing
# the assistant at an arbitrary endpoint, which makes the endpoint an
# attacker-controlled input. `_validate_base_url` also accepts plain `http`,
# so a MITM reaches it too.
#
# The guard is "was this tool actually OFFERED this turn", not merely
# "is profile.tools true" — an allowlist over the payload we really sent,
# so a hostile endpoint cannot invent a tool name either. That is the
# sibling path: a `tools: true` profile naming a tool that is not in the
# catalogue must be refused by the same predicate.
# ─────────────────────────────────────────────────────────────────────────


def _toolless_profile_session(appdata, *, tools: bool):
    from services import llm_config
    profile = llm_config.LLMProfile(
        id="wire-test", label="Wire Test", preset="custom",
        wire="anthropic", base_url=None, model="claude-sonnet-5",
        tools=tools, vision=True, auth="none", fallback_model=None,
        max_output_tokens=None,
    )
    llm_config.save_profiles([profile], "anthropic-sonnet")
    session = chat_service.ChatSession(model="claude-sonnet-5")
    session.profile_id = "wire-test"
    return session


def _fake_returning_tool_use(tool_name: str, tool_input: dict):
    from services.llm_fake import FakeProvider
    from services.llm_provider import LLMEvent
    return FakeProvider([
        {"events": [LLMEvent(type="tool_use_start", tool_use_id="tu-1",
                             tool_name=tool_name)],
         "blocks": [{"type": "tool_use", "id": "tu-1",
                     "name": tool_name, "input": tool_input}],
         "usage": {"input_tokens": 1, "output_tokens": 1}},
        {"events": [LLMEvent(type="text_delta", text="done")],
         "blocks": [{"type": "text", "text": "done"}],
         "usage": {"input_tokens": 1, "output_tokens": 1}},
    ])


def test_toolless_profile_refuses_a_tool_use_the_endpoint_invented(
    appdata, monkeypatch,
):
    """
    THE CAPABILITY BOUNDARY. `tools: false` -> `tools=[]` was sent, so ANY
    `tool_use` coming back was never offered and must not be dispatched.
    """
    from services import chat_tools

    called: list[str] = []
    monkeypatch.setitem(
        chat_tools.DISPATCHERS, "get_meta",
        lambda *a, **k: called.append("ran") or {"ok": True},
    )

    session = _toolless_profile_session(appdata, tools=False)
    fake = _fake_returning_tool_use("get_meta", {})
    events = list(chat_service.run_turn(session, "hi", provider=fake))

    assert called == [], (
        "a tool the profile was never offered was DISPATCHED — this is the "
        "capability bypass C-1 describes"
    )
    errs = [p for n, p in events if n == "tool_error"]
    assert any(e.get("error_kind") == "tool_not_offered" for e in errs), (
        f"expected a typed tool_not_offered frame, got {errs!r}"
    )


def test_a_tools_enabled_profile_still_refuses_an_unknown_tool_name(
    appdata,
):
    """
    THE SIBLING PATH. The guard is an allowlist over what was actually sent,
    so it must also refuse a name that is not in the catalogue at all — the
    case a `profile.tools`-only check would wave straight through.
    """
    session = _toolless_profile_session(appdata, tools=True)
    fake = _fake_returning_tool_use("exfiltrate_everything", {})
    events = list(chat_service.run_turn(session, "hi", provider=fake))

    errs = [p for n, p in events if n == "tool_error"]
    assert any(e.get("error_kind") == "tool_not_offered" for e in errs), (
        f"expected a typed tool_not_offered frame, got {errs!r}"
    )


def test_a_tools_enabled_profile_still_runs_an_offered_tool(appdata, monkeypatch):
    """
    DISCRIMINATION. The guard must not refuse the normal case — otherwise the
    two tests above would pass against a dispatcher that refuses everything.
    """
    from services import chat_tools

    called: list[str] = []
    monkeypatch.setitem(
        chat_tools.DISPATCHERS, "get_meta",
        lambda *a, **k: called.append("ran") or {"ok": True},
    )

    session = _toolless_profile_session(appdata, tools=True)
    fake = _fake_returning_tool_use("get_meta", {})
    events = list(chat_service.run_turn(session, "hi", provider=fake))

    errs = [p for n, p in events if n == "tool_error"]
    assert not any(e.get("error_kind") == "tool_not_offered" for e in errs), (
        f"an OFFERED tool was refused as not-offered: {errs!r}"
    )
    assert called == ["ran"], "the offered tool did not run"


# ─────────────────────────────────────────────────────────────────────────
# C-4 — an unknown or deleted profile_id ran the turn on a DIFFERENT
# provider, wire and model, with no error frame.
#
# `resolve_profile` fell through to `by_id[active_id]` for any id it did not
# recognise. Its docstring called that intended, but `frontend/src/api/chat.ts`
# states the contract as "the server resolves it and refuses an unconfigured
# id" — a disproven claim written into source as fact.
#
# The user impact is the ADR-0001 shape at its worst: unresolvable renders as
# SUCCESS. A user picks `local-ollama` believing their data stays on
# localhost; the id no longer exists, so the prompt goes to whatever profile
# is active — `api.moonshot.ai`, say — and every frame says the turn
# succeeded.
#
# An OMITTED profile_id keeps meaning "the server's active profile", and a
# free-text legacy `model` keeps its documented passthrough. Only an
# EXPLICIT id that names nothing is refused.
# ─────────────────────────────────────────────────────────────────────────


def test_resolve_profile_refuses_an_unconfigured_id(appdata):
    """The predicate itself: an explicit id that names nothing must raise."""
    from services import llm_config
    with pytest.raises(llm_config.ProfileNotConfiguredError):
        llm_config.resolve_profile("no-such-profile")


def test_resolve_profile_still_falls_back_when_no_id_is_given(appdata):
    """SIBLING PATH — `None` still means "the active profile" (zero-config)."""
    from services import llm_config
    assert llm_config.resolve_profile(None).id == "anthropic-sonnet"
    assert llm_config.resolve_active().id == "anthropic-sonnet"


def test_legacy_model_passthrough_is_unchanged(appdata):
    """
    SIBLING PATH — free-text `model` is a documented passthrough contract and
    must still resolve to the active profile with a warning, NOT raise.
    """
    from services import llm_config
    assert llm_config.resolve_legacy_model("some-unknown-model").id == "anthropic-sonnet"
    assert llm_config.resolve_legacy_model(None).id == "anthropic-sonnet"
    assert llm_config.resolve_legacy_model(
        llm_config.OPUS_MODEL).id == "anthropic-opus"


def test_stream_refuses_an_unconfigured_profile_id_with_a_typed_frame(
    appdata, client,
):
    """
    THE USER-VISIBLE BOUNDARY. An explicit unconfigured id must produce a
    typed error frame and run NO turn — never a silent switch to whatever
    profile happens to be active.
    """
    resp = client.post(
        "/api/chat/stream",
        json={
            "session_id": "sess-unknown-profile",
            "profile_id": "deleted-profile",
            "script": [{"type": "session_done"}],
        },
    )
    assert resp.status_code == 200  # a non-2xx SSE body is discarded client-side
    frames = _parse_sse(resp.content)
    kinds = [p.get("error_kind") for n, p in frames if n == "error"]
    assert "unknown_profile_id" in kinds, (
        f"expected a typed unknown_profile_id frame, got {frames!r}"
    )
    # The session must NOT have been bound to the active profile behind the
    # user's back — that silent substitution is the whole defect.
    sess = chat_service.get_session("sess-unknown-profile")
    assert sess is None or sess.profile_id != "anthropic-sonnet", (
        "the turn was silently rebound to the active profile"
    )


def test_stream_with_no_profile_id_still_runs_normally(appdata, client):
    """
    DISCRIMINATION. Refusing an unconfigured id must not refuse the ordinary
    zero-config turn, which sends no profile_id at all.
    """
    resp = client.post(
        "/api/chat/stream",
        json={
            "session_id": "sess-still-fine",
            "script": [{"type": "session_done"}],
        },
    )
    assert resp.status_code == 200
    frames = _parse_sse(resp.content)
    kinds = [p.get("error_kind") for n, p in frames if n == "error"]
    assert "unknown_profile_id" not in kinds, f"zero-config turn refused: {frames!r}"
    assert frames[0][0] == "session_init"
    assert frames[0][1]["profile_id"] == "anthropic-sonnet"


# ═════════════════════════════════════════════════════════════════════════
# TWO TURNS THROUGH THE ROUTER — the seam the closing review named.
#
# The review's own closing lesson: Task 7's A8 hoist is tested by calling
# `chat_service` directly, and Task 7's router binding is tested by driving
# `/stream`. Both green. NEITHER CROSSES. C-8, C-9 and C-12 are all that one
# shape — state a first turn establishes, silently undone by the second.
#
# Every test below drives TWO real `/stream` requests against the same
# session, because one request cannot see any of these defects.
# ═════════════════════════════════════════════════════════════════════════


@pytest.fixture()
def _fast_retries(monkeypatch):
    monkeypatch.setattr(chat_service, "BASE_STREAM_RETRY_DELAY", 0.0)
    monkeypatch.setattr(chat_service, "MAX_STREAM_RETRY_DELAY", 0.0)
    monkeypatch.setattr(chat_service, "MAX_STREAM_RETRIES", 2)


def _ok_turn(text="ok"):
    from services.llm_provider import LLMEvent
    return {"events": [LLMEvent(type="text_delta", text=text)],
            "blocks": [{"type": "text", "text": text}],
            "usage": {"input_tokens": 1, "output_tokens": 1}}


def _rate_limited():
    from services.llm_provider import ProviderError
    return ProviderError("rate_limited", "429 slow down")


def test_a8_fallback_survives_the_next_turn(
    appdata, client, fake_provider_for_profile, _fast_retries,
):
    """
    C-8. Turn 1 rate-limits on Opus and the user is TOLD we fell back to
    Sonnet. Turn 2 — sending neither `profile_id` nor `model`, which is what
    the frontend actually does — must not silently put them back on the
    rate-limited model.

    The router assigned `session.model = target_profile.model` on EVERY turn,
    where master did it only `if body.model:`. So the fallback lasted exactly
    one turn and the next request went straight back to Opus.
    """
    # The instance-wide active profile IS Opus — the realistic case, and the
    # one that discriminates: with the default (Sonnet) active, a rebind on
    # turn 2 lands on Sonnet by COINCIDENCE and hides the defect entirely.
    from services import llm_config
    llm_config.set_active("anthropic-opus")

    # Turn 1: bind Opus explicitly, exhaust retries, take the A8 fallback.
    fake_provider_for_profile(
        [_rate_limited(), _rate_limited(), _rate_limited(), _ok_turn("fell back")]
    )
    r1 = client.post("/api/chat/stream", json={
        "session_id": "sess-a8", "profile_id": "anthropic-opus",
        "message": "hello",
    })
    assert r1.status_code == 200
    kinds = [n for n, _ in _parse_sse(r1.content)]
    assert "model_fallback" in kinds, f"A8 never fired: {kinds}"

    sess = chat_service.get_session("sess-a8")
    assert sess.model == chat_service.DEFAULT_MODEL, "A8 did not switch the model"

    # Turn 2: the real client sends neither field.
    provider = fake_provider_for_profile([_ok_turn("second")])
    r2 = client.post("/api/chat/stream", json={
        "session_id": "sess-a8", "message": "again",
    })
    assert r2.status_code == 200

    assert sess.model == chat_service.DEFAULT_MODEL, (
        "the A8 fallback was undone by the next turn — the user was told they "
        "were moved off the rate-limited model and then sent back to it"
    )
    assert provider.requests[0].model == chat_service.DEFAULT_MODEL, (
        f"turn 2 was actually SENT to {provider.requests[0].model!r}"
    )


def test_omitting_profile_id_does_not_repoint_a_bound_session(
    appdata, client, fake_provider_for_profile,
):
    """
    C-9. `frontend/src/api/chat.ts` documents omitting `profile_id` as meaning
    "the server's active profile", and warns that sending one unconditionally
    "would re-assert a stale choice every turn". `set_active_profile`'s own
    note promises the opposite direction too: "This chat stays on the model it
    started with — start a new chat to use it."

    Both are broken by the same line. With `profile_id` omitted the router
    resolved the INSTANCE-WIDE active profile and rebound the session to it,
    so an admin flipping the active profile silently moved every chat already
    in flight.
    """
    from services import llm_config

    fake_provider_for_profile([_ok_turn()])
    client.post("/api/chat/stream", json={
        "session_id": "sess-c9", "profile_id": "anthropic-opus",
        "message": "first",
    })
    sess = chat_service.get_session("sess-c9")
    assert sess.profile_id == "anthropic-opus"

    # A super-admin flips the instance-wide active profile mid-conversation.
    llm_config.set_active("anthropic-sonnet")

    provider = fake_provider_for_profile([_ok_turn()])
    client.post("/api/chat/stream", json={
        "session_id": "sess-c9", "message": "second",
    })

    assert sess.profile_id == "anthropic-opus", (
        "the running chat was re-pointed at the newly-active profile, which "
        "is exactly what set_active_profile promises does NOT happen"
    )
    assert provider.requests[0].model == chat_service.OPUS_MODEL, (
        f"turn 2 was sent to {provider.requests[0].model!r}, not the profile "
        f"this session is bound to"
    )


def test_an_explicit_profile_id_still_rebinds_on_the_same_wire(
    appdata, client, fake_provider_for_profile,
):
    """
    DISCRIMINATION. The two tests above must not be satisfiable by a router
    that simply stops rebinding. An EXPLICIT same-wire `profile_id` on turn 2
    is a deliberate user choice and must still take effect.
    """
    fake_provider_for_profile([_ok_turn()])
    client.post("/api/chat/stream", json={
        "session_id": "sess-rebind", "profile_id": "anthropic-sonnet",
        "message": "first",
    })
    sess = chat_service.get_session("sess-rebind")
    assert sess.profile_id == "anthropic-sonnet"

    provider = fake_provider_for_profile([_ok_turn()])
    client.post("/api/chat/stream", json={
        "session_id": "sess-rebind", "profile_id": "anthropic-opus",
        "message": "second",
    })
    assert sess.profile_id == "anthropic-opus", "an explicit switch was ignored"
    assert provider.requests[0].model == chat_service.OPUS_MODEL


def test_an_explicit_legacy_model_still_takes_effect_on_turn_two(
    appdata, client, fake_provider_for_profile,
):
    """
    DISCRIMINATION, legacy half. `model` is a documented free-text passthrough
    an older client may still send; naming one explicitly must still switch.
    """
    fake_provider_for_profile([_ok_turn()])
    client.post("/api/chat/stream", json={
        "session_id": "sess-legacy", "message": "first",
    })
    sess = chat_service.get_session("sess-legacy")
    assert sess.model == chat_service.DEFAULT_MODEL

    provider = fake_provider_for_profile([_ok_turn()])
    client.post("/api/chat/stream", json={
        "session_id": "sess-legacy", "model": chat_service.OPUS_MODEL,
        "message": "second",
    })
    assert sess.model == chat_service.OPUS_MODEL, "an explicit model was ignored"
    assert provider.requests[0].model == chat_service.OPUS_MODEL


def test_a_first_turn_with_nothing_named_still_binds_the_active_profile(
    appdata, client, fake_provider_for_profile,
):
    """
    DISCRIMINATION, zero-config half. An UNBOUND session naming nothing must
    still adopt the active profile — this is the default path every existing
    chat takes, and hard constraint 2 says it must not change.
    """
    from services import llm_config
    llm_config.set_active("anthropic-opus")

    provider = fake_provider_for_profile([_ok_turn()])
    client.post("/api/chat/stream", json={
        "session_id": "sess-fresh", "message": "first",
    })
    sess = chat_service.get_session("sess-fresh")
    assert sess.profile_id == "anthropic-opus"
    assert provider.requests[0].model == chat_service.OPUS_MODEL


# ═════════════════════════════════════════════════════════════════════════
# Defects found by the adversarial security pass IN THE FIXES ABOVE.
# Each is a case the fix's own tests could not see.
# ═════════════════════════════════════════════════════════════════════════


class _EndlessToolProvider:
    """An endpoint that answers every request with the same tool_use block."""

    name = "endless"

    def __init__(self, tool_name: str):
        self._tool_name = tool_name
        self.calls = 0

    def stream(self, request):
        from services.llm_provider import LLMEvent
        self.calls += 1
        yield LLMEvent(type="tool_use_start", tool_use_id=f"t{self.calls}",
                       tool_name=self._tool_name)
        yield LLMEvent(
            type="message_done",
            blocks=[{"type": "tool_use", "id": f"t{self.calls}",
                     "name": self._tool_name, "input": {}}],
            usage={"input_tokens": 1, "output_tokens": 1},
        )


def test_a_refused_tool_still_consumes_the_turn_budget(appdata):
    """
    F1 (HIGH) — the C-1 refusal `continue`d BEFORE `tool_call_count += 1`,
    justified in its own comment as "an unoffered tool must not be able to
    consume the turn's budget". That comment was the bug: the refusal is
    inside the agentic `while True:`, so an endpoint that answers every
    request with an unoffered tool_use drove the loop forever — re-sending
    the whole growing conversation, and the Authorization header with it, on
    every iteration.

    This REMOVED a bound that existed before the fix: an unknown tool name
    used to fall through to the counter and hit MAX_TOOL_CALLS_PER_TURN.
    """
    from services import llm_config

    profile = llm_config.LLMProfile(
        id="toolless-x", label="Toolless", preset="custom", wire="anthropic",
        base_url=None, model="claude-sonnet-5", tools=False, vision=True,
        auth="none", fallback_model=None, max_output_tokens=None)
    llm_config.save_profiles([profile], "anthropic-sonnet")

    session = chat_service.ChatSession(model="claude-sonnet-5")
    session.profile_id = "toolless-x"
    provider = _EndlessToolProvider("get_meta")

    events = []
    for event in chat_service.run_turn(session, "hi", provider=provider):
        events.append(event)
        if len(events) > 400:  # a bounded turn never gets here
            break

    assert provider.calls <= chat_service.MAX_TOOL_CALLS_PER_TURN + 2, (
        f"the turn never terminated — {provider.calls} calls to the endpoint, "
        f"each re-sending the conversation and the auth header"
    )
    assert events[-1][0] == "session_done", (
        f"turn did not end cleanly; last frame was {events[-1][0]!r}"
    )


def test_the_turn_profile_reaches_a_tool_dispatched_through_the_router(
    appdata, client, fake_provider_for_profile, monkeypatch,
):
    """
    F2 — the C-3 contextvar was DEAD in production.

    `_run_turn_body` runs inside the sync generator Starlette drives with
    `iterate_in_threadpool`, which — as `routers/chat.py` says in its own
    comment about `set_acting_user` — "copies the task context afresh for
    EVERY yielded item". A `set()` inside one `next()` lands on that item's
    throwaway copy, so every later frame, and therefore every tool dispatch,
    read `None`.

    So `reconstruct_network_from_image`'s capability refusal was unreachable
    and it kept building an Anthropic client with DEFAULT_MODEL — the exact
    silent cross-provider image egress C-3 claims to prevent. C-3's own tests
    missed it because they drive `run_turn` with `list(...)` in one thread,
    which never crosses the threadpool boundary.

    Asserted at the MECHANISM, through the real router, so it holds for any
    future tool that reads the turn profile — not just the vision one.
    """
    from services import chat_tools
    from services.llm_provider import LLMEvent

    seen: list = []
    monkeypatch.setitem(
        chat_tools.DISPATCHERS, "get_meta",
        lambda *a, **k: (seen.append(chat_tools.turn_profile()), {"ok": True})[1],
    )

    fake_provider_for_profile([
        {"events": [LLMEvent(type="tool_use_start", tool_use_id="t1",
                             tool_name="get_meta")],
         "blocks": [{"type": "tool_use", "id": "t1", "name": "get_meta",
                     "input": {}}],
         "usage": {"input_tokens": 1, "output_tokens": 1}},
        {"events": [LLMEvent(type="text_delta", text="done")],
         "blocks": [{"type": "text", "text": "done"}],
         "usage": {"input_tokens": 1, "output_tokens": 1}},
    ])

    resp = client.post("/api/chat/stream", json={
        "session_id": "sess-ctxvar", "profile_id": "anthropic-opus",
        "message": "read the meta",
    })
    assert resp.status_code == 200
    assert seen, "the tool never ran, so this test proves nothing"
    assert seen[0] is not None, (
        "the tool saw NO turn profile — the contextvar does not survive "
        "Starlette's per-item context copy, so every capability check keyed "
        "on it is dead in production"
    )
    assert seen[0].id == "anthropic-opus"


def test_a_session_bound_to_a_deleted_profile_refuses_cleanly(
    appdata, client, fake_provider_for_profile,
):
    """
    F4 — C-4 taught `resolve_profile` to raise and taught the ROUTER to catch,
    but only for `body.profile_id`. It left `_resolve_turn_profile`, which
    resolves `session.profile_id`, unguarded — and per the C-8/C-9 change in
    the same commit a bound session normally sends NO `profile_id`, so this is
    the common path, not an edge case.

    A super-admin deleting a profile therefore turned every chat bound to it
    into an `internal_error` with the profile id echoed back, instead of the
    typed `unknown_profile_id` frame with guidance that C-4 built.
    """
    from services import llm_config

    profile = llm_config.LLMProfile(
        id="doomed", label="Doomed", preset="custom", wire="anthropic",
        base_url=None, model="claude-sonnet-5", tools=False, vision=True,
        auth="none", fallback_model=None, max_output_tokens=None)
    llm_config.save_profiles([profile], "anthropic-sonnet")

    fake_provider_for_profile([_ok_turn()])
    client.post("/api/chat/stream", json={
        "session_id": "sess-doomed", "profile_id": "doomed", "message": "first",
    })
    assert chat_service.get_session("sess-doomed").profile_id == "doomed"

    # The super-admin deletes it. The chat is still open and still bound.
    llm_config.save_profiles([], "anthropic-sonnet")

    r2 = client.post("/api/chat/stream", json={
        "session_id": "sess-doomed", "message": "second",
    })
    assert r2.status_code == 200
    frames = _parse_sse(r2.content)
    kinds = [p.get("error_kind") for n, p in frames if n == "error"]
    assert "unknown_profile_id" in kinds, (
        f"expected the typed refusal C-4 built, got {frames!r}"
    )
    assert "internal_error" not in kinds
    # And the id must not be echoed back to the client in the message.
    for _n, p in frames:
        assert "doomed" not in str(p.get("message", ""))


class _EndlessParallelDestructiveProvider:
    """Answers every request with TWO destructive tool calls."""

    name = "endless-parallel"

    def __init__(self):
        self.calls = 0

    def stream(self, request):
        from services.llm_provider import LLMEvent
        self.calls += 1
        n = self.calls
        blocks = [
            {"type": "tool_use", "id": f"d{n}a", "name": "delete_project",
             "input": {"name": "a"}},
            {"type": "tool_use", "id": f"d{n}b", "name": "delete_project",
             "input": {"name": "b"}},
        ]
        for b in blocks:
            yield LLMEvent(type="tool_use_start", tool_use_id=b["id"],
                           tool_name=b["name"])
        yield LLMEvent(type="message_done", blocks=blocks,
                       usage={"input_tokens": 1, "output_tokens": 1})


def test_a_refused_parallel_destructive_batch_also_consumes_the_budget(appdata):
    """
    W-2 — the SIBLING of F1, and the one I did not check when fixing F1.

    F1 moved the `tool_not_offered` refusal below `tool_call_count += 1` and
    justified it as "the cap is the only per-turn bound there is; nothing may
    skip it". Twenty lines above, the `parallel_destructive_not_allowed`
    refusal still `continue`d the agentic loop without reaching that counter.

    Nothing executes — the guard does its job — but the turn never ends, and
    every pass re-POSTs the whole growing conversation and the Authorization
    header. This predates the branch; what the branch changed is WHO can
    drive it, since the endpoint is now operator-chosen and `_validate_base_url`
    accepts plain http.
    """
    from services import llm_config

    profile = llm_config.LLMProfile(
        id="par-x", label="Par", preset="custom", wire="anthropic",
        base_url=None, model="claude-sonnet-5", tools=True, vision=True,
        auth="none", fallback_model=None, max_output_tokens=None)
    llm_config.save_profiles([profile], "anthropic-sonnet")

    session = chat_service.ChatSession(model="claude-sonnet-5")
    session.profile_id = "par-x"
    provider = _EndlessParallelDestructiveProvider()

    events = []
    for event in chat_service.run_turn(session, "delete both", provider=provider):
        events.append(event)
        if len(events) > 400:
            break

    assert provider.calls <= chat_service.MAX_TOOL_CALLS_PER_TURN + 2, (
        f"the turn never terminated — {provider.calls} calls to the endpoint"
    )
    assert events[-1][0] == "session_done"
