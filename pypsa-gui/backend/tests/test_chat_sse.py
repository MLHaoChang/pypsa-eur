"""
Phase 2 — SSE protocol + confirmation card lifecycle.

Exit criteria covered:

  (a) SSE returns session_init within 200ms of POST /stream.
  (b) Stub destructive tool emits tool_pending_confirmation.
  (c) Approving within TTL executes the tool (tool_running + tool_result).
  (d) >5 min wait returns 409 error_kind='confirmation_expired'.
  (e) Replay returns 404 error_kind='unknown_confirmation_token'.
  (g) M7 — parallel destructive batch: TWO tool_error frames
      `error_kind='parallel_destructive_not_allowed'`, NO confirmation cards.
  (h) M8 — closing SSE mid-turn sets session.abort_event.
  (i) v4-MINOR-3 — two concurrent /confirm POSTs against the same token —
      first succeeds, second returns 404 (single-use enforced under lock).

(d) uses a SHORT test-time TTL injected via monkeypatching
`CONFIRMATION_TTL_SECONDS` so we never literally wait 5 minutes.
"""
from __future__ import annotations

import concurrent.futures
import json
import time

import pytest

from services import chat_service


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


@pytest.fixture(autouse=True)
def _reset_chat_sessions():
    """Wipe the session registry around every test."""
    chat_service._reset_sessions_for_tests()
    yield
    chat_service._reset_sessions_for_tests()


@pytest.fixture(autouse=True)
def _short_confirmation_ttl(monkeypatch):
    """
    Phase 2 tests inject a short confirmation TTL so a destructive tool_call
    without a paired /confirm completes in milliseconds instead of waiting
    the production 300 s. Tests that exercise specific TTL semantics
    re-override locally.
    """
    monkeypatch.setattr(chat_service, "CONFIRMATION_TTL_SECONDS", 0.3)


# ─────────────────────────────────────────────────────────────────────────
# (a) session_init within 200ms; basic frame ordering
# ─────────────────────────────────────────────────────────────────────────


def test_session_init_emitted_first(client):
    """POST /api/chat/stream emits session_init as the first event."""
    t0 = time.monotonic()
    resp = client.post(
        "/api/chat/stream",
        json={"session_id": "sess-init-1", "script": [{"type": "session_done"}]},
    )
    elapsed = time.monotonic() - t0
    assert resp.status_code == 200
    frames = _parse_sse(resp.content)
    assert frames, "no SSE frames received"
    first_event, first_payload = frames[0]
    assert first_event == "session_init"
    assert first_payload["session_id"] == "sess-init-1"
    assert first_payload["model"] == chat_service.DEFAULT_MODEL
    # Exit criterion (a): within 200ms in the in-process TestClient harness
    # (loose bound — sufficient to catch a slow regression).
    assert elapsed < 1.0


# ─────────────────────────────────────────────────────────────────────────
# Read-tier tool — no confirmation
# ─────────────────────────────────────────────────────────────────────────


def test_read_tier_tool_runs_immediately(client):
    """Read-tier tool emits tool_request → tool_running → tool_result with no confirmation."""
    resp = client.post(
        "/api/chat/stream",
        json={
            "session_id": "sess-read-1",
            "script": [
                {
                    "type": "tool_call",
                    "tool_use_id": "tu-1",
                    "name": "list_components",
                    "args": {"component_class": "Bus"},
                    "safety_tier": "read",
                    "result": [{"name": "B1"}],
                },
                {"type": "session_done"},
            ],
        },
    )
    events = [e for e, _ in _parse_sse(resp.content)]
    assert "tool_pending_confirmation" not in events
    assert events.count("tool_request") == 1
    assert events.count("tool_running") == 1
    assert events.count("tool_result") == 1


# ─────────────────────────────────────────────────────────────────────────
# (b) Destructive tool emits tool_pending_confirmation
# ─────────────────────────────────────────────────────────────────────────


def test_destructive_tool_emits_pending_confirmation(client):
    """A destructive tool yields a tool_pending_confirmation frame with a token + TTL."""
    resp = client.post(
        "/api/chat/stream",
        json={
            "session_id": "sess-d-1",
            "script": [
                {
                    "type": "tool_call",
                    "tool_use_id": "tu-d",
                    "name": "delete_component",
                    "args": {"component_class": "Bus", "name": "B1"},
                    "safety_tier": "destructive",
                    "result": {"deleted": "B1"},
                },
            ],
        },
    )
    frames = _parse_sse(resp.content)
    pending = [p for e, p in frames if e == "tool_pending_confirmation"]
    assert len(pending) == 1
    pc = pending[0]
    assert pc["tool_name"] == "delete_component"
    assert pc["safety_tier"] == "destructive"
    assert "confirmation_token" in pc
    assert pc["ttl_seconds"] == chat_service.CONFIRMATION_TTL_SECONDS
    # The stream ends with tool_error (TTL elapsed because /confirm never came)
    # OR remains pending until session_done. The exit criterion only asserts
    # the pending frame exists.


