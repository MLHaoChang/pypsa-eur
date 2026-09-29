# Every shutdown flush saves with `persist_user_ts=False`, so the open project's `user_ts.json` goes stale

**Date:** 2026-09-28
**Severity:** HIGH — silent loss of profile edits made since the last explicit save, on the desktop build's normal quit path.
**Status: FIXED 2026-09-29.** The predicate is now
`project_context.holds_user_series(ctx)` — each context judged on what it holds —
and the `active` parameter was removed from `flush_all` along with
`desktop/gui.py::_active_context`, so the `PyPSAService._active` read that made
the old predicate wrong is gone from the desktop path entirely. The same obsolete
`persist_user_ts=False` was found at a SECOND call site,
`PyPSAService._save_evicted_ctx`, and fixed with it (see "A second call site"
below). Pinned by `pypsa-gui/backend/tests/test_shutdown_persists_user_ts.py`.
Found while making the user time-series store per-`ProjectContext`.
**Scope:** the desktop build's quit sequence AND the server's resident-cap
eviction. The original write-up said "the server has no equivalent caller" — that
was wrong, and the second call site is exactly where it was hiding.
**Basis:** the eviction half is REPRODUCED — `test_an_evicted_context_persists_its_series_too`
drives the real `_save_context` and asserts the sidecar on disk; before the fix
it wrote no `user_ts.json` at all. The shutdown half is source-derived: every
link below was re-read in the tree, but the end-to-end quit-then-reopen was not
executed, and the one step not observed is named at the bottom.

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

## Why it was fixable now and was not before

The gate existed because `_serialize_user_ts()` read a PROCESS-GLOBAL store, so
`True` for a non-foreground context wrote the foreground's series into that
project's directory. That is no longer true: the store is per-`ProjectContext`
(`ProjectContext.user_ts`), and the save path passes the saved context's own
store. `ctx is active` has stopped being the question — the right value is `True`
for every context, because every context now serialises its own profiles.

### Correction: which test actually pinned it

This section originally named `tests/test_chat_state_carry.py:156` and
`tests/test_shutdown.py:1049` as the tests that would have to change. Both were
wrong, and the error is worth keeping rather than quietly deleting, because it
is what a reader would otherwise repeat:

* `test_chat_state_carry.py:156` asserts `persist_user_ts is False` for
  `_save_evicted_ctx`, a DIFFERENT call site. It was not a blocker — it was a
  second instance of the same defect, hiding in plain sight in a test that
  looked like it was about chat flushing.
* `test_shutdown.py:1049` mentions `persist_user_ts` only in a DOCSTRING
  explaining a historical failure mode. It asserts nothing about it.

The single real pin was
`test_shutdown.py::test_the_active_context_persists_its_time_series_and_others_do_not`,
whose whole premise was the process-global store. It is now
`test_each_context_persists_its_own_time_series_or_none`, asserting the
constraint it always defended (a flush must not write one project's series into
another's file) under the predicate that now satisfies it.

### A second call site

`PyPSAService._save_evicted_ctx` passed `persist_user_ts=False` on the same
obsolete reasoning. It writes a context to disk immediately before the resident
cap makes it unreachable, so an eviction silently dropped that project's
uploaded profiles — with no user action involved at all, which makes it quieter
than the shutdown case rather than milder. Fixed with the same predicate; the
two are now one definition (`holds_user_series`) because a guard that differs
between its callers is not a guard.

### Why not simply `True`

Because `_save_context` UNLINKS `user_ts.json` when the store it serialises is
empty, and a context hydrated from disk HAS an empty store —
`_hydrate_context_from_disk` does not restore the sidecar (OPEN-ITEMS 13). So
`True` for everything would delete a good file for any project the user merely
opened, and after representative-week sampling would replace a full-year series
on disk with the 168-row `_t` view. That is the mutation the empty-store test
exists to catch, and it does.

### Still open

`services/solve_queue.py` passes `ctx is PyPSAService._active` for queue saves.
Deliberately unchanged: its dispatcher thread hydrates a context from disk, so
that context's store is empty for the reason above, and `holds_user_series`
would correctly answer False for it — but "should a background solve rewrite the
sidecar at all" is a different question from this one, and it becomes tractable
only once OPEN-ITEMS 13 lands.

## The step not observed

That the reverted values are visible in the GUI after a quit-and-reopen. Every
other link is a source fact; this one is the user-visible consequence, inferred
from `load_project`'s restore-then-reapply order rather than watched.
