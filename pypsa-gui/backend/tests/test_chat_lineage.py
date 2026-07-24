"""
Phase 4 — chat.jsonl lineage rules (F12 + C2 + rename cache invalidation).

Tests each lineage helper in isolation + integration tests that drive the
route handlers via TestClient and confirm chat.jsonl moves / copies as
specified.

Modes covered:
  * handle_save_lineage(mode='rebind_move') — Save-As: MOVE source/target
  * handle_save_lineage(mode='copy')        — Save-a-Copy: COPY source→target
  * handle_save_lineage(mode='scenario_copy') — create_scenario: COPY
  * handle_rename_lineage                   — persist_path cache invalidation
  * handle_snapshot_lineage(mode='create')  — snapshot bundle includes chat.jsonl
  * handle_snapshot_lineage(mode='restore') — restore overwrites chat.jsonl
"""
from __future__ import annotations

import json
import pathlib

import pypsa
import pytest

from services import chat_service
from services.project_context import ProjectContext


def _make_bound_ctx(name: str, projects_dir: pathlib.Path) -> ProjectContext:
    """Bind a fresh ctx to `name` with a non-empty network. Pre-creates the dir."""
    n = pypsa.Network()
    n.add("Bus", "B1")
    ctx = ProjectContext(network=n, loaded_project=name)
    (projects_dir / name).mkdir(parents=True, exist_ok=True)
    return ctx


def _write_chat(projects_dir: pathlib.Path, project: str, lines: list[dict]) -> pathlib.Path:
    """Write a deterministic chat.jsonl into `projects_dir / project /`."""
    path = projects_dir / project / chat_service.CHAT_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line))
            f.write("\n")
    return path


# ─────────────────────────────────────────────────────────────────────────
# handle_save_lineage — rebind_move (Save-As)
# ─────────────────────────────────────────────────────────────────────────


def test_save_lineage_uses_explicit_source_name_after_rebind(tmp_projects_dir, monkeypatch):
    """
    Phase 4 walkthrough bug: by the time _save_context calls
    handle_save_lineage(rebind_move) at its tail, ctx.loaded_project has
    ALREADY been re-bound to the new name. Without an explicit source_name,
    the helper would resolve src == dst and the move would be a no-op.

    Simulate this exactly: bind ctx to the NEW name (post-rebind state) and
    pass the OLD name explicitly. The lineage should still MOVE from the
    old dir to the new one.
    """
    from routers import projects as projects_router
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)

    n = pypsa.Network()
    n.add("Bus", "B1")
    # Bind ctx to NEW name (post-rebind state)
    ctx = ProjectContext(network=n, loaded_project="NewName")
    chat_service.get_persist_path(ctx)  # cache populated to NewName

    # Seed the OLD project dir with chat history
    (tmp_projects_dir / "OldName").mkdir(exist_ok=True)
    (tmp_projects_dir / "NewName").mkdir(exist_ok=True)
    src_path = _write_chat(tmp_projects_dir, "OldName",
                            [{"role": "user", "content": "ten turns"}])

    # Without explicit source_name, src would resolve to NewName/chat.jsonl
    # (doesn't exist) and the move would be a no-op. With explicit source,
    # the MOVE actually happens.
    chat_service.handle_save_lineage(
        ctx, target_name="NewName",
        mode=chat_service.SAVE_LINEAGE_REBIND_MOVE,
        source_name="OldName",
    )

    assert not src_path.exists(), "source chat.jsonl should have been moved"
    dst_path = tmp_projects_dir / "NewName" / chat_service.CHAT_FILENAME
    assert dst_path.exists()
    assert "ten turns" in dst_path.read_text(encoding="utf-8")
    # Cache invalidated so a subsequent get_persist_path re-resolves
    assert ctx.chat_state.persist_path is None