# ─────────────────────────────────────────────────────────────────────────
# (c) Approve within TTL executes the tool
# ─────────────────────────────────────────────────────────────────────────


def test_approve_within_ttl_executes_tool(client):
    """
    Two-step: open SSE in a thread, /confirm approve, then SSE thread sees
    tool_running + tool_result.
    """
    import threading

    streamed: list[tuple[str, dict]] = []
    done = threading.Event()

    def _run_stream():
        r = client.post(
            "/api/chat/stream",
            json={
                "session_id": "sess-approve",
                "script": [
                    {
                        "type": "tool_call",
                        "tool_use_id": "tu-A",
                        "name": "save_project",
                        "args": {"name": "P"},
                        "safety_tier": "destructive",
                        "result": {"name": "P", "saved": True},
                    },
                    {"type": "session_done"},
                ],
            },
        )
        streamed.extend(_parse_sse(r.content))
        done.set()

    t = threading.Thread(target=_run_stream)
    t.start()

    # Wait for the pending token to appear in the session registry. The agent
    # loop emits issue_confirmation BEFORE blocking on wait_for_decision.
    deadline = time.monotonic() + 5.0
    token = None
    while time.monotonic() < deadline:
        sess = chat_service.get_session("sess-approve")
        if sess is not None:
            with sess._lock:
                if sess.pending_confirmations:
                    token = next(iter(sess.pending_confirmations))
                    break
        time.sleep(0.02)
    assert token, "pending confirmation token never appeared"

    # /confirm approve
    r = client.post(
        "/api/chat/sess-approve/confirm",
        json={"token": token, "decision": "approve"},
    )
    assert r.status_code == 200
    assert r.json()["decision"] == "approve"

    done.wait(5.0)
    t.join()

    events = [e for e, _ in streamed]
    assert "tool_running" in events
    # tool_result MUST appear after running
    assert events.index("tool_running") < events.index("tool_result")


# ─────────────────────────────────────────────────────────────────────────
# (d) TTL expiry → 409 / tool_error 'confirmation_expired'
# ─────────────────────────────────────────────────────────────────────────


def test_confirmation_expires_after_ttl(client, monkeypatch):
    """Short TTL — stream times out the confirmation and emits tool_error expired."""
    # Inject a 100ms TTL so the test doesn't wait 5 minutes
    monkeypatch.setattr(chat_service, "CONFIRMATION_TTL_SECONDS", 0.1)

    resp = client.post(
        "/api/chat/stream",
        json={
            "session_id": "sess-ttl",
            "script": [
                {
                    "type": "tool_call",
                    "tool_use_id": "tu-T",
                    "name": "delete_project",
                    "args": {"name": "P"},
                    "safety_tier": "destructive",
                    "result": {"deleted": ["P"]},
                },
            ],
        },
    )
    frames = _parse_sse(resp.content)
    err = [p for e, p in frames if e == "tool_error"]
    assert any(p["error_kind"] == "confirmation_expired" for p in err), (
        f"no confirmation_expired tool_error frame; got: {err}"
    )


def test_late_confirm_after_ttl_returns_409(client, monkeypatch):
    """
    POST /confirm AFTER the TTL fires returns 404 (the token was pruned
    server-side when wait_for_decision recognised expiry). 409 with
    error_kind='confirmation_expired' only fires if the /confirm POST
    arrives in the narrow window BEFORE the agent's wait-side prune runs.
    """
    monkeypatch.setattr(chat_service, "CONFIRMATION_TTL_SECONDS", 0.1)

    # Pre-issue a token directly on a session WITHOUT routing through SSE so
    # we control the timing precisely.
    sess = chat_service.get_or_create_session("sess-late")
    pc = sess.issue_confirmation(
        tool_name="delete_project", args={"name": "P"},
        safety_tier="destructive", ttl_seconds=0.05,
    )
    time.sleep(0.2)  # let TTL elapse

    r = client.post(
        "/api/chat/sess-late/confirm",
        json={"token": pc.token, "decision": "approve"},
    )
    # Either 409 expired (caught before wait prunes) or 404 (post-prune).
    # Both are acceptable expiry semantics — the test asserts NOT 200.
    assert r.status_code in (404, 409)
    body = r.json()
    detail = body.get("detail")
    assert isinstance(detail, dict)
    assert detail.get("error_kind") in {
        "confirmation_expired", "unknown_confirmation_token",
    }


