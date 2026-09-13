"""
Undo capture / restore path for the network router.

`services/undo_service.py` owns the per-project stack and coalesce window.
This module owns the netcdf + user-ts round-trip that feeds it:

  * ``push_undo_snapshot`` — middleware capture (main.py)
  * ``get_undo_info`` — GET /api/network/undo/info
  * ``apply_undo`` — POST /api/network/undo

Lifted out of ``routers/network.py`` after the profiles sibling (#33). The
router keeps thin FastAPI handlers and re-exports ``_push_undo_snapshot`` as
the identical ``push_undo_snapshot`` object so ``main.py`` and chat_tools stay
unchanged. Never imports ``routers.*``.
"""
from __future__ import annotations

from fastapi import HTTPException

from services import change_log_service
from services.pypsa_service import PyPSAService
from services.user_timeseries import (
    _backup_network_ts_to_user_ts,
    _reapply_user_ts_to_network,
    _restore_user_ts,
    _serialize_user_ts,
)


def push_undo_snapshot() -> None:
    """
    Capture current network + user-ts state and push onto the undo stack.

    Called by the HTTP middleware in main.py before every mutating request so
    that the state *before* each change is always restorable. The middleware
    runs us in a worker thread to keep the event loop responsive, so we must
    hold the PyPSA lock for the duration of the export — otherwise a
    concurrent mutation could rewrite the network mid-snapshot.

    PyPSA ≥ 1.0's export_to_netcdf requires a real file path (its context
    manager's __exit__ calls Path(self.path) which rejects BytesIO with a
    TypeError). We write to a temp file and read the bytes back.
    """
    import logging as _logging
    import pathlib
    import tempfile

    from services import undo_service
    try:
        with PyPSAService.get_lock():
            n = PyPSAService.get_network()
            _backup_network_ts_to_user_ts(n)
            with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
                tmp = pathlib.Path(f.name)
            try:
                with PyPSAService.get_netcdf_io_lock():
                    PyPSAService.export_network_to_netcdf(n, tmp)
                netcdf_bytes = tmp.read_bytes()
            finally:
                tmp.unlink(missing_ok=True)
            user_ts_payload = _serialize_user_ts()
        undo_service.push(netcdf_bytes, user_ts_payload)
    except Exception as exc:
        # Log instead of swallowing silently so future regressions are visible.
        _logging.getLogger(__name__).warning("undo snapshot skipped: %s", exc)


def get_undo_info():
    """
    Return undo-stack telemetry: depth + memory usage.

    `memory_bytes` / `max_bytes` let the frontend surface a "Undo memory:
    X / Y MB" hint when the stack is approaching the byte budget — useful
    on multi-period sector-coupled networks where each snapshot is large
    enough that the byte-eviction path can trim deep undo history
    invisibly otherwise.
    """
    from services import dirty_state, undo_service
    return {
        "depth": undo_service.depth(),
        # NOT `depth > 0`. Undo depth answers "how many undoable edits since the
        # last save"; this answers "does memory differ from disk". A solved
        # project has depth 0 and unsaved True, which is the whole reason this
        # field exists — see the 2026-08-27 unsaved-results spec.
        "unsaved": dirty_state.is_dirty() or undo_service.depth() > 0,
        "memory_bytes": undo_service.memory_bytes(),
        "max_bytes": undo_service.MAX_BYTES,
        "max_steps": undo_service.MAX_STEPS,
    }


def apply_undo():
    """Restore the network to the state before the most recent mutating operation."""
    import pathlib
    import tempfile

    from services import undo_service

    # ★ Precheck BEFORE the destructive pop (Phase 11 review, BLOCKER 1).
    # `undo_service.pop()` removes the entry from the stack and returns it; a
    # 409 raised after it discards that entry, so two refused Ctrl-Z presses
    # during a study emptied a two-deep undo stack while changing nothing.
    # /api/network/undo is in `_UNDO_EXCLUDE`, so the middleware's
    # push-then-rollback does not cover it either.
    PyPSAService.refuse_if_study_running("undo")
    result = undo_service.pop()
    if result is None:
        raise HTTPException(409, "Nothing to undo")

    netcdf_bytes, user_ts_data = result
    # PyPSA ≥ 1.0 import_from_netcdf also requires a path — round-trip via tempfile.
    with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
        tmp = pathlib.Path(f.name)
        f.write(netcdf_bytes)
    try:
        with PyPSAService.get_lock():
            # Undo is an in-place edit of the CURRENT project (not a project
            # switch), so identity must survive it. `reset_network()` clears
            # the binding to None; capture it first and restore it after the
            # re-import, all inside the lock, so a concurrent save never sees
            # the current project momentarily unbound (which would let its
            # `expect` guard fall through and its claim rebind wrongly).
            # Capture the WHOLE binding, not just the name: `reset_network()`
            # drops the tenant identity too, and restoring the name alone would
            # leave the ctx keyed by name in the resident registry — the
            # cross-org collision Step 0a removed.
            prev_binding = PyPSAService.get_binding()
            prev_loaded = prev_binding["name"]
            PyPSAService.reset_network()
            n = PyPSAService.get_network()
            with PyPSAService.get_netcdf_io_lock():
                PyPSAService.import_network_from_netcdf(n, tmp)
            PyPSAService.set_binding(prev_binding)
            if prev_loaded:
                try:
                    n.name = prev_loaded
                except Exception:
                    pass
    finally:
        tmp.unlink(missing_ok=True)

    _restore_user_ts(user_ts_data)
    n = PyPSAService.get_network()
    _reapply_user_ts_to_network(n)
    change_log_service.log("undo", "Network", "", "Undone last action")
    return {"undone": True, "remaining": undo_service.depth()}


# Alias for the middleware / facade identity pin.
_push_undo_snapshot = push_undo_snapshot
