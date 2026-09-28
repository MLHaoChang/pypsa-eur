# `py/path-injection`: 48 → 5, and why the five stand

*2026-09-12. Branch `claude/set-api-key-continuation-2nxzqj`.*

CodeQL reported 48 `py/path-injection` alerts across this backend (65 alerts in
total). Four changes took that to 5 (23 total), all measured rather than
argued — each number below came from reading the SARIF in the analysis job's
own log, because the code-scanning API is not readable from the session that
did the work.

| after | `py/path-injection` | all rules |
|---|---|---|
| baseline | 48 | 65 |
| snapshot lookup by name | 40 | 57 |
| slug from a constant alphabet | 10 | 27 |
| template key + static file lookup | 6 | 23 |
| org id resolved to its row | 5 | 22 |

## The single finding underneath all of it

**CodeQL was not disagreeing with the guards. It could not see them.**

Every flow it reported ran *through* a correct check and out the other side.
Three barriers this codebase relies on are not modelled by
`codeql/python-queries`:

* `Path.is_relative_to` after `resolve()` — the containment check in
  `snapshots._safe_snapshot_dir` and `main.serve_spa`;
* a `re.sub` character allowlist — `snapshots._slugify_label`;
* a membership test against a known dict — `projects.create_from_template`.

The flow for the snapshot guard made this unmistakable:
`snapshots.py:118 -> :130 -> :136 -> <sink>` — 118 is the guard's own
signature, 130 is where it builds the path, 136 is where it returns it.

So the remedy was never to add a check. It was to stop *arguing* a value is
safe and instead *derive* it from something already safe:

* `iterdir()` for a directory that must already exist (snapshots, static files);
* a module constant for a generated name (the slug alphabet);
* the registry's own key, or the database row, instead of the caller's copy
  (templates, org ids).

That last one is the pattern `gridspine_service._authorized_dispatch_dir`
established in 859a7265, generalised.

## What is left, and why

### 4 alerts — a project or study name becomes its directory name

`routers/projects.py` rename (3) and `routers/gridspine.py` create-study (1),
both reaching `storage_paths.allocate_storage_path` →
`safe_names.safe_dir_name`.

**This is the product design, and it is documented where it lives.** E1 put the
user's own name on disk so the directory is findable in Finder. `safe_dir_name`
is a *denylist* — it strips `<>:"/\|?*` and control characters, defuses Windows
reserved device names, strips leading/trailing dots and spaces, caps the
component at 96 characters, and then **deliberately preserves everything else,
including non-ASCII**: its docstring says folding to ASCII "would make `Étude`
and `Etude` collide for no reason".

That is why the two tricks above do not apply here. There is no finite alphabet
to index into — the accepted set is all of Unicode minus a denylist — and the
directory does not exist yet, so there is nothing to enumerate and match.

Satisfying CodeQL here would mean dropping Unicode support in project names: a
real regression, traded for a finding that is not one. The value is a single
path *component* with every separator removed, joined under the projects root.

**Disposition: dismiss as "used in tests"/"won't fix" with this file as the
justification.** If it is ever revisited, the honest fix is a different
storage scheme (uuid directories plus a display name), not a narrower allowlist.

### 1 alert — the desktop folder importer

`routers/projects.import_projects_from_folder` takes `body.path`, expands it,
and reads the tree under it.

**The taint is the feature.** The route exists because a packaged desktop app
cannot otherwise reach a pre-desktop project tree, so the user names the folder
to import from. It is gated by `local_mode.reject_unless_local_mode` — desktop
only, one user, their own machine, their own files — and defaults to
`apply=false` with a dry run.

A path the user typed, read on the user's own machine, is not a traversal.

**Disposition: dismiss as "won't fix".**

## For whoever clears the Security tab

All five are `py/path-injection`. None is reachable as a traversal; each is
either a documented design decision or a value the analyser cannot see is
constrained. The 17 non-path-injection alerts are a separate question this work
did not touch — `py/clear-text-logging-sensitive-data` (7) is mostly the
`smoke/` repro scripts whose purpose is printing a key, and
`py/insecure-temporary-file` (4) is entirely test modules.

## Method note, for the next person

The alerts page and the check summary show only the **sink**. `py/path-injection`
is a path-problem query, so the alert is a source→sink flow, and the fix lives
at the source. Getting that required printing the SARIF from the analysis job
into its own log — a temporary workflow step, added and reverted twice here.
Nothing about it is clever; it is just the only readable channel when the
code-scanning API is not.

Two mistakes worth not repeating, both recorded in the commits that made them:

* A traversal test driven through the HTTP client tests **httpx**, not the
  server: it resolves `..` against the URL before sending, so
  `/api/projects/A/snapshots/../../B/snapshots/X/restore` leaves as
  `/api/projects/B/snapshots/X/restore` and answers 200 for an ordinary
  request. Test the helper.
* Rewriting `_slugify_label` nearly renamed every existing snapshot: the old
  pattern was `[^A-Za-z0-9_\-]+` and the `+` collapses a *run* to one dash.
  Directory names are ids clients hold. Compare implementations over a corpus,
  not against expectations written by whoever wrote the new code.
