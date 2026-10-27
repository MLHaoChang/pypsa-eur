"""
Chat harness issue 19, slice 2 — the MCP server settings routes
(`.scratch/harness/mcp-spec.md` A.1).

    GET    /api/chat/settings/mcp/servers
    PUT    /api/chat/settings/mcp/servers/{id}
    DELETE /api/chat/settings/mcp/servers/{id}
    POST   /api/chat/settings/mcp/servers/{id}/test
    PUT    /api/chat/settings/mcp/servers/{id}/keys/{key_name}
    DELETE /api/chat/settings/mcp/servers/{id}/keys/{key_name}

Super-admin only, like the LLM keys. The web adds and edits http entries
only; stdio entries are written by hand and given back as they were. The
body never names a key; no response, log line or file byte carries a key
value. Fixtures and isolation
mirror `test_llm_settings_api.py`.
"""
from __future__ import annotations

import json
import logging
import os
import stat
import sys
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
from db.models import OrgMembership, User
from harness import mcp_client
from services import app_secrets
from tests.conftest import attach_session

STUB = Path(__file__).resolve().parents[1] / "smoke" / "mcp_stub_server.py"
BASE = "/api/chat/settings/mcp/servers"
SECRET = "mcp-secret-value-0123456789abcdef"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    before = dict(os.environ)
    previous_shell = app_secrets._SHELL_NAMES
    app_secrets._SHELL_NAMES = frozenset()
    mcp_client.reset_for_tests()
    yield
    mcp_client.reset_for_tests()
    app_secrets._SHELL_NAMES = previous_shell
    for name in list(os.environ):
        if name not in before and app_secrets.is_managed_key(name):
            os.environ.pop(name, None)


@pytest.fixture
def admin(_auth_db, seeded_identity):
    _engine, session_local = _auth_db
    with session_local() as db:
        user = User(id=uuid.uuid4(), email=f"mcp-admin-{uuid.uuid4().hex[:8]}@example.com",
                    password_hash=None, status="active", is_super_admin=True,
                    created_at=datetime.now(tz=timezone.utc))
        db.add(user)
        db.flush()
        db.add(OrgMembership(id=uuid.uuid4(), user_id=user.id,
                             org_id=seeded_identity["org_id"], role="admin"))
        db.commit()
        user_id = user.id
    try:
        with TestClient(main.app) as c:
            yield attach_session(c, session_local, user_id)
    finally:
        with session_local() as db:
            row = db.get(User, user_id)
            if row is not None:
                db.delete(row)
                db.commit()


def _http(**over):
    body = {"enabled": False, "url": "https://example.com/mcp"}
    body.update(over)
    return body


def _stdio_entry(sid="stub", **over):
    """A stdio entry as an admin writes it by hand: the web never makes one."""
    entry = {"id": sid, "transport": "stdio", "command": sys.executable, "args": [str(STUB)]}
    entry.update(over)
    return entry


def _hand(*entries) -> str:
    mcp_client.config_path().parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps({"servers": list(entries)})
    mcp_client.config_path().write_text(text)
    return text


def _file() -> dict:
    return json.loads(mcp_client.config_path().read_text(encoding="utf-8"))


# ── the gate ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("method,path", [
    ("get", BASE), ("put", f"{BASE}/stub"), ("delete", f"{BASE}/stub"),
    ("post", f"{BASE}/stub/test"), ("put", f"{BASE}/stub/keys/bearer"),
    ("delete", f"{BASE}/stub/keys/bearer"),
])
def test_an_org_admin_and_anonymous_are_refused(client, anon_client, method, path):
    kwargs = {}
    if method == "put":
        kwargs["json"] = {"value": "x"} if path.endswith("bearer") else _http()
    assert getattr(client, method)(path, **kwargs).status_code == 403
    assert getattr(anon_client, method)(path, **kwargs).status_code == 401
    assert not mcp_client.config_path().exists()


# ── the list and http entries ──────────────────────────────────────────────

def test_no_file_lists_nothing(admin):
    r = admin.get(BASE)
    assert r.status_code == 200
    assert r.json() == {"servers": [], "file_error": None}


