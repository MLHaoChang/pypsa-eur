"""
Local MCP exposure (chat harness issue 11, MCP build step 4; owner decision
Q8 = option 2: the full catalogue, this machine only, every write carded in
the app; `.scratch/harness/mcp-spec.md` Part B).

An external agent (Claude Code, Codex, a Kimi agent) on this machine drives
the running app through `/api/mcp` (Streamable HTTP, JSON responses):

* Local mode only: the route answers 404 in web mode.
* A per-launch bearer token, written with the URL to
  `<app data>/mcp-endpoint.json` (0600) at startup; a foreign `Origin` is
  refused (DNS rebinding).
* One chat session (`mcp-<hex>`) per MCP session, owned by the local user;
  its calls are recorded in that session's event log.
* The catalogue minus the harness control tools and the panel-driving tools;
  `read_result` stays. Annotations carry the tier.
* Every call runs through `harness.loop.dispatch_tool_call`, with `write` and
  above carded in every mode. The card waits in
  `GET /api/chat/external/pending` and is answered through the existing
  `POST /api/chat/{session_id}/confirm`, with the usual 300 s TTL.
"""
from __future__ import annotations

import json
import os
import stat
import threading
import time

import pytest
from fastapi.testclient import TestClient

import local_mode
import main
from harness import catalogue, eventlog
from services import chat_service, chat_tools


@pytest.fixture
def local_client(_auth_db, monkeypatch, tmp_path):
    monkeypatch.setenv("PYPSAGUI_LOCAL_MODE", "1")
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    _engine, session_local = _auth_db
    with session_local() as db:
        local_mode.ensure_local_identity(db)
    chat_service._reset_sessions_for_tests()
    try:
        with TestClient(main.app) as c:
            c.cookies.clear()
            yield c
    finally:
        chat_service._reset_sessions_for_tests()
        with session_local() as db:
            local_mode.remove_local_identity(db)


@pytest.fixture
def endpoint(local_client, tmp_path):
    return json.loads((tmp_path / "appdata" / "mcp-endpoint.json").read_text())


@pytest.fixture
def probes(monkeypatch):
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


def _rpc(client, endpoint, method, params=None, *, sid=None, rid=1, headers=None, path="/api/mcp"):
    h = {"Authorization": f"Bearer {endpoint['token']}"}
    if sid:
        h["Mcp-Session-Id"] = sid
    h.update(headers or {})
    body = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if rid is not None:
        body["id"] = rid
    return client.post(path, json=body, headers=h)


def _open(client, endpoint):
    r = _rpc(client, endpoint, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                              "clientInfo": {"name": "test-agent", "version": "1"}})
    assert r.status_code == 200, r.text
    return r.headers["Mcp-Session-Id"], r.json()


def _call(client, endpoint, sid, name, args=None):
    r = _rpc(client, endpoint, "tools/call", {"name": name, "arguments": args or {}}, sid=sid, rid=7)
    assert r.status_code == 200, r.text
    return r.json()["result"]


# ── where it exists, who may call it ───────────────────────────────────────

def test_web_mode_has_no_mcp_endpoint(client):
    r = client.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert r.status_code == 404


def test_the_endpoint_file_is_private_and_names_the_url(endpoint, tmp_path):
    path = tmp_path / "appdata" / "mcp-endpoint.json"
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert endpoint["url"].endswith("/api/mcp") and len(endpoint["token"]) >= 32


@pytest.mark.parametrize("auth", [None, "Bearer wrong"])
def test_a_missing_or_wrong_token_is_refused(local_client, endpoint, auth):
    headers = {"Authorization": auth} if auth else {}
    r = local_client.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers=headers)
    assert r.status_code == 401


