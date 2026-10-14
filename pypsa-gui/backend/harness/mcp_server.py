"""
Local MCP exposure (chat harness issue 11, MCP build step 4; owner decision
Q8 = option 2; `.scratch/harness/mcp-spec.md` Part B).

An external agent on this machine (Claude Code, Codex, a Kimi agent) drives
the running app through `POST /api/mcp` (`routers/mcp.py`), the Streamable
HTTP transport of the Model Context Protocol with JSON responses. This module
is the protocol layer behind that route; the route owns HTTP, the token check
and the identity binding.

* **Local mode only**, behind a per-launch bearer token written with the URL
  to `<app data>/mcp-endpoint.json` (0600) when the app starts.
* **One chat session per MCP session** (`mcp-<hex>`, owned by the local
  user); its calls are recorded as `mcp/call` and `mcp/result` events.
* **The catalogue** minus the harness control tools (they shape the in-app
  model's turn) and the panel-driving tools (there is no panel stream to
  drive); `read_result` stays. External MCP tools are never re-exported:
  they are not in the catalogue.
* **Every call runs through `harness.loop.dispatch_tool_call`**, so no step
  is skipped; `write` and above are carded in every mode (the owner's "every
  write shows its card"). The card waits in `pending_cards` for the panel and
  is answered through `POST /api/chat/{session_id}/confirm`.
"""
from __future__ import annotations

import json
import os
import secrets
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from harness.mcp_client import SUPPORTED_VERSIONS

SESSION_PREFIX = "mcp-"
SERVER_INFO = {"name": "pypsa-gui", "version": "1"}
_DESTRUCTIVE = {"destructive", "execution", "execution_long_running"}
_TOKEN: str | None = None
# Bounds on what one token holder can tie up: open sessions per user (the
# oldest idle one is evicted for a new one) and calls in flight per session
# (a call waiting on a card holds a worker thread for up to its TTL).
MAX_SESSIONS_PER_USER = 8
_IN_FLIGHT: set[str] = set()
_IN_FLIGHT_LOCK = threading.Lock()


class SessionCapReached(Exception):
    """Every one of the user's sessions has a call waiting; none to evict."""


# ── the endpoint file ──────────────────────────────────────────────────────

def endpoint_path() -> Path:
    from app_paths import app_data_dir
    return app_data_dir() / "mcp-endpoint.json"


def token() -> str:
    global _TOKEN
    if _TOKEN is None:
        _TOKEN = secrets.token_urlsafe(32)
    return _TOKEN


def api_origin() -> str:
    """This app's own origin. In desktop mode the launcher pins the CORS
    allowlist to exactly the bound loopback origin, which is also where the
    API is served; in development the Vite origin proxies `/api`."""
    from settings import get_settings
    origins = [o.strip() for o in str(get_settings().cors_allowed_origins).split(",")
               if o.strip().startswith("http")]
    return (origins[0] if origins else "http://127.0.0.1:8000").rstrip("/")