def test_put_writes_an_http_entry_at_0600_with_a_derived_bearer_slot(admin):
    r = admin.put(f"{BASE}/feed", json=_http(tiers={"lookup": "destructive"}))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["key"] == {"present": False, "hint": None, "redactable": None}
    assert out["env"] == [] and out["problem"] is None and out["enabled"] is False
    assert _file()["servers"] == [{
        "id": "feed", "enabled": False, "transport": "http", "url": "https://example.com/mcp",
        "key": "PYPSA_GUI_MCP_KEY__FEED", "tiers": {"lookup": "destructive"}}]
    if os.name == "posix":
        assert stat.S_IMODE(mcp_client.config_path().stat().st_mode) == 0o600
    assert not list(mcp_client.config_path().parent.glob("*.tmp"))


@pytest.mark.parametrize("sid,body", [
    ("Bad", _http()),
    ("toolongserverid12345", _http()),
    ("a", _http(url="http://example.com/mcp")),                         # http off loopback
    ("a", _http(url="file:///etc/passwd")),
    ("a", _http(url="https://user:pass@example.com/mcp")),               # credentials in the URL
    ("a", _http(url="https://token@example.com/mcp")),
    ("a", _http(url="https://169.254.169.254/latest")),                 # metadata, link-local
    ("a", _http(url="http://169.254.169.254/latest")),
    ("a", _http(url="https://[::ffff:169.254.169.254]/latest")),         # v4-mapped link-local
    ("a", _http(url="http://[::ffff:169.254.169.254]/latest")),
    ("a", _http(url="https://[fe80::1]/mcp")),
    ("a", _http(url="https://example.com:99999/mcp")),
    ("a", {"enabled": False}),                                          # no URL
    ("a", _http(transport="stdio")),                                    # the web makes http only
    ("a", _http(command=sys.executable, args=[str(STUB)])),
    ("a", _http(env=["TOKEN"])),
    ("a", _http(tiers={"lookup": "harmless"})),
    ("a", _http(key="PYPSA_GUI_LLM_KEY__OTHER")),                       # keys are never named
    ("a", _http(enabled="yes")),
])
def test_invalid_bodies_are_refused_and_nothing_is_written(admin, sid, body):
    assert admin.put(f"{BASE}/{sid}", json=body).status_code == 422
    assert not mcp_client.config_path().exists()


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:9000/mcp", "http://127.8.0.1/mcp", "http://[::1]:9000/mcp",
    "http://localhost:9000/mcp",
])
def test_loopback_http_is_allowed(admin, url):
    assert admin.put(f"{BASE}/local", json=_http(url=url)).status_code == 200


def test_a_hand_written_url_gets_the_same_check():
    for url in ("https://u:p@example.com/mcp", "https://169.254.169.254/", "https://[fe80::1]/"):
        assert mcp_client.validate_entry({"id": "x", "transport": "http", "url": url})[1]


def test_put_replaces_in_place_and_keeps_other_entries_as_written(admin):
    hand = [{"id": "first", "transport": "http", "url": "https://a.example/mcp"},
            {"id": "BROKEN"}, "not an object",
            {"transport": "stdio", "id": "last", "command": "y", "env": {}, "args": ["-q"]}]
    _hand(*hand)
    listed = admin.get(BASE).json()["servers"]
    assert [s["id"] for s in listed] == ["first", None, None, "last"]
    assert listed[1]["problem"] and listed[2]["problem"]
    assert admin.put(f"{BASE}/first", json=_http(url="https://b.example/mcp")).status_code == 200
    assert admin.put(f"{BASE}/added", json=_http()).status_code == 200
    assert admin.delete(f"{BASE}/added").status_code == 200
    written = _file()["servers"]
    assert written[1:] == hand[1:]
    assert list(written[3]) == list(hand[3])                  # key order too
    assert written[0]["url"] == "https://b.example/mcp"


# ── stdio entries are the file's alone ─────────────────────────────────────