def test_save_lineage_rebind_move_moves_chat_jsonl(tmp_projects_dir, monkeypatch):
    """Save-As moves chat.jsonl from source dir to target dir + invalidates cache."""
    from routers import projects as projects_router
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)

    ctx = _make_bound_ctx("A", tmp_projects_dir)
    chat_service.get_persist_path(ctx)  # cache populated to A/chat.jsonl
    src_path = _write_chat(tmp_projects_dir, "A",
                            [{"role": "user", "content": "hello"}])
    (tmp_projects_dir / "B").mkdir()  # post-save dest dir already exists

    # Now apply Save-As: rebind_move from A → B (the route handler would
    # have already mutated ctx.loaded_project; the lineage helper looks at
    # the SOURCE = ctx.loaded_project BEFORE the rebind. We pass the live
    # ctx pre-rebind to match production semantics).
    chat_service.handle_save_lineage(
        ctx, target_name="B", mode=chat_service.SAVE_LINEAGE_REBIND_MOVE,
    )

    # Source chat.jsonl gone; target has the content
    assert not src_path.exists()
    dst_path = tmp_projects_dir / "B" / chat_service.CHAT_FILENAME
    assert dst_path.exists()
    content = dst_path.read_text(encoding="utf-8")
    assert "hello" in content
    # Cache invalidated so the next get_persist_path resolves the new dir
    assert ctx.chat_state.persist_path is None


def test_save_lineage_rebind_move_overwrites_existing_target(tmp_projects_dir, monkeypatch):
    """rebind_move into a target that already has chat.jsonl: target is replaced."""
    from routers import projects as projects_router
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)

    ctx = _make_bound_ctx("A", tmp_projects_dir)
    src_path = _write_chat(tmp_projects_dir, "A", [{"src": "new"}])
    dst_path = _write_chat(tmp_projects_dir, "B", [{"dst": "old"}])

    chat_service.handle_save_lineage(
        ctx, target_name="B", mode=chat_service.SAVE_LINEAGE_REBIND_MOVE,
    )

    assert not src_path.exists()
    assert dst_path.exists()
    assert "new" in dst_path.read_text(encoding="utf-8")
    assert "old" not in dst_path.read_text(encoding="utf-8")


def test_save_lineage_rebind_move_carries_rotation_backup(tmp_projects_dir, monkeypatch):
    """If chat.jsonl.1 exists at source, it MOVES too."""
    from routers import projects as projects_router
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)

    ctx = _make_bound_ctx("A", tmp_projects_dir)
    _write_chat(tmp_projects_dir, "A", [{"current": True}])
    backup_src = tmp_projects_dir / "A" / (chat_service.CHAT_FILENAME + ".1")
    backup_src.write_text('{"rotated":true}\n', encoding="utf-8")

    chat_service.handle_save_lineage(
        ctx, target_name="B", mode=chat_service.SAVE_LINEAGE_REBIND_MOVE,
    )

    backup_dst = tmp_projects_dir / "B" / (chat_service.CHAT_FILENAME + ".1")
    assert backup_dst.exists()
    assert "rotated" in backup_dst.read_text(encoding="utf-8")
    assert not backup_src.exists()


# ─────────────────────────────────────────────────────────────────────────
# handle_save_lineage — copy (Save-a-Copy)
# ─────────────────────────────────────────────────────────────────────────


def test_save_lineage_copy_preserves_source_and_creates_target(tmp_projects_dir, monkeypatch):
    """Save-a-Copy: source chat.jsonl preserved, target gets a copy."""
    from routers import projects as projects_router
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)

    ctx = _make_bound_ctx("A", tmp_projects_dir)
    src_path = _write_chat(tmp_projects_dir, "A",
                            [{"role": "user", "content": "hello"}])
    chat_service.get_persist_path(ctx)

    chat_service.handle_save_lineage(
        ctx, target_name="B", mode=chat_service.SAVE_LINEAGE_COPY,
    )

    # Source intact
    assert src_path.exists()
    assert "hello" in src_path.read_text(encoding="utf-8")
    # Target has identical content
    dst_path = tmp_projects_dir / "B" / chat_service.CHAT_FILENAME
    assert dst_path.exists()
    assert dst_path.read_text(encoding="utf-8") == src_path.read_text(encoding="utf-8")
    # Cache NOT invalidated — active session continues at A
    assert ctx.chat_state.persist_path is not None
    assert ctx.chat_state.persist_path.endswith(
        str(pathlib.Path("A") / chat_service.CHAT_FILENAME)
    ) or ctx.chat_state.persist_path.endswith(
        str(pathlib.Path("A") / chat_service.CHAT_FILENAME).replace("\\", "/")
    )


