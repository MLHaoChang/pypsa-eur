"""
Sidecar carry on a cross-project save — `_carry_sidecars_on_move` lifted out of
`routers/projects.py::_save_context`.

When a save's TARGET differs from the project the network is bound to, two
sidecars travel with it, and they travel differently:

  * **`chat.jsonl`** — MOVED on `rebind=True` (Save-As claims the new name, so
    the conversation goes with it) and COPIED otherwise (Save-a-Copy /
    create_scenario leave the original's thread intact).
  * **`uploads/`** — ALWAYS copied. Uploads are reference material a user may
    want in both projects, not a per-conversation thread.

Four properties, each of which fails differently if the extraction drops it.

**`source_name` must be threaded explicitly.** By the time this runs,
`ctx.loaded_project` has ALREADY been re-bound to the target, so a lineage
helper left to resolve its own source sees src == dst and silently no-ops. That
is a bug this codebase already had once ("Phase 4 walkthrough bug"), and the
existing `tests/test_chat_lineage.py` guards it at the call level; this file
guards it at the seam.

**The two modes must not swap.** `rebind_move` where a copy was meant destroys
the source project's chat history.

**Both halves are best-effort.** Neither a lineage failure nor a copy failure
may abort a save the user asked for — the network is already on disk by this
point, so raising here would report failure for a save that succeeded. They
differ deliberately in one respect: the copy failure is LOGGED, the lineage
failure is swallowed silently.

**The source directory is resolved through the registry in auth mode.** Storage
is org-scoped, so `_safe_project_dir(loaded)` names a flat path that is wrong (or
absent) once a DB and user are in play. Copying from there silently carries
nothing, or the wrong project's uploads.
"""
from __future__ import annotations

import inspect
import pathlib

import pytest

from routers import projects as projects_router
from services import chat_service


def _seam():
    fn = getattr(projects_router, "_carry_sidecars_on_move", None)
    assert fn is not None, "routers.projects._carry_sidecars_on_move does not exist yet"
    return fn


class _Ctx:
    """Post-rebind context: `loaded_project` already points at the TARGET."""

    def __init__(self, target):
        self.loaded_project = target


@pytest.fixture
def spy(monkeypatch):
    """Record what the seam asks of the lineage helper and the dir copier."""
    calls: dict[str, list] = {"lineage": [], "copy": []}
    monkeypatch.setattr(
        chat_service, "handle_save_lineage",
        lambda ctx, **kw: calls["lineage"].append(kw),
    )
    monkeypatch.setattr(
        projects_router, "_copy_bundle_dirs",
        lambda src, dst: calls["copy"].append((str(src), str(dst))),
    )
    return calls


def _run(loaded, name, *, rebind=False, dest="/tmp/dest", db=None, user=None):
    return _seam()(
        _Ctx(name), loaded=loaded, name=name, rebind=rebind,
        dest=pathlib.Path(dest), db=db, user=user,
    )


def test_the_seam_is_an_ordinary_function():
    assert not inspect.isgeneratorfunction(_seam())


def test_a_same_name_save_carries_nothing(spy):
    """
    Autosave and plain re-save. `loaded == name` means nothing moved, so a
    lineage transition would be a no-op at best and a self-move at worst.
    """
    _run("P", "P")
    assert spy["lineage"] == [] and spy["copy"] == []


def test_an_unbound_save_carries_nothing(spy):
    """First save of a fresh network — there is no source to carry from."""
    _run(None, "P")
    assert spy["lineage"] == [] and spy["copy"] == []


def test_rebind_moves_the_chat_thread(spy):
    _run("A", "B", rebind=True)
    assert len(spy["lineage"]) == 1
    kw = spy["lineage"][0]
    assert kw["mode"] == chat_service.SAVE_LINEAGE_REBIND_MOVE
    assert kw["target_name"] == "B"


