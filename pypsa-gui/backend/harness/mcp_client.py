"""
The harness as an MCP client (chat harness issue 19, slice 1;
`.scratch/harness/mcp-spec.md` Part A).

An admin lists MCP servers in `<app data>/mcp-servers.json`. Their tools join
a chat session's catalogue as `mcp__<server>__<tool>`, for every provider,
behind the same confirmation card and untrusted fence as the built-in tools.

* **Zero by default.** No file means no tool, no event and no connection.
* **Fixed per session.** The list is taken once per chat session
  (`session_tools`) and recorded as an `mcp/catalogue` event; an edited file
  or a server's `tools/list_changed` reaches the next session, never this one.
* **Tiers.** `write` unless the server marks a tool `readOnlyHint` (then
  `read`) or `destructiveHint` (then `destructive`); an admin's `tiers`
  override only ever raises one. The harness appends the `Safety:` marker
  itself after removing any the server wrote.
* **External tools always ask** (owner decision Q20): the gate cards an
  external tool whose tier is not `read` in Expert as well as Guided, and
  never auto-approves one (`confirm._confirm_destructive_tool`).
* **What a server cannot do.** Its `instructions` are dropped; its prompts
  and resources are not read; its own requests (sampling, elicitation,
  roots) are answered "method not found"; images in results are dropped.
* **No SDK.** The protocol subset used here (initialize, tools/list,
  tools/call, cancellation, ping) is plain JSON-RPC over a stdio subprocess
  or Streamable HTTP (`httpx`), so the bundle gains no dependency (spec A.7).
"""
from __future__ import annotations

import hashlib
import ipaddress
import itertools
import json
import logging
import os
import re
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from fastapi import HTTPException

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2025-06-18"
PREFIX = "mcp__"
NAME_MAX = 64                 # the tool-name limit both wires share
DESCRIPTION_CAP = 1024
SCHEMA_MAX_BYTES = 8 * 1024
SCHEMA_MAX_DEPTH = 6
KEY_PREFIX = "PYPSA_GUI_MCP_KEY__"
_ID = re.compile(r"[a-z0-9]{1,16}")
_CHILD_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "SYSTEMROOT", "TEMP", "TMP")
_TIER_ORDER = ("read", "write", "destructive", "execution", "execution_long_running")

# The refusals, as `{"error_kind": ...}` literals so
# tests/test_tool_error_kind_manifest.py sees every kind this module raises.
_UNAVAILABLE = {"error_kind": "mcp_unavailable"}
_TIMEOUT = {"error_kind": "mcp_timeout"}
_TOOL_ERROR = {"error_kind": "mcp_tool_error"}
_PROTOCOL = {"error_kind": "mcp_protocol_error"}


class _Failure(Exception):
    def __init__(self, kind: dict[str, str], message: str) -> None:
        super().__init__(message)
        self.kind = kind


def _refuse(kind: dict[str, str], message: str, status: int = 502) -> HTTPException:
    return HTTPException(status_code=status, detail={**kind, "message": message})


# ── the admin's list ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class ServerConfig:
    id: str
    transport: str
    command: str | None = None
    args: tuple[str, ...] = ()
    url: str | None = None
    env: tuple[tuple[str, str], ...] = ()
    key: str | None = None
    tiers: tuple[tuple[str, str], ...] = ()

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(repr(self).encode()).hexdigest()


def config_path() -> Path:
    from app_paths import app_data_dir
    return app_data_dir() / "mcp-servers.json"


def _https_or_loopback(url: str) -> bool:
    parts = urlsplit(url)
    if parts.scheme == "https":
        return bool(parts.hostname)
    if parts.scheme != "http" or not parts.hostname:
        return False
    if parts.hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(parts.hostname).is_loopback
    except ValueError:
        return False