def test_save_lineage_scenario_copy_uses_copy_behavior(tmp_projects_dir, monkeypatch):
    """scenario_copy mode behaves like copy — source preserved, target created."""
    from routers import projects as projects_router
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)

    ctx = _make_bound_ctx("A", tmp_projects_dir)
    src_path = _write_chat(tmp_projects_dir, "A", [{"src": True}])

    chat_service.handle_save_lineage(
        ctx, target_name="A_scenario", mode=chat_service.SAVE_LINEAGE_SCENARIO_COPY,
    )

    assert src_path.exists()
    dst_path = tmp_projects_dir / "A_scenario" / chat_service.CHAT_FILENAME
    assert dst_path.exists()
    assert dst_path.read_text(encoding="utf-8") == src_path.read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────
# handle_save_lineage — error tolerance + no-ops
# ─────────────────────────────────────────────────────────────────────────


def test_save_lineage_no_source_chat_is_noop(tmp_projects_dir, monkeypatch):
    """Source has no chat.jsonl — lineage is a clean no-op (no errors)."""
    from routers import projects as projects_router
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)

    ctx = _make_bound_ctx("A", tmp_projects_dir)
    chat_service.handle_save_lineage(
        ctx, target_name="B", mode=chat_service.SAVE_LINEAGE_REBIND_MOVE,
    )
    # Cache still invalidated for rebind_move; for copy it stays as None
    assert (tmp_projects_dir / "B" / chat_service.CHAT_FILENAME).exists() is False


def test_save_lineage_unbound_ctx_is_noop(tmp_projects_dir, monkeypatch):
    from routers import projects as projects_router
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)

    n = pypsa.Network()
    ctx = ProjectContext(network=n, loaded_project=None)
    chat_service.handle_save_lineage(ctx, target_name="B",
                                       mode=chat_service.SAVE_LINEAGE_REBIND_MOVE)
    # Nothing should be created or moved.
    assert not (tmp_projects_dir / "B").exists()


def test_save_lineage_unknown_mode_is_noop_with_warning(tmp_projects_dir, monkeypatch, caplog):
    from routers import projects as projects_router
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)
    ctx = _make_bound_ctx("A", tmp_projects_dir)
    src_path = _write_chat(tmp_projects_dir, "A", [{"hi": 1}])

    with caplog.at_level("WARNING"):
        chat_service.handle_save_lineage(ctx, target_name="B", mode="bogus")

    # Source untouched.
    assert src_path.exists()
    assert any("unknown mode" in r.message for r in caplog.records)


# ─────────────────────────────────────────────────────────────────────────
# handle_rename_lineage — cache invalidation (Phase 3 Gate C-13 fix)
# ─────────────────────────────────────────────────────────────────────────


def test_rename_lineage_invalidates_persist_path_cache(tmp_projects_dir, monkeypatch):
    """rename_project must clear the cached persist_path so next resolve picks the new dir."""
    from routers import projects as projects_router
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)

    ctx = _make_bound_ctx("Old", tmp_projects_dir)
    chat_service.get_persist_path(ctx)  # cache populated to Old/chat.jsonl
    assert "Old" in (ctx.chat_state.persist_path or "")

    # Simulate the directory rename (production handler does this on disk).
    (tmp_projects_dir / "New").mkdir()
    ctx.loaded_project = "New"

    chat_service.handle_rename_lineage(ctx, "Old", "New")
    assert ctx.chat_state.persist_path is None  # cache cleared

    # Next resolution picks the new dir.
    new_path = chat_service.get_persist_path(ctx)
    assert new_path is not None
    assert "New" in str(new_path)


# ─────────────────────────────────────────────────────────────────────────
# handle_snapshot_lineage — create (C2)
# ─────────────────────────────────────────────────────────────────────────


