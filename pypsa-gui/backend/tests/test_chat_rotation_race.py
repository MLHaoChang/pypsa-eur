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
