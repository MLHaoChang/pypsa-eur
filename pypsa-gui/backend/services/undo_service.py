"""
Lightweight in-memory undo stack for the PyPSA GUI.

Each entry stores both the network (netcdf bytes) and user-uploaded time series
so that a full round-trip restore is possible without hitting disk.

The stack is bounded by TWO limits (whichever fires first):

  * `MAX_STEPS`  — soft cap on the number of undo levels (20 by default).
  * `MAX_BYTES`  — hard cap on cumulative payload size (500 MB by default).

The byte cap matters for multi-period sector-coupled runs: a 200-bus,
26 280-snapshot network's pickled payload is ~30-50 MB. 20 × that = 600-
1000 MB parked in the deque, which OOM'd long sessions on 8 GB hosts. The
byte-based eviction trims oldest entries on push until cumulative size
fits, always retaining at least one snapshot so single-edit undo still
works even when the user is editing a giant network.
"""
from __future__ import annotations

import logging
import pickle
import threading
from collections import deque

_log = logging.getLogger(__name__)

MAX_STEPS = 20
MAX_BYTES = 500 * 1024 * 1024  # 500 MB

# Pair (payload_bytes, len(payload_bytes)) so we can sum sizes in O(N)
# without re-measuring each call. `_total_bytes` mirrors the sum and is
# kept in sync by every mutation under `_lock`.
_stack: deque[tuple[bytes, int]] = deque(maxlen=MAX_STEPS)
_total_bytes: int = 0
_lock = threading.Lock()


def push(netcdf_bytes: bytes, user_ts_data: dict) -> None:
    """
    Serialize network + time-series state and push onto the undo stack.

    Evicts oldest entries when cumulative payload size would exceed
    MAX_BYTES. Always retains at least one entry so a single-step undo
    is always available regardless of network size.
    """
    payload = pickle.dumps({"netcdf": netcdf_bytes, "user_ts": user_ts_data})
    size = len(payload)
    global _total_bytes
    with _lock:
        # deque(maxlen=MAX_STEPS) auto-drops the leftmost when we exceed
        # MAX_STEPS — we have to subtract that from `_total_bytes`
        # ourselves before appending. Capture the would-be evictee.
        evicted = _stack[0] if len(_stack) == MAX_STEPS else None
        _stack.append((payload, size))
        _total_bytes += size
        if evicted is not None:
            _total_bytes -= evicted[1]
        # Byte-budget eviction. Trim from the left until size fits, but
        # always keep at least one entry — a single jumbo payload still
        # gets stored so the user has at least one undo level on a huge
        # network. Smaller older entries are sacrificed first.
        evicted_count = 0
        evicted_bytes = 0
        while _total_bytes > MAX_BYTES and len(_stack) > 1:
            _, evicted_size = _stack.popleft()
            _total_bytes -= evicted_size
            evicted_count += 1
            evicted_bytes += evicted_size
    # Log AFTER releasing the lock so the log call doesn't extend the
    # critical section. The log handler may block briefly on a queue.
    if evicted_count > 0:
        _log.info(
            "undo: trimmed %d entries (%.1f MB) to fit %d MB budget",
            evicted_count,
            evicted_bytes / (1024 * 1024),
            MAX_BYTES // (1024 * 1024),
        )


def pop() -> tuple[bytes, dict] | None:
    """Pop the most recent snapshot.  Returns None when the stack is empty."""
    global _total_bytes
    with _lock:
        if not _stack:
            return None
        payload, size = _stack.pop()
        _total_bytes -= size
    data = pickle.loads(payload)
    return data["netcdf"], data["user_ts"]


def depth() -> int:
    """Number of undo steps currently available."""
    with _lock:
        return len(_stack)


def memory_bytes() -> int:
    """
    Cumulative byte size of all queued snapshots. Useful for the
    `/api/network/undo/info` probe so the UI can display memory usage.
    """
    with _lock:
        return _total_bytes


def clear() -> None:
    """Discard all snapshots (call on project load / new network)."""
    global _total_bytes
    with _lock:
        _stack.clear()
        _total_bytes = 0
