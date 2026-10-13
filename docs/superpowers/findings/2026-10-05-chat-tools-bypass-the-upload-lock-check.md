# Chat tools that write a project's upload store bypass its lock check

**Date:** 2026-10-05
**Severity:** HIGH: cross-user write to a bundled project store. One tool deletes, six add.
**Status: FIXED** by #76 (`e36c041`), merged 2026-10-07, not by this work. It was recorded here first, and the write-surface test's `KNOWN_GAPS` ratchet confirmed the fix on merge. The reproduction below is the pre-fix behaviour.
**Scope:** the multi-user server only. Local mode has one identity, so there is
never a foreign holder.

## The mechanism

The HTTP upload routes check the edit lock:

| route | handler | check |
| --- | --- | --- |
| `POST /api/projects/{name}/uploads` | `post_upload` | `_check_project_lock` |
| `DELETE /api/projects/{name}/uploads/{file_id}` | `delete_upload_route` | `_check_project_lock` |

Seven chat tools write the same store, `upload_service` against the active
project, without going through either route and without a check of their own:

* `clear_uploads` (destructive tier) calls `upload_service.delete_upload` for
  every upload of the active project.
* `export_to_excel`, `export_to_csv`, `export_preview_png`,
  `export_chat_summary`, `export_eh_report_docx` and `export_asset_results`
  (write tier) call `_save_agent_export` → `upload_service.add_upload(...,
  kind="agent_export")`.

They are not caught at the chat seam either. `_lock_gated_tool_names()` gates
a tool when it maps to a write route under the middleware's prefixes, or when
it is on `_LOCK_GATE_SERVICE_CALL_MUTATORS`. All seven are routeless
(`_service_call_`), and that list holds only tools that mutate the **network**.

## Why it was missed: a justification that went stale

`_lock_gated_tool_names`'s docstring states the exclusion explicitly:

> Upload / export / chat-history tools write artifacts, not network state, on
> surfaces the middleware does not gate either.

The second half is no longer true for uploads. The HTTP upload routes gained
in-handler lock checks; `routers/uploads.py` carries a comment saying its case
"was missed by the sweep that added lock enforcement elsewhere in this router
family". The tools' exclusion rested on parity with an HTTP surface that has
since changed, and nothing tied the two together. This is OPEN-ITEMS item 6's
shape on a second list: a hand-maintained set that denies by omission, so a
change elsewhere reopens a hole without anything failing.

## Reproduced

Against `24ed0b8`, with the acting chat identity a same-org user who does
**not** hold the lock (another user does), on a project carrying one upload
the holder added:

```
control  HTTP DELETE /api/projects/<p>/uploads/<id>  -> 409 project_locked
subject  chat tool clear_uploads()                   -> {'cleared': 1, 'files': ['holder-data.csv']}
uploads  ['8093ef1bc11e0473'] -> []
```

```
control  HTTP POST /api/projects/<p>/uploads         -> 409 project_locked
subject  chat tool export_to_csv(...)                -> {'file_id': 'ea14f99c47575613', 'kind': 'agent_export'}
uploads  1 -> 2
```

Each control is the same identity making the same kind of write to the same
project through HTTP, refused in the same run. That is what shows the lock was
live and the tools went around it, rather than the test being mis-set-up. The
other five export tools share `_save_agent_export` and were verified from
source, not separately reproduced.

## Why it matters

* `uploads/` is in `routers/projects._BUNDLE_DIRS`, so both writes travel into
  bundle exports, Save-As, scenario copies and snapshots.
* `clear_uploads` is destructive: the holder's files are gone. Its
  destructive-tier confirmation is given by the **caller**, which protects the
  caller from themselves and does nothing for the holder.
* The exports are additive and smaller. They do use up the holder's project
  upload quota, though, and the HTTP surface refuses the same addition
  outright, so the two surfaces disagree about one store.

## The fix (not applied)

The smallest consistent fix gates these seven tools at the chat seam with the
foreign-lock predicate the seam already has (`_check_foreign_lock`). That makes
them refuse exactly when the HTTP upload routes would. Two things to decide
while doing it:

* `_LOCK_GATE_SERVICE_CALL_MUTATORS` is named and documented for **network**
  mutators. Adding upload writers to it would make the name lie, so a second
  named set, or one set renamed to what it actually gates, is better.
* The `_lock_gated_tool_names` docstring line quoted above must change in the
  same commit. It is the stale claim that let this through.

## Fix criteria

* Each of the seven tools raises 409 `project_locked` for a non-holder, in the
  same run as an HTTP control showing the lock is live.
* The holder's own calls succeed.
* A test asserts the upload store is **unchanged** after the refused call (the
  property), not only that a 409 was raised.
* The seven `KNOWN_GAPS` entries in `tests/test_write_surface_lock_policy.py`
  are deleted. That test fails until they are, by design.