@pytest.mark.parametrize("entry", [
    _stdio_entry("tool"),
    {"id": "tool", "transport": "carrier-pigeon"},
    {"id": "tool"},
])
def test_an_entry_that_is_not_http_is_never_replaced_or_removed(admin, entry):
    text = _hand(entry)
    assert admin.put(f"{BASE}/tool", json=_http()).status_code == 409
    assert admin.delete(f"{BASE}/tool").status_code == 409
    assert mcp_client.config_path().read_text() == text
    listed = admin.get(BASE).json()["servers"][0]
    assert listed["id"] == "tool"


@pytest.mark.parametrize("var", [
    "LD_PRELOAD", "LD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES", "PYTHONPATH", "PYTHONSTARTUP",
    "PYTHONHOME", "NODE_OPTIONS", "PATH", "HOME", "SHELL", "LANG", "LC_ALL", "SYSTEMROOT",
    "TEMP", "TMP", "lower", "A-B", "V" * 33,
])
def test_a_variable_that_steers_the_child_is_refused_everywhere(admin, var):
    slot = mcp_client.key_slot("tool", "X")
    entry = _stdio_entry("tool", env={var: slot})
    _hand(entry)
    assert mcp_client.validate_entry(entry)[1]
    assert mcp_client.parse_servers(_file())[0] == []           # never started
    assert admin.get(BASE).json()["servers"][0]["problem"]
    assert admin.post(f"{BASE}/tool/test").status_code == 422
    if var.isupper() and len(var) <= 32 and "-" not in var:
        assert admin.put(f"{BASE}/tool/keys/{var}", json={"value": SECRET}).status_code == 422
    assert app_secrets.get_stored(slot) is None


def test_an_allowed_variable_passes():
    assert mcp_client.env_name_allowed("TARIFF_TOKEN")
    assert mcp_client.env_name_allowed("_PRIVATE")


def test_an_unreadable_file_is_reported_and_never_overwritten(admin):
    mcp_client.config_path().parent.mkdir(parents=True, exist_ok=True)
    mcp_client.config_path().write_text("{not json")
    got = admin.get(BASE).json()["file_error"]
    assert got and "not json" not in got and str(mcp_client.config_path()) not in got
    assert admin.put(f"{BASE}/a", json=_http()).status_code == 409
    assert mcp_client.config_path().read_text() == "{not json"


@pytest.mark.parametrize("text", ["{not json", "[]", '{"servers": {}}', '{"other": []}'])
def test_no_write_route_rewrites_a_file_it_cannot_read(admin, text):
    mcp_client.config_path().parent.mkdir(parents=True, exist_ok=True)
    mcp_client.config_path().write_text(text)
    assert admin.put(f"{BASE}/a", json=_http()).status_code == 409
    assert admin.delete(f"{BASE}/a").status_code == 409
    assert admin.put(f"{BASE}/a/keys/bearer", json={"value": SECRET}).status_code == 409
    assert admin.delete(f"{BASE}/a/keys/bearer").status_code == 409
    assert admin.post(f"{BASE}/a/test").status_code == 409
    assert mcp_client.config_path().read_text() == text
    assert app_secrets.get_stored("PYPSA_GUI_MCP_KEY__A") is None


def test_a_disabled_entry_is_still_validated(admin):
    _hand({"id": "off", "enabled": False, "transport": "http", "url": "http://example.com"})
    assert admin.get(BASE).json()["servers"][0]["problem"]
    assert mcp_client.parse_servers(_file())[0] == []         # disabled: never connected


# ── keys ───────────────────────────────────────────────────────────────────

def test_a_key_is_stored_hinted_and_never_echoed(admin, caplog):
    caplog.set_level(logging.DEBUG)
    admin.put(f"{BASE}/feed", json=_http())
    r = admin.put(f"{BASE}/feed/keys/bearer", json={"value": SECRET})
    assert r.status_code == 200, r.text
    assert r.json()["present"] is True and r.json()["hint"] == f"…{SECRET[-4:]}"
    assert os.environ["PYPSA_GUI_MCP_KEY__FEED"] == SECRET
    assert app_secrets.get_stored("PYPSA_GUI_MCP_KEY__FEED") == SECRET
    listed = admin.get(BASE)
    assert SECRET not in r.text and SECRET not in listed.text
    assert SECRET not in caplog.text
    assert SECRET not in mcp_client.config_path().read_text()


