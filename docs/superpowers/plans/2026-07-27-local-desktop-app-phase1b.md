# Local Desktop App — Phase 1b Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Give the desktop app a storage layout a human can navigate in Finder/Explorer, and import the projects already on this machine into it — without ever putting the only copy of a project at risk.

**Architecture:** Spec workstreams **E** (human-readable, relative, atomic storage) and **F** (a staged, verified, resumable importer). Task order is safety-driven, not alphabetical: the route that can `shutil.move` a live project is closed **first**.

**Tech Stack:** FastAPI, SQLAlchemy 2.x + Alembic, SQLite (WAL), pytest. No new dependencies.

**Builds on:** phase 1a, tasks 0–15, landed on `feature/local-app-impl`.

---

## Execution log — phase 1b COMPLETE (2026-07-27)

All ten tasks landed on `feature/local-app-impl`. Backend **64 files / 1251
tests → 73 files / 1413 tests**, all green; frontend unchanged at **23 / 147**,
as predicted (the frontend already read 404 as "nothing to import").

| Task | Commit | Tests |
|---|---|---|
| 1 close `/unclaimed` locally | `f0970d7e` | 6 |
| 2 portable directory names | `50e4bdd2` | 51 |
| 3 split resolve from materialise | `6a7ca9cc` | 7 |
| 4 relative paths + 0003 | `becca2d8` | 25 |
| 5 atomic writes | `db4b2fd6` | 14 |
| 6 reconcile | `ab7c8750` | 13 |
| 7 inventory + import | `6f43e93f` | 38 |
| 8 first-run wiring | `977f105d` | 8 |

**What the RED runs proved, rather than described.** Task 1's failing test
returned **201** from `POST /api/projects/unclaimed/Old Study/import` in local
mode — the `shutil.move` hazard, executed. Task 3's guard found exactly the
eight `storage_path` readers the plan predicted, by name and line.

**Six deviations from the plan, each recorded in its commit:**

1. `routers/solve_queue.py:56` stays on `project_dir`, not `ensure_project_dir`
   — it 404s when `network.nc` is absent, so mkdir-ing first would resurrect a
   deleted project's directory, which is what Task 3 exists to stop.
2. `_taken_names` / `_org_segment` are public: `taken_names` and
   `use_org_segment`. The latter renamed to avoid shadowing the `org_segment`
   keyword every function here takes.
3. Migration 0003's test database is built by running alembic from 0001, not
   `create_all` + `stamp`. `create_all` emits the current model, which already
   carries the unique constraint 0003 adds, so a database built that way
   cannot hold the colliding rows the abort test needs.
4. A foreign directory in the import destination is stepped **around**, not
   refused: `taken_names` unions the filesystem, so the project lands beside it
   as `… (2)`. Same reasoning as rename. The explicit `exists()` check remains
   as the race guard and is tested with a stale `taken` set.
5. A dry run consults the receipts, so the rehearsal tells the truth on a
   second run.
6. E5-partial stands: `snapshots.py:294,361,364` remain plain `copy2`. They
   write into a directory created `exist_ok=False` five lines earlier.

**Rehearsal + acceptance, against a COPY of the real tree.** A real uvicorn on
port 8125 with a fresh app-data directory, first run:

- `auth_enabled:false`; `/api/projects/` lists **12**; 12 readable top-level
  directories, no org segment
- `Belgium Grid` opens: **10 buses**, 6 lines, 33 generators, 7 snapshots, real
  Belgian coordinates — the copies are valid netCDF, not just present
- both real lineage chains reconstructed; the one dangling parent
  (`4_nodes_N-1` → `test_project_4_nodes2`) imported as a root **with a
  warning**, not refused
- restart → still 12 projects, 12 directories: idempotent in a live app
- `/api/projects/unclaimed`, `/api/admin/legacy-projects`,
  `/api/admin/organizations` all **404**; no `X-PyPSA-Replica`
- deep links 200, assets 200, `PUT /layout` 200 with no cookie and no CSRF
- `--rollback` removed 12 rows and 12 directories, source untouched
- **the real tree is byte-identical to Task 0's `shasum` manifest**, verified
  after the rehearsal and again after acceptance
- `~/Library/Application Support/PyPSA GUI/` never created

**Six review rounds, four of them REJECT.** The findings are recorded in the
commits that fixed them; the shape worth carrying forward is that *every fix
made without a failing test first became the next round's finding*. Three of
the five defects in round six were introduced by round five's remediation, and
the case-only-rename defect was caught by the live e2e rather than the suite —
on a case-insensitive filesystem the broken version passes every assertion that
only checks "the project still opens".

**Residual risks accepted rather than fixed:**

- **The idempotence key is unstable across a Documents relocation.** Both the
  receipt and the ledger key on `str(dest_root)`, which is `.resolve()`d.
  Turning on macOS *Desktop & Documents in iCloud* changes that string, so
  every receipt stops matching and the legacy tree re-imports as `Name (2)`.
  `source_root_str` fixed this class of problem on the SOURCE side; the
  destination side carries the same exposure.
- **A sub-millisecond window where the ledger misses a project.** A kill
  between the row commit and the manifest rewrite leaves a project imported but
  unrecorded. The next run recognises it by receipt and still never adds it, so
  a later user delete resurrects it once. Too small to chase, worth knowing.
- **`os.replace` on a case-only DIRECTORY rename is unverified on Windows** —
  it is `MoveFileExW` with a flag Microsoft documents as unusable on
  directories. The failure path is correct if it raises, so this is
  portability, not data.

**Still open, deliberately.** `pypsa-gui/backend/projects/` is still in the
checkout: `--forget-legacy` is a user action and Task 9 does not run it, so
`git clean -xdf` can still delete 113 MB. Task 0's verified tarball is at
`~/pypsa-phase1b-backup-20260727T213326/` (80 files, `shasum` manifest) and the
path is in `~/.pypsa-phase1b-backup-path`. `smoke/qa_e2e.py:289,639` hardcode
`BACKEND_DIR / "projects"` and will self-skip once the tree is retired; they
are not in the pytest gate, so nothing goes red — which is why it is written
down.

The importable count is **13, not 12**: the final review found that the
org-scoped tree holds a real solved 3-bus, 8760-snapshot network with its chat,
layout and time series, and that NOTHING in the phase imported it — it was
classified `org-scoped tree`, dropped, and not even reported, while
`--rebase-db` only walks database rows and a fresh install has none. The
importer now descends exactly one level into an org tree. That project has no
display name (it lived in the database the tree was detached from), so it
arrives as `Imported project <8 hex>` for the user to rename. `--rebase-db` has not been run against `auth_dev.db`, so
`3_nodes_system` is still absolute into the checkout.

---

## Revision log

**v4 (2026-07-27)** — fourth independent review; v3 was **REJECT**ed with 5 blocking findings, all narrow. Each re-verified before applying.

