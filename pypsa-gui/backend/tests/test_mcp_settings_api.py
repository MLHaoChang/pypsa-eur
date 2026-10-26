"""
Chat harness issue 19, slice 2 — the MCP server settings routes
(`.scratch/harness/mcp-spec.md` A.1).

    GET    /api/chat/settings/mcp/servers
    PUT    /api/chat/settings/mcp/servers/{id}
    DELETE /api/chat/settings/mcp/servers/{id}
    POST   /api/chat/settings/mcp/servers/{id}/test
    PUT    /api/chat/settings/mcp/servers/{id}/keys/{name}
    DELETE /api/chat/settings/mcp/servers/{id}/keys/{name}

Super-admin only, like the LLM keys. The body names variables, never keys;
no response and no log line carries a key value. Fixtures and isolation
mirror `test_llm_settings_api.py`.
"""
from __future__ import annotations

import json
import logging
import os
import stat
import sys
import uuid
from datetime import datetime, timezone
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


def _stdio(**over):
    body = {"enabled": False, "transport": "stdio", "command": sys.executable,
            "args": [str(STUB)], "env": []}
    body.update(over)
    return body


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
        kwargs["json"] = {"value": "x"} if path.endswith("bearer") else _stdio()
    assert getattr(client, method)(path, **kwargs).status_code == 403
    assert getattr(anon_client, method)(path, **kwargs).status_code == 401
    assert not mcp_client.config_path().exists()


# ── the list ───────────────────────────────────────────────────────────────

def test_no_file_lists_nothing(admin):
    r = admin.get(BASE)
    assert r.status_code == 200
    assert r.json() == {"servers": [], "file_error": None}


def test_put_writes_the_file_at_0600_with_derived_keys(admin):
    r = admin.put(f"{BASE}/tariffs", json=_stdio(env=["TARIFF_TOKEN"], tiers={"lookup": "destructive"}))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["env"] == [{"name": "TARIFF_TOKEN", "present": False, "hint": None, "redactable": None}]
    assert out["key"] is None and out["problem"] is None and out["enabled"] is False
    entry = _file()["servers"][0]
    assert entry["env"] == {"TARIFF_TOKEN": "PYPSA_GUI_MCP_KEY__TARIFFS__TARIFF_TOKEN"}
    assert entry["tiers"] == {"lookup": "destructive"}
    if os.name == "posix":
        assert stat.S_IMODE(mcp_client.config_path().stat().st_mode) == 0o600
    assert not list(mcp_client.config_path().parent.glob("*.tmp"))


def test_an_http_server_gets_a_bearer_slot(admin):
    r = admin.put(f"{BASE}/feed", json={"transport": "http", "url": "https://example.com/mcp"})
    assert r.status_code == 200, r.text
    assert r.json()["key"] == {"present": False, "hint": None, "redactable": None}
    assert _file()["servers"][0]["key"] == "PYPSA_GUI_MCP_KEY__FEED"


@pytest.mark.parametrize("sid,body", [
    ("Bad", _stdio()),
    ("toolongserverid12345", _stdio()),
    ("a", {"transport": "http", "url": "http://example.com/mcp"}),       # http off loopback
    ("a", {"transport": "http", "url": "file:///etc/passwd"}),
    ("a", _stdio(command="")),
    ("a", _stdio(env=["lower"])),
    ("a", _stdio(env=["PATH"])),                                        # already given
    ("a", _stdio(env=["X", "X"])),
    ("a", _stdio(tiers={"lookup": "harmless"})),
    ("a", _stdio(key="PYPSA_GUI_LLM_KEY__OTHER")),                       # keys are never named
    ("a", _stdio(enabled="yes")),
])
def test_invalid_bodies_are_refused_and_nothing_is_written(admin, sid, body):
    assert admin.put(f"{BASE}/{sid}", json=body).status_code == 422
    assert not mcp_client.config_path().exists()


def test_loopback_http_is_allowed(admin):
    assert admin.put(f"{BASE}/local", json={"transport": "http",
                                             "url": "http://127.0.0.1:9000/mcp"}).status_code == 200


def test_put_replaces_in_place_and_keeps_other_entries_as_written(admin):
    mcp_client.config_path().parent.mkdir(parents=True, exist_ok=True)
    hand = [{"id": "first", "transport": "stdio", "command": "x"},
            {"id": "BROKEN"}, "not an object",
            {"id": "last", "transport": "stdio", "command": "y"}]
    mcp_client.config_path().write_text(json.dumps({"servers": hand}))
    listed = admin.get(BASE).json()["servers"]
    assert [s["id"] for s in listed] == ["first", None, None, "last"]
    assert listed[1]["problem"] and listed[2]["problem"]
    assert admin.put(f"{BASE}/first", json=_stdio(command="z")).status_code == 200
    written = _file()["servers"]
    assert written[1:] == hand[1:]
    assert written[0]["command"] == "z"


def test_an_unreadable_file_is_reported_and_never_overwritten(admin):
    mcp_client.config_path().parent.mkdir(parents=True, exist_ok=True)
    mcp_client.config_path().write_text("{not json")
    assert admin.get(BASE).json()["file_error"]
    assert admin.put(f"{BASE}/a", json=_stdio()).status_code == 409
    assert mcp_client.config_path().read_text() == "{not json"


