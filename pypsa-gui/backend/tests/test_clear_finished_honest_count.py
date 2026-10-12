"""
`clear_finished` must not report rows it did not delete.

`solve_job_store.delete_jobs` swallows every database error, and
`SolveQueue.clear_finished` returned `len(to_delete)` — every id it had
INTENDED to delete — regardless. On a database error the super-admin was told
"removed N" while the rows survived and the very next listing pulled them
straight back. `clear_finished`'s own docstring claimed the number was what
"actually" left the table.
"""
from __future__ import annotations

import pytest

from services import solve_job_store
from services.solve_queue import solve_queue


def test_delete_jobs_reports_failure_as_none(monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("database is gone")

    monkeypatch.setattr(solve_job_store, "SessionLocal", _boom, raising=False)
    # The import inside the function is what actually resolves, so patch the
    # module the function imports from as well.
    import db.session as _s
    monkeypatch.setattr(_s, "SessionLocal", _boom, raising=False)

    import uuid
    assert solve_job_store.delete_jobs([uuid.uuid4()]) is None


def test_delete_jobs_reports_zero_for_an_empty_request():
    assert solve_job_store.delete_jobs([]) == 0


def test_clear_finished_reports_only_memory_when_the_delete_fails(monkeypatch):
    monkeypatch.setattr(
        solve_job_store, "load_by_status",
        lambda _statuses: [{"id": __import__("uuid").uuid4()} for _ in range(3)],
    )
    monkeypatch.setattr(solve_job_store, "delete_jobs", lambda _ids: None)
    # Nothing resident, so memory contributes 0 — and the three table rows
    # that did NOT go must not be counted.
    assert solve_queue.clear_finished() == 0


def test_clear_finished_counts_the_union_when_the_delete_lands(monkeypatch):
    import uuid

    rows = [{"id": uuid.uuid4()} for _ in range(3)]
    monkeypatch.setattr(solve_job_store, "load_by_status", lambda _s: rows)
    monkeypatch.setattr(solve_job_store, "delete_jobs", lambda ids: len(list(ids)))
    assert solve_queue.clear_finished() == 3