def _parse_one(entry: Any) -> tuple[ServerConfig | None, str | None]:
    if not isinstance(entry, dict):
        return None, "an entry must be an object"
    sid = entry.get("id")
    if not isinstance(sid, str) or not _ID.fullmatch(sid):
        return None, f"server id {sid!r} must be 1-16 characters of [a-z0-9]"
    if entry.get("enabled", True) is False:
        return None, None
    transport = entry.get("transport")
    env = entry.get("env") or {}
    if not isinstance(env, dict) or not all(
            isinstance(k, str) and isinstance(v, str) and v.startswith(KEY_PREFIX)
            for k, v in env.items()):
        return None, f"{sid}: env values must name keys starting {KEY_PREFIX}"
    key = entry.get("key")
    if key is not None and not (isinstance(key, str) and key.startswith(KEY_PREFIX)):
        return None, f"{sid}: key must name a key starting {KEY_PREFIX}"
    tiers = entry.get("tiers") or {}
    if not isinstance(tiers, dict) or not all(t in _TIER_ORDER for t in tiers.values()):
        return None, f"{sid}: every tier override must be one of {', '.join(_TIER_ORDER)}"
    common = {"env": tuple(sorted(env.items())), "key": key, "tiers": tuple(sorted(tiers.items()))}
    if transport == "stdio":
        command, args = entry.get("command"), entry.get("args") or []
        if not isinstance(command, str) or not command or not all(isinstance(a, str) for a in args):
            return None, f"{sid}: a stdio server needs a command and string args"
        return ServerConfig(sid, "stdio", command=command, args=tuple(args), **common), None
    if transport == "http":
        url = entry.get("url")
        if not isinstance(url, str) or not _https_or_loopback(url):
            return None, f"{sid}: an http server needs an https URL (http only on loopback)"
        return ServerConfig(sid, "http", url=url, **common), None
    return None, f"{sid}: transport must be stdio or http"


def parse_servers(data: Any) -> tuple[list[ServerConfig], list[str]]:
    """The enabled, valid servers and a reason for every entry refused."""
    entries = data.get("servers") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return [], ["the file must be an object with a servers list"]
    servers, problems, seen = [], [], set()
    for entry in entries:
        cfg, problem = _parse_one(entry)
        if problem:
            problems.append(problem)
        elif cfg is not None and cfg.id in seen:
            problems.append(f"{cfg.id}: duplicate server id")
        elif cfg is not None:
            seen.add(cfg.id)
            servers.append(cfg)
    return servers, problems


def _load() -> list[ServerConfig]:
    path = config_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("mcp: %s is unreadable; no servers", path)
        return []
    servers, problems = parse_servers(data)
    for problem in problems:
        logger.warning("mcp: server refused: %s", problem)
    return servers


# ── the connections ────────────────────────────────────────────────────────

def _call_timeout() -> float:
    from harness import budget
    return max(0.2, budget.PER_TOOL_TIMEOUT_SECONDS * 0.9)


