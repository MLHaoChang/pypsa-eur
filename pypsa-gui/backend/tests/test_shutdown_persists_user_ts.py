"""
The shutdown flush must persist each context's OWN uploaded time series.

WHAT THIS PINS. `flush_all` decided `persist_user_ts` by `ctx is active`, where
`active` is `PyPSAService._active` read directly by `desktop/gui.py`. After
Step 0b that slot is a BOOTSTRAP slot: `adopt_process_foreground()` hands it to
the first session that asks and sets it to None, and nothing in the request path
writes it back. So at quit time `active` is None, `ctx is active` is False for
every resident context, and the flush takes the branch `flush_all`'s own
docstring calls "loses the active project's" series.

What that costs: `network.nc` is still rewritten, but `user_ts.json` is not — so
the next open restores the STALE sidecar and `_reapply_user_ts_to_network`
writes it back over the fresher `_t` tables. A profile uploaded after the last
explicit save is reverted on reopen, with no error.

The predicate that replaced it, `may_rewrite_user_ts`, answers yes unless the
context's sidecar could not be read when it was hydrated: the store is faithful
to disk otherwise, so rewriting keeps the file current. The second test below is
the unreadable half.

Written before the fix and proven red against it. See
`docs/superpowers/findings/2026-09-28-every-shutdown-flush-saves-with-persist-user-ts-false.md`.
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest

from services import shutdown as shutdown_service
from services.project_context import ProjectContext


SNAPS = pd.date_range("2025-01-01", periods=4, freq="h")
UPLOADED = 777.0


def _ctx(name: str, *, with_series: bool) -> ProjectContext:
    n = pypsa.Network()
    idx = SNAPS.copy()
    idx.name = "snapshot"
    n.set_snapshots(idx)
    n.add("Bus", "B1")
    n.add("Load", "L1", bus="B1", p_set=pd.Series([100.0] * len(idx), index=idx))
    ctx = ProjectContext(network=n)
    ctx.loaded_project = name
    if with_series:
        ctx.user_ts[("loads", "p_set", "L1")] = pd.Series(
            [UPLOADED] * len(SNAPS), index=SNAPS
        )
    return ctx


def _flush(contexts) -> list[tuple[str, bool]]:
    """
    Drive `flush_all` and report what `persist_user_ts` each context was saved
    with. There is no `active` argument to pass any more — the fix removed the
    parameter along with the predicate that used it, so a caller can no longer
    get it wrong.
    """
    saved: list[tuple[str, bool]] = []
    shutdown_service.flush_all(
        contexts=contexts,
        save=lambda c, persist: saved.append((c.loaded_project, persist)),
        flush_chat=lambda: None,
        safe=True,
    )
    return saved


def test_a_context_holding_uploads_is_flushed_with_its_series():
    """
    The defect, at the predicate. Before the fix this context was saved with
    `persist_user_ts=False`, because `desktop/gui.py` passed
    `PyPSAService._active` — None in a real quit, not as an edge case but as the
    normal case — and `ctx is active` was therefore False for everything.
    """
    open_project = _ctx("Open", with_series=True)

    assert _flush([open_project]) == [("Open", True)], (
        "the open project's uploaded series were dropped from its user_ts.json "
        "by the shutdown flush"
    )


def test_a_context_whose_sidecar_was_unreadable_does_not_rewrite_it():
    """
    The one refusal. A hydrate that could not read `user_ts.json` leaves the
    store empty; rewriting would replace the only copy of the file's contents
    with the `_t` view, so the flush leaves it alone.
    """
    unreadable = _ctx("Unreadable", with_series=False)
    unreadable.user_ts_unreadable = True

    assert _flush([unreadable]) == [("Unreadable", False)], (
        "a context whose sidecar could not be read rewrote it"
    )


def test_every_context_is_judged_on_its_own_series_not_on_being_active():
    """
    The old predicate answered False for all three (nothing was ever `active`
    in production). The shape that matters is that the answer tracks each
    context's OWN state, not one blessed context.
    """
    unreadable = _ctx("Unreadable", with_series=False)
    unreadable.user_ts_unreadable = True
    saved = _flush([
        _ctx("HasUploads", with_series=True),
        unreadable,
        _ctx("Empty", with_series=False),
    ])

    assert saved == [
        ("HasUploads", True), ("Unreadable", False), ("Empty", True),
    ], saved


def test_an_evicted_context_persists_its_series_too(tmp_projects_dir):
    """
    The SAME defect at a second call site. `PyPSAService._save_evicted_ctx`
    writes a context to disk just before the resident cap makes it unreachable,
    and it too passed `persist_user_ts=False` — so an eviction silently dropped
    that project's uploaded profiles, with no user action involved at all.

    Driven through the real `_save_context` rather than a stub, so this asserts
    the file on disk rather than the argument.
    """
    import json

    from services.pypsa_service import PyPSAService

    victim = _ctx("Victim", with_series=True)
    PyPSAService._save_evicted_ctx("org:victim", victim)

    sidecar = tmp_projects_dir / "Victim" / "user_ts.json"
    assert sidecar.exists(), "the evicted context wrote no user_ts.json at all"
    values = json.loads(sidecar.read_text())["loads"]["p_set"]["L1"]["values"]
    assert values == [UPLOADED] * len(SNAPS), (
        f"eviction dropped the victim's uploaded series (got {values!r})"
    )
