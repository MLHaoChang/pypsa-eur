# `POST /{name}/rename` accepts any name, and the name reaches a response header unescaped

**Date:** 2026-09-06
**Found while:** unblocking `tests/qa_rename_project.py`, whose "400 for invalid
characters (path traversal)" assertion now gets a `200`
**Status:** recorded, not fixed — this branch is a test-repair branch, and the
fix is a product change

## What the driver expected, and what happens

`tests/qa_rename_project.py` has asserted since it was written:

```python
r = client.post(f"/api/projects/{name}/rename", json={"new_name": "../escape"})
_step("400 for invalid characters", r.status_code in (400, 422), ...)
```

It returns **200**. `_rename_project_db` validates exactly three things —
non-empty after `.strip()`, different from the current name, not already taken
in the org — and nothing about the characters:

```python
new_name = req.new_name.strip()
if not new_name:                                   raise HTTPException(400, ...)
if new_name == old_name:                           raise HTTPException(400, ...)
if project_registry.find_project(db, user, new_name) is not None:
                                                   raise HTTPException(409, ...)
```

That is a deliberate-looking consequence of the tenancy migration: a project name
is a **database value** now, not a path segment. The old 400 was defending a path
join that no longer exists.

## The traversal itself is contained

Verified, not assumed. The only place a project name reaches the filesystem is
`storage_paths.allocate_storage_path` → `storage_path_for` → `safe_names.safe_dir_name`:

```
'../escape'   -> '_escape'
'..\\escape'  -> '_escape'
'a/b'         -> 'a_b'
'..'          -> 'project'
```

and in the mode this driver exercises the directory does not move on rename at
all — `_may_move_directory` permits that only in local mode, so the directory
keeps its original UUID-scoped path and only the row's `name` column changes.
The QA driver now asserts the property the old status code was defending: after
a rename to `../escape`, the project's resolved directory is still inside
`settings.projects_root`. It is.

**So this is not a path-traversal hole.** It is the next thing along.

## Where it does bite: the download filename

`GET /api/projects/{name}/bundle` puts the project name straight into a header:

```python
headers={"Content-Disposition": f'attachment; filename="{name}.pypsaproj.zip"'}
```

Renaming a project and then requesting its bundle, against the in-process
TestClient:

| project renamed to | resulting `Content-Disposition` |
|---|---|
| `../escape` | `attachment; filename="../escape.pypsaproj.zip"` |
| `ev"il` | `attachment; filename="ev"il.pypsaproj.zip"` |
| `a\nb` | `attachment; filename="a` ⏎ `b.pypsaproj.zip"` |

All three renames returned 200 and all three bundle requests returned 200.

Reading these in increasing order of seriousness:

1. **`../escape`** — a relative path in a `filename=` parameter. Browsers take
   the basename, so this is malformed rather than dangerous.
2. **`ev"il`** — the embedded quote closes the quoted-string early, so a
   conforming parser reads the filename as `ev`. Header-value confusion.
3. **`a\nb`** — a **raw newline inside a header value**. This is the one worth
   fixing. Be precise about the severity: over a real ASGI server this is very
   unlikely to become response splitting, because `h11` (under uvicorn)
   validates header values and raises on control characters — so the realistic
   outcome is that the bundle download 500s rather than that a response is
   split. It reached the header at all, though, which is the defect; the
   protection is currently the server's, not this code's.

Not verified here: what uvicorn actually does with it. The table above is from
the in-process TestClient, which does not run h11's header validation. Anyone
fixing this should confirm the real-server behaviour before writing the severity
into a changelog.

## Same shape, other routes

`Content-Disposition` is built by f-string from a caller-influenced value in at
least three more places:

* `routers/asset_results.py:71` — `filename="{fname}"`
* `routers/network.py:391` — `filename="{fname}"`
* `routers/projects.py:2882` — the one above

The rest of the `routers/io.py` downloads use fixed filenames and are fine.

## What a fix would be

Two independent halves, and the second is the one that matters:

1. **Validate the name at the route.** Reject control characters and quotes in
   `RenameProjectRequest` / `CreateProjectRequest` — a Pydantic validator, so
   create and rename cannot disagree. Path separators are a judgement call: they
   are harmless now, and `Q1/Q2 scenarios` is a name a user might reasonably
   want.
2. **Encode the header regardless.** A route must not depend on name validation
   for header safety. RFC 6266 gives the form —
   `filename="<ascii-safe>"; filename*=UTF-8''<percent-encoded>` — and a single
   shared `content_disposition(filename)` helper for all four call sites means
   the next download route added gets it for free.

Worth a test either way: rename to `a\nb`, request the bundle, assert the
response header carries no control character.