def test_no_rebind_copies_the_chat_thread(spy):
    """
    Save-a-Copy / create_scenario. A move here would destroy A's history —
    the user asked for a copy and would lose the original's conversation.
    """
    _run("A", "B", rebind=False)
    assert spy["lineage"][0]["mode"] == chat_service.SAVE_LINEAGE_COPY


def test_the_source_name_is_passed_explicitly_not_left_to_the_context(spy):
    """
    The Phase 4 walkthrough bug. `ctx.loaded_project` is already the TARGET by
    now, so a helper left to infer its own source resolves src == dst and the
    move silently does nothing.
    """
    _run("A", "B", rebind=True)
    kw = spy["lineage"][0]
    assert kw.get("source_name") == "A", (
        f"source_name is {kw.get('source_name')!r}, not the PRE-rebind name — "
        f"the lineage helper will see src == dst and no-op"
    )


def test_uploads_are_copied_even_on_a_rebind(spy):
    """
    Always a COPY, both modes. Uploads are reference material, not a thread.
    """
    _run("A", "B", rebind=True)
    assert len(spy["copy"]) == 1
    _src, dst = spy["copy"][0]
    assert dst.endswith("dest")


def test_a_lineage_failure_does_not_abort_the_save(monkeypatch, spy):
    """
    The network is already on disk by the time this runs. Raising here would
    report failure for a save that succeeded.
    """
    def _boom(ctx, **kw):
        raise RuntimeError("lineage exploded")

    monkeypatch.setattr(chat_service, "handle_save_lineage", _boom)
    _run("A", "B", rebind=True)          # must not raise
    assert len(spy["copy"]) == 1, (
        "a lineage failure stopped the uploads copy; the two halves are "
        "independent best-effort steps"
    )


def test_a_copy_failure_does_not_abort_the_save_and_IS_logged(monkeypatch, caplog):
    """
    Also best-effort — but logged, unlike the lineage half. A silent uploads
    failure is how a user finds an empty uploads/ with no clue why.
    """
    import logging

    monkeypatch.setattr(chat_service, "handle_save_lineage", lambda ctx, **kw: None)

    def _boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(projects_router, "_copy_bundle_dirs", _boom)
    with caplog.at_level(logging.ERROR):
        _run("A", "B")                    # must not raise
    logged = "\n".join(r.getMessage() + (r.exc_text or "") for r in caplog.records)
    assert "disk full" in logged or "_copy_bundle_dirs" in logged, (
        "the uploads copy failed silently; nothing tells the user or the log why "
        f"uploads/ is empty. captured: {logged[:300]!r}"
    )


def test_the_source_dir_resolves_through_the_registry_in_auth_mode(monkeypatch, spy):
    """
    Storage is org-scoped, so the flat `_safe_project_dir(loaded)` is the wrong
    path once a db + user are in play — copying from it carries nothing, or
    another project's uploads.
    """
    monkeypatch.setattr(chat_service, "handle_save_lineage", lambda ctx, **kw: None)
    monkeypatch.setattr(projects_router, "_safe_project_dir",
                        lambda n: pathlib.Path("/flat/WRONG") / n)

    from services import project_registry

    monkeypatch.setattr(project_registry, "find_project",
                        lambda db, user, name: object())
    monkeypatch.setattr(project_registry, "project_dir",
                        lambda row: pathlib.Path("/org/abc/uuid-of-A"))

    _run("A", "B", db=object(), user=object())
    src, _dst = spy["copy"][0]
    assert src == "/org/abc/uuid-of-A", (
        f"copied from {src!r} — auth mode must resolve the SOURCE through the "
        f"registry, not the flat projects dir"
    )


def test_the_flat_path_is_still_used_without_a_db(monkeypatch, spy):
    """Legacy / desktop mode has no registry to consult."""
    monkeypatch.setattr(chat_service, "handle_save_lineage", lambda ctx, **kw: None)
    monkeypatch.setattr(projects_router, "_safe_project_dir",
                        lambda n: pathlib.Path("/flat") / n)
    _run("A", "B")
    assert spy["copy"][0][0] == "/flat/A"
