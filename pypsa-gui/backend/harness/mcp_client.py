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
* **Limits.** A protocol version outside `SUPPORTED_VERSIONS`, a message
  over `MAX_MESSAGE_BYTES`, or a call past the per-tool budget fails that
  server or call cleanly; at most `MAX_TOOLS_PER_SERVER` tools per server.
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from fastapi import HTTPException

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2025-06-18"
# The versions this client speaks; a server answering any other is refused.
SUPPORTED_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
# Hard limits, so a misbehaving server can neither flood nor hang the loop:
MAX_MESSAGE_BYTES = 1_000_000      # one JSON-RPC message, either transport
MAX_TOOLS_PER_SERVER = 64          # the rest of a longer list is not offered
PREFIX = "mcp__"
NAME_MAX = 64                 # the tool-name limit both wires share
DESCRIPTION_CAP = 1024
SCHEMA_MAX_BYTES = 8 * 1024
SCHEMA_MAX_DEPTH = 6
KEY_PREFIX = "PYPSA_GUI_MCP_KEY__"
_ID = re.compile(r"[a-z0-9]{1,16}")
_CHILD_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "SYSTEMROOT", "TEMP", "TMP")
# A variable an entry may map: never one the child already receives, nor one
# that changes how a loader or interpreter starts (a stdio server is a process
# this backend runs; the value is a managed key, the name must not be a lever).
_ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]{0,31}")
_ENV_REFUSED = frozenset({"NODE_OPTIONS", "PATH", "HOME", "SHELL", *_CHILD_ENV})
_ENV_REFUSED_PREFIXES = ("LD_", "DYLD_", "PYTHON")
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
    """https, or http to a loopback host; never credentials in the URL nor a
    link-local address (the cloud metadata endpoints live there)."""
    try:
        parts = urlsplit(url)
        parts.port                                  # raises on a bad port
    except ValueError:
        return False
    if not parts.hostname or parts.username is not None or parts.password is not None:
        return False
    try:
        address = ipaddress.ip_address(parts.hostname)
    except ValueError:
        address = None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped               # ::ffff:a.b.c.d is a.b.c.d
    if address is not None and address.is_link_local:
        return False
    if parts.scheme == "https":
        return True
    if parts.scheme != "http":
        return False
    return parts.hostname == "localhost" or (address is not None and address.is_loopback)


def _parse_one(entry: Any) -> tuple[ServerConfig | None, str | None]:
    cfg, problem = validate_entry(entry)
    if cfg is not None and entry.get("enabled", True) is False:
        return None, None
    return cfg, problem


def env_name_allowed(var: Any) -> bool:
    return (isinstance(var, str) and bool(_ENV_NAME.fullmatch(var))
            and var not in _ENV_REFUSED and not var.startswith(_ENV_REFUSED_PREFIXES))


def validate_entry(entry: Any) -> tuple[ServerConfig | None, str | None]:
    """The entry's config, or the reason it is refused; `enabled` aside, so a
    disabled entry is checked as strictly as a live one."""
    if not isinstance(entry, dict):
        return None, "an entry must be an object"
    sid = entry.get("id")
    if not isinstance(sid, str) or not _ID.fullmatch(sid):
        return None, f"server id {sid!r} must be 1-16 characters of [a-z0-9]"
    if not isinstance(entry.get("enabled", True), bool):
        return None, f"{sid}: enabled must be true or false"
    transport = entry.get("transport")
    env = entry.get("env") or {}
    if not isinstance(env, dict) or not all(
            isinstance(k, str) and isinstance(v, str) and v.startswith(KEY_PREFIX)
            for k, v in env.items()):
        return None, f"{sid}: env values must name keys starting {KEY_PREFIX}"
    for var in env:
        if not env_name_allowed(var):
            return None, (f"{sid}: {var!r} is not a variable a server may be given "
                          "(up to 32 characters of [A-Z0-9_]; not PATH, HOME, LD_*, PYTHON*, ...)")
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


class ConfigUnreadable(Exception):
    """`mcp-servers.json` exists but is not a servers list; it is never
    overwritten while it is in that state."""


_FILE_LOCK = threading.Lock()


