"""
AS-10 — the intent comparison as a background job, one record per project.

A comparison is two to five sizing loops of several solves each, so it never runs inside a request. The record
holds per-intent progress (queued, running, done) and the rows as they finish; `abort` sets the stop event the
loops read. The job runs on the named project's own network (loaded from its saved file), never the active one.
Nothing here imports a router.
"""
from __future__ import annotations

import contextvars
import threading
import time

_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()


class JobBusy(RuntimeError):
    """A comparison for this project is already running."""


def _key(project_uuid: str) -> str:
    """Jobs are keyed by the project's uuid: a deleted and recreated project of the same name is a new key."""
    return project_uuid


def start(project: str, specs, work) -> dict:
    """Start `work(stop_event, on_progress)` (returns `{"rows": [...]}`) in a thread; JobBusy if one is running."""
    stop = threading.Event()
    record = {"status": "running", "progress": {s.intent: "queued" for s in specs}, "rows": [], "error": None,
              "started_at": time.time(), "finished_at": None, "stop_event": stop}

    def on_progress(intent, state, row):
        with _LOCK:
            record["progress"][intent] = state
            if row is not None:
                record["rows"].append(row)

    def worker():
        try:
            out = work(stop, on_progress)
            status = "aborted" if stop.is_set() else "done"
            with _LOCK:
                record.update(status=status, rows=out["rows"], finished_at=time.time())
        except Exception as exc:                       # noqa: BLE001
            with _LOCK:
                record.update(status="failed", error=str(exc), finished_at=time.time())

    ctx = contextvars.copy_context()
    thread = threading.Thread(target=lambda: ctx.run(worker), daemon=True, name="intent-comparison")
    with _LOCK:
        cur = _JOBS.get(_key(project))
        if cur is not None and cur["status"] == "running":
            raise JobBusy("a comparison is already running for this project")
        _JOBS[_key(project)] = record
        thread.start()
    return view(record)


def view(record: dict) -> dict:
    with _LOCK:
        return {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v)
                for k, v in record.items() if k not in ("stop_event",)}


def get(project: str) -> dict | None:
    rec = _JOBS.get(_key(project))
    return None if rec is None else view(rec)


def abort(project: str) -> dict | None:
    rec = _JOBS.get(_key(project))
    if rec is None:
        return None
    rec["stop_event"].set()
    return {"status": rec["status"], "aborting": rec["status"] == "running"}


def clear() -> None:
    """Tests."""
    _JOBS.clear()
