"""
Phase 0 chatbot integration v6 — covers:

  1. ChatState dataclass shape (services/project_context.py).
  2. ProjectContext.chat_state default-factory + forward-carry by
     reset_network / set_network (services/pypsa_service.py).
  3. build_context EXCLUDES chat_state (background contexts get fresh state).
  4. _save_evicted_ctx flushes chat_state AFTER _save_context succeeds,
     INSIDE the same try/except umbrella (v4-MAJOR-2 placement;
     _evict_if_over_cap is UNCHANGED).
  5. BufferedLogQueue.subscribe()/unsubscribe() + None-sentinel skip in fanout
     (routers/simulation.py — v6 F8 invariant).
  6. _save_context's cross-project name-claim 409 guard fires correctly +
     placement is INSIDE ctx.mutation_lock (v6 F1 line-anchored).
  7. chat_service helpers: get_persist_path + append_turn + flush_to_disk
     (services/chat_service.py).

Tests intentionally avoid hitting the HTTP layer for the lifecycle pieces —
that's Phase 2's TestClient harness. Phase 0 verifies the building blocks
in isolation so a regression on either side is locally diagnosable.
"""
from __future__ import annotations

import json
import pathlib

import pypsa
import pytest

from routers import projects as projects_router
from routers.simulation import BufferedLogQueue
from services import chat_service
from services.chat_service import ChatSession
from services.project_context import ChatState, ProjectContext
from services.pypsa_service import PyPSAService


# ── 1. ChatState dataclass shape ────────────────────────────────────────────


def test_chatstate_default_fields():
    """ChatState has session=None, persist_path=None, lock is a threading.Lock."""
    s = ChatState()
    assert s.session is None
    assert s.persist_path is None
    assert hasattr(s.lock, "acquire") and hasattr(s.lock, "release")


def test_projectcontext_has_chat_state_field():
    """ProjectContext default factory yields a fresh ChatState per ctx."""
    n = pypsa.Network()
    ctx_a = ProjectContext(network=n)
    ctx_b = ProjectContext(network=n)
    assert isinstance(ctx_a.chat_state, ChatState)
    assert isinstance(ctx_b.chat_state, ChatState)
    # Distinct instances (no shared mutable default trap)
    assert ctx_a.chat_state is not ctx_b.chat_state
    assert ctx_a.chat_state.lock is not ctx_b.chat_state.lock


# ── 2. reset_network / set_network forward-carry chat_state ─────────────────


def test_reset_network_carries_chat_state_identity():
    """
    Prior context's chat_state must be the SAME instance on the new active
    ctx after reset_network — a same-project network swap (load/import/restore
    flow which begins with reset_network() then rebinds) must not silently
    destroy an active chat session mid-conversation.
    """
    PyPSAService.reset_network()
    prev = PyPSAService.get_active_context()
    sentinel_session = ChatSession()
    prev.chat_state.session = sentinel_session
    prev.chat_state.persist_path = "/some/cached/path/chat.jsonl"

    PyPSAService.reset_network()
    new_ctx = PyPSAService.get_active_context()

    # Identity check, not value: same ChatState object follows the ctx.
    assert new_ctx.chat_state is prev.chat_state
    assert new_ctx.chat_state.session is sentinel_session
    assert new_ctx.chat_state.persist_path == "/some/cached/path/chat.jsonl"


def test_set_network_carries_chat_state_identity():
    """set_network (clustering swap) preserves chat_state identity."""
    PyPSAService.reset_network()
    prev = PyPSAService.get_active_context()
    sentinel_session = ChatSession()
    prev.chat_state.session = sentinel_session

    new_n = pypsa.Network()
    PyPSAService.set_network(new_n)
    new_ctx = PyPSAService.get_active_context()

    assert new_ctx.chat_state is prev.chat_state
    assert new_ctx.chat_state.session is sentinel_session


# ── 3. build_context EXCLUDES chat_state ────────────────────────────────────


def test_build_context_does_not_carry_chat_state():
    """
    build_context() creates an off-to-the-side ctx (B4.3 background solve
    target) that MUST NOT inherit the foreground's chat_state — sharing the
    session would expose the foreground's in-flight conversation to a
    background-solve agent and cross-contaminate chat.jsonl persist target.
    """
    PyPSAService.reset_network()
    foreground = PyPSAService.get_active_context()
    foreground.chat_state.session = ChatSession()

    background = PyPSAService.build_context()

    assert background.chat_state is not foreground.chat_state
    assert background.chat_state.session is None
    assert background.chat_state.lock is not foreground.chat_state.lock


