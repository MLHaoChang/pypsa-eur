"""
The stdio shim for local MCP exposure (issue 11 follow-up): an MCP client
that only speaks stdio (or does not know the desktop's ephemeral port) runs
`python mcp_stdio.py`, which reads `<app data>/mcp-endpoint.json` for every
message and forwards each JSON-RPC line to `/api/mcp` with the bearer token,
carrying the `Mcp-Session-Id` from `initialize`. Standard library only.
"""
from __future__ import annotations

import io
import json

import pytest

import mcp_stdio


def _endpoint(tmp_path, url="http://127.0.0.1:9/api/mcp", token="t0k"):
    path = tmp_path / "mcp-endpoint.json"
    path.write_text(json.dumps({"url": url, "token": token}))
    return path


class _Recorder:
    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []

    def __call__(self, url, body, headers):
        self.sent.append((url, json.loads(body), dict(headers)))
        return self.replies.pop(0)


def test_a_missing_endpoint_file_answers_the_request_with_an_error(tmp_path):
    shim = mcp_stdio.Shim(tmp_path / "absent.json", post=_Recorder([]))
    out = json.loads(shim.forward('{"jsonrpc":"2.0","id":4,"method":"tools/list"}'))
    assert out["id"] == 4 and "not running" in out["error"]["message"]


def test_initialize_carries_the_token_and_later_calls_the_session(tmp_path):
    post = _Recorder([
        (200, {"Mcp-Session-Id": "mcp-abc", "Content-Type": "application/json"},
         b'{"jsonrpc":"2.0","id":1,"result":{}}'),
        (200, {"Content-Type": "application/json"}, b'{"jsonrpc":"2.0","id":2,"result":{"tools":[]}}'),
    ])
    shim = mcp_stdio.Shim(_endpoint(tmp_path), post=post)
    assert json.loads(shim.forward('{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'))["id"] == 1
    assert json.loads(shim.forward('{"jsonrpc":"2.0","id":2,"method":"tools/list"}'))["result"] == {"tools": []}
    (_, _, first), (url, _, second) = post.sent
    assert first["Authorization"] == "Bearer t0k" and "Mcp-Session-Id" not in first
    assert second["Mcp-Session-Id"] == "mcp-abc" and url == "http://127.0.0.1:9/api/mcp"


def test_a_notification_gets_no_reply(tmp_path):
    shim = mcp_stdio.Shim(_endpoint(tmp_path), post=_Recorder([(202, {}, b"")]))
    assert shim.forward('{"jsonrpc":"2.0","method":"notifications/initialized"}') is None


def test_an_unreachable_app_is_a_json_rpc_error_not_a_crash(tmp_path):
    def down(url, body, headers):
        raise OSError("connection refused")
    shim = mcp_stdio.Shim(_endpoint(tmp_path), post=down)
    out = json.loads(shim.forward('{"jsonrpc":"2.0","id":9,"method":"ping"}'))
    assert out["id"] == 9 and out["error"]["code"] == -32000


def test_an_http_error_without_a_json_rpc_body_is_reported(tmp_path):
    shim = mcp_stdio.Shim(_endpoint(tmp_path), post=_Recorder([(401, {}, b'{"detail":"no"}')]))
    out = json.loads(shim.forward('{"jsonrpc":"2.0","id":3,"method":"ping"}'))
    assert out["id"] == 3 and "401" in out["error"]["message"]


def test_unparseable_input_is_a_parse_error(tmp_path):
    shim = mcp_stdio.Shim(_endpoint(tmp_path), post=_Recorder([]))
    assert json.loads(shim.forward("not json"))["error"]["code"] == -32700


def test_run_reads_lines_and_writes_replies(tmp_path):
    post = _Recorder([(200, {}, b'{"jsonrpc":"2.0","id":1,"result":{}}')])
    shim = mcp_stdio.Shim(_endpoint(tmp_path), post=post)
    stdout = io.StringIO()
    shim.run(io.StringIO('{"jsonrpc":"2.0","id":1,"method":"ping"}\n\n'), stdout)
    assert json.loads(stdout.getvalue().strip()) == {"jsonrpc": "2.0", "id": 1, "result": {}}


def test_through_the_real_app(tmp_path, monkeypatch, _auth_db):
    """The shim against the app in local mode, the endpoint file the app wrote."""
    import local_mode
    import main
    from fastapi.testclient import TestClient
    from services import chat_service
    monkeypatch.setenv("PYPSAGUI_LOCAL_MODE", "1")
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    _engine, session_local = _auth_db
    with session_local() as db:
        local_mode.ensure_local_identity(db)
    try:
        with TestClient(main.app) as client:
            client.cookies.clear()

            def via_app(url, body, headers):
                r = client.post("/api/mcp", content=body, headers=headers)
                return r.status_code, dict(r.headers), r.content
            shim = mcp_stdio.Shim(tmp_path / "appdata" / "mcp-endpoint.json", post=via_app)
            init = json.loads(shim.forward(json.dumps({
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                           "clientInfo": {"name": "via-shim"}}})))
            assert init["result"]["serverInfo"]["name"] == "pypsa-gui"
            tools = json.loads(shim.forward('{"jsonrpc":"2.0","id":2,"method":"tools/list"}'))
            assert any(t["name"] == "list_projects" for t in tools["result"]["tools"])
    finally:
        chat_service._reset_sessions_for_tests()
        with session_local() as db:
            local_mode.remove_local_identity(db)


def test_the_default_endpoint_path_is_in_the_app_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path))
    assert mcp_stdio.default_endpoint_path() == tmp_path.resolve() / "mcp-endpoint.json"
