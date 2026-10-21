"""
The user's own grid-readiness limits (DC 03 slice 6): a bundled sidecar,
``grid_readiness_limits.json``, ``{item: {value, source, note, set_by, set_at,
default}}``.

The scorecard (``grid_readiness._Inputs.marking``) reads a limit here first,
then the template's ledger row, then the generic placeholder. The template's
``eh_template.json`` is never written: a set and a clear leave it
byte-identical, so the template's own value is always there to reset to.

Items:

* ``connection_ramp`` (MW/min, > 0) and ``oscillation`` (% of the connection,
  peak-to-peak, in (0, 100]) take ``{value, source, note}``;
* ``fault_level`` takes ``{source, note}`` only: its value lives on the PoC
  bus (``eh_sk_mva``), so a value is refused (``value_lives_on_the_bus``).

``source`` is one of ``SOURCES``; only ``assumed`` leaves the limit to check.
The band of concern (``grid_readiness.OSCILLATION_BAND_HZ``) stays a
constant. Writes are a read-modify-write under one lock per project
directory.
"""
from __future__ import annotations

import hashlib
import json
import math
import pathlib
import threading
from datetime import datetime, timezone

from services.atomic_io import atomic_write_text

SIDECAR_NAME = "grid_readiness_limits.json"
ITEMS = ("connection_ramp", "oscillation", "fault_level")
SOURCES = ("code", "datasheet", "measured", "assumed")
MAX_NOTE = 500
#: The items whose value lives elsewhere (the PoC bus): source and note only.
SOURCE_ONLY = frozenset({"fault_level"})

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


class LimitError(ValueError):
    """A refused limit: ``kind`` is the 422's ``error_kind``."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


def _lock(project_dir: pathlib.Path) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(str(pathlib.Path(project_dir).resolve()), threading.Lock())


def _valid_entry(item, entry) -> bool:
    if item not in ITEMS or not isinstance(entry, dict) or entry.get("source") not in SOURCES:
        return False
    if item in SOURCE_ONLY:
        return True
    v = entry.get("value")
    return (isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
            and _in_range(item, float(v)))


def _in_range(item: str, v: float) -> bool:
    return v > 0 and (item != "oscillation" or v <= 100.0)


def load_limits(project_dir) -> dict:
    """The user's limits, ``{item: entry}``; an absent or unreadable file, or
    an entry that does not validate, reads as not set."""
    try:
        raw = json.loads((pathlib.Path(project_dir) / SIDECAR_NAME).read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if _valid_entry(k, v)}


def sidecar_hash(project_dir) -> str:
    """A short hash of the sidecar's bytes ("" when absent), for cache keys."""
    try:
        data = (pathlib.Path(project_dir) / SIDECAR_NAME).read_bytes()
    except OSError:
        return ""
    return hashlib.sha256(data).hexdigest()[:16]


def validate(item: str, value, source, note) -> tuple[float | None, str, str]:
    """``(value, source, note)`` as stored, or a ``LimitError``."""
    if item not in ITEMS:
        raise LimitError("unknown_limit_item", f"unknown limit {item!r}; known {list(ITEMS)}")
    if source not in SOURCES:
        raise LimitError("invalid_limit_source", f"source must be one of {list(SOURCES)}")
    note = "" if note is None else str(note)
    if len(note) > MAX_NOTE:
        raise LimitError("note_too_long", f"the note is at most {MAX_NOTE} characters")
    if item in SOURCE_ONLY:
        if value is not None:
            raise LimitError("value_lives_on_the_bus",
                             "the fault level is set on the point-of-connection bus; "
                             "here only its source and note")
        return None, source, note
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
            or not _in_range(item, float(value)):
        rng = "in (0, 100] %" if item == "oscillation" else "a finite number above 0 MW/min"
        raise LimitError("invalid_limit_value", f"the {item.replace('_', ' ')} limit must be {rng}")
    return float(value), source, note


def _template_default(project_dir: pathlib.Path, item: str) -> dict | None:
    """The template's own ledger row for ``item`` as ``{value, source}``, or None."""
    try:
        meta = json.loads((project_dir / "eh_template.json").read_text("utf-8"))
    except (OSError, ValueError):
        return None
    for row in (meta.get("ledger") or []) if isinstance(meta, dict) else []:
        if isinstance(row, dict) and row.get("item") == item:
            return {"value": row.get("value"), "source": row.get("source")}
    return None


def _write(project_dir: pathlib.Path, state: dict) -> None:
    path = project_dir / SIDECAR_NAME
    if state:
        atomic_write_text(path, json.dumps(state, indent=2, sort_keys=True))
    else:
        path.unlink(missing_ok=True)


def set_limit(project_dir, item: str, *, value=None, source, note="", set_by: str | None = None) -> dict:
    """Validate, then set ``item``; returns the stored entry."""
    project_dir = pathlib.Path(project_dir)
    value, source, note = validate(item, value, source, note)
    entry = {"value": value, "source": source, "note": note, "set_by": set_by,
             "set_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "default": _template_default(project_dir, item)}
    with _lock(project_dir):
        state = load_limits(project_dir)
        state[item] = entry
        _write(project_dir, state)
    return entry


def clear_limit(project_dir, item: str) -> bool:
    """Reset ``item`` to the template's value or the placeholder; whether one was set."""
    if item not in ITEMS:
        raise LimitError("unknown_limit_item", f"unknown limit {item!r}; known {list(ITEMS)}")
    project_dir = pathlib.Path(project_dir)
    with _lock(project_dir):
        state = load_limits(project_dir)
        was = state.pop(item, None) is not None
        _write(project_dir, state)
    return was


def with_user_limits(template: dict | None, project_dir) -> dict | None:
    """The template as the flow reads it: each ledger row the user has set
    takes the user's source (and value), so a confirmed limit leaves the
    to-check items and an assumed one shows the user's value. Carries the
    sidecar's hash, so the flow's readiness key moves with it. The template
    file itself is not touched."""
    if template is None:
        return None
    limits = load_limits(project_dir)
    if not limits:
        return template
    ledger = []
    for row in template.get("ledger") or []:
        user = limits.get(row.get("item")) if isinstance(row, dict) else None
        if user is not None:
            row = {**row, "source": user["source"],
                   **({"value": user["value"]} if user.get("value") is not None else {})}
        ledger.append(row)
    return {**template, "ledger": ledger, "user_limits_hash": sidecar_hash(project_dir)}