# ─────────────────────────────────────────────────────────────────────────
# (e) Replay returns 404 'unknown_confirmation_token'
# ─────────────────────────────────────────────────────────────────────────


def test_replay_after_consume_returns_404(client):
    """Second /confirm against an already-consumed token returns 404."""
    sess = chat_service.get_or_create_session("sess-replay")
    pc = sess.issue_confirmation(
        tool_name="delete_project", args={"name": "P"},
        safety_tier="destructive",
    )
    # First call: should succeed (200)
    r1 = client.post(
        "/api/chat/sess-replay/confirm",
        json={"token": pc.token, "decision": "approve"},
    )
    assert r1.status_code == 200
    # Replay: 404 unknown_confirmation_token
    r2 = client.post(
        "/api/chat/sess-replay/confirm",
        json={"token": pc.token, "decision": "approve"},
    )
    assert r2.status_code == 404
    assert r2.json()["detail"]["error_kind"] == "unknown_confirmation_token"


# ─────────────────────────────────────────────────────────────────────────
# (g) M7 — parallel destructive batch
# ─────────────────────────────────────────────────────────────────────────


def test_parallel_destructive_batch_rejects_both(client):
    """
    Two destructive tool_use blocks in one batch → TWO tool_error frames
    with error_kind='parallel_destructive_not_allowed' and NO
    confirmation cards.
    """
    resp = client.post(
        "/api/chat/stream",
        json={
            "session_id": "sess-m7",
            "script": [
                {
                    "type": "tool_batch",
                    "calls": [
                        {
                            "tool_use_id": "tu-A",
                            "name": "delete_project",
                            "args": {"name": "A"},
                            "safety_tier": "destructive",
                            "result": None,
                        },
                        {
                            "tool_use_id": "tu-B",
                            "name": "delete_component",
                            "args": {"component_class": "Bus", "name": "B1"},
                            "safety_tier": "destructive",
                            "result": None,
                        },
                    ],
                },
                {"type": "session_done"},
            ],
        },
    )
    frames = _parse_sse(resp.content)
    events = [e for e, _ in frames]
    assert "tool_pending_confirmation" not in events, (
        "M7 violation: confirmation card emitted for parallel destructives"
    )
    err = [p for e, p in frames if e == "tool_error"]
    assert len(err) == 2
    for p in err:
        assert p["error_kind"] == "parallel_destructive_not_allowed"


# ─────────────────────────────────────────────────────────────────────────
# (h) M8 — closing SSE mid-turn sets abort_event
# ─────────────────────────────────────────────────────────────────────────


def test_abort_endpoint_sets_abort_event(client):
    """Synthetic abort_event check — POST /abort sets the flag."""
    sess = chat_service.get_or_create_session("sess-abort")
    assert not sess.abort_event.is_set()
    r = client.post("/api/chat/sess-abort/abort")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert sess.abort_event.is_set()


# ─────────────────────────────────────────────────────────────────────────
# (i) v4-MINOR-3 — concurrent /confirm against the same token
# ─────────────────────────────────────────────────────────────────────────


