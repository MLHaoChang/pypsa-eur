"""
The spill store (chat harness issue 16): an oversized tool result is kept
whole on disk and the model receives a preview plus a locator it can page
with `read_result`, instead of a cut it cannot recover from.

Borrowed from DeepSeek Harness's spill seam (assessment design 3); written
for this harness's constraints, re-scoped 2026-10-12 against master:

* Never during a checked turn. `session.assurance_turn` projects every
  result itself and allows only mapped tools; raw data must not leak past it.
* Never for a tool that pages by itself (an `offset`, `page` or `cursor`
  argument in its catalogue schema). Its truncation marker names the tool
  instead (`paging_hint`), because the domain's own paging is better than a
  generic one. `read_result` is such a tool, so a page never spills again.
* Owner- and session-bound. The directory is
  `<app data>/chat-spill/<sha(owner)>/<sha(session)>/`, mode 0700, and a
  locator is 32 hex characters checked by a regex before any path is built:
  nothing the model sends becomes a path component.
* Bounded. One result over `ITEM_CAP_BYTES` is not spilled (the ordinary cut
  applies); a session's spills over `SESSION_CAP_BYTES` lose the oldest
  first; a session directory untouched for `TTL_SECONDS` is swept on the next
  write anywhere, and `drop_session` removes its own at once.
* Redacted like a persisted transcript (`history._redact_for_persist`) and
  scrubbed of person-identifying members (`results._scrub_identities`)
  before the write.

A failure here never costs the turn: the caller falls back to the ordinary
cut and the turn goes on.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger("pypsa_gui.chat")

SESSION_CAP_BYTES: int = 50 * 1024 * 1024
ITEM_CAP_BYTES: int = 20 * 1024 * 1024
TTL_SECONDS: int = 24 * 3600
# The preview the model sees beside the locator; with the spill block it
# stays under `results._RESULT_CONTENT_CAP`, so the result is not cut twice.
PREVIEW_CHARS: int = 2500

_LOCATOR = re.compile(r"^[0-9a-f]{32}$")
_PAGING_ARGS = ("offset", "page", "cursor")


def _root() -> Path:
    from app_paths import app_data_dir
    return app_data_dir() / "chat-spill"


def _key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def _session_dir(session: Any) -> Path:
    owner = str(getattr(session, "owner_user_id", None) or "_local")
    return _root() / _key(owner) / _key(str(session.session_id))


def valid_locator(locator: Any) -> bool:
    return isinstance(locator, str) and bool(_LOCATOR.match(locator))


def path_for(session: Any, locator: str) -> Path:
    """The file for a locator in THIS session's directory. Raises ValueError
    on anything that is not a locator, before a path is built."""
    if not valid_locator(locator):
        raise ValueError("not a spill locator")
    return _session_dir(session) / f"{locator}.json"


def _paging_arg(tool_name: str) -> str | None:
    from harness.catalogue import TOOLS
    for t in TOOLS:
        if t["name"] == tool_name:
            props = (t.get("input_schema") or {}).get("properties") or {}
            return next((a for a in _PAGING_ARGS if a in props), None)
    return None


def paging_hint(tool_name: str) -> str | None:
    """For a tool that pages by itself, what the truncation marker should say."""
    arg = _paging_arg(tool_name)
    if arg is None:
        return None
    return f"call {tool_name} again with its `{arg}` argument for the rest"


def describe(value: Any) -> str:
    """A one-line shape: `list[1200] of {bus, hour, p}`, `dict with keys: a, b`."""
    if isinstance(value, list):
        first = value[0] if value else None
        if isinstance(first, dict):
            keys = list(first)[:8]
            inner = "{" + ", ".join(map(str, keys)) + (", …" if len(first) > 8 else "") + "}"
        else:
            inner = type(first).__name__ if value else "nothing"
        return f"list[{len(value)}] of {inner}"
    if isinstance(value, dict):
        keys = list(value)[:12]
        more = ", …" if len(value) > 12 else ""
        return "dict with keys: " + ", ".join(map(str, keys)) + more
    if isinstance(value, str):
        return f"str[{len(value)}]"
    return type(value).__name__


def shape_for_model(session: Any, tool_name: str, tool_use_id: str, result: Any) -> Any:
    """`result` unchanged, or `{"spill": {...}, "preview": "..."}` when it is
    too large for the model and may be kept. Never raises."""
    try:
        return _spill(session, tool_name, result)
    except Exception:  # noqa: BLE001 — a spill failure must never cost the turn
        logger.warning("chat: spill failed for %s; the ordinary cut applies", tool_name,
                       exc_info=True)
        return result


def _spill(session: Any, tool_name: str, result: Any) -> Any:
    from harness.results import _RESULT_CONTENT_CAP, _coerce_jsonable, _scrub_identities

    if session is None or getattr(session, "assurance_turn", None) is not None:
        return result
    if not isinstance(result, (dict, list)) or _paging_arg(tool_name) is not None:
        return result
    safe = _scrub_identities(_coerce_jsonable(result))
    body = json.dumps(safe, default=str)
    if len(body) <= _RESULT_CONTENT_CAP:
        return result
    from harness.history import _redact_for_persist
    stored = json.dumps(_redact_for_persist(json.loads(body)), default=str, ensure_ascii=False)
    data = stored.encode("utf-8")
    if len(data) > ITEM_CAP_BYTES:
        return result

    from harness.durable import transaction
    directory = _session_dir(session)
    directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    locator = uuid.uuid4().hex
    with transaction(directory):
        target = directory / f"{locator}.json"
        tmp = directory / f".{locator}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, target)
        _enforce_cap(directory)
    _sweep(keep=directory)
    return {
        "spill": {
            "locator": locator,
            "total_chars": len(stored),
            "shape": describe(safe),
            "read_with": "read_result",
        },
        "preview": stored[:PREVIEW_CHARS] + ("…" if len(stored) > PREVIEW_CHARS else ""),
        "note": ("The full result is kept. Page it with read_result(locator, "
                 "pointer, offset, limit) instead of asking for everything."),
    }


def _enforce_cap(directory: Path) -> None:
    files = sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime)
    total = sum(p.stat().st_size for p in files)
    for p in files[:-1]:                      # never the one just written
        if total <= SESSION_CAP_BYTES:
            break
        total -= p.stat().st_size
        p.unlink(missing_ok=True)


def _sweep(keep: Path) -> None:
    root = _root()
    if not root.is_dir():
        return
    cutoff = time.time() - TTL_SECONDS
    for owner in root.iterdir():
        if not owner.is_dir():
            continue
        for sdir in owner.iterdir():
            if sdir == keep or not sdir.is_dir():
                continue
            try:
                if sdir.stat().st_mtime < cutoff:
                    shutil.rmtree(sdir, ignore_errors=True)
            except OSError:
                continue


def drop(session: Any) -> None:
    """Remove a session's spills (called by `drop_session`). Never raises."""
    try:
        shutil.rmtree(_session_dir(session), ignore_errors=True)
    except Exception:  # noqa: BLE001
        logger.warning("chat: could not remove a dropped session's spills", exc_info=True)
