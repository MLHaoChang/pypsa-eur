from __future__ import annotations

import itertools
import threading
from collections import deque
from dataclasses import asdict, dataclass, field
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
    # Owning organization, or None for an entry written before any project was
    # bound (a fresh unsaved network). Tenancy (Step 0a): the deque is
    # PROCESS-GLOBAL, so before this field every org read every other org's
    # audit trail — component names, project names, and edit timing. `get_all`
    # now filters on it and `clear` only drops the caller's own rows.
    org_id: str | None = field(default=None)

_entries: deque[LogEntry] = deque(maxlen=MAX_ENTRIES)
# `itertools.count` is thread-safe at the C level (the increment-and-return
# happens under the GIL as a single bytecode pair), so two concurrent log
# callers — solver-worker thread + middleware audit thread — never produce
# duplicate ids. The previous `_counter += 1` was two separate bytecodes
# and could collide under thread interleaving, causing the History tab's
# id-keyed React Query cache to silently drop a real entry.
#
# STEP 3 NOTE: this is a PER-PROCESS counter. Once more than one replica
# writes entries, ids collide across replicas and the History timeline cannot
# be ordered. Step 3 replaces it with a DB sequence; nothing here should start
# depending on the id being globally unique before then.
_id_seq = itertools.count(1)
# Append + counter ARE atomic individually, but `id=next(_id_seq)` followed
# by `_entries.append(...)` is two ops — without a lock, two threads can
# interleave such that the deque order doesn't match the id sequence. Lock
# the pair so the History timeline reads chronologically.
_lock = threading.Lock()


def _active_org_id() -> str | None:
    """
    Org of the context the write belongs to.

    Read from the ACTIVE `ProjectContext` rather than from the request, because
    most writers are not in a request at all (the solver worker, the eviction
    save, the undo middleware). While the process holds one active project this
    is exactly the owning org; Step 0b, which moves the active-project pointer
    into the session, is what makes it correct under concurrent users too.
    """
    try:
        from services.pypsa_service import PyPSAService

        return PyPSAService.get_active_context().org_id
    except Exception:  # noqa: BLE001 — the audit log must never break a mutation
        return None


def log(
    action: str,
    component_type: str,
    name: str,
    description: str,
    org_id: str | None = None,
) -> None:
    resolved_org = org_id if org_id is not None else _active_org_id()
    with _lock:
        _entries.append(LogEntry(
            id=next(_id_seq),
            timestamp=datetime.now(tz=timezone.utc).isoformat(),
            action=action,
            component_type=component_type,
            name=name,
            description=description,
            org_id=resolved_org,
        ))


def get_all(org_id: str | None = None) -> list[dict]:
    with _lock:
        # Snapshot the deque under the lock so a concurrent append doesn't
        # race with the reverse-iteration below. `list(...)` materialises
        # synchronously; the reverse + asdict happen on the local copy.
        snap = list(_entries)
    if org_id is not None:
        # Entries with org_id=None were written while no project was bound
        # (a fresh unsaved network). They belong to whoever is at the keyboard,
        # so they stay visible; they carry no other tenant's data by
        # construction, because an unbound context has no other tenant's rows.
        snap = [e for e in snap if e.org_id == org_id or e.org_id is None]
    return [asdict(e) for e in reversed(snap)]   # newest first


def clear(org_id: str | None = None) -> None:
    """
    Drop audit entries. Scoped to ``org_id`` when given.

    An unscoped clear wipes every tenant's history and is reachable only from
    test setup and the in-process reset helpers. The HTTP route always passes
    the caller's org.
    """
    with _lock:
        if org_id is None:
            _entries.clear()
            return
        kept = [e for e in _entries if e.org_id != org_id and e.org_id is not None]
        _entries.clear()
        _entries.extend(kept)
