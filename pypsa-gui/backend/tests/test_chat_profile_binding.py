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
    `_X_FACTS + _X_CHAINING == <the pre-split constant>` for all five prompt
    guides, so the DEFAULT (tools-enabled) assembled system prompt stays
    byte-identical to what `test_chat_e2e.py`'s prompt pins already assert
    — for `_DOMAIN_GUIDE`, `_PRICE_CONGESTION_GUIDE`, and `_ASSISTANT_STANCE`
    (added fix round 1, finding 2; its split is a clean suffix of the
    original, no reordering needed).

    `_SOLVER_ERROR_DECODER` and `_NEXT_STEP_RUBRIC` are the fix round 1
    (Task 8 review, finding 3) exception: their pre-Task-8 wording put a
    tool-naming imperative BEFORE the domain content (the symptom→cause
    table / the rubric), so the original prefix/suffix split (matching HEAD
    exactly) could only put a bare heading in FACTS — throwing away useful,
    tool-independent content for a tools-off profile. Fixed by reordering
    the underlying text (tool imperative moved to the end, every original
    word kept); the hashes for these two are pinned against that NEW
    (reordered) text, not pre-Task-8 HEAD — see the comment above each in
    chat_service.py.

    Hashes for `_DOMAIN_GUIDE` / `_PRICE_CONGESTION_GUIDE` / `_ASSISTANT_STANCE`
    captured at HEAD (32a0949a, before Task 8's split) via:
        pixi run -e test python -c "
        import hashlib
        from services import chat_service as cs
        for n in ['_DOMAIN_GUIDE', '_PRICE_CONGESTION_GUIDE',
                  '_ASSISTANT_STANCE']:
            print(n, hashlib.sha256(getattr(cs, n).encode()).hexdigest())"
    Hashes for `_SOLVER_ERROR_DECODER` / `_NEXT_STEP_RUBRIC` captured against
    the reordered chat_service.py (fix round 1) the same way.
    This is meaningful (not tautological) because every expected hash below
    was computed independently of whatever _X_FACTS/_X_CHAINING end up being
    — either against pre-split HEAD, or against the deliberately-reordered
    text committed alongside these hashes in the same fix.
    """
    import hashlib

    expected_sha256 = {
        "_DOMAIN_GUIDE": (
            "3e6f420d74fea27240186cc520718dd401fd7b18a6e0fef1262630f053256f8f"
        ),
        "_SOLVER_ERROR_DECODER": (
            "ffd6c9919a89fe3a809678857ac6a1e6b53e63c7440cecc33a7efa678b333484"
        ),
        "_PRICE_CONGESTION_GUIDE": (
            "a65668e11eee7e3f30007ffc91cc7f3197e2a6938c7e8ea6e1fd647b5aa25129"
        ),
        "_NEXT_STEP_RUBRIC": (
            "0f02c5356d1da6fa06ffba6c41bd39e3e311acd1ae2c8b3164c28b2402420d57"
        ),
        "_ASSISTANT_STANCE": (
            "1dd77952d0cdec42606c4d269adf31aae9d11ae8ef22cf83103ac0659028889b"
        ),
    }
    halves = {
        "_DOMAIN_GUIDE": (
            chat_service._DOMAIN_GUIDE_FACTS, chat_service._DOMAIN_GUIDE_CHAINING,
        ),
        "_SOLVER_ERROR_DECODER": (
            chat_service._SOLVER_ERROR_DECODER_FACTS,
            chat_service._SOLVER_ERROR_DECODER_CHAINING,
        ),
        "_PRICE_CONGESTION_GUIDE": (
            chat_service._PRICE_CONGESTION_GUIDE_FACTS,
            chat_service._PRICE_CONGESTION_GUIDE_CHAINING,
        ),
        "_NEXT_STEP_RUBRIC": (
            chat_service._NEXT_STEP_RUBRIC_FACTS,
            chat_service._NEXT_STEP_RUBRIC_CHAINING,
        ),
        "_ASSISTANT_STANCE": (
            chat_service._ASSISTANT_STANCE_FACTS,
            chat_service._ASSISTANT_STANCE_CHAINING,
        ),
    }
    for name, (facts, chaining) in halves.items():
        combined = facts + chaining
        actual_hash = hashlib.sha256(combined.encode()).hexdigest()
        assert actual_hash == expected_sha256[name], (
            f"{name}_FACTS + {name}_CHAINING no longer reconstructs the "
            f"pinned constant byte-for-byte"
        )
        # The module-level `_X = _X_FACTS + _X_CHAINING` assembly itself.
        assert combined == getattr(chat_service, name)


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
    ):
        assert tool_name not in low, f"tools-off prompt leaks {tool_name!r}"
    # Domain content this review found was wrongly dropped must survive.
    assert "infeasible" in low
    assert "dim_0" in low
    assert "assign_duals" in low
    assert "clustering" in low or "sector coupling" in low or "co2 cap" in low
    assert "myopic" in low and "perfect" in low