def test_snapshot_lineage_create_includes_chat_jsonl(tmp_projects_dir, monkeypatch):
    """create_project_snapshot copies chat.jsonl + chat.jsonl.1 into snap dir."""
    from routers import projects as projects_router
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)

    ctx = _make_bound_ctx("P", tmp_projects_dir)
    _write_chat(tmp_projects_dir, "P", [{"main": True}])
    (tmp_projects_dir / "P" / (chat_service.CHAT_FILENAME + ".1")).write_text(
        '{"rotated":true}\n', encoding="utf-8",
    )
    snap_dir = tmp_projects_dir / "P" / "_snapshots" / "snap1"

    chat_service.handle_snapshot_lineage(ctx, snap_dir, mode="create")

    assert (snap_dir / chat_service.CHAT_FILENAME).exists()
    assert (snap_dir / (chat_service.CHAT_FILENAME + ".1")).exists()
    assert "main" in (snap_dir / chat_service.CHAT_FILENAME).read_text(encoding="utf-8")
    # Active chat.jsonl untouched
    assert (tmp_projects_dir / "P" / chat_service.CHAT_FILENAME).exists()


def test_snapshot_lineage_create_handles_missing_chat_gracefully(tmp_projects_dir, monkeypatch):
    """If the project has no chat.jsonl yet, snapshot create is a no-op."""
    from routers import projects as projects_router
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)

    ctx = _make_bound_ctx("P", tmp_projects_dir)
    snap_dir = tmp_projects_dir / "P" / "_snapshots" / "snap0"

    chat_service.handle_snapshot_lineage(ctx, snap_dir, mode="create")

    assert snap_dir.exists()  # dir created by mkdir(parents=True)
    assert not (snap_dir / chat_service.CHAT_FILENAME).exists()


# ─────────────────────────────────────────────────────────────────────────
# handle_snapshot_lineage — restore (C2)
# ─────────────────────────────────────────────────────────────────────────


def test_snapshot_lineage_restore_overwrites_active_chat(tmp_projects_dir, monkeypatch):
    """restore_project_snapshot replaces active chat.jsonl with snapshot copy."""
    from routers import projects as projects_router
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)

    ctx = _make_bound_ctx("P", tmp_projects_dir)
    chat_service.get_persist_path(ctx)

    # Active project has CURRENT chat.jsonl; snapshot has OLDER content
    _write_chat(tmp_projects_dir, "P", [{"current": "now"}])
    snap_dir = tmp_projects_dir / "P" / "_snapshots" / "older"
    snap_dir.mkdir(parents=True)
    (snap_dir / chat_service.CHAT_FILENAME).write_text(
        '{"snapshot":"older"}\n', encoding="utf-8",
    )

    chat_service.handle_snapshot_lineage(ctx, snap_dir, mode="restore")

    # Active chat.jsonl now mirrors the snapshot
    active = tmp_projects_dir / "P" / chat_service.CHAT_FILENAME
    text = active.read_text(encoding="utf-8")
    assert "older" in text
    assert "now" not in text
    # persist_path cache invalidated (file content changed, path didn't)
    assert ctx.chat_state.persist_path is None


def test_snapshot_lineage_restore_with_no_snap_chat_clears_active(tmp_projects_dir, monkeypatch):
    """If the snapshot has no chat.jsonl, restore wipes the active one to match."""
    from routers import projects as projects_router
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)

    ctx = _make_bound_ctx("P", tmp_projects_dir)
    _write_chat(tmp_projects_dir, "P", [{"live": True}])
    snap_dir = tmp_projects_dir / "P" / "_snapshots" / "blank"
    snap_dir.mkdir(parents=True)

    chat_service.handle_snapshot_lineage(ctx, snap_dir, mode="restore")

    assert not (tmp_projects_dir / "P" / chat_service.CHAT_FILENAME).exists()


# ─────────────────────────────────────────────────────────────────────────
# Lock discipline — lineage runs under ctx.chat_state.lock
# ─────────────────────────────────────────────────────────────────────────


