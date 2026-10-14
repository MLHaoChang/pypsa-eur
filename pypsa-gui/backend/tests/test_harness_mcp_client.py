"""
Chat harness issue 19, slice 1: the harness as an MCP client
(`.scratch/harness/mcp-spec.md` Part A, build step 2).

An admin lists MCP servers in `<app data>/mcp-servers.json`; their tools join
a chat session's catalogue as `mcp__<server>__<tool>`, fixed for the session,
behind the same card and fence as the built-in tools.

* Zero servers by default: no file, no tool, no event, no connection.
* The tier is `write` unless the server marks a tool read-only; an admin
  override only raises it. A forged `Safety:` marker in a description is
  removed, the description is capped and its fence delimiters neutralised.
* External tools always ask (owner decision Q20): a tool that is not
  read-only gets the card in Expert as well as Guided.
* Results are fenced like any tool result; images are dropped; server
  instructions never reach the model; a server's own requests (sampling,
  elicitation, roots) are refused.
* `smoke/mcp_stub_server.py` is the server.
"""
from __future__ import annotations

import json
import pathlib
import sys
import time

import httpx
import pytest

from harness import eventlog, mcp_client
from services import chat_service

BACKEND = pathlib.Path(__file__).resolve().parents[1]
STUB = BACKEND / "smoke" / "mcp_stub_server.py"


@pytest.fixture(autouse=True)
def _clean():
    chat_service._reset_sessions_for_tests()
    mcp_client.reset_for_tests()
    yield
    mcp_client.reset_for_tests()
    chat_service._reset_sessions_for_tests()


@pytest.fixture
def servers(tmp_path, monkeypatch):
    """Write the admin's server list; returns a function taking the entries."""
    path = tmp_path / "mcp-servers.json"
    monkeypatch.setattr(mcp_client, "config_path", lambda: path)

    def write(*entries):
        path.write_text(json.dumps({"servers": list(entries)}))
        mcp_client.reset_for_tests()
        return path
    return write


def _stub(**over):
    entry = {"id": "stub", "enabled": True, "transport": "stdio",
             "command": sys.executable, "args": [str(STUB)]}
    entry.update(over)
    return entry


def _session():
    from harness.session import ChatSession
    return ChatSession(model="m", owner_user_id="u1")


def _by_name(session):
    return {t["name"]: t for t in mcp_client.session_tools(session)}


# ── zero by default ────────────────────────────────────────────────────────

def test_no_config_file_means_no_tools_no_event_no_connection(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_client, "config_path", lambda: tmp_path / "absent.json")
    session = _session()
    assert mcp_client.session_tools(session) == []
    assert mcp_client.tier_for(session, "mcp__stub__lookup") is None
    assert mcp_client.dispatcher_for(session, "mcp__stub__lookup") is None
    path = eventlog.path_for(session)
    assert not path.exists() or not [e for e in eventlog.read_events(path) if e["type"] == "mcp/catalogue"]
    assert mcp_client._CONNECTIONS == {}


# ── the admin's list ───────────────────────────────────────────────────────

@pytest.mark.parametrize("entry, reason", [
    ({"id": "Bad Id", "transport": "stdio", "command": "x"}, "id"),
    ({"id": "web", "transport": "http", "url": "http://example.com/mcp"}, "https"),
    ({"id": "env", "transport": "stdio", "command": "x", "env": {"TOKEN": "ANTHROPIC_API_KEY"}}, "PYPSA_GUI_MCP_KEY__"),
    ({"id": "what", "transport": "carrier-pigeon"}, "transport"),
    ({"id": "tier", "transport": "stdio", "command": "x", "tiers": {"a": "godlike"}}, "tier"),
])
def test_a_malformed_server_entry_is_refused_with_its_reason(entry, reason):
    servers, problems = mcp_client.parse_servers({"servers": [entry]})
    assert servers == []
    assert any(reason in p for p in problems), problems