def test_a_disabled_entry_is_still_validated(admin):
    mcp_client.config_path().parent.mkdir(parents=True, exist_ok=True)
    mcp_client.config_path().write_text(json.dumps({"servers": [
        {"id": "off", "enabled": False, "transport": "http", "url": "http://example.com"}]}))
    assert admin.get(BASE).json()["servers"][0]["problem"]
    assert mcp_client.parse_servers(_file())[0] == []         # disabled: never connected


# ── keys ───────────────────────────────────────────────────────────────────

def test_a_key_is_stored_hinted_and_never_echoed(admin, caplog):
    caplog.set_level(logging.DEBUG)
    admin.put(f"{BASE}/feed", json={"transport": "http", "url": "https://example.com/mcp"})
    r = admin.put(f"{BASE}/feed/keys/bearer", json={"value": SECRET})
    assert r.status_code == 200, r.text
    assert r.json()["present"] is True and r.json()["hint"] == f"…{SECRET[-4:]}"
    assert os.environ["PYPSA_GUI_MCP_KEY__FEED"] == SECRET
    assert app_secrets.get_stored("PYPSA_GUI_MCP_KEY__FEED") == SECRET
    listed = admin.get(BASE)
    assert SECRET not in r.text and SECRET not in listed.text
    assert SECRET not in caplog.text
    assert SECRET not in mcp_client.config_path().read_text()


def test_a_key_name_the_server_does_not_list_is_404(admin):
    admin.put(f"{BASE}/tool", json=_stdio(env=["TOKEN"]))
    assert admin.put(f"{BASE}/tool/keys/bearer", json={"value": SECRET}).status_code == 404
    assert admin.put(f"{BASE}/tool/keys/OTHER", json={"value": SECRET}).status_code == 404
    assert admin.put(f"{BASE}/none/keys/TOKEN", json={"value": SECRET}).status_code == 404
    assert admin.put(f"{BASE}/tool/keys/TOKEN", json={"value": SECRET}).status_code == 200
    assert admin.put(f"{BASE}/tool/keys/TOKEN", json={"value": "a\nb"}).status_code == 422


def test_a_hand_written_key_outside_the_family_is_never_touched(admin):
    mcp_client.config_path().parent.mkdir(parents=True, exist_ok=True)
    mcp_client.config_path().write_text(json.dumps({"servers": [
        {"id": "x", "transport": "stdio", "command": "c", "env": {"T": "ANTHROPIC_API_KEY"}}]}))
    assert admin.put(f"{BASE}/x/keys/T", json={"value": SECRET}).status_code == 404
    assert admin.delete(f"{BASE}/x/keys/T").status_code == 404
    assert admin.get(BASE).json()["servers"][0]["env"][0]["present"] is False


def test_dropping_a_variable_or_deleting_the_server_clears_its_slots(admin):
    admin.put(f"{BASE}/tool", json=_stdio(env=["A", "B"]))
    admin.put(f"{BASE}/tool/keys/A", json={"value": SECRET})
    admin.put(f"{BASE}/tool/keys/B", json={"value": SECRET + "b"})
    admin.put(f"{BASE}/tool", json=_stdio(env=["B"]))
    assert app_secrets.get_stored("PYPSA_GUI_MCP_KEY__TOOL__A") is None
    assert app_secrets.get_stored("PYPSA_GUI_MCP_KEY__TOOL__B") == SECRET + "b"
    assert admin.delete(f"{BASE}/tool").status_code == 200
    assert app_secrets.get_stored("PYPSA_GUI_MCP_KEY__TOOL__B") is None
    assert "PYPSA_GUI_MCP_KEY__TOOL__B" not in os.environ
    assert _file() == {"servers": []}
    assert admin.delete(f"{BASE}/tool").status_code == 404


def test_the_family_is_managed_and_bounded():
    assert app_secrets.is_managed_key("PYPSA_GUI_MCP_KEY__TARIFFS")
    assert app_secrets.is_managed_key("PYPSA_GUI_MCP_KEY__TARIFFS__TOKEN")
    assert not app_secrets.is_managed_key("PYPSA_GUI_MCP_KEY__tariffs")
    assert not app_secrets.is_managed_key("PYPSA_GUI_MCP_KEY__")
    assert not app_secrets.is_managed_key("PYPSA_GUI_MCP_KEY__A\n")
    assert len(mcp_client.key_slot("a" * 16, "V" * 32)) - len(mcp_client.KEY_PREFIX) <= 64


# ── the test route ─────────────────────────────────────────────────────────

def test_test_lists_shaped_tools_without_enabling(admin):
    admin.put(f"{BASE}/stub", json=_stdio(args=[str(STUB), "--forged"]))
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
    admin.put(f"{BASE}/gone", json=_stdio(command="/nonexistent/mcp-server"))
    out = admin.post(f"{BASE}/gone/test").json()
    assert out == {"ok": False, "error_kind": "mcp_unavailable",
                   "message": "The server could not be started or reached."}


def test_test_of_an_unknown_server_is_404(admin):
    assert admin.post(f"{BASE}/nobody/test").status_code == 404