def test_lineage_helpers_acquire_chat_state_lock(tmp_projects_dir, monkeypatch):
    """
    Verify the lineage helpers acquire ctx.chat_state.lock — by source
    inspection. The lock is required for serialisation with concurrent
    append_turn writes (M9 + v4-MINOR-2).
    """
    import inspect

    for fn in (
        chat_service.handle_save_lineage,
        chat_service.handle_snapshot_lineage,
        chat_service.handle_rename_lineage,
    ):
        src = inspect.getsource(fn)
        assert "with ctx.chat_state.lock:" in src, (
            f"{fn.__name__} body does not acquire ctx.chat_state.lock"
        )


# ─────────────────────────────────────────────────────────────────────────
# Integration: _save_context calls handle_save_lineage on rebind_move
# ─────────────────────────────────────────────────────────────────────────


def test_save_context_rebind_invokes_lineage_rebind_move(tmp_projects_dir, monkeypatch):
    """When _save_context is invoked with rebind=True against a DIFFERENT
    target than ctx.loaded_project, handle_save_lineage runs with mode='rebind_move'."""
    from routers import projects as projects_router
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)

    calls: list[tuple[str, str, str | None]] = []

    def fake_lineage(ctx, target_name: str, mode: str, source_name=None):
        calls.append((target_name, mode, source_name))

    monkeypatch.setattr(chat_service, "handle_save_lineage", fake_lineage)

    # Set up a bound ctx + create the destination dir up-front (the save
    # also _safe_project_dir.mkdir's it, but pre-existence is harmless).
    from services.pypsa_service import PyPSAService
    n = pypsa.Network()
    n.add("Bus", "B1")
    PyPSAService.set_network(n)
    PyPSAService.set_loaded_project("A")
    (tmp_projects_dir / "A").mkdir(exist_ok=True)

    ctx = PyPSAService.get_active_context()
    # Drive _save_context with rebind=True, target=B
    projects_router._save_context(ctx, "B", rebind=True)

    # The lineage call captured the documented args, including the explicit
    # source_name (the PRE-rebind binding "A").
    assert calls
    target_name, mode, source_name = calls[0]
    assert target_name == "B"
    assert mode == chat_service.SAVE_LINEAGE_REBIND_MOVE
    assert source_name == "A", (
        f"source_name not threaded — _save_context still relies on "
        f"ctx.loaded_project which has been re-bound to {target_name!r}"
    )


def test_save_context_no_rebind_diff_target_invokes_lineage_copy(tmp_projects_dir, monkeypatch):
    """Save-a-Copy semantics: loaded != name AND rebind=False → mode='copy'."""
    from routers import projects as projects_router
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)

    calls: list[tuple[str, str, str | None]] = []
    monkeypatch.setattr(
        chat_service, "handle_save_lineage",
        lambda ctx, target_name, mode, source_name=None:
            calls.append((target_name, mode, source_name)),
    )

    from services.pypsa_service import PyPSAService
    n = pypsa.Network()
    n.add("Bus", "B1")
    PyPSAService.set_network(n)
    PyPSAService.set_loaded_project("A")
    (tmp_projects_dir / "A").mkdir(exist_ok=True)

    ctx = PyPSAService.get_active_context()
    projects_router._save_context(ctx, "B", rebind=False)

    assert calls
    # source_name is the PRE-save loaded_project ("A"); save-a-copy mode
    # does NOT rebind, so ctx.loaded_project stays "A" too — both reach the
    # helper as "A".
    assert calls[0] == ("B", chat_service.SAVE_LINEAGE_COPY, "A")


def test_save_context_same_name_no_lineage_call(tmp_projects_dir, monkeypatch):
    """Routine re-save (loaded == name) → no lineage transition."""
    from routers import projects as projects_router
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)

    calls: list = []
    monkeypatch.setattr(
        chat_service, "handle_save_lineage",
        lambda *a, **k: calls.append((a, k)),
    )

    from services.pypsa_service import PyPSAService
    n = pypsa.Network()
    n.add("Bus", "B1")
    PyPSAService.set_network(n)
    PyPSAService.set_loaded_project("A")
    (tmp_projects_dir / "A").mkdir(exist_ok=True)

    ctx = PyPSAService.get_active_context()
    projects_router._save_context(ctx, "A")  # routine re-save

    assert calls == []  # NO lineage transition