def is_server_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_ID.fullmatch(value))


def key_slot(server_id: str, var: str | None = None) -> str:
    """The managed key the settings routes give a server's bearer token
    (`var` None) or one of its child-environment variables."""
    slot = KEY_PREFIX + server_id.upper()
    return slot if var is None else f"{slot}__{var}"


def _read_entries() -> list[Any]:
    path = config_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigUnreadable(f"{path.name} is not valid JSON") from exc
    entries = data.get("servers") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise ConfigUnreadable(f"{path.name} must be an object with a servers list")
    return entries


def read_entries() -> list[Any]:
    """Every entry in the file as written, valid or not; [] without a file."""
    with _FILE_LOCK:
        return _read_entries()


def update_entries(change: Callable[[list[Any]], list[Any]]) -> list[Any]:
    """Read the entries, apply `change`, write the result: under one lock, at
    0600 from the first byte, through a unique temp file and `os.replace`
    (the `llm_config.save_profiles` pattern), so a failed write leaves the
    previous file whole. Returns the entries `change` was given."""
    import tempfile
    with _FILE_LOCK:
        before = _read_entries()
        after = change(list(before))
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"servers": after}, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return before


# ── the connections ────────────────────────────────────────────────────────

def _call_timeout() -> float:
    from harness import budget
    return max(0.2, budget.PER_TOOL_TIMEOUT_SECONDS * 0.9)


