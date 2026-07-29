"""
Phase 2 — v4-MINOR-2 chat.jsonl rotation race.

Exit criterion (j): two threads each writing 60 turns of ~9 KB hit the
ROTATE_BYTES threshold near-simultaneously → exactly ONE rotation backup
file is created; no torn lines in chat.jsonl OR chat.jsonl.1; the union
of both files contains exactly 120 well-formed turn records.

Because `append_turn` acquires `ctx.chat_state.lock` for BOTH the rotation
check AND the append, the rotation cannot race a concurrent write — one
thread sees `path.exists() and stat >= ROTATE_BYTES` and rotates; the next
thread arrives AFTER the rename and either appends to a fresh file (no
second rotation needed) OR observes the freshly-rotated state and does NOT
re-rotate (rotation is sized to free space after the rename).
"""
from __future__ import annotations

import json
import pathlib
import threading

import pypsa
import pytest

from services import chat_service
from services.project_context import ProjectContext


@pytest.fixture(autouse=True)
def _reset_chat_sessions():
    chat_service._reset_sessions_for_tests()
    yield
    chat_service._reset_sessions_for_tests()


def _make_bound_ctx(name: str, projects_dir: pathlib.Path) -> ProjectContext:
    n = pypsa.Network()
    n.add("Bus", "B1")
    ctx = ProjectContext(network=n, loaded_project=name)
    # Pre-resolve persist_path so the test inspects the right file.
    chat_service.get_persist_path(ctx)
    # Ensure the per-project directory exists.
    (projects_dir / name).mkdir(parents=True, exist_ok=True)
    return ctx


def test_two_threads_rotation_race_exactly_one_backup(
    tmp_projects_dir, monkeypatch,
):
    """
    Two threads each write 60 turns of ~9 KB. The threshold is monkeypatched
    to a small value so both threads cross it near-simultaneously. Asserts:

      * exactly ONE chat.jsonl.1 rotation file appears
      * no torn lines (every line in EITHER file is well-formed JSON)
      * the union of both files contains 120 distinct turn records
    """
    # Patch PROJECTS_DIR so the test writes inside the temp dir.
    from routers import projects as projects_router
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)
    # 120 turns × ~9 KB per line ≈ 1.08 MB total. Set the threshold so the
    # combined writes cross it EXACTLY ONCE, mid-stream:
    #   * Each line ≈ 9050 bytes (9000-char content + JSON overhead + \n).
    #   * Threshold = 600 KB ≈ line 67 triggers rotation.
    #   * After rotation, ~53 remaining lines write 480 KB — below 600 KB
    #     → no second rotation, no data lost.
    # Since rotation is under the same lock as append, both threads see
    # the rotated state consistently. Backup = first ~67 lines, current =
    # last ~53 lines, total = 120. (Boundary may vary ±1 depending on race.)
    monkeypatch.setattr(chat_service, "ROTATE_BYTES", 600 * 1024)

    ctx = _make_bound_ctx("race", tmp_projects_dir)
    chat_path = pathlib.Path(ctx.chat_state.persist_path)
    backup_path = chat_path.with_suffix(chat_path.suffix + ".1")

    # ~9 KB per turn (the 'content' string padding hits the byte target).
    PAD = "x" * 9000
    TURNS_PER_THREAD = 60

    def writer(thread_id: int) -> None:
        for i in range(TURNS_PER_THREAD):
            turn = {
                "thread_id": thread_id,
                "turn_idx": i,
                "content": PAD,
            }
            chat_service.append_turn(ctx, turn)

    t_a = threading.Thread(target=writer, args=(0,))
    t_b = threading.Thread(target=writer, args=(1,))
    t_a.start()
    t_b.start()
    t_a.join(timeout=30.0)
    t_b.join(timeout=30.0)
    assert not t_a.is_alive() and not t_b.is_alive()

    # Exactly one backup. The rotation overwrites any prior backup with
    # `backup.unlink(); path.rename(backup)`, so even if both threads cross
    # the threshold and rotate, the final backup file count is 1.
    rotated_files = sorted(chat_path.parent.glob("chat.jsonl.*"))
    assert len(rotated_files) == 1, (
        f"expected exactly one rotation backup file; got {rotated_files}"
    )

    # No torn lines in either file. Every non-empty line parses as JSON.
    parsed: list[dict] = []
    for src in (backup_path, chat_path):
        if not src.exists():
            continue
        for line in src.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                parsed.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise AssertionError(
                    f"torn line in {src.name}: {line[:80]!r} — {e}"
                )

    # All 120 turns must be present somewhere in the union of backup + current.
    # (Earlier turns rotated into chat.jsonl.1; later turns are in chat.jsonl.)
    assert len(parsed) == 120, (
        f"expected 120 turns across rotated+current; got {len(parsed)}: "
        f"backup_lines="
        f"{len(backup_path.read_text(encoding='utf-8').splitlines()) if backup_path.exists() else 0}, "
        f"current_lines={len(chat_path.read_text(encoding='utf-8').splitlines())}"
    )
    # Sanity: each thread wrote TURNS_PER_THREAD distinct (turn_idx) values.
    per_thread: dict[int, set[int]] = {0: set(), 1: set()}
    for rec in parsed:
        per_thread[rec["thread_id"]].add(rec["turn_idx"])
    for tid in (0, 1):
        assert per_thread[tid] == set(range(TURNS_PER_THREAD)), (
            f"thread {tid} missing turns: {sorted(set(range(TURNS_PER_THREAD)) - per_thread[tid])}"
        )