# ─────────────────────────────────────────────────────────────────────────
# Routing-level regression: _create_snapshot_internal must not shadow `shutil`
# ─────────────────────────────────────────────────────────────────────────


def test_create_snapshot_internal_no_shutil_unboundlocalerror(tmp_projects_dir, monkeypatch):
    """
    E2E walkthrough bug 2026-06-07: a function-local `import shutil` inside the
    non-active-ctx branch of `_create_snapshot_internal` shadowed the module-
    level `shutil`, making the EARLIER `shutil.copy2(src, snap_dir / fname)`
    line raise `UnboundLocalError`. Every snapshot create surfaced as a 500
    with empty plain-text body. Test calls the function directly through the
    common active-ctx path — Python's local-binding analysis at function
    entry would re-trigger the bug regardless of which branch executes.
    """
    from routers import projects as projects_router
    from routers import snapshots as snapshots_router
    from services.pypsa_service import PyPSAService

    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)

    # Bind a saved project so the snapshot endpoint can find network.nc.
    n = pypsa.Network()
    n.add("Bus", "B1")
    PyPSAService.set_network(n)
    PyPSAService.set_loaded_project("Proj")
    proj_dir = tmp_projects_dir / "Proj"
    proj_dir.mkdir(exist_ok=True)
    n.export_to_netcdf(str(proj_dir / "network.nc"))
    _write_chat(tmp_projects_dir, "Proj", [{"hello": "world"}])

    info = snapshots_router._create_snapshot_internal("Proj", "regression", "")
    assert info.id  # snapshot id assigned (function returned successfully)

    snap_dir = proj_dir / "snapshots" / info.id
    assert (snap_dir / "network.nc").exists()
    assert (snap_dir / chat_service.CHAT_FILENAME).exists()
    assert "hello" in (snap_dir / chat_service.CHAT_FILENAME).read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────
# Concurrency contract: append_turn serialises under ctx.chat_state.lock
# ─────────────────────────────────────────────────────────────────────────


def test_concurrent_append_turn_no_torn_writes_no_lost_writes(
    tmp_projects_dir, monkeypatch,
):
    """
    M9 lock contract: N threads writing turns to the same ctx must all
    persist (no lost writes) AND every persisted line must be valid JSON
    (no torn writes). Without ctx.chat_state.lock the file would interleave
    partial line writes between threads and parse-breakage would surface.
    """
    import json
    import threading

    from routers import projects as projects_router
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)

    ctx = _make_bound_ctx("Concur", tmp_projects_dir)
    N = 64

    errors: list[tuple[int, str]] = []

    def write_one(i: int) -> None:
        try:
            chat_service.append_turn(ctx, {
                "ts": 1.0 + i * 0.001,
                "session_id": f"s{i:03d}",
                "model": "fake",
                "user": f"concur-msg-{i:03d}",
                "assistant": [],
                "usage": {},
            })
        except Exception as exc:  # noqa: BLE001
            errors.append((i, repr(exc)))

    threads = [threading.Thread(target=write_one, args=(i,)) for i in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, f"append_turn raised in {len(errors)} threads: {errors[:3]}"

    chat_path = tmp_projects_dir / "Concur" / chat_service.CHAT_FILENAME
    assert chat_path.exists()
    ok = bad = 0
    seen = set()
    with chat_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                ok += 1
                u = d.get("user")
                if isinstance(u, str) and u.startswith("concur-msg-"):
                    seen.add(u)
            except json.JSONDecodeError:
                bad += 1
    assert bad == 0, f"{bad} torn/invalid JSON lines in chat.jsonl"
    assert len(seen) == N, (
        f"lost writes: {N - len(seen)}/{N} concurrent messages missing"
    )
    assert ok >= N