def test_build_context_with_carry_flags_still_skips_chat_state():
    """
    Even when the caller asks for solver_state + undo to be carried,
    chat_state is NOT carried (v6 M2 invariant — there is no opt-in flag).
    """
    PyPSAService.reset_network()
    foreground = PyPSAService.get_active_context()
    foreground.chat_state.session = ChatSession()

    background = PyPSAService.build_context(
        carry_solver_state=True, carry_undo=True,
    )

    assert background.chat_state is not foreground.chat_state
    assert background.chat_state.session is None


# ── 4. _save_evicted_ctx flushes chat_state after _save_context succeeds ────


def test_save_evicted_ctx_calls_chat_flush_after_save(monkeypatch):
    """
    _save_evicted_ctx must call chat_service.flush_to_disk AFTER
    _save_context returns successfully — same try/except umbrella so a flush
    failure logs but does not abort eviction (v4-MAJOR-2 placement).
    """
    call_log: list[str] = []

    def fake_save_context(ctx, name, *, expect=None, persist_user_ts=True, **kw):
        call_log.append(f"save:{name}")
        # _save_context's documented args from _save_evicted_ctx (expect=victim_id,
        # persist_user_ts=False) — assert here so a refactor that breaks the
        # contract fails loudly:
        assert expect == name
        assert persist_user_ts is False

    def fake_flush(ctx):
        call_log.append(f"flush:{ctx.loaded_project}")

    monkeypatch.setattr(projects_router, "_save_context", fake_save_context)
    monkeypatch.setattr(chat_service, "flush_to_disk", fake_flush)

    n = pypsa.Network()
    n.add("Bus", "B1")
    victim = ProjectContext(network=n, loaded_project="victim")

    PyPSAService._save_evicted_ctx("victim", victim)

    # Order matters: save first, then flush.
    assert call_log == ["save:victim", "flush:victim"]


def test_save_evicted_ctx_flush_failure_does_not_abort_eviction(monkeypatch, caplog):
    """
    A flush failure logs and the eviction completes — must not propagate.
    """
    monkeypatch.setattr(
        projects_router, "_save_context", lambda *a, **kw: None,
    )
    def boom(ctx):
        raise RuntimeError("flush failed")
    monkeypatch.setattr(chat_service, "flush_to_disk", boom)

    n = pypsa.Network()
    n.add("Bus", "B1")
    victim = ProjectContext(network=n, loaded_project="victim")

    # Must not raise:
    PyPSAService._save_evicted_ctx("victim", victim)


def test_save_evicted_ctx_skips_unbound_ctx(monkeypatch):
    """Unbound victim (loaded_project=None) — no save, no flush."""
    sentinel = []
    monkeypatch.setattr(
        projects_router, "_save_context",
        lambda *a, **kw: sentinel.append("save"),
    )
    monkeypatch.setattr(
        chat_service, "flush_to_disk",
        lambda c: sentinel.append("flush"),
    )

    n = pypsa.Network()
    n.add("Bus", "B1")
    victim = ProjectContext(network=n, loaded_project=None)
    PyPSAService._save_evicted_ctx("anything", victim)
    assert sentinel == []


def test_save_evicted_ctx_skips_empty_ctx(monkeypatch):
    """Empty (no buses) victim — no save, no flush."""
    sentinel = []
    monkeypatch.setattr(
        projects_router, "_save_context",
        lambda *a, **kw: sentinel.append("save"),
    )
    monkeypatch.setattr(
        chat_service, "flush_to_disk",
        lambda c: sentinel.append("flush"),
    )

    n = pypsa.Network()  # no buses
    victim = ProjectContext(network=n, loaded_project="bound")
    PyPSAService._save_evicted_ctx("bound", victim)
    assert sentinel == []


def test_evict_if_over_cap_does_not_call_chat_flush_directly():
    """
    v4-MAJOR-2 invariant: chat-flush is in _save_evicted_ctx, NOT
    _evict_if_over_cap. Verify by source inspection — _evict_if_over_cap
    must not reference chat_service.
    """
    import inspect
    src = inspect.getsource(PyPSAService._evict_if_over_cap)
    assert "chat_service" not in src, (
        "v4-MAJOR-2 violation: _evict_if_over_cap directly references "
        "chat_service. The flush belongs in _save_evicted_ctx so it inherits "
        "the OUTSIDE-_registry_lock placement (lock-discipline invariant)."
    )


# ── 5. BufferedLogQueue subscriber semantics ────────────────────────────────


