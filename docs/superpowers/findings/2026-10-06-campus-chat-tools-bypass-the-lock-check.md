# The campus-electrical chat tools bypass the edit-lock check

**Date:** 2026-10-06
**Severity:** HIGH: a cross-user write to a project's campus study, including
an overwrite of the holder's edited campus file.
**Status: FIXED** on 2026-10-07 by `_gridspine_project_for_write` in
`services/chat_tools.py`, which all four write tools now use. It was recorded
first, while it kept growing (#79 added two tools, #84 one, #91 one), and fixed
afterwards on the owner's instruction. Tripwire:
`tests/test_campus_tools_foreign_lock.py`. The reproductions below are the
pre-fix behaviour and are kept verbatim as the evidence.
**Scope:** the multi-user server only.

## The mechanism

`routers/campus_electrical.py` checks the lock in each write handler, then
calls the service:

```python
@router.post("/{name}/draft")
async def draft(body, proj = ProjectAccessDep, db = ..., user = ...):
    _check_lock(proj, db, user)                   # -> _check_project_lock
    return await run_in_threadpool(ce.draft, _row(proj, db), body.overwrite)
```

The two chat tools call the **service** directly and never reach the handler:

```python
def campus_draft_campus(project_id, overwrite=False):
    with _acting() as (db, user):
        return _h(_gridspine_project(db, user, project_id), bool(overwrite))   # ce.draft
```

`_gridspine_project` is just `project_registry.resolve_project`, which checks
access (ACL) but not the lock. Neither tool is in the chat seam's gated set.
So under a foreign lock:

* `campus_draft_campus(overwrite=True)` rewrites `campus_input.yaml` and
  `draft_notes.json`, replacing a campus file the holder edited and saved
  through `PUT .../campus`;
* `campus_run_study(...)` writes `settings.json` and the engine run directory.

`campus_get_study` is a read and is correctly left alone.

**A third tool, added the same afternoon.** #84 (campus grid codes) added
`campus_extract_grid_code`, which has the same shape. `POST
/{name}/grid-codes/documents/{id}/extract` runs `_check_lock` and then
`campus_grid_code_service.extract`, while the tool calls `extract` directly.
`extract` writes a draft (`<profile>.yaml` plus `<profile>.review.json`) into
the project's drafts directory. The tool always passes the default
`overwrite=False`, so it can ADD a draft under a foreign lock but cannot
replace one. That makes it less severe than `campus_draft_campus`, but it is
the same bypass. The write-surface test caught it when master (with #84) was
merged into #80. Reproduced the same way: HTTP extract returns 409 for a
non-holder, and the tool reached `gc.extract('doc1')`.

## Reproduced

Against master `af69613`. The acting chat identity is a same-org user who
does **not** hold the lock. The service functions were replaced with
recorders, because a real draft needs a solved hub network. This shows the
authorization bypass, not a full draft.

```
control  HTTP POST /api/campus-electrical/<p>/draft {"overwrite": true}
         -> 409 project_locked, service not reached
subject  campus_draft_campus(project_id=<p>, overwrite=True) -> reached ce.draft(overwrite=True)
subject  campus_run_study(project_id=<p>)                    -> reached ce.run({'pf': None})
```

**A fourth tool, a day later.** #91 (campus investment, a per-project asset
library) added `campus_set_library`. `PUT /{name}/library` runs `_check_lock`
before `campus_electrical_service.save_library`, while the tool calls
`save_library` directly. `save_library` replaces the project's asset library
wholesale, so this one is a destructive overwrite, like `campus_draft_campus`.
The write-surface test caught it when master (with #91) was merged into #80 on
2026-10-07. Reproduced the same way: for a non-holder, HTTP PUT returned 409,
and the tool reached `save_library` with the intruder's YAML.

## Why it keeps happening

This is the fifth occurrence of one shape (now with four tools in this item alone) (items 12 and 13, the stress and
worksheet PUTs in `68e5f62`, and now this one). A REST handler is the only
place that checks the lock, and a second caller of the same service skips
the handler. The chat tools are that second caller every time. #76's
`e36c041` gated the item 13 tools one by one. It could not have covered
these, which did not exist yet. Gating per tool will keep missing new tools,
and that is why the write-surface test exists: it caught this one on the day
it landed.

## The fix (applied 2026-10-07)

Smallest: add both tools to the chat seam's gated set (`_check_foreign_lock`
on the active project). One caveat applies. These tools take a `project_id`,
not the active project, so the seam's predicate may test the wrong project's
lock, the same reason `main.py` exempts `POST /api/simulation/queue`. The
correct fix checks the lock of the project the tool actually resolves:
call `_check_project_lock` on it in the tool, or route the tool through the
handler with `_route`.

## Fix criteria

* Under a foreign lock on the project named by `project_id`, all four tools raise
  409 `project_locked`, in the same run as the HTTP control.
* The holder's own calls succeed.
* A test asserts that `campus_input.yaml` is unchanged after a refused
  `campus_draft_campus(overwrite=True)` (the property), not only that a 409 was
  raised.
* The four `KNOWN_GAPS` entries in `tests/test_write_surface_lock_policy.py`
  are deleted. That test fails until they are.
