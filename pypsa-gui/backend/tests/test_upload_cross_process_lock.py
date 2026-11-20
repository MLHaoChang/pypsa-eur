"""`add_upload` holds a cross-process file lock (GK B ruling #1070).

Two API processes uploading the same bytes to one project must store one copy,
and a lock another process holds past the timeout is a stated 409, not a hang.
"""
from __future__ import annotations

import multiprocessing as mp
import pathlib
import sys

import pytest
from fastapi import HTTPException

BACKEND = str(pathlib.Path(__file__).resolve().parents[1])
DATA = b"%PDF-1.4 same bytes in two processes\n" * 50


def _worker(project_dir: str, ready, go, out) -> None:
    sys.path.insert(0, BACKEND)
    from services import upload_service

    ready.set()
    go.wait(10)
    try:
        meta = upload_service.add_upload(
            "p", DATA, "a.txt", "text/plain", project_dir=pathlib.Path(project_dir)
        )
        out.put(("ok", meta.file_id))
    except HTTPException as exc:
        out.put(("http", exc.detail["error_kind"]))


def test_two_processes_store_one_copy(tmp_path):
    ctx = mp.get_context("spawn")
    go, out = ctx.Event(), ctx.Queue()
    readies = [ctx.Event() for _ in range(2)]
    procs = [ctx.Process(target=_worker, args=(str(tmp_path), r, go, out)) for r in readies]
    for p in procs:
        p.start()
    for r in readies:
        assert r.wait(30)
    go.set()
    results = [out.get(timeout=60) for _ in procs]
    for p in procs:
        p.join(30)
    assert [r[0] for r in results] == ["ok", "ok"]
    assert results[0][1] == results[1][1]
    stored = [d for d in (tmp_path / "uploads").iterdir() if d.is_dir()]
    assert len(stored) == 1
    assert (stored[0] / "blob").read_bytes() == DATA


def test_busy_lock_is_a_stated_409(tmp_path, monkeypatch):
    from harness.durable import file_lock
    from services import upload_service

    monkeypatch.setattr(upload_service, "UPLOAD_LOCK_TIMEOUT_S", 0.2)
    with file_lock(tmp_path / upload_service.UPLOAD_LOCK_FILE):
        # flock is per open file description, so a second open in this process
        # contends exactly as another process would.
        with pytest.raises(HTTPException) as err:
            upload_service.add_upload("p", DATA, "a.txt", "text/plain", project_dir=tmp_path)
    assert err.value.status_code == 409
    assert err.value.detail["error_kind"] == "upload_busy"
    assert not (tmp_path / "uploads").exists() or not list((tmp_path / "uploads").iterdir())