def test_buffered_log_queue_subscribers_receive_real_lines():
    q = BufferedLogQueue(maxlen=10)
    sub_id, dq = q.subscribe()
    q.put("hello")
    q.put("world")
    assert list(dq) == ["hello", "world"]
    q.unsubscribe(sub_id)


def test_buffered_log_queue_subscribers_skip_none_sentinel():
    """
    v6 F8: None is the legacy /log_stream close-signal; subscriber deques
    MUST NOT receive it. Without this skip a chat consumer would interpret
    end-of-stream when a DIFFERENT /run's None fires.
    """
    q = BufferedLogQueue(maxlen=10)
    sub_id, dq = q.subscribe()
    q.put("a")
    q.put(None)
    q.put("b")
    assert list(dq) == ["a", "b"]
    q.unsubscribe(sub_id)


def test_buffered_log_queue_legacy_get_still_receives_none():
    """
    The legacy SSE consumer relies on None as close-signal — backwards
    compatibility invariant. Subscriber-side skip must NOT change legacy
    path behaviour.
    """
    q = BufferedLogQueue(maxlen=10)
    q.put("x")
    q.put(None)
    assert q.get(timeout=0.5) == "x"
    assert q.get(timeout=0.5) is None


def test_buffered_log_queue_unsubscribe_drops_subscriber():
    q = BufferedLogQueue(maxlen=10)
    sub_id, dq = q.subscribe()
    q.unsubscribe(sub_id)
    q.put("after-unsubscribe")
    assert list(dq) == []
    # Idempotent unsubscribe (no error on unknown id)
    q.unsubscribe(sub_id)
    q.unsubscribe(99999)


def test_buffered_log_queue_history_still_works_with_subscribers():
    """History behaviour unchanged by the fanout addition."""
    q = BufferedLogQueue(maxlen=10)
    q.subscribe()
    q.put("a")
    q.put("b")
    q.put(None)  # not recorded in history
    q.put("c")
    assert q.history() == ["a", "b", "c"]


def test_buffered_log_queue_multiple_subscribers_each_get_copy():
    """Two subscribers each independently see every (non-None) line."""
    q = BufferedLogQueue(maxlen=10)
    _, dq_a = q.subscribe()
    _, dq_b = q.subscribe()
    q.put("x")
    q.put(None)
    q.put("y")
    assert list(dq_a) == ["x", "y"]
    assert list(dq_b) == ["x", "y"]


# ── 6. _save_context cross-project 409 guard ────────────────────────────────


def _make_bound_ctx(name: str) -> ProjectContext:
    """A bound ctx with a non-empty network — sidesteps the empty-network gate."""
    n = pypsa.Network()
    n.add("Bus", "B1")
    return ProjectContext(network=n, loaded_project=name)


def _seed_existing_project(projects_dir: pathlib.Path, name: str) -> None:
    """
    Create a sibling project's directory with a placeholder network.nc so
    `nc_path.exists()` is True at the guard. Content is irrelevant — we never
    reach the read path; the guard fires before any export.
    """
    proj = projects_dir / name
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "network.nc").write_bytes(b"placeholder")


def test_save_guard_blocks_cross_project_overwrite(tmp_projects_dir):
    """
    Active project A; POST /projects/B where B already has data on disk,
    no rebind, no force → 409. Legacy code silently overwrote B's data with
    A's network and left binding on A.
    """
    _seed_existing_project(tmp_projects_dir, "B")
    ctx = _make_bound_ctx("A")
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        projects_router._save_context(ctx, "B")
    assert exc.value.status_code == 409
    # Marker phrase in the error message lets the frontend / agent
    # disambiguate this from the older expect-mismatch 409.
    assert "DIFFERENT project" in str(exc.value.detail)


def test_save_guard_allows_same_project_resave(tmp_projects_dir):
    """Loaded == name — re-save (autosave) path, no 409 from the new guard."""
    _seed_existing_project(tmp_projects_dir, "A")
    ctx = _make_bound_ctx("A")
    # Re-save under same name. The export may or may not succeed (test
    # environment specific), but the new guard must NOT fire — so any error
    # message must not mention the cross-project marker phrase.
    try:
        projects_router._save_context(ctx, "A")
    except Exception as e:
        assert "DIFFERENT project" not in str(e), (
            f"v6 F1 false positive: same-project re-save hit cross-project "
            f"guard: {e!r}"
        )


def test_save_guard_allows_rebind_save_as(tmp_projects_dir):
    """loaded=A, name=B, B exists, rebind=True → Save-As, guard does NOT fire."""
    _seed_existing_project(tmp_projects_dir, "B")
    ctx = _make_bound_ctx("A")
    try:
        projects_router._save_context(ctx, "B", rebind=True)
    except Exception as e:
        assert "DIFFERENT project" not in str(e), (
            f"v6 F1 false positive: Save-As (rebind=True) hit cross-project "
            f"guard: {e!r}"
        )