def write_endpoint_file() -> Path:
    """Write `{url, token}` for local MCP clients, readable by this user
    only, replacing the previous launch's file atomically."""
    path = endpoint_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"url": api_origin() + "/api/mcp", "token": token(),
                          "pid": os.getpid()})
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".mcp-endpoint.")
    try:
        os.chmod(tmp, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


def token_matches(header: str | None) -> bool:
    """Constant-time check of `Authorization: Bearer <token>`. Compared as
    bytes: `compare_digest` raises on a non-ASCII str, which a client must
    get as a refusal, never as a server error."""
    if not header or not header.startswith("Bearer "):
        return False
    given = header[len("Bearer "):].encode("utf-8", errors="surrogateescape")
    return secrets.compare_digest(given, token().encode("ascii"))


# ── what is exposed ────────────────────────────────────────────────────────

def exposed_tools(toolset: str | None = None) -> list[dict[str, Any]]:
    """The catalogue an external agent sees, as MCP tool definitions."""
    from harness import catalogue
    from harness.toolsets import CONTROL_TOOLS, filter_tools
    tools = [t for t in catalogue.TOOLS
             if (t["name"] not in CONTROL_TOOLS or t["name"] == "read_result")
             and catalogue.TOOL_ROUTES.get(t["name"]) != catalogue._UI_EVENT]
    if toolset and toolset != "all":
        tools = filter_tools(tools, toolset)
        tools = [t for t in tools if t["name"] not in CONTROL_TOOLS or t["name"] == "read_result"]
    out = []
    for tool in tools:
        tier = catalogue.safety_tier_for(tool["name"])
        out.append({"name": tool["name"], "description": tool["description"],
                    "inputSchema": tool["input_schema"],
                    "annotations": {"readOnlyHint": tier == "read",
                                    "destructiveHint": tier in _DESTRUCTIVE}})
    return out


# ── sessions ───────────────────────────────────────────────────────────────

def _user_sessions(user_id: str | None) -> list[Any]:
    from harness.session import _SESSIONS, _SESSIONS_LOCK
    from services import chat_service
    with _SESSIONS_LOCK:
        sessions = [s for sid, s in _SESSIONS.items() if sid.startswith(SESSION_PREFIX)]
    return [s for s in sessions if chat_service.session_owner_allows(s, user_id)]


def _idle(session: Any) -> bool:
    with session._lock:
        waiting = bool(session.pending_confirmations)
    with _IN_FLIGHT_LOCK:
        running = session.session_id in _IN_FLIGHT
    return not waiting and not running


def open_session(user_id: str | None, client_name: str | None) -> Any:
    """A new `mcp-` session for this user. At `MAX_SESSIONS_PER_USER` the
    oldest idle one is closed first; if none is idle, `SessionCapReached`."""
    from services import chat_service
    mine = _user_sessions(user_id)
    while len(mine) >= MAX_SESSIONS_PER_USER:
        idle = sorted((s for s in mine if _idle(s)), key=lambda s: s.last_activity)
        if not idle:
            raise SessionCapReached
        close_session(idle[0])
        mine.remove(idle[0])
    sid = SESSION_PREFIX + uuid.uuid4().hex
    session = chat_service.get_or_create_session(sid, owner_user_id=user_id)
    session.mcp_client = (client_name or "MCP client")[:80]
    return session


def find_session(sid: str | None, user_id: str | None) -> Any | None:
    from services import chat_service
    if not sid or not sid.startswith(SESSION_PREFIX):
        return None
    session = chat_service.get_session(sid)
    if session is None or not chat_service.session_owner_allows(session, user_id):
        return None
    session.last_activity = time.monotonic()
    return session


def begin_call(session: Any) -> bool:
    """Claim the session's one call slot; False when a call is running."""
    with _IN_FLIGHT_LOCK:
        if session.session_id in _IN_FLIGHT:
            return False
        _IN_FLIGHT.add(session.session_id)
        return True


def end_call(session: Any) -> None:
    with _IN_FLIGHT_LOCK:
        _IN_FLIGHT.discard(session.session_id)


def close_session(session: Any) -> None:
    from harness.session import drop_session
    with session._lock:
        tokens = list(session.pending_confirmations)
    for tok in tokens:
        try:
            session.record_decision(tok, "deny")
        except Exception:  # noqa: BLE001 — already decided or expired
            pass
    drop_session(session.session_id)


def pending_cards(user_id: str | None) -> list[dict[str, Any]]:
    """Every card an external agent's call is waiting on, for the panel."""
    from harness.session import _SESSIONS, _SESSIONS_LOCK
    from services import chat_service
    with _SESSIONS_LOCK:
        sessions = [s for sid, s in _SESSIONS.items() if sid.startswith(SESSION_PREFIX)]
    now = time.monotonic()
    cards = []
    for session in sessions:
        if not chat_service.session_owner_allows(session, user_id):
            continue
        with session._lock:
            pending = list(session.pending_confirmations.values())
        for pc in pending:
            if pc.is_expired(now):
                continue
            cards.append({"session_id": session.session_id,
                          "client": getattr(session, "mcp_client", None) or "MCP client",
                          "tool_name": pc.tool_name, "args": pc.args,
                          "safety_tier": pc.safety_tier,
                          "confirmation_token": pc.token,
                          "ttl_seconds": max(0.0, pc.expires_at - now)})
    return cards


# ── the protocol ───────────────────────────────────────────────────────────

def _error(rid: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def _result(rid: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def initialize(rid: Any, params: dict[str, Any]) -> dict[str, Any]:
    asked = params.get("protocolVersion")
    version = asked if asked in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[1]
    return _result(rid, {"protocolVersion": version,
                         "capabilities": {"tools": {"listChanged": False}},
                         "serverInfo": SERVER_INFO})


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(b.get("text", "")) for b in content
                         if isinstance(b, dict) and b.get("type") == "text")
    return json.dumps(content, default=str)


def call_tool(session: Any, rid: Any, params: dict[str, Any], *, toolset: str | None) -> dict[str, Any]:
    """Run one `tools/call` through the loop's dispatch path. Blocks while a
    card waits for its answer in the app."""
    from harness import eventlog
    from harness.loop import dispatch_tool_call
    from services.pypsa_service import PyPSAService

    name = params.get("name")
    args = params.get("arguments") or {}
    if not isinstance(name, str) or not isinstance(args, dict):
        return _error(rid, -32602, "tools/call needs a tool name and an arguments object")
    offered = {t["name"] for t in exposed_tools(toolset)}
    call_id = "mcp_" + uuid.uuid4().hex[:16]
    eventlog.record(session, "mcp/call", call_id=call_id, name=name, args=args)
    collector: list[dict[str, Any]] = []
    errors: list[str] = []
    for event, payload in dispatch_tool_call(
            session, {"id": call_id, "name": name, "input": args}, collector,
            offered_tool_names=offered, turn_ctx=PyPSAService.get_active_context(),
            result_char_budget={"used": 0}, guided=True):
        if event == "tool_error" and isinstance(payload, dict):
            errors.append(str(payload.get("error_kind") or "tool_error"))
    result = collector[0] if collector else {"is_error": True, "content": "no result"}
    is_error = bool(result.get("is_error"))
    text = _text(result.get("content"))
    if is_error and errors and errors[0] not in text:
        text = f"{errors[0]}: {text}"
    eventlog.record(session, "mcp/result", call_id=call_id, name=name, is_error=is_error,
                    error_kind=errors[0] if errors else None)
    return _result(rid, {"content": [{"type": "text", "text": text}], "isError": is_error})


def handle(session: Any, message: dict[str, Any], *, toolset: str | None) -> dict[str, Any]:
    """Answer one request on an open session."""
    rid, method = message.get("id"), message.get("method")
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    if method == "ping":
        return _result(rid, {})
    if method == "tools/list":
        return _result(rid, {"tools": exposed_tools(toolset)})
    if method == "tools/call":
        return call_tool(session, rid, params, toolset=toolset)
    return _error(rid, -32601, f"method not found: {method}")