def test_rotation_under_same_lock_as_append(tmp_projects_dir, monkeypatch):
    """
    v4-MINOR-2 invariant: rotation happens INSIDE the `with ctx.chat_state.lock`
    critical section of `append_turn`. Verified at the source level by
    grepping the function body for the literal `_rotate_chat_jsonl_unlocked`
    call BETWEEN the `with` opener and the file `.open("a", …)` call.
    """
    import inspect
    src = inspect.getsource(chat_service.append_turn)
    # Crude but resilient: split on the two anchor lines and assert the
    # rotation call sits between them.
    open_idx = src.find("with ctx.chat_state.lock:")
    rotate_idx = src.find("_rotate_chat_jsonl_unlocked(path)")
    write_idx = src.find('path.open("a"')
    assert 0 < open_idx < rotate_idx < write_idx, (
        f"v4-MINOR-2 violation: rotation call not between lock-open and "
        f"file-open. open={open_idx} rotate={rotate_idx} write={write_idx}"
    )


def test_read_all_turns_holds_chat_state_lock():
    """A5 — history reads must share the rotation lock with append_turn."""
    import inspect
    src = inspect.getsource(chat_service.read_all_turns)
    assert "with ctx.chat_state.lock:" in src
    lock_idx = src.find("with ctx.chat_state.lock:")
    resolve_idx = src.find("get_persist_path(ctx)")
    read_idx = src.find("read_text")
    assert 0 < lock_idx < resolve_idx < read_idx, (
        f"A5 violation: path resolve/read must sit inside the lock. "
        f"lock={lock_idx} resolve={resolve_idx} read={read_idx}"
    )


def test_concurrent_read_during_rotation_never_empty_spuriously(
    tmp_projects_dir, monkeypatch,
):
    """
    A5 — while writers rotate, readers via read_all_turns must not observe an
    empty union once at least one turn has been persisted.
    """
    from routers import projects as projects_router
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)
    monkeypatch.setattr(chat_service, "ROTATE_BYTES", 8 * 1024)

    ctx = _make_bound_ctx("readrace", tmp_projects_dir)
    PAD = "y" * 4000
    stop = threading.Event()
    errors: list[str] = []
    saw_nonempty = threading.Event()

    def writer() -> None:
        for i in range(40):
            chat_service.append_turn(ctx, {"i": i, "content": PAD})
            saw_nonempty.set()
        stop.set()

    def reader() -> None:
        while not stop.is_set():
            turns = chat_service.read_all_turns(ctx)
            if saw_nonempty.is_set() and len(turns) == 0:
                # Allow a brief window before the first append lands under the
                # lock; once we've seen a write signal, empty is a bug.
                errors.append("read_all_turns returned [] after writes started")
                break
            for rec in turns:
                if not isinstance(rec, dict):
                    errors.append(f"non-dict turn: {rec!r}")
                    break

    t_w = threading.Thread(target=writer)
    t_r = threading.Thread(target=reader)
    t_r.start()
    t_w.start()
    t_w.join(timeout=30.0)
    stop.set()
    t_r.join(timeout=30.0)
    assert not errors, errors
    final = chat_service.read_all_turns(ctx)
    # Aggressive ROTATE_BYTES means only current + one backup survive — we
    # only require a non-empty well-formed union after the writer finishes.
    assert len(final) >= 1
    assert all(isinstance(rec, dict) and "i" in rec for rec in final)


def test_append_turn_fsyncs_the_chat_jsonl_descriptor(
    tmp_projects_dir, monkeypatch,
):
    """
    QA #9 — `append_turn` must fsync, not merely close.

    Closing the file hands the bytes to the OS page cache, which already
    survives the desktop shell's `os._exit()` rung. `fsync` is what carries
    them past a power cut or a kernel panic.

    Asserted by INODE rather than by call count alone, so the test fails if
    the fsync is dropped AND if it is retargeted at some other descriptor.
    Correlating on the inode keeps this honest without depending on fd
    numbering, which is not stable across platforms.
    """
    import os as _os

    from routers import projects as projects_router
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)

    ctx = _make_bound_ctx("fsync_proj", tmp_projects_dir)
    path = chat_service.get_persist_path(ctx)
    assert path is not None

    synced_inodes: list[int] = []
    real_fsync = _os.fsync

    def recording_fsync(fd: int) -> None:
        # fstat BEFORE the real call — the fd is guaranteed open here.
        synced_inodes.append(_os.fstat(fd).st_ino)
        real_fsync(fd)

    monkeypatch.setattr(chat_service.os, "fsync", recording_fsync)

    chat_service.append_turn(ctx, {"role": "user", "content": "durable?"})

    assert path.exists(), "append_turn wrote nothing"
    assert synced_inodes, "append_turn closed the file but never fsynced it"
    assert path.stat().st_ino in synced_inodes, (
        "append_turn fsynced a descriptor that is not chat.jsonl "
        f"(synced inodes {synced_inodes}, chat.jsonl {path.stat().st_ino})"
    )

    # The turn is still readable — fsync must not disturb the write path.
    turns = chat_service.read_all_turns(ctx)
    assert [t.get("content") for t in turns] == ["durable?"]