def test_save_guard_allows_force(tmp_projects_dir):
    """loaded=A, name=B, B exists, force=True → acknowledged overwrite, no 409."""
    _seed_existing_project(tmp_projects_dir, "B")
    ctx = _make_bound_ctx("A")
    try:
        projects_router._save_context(ctx, "B", force=True)
    except Exception as e:
        assert "DIFFERENT project" not in str(e), (
            f"v6 F1 false positive: force=True hit cross-project guard: {e!r}"
        )


def test_save_guard_allows_first_save_unbound(tmp_projects_dir):
    """
    Unbound ctx (loaded=None), name=B, B doesn't exist → first-save claim,
    no 409. Unbound first-save to a NAME-CLASH where B exists is intentionally
    not gated by this guard (the empty-network check at line 957 catches the
    empty-vs-existing-data sub-case; non-empty unbound first-save is treated
    as bind/claim semantics).
    """
    n = pypsa.Network()
    n.add("Bus", "B1")
    ctx = ProjectContext(network=n, loaded_project=None)
    try:
        projects_router._save_context(ctx, "B")
    except Exception as e:
        assert "DIFFERENT project" not in str(e), (
            f"v6 F1 false positive: unbound first-save hit cross-project "
            f"guard: {e!r}"
        )


def test_save_guard_is_inside_mutation_lock(tmp_projects_dir):
    """
    v6 F1 PLACEMENT EVIDENCE: the 409 guard sits INSIDE the
    `with ctx.mutation_lock:` block. Mock the lock's __enter__ to raise a
    sentinel — if the guard fired BEFORE entering the lock, we would see
    HTTPException(409); since the guard sits INSIDE the lock, the lock's
    __enter__ raises first and HTTPException is never raised.
    """
    _seed_existing_project(tmp_projects_dir, "B")
    ctx = _make_bound_ctx("A")

    class _LockEnterRaised(Exception):
        """Sentinel — must be raised by the mocked lock's __enter__."""

    class _CMRaiseOnEnter:
        def __enter__(self):
            raise _LockEnterRaised("ctx.mutation_lock.__enter__ called")

        def __exit__(self, *a, **kw):
            return False

    ctx.mutation_lock = _CMRaiseOnEnter()  # type: ignore[assignment]
    with pytest.raises(_LockEnterRaised):
        projects_router._save_context(ctx, "B")


def test_save_guard_source_placement_invariant():
    """
    Source-level check: the new 409 guard ('DIFFERENT project') sits BETWEEN
    the existing `loaded = ctx.loaded_project` line and the empty-network
    safety check `if not force and nc_path.exists() and n.buses.empty:` —
    INSIDE the `with ctx.mutation_lock:` block. Detects future refactors
    that move the guard outside the lock.
    """
    src_path = pathlib.Path(projects_router.__file__)
    lines = src_path.read_text(encoding="utf-8").splitlines()

    def line_with(text: str) -> int:
        for i, line in enumerate(lines, start=1):
            if text in line:
                return i
        raise AssertionError(f"line containing {text!r} not found")

    lock_line = line_with("with ctx.mutation_lock:")
    loaded_read_line = line_with("loaded = ctx.loaded_project")
    # Unique marker — this exact phrase appears ONLY in the v6 F1 guard's
    # raise body (verified via grep at test-write time). Future refactors
    # that re-word the message must also update this anchor.
    guard_line = line_with("already exists on disk and the in-memory")
    empty_check_line = line_with(
        "if not force and nc_path.exists() and n.buses.empty:"
    )

    assert lock_line < loaded_read_line < guard_line < empty_check_line, (
        f"v6 F1 source-placement invariant violated: lock={lock_line}, "
        f"loaded_read={loaded_read_line}, guard={guard_line}, "
        f"empty_check={empty_check_line}"
    )


# ── 7. chat_service helpers ─────────────────────────────────────────────────


def test_chat_session_session6_six_chars():
    """session6() returns the first 6 hex chars (audit-log action prefix)."""
    s = ChatSession()
    assert len(s.session6()) == 6
    assert s.session6() == s.session_id[:6]


def test_append_turn_unbound_is_noop(tmp_projects_dir, monkeypatch):
    """Unbound ctx → append_turn silently drops the turn (no file written)."""
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)
    n = pypsa.Network()
    ctx = ProjectContext(network=n, loaded_project=None)
    chat_service.append_turn(ctx, {"role": "user", "content": "hi"})
    assert list(tmp_projects_dir.iterdir()) == []