def test_a_key_value_reaches_no_response_log_or_file(admin, caplog):
    caplog.set_level(logging.DEBUG)
    _hand(_stdio_entry("stub", env={"TOKEN": mcp_client.key_slot("stub", "TOKEN")}))
    texts = [
        admin.put(f"{BASE}/stub/keys/TOKEN", json={"value": SECRET}).text,
        admin.put(f"{BASE}/feed", json=_http(url="http://127.0.0.1:9/mcp")).text,
        admin.put(f"{BASE}/feed/keys/bearer", json={"value": SECRET}).text,
        admin.get(BASE).text,
        admin.post(f"{BASE}/stub/test").text,
        admin.post(f"{BASE}/feed/test").text,
        admin.put(f"{BASE}/feed", json=_http(url="http://127.0.0.1:9/other")).text,
    ]
    file_text = mcp_client.config_path().read_text()
    texts.append(admin.delete(f"{BASE}/feed").text)
    texts.append(admin.delete(f"{BASE}/stub/keys/TOKEN").text)
    assert all(SECRET not in t for t in texts)
    assert SECRET not in caplog.text
    assert SECRET not in file_text and SECRET not in mcp_client.config_path().read_text()


def test_a_key_name_the_server_does_not_list_is_404(admin):
    _hand(_stdio_entry("tool", env={"TOKEN": mcp_client.key_slot("tool", "TOKEN")}))
    assert admin.put(f"{BASE}/tool/keys/bearer", json={"value": SECRET}).status_code == 404
    assert admin.put(f"{BASE}/tool/keys/OTHER", json={"value": SECRET}).status_code == 404
    assert admin.put(f"{BASE}/none/keys/TOKEN", json={"value": SECRET}).status_code == 404
    assert admin.put(f"{BASE}/tool/keys/TOKEN", json={"value": SECRET}).status_code == 200
    assert admin.put(f"{BASE}/tool/keys/TOKEN", json={"value": "a\nb"}).status_code == 422


def test_a_hand_written_key_outside_the_family_is_never_touched(admin):
    _hand({"id": "x", "transport": "stdio", "command": "c", "env": {"T": "ANTHROPIC_API_KEY"}})
    assert admin.put(f"{BASE}/x/keys/T", json={"value": SECRET}).status_code == 404
    assert admin.delete(f"{BASE}/x/keys/T").status_code == 404
    assert admin.get(BASE).json()["servers"][0]["env"][0]["present"] is False


def test_replacing_or_deleting_an_http_server_clears_its_slots(admin):
    var_slot = mcp_client.key_slot("feed", "A")
    _hand({"id": "feed", "transport": "http", "url": "https://example.com/mcp",
           "key": mcp_client.key_slot("feed"), "env": {"A": var_slot}})
    admin.put(f"{BASE}/feed/keys/A", json={"value": SECRET})
    admin.put(f"{BASE}/feed/keys/bearer", json={"value": SECRET + "b"})
    assert admin.put(f"{BASE}/feed", json=_http()).status_code == 200
    assert app_secrets.get_stored(var_slot) is None
    assert app_secrets.get_stored("PYPSA_GUI_MCP_KEY__FEED") == SECRET + "b"
    assert admin.delete(f"{BASE}/feed").status_code == 200
    assert app_secrets.get_stored("PYPSA_GUI_MCP_KEY__FEED") is None
    assert "PYPSA_GUI_MCP_KEY__FEED" not in os.environ
    assert _file() == {"servers": []}
    assert admin.delete(f"{BASE}/feed").status_code == 404


def test_the_bearer_token_is_cleared_before_the_file_is_replaced(admin, monkeypatch):
    admin.put(f"{BASE}/feed", json=_http())
    admin.put(f"{BASE}/feed/keys/bearer", json={"value": SECRET})
    seen = []
    real = os.replace

    def spy(src, dst, *a, **k):
        if os.path.basename(dst) == "mcp-servers.json":
            seen.append(app_secrets.get_stored("PYPSA_GUI_MCP_KEY__FEED"))
        return real(src, dst, *a, **k)

    monkeypatch.setattr(mcp_client.os, "replace", spy)
    assert admin.put(f"{BASE}/feed", json=_http(url="https://other.example/mcp")).status_code == 200
    assert seen == [None]