class _Connection:
    """One initialized JSON-RPC session with a server."""

    def __init__(self, cfg: ServerConfig) -> None:
        self.cfg = cfg
        self._ids = itertools.count(1)
        self.version = PROTOCOL_VERSION

    def initialize(self) -> None:
        result = self.request("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                             "clientInfo": {"name": "pypsa-gui", "version": "1"}},
                              timeout=_call_timeout())
        version = result.get("protocolVersion")
        if version not in SUPPORTED_VERSIONS:
            raise _Failure(_PROTOCOL, f"{self.cfg.id}: unsupported protocol version {version!r}")
        self.version = version
        # The server's `instructions` are deliberately not kept.
        self.notify("notifications/initialized", {})

    def list_tools(self) -> list[dict[str, Any]]:
        tools, cursor = [], None
        for _ in range(20):
            params = {"cursor": cursor} if cursor else {}
            page = self.request("tools/list", params, timeout=_call_timeout())
            tools.extend(t for t in page.get("tools") or [] if isinstance(t, dict))
            cursor = page.get("nextCursor")
            if not cursor or len(tools) >= MAX_TOOLS_PER_SERVER:
                break
        if len(tools) > MAX_TOOLS_PER_SERVER:
            logger.warning("mcp: %s lists %d tools; the first %d are offered",
                           self.cfg.id, len(tools), MAX_TOOLS_PER_SERVER)
        return tools[:MAX_TOOLS_PER_SERVER]

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

    def _lines(self):
        """Lines from the server, each at most MAX_MESSAGE_BYTES; a longer one
        is never buffered whole: it breaks the connection."""
        while True:
            line = self.proc.stdout.readline(MAX_MESSAGE_BYTES + 1)
            if not line:
                return
            if len(line) > MAX_MESSAGE_BYTES and not line.endswith("\n"):
                logger.warning("mcp: %s sent a message over %d bytes; disconnecting",
                               self.cfg.id, MAX_MESSAGE_BYTES)
                self._fail_all(_PROTOCOL)
                self.close()
                return
            yield line

    def _fail_all(self, kind: dict[str, str]) -> None:
        with self._pending_lock:
            for slot in self._pending.values():
                if slot[1] is None:
                    slot[2] = kind
                    slot[0].set()

    def _read(self) -> None:
        for line in self._lines():
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
        self._fail_all(_UNAVAILABLE)                  # the server exited: fail every waiter

    def request(self, method: str, params: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        rid = next(self._ids)
        slot: list[Any] = [threading.Event(), None, None]   # done, reply, failure kind
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
            kind = slot[2] or _UNAVAILABLE
            raise _Failure(kind, f"{self.cfg.id}: the server "
                           + ("sent an oversized message" if kind is _PROTOCOL else "exited"))
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


@dataclass(frozen=True)
class _Reply:
    status_code: int
    headers: dict[str, str]
    content: bytes

    def header(self, name: str) -> str:
        lowered = name.lower()
        return next((v for k, v in self.headers.items() if k.lower() == lowered), "")


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
            headers["MCP-Protocol-Version"] = self.version
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if self.cfg.key and os.environ.get(self.cfg.key):
            headers["Authorization"] = f"Bearer {os.environ[self.cfg.key]}"
        return headers

    def _post(self, message: dict[str, Any], timeout: float):
        import httpx
        try:
            with self.client.stream("POST", self.cfg.url, json=message, headers=self._headers(),
                                    timeout=timeout) as response:
                declared = response.headers.get("Content-Length", "")
                if declared.isdigit() and int(declared) > MAX_MESSAGE_BYTES:
                    raise _Failure(_PROTOCOL, f"{self.cfg.id}: a reply over {MAX_MESSAGE_BYTES} bytes")
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_MESSAGE_BYTES:
                        raise _Failure(_PROTOCOL, f"{self.cfg.id}: a reply over {MAX_MESSAGE_BYTES} bytes")
                reply = _Reply(response.status_code, dict(response.headers), bytes(body))
        except httpx.TimeoutException as exc:
            raise _Failure(_TIMEOUT, f"{self.cfg.id}: no answer within {timeout:.1f}s") from exc
        except httpx.HTTPError as exc:
            raise _Failure(_UNAVAILABLE, f"{self.cfg.id}: {type(exc).__name__}") from exc
        if reply.header("Mcp-Session-Id"):
            self.session_id = reply.header("Mcp-Session-Id")
        return reply

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
        if response.status_code >= 300:
            # A 3xx too: redirects are never followed (`_http_client`), so the
            # URL the admin entered is the only host this server ever reaches.
            raise _Failure(_UNAVAILABLE, f"{self.cfg.id}: HTTP {response.status_code}")
        text = response.content.decode("utf-8", errors="replace")
        if "text/event-stream" in response.header("Content-Type"):
            candidates = _sse_messages(text)
        else:
            try:
                body = json.loads(text)
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


def forget(server_id: str) -> None:
    """Close the pooled connection to `server_id`, if any (the admin edited,
    disabled or removed it)."""
    with _CONNECTIONS_LOCK:
        conn = _CONNECTIONS.pop(server_id, None)
    if conn is not None:
        conn.close()


# A test failure's message is fixed per kind: a server's own error text is
# data, and never travels back to the settings panel.
_PROBE_MESSAGES = {
    "mcp_unavailable": "The server could not be started or reached.",
    "mcp_timeout": "The server did not answer in time.",
    "mcp_protocol_error": "The server's answer was not usable MCP.",
}


def probe(cfg: ServerConfig) -> dict[str, Any]:
    """Connect to `cfg` on a connection of its own (never the pool), list its
    tools and close: what the model would be offered, for the admin to read
    before enabling the server."""
    conn: _Connection | None = None
    try:
        conn = _StdioConnection(cfg) if cfg.transport == "stdio" else _HttpConnection(cfg)
        conn.initialize()
        tools = conn.list_tools()
    except _Failure as failure:
        kind = failure.kind["error_kind"]
        logger.warning("mcp: test of %s failed (%s)", cfg.id, kind)
        return {"ok": False, "error_kind": kind,
                "message": _PROBE_MESSAGES.get(kind, _PROBE_MESSAGES["mcp_protocol_error"])}
    except Exception as exc:  # noqa: BLE001 — a test never 500s the settings route
        logger.warning("mcp: test of %s failed (%s)", cfg.id, type(exc).__name__)
        return {"ok": False, "error_kind": _PROTOCOL["error_kind"],
                "message": _PROBE_MESSAGES["mcp_protocol_error"]}
    finally:
        if conn is not None:
            conn.close()
    shaped = _shape(cfg, tools)
    return {"ok": True, "skipped": len(tools) - len(shaped),
            "tools": [{"name": e["name"], "tier": e["_tier"], "description": e["description"]}
                      for e in shaped]}


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
