from __future__ import annotations

import itertools
import threading
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

MAX_ENTRIES = 500

@dataclass
class LogEntry:
    id: int
    timestamp: str
    action: str           # 'add' | 'update' | 'delete' | 'undo' | 'import' | 'export' | 'save' | 'load' | 'timeseries'
    component_type: str   # 'Bus', 'Generator', 'Network', 'Project', …
    name: str
    description: str

_entries: deque[LogEntry] = deque(maxlen=MAX_ENTRIES)
# `itertools.count` is thread-safe at the C level (the increment-and-return
# happens under the GIL as a single bytecode pair), so two concurrent log
# callers — solver-worker thread + middleware audit thread — never produce
# duplicate ids. The previous `_counter += 1` was two separate bytecodes
# and could collide under thread interleaving, causing the History tab's
# id-keyed React Query cache to silently drop a real entry.
_id_seq = itertools.count(1)
# Append + counter ARE atomic individually, but `id=next(_id_seq)` followed
# by `_entries.append(...)` is two ops — without a lock, two threads can
# interleave such that the deque order doesn't match the id sequence. Lock
# the pair so the History timeline reads chronologically.
_lock = threading.Lock()


def log(action: str, component_type: str, name: str, description: str) -> None:
    with _lock:
        _entries.append(LogEntry(
            id=next(_id_seq),
            timestamp=datetime.now(tz=timezone.utc).isoformat(),
            action=action,
            component_type=component_type,
            name=name,
            description=description,
        ))


def get_all() -> list[dict]:
    with _lock:
        # Snapshot the deque under the lock so a concurrent append doesn't
        # race with the reverse-iteration below. `list(...)` materialises
        # synchronously; the reverse + asdict happen on the local copy.
        snap = list(_entries)
    return [asdict(e) for e in reversed(snap)]   # newest first


def clear() -> None:
    with _lock:
        _entries.clear()
