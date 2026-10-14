"""
The approval log (chat harness issue 18): every confirmation decision as a
durable, append-only record, and the gate fails closed when it cannot write.

Borrowed from DeepSeek Harness's approval subsystem (assessment design 2):

* ``approval/asked {call_id, tool, tier, token6}`` when a card is issued.
* ``approval/decided {call_id, tool, tier, outcome, by}`` when the card is
  answered (``by: user``), times out (``ttl``), is aborted (``session``) or
  revoked (``system``), or when policy approves a tier without a card
  (``by: policy``, ``AUTO_APPROVE_TIERS``, no ``asked``).

Outcomes are the closed set ``OUTCOMES``; anything but ``approved`` denies.
Arguments are never written: the tool call already holds them, and a log
that repeats them would be a second copy of whatever the user typed.

Storage follows the spill store: ``<app data>/chat-approvals/<sha(owner)>/
<sha(session)>.jsonl``, a 0700 directory and a 0600 file, each line appended
and fsynced under ``harness/durable.transaction``. Files untouched for
``RETENTION_SECONDS`` are swept on the next write for that owner. When issue
13's event log lands these records fold into it.

``asked`` and ``decided`` return False when the record could not be written;
the gate turns that into ``approval_unavailable`` on any path that would
otherwise run the tool.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("pypsa_gui.chat")

OUTCOMES = frozenset({"approved", "rejected", "expired", "aborted", "revoked", "unavailable"})
DECIDERS = frozenset({"user", "ttl", "session", "system", "policy"})
RETENTION_SECONDS: int = 90 * 24 * 3600


def _root() -> Path:
    from app_paths import app_data_dir
    return app_data_dir() / "chat-approvals"


def _key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def _owner_dir(session: Any) -> Path:
    owner = str(getattr(session, "owner_user_id", None) or "_local")
    return _root() / _key(owner)


def path_for(session: Any) -> Path:
    sid = str(getattr(session, "session_id", None) or "_none")
    return _owner_dir(session) / f"{_key(sid)}.jsonl"


def _append(session: Any, record: dict[str, Any]) -> None:
    from harness.durable import transaction

    path = path_for(session)
    path.parent.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.mkdir(mode=0o700, exist_ok=True)
    line = (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    with transaction(path.parent):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line)
            os.fsync(fd)
        finally:
            os.close(fd)
        _sweep(path.parent, keep=path)


def _sweep(directory: Path, keep: Path) -> None:
    cutoff = time.time() - RETENTION_SECONDS
    for candidate in directory.glob("*.jsonl"):
        if candidate == keep:
            continue
        try:
            if candidate.stat().st_mtime < cutoff:
                candidate.unlink(missing_ok=True)
        except OSError:
            continue


def _base(session: Any, kind: str, call_id: str, tool: str, tier: str) -> dict[str, Any]:
    sid = str(getattr(session, "session_id", None) or "")
    return {"v": 1, "type": kind, "at": datetime.now(timezone.utc).isoformat(),
            "session6": sid[:6], "call_id": str(call_id), "tool": str(tool), "tier": str(tier)}


def _write(session: Any, record: dict[str, Any]) -> bool:
    try:
        _append(session, record)
        return True
    except Exception:  # noqa: BLE001 — the caller decides; this only reports
        logger.warning("chat: approval record %s for %s could not be written",
                       record.get("type"), record.get("tool"), exc_info=True)
        return False


def asked(session: Any, *, call_id: str, tool: str, tier: str, token: str) -> bool:
    record = _base(session, "approval/asked", call_id, tool, tier)
    record["token6"] = str(token)[:6]
    return _write(session, record)


def links(session: Any, args: Any) -> dict[str, str]:
    """What a decision can be joined on later, never the arguments: the
    checked turn's effect `operation_id` (`harness/effects.py` reserves it
    after confirmation) and a preview confirmation's `preview_id` (the diff
    the user saw stays in the preview record)."""
    out: dict[str, str] = {}
    contract = getattr(getattr(session, "assurance_turn", None), "operation_contract", None)
    if isinstance(contract, dict) and isinstance(contract.get("operation_id"), str):
        out["operation_id"] = contract["operation_id"]
    if isinstance(args, dict) and isinstance(args.get("preview_id"), str):
        out["preview_id"] = args["preview_id"]
    return out


def decided(session: Any, *, call_id: str, tool: str, tier: str, outcome: str, by: str,
            link: dict[str, str] | None = None) -> bool:
    if outcome not in OUTCOMES or by not in DECIDERS:
        raise ValueError(f"approval outcome {outcome!r} by {by!r} is not in the closed set")
    record = _base(session, "approval/decided", call_id, tool, tier)
    record.update(outcome=outcome, by=by)
    record.update({k: v for k, v in (link or {}).items() if k in ("operation_id", "preview_id")})
    return _write(session, record)


def read(session: Any) -> list[dict[str, Any]]:
    """This session's records, oldest first (tests; issue 13's replay)."""
    path = path_for(session)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