def test_a_foreign_origin_is_refused(local_client, endpoint):
    r = _rpc(local_client, endpoint, "ping", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


# ── the protocol ───────────────────────────────────────────────────────────

def test_initialize_opens_a_chat_session_and_negotiates_the_version(local_client, endpoint):
    sid, body = _open(local_client, endpoint)
    assert sid.startswith("mcp-")
    assert body["result"]["protocolVersion"] == "2025-06-18"
    assert "tools" in body["result"]["capabilities"]
    session = chat_service.get_session(sid)
    assert session is not None and session.mcp_client == "test-agent"
    r = _rpc(local_client, endpoint, "initialize", {"protocolVersion": "1999-01-01", "capabilities": {}})
    assert r.json()["result"]["protocolVersion"] in ("2025-11-25", "2025-06-18")


def test_a_call_needs_an_open_session(local_client, endpoint):
    assert _rpc(local_client, endpoint, "tools/list").status_code == 400
    assert _rpc(local_client, endpoint, "tools/list", sid="mcp-nope").status_code == 404


def test_notifications_are_accepted_and_unknown_methods_refused(local_client, endpoint):
    sid, _ = _open(local_client, endpoint)
    assert _rpc(local_client, endpoint, "notifications/initialized", sid=sid, rid=None).status_code == 202
    r = _rpc(local_client, endpoint, "resources/list", sid=sid)
    assert r.json()["error"]["code"] == -32601


def test_delete_ends_the_session(local_client, endpoint):
    sid, _ = _open(local_client, endpoint)
    r = local_client.delete("/api/mcp", headers={"Authorization": f"Bearer {endpoint['token']}",
                                                 "Mcp-Session-Id": sid})
    assert r.status_code == 204
    assert _rpc(local_client, endpoint, "tools/list", sid=sid).status_code == 404


# ── what is exposed ────────────────────────────────────────────────────────

def test_the_catalogue_without_the_harness_controls_or_the_panel_tools(local_client, endpoint, probes):
    sid, _ = _open(local_client, endpoint)
    tools = {t["name"]: t for t in _rpc(local_client, endpoint, "tools/list", sid=sid).json()["result"]["tools"]}
    for control in ("ask_user", "use_skill", "start_workflow", "set_goal", "use_toolset"):
        assert control not in tools
    ui_tools = [n for n, route in catalogue.TOOL_ROUTES.items() if route == catalogue._UI_EVENT]
    assert ui_tools and not [n for n in ui_tools if n in tools]
    assert "read_result" in tools and "list_projects" in tools
    assert not [n for n in tools if n.startswith("mcp__")]
    assert tools["probe_read"]["annotations"] == {"readOnlyHint": True, "destructiveHint": False}
    assert tools["probe_wipe"]["annotations"] == {"readOnlyHint": False, "destructiveHint": True}
    assert tools["probe_write"]["inputSchema"] == {"type": "object", "properties": {}, "required": []}


def test_a_toolset_narrows_the_list(local_client, endpoint):
    sid, _ = _open(local_client, endpoint)
    everything = _rpc(local_client, endpoint, "tools/list", sid=sid).json()["result"]["tools"]
    narrowed = _rpc(local_client, endpoint, "tools/list", sid=sid,
                    path="/api/mcp?toolset=results").json()["result"]["tools"]
    assert 0 < len(narrowed) < len(everything)


# ── calls ──────────────────────────────────────────────────────────────────

def test_a_read_runs_at_once_and_is_logged(local_client, endpoint, probes):
    sid, _ = _open(local_client, endpoint)
    result = _call(local_client, endpoint, sid, "probe_read")
    assert result["isError"] is False and "probe_read" in result["content"][0]["text"]
    assert probes == ["probe_read"]
    kinds = [e["type"] for e in eventlog.read_events(eventlog.path_for(chat_service.get_session(sid)))]
    assert "mcp/call" in kinds and "mcp/result" in kinds


def test_a_tool_that_is_not_exposed_never_runs(local_client, endpoint, monkeypatch):
    ran = []
    monkeypatch.setitem(chat_tools.DISPATCHERS, "ask_user", lambda **kw: ran.append(1))
    sid, _ = _open(local_client, endpoint)
    result = _call(local_client, endpoint, sid, "ask_user", {"question": "q", "options": ["a", "b"]})
    assert result["isError"] is True and "tool_not_offered" in json.dumps(result)
    assert ran == []


def _call_in_background(client, endpoint, sid, name):
    out: dict = {}

    def run():
        out["result"] = _call(client, endpoint, sid, name)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t, out


def _wait_for_pending(client, sid, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        cards = [c for c in client.get("/api/chat/external/pending").json()["pending"]
                 if c["session_id"] == sid]
        if cards:
            return cards[0]
        time.sleep(0.05)
    raise AssertionError("no pending card")


@pytest.mark.parametrize("decision, ran", [("approve", True), ("deny", False)])
def test_a_write_waits_for_its_card_in_the_app(local_client, endpoint, probes, decision, ran):
    sid, _ = _open(local_client, endpoint)
    t, out = _call_in_background(local_client, endpoint, sid, "probe_write")
    card = _wait_for_pending(local_client, sid)
    assert card["tool_name"] == "probe_write" and card["safety_tier"] == "write"
    assert card["client"] == "test-agent" and card["ttl_seconds"] > 0
    assert probes == []                                          # nothing ran before the answer
    r = local_client.post(f"/api/chat/{sid}/confirm",
                          json={"token": card["confirmation_token"], "decision": decision})
    assert r.status_code == 200, r.text
    t.join(10)
    assert (probes == ["probe_write"]) is ran
    assert out["result"]["isError"] is (not ran)
    if not ran:
        assert "confirmation_denied" in json.dumps(out["result"])


def test_the_pending_list_shows_only_external_sessions(local_client, endpoint, probes):
    in_app = chat_service.get_or_create_session("in-app-session", owner_user_id=None)
    in_app.issue_confirmation(tool_name="probe_wipe", args={}, safety_tier="destructive")
    assert local_client.get("/api/chat/external/pending").json()["pending"] == []