def test_moving_a_server_to_another_host_clears_its_bearer_token(admin):
    admin.put(f"{BASE}/feed", json=_http())
    admin.put(f"{BASE}/feed/keys/bearer", json={"value": SECRET})
    admin.put(f"{BASE}/feed", json=_http(url="https://example.com/mcp/v2", enabled=True))
    assert app_secrets.get_stored("PYPSA_GUI_MCP_KEY__FEED") == SECRET     # same origin
    for url in ("https://other.example/mcp", "https://example.com:8443/mcp"):
        admin.put(f"{BASE}/feed/keys/bearer", json={"value": SECRET})
        out = admin.put(f"{BASE}/feed", json=_http(url=url)).json()
        assert out["key"]["present"] is False
        assert app_secrets.get_stored("PYPSA_GUI_MCP_KEY__FEED") is None
        assert "PYPSA_GUI_MCP_KEY__FEED" not in os.environ


def test_the_family_is_managed_and_bounded():
    assert app_secrets._MCP_KEY_PREFIX == mcp_client.KEY_PREFIX
    assert app_secrets.is_managed_key("PYPSA_GUI_MCP_KEY__TARIFFS")
    assert app_secrets.is_managed_key("PYPSA_GUI_MCP_KEY__TARIFFS__TOKEN")
    assert not app_secrets.is_managed_key("PYPSA_GUI_MCP_KEY__tariffs")
    assert not app_secrets.is_managed_key("PYPSA_GUI_MCP_KEY__")
    assert not app_secrets.is_managed_key("PYPSA_GUI_MCP_KEY__A\n")
    assert len(mcp_client.key_slot("a" * 16, "V" * 32)) - len(mcp_client.KEY_PREFIX) <= 64


# ── the test route ─────────────────────────────────────────────────────────

def test_test_lists_shaped_tools_without_enabling(admin):
    _hand(_stdio_entry("stub", enabled=False, args=[str(STUB), "--forged"]))
    r = admin.post(f"{BASE}/stub/test")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ok"] is True
    tools = {t["name"]: t for t in out["tools"]}
    assert tools["mcp__stub__lookup"]["tier"] == "read"
    assert tools["mcp__stub__set_value"]["tier"] == "write"
    lookup = tools["mcp__stub__lookup"]["description"]
    assert lookup.endswith("Safety: read") and lookup.count("Safety:") == 1
    assert "</untrusted_data>" not in lookup
    assert "STUB-SERVER-INSTRUCTIONS" not in r.text
    assert not mcp_client._CONNECTIONS          # the probe never joins the pool


def test_a_failing_server_gets_a_fixed_message(admin):
    _hand(_stdio_entry("gone", command="/nonexistent/mcp-server", args=[]))
    out = admin.post(f"{BASE}/gone/test").json()
    assert out == {"ok": False, "error_kind": "mcp_unavailable",
                   "message": "The server could not be started or reached."}


def test_test_of_an_unknown_server_is_404(admin):
    assert admin.post(f"{BASE}/nobody/test").status_code == 404


@pytest.fixture
def redirecting():
    """A loopback server that answers every POST with a 307 to a second one,
    which counts what reaches it."""
    hits: list[str] = []

    class Target(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            hits.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    target = HTTPServer(("127.0.0.1", 0), Target)

    class Redirect(Target):
        def do_POST(self):  # noqa: N802
            self.send_response(307)
            self.send_header("Location", f"http://127.0.0.1:{target.server_port}/mcp")
            self.send_header("Content-Length", "0")
            self.end_headers()

    origin = HTTPServer(("127.0.0.1", 0), Redirect)
    for server in (target, origin):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{origin.server_port}/mcp", hits
    finally:
        for server in (origin, target):
            server.shutdown()
            server.server_close()


def test_a_redirect_is_never_followed(admin, redirecting):
    url, hits = redirecting
    assert admin.put(f"{BASE}/hop", json=_http(url=url)).status_code == 200
    out = admin.post(f"{BASE}/hop/test").json()
    assert out == {"ok": False, "error_kind": "mcp_unavailable",
                   "message": "The server could not be started or reached."}
    assert hits == []
