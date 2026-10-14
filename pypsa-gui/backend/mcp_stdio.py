"""
stdio bridge to a running pypsa-gui's local MCP endpoint (chat harness
issue 11).

    python pypsa-gui/backend/mcp_stdio.py [--endpoint PATH]

An MCP client that launches stdio servers (Claude Code, Codex, …) runs this
script instead of knowing the desktop app's port, which changes on every
launch. Each JSON-RPC line on stdin is forwarded to the URL in
`<app data>/mcp-endpoint.json` with that file's bearer token, and each reply
is written to stdout as one line. The file is read for every message, so a
restarted app is picked up (its old sessions are gone: the client
re-initializes). The `Mcp-Session-Id` from `initialize` is carried on every
later request.

Standard library only, and it imports nothing of the app but `app_paths`.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, TextIO

Post = Callable[[str, bytes, dict], "tuple[int, dict, bytes]"]


def default_endpoint_path() -> Path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import app_paths
    return app_paths.app_data_dir() / "mcp-endpoint.json"


def _urllib_post(url: str, body: bytes, headers: dict) -> tuple[int, dict, bytes]:
    # No proxy: the endpoint is on this machine, and a proxy from the
    # environment must never see the bearer token.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with opener.open(request, timeout=600) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers or {}), err.read() or b""


def _error(rid, code: int, message: str) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}})


class Shim:
    def __init__(self, endpoint_path: Path, *, post: Post = _urllib_post) -> None:
        self.endpoint_path = Path(endpoint_path)
        self.post = post
        self.session_id: str | None = None

    def _endpoint(self) -> dict | None:
        try:
            data = json.loads(self.endpoint_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) and data.get("url") and data.get("token") else None

    def forward(self, line: str) -> str | None:
        """One JSON-RPC message in, its reply out (None for a notification)."""
        try:
            message = json.loads(line)
        except ValueError:
            return _error(None, -32700, "parse error")
        rid = message.get("id") if isinstance(message, dict) else None
        endpoint = self._endpoint()
        if endpoint is None:
            return None if rid is None else _error(
                rid, -32000, f"pypsa-gui is not running (no endpoint file at {self.endpoint_path})")
        headers = {"Authorization": f"Bearer {endpoint['token']}",
                   "Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"}
        if self.session_id and not (isinstance(message, dict) and message.get("method") == "initialize"):
            headers["Mcp-Session-Id"] = self.session_id
        try:
            status, reply_headers, body = self.post(endpoint["url"], json.dumps(message).encode(), headers)
        except OSError as exc:
            return None if rid is None else _error(rid, -32000, f"pypsa-gui is unreachable: {exc}")
        session = next((v for k, v in reply_headers.items() if k.lower() == "mcp-session-id"), None)
        if session:
            self.session_id = session
        if status == 202 or rid is None:
            return None
        text = body.decode("utf-8", errors="replace").strip()
        try:
            reply = json.loads(text)
        except ValueError:
            reply = None
        if isinstance(reply, dict) and reply.get("jsonrpc") == "2.0":
            return json.dumps(reply)
        return _error(rid, -32000, f"pypsa-gui answered HTTP {status}")

    def run(self, stdin: TextIO, stdout: TextIO) -> None:
        for line in stdin:
            if not line.strip():
                continue
            reply = self.forward(line)
            if reply is not None:
                stdout.write(reply + "\n")
                stdout.flush()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--endpoint", type=Path, help="mcp-endpoint.json (default: the app data dir)")
    opts = parser.parse_args(argv)
    Shim(opts.endpoint or default_endpoint_path()).run(sys.stdin, sys.stdout)


if __name__ == "__main__":
    main()
