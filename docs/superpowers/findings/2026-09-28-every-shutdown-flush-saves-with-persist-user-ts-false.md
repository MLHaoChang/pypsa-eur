# Every shutdown flush saves with `persist_user_ts=False`, so the open project's `user_ts.json` goes stale

**Date:** 2026-09-28
**Severity:** HIGH — silent loss of profile edits made since the last explicit save, on the desktop build's normal quit path.
**Status: OPEN, NOT FIXED.** Found while making the user time-series store
per-`ProjectContext`; it is a separate defect in the same gate and is left alone
deliberately (it needs its own call-site decisions and its own tests).
**Scope:** the desktop build's quit sequence. The server has no equivalent caller.
**Basis: source, not a reproduction.** Every link below was re-read in the tree
at `HEAD`; the end-to-end quit-then-reopen was NOT executed. The one step not
observed is named at the bottom.

## The chain

`desktop/gui.py:331` passes `active = _active_context()`, which is
`PyPSAService._active` read directly (`desktop/gui.py:365-368`) — deliberately,
to avoid `_ensure_active()`'s self-heal, which is itself a fix for an earlier
version of this same defect (`services/shutdown.py:107-122`).

`services/shutdown.py`'s `flush_all` then calls `save(ctx, ctx is active)` per
resident context, and `flush_all`'s own docstring states the asymmetry it is choosing
between: "True for everything stamps the foreground's series onto every project;
False for everything loses the active project's."

After Step 0b, `_active` is a BOOTSTRAP slot, not the open project.
`PyPSAService.adopt_process_foreground()` hands it to the first session that asks
and sets `cls._active = None`; nothing in the request path ever writes it back
(`_publish_active` writes the request-scoped context when one is bound). The
desktop app drives the backend over HTTP with a real session, so it adopts.
Therefore at quit time `active is None`, `ctx is active` is False for EVERY
resident context, and the flush takes the "False for everything" branch the
docstring calls out as losing the active project's series.

## What that costs

With `persist_user_ts=False`, `_save_context`:

* skips `_reapply_user_ts_to_network(n)` before the export, and
* skips writing `user_ts.json` entirely.

`network.nc` is still rewritten. So the freshly-uploaded profile survives in the
netCDF (the upload routes write the `_t` table as well as the store), while
`user_ts.json` on disk still holds the version from the last EXPLICIT save.

The next open then prefers the stale file: `load_project` does
`_restore_user_ts(json.loads(user_ts_path.read_text()))` and
`_reapply_user_ts_to_network(n)`, which writes
the stale series over the netCDF's newer `_t` values. A profile uploaded after
the last Ctrl+S and before quitting is reverted on reopen, with no error.

## Why it is now easy to fix, and was not before

The gate existed because `_serialize_user_ts()` read a PROCESS-GLOBAL store, so
`True` for a non-foreground context wrote the foreground's series into that
project's directory. That is no longer true: the store is per-`ProjectContext`
(`ProjectContext.user_ts`), and the save path passes the saved context's own
store. `ctx is active` has stopped being the question — the right value is `True`
for every context, because every context now serialises its own profiles.

Two things to settle before flipping it, which is why this is a separate change:

* `tests/test_chat_state_carry.py:156` and `tests/test_shutdown.py:1049` assert
  the current `False`; both would have to change, and changing a test that pins a
  behaviour is a decision, not a formality.
* `services/solve_queue.py:1319` passes the same predicate for queue saves and
  wants the same treatment; its dispatcher thread has no request context, so
  whether it should persist at all is a second question.

## The step not observed

That the reverted values are visible in the GUI after a quit-and-reopen. Every
other link is a source fact; this one is the user-visible consequence, inferred
from `load_project`'s restore-then-reapply order rather than watched.