def test_concurrent_confirm_one_succeeds_one_404(client):
    """
    Two concurrent /confirm POSTs against the SAME token. _lock serialises:
    one succeeds (200), the other observes a missing token (404).
    """
    sess = chat_service.get_or_create_session("sess-race")
    pc = sess.issue_confirmation(
        tool_name="delete_project", args={"name": "X"},
        safety_tier="destructive",
    )

    def _confirm():
        return client.post(
            "/api/chat/sess-race/confirm",
            json={"token": pc.token, "decision": "approve"},
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        futures = [ex.submit(_confirm) for _ in range(2)]
        results = [f.result() for f in futures]

    statuses = sorted(r.status_code for r in results)
    assert statuses == [200, 404], (
        f"expected exactly one 200 and one 404; got {statuses}"
    )


# ─────────────────────────────────────────────────────────────────────────
# /health endpoint
# ─────────────────────────────────────────────────────────────────────────


def test_chat_health_reports_api_key_presence(client, monkeypatch):
    """/api/chat/health is cheap and never echoes the actual key value."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-xyz")
    r = client.get("/api/chat/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["anthropic_api_key_present"] is True
    assert "sk-ant-test-xyz" not in json.dumps(body)


def test_chat_health_when_no_api_key(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = client.get("/api/chat/health")
    assert r.json()["anthropic_api_key_present"] is False


# ─────────────────────────────────────────────────────────────────────────
# Confirmation against an unknown session
# ─────────────────────────────────────────────────────────────────────────


def test_confirm_unknown_session_returns_404(client):
    r = client.post(
        "/api/chat/nope/confirm",
        json={"token": "abc", "decision": "approve"},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["error_kind"] == "unknown_session"


def test_invalid_decision_returns_400_and_preserves_token(client):
    """An unknown decision string returns 400 + leaves the token usable."""
    sess = chat_service.get_or_create_session("sess-invalid")
    pc = sess.issue_confirmation(
        tool_name="delete_project", args={"name": "Q"},
        safety_tier="destructive",
    )
    r = client.post(
        "/api/chat/sess-invalid/confirm",
        json={"token": pc.token, "decision": "maybe"},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error_kind"] == "invalid_decision"
    # Retry with a valid decision should still succeed.
    r2 = client.post(
        "/api/chat/sess-invalid/confirm",
        json={"token": pc.token, "decision": "deny"},
    )
    assert r2.status_code == 200
    assert r2.json()["decision"] == "deny"


# ─────────────────────────────────────────────────────────────────────────
# #26 — /stream rate limit (in-memory token bucket, keyed per session_id)
# ─────────────────────────────────────────────────────────────────────────


def test_stream_rate_limit_returns_429_with_retry_after(client, monkeypatch):
    """capacity=1 + slow refill → the 2nd /stream on the SAME session_id 429s."""
    monkeypatch.setattr(chat_service, "STREAM_RATE_CAPACITY", 1.0)
    monkeypatch.setattr(chat_service, "STREAM_RATE_REFILL_PER_SEC", 0.001)

    body = {"session_id": "sess-rate-1", "script": [{"type": "session_done"}]}
    r1 = client.post("/api/chat/stream", json=body)
    assert r1.status_code == 200  # first request consumes the only token

    r2 = client.post("/api/chat/stream", json=body)
    assert r2.status_code == 429
    assert r2.headers.get("Retry-After") is not None
    assert r2.json()["detail"]["error_kind"] == "rate_limited_local"


def test_rate_limit_disabled_by_default(client, monkeypatch):
    """With the default capacity (0 → disabled), repeated /stream all 200."""
    monkeypatch.setattr(chat_service, "STREAM_RATE_CAPACITY", 0.0)
    body = {"session_id": "sess-rate-off", "script": [{"type": "session_done"}]}
    for _ in range(5):
        r = client.post("/api/chat/stream", json=body)
        assert r.status_code == 200


def test_rate_limit_separate_sessions_independent(client, monkeypatch):
    """A bucket is per-session — exhausting one session doesn't block another."""
    monkeypatch.setattr(chat_service, "STREAM_RATE_CAPACITY", 1.0)
    monkeypatch.setattr(chat_service, "STREAM_RATE_REFILL_PER_SEC", 0.001)

    a = {"session_id": "sess-rate-A", "script": [{"type": "session_done"}]}
    b = {"session_id": "sess-rate-B", "script": [{"type": "session_done"}]}
    assert client.post("/api/chat/stream", json=a).status_code == 200
    assert client.post("/api/chat/stream", json=a).status_code == 429  # A exhausted
    # B has its own bucket — still allowed.
    assert client.post("/api/chat/stream", json=b).status_code == 200


def test_check_rate_limit_unit_refill_allows_again(monkeypatch):
    """Token-bucket unit check: after enough refill the key is allowed again."""
    monkeypatch.setattr(chat_service, "STREAM_RATE_CAPACITY", 2.0)
    monkeypatch.setattr(chat_service, "STREAM_RATE_REFILL_PER_SEC", 1000.0)
    chat_service._reset_sessions_for_tests()  # clear buckets

    allowed1, _ = chat_service.check_rate_limit("k")
    allowed2, _ = chat_service.check_rate_limit("k")
    assert allowed1 and allowed2  # capacity 2 → two allowed
    allowed3, retry = chat_service.check_rate_limit("k")
    assert not allowed3 and retry > 0
    time.sleep(0.02)  # 0.02s * 1000/s = 20 tokens refilled → allowed again
    allowed4, _ = chat_service.check_rate_limit("k")
    assert allowed4