class _Connection:
    """One initialized JSON-RPC session with a server."""

    def __init__(self, cfg: ServerConfig) -> None:
        self.cfg = cfg
        self._ids = itertools.count(1)

    def initialize(self) -> None:
        self.request("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                    "clientInfo": {"name": "pypsa-gui", "version": "1"}},
                     timeout=_call_timeout())
        # The server's `instructions` are deliberately not kept.
        self.notify("notifications/initialized", {})

    def list_tools(self) -> list[dict[str, Any]]:
        tools, cursor = [], None
        for _ in range(20):
            params = {"cursor": cursor} if cursor else {}
            page = self.request("tools/list", params, timeout=_call_timeout())
            tools.extend(t for t in page.get("tools") or [] if isinstance(t, dict))
            cursor = page.get("nextCursor")
            if not cursor:
                break
        return tools

    def request(self, method: str, params: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        raise NotImplementedError

    def notify(self, method: str, params: dict[str, Any]) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass

    @property
    def alive(self) -> bool:
        return True


def _refusal_for(message: dict[str, Any]) -> dict[str, Any] | None:
    """The answer to a request the server sends us: ping is answered, every
    other method (sampling, elicitation, roots) is refused."""
    if "method" not in message or "id" not in message:
        return None
    if message["method"] == "ping":
        return {"jsonrpc": "2.0", "id": message["id"], "result": {}}
    return {"jsonrpc": "2.0", "id": message["id"],
            "error": {"code": -32601, "message": f"method not found: {message['method']}"}}


def _unwrap(message: dict[str, Any]) -> dict[str, Any]:
    if "error" in message:
        error = message.get("error") or {}
        raise _Failure(_PROTOCOL, f"server error {error.get('code')}: {error.get('message')}")
    result = message.get("result")
    if not isinstance(result, dict):
        raise _Failure(_PROTOCOL, "the server's reply carried no result")
    return result


class _StdioConnection(_Connection):
    def __init__(self, cfg: ServerConfig) -> None:
        super().__init__(cfg)
        env = {k: os.environ[k] for k in _CHILD_ENV if k in os.environ}
        env.update({var: os.environ.get(key, "") for var, key in cfg.env})
        try:
            self.proc = subprocess.Popen(
                [cfg.command, *cfg.args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, env=env, text=True, encoding="utf-8", bufsize=1)
        except OSError as exc:
            raise _Failure(_UNAVAILABLE, f"{cfg.id}: could not start: {exc.strerror or exc}") from exc
        self._write_lock = threading.Lock()
        self._pending: dict[int, list[Any]] = {}
        self._pending_lock = threading.Lock()
        threading.Thread(target=self._read, daemon=True, name=f"mcp-{cfg.id}").start()

    @property
    def alive(self) -> bool:
        return self.proc.poll() is None

    def _send(self, message: dict[str, Any]) -> None:
        try:
            with self._write_lock:
                self.proc.stdin.write(json.dumps(message) + "\n")
                self.proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise _Failure(_UNAVAILABLE, f"{self.cfg.id}: the server is gone") from exc

    def _read(self) -> None:
        for line in self.proc.stdout:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if not isinstance(message, dict):
                continue
            refusal = _refusal_for(message)
            if refusal is not None:
                try:
                    self._send(refusal)
                except _Failure:
                    pass
                continue
            with self._pending_lock:
                slot = self._pending.get(message.get("id"))
            if slot is not None:
                slot[1] = message
                slot[0].set()
        with self._pending_lock:                      # the server exited: fail every waiter
            for slot in self._pending.values():
                slot[0].set()

    def request(self, method: str, params: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        rid = next(self._ids)
        slot: list[Any] = [threading.Event(), None]
        with self._pending_lock:
            self._pending[rid] = slot
        try:
            self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
            if not slot[0].wait(timeout):
                try:
                    self.notify("notifications/cancelled", {"requestId": rid, "reason": "timeout"})
                except _Failure:
                    pass
                raise _Failure(_TIMEOUT, f"{self.cfg.id}: {method} took longer than {timeout:.1f}s")
        finally:
            with self._pending_lock:
                self._pending.pop(rid, None)
        if slot[1] is None:
            raise _Failure(_UNAVAILABLE, f"{self.cfg.id}: the server exited")
        return _unwrap(slot[1])

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def close(self) -> None:
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.terminate()
            self.proc.wait(timeout=2)
        except Exception:  # noqa: BLE001 — closing is best effort
            self.proc.kill()


def _http_client():
    import httpx
    return httpx.Client(follow_redirects=False)


def _sse_messages(text: str) -> list[dict[str, Any]]:
    messages, data = [], []
    for line in text.splitlines() + [""]:
        if line.startswith("data:"):
            data.append(line[5:].lstrip())
        elif not line and data:
            try:
                messages.append(json.loads("\n".join(data)))
            except ValueError:
                pass
            data = []
    return [m for m in messages if isinstance(m, dict)]


class _HttpConnection(_Connection):
    def __init__(self, cfg: ServerConfig) -> None:
        super().__init__(cfg)
        self.client = _http_client()
        self.session_id: str | None = None
        self.initialized = False

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json, text/event-stream",
                   "Content-Type": "application/json"}
        if self.initialized:
            headers["MCP-Protocol-Version"] = PROTOCOL_VERSION
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if self.cfg.key and os.environ.get(self.cfg.key):
            headers["Authorization"] = f"Bearer {os.environ[self.cfg.key]}"
        return headers

    def _post(self, message: dict[str, Any], timeout: float):
        import httpx
        try:
            response = self.client.post(self.cfg.url, json=message, headers=self._headers(),
                                        timeout=timeout)
        except httpx.TimeoutException as exc:
            raise _Failure(_TIMEOUT, f"{self.cfg.id}: no answer within {timeout:.1f}s") from exc
        except httpx.HTTPError as exc:
            raise _Failure(_UNAVAILABLE, f"{self.cfg.id}: {type(exc).__name__}") from exc
        if response.headers.get("Mcp-Session-Id"):
            self.session_id = response.headers["Mcp-Session-Id"]
        return response

    def request(self, method: str, params: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        rid = next(self._ids)
        try:
            response = self._post({"jsonrpc": "2.0", "id": rid, "method": method, "params": params},
                                  timeout)
        except _Failure as failure:
            if failure.kind is _TIMEOUT:
                try:
                    self.notify("notifications/cancelled", {"requestId": rid, "reason": "timeout"})
                except _Failure:
                    pass
            raise
        if response.status_code >= 400:
            raise _Failure(_UNAVAILABLE, f"{self.cfg.id}: HTTP {response.status_code}")
        if "text/event-stream" in response.headers.get("Content-Type", ""):
            candidates = _sse_messages(response.text)
        else:
            try:
                body = response.json()
            except ValueError as exc:
                raise _Failure(_PROTOCOL, f"{self.cfg.id}: the reply is not JSON") from exc
            candidates = body if isinstance(body, list) else [body]
        for message in candidates:
            if not isinstance(message, dict):
                continue
            refusal = _refusal_for(message)
            if refusal is not None:
                try:
                    self._post(refusal, timeout)
                except _Failure:
                    pass
            elif message.get("id") == rid:
                if method == "initialize":
                    self.initialized = True
                return _unwrap(message)
        raise _Failure(_PROTOCOL, f"{self.cfg.id}: no reply to {method}")

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._post({"jsonrpc": "2.0", "method": method, "params": params}, _call_timeout())

    def close(self) -> None:
        self.client.close()


_CONNECTIONS: dict[str, _Connection] = {}
_CONNECTIONS_LOCK = threading.Lock()


def _connection(cfg: ServerConfig) -> _Connection:
    """The live connection to `cfg`, (re)connecting when there is none, it
    died, or the admin changed the server's entry."""
    with _CONNECTIONS_LOCK:
        current = _CONNECTIONS.get(cfg.id)
        if current is not None and current.cfg == cfg and current.alive:
            return current
        if current is not None:
            current.close()
            _CONNECTIONS.pop(cfg.id, None)
        conn = _StdioConnection(cfg) if cfg.transport == "stdio" else _HttpConnection(cfg)
        try:
            conn.initialize()
        except Exception:
            conn.close()
            raise
        _CONNECTIONS[cfg.id] = conn
        return conn


def reset_for_tests() -> None:
    with _CONNECTIONS_LOCK:
        for conn in _CONNECTIONS.values():
            conn.close()
        _CONNECTIONS.clear()


# ── the catalogue entry ────────────────────────────────────────────────────

def _depth(node: Any, level: int = 0) -> int:
    if isinstance(node, dict):
        return max([level] + [_depth(v, level + 1) for v in node.values()])
    if isinstance(node, list):
        return max([level] + [_depth(v, level + 1) for v in node])
    return level


def _has_ref(node: Any) -> bool:
    if isinstance(node, dict):
        return "$ref" in node or "$dynamicRef" in node or any(_has_ref(v) for v in node.values())
    if isinstance(node, list):
        return any(_has_ref(v) for v in node)
    return False


def _schema_ok(schema: Any) -> bool:
    if not isinstance(schema, dict) or schema.get("type") != "object":
        return False
    if len(json.dumps(schema)) > SCHEMA_MAX_BYTES or _has_ref(schema):
        return False
    return _depth(schema) <= SCHEMA_MAX_DEPTH * 2   # a schema level is a dict inside "properties"


def _tier(cfg: ServerConfig, tool: dict[str, Any]) -> str:
    hints = tool.get("annotations") if isinstance(tool.get("annotations"), dict) else {}
    if hints.get("destructiveHint") is True:
        tier = "destructive"
    elif hints.get("readOnlyHint") is True:
        tier = "read"
    else:
        tier = "write"
    override = dict(cfg.tiers).get(tool.get("name"))
    if override and _TIER_ORDER.index(override) > _TIER_ORDER.index(tier):
        tier = override
    return tier


def _description(cfg: ServerConfig, text: Any, tier: str) -> str:
    from harness.fence import _neutralise_untrusted_delimiters
    body = " ".join(str(text or "").split())
    body = re.sub(r"Safety:\s*\w*", "", body)
    body = " ".join(_neutralise_untrusted_delimiters(body).split())[:DESCRIPTION_CAP]
    return f'External tool from MCP server "{cfg.id}". {body} Safety: {tier}'.replace("  ", " ")


def _shape(cfg: ServerConfig, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The session's catalogue entries for one server's tools, each with the
    private `_server`, `_tool` and `_tier` keys `session_tools` strips."""
    shaped, seen = [], set()
    for tool in tools:
        raw = tool.get("name")
        if not isinstance(raw, str) or not raw:
            continue
        name = PREFIX + cfg.id + "__" + re.sub(r"[^a-z0-9_]", "_", raw.lower())
        schema = tool.get("inputSchema")
        if len(name) > NAME_MAX or name in seen or not _schema_ok(schema):
            logger.warning("mcp: %s tool %r skipped (name, collision or schema)", cfg.id, raw)
            continue
        seen.add(name)
        tier = _tier(cfg, tool)
        shaped.append({"name": name, "description": _description(cfg, tool.get("description"), tier),
                       "input_schema": schema, "_server": cfg.id, "_tool": raw, "_tier": tier})
    return shaped


# ── per session ────────────────────────────────────────────────────────────

def _snapshot(session: Any) -> None:
    servers = _load()
    entries: list[dict[str, Any]] = []
    logged: list[dict[str, Any]] = []
    configs: dict[str, ServerConfig] = {}
    for cfg in servers:
        configs[cfg.id] = cfg
        try:
            shaped = _shape(cfg, _connection(cfg).list_tools())
        except _Failure as failure:
            logger.warning("mcp: %s", failure)
            logged.append({"id": cfg.id, "error": failure.kind["error_kind"]})
            continue
        except Exception:  # noqa: BLE001 — one broken server never costs the chat
            logger.warning("mcp: %s could not be listed", cfg.id, exc_info=True)
            logged.append({"id": cfg.id, "error": _PROTOCOL["error_kind"]})
            continue
        entries.extend(shaped)
        logged.append({"id": cfg.id, "tools": [
            {"name": e["name"], "tier": e["_tier"],
             "schema_sha256": hashlib.sha256(json.dumps(e["input_schema"], sort_keys=True).encode()).hexdigest()}
            for e in shaped]})
    session.external_tools = entries
    session.external_servers = configs
    if servers:
        from harness import eventlog
        eventlog.record(session, "mcp/catalogue", servers=logged)


def session_tools(session: Any) -> list[dict[str, Any]]:
    """The session's external tools as catalogue entries
    (`{name, description, input_schema}`), listed once per session."""
    if session is None:
        return []
    if getattr(session, "external_tools", None) is None:
        _snapshot(session)
    return [{k: e[k] for k in ("name", "description", "input_schema")}
            for e in session.external_tools]


def _entry(session: Any, name: str) -> dict[str, Any] | None:
    if session is None or not isinstance(name, str) or not name.startswith(PREFIX):
        return None
    for entry in getattr(session, "external_tools", None) or []:
        if entry["name"] == name:
            return entry
    return None


def tier_for(session: Any, name: str) -> str | None:
    """The external tool's tier, or None when `name` is not one of this
    session's external tools (the catalogue lookup then answers, and for an
    unknown name that is `destructive`)."""
    entry = _entry(session, name)
    return entry["_tier"] if entry else None


def server_for(session: Any, name: str) -> str | None:
    entry = _entry(session, name)
    return entry["_server"] if entry else None


def _render(result: dict[str, Any]) -> Any:
    structured = result.get("structuredContent")
    if isinstance(structured, dict) and not result.get("isError"):
        return structured
    parts = []
    for block in result.get("content") or []:
        kind = block.get("type") if isinstance(block, dict) else None
        if kind == "text":
            parts.append(str(block.get("text", "")))
        elif kind in ("image", "audio"):
            parts.append(f"[{kind} omitted: external tools' {kind} content is not shown]")
        elif kind is not None:
            parts.append(f"[{kind} omitted]")
    return "\n".join(parts)


def dispatcher_for(session: Any, name: str) -> Callable[..., Any] | None:
    """The handler for one of the session's external tools, or None."""
    entry = _entry(session, name)
    if entry is None:
        return None
    cfg = (getattr(session, "external_servers", None) or {}).get(entry["_server"])

    def call(**args: Any) -> Any:
        if cfg is None:
            raise _refuse(_UNAVAILABLE, f"{entry['_server']}: the server is not configured")
        try:
            result = _connection(cfg).request("tools/call", {"name": entry["_tool"], "arguments": args},
                                              timeout=_call_timeout())
        except _Failure as failure:
            raise _refuse(failure.kind, str(failure)) from failure
        if result.get("isError"):
            raise _refuse(_TOOL_ERROR, _render(result) or f"{name} failed", status=422)
        return _render(result)
    return call