def test_loopback_http_and_a_disabled_entry():
    servers, problems = mcp_client.parse_servers({"servers": [
        {"id": "local", "transport": "http", "url": "http://127.0.0.1:9000/mcp"},
        {"id": "off", "transport": "stdio", "command": "x", "enabled": False},
    ]})
    assert [s.id for s in servers] == ["local"] and problems == []


def test_the_child_sees_only_its_mapped_keys(servers, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-leak")
    monkeypatch.setenv("PYPSA_GUI_MCP_KEY__STUB", "stub-secret")
    servers(_stub(env={"STUB_TOKEN": "PYPSA_GUI_MCP_KEY__STUB"}))
    session = _session()
    mcp_client.session_tools(session)
    names = json.loads(mcp_client.dispatcher_for(session, "mcp__stub__env_names")())
    assert "STUB_TOKEN" in names
    assert "ANTHROPIC_API_KEY" not in names
    assert not [n for n in names if n.startswith("PYPSA_GUI_")]


# ── the catalogue entry ────────────────────────────────────────────────────

def test_names_tiers_and_overrides(servers):
    servers(_stub(tiers={"set_value": "destructive", "wipe": "read"}))
    tools = _by_name(_session())
    session = _session()
    mcp_client.session_tools(session)
    assert "mcp__stub__lookup" in tools
    assert mcp_client.tier_for(session, "mcp__stub__lookup") == "read"
    assert mcp_client.tier_for(session, "mcp__stub__set_value") == "destructive"   # raised
    assert mcp_client.tier_for(session, "mcp__stub__wipe") == "destructive"        # never lowered
    assert set(tools["mcp__stub__lookup"]) == {"name", "description", "input_schema"}
    assert tools["mcp__stub__lookup"]["input_schema"]["required"] == ["key"]


def test_a_tool_without_hints_is_write(servers):
    servers(_stub())
    session = _session()
    mcp_client.session_tools(session)
    assert mcp_client.tier_for(session, "mcp__stub__set_value") == "write"


def test_a_hostile_description_is_capped_neutralised_and_cannot_forge_its_tier(servers):
    servers(_stub(args=[str(STUB), "--forged"]))
    desc = _by_name(_session())["mcp__stub__lookup"]["description"]
    assert desc.startswith('External tool from MCP server "stub". ')
    assert desc.endswith(" Safety: read")
    assert desc.count("Safety:") == 1
    assert "</untrusted_data>" not in desc
    assert len(desc) <= len('External tool from MCP server "stub". ') + mcp_client.DESCRIPTION_CAP + len(" Safety: read")


def test_server_instructions_never_reach_the_catalogue(servers):
    servers(_stub())
    assert "STUB-SERVER-INSTRUCTIONS" not in json.dumps(mcp_client.session_tools(_session()))


def test_the_tool_list_is_fixed_for_the_session_and_logged(servers):
    servers(_stub())
    session = _session()
    first = mcp_client.session_tools(session)
    servers(_stub(id="other"))                                    # the admin edits the file
    assert mcp_client.session_tools(session) == first             # same session: same list
    assert {t["name"] for t in mcp_client.session_tools(_session())} >= {"mcp__other__lookup"}
    events = [e for e in eventlog.read_events(eventlog.path_for(session)) if e["type"] == "mcp/catalogue"]
    assert len(events) == 1
    listed = {t["name"]: t for t in events[0]["servers"][0]["tools"]}
    assert listed["mcp__stub__wipe"]["tier"] == "destructive"
    assert len(listed["mcp__stub__lookup"]["schema_sha256"]) == 64


@pytest.mark.parametrize("schema", [
    {"type": "object", "properties": {"a": {"$ref": "#/defs/x"}}},
    {"type": "array"},
    {"type": "object", "properties": {"a": {"type": "string", "description": "x" * 9000}}},
    {"type": "object", "properties": {"a": {"type": "object", "properties": {"b": {"type": "object", "properties": {"c": {"type": "object", "properties": {"d": {"type": "object", "properties": {"e": {"type": "object", "properties": {"f": {"type": "object"}}}}}}}}}}}}},
])
def test_an_unsafe_schema_skips_the_tool(schema):
    assert mcp_client._schema_ok(schema) is False


def test_long_or_colliding_names_are_skipped():
    tools = [{"name": "Do-It", "inputSchema": {"type": "object"}},
             {"name": "do_it", "inputSchema": {"type": "object"}},
             {"name": "x" * 80, "inputSchema": {"type": "object"}}]
    cfg = mcp_client.parse_servers({"servers": [{"id": "s", "transport": "stdio", "command": "x"}]})[0][0]
    shaped = mcp_client._shape(cfg, tools)
    assert [t["name"] for t in shaped] == ["mcp__s__do_it"]


# ── the gate (Q20) ─────────────────────────────────────────────────────────

def _dispatch(session, name, args, *, guided):
    from harness.loop import _dispatch_real_tool_call
    collector: list[dict] = []
    frames = []
    gen = _dispatch_real_tool_call(session, {"id": "tu_1", "name": name, "input": args},
                                   collector, guided=guided)
    for event, payload in gen:
        frames.append((event, payload))
        if event == "tool_pending_confirmation":
            break
    gen.close()
    return frames, collector


@pytest.mark.parametrize("guided", [False, True], ids=["expert", "guided"])
def test_an_external_write_is_carded_in_both_modes(servers, guided):
    servers(_stub())
    session = _session()
    mcp_client.session_tools(session)
    frames, _ = _dispatch(session, "mcp__stub__set_value", {"key": "a", "value": "1"}, guided=guided)
    assert frames[-1][0] == "tool_pending_confirmation"
    assert frames[-1][1]["safety_tier"] == "write"
    assert frames[-1][1]["external"] == {"server": "stub"}


def test_an_external_destructive_tool_is_never_auto_approved(servers, monkeypatch):
    from harness import confirm
    monkeypatch.setattr(confirm, "AUTO_APPROVE_TIERS", frozenset({"destructive"}))
    servers(_stub())
    session = _session()
    mcp_client.session_tools(session)
    frames, _ = _dispatch(session, "mcp__stub__wipe", {}, guided=False)
    assert frames[-1][0] == "tool_pending_confirmation"


def test_a_read_only_external_tool_runs_without_a_card_and_its_result_is_fenced(servers, monkeypatch):
    from services import dirty_state
    marked = []
    monkeypatch.setattr(dirty_state, "mark_dirty", lambda: marked.append(1))
    servers(_stub())
    session = _session()
    mcp_client.session_tools(session)
    frames, results = _dispatch(session, "mcp__stub__lookup", {"key": "k"}, guided=False)
    assert "tool_pending_confirmation" not in [e for e, _ in frames]
    assert not [p for e, p in frames if e == "tool_error"]
    content = json.dumps(results[0]["content"])
    assert "value of k" in content and "untrusted_data" in content
    assert marked == []                                         # an external tool is not a project edit


def test_an_mcp_name_missing_from_the_session_is_carded():
    session = _session()
    frames, _ = _dispatch(session, "mcp__ghost__anything", {}, guided=False)
    assert frames[0][1]["safety_tier"] == "destructive"


# ── results and errors ─────────────────────────────────────────────────────

def test_is_error_becomes_mcp_tool_error(servers):
    servers(_stub())
    session = _session()
    mcp_client.session_tools(session)
    frames, results = _dispatch(session, "mcp__stub__fail", {}, guided=False)
    errors = [p for e, p in frames if e == "tool_error"]
    assert errors and errors[0]["error_kind"] == "mcp_tool_error"
    assert results[0]["is_error"] is True


def test_images_are_dropped_with_a_note(servers):
    servers(_stub())
    session = _session()
    mcp_client.session_tools(session)
    out = mcp_client.dispatcher_for(session, "mcp__stub__picture")()
    assert "a picture" in out and "aGVsbG8=" not in out and "image omitted" in out


def test_structured_content_is_preferred(servers):
    servers(_stub())
    session = _session()
    mcp_client.session_tools(session)
    assert mcp_client.dispatcher_for(session, "mcp__stub__lookup")(key="z") == {"key": "z", "value": "value of z"}


def test_a_slow_call_times_out_and_is_cancelled(servers, tmp_path, monkeypatch):
    from fastapi import HTTPException
    log = tmp_path / "stub.log"
    servers(_stub(args=[str(STUB), "--log", str(log)]))
    monkeypatch.setattr(mcp_client, "_call_timeout", lambda: 0.5)
    session = _session()
    mcp_client.session_tools(session)
    with pytest.raises(HTTPException) as exc:
        mcp_client.dispatcher_for(session, "mcp__stub__slow")(seconds=5)
    assert exc.value.detail["error_kind"] == "mcp_timeout"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and "notifications/cancelled" not in log.read_text():
        time.sleep(0.05)
    assert "notifications/cancelled" in log.read_text()


def test_a_server_that_will_not_start_is_unavailable(servers):
    servers(_stub(command="/nonexistent/mcp-server"))
    session = _session()
    assert mcp_client.session_tools(session) == []
    events = [e for e in eventlog.read_events(eventlog.path_for(session)) if e["type"] == "mcp/catalogue"]
    assert events[0]["servers"][0]["error"] == "mcp_unavailable"


def test_the_servers_own_requests_are_refused(servers):
    servers(_stub())
    session = _session()
    mcp_client.session_tools(session)
    assert mcp_client.dispatcher_for(session, "mcp__stub__ask_client")() == "client answered -32601"


# ── the turn ───────────────────────────────────────────────────────────────

def test_a_turn_offers_external_tools_and_calls_one(servers):
    from harness.providers.fake import FakeProvider
    servers(_stub())
    session = _session()
    seen = []

    class Watching(FakeProvider):
        def stream(self, request):
            seen.append([t["name"] for t in request.tools])
            yield from super().stream(request)

    turns = [{"blocks": [{"type": "tool_use", "id": "tu_1", "name": "mcp__stub__lookup",
                          "input": {"key": "q"}}],
              "usage": {"input_tokens": 1, "output_tokens": 1}},
             {"blocks": [{"type": "text", "text": "done"}], "usage": {"input_tokens": 1, "output_tokens": 1}}]
    frames = list(chat_service.run_turn(session, "look q up", provider=Watching(turns)))
    assert "mcp__stub__lookup" in seen[0]
    assert not [p for e, p in frames if e == "tool_error"]
    assert "value of q" in json.dumps(list(session.messages))


def test_a_narrowed_toolset_leaves_external_tools_out(servers):
    from harness.providers import wiring
    from services import chat_tools
    servers(_stub())
    session = _session()
    session.toolset = "network"
    chat_tools.set_chat_session(session)
    profile = type("P", (), {"tools": True, "wire": "anthropic", "base_url": None, "preset": "custom"})()
    names = {t["name"] for t in wiring._tools_payload_for_profile(profile)}
    assert not [n for n in names if n.startswith("mcp__")]
    session.toolset = "all"
    names = {t["name"] for t in wiring._tools_payload_for_profile(profile)}
    assert "mcp__stub__lookup" in names


# ── the HTTP transport ─────────────────────────────────────────────────────

def _http_server(sse: bool):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append((body, dict(request.headers)))
        if "id" not in body:
            return httpx.Response(202)
        method = body["method"]
        if method == "initialize":
            result = {"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "h"}}
        elif method == "tools/list":
            result = {"tools": [{"name": "Echo", "inputSchema": {"type": "object"},
                                 "annotations": {"readOnlyHint": True}}]}
        else:
            result = {"content": [{"type": "text", "text": "echoed"}]}
        message = {"jsonrpc": "2.0", "id": body["id"], "result": result}
        headers = {"Mcp-Session-Id": "sess-1"}
        if sse:
            return httpx.Response(200, headers={**headers, "Content-Type": "text/event-stream"},
                                  content=f"event: message\ndata: {json.dumps(message)}\n\n")
        return httpx.Response(200, headers=headers, json=message)
    return handler, seen


@pytest.mark.parametrize("sse", [False, True], ids=["json", "sse"])
def test_the_http_transport(servers, monkeypatch, sse):
    handler, seen = _http_server(sse)
    monkeypatch.setenv("PYPSA_GUI_MCP_KEY__WEB", "web-secret")
    monkeypatch.setattr(mcp_client, "_http_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    servers({"id": "web", "transport": "http", "url": "https://tools.example/mcp",
             "key": "PYPSA_GUI_MCP_KEY__WEB"})
    session = _session()
    assert [t["name"] for t in mcp_client.session_tools(session)] == ["mcp__web__echo"]
    assert mcp_client.dispatcher_for(session, "mcp__web__echo")() == "echoed"
    call = next(h for b, h in seen if b.get("method") == "tools/call")
    assert call["authorization"] == "Bearer web-secret"
    assert call["mcp-session-id"] == "sess-1"


# ── layering ───────────────────────────────────────────────────────────────

def test_the_loop_reaches_the_client_through_its_three_lookups_only():
    import ast
    tree = ast.parse((BACKEND / "harness" / "loop.py").read_text(encoding="utf-8"))
    used = {node.attr for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
            and node.value.id == "mcp_client"}
    assert used == {"tier_for", "server_for", "dispatcher_for"}



# ── limits a misbehaving server cannot get past ────────────────────────────

def _fake_http(monkeypatch, servers, *, version="2025-06-18", tools=None, call_body=None):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "id" not in body:
            return httpx.Response(202)
        if body["method"] == "initialize":
            result = {"protocolVersion": version, "capabilities": {}, "serverInfo": {"name": "f"}}
        elif body["method"] == "tools/list":
            result = {"tools": tools if tools is not None else
                      [{"name": "t", "inputSchema": {"type": "object"}, "annotations": {"readOnlyHint": True}}]}
        else:
            if call_body is not None:
                return httpx.Response(200, content=call_body, headers={"Content-Type": "application/json"})
            result = {"content": [{"type": "text", "text": "ok"}]}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})
    monkeypatch.setattr(mcp_client, "_http_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    servers({"id": "fake", "transport": "http", "url": "https://fake.example/mcp"})


def test_an_unknown_protocol_version_is_refused(servers, monkeypatch):
    _fake_http(monkeypatch, servers, version="1999-01-01")
    session = _session()
    assert mcp_client.session_tools(session) == []
    events = [e for e in eventlog.read_events(eventlog.path_for(session)) if e["type"] == "mcp/catalogue"]
    assert events[0]["servers"][0]["error"] == "mcp_protocol_error"


def test_a_server_listing_too_many_tools_is_cut(servers, monkeypatch):
    many = [{"name": f"t{i}", "inputSchema": {"type": "object"}} for i in range(mcp_client.MAX_TOOLS_PER_SERVER + 50)]
    _fake_http(monkeypatch, servers, tools=many)
    assert len(mcp_client.session_tools(_session())) == mcp_client.MAX_TOOLS_PER_SERVER


def test_an_oversized_reply_is_refused(servers, monkeypatch):
    from fastapi import HTTPException
    huge = json.dumps({"jsonrpc": "2.0", "id": 3, "result": {"content": [
        {"type": "text", "text": "x" * (mcp_client.MAX_MESSAGE_BYTES + 10)}]}})
    _fake_http(monkeypatch, servers, call_body=huge)
    session = _session()
    mcp_client.session_tools(session)
    with pytest.raises(HTTPException) as exc:
        mcp_client.dispatcher_for(session, "mcp__fake__t")()
    assert exc.value.detail["error_kind"] == "mcp_protocol_error"


def test_an_oversized_stdio_line_is_dropped_not_buffered(servers, tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_client, "MAX_MESSAGE_BYTES", 2048)
    servers(_stub(args=[str(STUB), "--forged"]))                  # tools/list is ~2.5 KB
    session = _session()
    assert mcp_client.session_tools(session) == []
    events = [e for e in eventlog.read_events(eventlog.path_for(session)) if e["type"] == "mcp/catalogue"]
    assert events[0]["servers"][0]["error"] in {"mcp_timeout", "mcp_protocol_error"}