| # | v3 defect | v4 |
|---|---|---|
| 1 | **Task 4 could not reach a green gate.** Promoting `legacy_migrate.py:243` to a third production caller means the tests that assert the layout *it produces* also break, and neither was listed: `tests/test_unclaimed_import.py:217-224` builds `projects_root/<org>/<project_id>` and asserts `project.storage_path == str(destination)`; `tests/test_legacy_migrate.py:155` asserts the same shape. Both run with auth on and no local mode, so Task 1 does not shield them. | Both added to Task 4's Files list, with the arguments `legacy_migrate.py:243` must pass — including that `taken` **accumulates across the claim loop**, or two legacy names that sanitise alike target one directory. |
| 2 | **"install id" was load-bearing and undefined.** The two obvious readings are both broken: `LOCAL_ORG_ID`/`LOCAL_USER_ID` are fixed constants shared by every install (`local_mode.py:25-26`), making the term a no-op; a per-process `uuid4()` makes Task 8 re-import on every launch, duplicating 113 MB each time. | Defined: a `uuid4` persisted in `<app_data_dir()>/install.json`, created by Task 7's `install_id()` (**superseded** — see Task 7 rule 6; an earlier draft placed it in Task 8, which lands too late). Absent file ⇒ new install. |
| 3 | **A new project could silently adopt and overwrite an orphan directory.** `_taken_names` was DB-only by design, so `unique_dir_name` can hand a new project the name of an existing directory that has a `network.nc` in it — and `routers/projects.py:1250` (`mkdir(exist_ok=True)`) then `:1363` (`_atomic_write_with(nc_path, …)`) replaces it. **New to this phase**: impossible while paths are `<org>/<uuid>`. Orphans are not hypothetical — Task 6 exists because of them, and Rollback tells the user to `tar xzf` directories back into place. | `taken` is the DB set **union the filesystem listing**. Reserving an orphan's name costs one suffix; adopting it costs the user's data. |
| 4 | **The uniqueness backstop covered 2 of 4 write paths.** It was scoped to `create_root`/`create_scenario`; the rename path got only `if new_dir.exists()`, which is False for a row whose directory the user deleted in Finder (Task 6's `missing_dirs`), so a rename could be assigned another row's `storage_path`. The importer got nothing. Step 4's snippet elided the `taken=` argument that *is* the fix for v3 row 1. And there is no DB constraint behind any of it (`db/models.py:43` constrains `("org_id","name")` only). | A **unique index on `("org_id","storage_path")` in migration 0003** — the only atomic mechanism, and 0003 is already being written. Plus the full `taken=` argument spelled out, and the assertion applied on rename and in the importer. |
| 5 | **`--rebase-db` moved rather than copied the only DB-tracked project**, contradicting the plan's own "the importer copies, never moves", claimed a filesystem move and a DB commit were "the same transaction", and had no test. | Copy → verify (same manifest + SHA-256 as rule 2) → rewrite the row → leave the source for `--forget-legacy`. Ordering and compensation specified; three test cases required. |

MINOR fixes applied: the deviation list now includes **E2-partial** (0003 does not reach the one existing row) and **E5-partial** (the three `snapshots.py` sites are cosmetic); the Task 3 guard test flags **any** `storage_path` attribute access outside the resolver, not only ones wrapped in `Path(...)` — `project_registry.py:146` (`ctx.storage_dir = str(project.storage_path)`) is constraint #5 and the old regex could not see it, though its value flows into `Path(...)` at `chat_service.py:758`, `upload_service.py:154`, `pypsa_service.py:696`, `solve_queue.py:368`; `tests/conftest.py` pops `PYPSAGUI_LEGACY_IMPORT_ROOT` at import (its own comment: "Pinning only one moves the problem to the others"); Task 7's rehearsal runs `tools.bootstrap_local` first, since a fresh DB has no schema and no identity; the receipt gets a schema and an explicit **lookup-before-allocate** order; `scan` skips `*.importing`; `--forget-legacy` defaults to a destination **outside the checkout** (renaming to `backend/projects.imported-<date>` leaves 113 MB untracked *and un-ignored*, so `git status` goes permanently dirty and a plain `git clean -df` deletes it); `_org_segment()` is defined once as `not local_mode.is_local_mode()` with a test asserting it and Task 1's gate agree; the backup pointer moves off `/tmp` (macOS clears it on reboot); constraint #17's ignore file is `pypsa-gui/.gitignore:23`.

An earlier draft of this paragraph also claimed `_scan_root`'s filter would skip any directory a `Project.storage_path` resolves to, "demoting Task 1 from sole guard to belt-and-braces". **That claim is withdrawn** — no task implemented it, and it contradicted Task 4's own note. Adding it would mean threading a `db` session through `_scan_legacy_entries` (`legacy_migrate.py:120-135`, which has none today) into `routers/projects.py:678` and `routers/admin.py:230`. Task 1 alone does close the hazard; that it is the *only* layer is now recorded under Residual risk rather than papered over.

**v3 (2026-07-27)** — third independent review; v2 was **REJECT**ed with 10 blocking findings. Each re-verified before applying.

| # | v2 defect | v3 |
|---|---|---|
| 1 | **`_taken_names` could map two rows onto one directory.** v2 said it "reads sibling names from the DB" — read as `Project.name`, a project named `Study:1` takes directory `Study_1`, and a later project literally named `Study_1` finds no collision and is assigned **the same directory**. Both rows then resolve to one path and each save clobbers the other. Nothing validates the charset on the rename path (`routers/projects.py:2191-2212`) and there is no uniqueness constraint on `storage_path`. | Specified as `Path(row.storage_path).name` — the **directory** name, never the display name — plus a pre-commit assertion that no two rows in an org share a `storage_path`, and a test using a name whose sanitised form equals another row's directory. |
| 2 | **Task order created the window it forbade.** v2's self-review said "Task 6 → any running local install with Task 3's layout", then listed Task 6 sixth. Between Task 3 and Task 6 a local install has projects at the top of `projects_root` **and** `/unclaimed` live — and `frontend/src/api/projects.ts:87,117` actively polls it and exposes `importUnclaimed`. | Closing `/unclaimed` is now **Task 1**, immediately after the backup. It is fully independent and can land today. |
| 3 | **Task 3 could not reach a green gate.** Eight test call sites pass `storage_path_for` two arguments (`test_project_locks.py:105`; `test_projects_tenancy.py:107,245,383,505,522`; `test_project_acl.py:84,122`), and `test_project_acl.py:122` asserts the old absolute shape. Worse, **`tests/conftest.py:418`** is a **seventh** direct `Path(row.storage_path)` read that v2's constraint #3 omitted and its guard test deliberately could not see (it scanned only `services/`, `routers/`, `tools/`). 19 usages depend on that fixture. | All nine sites enumerated in the task's file list; `conftest.py:418` routed through the resolver; constraint #3 corrected to "six in production **plus `tests/conftest.py:418`**". |
| 4 | **Task 7 rule 11 was forbidden by Task 7 step 4.** Rule 11 promised to relocate the org tree and rewrite `3_nodes_system`'s row — "what actually delivers E2 for the only row that exists". That row lives only in `backend/auth_dev.db`, and step 4 makes `--apply` **refuse** when `DATABASE_URL` resolves inside the checkout, which is exactly where it lives. | Split into a separate, separately-gated `--rebase-db` mode with its own confirmation. E2's limits are stated plainly instead of over-claimed. |
| 5 | **`PYPSAGUI_LEGACY_IMPORT_ROOT` would never bind.** `Settings` declares no `env_prefix` (`settings.py:12`), so a `legacy_import_root` field binds `LEGACY_IMPORT_ROOT`. The `PYPSAGUI_*` variables that do work (`app_paths.py:23,42`) are read from `os.environ` directly, not through pydantic. v2's rehearsal and acceptance steps both set the prefixed name, so they would have inventoried the wrong root. Its **default** was also never stated, so Task 8 would never fire for a real user — an undeclared F1 deviation. | `validation_alias="PYPSAGUI_LEGACY_IMPORT_ROOT"`, default stated explicitly, and listed in the deviation table. |
| 6 | **Staging lost a project permanently on a crash between `os.rename` and the row insert** — a populated destination with no row and no manifest entry, which the next run classifies as a foreign collision and skips forever. Also: `os.rename` onto an existing empty directory succeeds on POSIX but raises on Windows (D1 targets both), and the exists-check was TOCTOU. | A per-project **receipt written inside the staging directory before the rename**, so the marker moves atomically with the data. A destination carrying a matching receipt is "copied, needs row", not a collision. Windows/POSIX divergence documented. |
| 7 | **A source-side manifest suppresses the import on a second machine.** This repo is developed on Windows *and* macOS and CLAUDE.md documents OneDrive-synced checkouts. Import on machine A writes the manifest into the synced tree; machine B sees a matching manifest for a matching source root and imports **nothing, silently**. | Idempotence keys on (source root, **destination** root + install id). A manifest whose destination is not this install's means "not imported here". `--apply` refuses when the manifest cannot be written. |
| 8 | **The rename orphaned the project on the destination-exists path** — the row was committed, then `raise HTTPException(409)` with no compensating write-back, leaving the row pointing at a directory that never moved. The `EXDEV` handling also contradicted itself: the comment promised a `shutil.move` fallback, the code rolled back and re-raised. | Destination-exists is checked **before** the commit; `except OSError` dispatches on `errno.EXDEV` to `shutil.move` before compensating. |
| 9 | **Task 4's headline test was a tautology** — it wrote `network.nc.tmp` and asserted that file existed, exercising no production code. This is the exact defect v2's own revision log claimed to have fixed. | Parametrised over `_BUNDLE_FILES`, asserting `atomic_write_with`'s tmp name equals what the detector at `routers/projects.py:546-548` looks for. |
| 10 | **No quiescence precondition.** `projects/860edcb4-…/layout.json` was written at 17:36 today and a vite dev server is running — so a backend session between Task 0's checksum manifest and Task 9's `diff` yields a false alarm on the phase's only real safety check, and a concurrent autosave during a copy can tear a staged project. | Task 0 asserts no API listener and no uvicorn process, re-asserted before the rehearsal and the final diff. |

MINOR fixes applied: constraint #1 reworded (SQLite does **not** enforce `String(64)`; only `RenameProjectRequest` caps it, `models/schemas.py:530`); the legacy-tree count no longer double-counts (13 directories **including** the org tree, plus one `.zip`); `routers/projects.py:577` moved out of the `project_dir`-callers table (it is a direct read, not a caller); Task 2's case count dropped rather than guessed; Task 1 uses **per-route** dependencies, since `projects.router` must stay mounted; `PUT /layout`'s 404 decided rather than deferred; Task 6's scan depth parameterised on `org_segment`; the snapshot-create conversions marked cosmetic (they write into a dir created `exist_ok=False`) with the bundle-import sites named as the real ones; the guard test specified as an `ast` walk; `smoke/qa_e2e.py:289,639` noted as reading the retired tree; Q1's hazard stated accurately (`legacy_migrate.py:230-232` already refuses a name with a row, so the exposure is projects whose name was **sanitised or suffixed**); `routers/admin.py:225,243` named as the same two functions behind a different door.

**v2 (2026-07-27)** — two independent reviews of v1, both REJECT, 11 critical findings. Summary of what they caught: `services/legacy_migrate.py` was never mentioned though it holds a third `storage_path_for` caller and a sixth direct read feeding `shutil.move`; `new_project_test.pypsaproj` is a real 22 MB project directory, not a bundle file, and v1's acceptance criteria would have dropped it; `project_dir()` mkdirs, so v1's "changes no behaviour" claim would have shipped two broken endpoints; the size-based idempotence check is provably not a content check on this tree (`KeepA`/`KeepB` are both 39,716 B); the app-data marker let a reinstall overwrite live projects; the marker burned on an empty run; v1 pointed `LEGACY_ROOT` at a tree that `legacy_migrate` scans **unfiltered**; the rename dropped a 409 handler; Task 4's test contradicted its own instruction; migration 0003 is a no-op for the only row that exists; and `git status` cannot verify a gitignored tree.

---

## Why this phase is not optional

Phase 1a moved both storage roots out of the source tree:

| setting | before (`09bd7020`) | after (`39b3503e`) |
|---|---|---|
| `projects_root` | `_BACKEND / "projects"` | `~/Documents/PyPSA GUI/Projects` |
| `PROJECTS_DIR` (flat) | hardcoded `_BACKEND / "projects"` | app-data `flat_projects_root` |

`pypsa-gui/backend/projects/` holds **113 MB**: 13 directories plus one `.zip`. Exactly 12 have a depth-1 `network.nc` and are the import targets; the 13th is the org-scoped tree `860edcb4-…/`, whose `network.nc` sits one level deeper under `e8645aba-…/` and which `--rebase-db` handles separately. Nothing was deleted, and the one DB-tracked project still resolves because its `storage_path` is absolute:

```
3_nodes_system -> /Users/…/pypsa-gui/backend/projects/860edcb4-…/e8645aba-…
```

That path is pinned inside a **gitignored directory inside a git checkout** — `git clean -xdf` deletes all 113 MB silently, and `git status` never warns. Getting this data into a real user directory is the point of the phase.

To see the flat projects before F lands, set `FLAT_PROJECTS_ROOT` **alone** — not `PROJECTS_ROOT` too; `settings.py:53-57` documents why they must differ.

---

## Global Constraints

- **Both modes must keep working.** Every change is conditional on local mode or mode-neutral.
- **Never reload or re-import modules in tests.**
- **Serialize strictly: edit → gate → commit → next task.** No edits under `pypsa-gui/` while a suite runs; a test file created mid-run aborts collection with `exit=2` and yields **no signal at all**.
- **Every local-mode fixture seeds AND removes the local identity.**
- **Set the env FIRST, then `cache_clear()`.**
- **`DATABASE_URL` is mandatory for any manual run.** `.env:17` carries a CWD-relative `sqlite+pysqlite:///./auth_dev.db`, dotenv outranks field defaults, and `python -m tools.…` runs with cwd `pypsa-gui/backend` — exactly where it resolves. This bit during phase 1a.
- **Never hardcode an interpreter path** (CLAUDE.md): `pixi run …`.
- **`PROJECTS_DIR` stays a settable module attribute** (`conftest.py:439`; 18 files use `tmp_projects_dir`).
- **Portable commands only** — no `ls -lT`, no `stat -f`. Windows and macOS both.
- **The importer copies, never moves, and never writes into a destination it cannot prove it created.**
- **Nothing touches the legacy tree while a backend is running.**

---

## Verified constraints

Checked at `9a2ae8f4`, and re-checked by a third reviewer.

| # | Fact | Why it matters |
|---|---|---|
| 1 | `Project.storage_path` is `Text`; `Project.name` is `String(64)` but **SQLite does not enforce it** — only `RenameProjectRequest.new_name` caps length (`models/schemas.py:530`), on one route. `UniqueConstraint("org_id","name")` exists; there is **no** constraint on `storage_path` | The importer must truncate defensively, and directory uniqueness must be asserted in code. |
| 2 | `storage_path_for` has **three** production callers: `project_registry.py:171,207`, `legacy_migrate.py:243` — plus **eight in tests** | Signature change breaks all eleven. |
| 3 | **Six** direct `Path(<x>.storage_path)` reads in production: `routers/projects.py:577,1591,2139,2219,2229`, `legacy_migrate.py:278` — **plus `tests/conftest.py:418`**. `legacy_migrate.py:278` feeds `shutil.move` (`:290`) | The seventh is in a fixture 19 tests depend on. |
| 4 | `project_registry.project_dir()` **mkdirs** (`:149-153`, "Materialise"); **13** call sites | Cannot become a pure resolver without auditing each. |
| 5 | `bind_context:146` sets `ctx.storage_dir` from the raw column | Same bug class as #3. |
| 6 | `rename_project:225-238` has `except IntegrityError → HTTPException(409)`; its "storage_path is UUID-keyed" comment is invalidated by E1, the handler is not | Keep the handler. |
| 7 | `_atomic_write_with` at `routers/projects.py:234`, imported by `snapshots.py:46-47`; its `except` **unlinks** the tmp (`:251-256`) | The `.tmp` survives a killed process, not an exception. |
| 8 | Crash detector at `routers/projects.py:546-548,1879` looks for `f"{fname}.tmp"` per `_BUNDLE_FILES` | E4's sweep stays explicit; Task 5's test asserts the names match. |
| 9 | Legacy tree: **13 directories** + one `.zip`. Exactly **12** have a depth-1 `network.nc` and are the import targets, including `new_project_test.pypsaproj` — a real 22 MB project. The 13th is the org tree `860edcb4-…/`, whose `network.nc` is at depth 2 | Classify by content, never by suffix. The org tree is flagged, not imported. |
| 10 | `parent_project` is a **name**. Chains: `H2 Demand 250MW`→`heat with time-series`→`new_project_test.pypsaproj`; `chatbot_validation_scenario1`→`chatbot_validation_movetest`. Dangling: `4_nodes_N-1`→`test_project_4_nodes2` | Two-pass; dangling is normal; dropping the `.pypsaproj` directory fabricates a second dangling parent. |
| 11 | Duplicate `network.nc` sizes: `KeepA`/`KeepB` 39,716 B; three `chatbot_validation_*` 115,099 B | Size is not identity. |
| 12 | 5 × `chat.jsonl`, 1 × `snapshots/`, 1 × `uploads/`; `_BUNDLE_DIRS` at `routers/projects.py:77` | Assert the full file set. |
| 13 | `legacy_migrate._scan_root` classifies org roots by **UUID-ness of the name**; `_contains_any_file` uses **`rglob`**; `:134` scans `projects_root` with `pre_auth_layout=True`, `:135` scans `legacy_root` with `pre_auth_layout=False` (**no filters**). `claim_legacy_project:230-232` refuses a name that already has a row | Decides Q1. Exposure is projects whose **sanitised or suffixed** directory name differs from their row name. |
| 14 | `/unclaimed` is on the **projects** router (`:662,690`), mounted in local mode; `main.py:534-539` gates only `admin`. `routers/admin.py:225,243` expose the same two functions | Both doors need closing. |
| 15 | Frontend polls `/projects/unclaimed` (`api/projects.ts:87`) and exposes `importUnclaimed` (`:117`) — and **already treats 404 as "nothing to import"** (`:83`) | The hazard is live; Task 1 needs no frontend change. |
| 16 | Alembic head `0002_session_active_project`; `conftest.py:161,458` use `create_all`, so **alembic never runs under pytest**; `local_bootstrap.py:65-67` stamps head when tables exist without `alembic_version` | 0003 needs an explicitly built test DB. |
| 17 | `auth_dev.db`'s one row is not under `projects_root`; `backend/projects/` is gitignored (`.gitignore:23`) and 113 MB | 0003 skips that row by design; `git status` cannot verify the tree. |
| 18 | Baseline at `9a2ae8f4`: **64 files / 1251 tests**; frontend 23 files / 147 tests | Task 0. |
| 19 | `Settings` declares **no `env_prefix`** (`settings.py:12`). Working `PYPSAGUI_*` variables are read from `os.environ` directly (`app_paths.py:23,42`) | A new setting needs an explicit `validation_alias`. |
| 20 | `smoke/qa_e2e.py:289,639` hardcode `BACKEND_DIR / "projects"` | Retiring the tree silently self-skips two smoke checks; not in the pytest gate. |

---

## Resolved design questions

### Q1 — Does the org UUID stay in the path?

**Local mode drops it; web mode keeps it; `/unclaimed` is closed locally first (Task 1).**

Spec §4 writes the layout as `Documents/PyPSA GUI/Projects/<Project Name>/` and D13 asks for human-readable directories; `Projects/860edcb4-…/Belgium Grid` just moves the UUID up a level. Locally the segment carries no information — one org, one fixed id.

Removing it is not cosmetic. With projects at the top of `projects_root`, `_scan_root(…, pre_auth_layout=True)` (constraint #13) treats each non-UUID-named directory as a claimable leftover. `claim_legacy_project:230-232` refuses names that already have a row, so the exposure is **projects whose directory name was sanitised or collision-suffixed** — precisely the ones Task 2 creates. One `POST /unclaimed/{name}/import` then `shutil.move`s that live project. A literal `Local/` segment does not help: `_contains_any_file` is recursive.

So the org segment becomes a parameter (`""` locally, `str(org_id)` on the web) **and Task 1 closes `/unclaimed` in local mode before anything else**. That also settles which importer owns local mode: this phase's.

Consequence to record: a local install later converted to web needs a rebase migration to reintroduce the segment — same shape as 0003, since paths are relative and `project_dir` rejoins the root.

### Q2 — What happens to `backend/projects/` after import?

**Retire it in place. Never delete it, never leave it silently live.**

Not a disk argument (113 MB against 561 GiB). Three real ones: it stays a fully working project store with nothing on disk saying which copy the app uses; retention plus a misplaced idempotence marker is what lets a reinstall copy stale data over live work; and it is gitignored inside a checkout, so `git clean -xdf` erases it silently.

- The import receipt is written **into each staged project directory** and a run manifest alongside, keyed on (source root, destination root, install id) — see revision-log rows 6 and 7 for why both, and why source-root alone breaks on a synced checkout.
- After a verified import, `--forget-legacy` moves the tree **out of the checkout** — default `~/PyPSA GUI legacy-<date>/`, printed on completion. One rename, reversible, and it deliberately breaks the `FLAT_PROJECTS_ROOT` workaround so the stale copy stops being reachable by accident. Renaming *within* the checkout to `backend/projects.imported-<date>` is an explicit opt-in, because `pypsa-gui/.gitignore:23` ignores `backend/projects/` specifically: the renamed tree would be untracked **and un-ignored**, leaving `git status` permanently dirty (which this project's multi-session rule reads as "another session is working, stop") and putting 113 MB in reach of a plain `git clean -df`, not just `-xdf`.
- Deletion is always the user's action. Both CLI and UI print where the old copy is and that it is safe to delete once checked.
- Recommend moving the retired tree out of the checkout, away from `git clean`.

---

## Execution order

Safety-driven. **Task 1 closes the live `shutil.move` hazard and must land before Task 4 changes the layout.**

```
0  backup + quiescence + baseline
1  close /unclaimed in local mode        ← independent, land immediately
2  portable directory names               ┐
3  split resolve from materialise         ├ E1/E2 prerequisites
4  human-readable relative paths + 0003   ┘
5  atomic writes
6  reconcile (read-only)
7  inventory + import
8  first-run wiring
9  acceptance
```

---

## Task 0: Make the data recoverable, then baseline

- [ ] **Step 1: Quiescence — nothing may be writing to the tree**

Constraint: `projects/860edcb4-…/layout.json` was modified at 17:36 today and a vite dev server is running. A backend session between this step's checksums and Task 9's diff produces a false alarm on the phase's only real safety check; a concurrent autosave during a copy tears a staged project.

```bash
lsof -nP -iTCP:8000 -sTCP:LISTEN || echo "port 8000 free"      # macOS/Linux
ps -eo pid,command | grep -i "[u]vicorn" || echo "no uvicorn"
```

On Windows: `netstat -ano | findstr :8000`. Stop any backend before continuing, and re-assert this before Task 7 Step 5 and Task 9 Step 3.

- [ ] **Step 2: Concurrency check**

```bash
git branch --show-current && git status --porcelain && git log --oneline -1 master
pixi run python - <<'PY'
import pathlib, datetime
for p in ("pypsa-gui/backend/routers/projects.py",
          "pypsa-gui/backend/services/project_registry.py",
          "pypsa-gui/backend/services/legacy_migrate.py"):
    print(f"{datetime.datetime.fromtimestamp(pathlib.Path(p).stat().st_mtime):%Y-%m-%d %H:%M}  {p}")
PY
```

- [ ] **Step 3: Back up everything this phase can damage — verified, not assumed**

```bash
cd pypsa-gui/backend
BK=~/pypsa-phase1b-backup-$(date +%Y%m%dT%H%M%S)   # to the SECOND: a date-only
mkdir -p "$BK"                                      # name lets a rerun truncate
tar czf "$BK/projects.tar.gz" projects/             # a good backup with a bad tree
cp auth_dev.db auth_dev.db-wal auth_dev.db-shm "$BK/" 2>/dev/null || true

echo "in tree:    $(find projects -type f | wc -l)"
echo "in archive: $(tar tzf "$BK/projects.tar.gz" | grep -vc '/$')"

# The ONLY valid before/after check: the tree is gitignored (constraint #17),
# so `git status` is structurally incapable of reporting a change here.
find projects -type f -exec shasum -a 256 {} \; | sort > "$BK/projects.sha256"
# NOT /tmp: macOS clears it on reboot and this plan spans days.
echo "$BK" > ~/.pypsa-phase1b-backup-path
```

Do not proceed unless the two counts match.

- [ ] **Step 4: Baseline**

```bash
pixi run gui-tests --collect-only -q 2>&1 | grep -E "^tests/.*: [0-9]+$" \
  | awk -F': ' '{s+=$2; n+=1} END {print "files:", n, "tests:", s}'
pixi run gui-tests -q 2>&1 | tail -3
pixi run npm --prefix pypsa-gui/frontend test 2>&1 | tail -4
```

Expect **64 files / 1251 tests**, exit 0; frontend 23 / 147.

---

## Task 1: Close `/unclaimed` in local mode

**Files:** Modify `backend/routers/projects.py:662,690`, `backend/routers/admin.py:225,243`; create `backend/tests/test_unclaimed_local_mode.py`

**Context:** Constraints #13–#15 and Q1. This is **first** because it is independent, lands today, and removes the hazard before Task 4 puts project directories where the scanner looks. The frontend already treats 404 as "nothing to import" (`api/projects.ts:83`), so no frontend change is needed and the 23/147 baseline holds.

**Per-route dependencies, not a router-level one** — `projects.router` must stay mounted; only `admin` can be gated wholesale (constraint #14). And a **runtime** guard, not import-time: conftest imports `main` with local mode unset (phase 1a Task 15's lesson).

`routers/admin.py:225,243` expose the same two functions behind the admin door, which phase 1a already 404s locally — close them here anyway so the reason is recorded in one place.

- [ ] **Step 1: Write the failing test** — both routes 404 in local mode; both unchanged in web mode (**401** without a session, not 404, proving they are still registered).

- [ ] **Step 2–4:** run (RED), implement, gate, commit.

---

## Task 2: Portable directory names

**Files:** Create `backend/services/safe_names.py`, `backend/tests/test_safe_names.py`

**Interfaces:** `safe_dir_name(name: str) -> str`, `unique_dir_name(name: str, taken: Iterable[str]) -> str`.

**Context:** Windows rejects `<>:"/\|?*`, reserves `CON`/`PRN`/`AUX`/`NUL`/`COM1-9`/`LPT1-9` **including with an extension**, silently strips trailing dots and spaces, and defaults to 260-char paths. macOS is case-insensitive. A **leading** dot must go too, or a legacy `.foo` imports hidden — `routers/projects.py:188` already rejects those.

`_MAX_LEN` is 96 for the **directory**; the row's own truncation is Task 7's job (constraint #1 — SQLite does not enforce `String(64)`).

- [ ] **Step 1: Write the failing test.** Ordinary names untouched; every forbidden character; reserved names incl. `CON.nc`; trailing dots/spaces; **leading dot**; empty/whitespace fallback; truncation; unicode preserved; collision suffixing; case-insensitive collision; suffix within the cap; `taken` accepts any iterable (`Container` would `TypeError` — the body iterates it). **Count the parametrized cases and use that number**; do not guess it.

- [ ] **Step 2: RED** — collection error; run this file alone.

- [ ] **Step 3: Implement.** Annotate `taken: Iterable[str]`; build the lowered set unconditionally; strip leading and trailing dots.

- [ ] **Step 4–5:** run, gate, commit.

---

## Task 3: Split resolve from materialise

**Files:** Modify `backend/services/project_registry.py:146,149-153`, `backend/routers/projects.py` (5 sites), `backend/services/legacy_migrate.py:278`, `backend/tests/conftest.py:418`; create `backend/tests/test_project_dir_resolver.py`

**Context — constraint #4 first.** `project_dir()` **creates** the directory. Seven sites read `storage_path` directly, one of which feeds `shutil.move`. **This task is not behaviour-preserving** — v1 claimed it was, which would have shipped `import_bundle` and `from_template` broken past a green suite because neither has a test.

- [ ] **Step 1: Write the failing test**

The guard must scan `services/`, `routers/`, `tools/` **and** `tests/conftest.py`, and match subscripted expressions:

Use an **`ast` walk** flagging *any* attribute access named `storage_path` outside `project_dir` / `ensure_project_dir` — not only ones wrapped in `Path(...)`. A regex keyed on `Path(` cannot see `project_registry.py:146` (`ctx.storage_dir = str(project.storage_path)`), which is constraint #5 and whose value flows straight into `Path(...)` at `chat_service.py:758`, `upload_service.py:154`, `pypsa_service.py:696` and `solve_queue.py:368`. Scan roots: `services/`, `routers/`, `tools/`, the backend root modules, and `tests/conftest.py`. Plus: `project_dir` resolves relative against the root, leaves absolute alone, and **creates nothing**; `ensure_project_dir` creates.

```python
def test_project_dir_does_not_create_anything(tmp_path, monkeypatch):
    """
    routers/projects.py:577 calls this for every row of GET /api/projects/.
    If it mkdir'd, listing would resurrect the directory of a project deleted
    in Finder and defeat Task 6's missing-dir detection.
    """
```

- [ ] **Step 2: RED** — **eight** offenders (the six in constraint #3, plus `tests/conftest.py:418`, plus `project_registry.py:146`, which is constraint #5 and which Step 3 converts); `project_registry.py:151` is correctly exempt as the resolver's own body. "Creates nothing" fails.

- [ ] **Step 3: Split**

```python
def project_dir(project: Project) -> Path:
    """Resolve a row to a path. Creates nothing. Accepts both formats
    permanently — pre-0003 rows are absolute, and a restored backup can carry
    either."""
    path = Path(project.storage_path)
    return path if path.is_absolute() else Path(get_settings().projects_root) / path


def ensure_project_dir(project: Project) -> Path:
    """`project_dir` plus mkdir. For callers about to WRITE."""
    d = project_dir(project)
    d.mkdir(parents=True, exist_ok=True)
    return d
```

`bind_context:146` → `str(project_dir(project))`.

- [ ] **Step 4: Audit the 13 callers individually**

| Site | Writes after? | Use |
|---|---|---|
| `projects.py:227` | no | `project_dir` |
| `projects.py:781` | **yes** (`write_bytes`) | `ensure_project_dir` |
| `projects.py:1003` | **yes** (`copy2`) | `ensure_project_dir` |
| `projects.py:1115` | yes (save) | `ensure_project_dir` |
| `projects.py:1731,1867,2531` | no | `project_dir` |
| `projects.py:2033` | no | `project_dir` |
| `projects.py:2042` | **yes** (`write_bytes`) | `ensure_project_dir` |
| `routers/deps.py:91` | no — `AuthorizedProject.directory` fans out to 14 read/write sites (`snapshots.py:390,398,422,598`, `uploads.py:204,218,228,243,246,265,297`, `chat_tools.py:1236`, `compare.py:100,2625`), and every write path among them already mkdirs with `parents=True` (`upload_service.py:270,313`, `snapshots.py:246,265`) | `project_dir` |
| `routers/deps.py:131` | check the site | decide |
| `solve_queue.py:56` | yes | `ensure_project_dir` |
| `active_project.py:107` | no | `project_dir` |

**`PUT /layout` (`projects.py:2460`)** guards `if not dest.exists(): 404`. That branch is unreachable today because `project_dir` mkdirs. **Decision: keep `project_dir` there and let the 404 become real** — a layout PUT against a project whose directory is gone should fail loudly, not recreate it. Add a test.

- [ ] **Step 5: Convert the seven direct reads**

`projects.py:577,1591,2139,2219,2229` → `project_registry.project_dir(<row>)`. **`:577` is in `_project_info_db`, which has no `project_registry` import** — this module imports it function-locally (`:225,780,1587,…`); add one or hoist. `legacy_migrate.py:278` → same (check for a cycle; it already imports `services.storage_paths`). **`tests/conftest.py:418`** → same, or the fixture resolves against the pytest CWD once paths go relative, breaking 19 usages.

- [ ] **Step 6: Gate and commit.** The suite **may** need edits — any test asserting a directory sprang into existence from a *read* was asserting the bug.

---

## Task 4: Human-readable, relative storage paths

**Files:** Modify `backend/services/storage_paths.py`, `backend/services/project_registry.py:171,207,225-238`, `backend/services/legacy_migrate.py:243`, `backend/db/models.py:43`, the **eight direct test call sites** (`tests/test_project_locks.py:105`; `tests/test_projects_tenancy.py:107,245,383,505,522`; `tests/test_project_acl.py:84,122`), **and the two modules that assert the layout those callers produce** — `tests/test_unclaimed_import.py:217-224` (builds `projects_root/<org>/<project_id>` and asserts `project.storage_path == str(destination)`) and `tests/test_legacy_migrate.py:155`; create `backend/alembic/versions/0003_relative_storage_path.py`, `backend/tests/test_storage_layout.py`

Those last two run with auth on and no local mode, so Task 1 does not shield them. Rewrite their assertions against `project_registry.project_dir(row)` rather than a hand-built path.

**What `legacy_migrate.py:243` passes:** `name=` the legacy directory's name, `org_segment=_org_segment()`, and `taken=` a set that **accumulates across the claim loop** — seeded from `_taken_names(...)` and added to after each claim. Without the accumulation, two legacy names that sanitise alike (e.g. `Study:1` and `Study/1`) are both handed the same directory within a single call.

**Interfaces:** `storage_path_for(org_id, project_id, name, taken, *, org_segment: bool) -> Path`, relative.

- [ ] **Step 1: Write the failing test** — relative; readable name; forbidden characters absent; collisions suffixed; `org_segment=False` yields **no** org component; `org_segment=True` yields the UUID first.

Plus the directory-uniqueness test that v2 lacked:

```python
def test_a_name_that_sanitises_onto_another_projects_directory_is_suffixed():
    """
    `taken` is the set of sibling DIRECTORY names, never Project.name.
    Project "Study:1" lives in "Study_1". A later project literally named
    "Study_1" must NOT be handed the same directory — both rows would resolve
    to one path and each save would clobber the other.
    """
```

- [ ] **Step 2: RED** — `TypeError`, two-arg signature.

- [ ] **Step 3: Implement; update ALL THREE production callers**

`project_registry.py:171`, `:207`, **and `legacy_migrate.py:243`** (constraint #2).

```python
def storage_path_for(org_id, project_id, name, taken, *, org_segment: bool) -> Path:
    """Relative to `projects_root`. `org_segment` is False locally — one org,
    one fixed id, so `Projects/<uuid>/<Name>` only moves the UUID up a level.
    See Q1 for why this is coupled to Task 1."""
    rel = Path(str(org_id)) if org_segment else Path()
    return rel / unique_dir_name(name, taken)
```

```python
def _taken_names(db, org_id, org_segment: bool) -> set[str]:
    """
    Sibling DIRECTORY names — `Path(row.storage_path).name`, NEVER `row.name`.
    Reading display names lets a project whose name sanitises onto another
    project's directory be assigned that same directory.

    DB **union filesystem**. Rows alone are not enough: a directory with no row
    is an orphan (Task 6's `orphan_dirs`), and handing its name to a new
    project makes the first save adopt and overwrite it —
    `routers/projects.py:1250` mkdirs with `exist_ok=True` and `:1363` then
    `_atomic_write_with`s over its `network.nc`. Impossible while paths were
    `<org>/<uuid>`; introduced by this phase. Reserving an orphan's name costs
    one suffix, adopting it costs the user's data.
    """
    rows = db.scalars(select(Project.storage_path).where(Project.org_id == org_id)).all()
    taken = {Path(r).name for r in rows}

    root = Path(get_settings().projects_root) / (str(org_id) if org_segment else "")
    if root.is_dir():
        taken |= {p.name for p in root.iterdir() if p.is_dir()}
    return taken
```

Test it: a new project whose sanitised name equals an existing **orphan** directory gets a suffix, and the orphan's `network.nc` is untouched.

`_taken_names` lives in **`services/storage_paths.py`** alongside `storage_path_for`, since both `project_registry` and `legacy_migrate` call it. That introduces a `storage_paths → db.models` import the module does not have today (it imports only `settings`) — confirm the direction is acyclic before writing it. No leading underscore: it is called cross-module.

Under pytest, `conftest.py:57-58` pins `PROJECTS_ROOT` to one session-scoped `mkdtemp`, so with `org_segment=False` the filesystem half of the union sees every prior local-mode test's directories. Harmless today — only `test_local_mode_e2e.py:80-87` creates a project this way and removes it in a `finally` — but the next local-mode test that creates a project and asserts its directory name becomes order-dependent. Note it in the test.

**The backstop is a DB constraint, not an assertion.** Migration 0003 adds a unique index on `("org_id", "storage_path")` — `op.create_index(..., unique=True)`, no batch mode needed on SQLite. **Order inside the migration: rewrite the paths first, then create the index** — the reverse fails on any database whose rows already collide. Add a test for a database holding two rows that share `(org_id, storage_path)`: the migration must abort loudly rather than silently drop one. `db/models.py:43` constrains `("org_id","name")` only, so nothing today prevents two rows sharing a directory, and an index is the only mechanism that is atomic under concurrency. Add the matching `UniqueConstraint` to the model. Every write path — `create_root`, `create_scenario`, `rename_project`, and Task 7's importer — is then covered by construction rather than by four separate assertions.

- [ ] **Step 4: Rename moves the directory — check destination BEFORE committing**

`_org_segment()` is defined **once**, in `services/storage_paths.py`, as `not local_mode.is_local_mode()` — the same predicate Task 1 gates `/unclaimed` on. **Task 1 is the sole guard here**, so the two must agree: add a test asserting it. If they diverge, projects sit at the top of `projects_root` while `_scan_root(…, pre_auth_layout=True)` is reachable and `shutil.move` (`legacy_migrate.py:290`) fires on a live project. Web mode is unaffected either way — the org segment stays, so top-level entries are UUID-named and `_is_uuid_named` skips them.

**Extend Task 3's guard exemption to `rename_project`** — it legitimately reads and writes `storage_path` (three accesses below). Do **not** narrow the matcher back to `Path(`-wrapped hits to make the gate pass: that is the weakening this plan removed so the guard could see `project_registry.py:146`.

```python
    old_dir = project_dir(project)
    old_rel = project.storage_path          # for the compensating branch below
    # taken= is the whole point of the fix — not elided. Exclude this project's
    # own current directory, or a no-op rename suffixes itself.
    new_rel = storage_path_for(
        project.org_id, project.id, new_name,
        taken=_taken_names(db, project.org_id, _org_segment()) - {old_dir.name},
        org_segment=_org_segment(),
    )
    new_dir = Path(get_settings().projects_root) / new_rel
    if new_dir.exists() and new_dir != old_dir:
        raise HTTPException(409, f"A directory named '{new_dir.name}' already exists")

    project.name, project.storage_path = new_name, str(new_rel)
    try:
        db.commit()                       # keep the existing 409 handler
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, f"Project '{new_name}' already exists") from exc

    if old_dir.exists() and old_dir != new_dir:
        try:
            new_dir.parent.mkdir(parents=True, exist_ok=True)
            os.replace(old_dir, new_dir)
        except OSError as exc:
            if exc.errno == errno.EXDEV:      # pre-0003 absolute row on another volume
                shutil.move(str(old_dir), str(new_dir))
            else:
                project.storage_path = str(old_rel)   # compensate: row is committed
                db.commit()
                raise
```

Test: happy path; duplicate name → 409 with the directory unmoved; forced move failure → row and disk still agree.

- [ ] **Step 5: Migration 0003 — and state what it does not do**

Rows outside the root are left absolute **by design**; on this machine that is the only row there is (constraint #17), so **0003 does not deliver E2 for `3_nodes_system`** — Task 7's `--rebase-db` does. Do not let a reader believe otherwise.

Constraint #16: alembic never runs under pytest, so build the test DB explicitly (`create_all` + `stamp("0002_session_active_project")` + `upgrade("0003")`), with rows absolute-under-root, relative, and absolute-outside-root; run `upgrade` twice for idempotence.

**Write and test the `downgrade` too.** Rollback step 2 calls `alembic downgrade 0002_session_active_project` and is load-bearing for recovery, so it must both re-absolutise the paths **and drop the new unique index** — otherwise a later re-`upgrade`'s `create_index` fails against an index that already exists.

- [ ] **Step 6: Gate and commit.** Expect to edit the eight test call sites. Three need **rewriting**, not merely re-argumenting:

  - `test_project_acl.py:122` asserts the old absolute `<root>/<org>/<uuid>` shape.
  - **Three sites write.** `test_project_locks.py:105-106` and `test_projects_tenancy.py:107-109` pass the result straight to `_seed_network(...)`, which does `directory.mkdir(parents=True, exist_ok=True)` + `export_to_netcdf`; and `test_projects_tenancy.py:505-507` does its own `uploads.mkdir(parents=True, exist_ok=True)` + `write_text`. With a **relative** return, all three write into `pypsa-gui/backend/<org>/…` — **inside the checkout**, untracked *and* un-ignored (`pypsa-gui/.gitignore:23` covers `backend/projects/` only), leaving `git status` permanently dirty and reachable by a plain `git clean -df`. `pixi.toml:23` runs the suite with `cwd = pypsa-gui/backend`, which is what makes the relative path land there. Pass `Path(get_settings().projects_root) / storage_path`.

  - **The four read-only sites need rebasing, not re-argumenting.** `test_projects_tenancy.py:245,383,505,522` re-derive a path for a row created earlier by `_create_project`. After Task 4, `storage_path_for` keys on the **name** plus a `taken` set, so re-calling it means reconstructing `taken` identically or getting a different collision suffix. Route them through `project_registry.project_dir(row)` instead — the same remedy this task already prescribes for `test_unclaimed_import.py` and `test_legacy_migrate.py`.

  This is the same reasoning the plan applies to `tests/conftest.py:418`.

---

## Task 5: Atomic writes everywhere that matters

**Files:** Create `backend/services/atomic_io.py`, `backend/tests/test_atomic_io.py`; modify `backend/routers/projects.py:234-263`, `backend/routers/snapshots.py:46-47`

**Context:** Constraint #7 — the `except` branch unlinks the tmp, so a test asserting the `.tmp` survives an exception contradicts the code. The detector is for a **killed process**.

- [ ] **Step 1: Write the failing test — the real contract**

Content replaced atomically; a failed write leaves the original intact; the tmp is cleaned on exception; and the naming contract, which must exercise production code rather than assert a file it just wrote:

```python
@pytest.mark.parametrize("fname", _BUNDLE_FILES)
def test_tmp_name_matches_what_the_crash_detector_looks_for(tmp_path, fname):
    """routers/projects.py:546-548 scans for f'{fname}.tmp'. If atomic_write_with's
    suffix logic drifts, the corruption banner silently stops firing."""
    target = tmp_path / fname
    captured = {}
    with pytest.raises(RuntimeError):
        atomic_write_with(target, lambda p: (captured.setdefault("p", p), (_ for _ in ()).throw(RuntimeError()))[0])
    assert captured["p"].name == f"{fname}.tmp"
```

- [ ] **Step 2: Move verbatim, alias the old names**, repoint `snapshots.py:46-47`.

- [ ] **Step 3: Convert the sites that actually matter**

**`routers/projects.py:784,800`** — bundle import, the genuinely destructive one: `write_bytes` over a live project's `network.nc`. Also `:1004` (from-template) and `:2048` (scenario create).

`routers/snapshots.py:294,361,364` are **cosmetic**: all write into `snap_dir`, created at `:265` with `exist_ok=False`, so there is nothing to truncate. Convert for consistency if cheap, but the effort belongs at the bundle sites. The genuinely destructive snapshot path is *restore* (`snapshots.py:477`), already atomic.

Record what is **not** fixed: `atomic_write_with` does no `fsync` of the file or its parent directory — the guarantee is against a killed process, not power loss.

- [ ] **Step 4: Gate and commit.**

---

## Task 6: Reconcile storage against the database

**Files:** Create `backend/services/storage_reconcile.py`, `backend/tools/reconcile_storage.py`, `backend/tests/test_storage_reconcile.py`

**Interfaces:** `scan(db, root, *, org_segment: bool) -> ReconcileReport(orphan_dirs, missing_dirs, stale_tmp)`.

**Declared deviation (E3).** The spec says "**on startup**, import orphan directories". This is a manual, opt-in CLI: auto-importing a directory at boot is the same class of action as the v1 defect that overwrote live projects, and it fires on exactly the ambiguous state where the app cannot know intent. Task 9 must not mark E3 delivered as written.

**Scan depth follows the layout.** With `org_segment=True` (web, and any local install whose pre-0003 rows are still `<org>/<uuid>`) projects are at depth 2, not depth 1 — a depth-1 scan finds nothing and reports a clean tree that is not clean.

**`scan` skips `*.importing`.** Task 7's staging directories live under `projects_root` and contain a `network.nc`, so they would otherwise be classified as orphans — and E3's premise is that an orphan with a `network.nc` is a project to adopt. Adopting one adopts an unverified partial copy.

`scan` is read-only and never deletes (constraint #8). Write full test bodies — prose is how a task ships half-done.

- [ ] **Steps:** test (orphan with `network.nc` reported; without one ignored; missing dir reported; `.tmp` listed but still present after `scan`; clean tree silent; `sweep_tmp` removes only `.tmp`, only when asked; both depths), implement, gate, commit.

---

## Task 7: Inventory and import the legacy tree

**Files:** Create `backend/services/legacy_import.py`, `backend/tools/import_legacy.py`, `backend/tests/test_legacy_inventory.py`, `backend/tests/test_legacy_import.py`; modify `backend/settings.py`, `backend/tests/conftest.py`

`conftest.py:41-68` pins every storage env var at import, with the comment "Pinning only one moves the problem to the others." Add `os.environ.pop("PYPSAGUI_LEGACY_IMPORT_ROOT", None)` there — otherwise a developer who exported it (exactly what Step 5 teaches) has every local-mode test walk their real legacy tree.

**Interfaces:** `inventory(root) -> list[LegacyProject]`; `import_all(db, root, org_id, user_id, *, apply=False) -> ImportReport`.

**Safety rules, each from a confirmed finding:**

1. **Classify by content, never by suffix** (constraint #9). `new_project_test.pypsaproj` is a directory holding a 7 MB `network.nc` and a 15.7 MB `user_ts.json`, and it roots a three-level chain. Test that a `*.pypsaproj` **directory** is importable.
2. **Stage → receipt → rename → row.** Copy into `<dest>.importing/`; verify the full manifest (every source file present at the same size, plus a SHA-256 of `network.nc`); **write a per-project receipt inside the staging directory**; then one `os.rename`; then insert the row. The receipt moves atomically with the data, so a crash between rename and insert leaves a destination that the next run recognises as "copied, needs row" rather than a foreign collision it skips forever.
3. **Size is never an "already imported" signal** (constraint #11).
4. **Never write into a destination it cannot prove it created.** Destination exists without a matching receipt → report a collision and skip. Check `dest.exists()` explicitly before `os.rename`: on POSIX it silently succeeds onto an existing *empty* directory, on Windows it raises (D1 targets both).
5. **Copy, never move.** The source is untouched until `--forget-legacy`.
6. **Idempotence keys on (source root, destination root, install id)** — not source root alone. A synced checkout on a second machine must not see machine A's manifest and import nothing (v2 revision-log row 7). `--apply` **refuses** when the manifest cannot be written.

   **`install_id()` lives in `services/legacy_import.py`** — this task's own module, because this task runs first and needs it. It reads `<app_data_dir()>/install.json`, creating it with `O_EXCL` and a `uuid4` when absent and re-reading on `FileExistsError` to tolerate a race. It `mkdir(parents=True, exist_ok=True)`s the app-data directory first: `O_EXCL` under a missing parent raises `FileNotFoundError`, which the rehearsal does not hit (bootstrap runs first) but a bare CLI invocation would. Both the CLI and Task 8's lifespan call it; neither mints its own.

   It cannot be `LOCAL_ORG_ID`/`LOCAL_USER_ID` — fixed constants shared by *every* install (`local_mode.py:25-26`), which makes the term a no-op. And it must not be per-process: an ephemeral id means every receipt carries a different one, the next run reads its own destinations as non-matching, rule 4 reports them as foreign collisions and skips them **permanently** — the exact outcome the receipt exists to prevent.

   **Receipt schema**, written inside the staging directory before the rename:

   ```json
   {"source_root": "...", "source_dir_name": "Belgium Grid", "dest_root": "...",
    "install_id": "...", "imported_at": "...",
    "files": [{"path": "network.nc", "size": 39716, "sha256": "..."}]}
   ```

   **Resolution order is lookup-before-allocate:** find the receipt naming this source directory *first*, and only allocate a fresh `unique_dir_name` when none matches. Otherwise a crash-resumed run whose inventory order differs allocates a different name and reports the half-imported project as a foreign collision it skips forever.

   **The allocation uses `taken = _taken_names(db, org_id, _org_segment())`, seeded once and added to after each successful `os.rename`.** Without the accumulation, two legacy names that sanitise alike — a case Step 1's fixture deliberately includes — both target one destination; the second is caught by rule 4's exists-check and silently skipped. No data is lost (the source is untouched until `--forget-legacy`), but a project goes un-imported, which is exactly what lookup-before-allocate exists to prevent.
7. **`copytree(symlinks=True, ignore_dangling_symlinks=True)`**; normalise destination modes — three legacy directories are `drwxrwxrwx` and must not become world-writable under `~/Documents`.
8. **Truncate the row name defensively.** SQLite does not enforce `String(64)` (constraint #1), so long names persist silently until something else trips.
9. **Two-pass parents** by name; dangling is normal and reported.
10. **Its own setting, correctly bound** (constraint #19):

```python
    legacy_import_root: Path | None = Field(
        default=None, validation_alias="PYPSAGUI_LEGACY_IMPORT_ROOT",
    )
```

`Settings` has no `env_prefix`, so without the alias this binds `LEGACY_IMPORT_ROOT`. Default `None` means "no import configured"; the dev default is `backend/projects`, and the packaged shell sets it (workstream H). **Declare this in the deviation table**: until H lands, F1's first-run trigger only fires when the variable is set.

- [ ] **Step 1: Inventory test + implementation.** Fixture mirroring the real tree: a `.zip`, a `*.pypsaproj` **directory**, a UUID-named org tree, a directory without `network.nc`, a dangling parent, a 200-char name, two names colliding after sanitising, a `drwxrwxrwx` directory.

- [ ] **Step 2: Run inventory against the real tree — read-only, safe**

```bash
cd pypsa-gui/backend
pixi run python -c "
from services.legacy_import import inventory
for p in inventory('projects'):
    print(f'{p.dir_name:42} net={p.has_network} parent={p.parent_name} skip={p.skip_reason}')"
```

Expect **12 importable**, the `.zip` skipped, the org tree flagged.

- [ ] **Step 3: Import test + implementation.** Clean import; parent resolution both orders; dangling parent reported and child still imported; re-run reports already-present **from receipts, not sizes**; a crash before the rename leaves an `*.importing/` the next run discards; a crash *after* the rename leaves a receipt-bearing destination the next run completes; two same-size projects not confused; a foreign non-empty destination refused; `apply=False` changes nothing; the full file set arrives (`network.nc`, `user_ts.json`, `layout.json`, `metadata.json`, `solver_config.json`, `chat.jsonl`, `snapshots/`, `uploads/`, `results_state.pkl`).

- [ ] **Step 4: CLI**

```bash
pixi run python -m tools.import_legacy                 # dry run (default)
pixi run python -m tools.import_legacy --apply
pixi run python -m tools.import_legacy --rollback <manifest>
pixi run python -m tools.import_legacy --forget-legacy
pixi run python -m tools.import_legacy --rebase-db      # separate, see below
```

`--apply` **refuses** when `DATABASE_URL` resolves inside the source checkout — v2's own step would have written into `auth_dev.db` and `~/Documents`, because `-m tools.…` runs with cwd `pypsa-gui/backend` where `.env`'s relative URL resolves.

**`--rebase-db` is separate and separately confirmed.** It is the *only* thing that delivers E2 for `3_nodes_system`, and it necessarily targets a database inside the checkout — which is why it cannot live under `--apply`'s refusal. It prompts, names the database and the row, and defaults to no.

It **copies**, like everything else here — v3 said "relocates", contradicting this plan's own global constraint on the single highest-value object in the phase. A filesystem move is also not transactional with a DB commit, so the ordering is explicit:

1. copy the org-scoped tree to `projects_root / unique_dir_name(row.name, _taken_names(db, row.org_id, _org_segment()))` — the **same sanitised layout every other project gets**, not `<org>/<uuid>` verbatim, which would re-introduce the segment Q1 removes. `shutil.copytree` defaults to `dirs_exist_ok=False`, so it fails safe if the name is taken;
2. verify with the same manifest + `network.nc` SHA-256 as rule 2 — abort and remove the copy if it fails;
3. rewrite the row's `storage_path` to the relative form and commit;
4. if the commit fails, remove the copy and re-raise — the source is still authoritative and untouched;
5. leave the source for `--forget-legacy`.

Test all three paths: happy, verify-fails, commit-fails-after-copy.

- [ ] **Step 5: Rehearse against a copy** (re-assert Task 0 Step 1 quiescence first)

```bash
cd pypsa-gui/backend
REH=$(mktemp -d); cp -R projects "$REH/legacy"; APP=$(mktemp -d)
# A fresh database has no schema and no local identity; bootstrap_local does
# ensure_app_dirs -> ensure_schema -> ensure_local_identity.
PYPSAGUI_LOCAL_MODE=1 PYPSAGUI_APP_DATA_DIR="$APP" \
  DATABASE_URL="sqlite+pysqlite:///$APP/pypsa-gui.db" PROJECTS_ROOT="$APP/projects" \
  pixi run python -m tools.bootstrap_local
PYPSAGUI_LOCAL_MODE=1 PYPSAGUI_APP_DATA_DIR="$APP" \
  DATABASE_URL="sqlite+pysqlite:///$APP/pypsa-gui.db" PROJECTS_ROOT="$APP/projects" \
  PYPSAGUI_LEGACY_IMPORT_ROOT="$REH/legacy" MPLBACKEND=Agg \
  pixi run python -m tools.import_legacy --apply
find "$APP/projects" -maxdepth 1
find projects -type f -exec shasum -a 256 {} \; | sort \
  | diff - "$(cat ~/.pypsa-phase1b-backup-path)/projects.sha256" && echo "real tree unchanged"
```

Expect 12 readable top-level directories (no org segment — Q1).

- [ ] **Step 6: Gate and commit.**

---

## Task 8: Run it on first launch

**Files:** Modify `backend/main.py` (`lifespan`); create `backend/tests/test_first_run_import.py`

**Context:** Phase 1a's `lifespan` does `ensure_app_dirs` → `ensure_schema` → `ensure_local_identity`. Import is a fourth step, after the identity exists (rows need `org_id` and `created_by`).

- **Idempotence comes from receipts keyed on (source, destination, install)** — not an app-data marker, which let a reinstall copy stale data over live work.
- **A zero-candidate run records nothing**, so the import is not permanently skipped because the root was not configured yet.
- **Take an `O_EXCL` lock** in the app-data dir — created *after* `ensure_app_dirs`, so the directory exists — released in a `finally`. The single-instance lock is D11/H1 and has not landed; two launches would otherwise interleave `copyfile` writes.
- **Never block startup.** A failure logs and continues to a working app; the CLI is the retry path.

- [ ] **Steps:** test (runs when no matching receipt and candidates exist; skips when receipts match this install; re-runs when the destination root differs; never in web mode; a raising importer still yields a booting app with reachable `/api/health`; a second concurrent start does not double-import), implement, gate, commit.

---

## Task 9: Phase 1b acceptance

- [ ] **Step 1:** both suites against the Task 0 baseline plus this phase's additions.
- [ ] **Step 2:** re-assert quiescence, then a real first run against a **copy**; check `/api/projects/` lists 12, each has a readable top-level directory, one opens and returns buses, both real lineage chains survive and the one dangling parent is reported.
- [ ] **Step 3:** prove the real tree is untouched — `shasum` diff against Task 0's manifest. `git status` is **not** valid here (constraint #17).
- [ ] **Step 4:** tear down; confirm the port is free.
- [ ] **Step 5:** note that `smoke/qa_e2e.py:289,639` hardcode `BACKEND_DIR / "projects"` and will self-skip after `--forget-legacy`. Not in the pytest gate, so nothing goes red — which is why it is written down.

---

## Rollback

1. `pixi run python -m tools.import_legacy --rollback <manifest>` — deletes exactly the destinations and rows the manifest records. Imported rows are identifiable **only** because the manifest records their ids; `parent_project_id` is `ondelete="SET NULL"`, so a partial manual cleanup silently flattens lineage.
2. `alembic downgrade 0002_session_active_project` — re-absolutises `storage_path`.
3. Restore `auth_dev.db` (+ `-wal`/`-shm`) from Task 0.
4. `tar xzf "$BK/projects.tar.gz"` if the source was retired or damaged.
5. `git revert` the phase's commits.

Steps 1–3 are possible only because Task 0 backs up the database and Task 7 writes a manifest. Do not start without both.

---

## Self-review

**Spec coverage.** E1 → 2, 4. E2 → 3, 4, 0003, and Task 7's `--rebase-db` (the only thing that reaches the existing row). E3 → 6, **declared deviation**: manual, not on startup. E4 → 6. E5 → 5, sites named and the cosmetic ones marked. F1 → 7, 8, **declared deviation**: the first-run trigger needs `PYPSAGUI_LEGACY_IMPORT_ROOT`, which the packaged shell sets in workstream H. F2 → 7. F3 → 7 (directory copy; the bundle path is never used). F4 → 7, via receipts rather than heuristics.

**Declared deviations:** E3 (manual, not on startup); F1 (the first-run trigger needs `PYPSAGUI_LEGACY_IMPORT_ROOT` until workstream H sets it); **E2-partial** (migration 0003 does not reach the one row that exists on this machine — `--rebase-db` does, as a separate confirmed step); **E5-partial** (`snapshots.py:294,361,364` are cosmetic — they write into a directory created `exist_ok=False`, so there is nothing to truncate; convert them if cheap, but the spec named them and this is a scope reduction). Nothing else deviates silently.

**Ordering.** 1 before 4 — the hazard closes before the layout moves. 2 before 4 (consumes it). 3 before 4 (seven direct reads routed before the format changes). 7 before 8. 5 and 6 independent.

**Type consistency.** `storage_path_for` gains three parameters in Task 4; all three production callers and all eight test call sites change in that commit. `project_dir` / `ensure_project_dir` are distinct from Task 3 onward. `inventory` returns the `LegacyProject` that `import_all` consumes. `scan` takes `org_segment` to match Task 4's layout.

**Residual risk, stated not hidden.**

- `atomic_write_with` does not `fsync` the file or its parent directory — the guarantee is against a killed process, not power loss.
- E3 is manual, not on startup.
- A local→web conversion needs a further rebase migration to reintroduce the org segment.
- A machine that runs 0003 but never `--rebase-db` keeps one absolute row.
- `smoke/qa_e2e.py:289,639` self-skip once the tree is retired; not in the pytest gate, so nothing goes red.
- **Task 1 is the *sole* guard** against `_scan_root(pre_auth_layout=True)` classifying a live local project as claimable. There is no second layer; an earlier draft claimed one and it is withdrawn above. Web mode is structurally safe (org UUIDs are skipped by name), so this is a local-mode-only single point of failure, and the test asserting `_org_segment()` and Task 1's gate share a predicate is what holds it.
- **The phase ends with the legacy tree still live inside the checkout.** `--forget-legacy` is a user action and Task 9 does not run it, so `git clean -xdf` can still silently delete 113 MB when the phase closes — the hazard this phase opens with. Task 0's verified tarball at `~/pypsa-phase1b-backup-<ts>/` is what makes that recoverable; tell the user to run `--forget-legacy` once they have checked the import.
