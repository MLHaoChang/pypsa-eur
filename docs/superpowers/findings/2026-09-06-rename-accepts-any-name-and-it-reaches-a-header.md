# `POST /{name}/rename` accepts any name, and the name reaches a response header unescaped

**Date:** 2026-09-06
**Found while:** unblocking `tests/qa_rename_project.py`, whose "400 for invalid
characters (path traversal)" assertion now gets a `200`
**Status:** FIXED 2026-09-07 in **PR #7** (`claude/fix-download-filename-headers`,
branched off `master` so it does not ride along with the decomposition). See
"How it was actually fixed" at the bottom. Two things in the original write-up below were wrong, and are
corrected there: the affected surface is four routes, not one, and the
real-server behaviour is now verified rather than inferred.

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

---

# How it was actually fixed, 2026-09-07

## Correction 1: four routes, not one

The write-up above named the project bundle and said three other routes "build
`Content-Disposition` by f-string from a caller-influenced value". That
undersold it — three of those four are genuinely exploitable, and I had not
checked which:

| route | caller-controlled? | verdict |
|---|---|---|
| `routers/projects.py` bundle | project name | **vulnerable** |
| `routers/network.py::_xlsx_response` | component name, via 3 template routes | **vulnerable** |
| `routers/asset_results.py` | component name | safe — a strict `isalnum() or in "-_"` allow-list |
| `routers/uploads.py` blob | upload filename | safe in practice — written through `safe_upload_filename` |

The `_xlsx_response` one is the more reachable of the two. Component names are
created through `POST /api/network/loads` (and generators, links) with **no
character validation at all** — verified by creating loads named `ev"il`,
`a\nb` and `../esc`, all of which returned `201`. Then
`GET /api/network/loads/template?load_name=ev"il` returned

```
attachment; filename="load_ev"il_template.xlsx"
```

so the defect was one API call away, not gated behind a rename.

## Correction 2: the real-server behaviour, verified

The original said the newline case was "not verified" and guessed that h11
would reject it. Tested against a real uvicorn:

| name | over real uvicorn |
|---|---|
| `ev"il` | **sent verbatim** — `filename="ev"il.zip"`; a conforming parser reads `ev` |
| `a\nb` | `RuntimeError: Invalid HTTP header value.` mid-send; client gets **an empty reply** |

So the guess was right and the severity is what it looked like: **not response
splitting**. It is a correctness bug with two faces — a truncated filename for
quotes, and a download that simply breaks for control characters. Worth fixing,
not worth alarm.

## The fix — PR #7

`services/http_filenames.py::content_disposition()` builds an RFC 6266 value
for **any** input string:

```
attachment; filename="<ascii-safe>"; filename*=UTF-8''<percent-encoded>
```

Applied at all four sites — including the two that were already safe, because
"is this one safe?" should not be a question a reader has to re-answer per
route.

Only the header half of the two-part fix proposed above was done. Name
validation was deliberately NOT added: with the header encoded, a hostile name
can no longer break anything, so which names are legal becomes a product-policy
question (is `Q1/Q2 scenarios` a name a user may want?) rather than a security
one. That belongs to whoever owns the product, not to this fix.

**The output is byte-identical to the old f-string for any plain ASCII name** —
`filename*` is only added when it says something the fallback does not. That
property is itself a test, because the helper touches every download in the
product and a reshaped header for ordinary names would be a behaviour change
for every user rather than a fix for an edge case.

Verified: the helper against 20 hostile inputs; each route through the
TestClient; the previously-broken `a\nb` project bundle over a **real uvicorn**,
which now returns `200` with
`filename="a_b.pypsaproj.zip"; filename*=UTF-8''a%0Ab.pypsaproj.zip` and no
server error. Both call-site fixes were mutation-tested by reverting them and
confirming the route tests go red.

A static sweep (`test_no_download_route_builds_the_header_by_interpolation`)
guards the next download route somebody adds, because this defect is invisible
to the test suite: `TestClient` sends a malformed header perfectly happily and
only a real server refuses it.