def test_append_turn_bound_writes_jsonl(tmp_projects_dir, monkeypatch):
    """Bound ctx → append writes one JSON line per call, encoded as UTF-8."""
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)
    n = pypsa.Network()
    ctx = ProjectContext(network=n, loaded_project="proj1")

    chat_service.append_turn(ctx, {"role": "user", "content": "hello"})
    chat_service.append_turn(ctx, {"role": "assistant", "content": "world"})
    chat_service.append_turn(ctx, {"role": "user", "content": "Grüße"})

    chat_path = tmp_projects_dir / "proj1" / "chat.jsonl"
    assert chat_path.exists()
    lines = chat_path.read_text(encoding="utf-8").rstrip("\n").split("\n")
    assert len(lines) == 3
    assert json.loads(lines[0])["content"] == "hello"
    assert json.loads(lines[1])["content"] == "world"
    assert json.loads(lines[2])["content"] == "Grüße"


def test_get_persist_path_caches_on_chat_state(tmp_projects_dir, monkeypatch):
    """get_persist_path caches resolved path on ctx.chat_state.persist_path."""
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)
    n = pypsa.Network()
    ctx = ProjectContext(network=n, loaded_project="proj1")
    assert ctx.chat_state.persist_path is None
    p1 = chat_service.get_persist_path(ctx)
    assert p1 is not None
    assert ctx.chat_state.persist_path == str(p1)
    # Second call returns same path; cache hit
    p2 = chat_service.get_persist_path(ctx)
    assert p2 == p1


def test_get_persist_path_unbound_returns_none():
    """Unbound ctx → None, no cache mutation."""
    n = pypsa.Network()
    ctx = ProjectContext(network=n, loaded_project=None)
    assert chat_service.get_persist_path(ctx) is None
    assert ctx.chat_state.persist_path is None


def test_flush_to_disk_is_noop_on_any_ctx():
    """Phase 0: flush_to_disk is a no-op; safe to call on any ctx state."""
    n = pypsa.Network()
    chat_service.flush_to_disk(ProjectContext(network=n, loaded_project=None))
    chat_service.flush_to_disk(ProjectContext(network=n.copy(), loaded_project="X"))


def test_append_turn_rotates_when_oversize(tmp_projects_dir, monkeypatch):
    """
    When chat.jsonl >= ROTATE_BYTES, append_turn renames it to chat.jsonl.1
    before writing the next turn — under the SAME lock (v4-MINOR-2).
    """
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)
    # Lower the rotation threshold for the test so we don't have to fabricate
    # a 5 MiB file.
    monkeypatch.setattr(chat_service, "ROTATE_BYTES", 100)

    n = pypsa.Network()
    ctx = ProjectContext(network=n, loaded_project="rot")
    chat_path = tmp_projects_dir / "rot" / "chat.jsonl"
    backup_path = tmp_projects_dir / "rot" / "chat.jsonl.1"

    # Write enough to cross the 100-byte threshold.
    chat_service.append_turn(ctx, {"role": "user", "content": "x" * 150})
    assert chat_path.exists() and chat_path.stat().st_size >= 100
    assert not backup_path.exists()

    # Next append rotates BEFORE writing.
    chat_service.append_turn(ctx, {"role": "user", "content": "next"})
    assert backup_path.exists()
    assert chat_path.exists()
    # The new chat.jsonl only contains the second turn.
    new_lines = chat_path.read_text(encoding="utf-8").rstrip("\n").split("\n")
    assert len(new_lines) == 1
    assert json.loads(new_lines[0])["content"] == "next"


# ── 8. .gitignore audit (chatbot integration v6 F4 simplified) ──────────────


def test_gitignore_covers_chat_jsonl_and_env_files():
    """
    Phase 0 gitignore audit: backend/projects/ already covers chat.jsonl
    (per the in-file comment), and the new entries cover .env / .env.local /
    .env.*. Asserts the literal lines so a future cleanup that drops one
    fails the test.
    """
    gi = (
        pathlib.Path(projects_router.__file__).resolve()
        .parent.parent.parent / ".gitignore"
    )
    assert gi.exists(), f"missing .gitignore at {gi}"
    text = gi.read_text(encoding="utf-8")
    assert "backend/projects/" in text, "chat.jsonl coverage line missing"
    for entry in (".env", ".env.local", ".env.*"):
        assert entry in text, f"missing gitignore entry: {entry}"
