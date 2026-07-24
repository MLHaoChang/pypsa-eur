"""
Lightweight in-memory undo stack for the PyPSA GUI — PER PROJECT (B3).

Each entry stores both the network (netcdf bytes) and user-uploaded time series
so that a full round-trip restore is possible without hitting disk.

Multi-project (B3): the stack, its byte accounting, and the push-coalesce
timestamp live on a ``_UndoState`` object carried by each ``ProjectContext``
(services/project_context.py). The module functions operate on the ACTIVE
project's ``_UndoState`` (resolved via ``PyPSAService.get_active_context().undo``),
so every existing call site (middleware push, ``/undo`` endpoints, load-time
clear) is unchanged. Single-resident behaviour is byte-identical to the previous
module global: ``reset_network``/``set_network`` carry the ``_UndoState`` forward,
so undo survives a network swap exactly as the old global deque did, and
``clear()`` (called on project load) empties it. B8 stops carrying it forward so
each resident project keeps its own history; the per-project ``MAX_BYTES`` cap
then makes the eviction RAM ceiling (cap x MAX_BYTES) well-defined.

The stack is bounded by TWO limits (whichever fires first), both overridable via
env for ops tuning:

  * ``MAX_STEPS``  — soft cap on the number of undo levels (20 by default;
    ``PYPSA_GUI_UNDO_MAX_STEPS``).
  * ``MAX_BYTES``  — hard cap on cumulative payload size per project (500 MB by
    default; ``PYPSA_GUI_UNDO_MAX_BYTES``).

The byte cap matters for multi-period sector-coupled runs: a 200-bus,
26 280-snapshot network's pickled payload is ~30-50 MB. 20 x that = 600-
1000 MB parked in the deque, which OOM'd long sessions on 8 GB hosts. The
byte-based eviction trims oldest entries on push until cumulative size
fits, always retaining at least one snapshot so single-edit undo still
works even when the user is editing a giant network.
"""
from __future__ import annotations

import logging
import os
import pickle
import threading
import time
from collections import deque
from dataclasses import dataclass, field

_log = logging.getLogger(__name__)

MAX_STEPS = int(os.environ.get("PYPSA_GUI_UNDO_MAX_STEPS", "20"))
MAX_BYTES = int(os.environ.get("PYPSA_GUI_UNDO_MAX_BYTES", str(500 * 1024 * 1024)))  # 500 MB

# ── Push coalescing ──────────────────────────────────────────────────────────
# Rapid mutation bursts (a canvas drag fires many sequential PUT /buses/{name}
# calls) should collapse to ONE undo step, not one per tick — otherwise every
# drag pays a netcdf-export round-trip and a single Ctrl+Z barely moves. The
# request path calls `claim_push_slot()` BEFORE the (expensive) export and only
# snapshots when it returns True.
COALESCE_WINDOW_S = 0.5


@dataclass
class _UndoState:
    """
    Per-project undo stack + byte accounting + push-coalesce timestamp.

    One instance lives on each ProjectContext (``ctx.undo``); the module
    functions below operate on the ACTIVE project's instance. ``stack`` pairs
    ``(payload_bytes, len(payload_bytes))`` so cumulative size is summed in O(1)
    via ``total_bytes`` (kept in sync under ``lock``). ``coalesce_lock`` is
    separate from ``lock`` so the hot drag path (``claim_push_slot``) doesn't
    contend with the pickle-heavy push/pop critical section.
    """

    stack: deque = field(default_factory=lambda: deque(maxlen=MAX_STEPS))
    total_bytes: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)
    coalesce_lock: threading.Lock = field(default_factory=threading.Lock)
    last_push: float = 0.0


def _active() -> _UndoState:
    """
    Return the ACTIVE project's undo state.

    Lazy ``PyPSAService`` import avoids a
    project_context -> undo_service -> pypsa_service -> project_context cycle.
    """
    from services.pypsa_service import PyPSAService  # noqa: PLC0415

    return PyPSAService.get_active_context().undo


def claim_push_slot(window_s: float = COALESCE_WINDOW_S) -> bool:
    """
    Return True if an undo snapshot should be pushed now.

    True iff the previous push (for the ACTIVE project) was more than
    ``window_s`` ago, atomically updating the timestamp. False coalesces (skips)
    this push.
    """
    u = _active()
    now = time.monotonic()
    with u.coalesce_lock:
        if (now - u.last_push) >= window_s:
            u.last_push = now
            return True
        return False


def push(netcdf_bytes: bytes, user_ts_data: dict) -> None:
    """
    Serialize network + time-series state and push onto the active stack.

    Evicts oldest entries when cumulative payload size would exceed MAX_BYTES.
    Always retains at least one entry so a single-step undo is always available
    regardless of network size.
    """
    payload = pickle.dumps({"netcdf": netcdf_bytes, "user_ts": user_ts_data})
    size = len(payload)
    u = _active()
    with u.lock:
        # deque(maxlen=MAX_STEPS) auto-drops the leftmost when we exceed
        # MAX_STEPS — subtract that from `total_bytes` before appending.
        evicted = u.stack[0] if len(u.stack) == MAX_STEPS else None
        u.stack.append((payload, size))
        u.total_bytes += size
        if evicted is not None:
            u.total_bytes -= evicted[1]
        # Byte-budget eviction. Trim from the left until size fits, but always
        # keep at least one entry — a single jumbo payload still gets stored so
        # the user has at least one undo level on a huge network.
        evicted_count = 0
        evicted_bytes = 0
        while u.total_bytes > MAX_BYTES and len(u.stack) > 1:
            _, evicted_size = u.stack.popleft()
            u.total_bytes -= evicted_size
            evicted_count += 1
            evicted_bytes += evicted_size
    # Log AFTER releasing the lock so the log call doesn't extend the critical
    # section (the log handler may block briefly on a queue).
    if evicted_count > 0:
        _log.info(
            "undo: trimmed %d entries (%.1f MB) to fit %d MB budget",
            evicted_count,
            evicted_bytes / (1024 * 1024),
            MAX_BYTES // (1024 * 1024),
        )


def pop() -> tuple[bytes, dict] | None:
    """Pop the active project's most recent snapshot. None when the stack is empty."""
    u = _active()
    with u.lock:
        if not u.stack:
            return None
        payload, size = u.stack.pop()
        u.total_bytes -= size
    data = pickle.loads(payload)
    return data["netcdf"], data["user_ts"]


def depth() -> int:
    """Number of undo steps currently available for the active project."""
    u = _active()
    with u.lock:
        return len(u.stack)


def memory_bytes() -> int:
    """
    Cumulative byte size of the active project's queued snapshots.

    Backs the ``/api/network/undo/info`` probe so the UI can display usage.
    """
    u = _active()
    with u.lock:
        return u.total_bytes


def clear() -> None:
    """Discard the active project's snapshots (call on project load / new network)."""
    u = _active()
    with u.lock:
        u.stack.clear()
        u.total_bytes = 0
    # Reset the coalesce timer too, so the first edit of a freshly-loaded
    # project always snapshots rather than being suppressed by a push that
    # happened just before the load.
    with u.coalesce_lock:
        u.last_push = 0.0
