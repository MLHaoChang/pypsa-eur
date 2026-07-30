# PyPSA GUI as a local desktop application — design

**Date:** 2026-07-26
**Status:** awaiting review — Step 0b landed `09bd7020`, premise holds, implementation unblocked (§10)
**Scope:** `pypsa-gui/` (backend + frontend). No changes to PyPSA-Eur workflow code.

---

## 1. Goal

Ship PyPSA GUI as an installable local desktop application for Windows and macOS:
files and results saved on the user's own machine, no user authentication, project
management preserved for locally available projects. The core modelling, solver, and
UI functionality is unchanged.

## 2. Non-goals

- Rewriting the tenancy/ACL layer. It stays; local mode satisfies it with one seeded org.
- Multi-project concurrency beyond what the backend does today.
- Auto-update (v1 is structured for it; the implementation is deferred).
- Code signing and notarization (deferred; documented bypass for v1).
- Removing the web/Postgres deployment. It stays alive in the same codebase.

## 3. Decisions

| # | Area | Decision |
|---|---|---|
| D1 | Platforms | Windows x64 (10/11), macOS arm64 (**14+**). No Intel Mac, no Linux. *Was 12+; corrected 2026-07-30 after measuring.* |
| D2 | App shell | pywebview native window (WebView2 / WKWebView). |
| D3 | Distribution | Installer + `--onedir` app folder. Not a single self-extracting file. |
| D4 | Auth | Local mode: seed one org + user, bypass the session gate. Auth code retained. |
| D5 | Data location | `Documents/PyPSA GUI/Projects`, changeable in Settings. Config + DB in OS app-data. |
| D6 | Chatbot | Retained. User supplies their own `ANTHROPIC_API_KEY` via Settings. |
| D7 | Web deployment | Retained. Local mode is a flag in one codebase; both modes covered in CI. |
| D8 | Signing | Unsigned for v1. Internal audience. Bypass documented. |
| D9 | Auto-update | Structure for it (version metadata, bundle layout); implement later. |
| D10 | Existing projects | One-time import on first launch via a purpose-built migrator. |
| D11 | Concurrency | One window, one active project. Single-instance lock is mandatory. |
| D12 | Close during solve | Confirm with the user, then abort, then flush, then exit. |
| D13 | Folder names | Human-readable project directories on disk. |
| D14 | Build env | Separate pip-wheel venv (`gui-requirements.txt`), not the pixi/conda env. |
| D15 | Size trim | None beyond free wins. Ship ~500–600 MB; revisit only if users complain. |

### Note on D1 — the macOS floor is 14, not 12

Measured against `gui-requirements.txt` on PyPI, not assumed. Lowest macOS
floor offered by each pinned package's arm64 wheels:

| Package | Floor |
|---|---|
| **netCDF4 1.7.3** | **14.0** |
| scipy 1.17.1 | 12.0 |
| numpy, pandas, matplotlib, highspy, ujson, SQLAlchemy | 11.0 |

netCDF4 is the sole binding constraint, and it is not version-specific: every
arm64 wheel it publishes from 1.7.1 through 1.7.4 floors at `macosx_14_0`.
netCDF4 is how every project is written and read, so it cannot be dropped, and
building it from source needs an HDF5/netcdf-c toolchain — precisely what D14's
pip-wheel decision exists to avoid.

So "12+" was never achievable through the chosen build path. `LSMinimumSystemVersion`
in `pypsa-gui.spec` says 14.0, which is correct; this row was the stale half.

Why it matters beyond bookkeeping: a macOS 12 or 13 user gets Finder's
"requires a newer version of macOS" and **no log entry**, because the app never
starts. It is invisible on the developer's machine and only ever surfaces on
someone else's.

### Note on D13

Human-readable folder names is the one decision that changes core code
(`storage_path_for` and its consumers). It was chosen knowingly: a "files saved
locally" application whose folders are `860edcb4-28ad-…` does not deliver the
promise. It is costed at 3–4 days including migration, and it overlaps with the
`storage_path` portability fix (§5.4), which is required regardless.

---

## 4. Architecture

```
PyPSA GUI.app / %LOCALAPPDATA%\PyPSA GUI\
├── PyPSAGUI(.exe)              desktop entrypoint
│   ├─ acquire single-instance lock
│   ├─ resolve app_data_dir() and projects_root
│   ├─ set os.environ BEFORE `import main`:
│   │    DATABASE_URL, PROJECTS_ROOT, LEGACY_ROOT,
│   │    CORS_ALLOWED_ORIGINS, PYPSAGUI_LOCAL_MODE, MPLBACKEND=Agg
│   ├─ bind a free port, then import main
│   ├─ first-run: alembic upgrade head → seed org/user → migrate legacy projects
│   ├─ uvicorn.Server(app_object) on a worker thread
│   ├─ poll /api/health, show splash (budget 6–10 s cold)
│   └─ pywebview window → http://127.0.0.1:<port>/
└── _internal/
    ├── frozen Python + ~15 pip-wheel dependencies
    ├── frontend_dist/          built SPA, served by FastAPI
    ├── alembic/                migrations
    ├── project_templates/      368 KB, 3 .nc files
    └── templates/              matpower.jinja2

User data (outside the bundle):
  Documents/PyPSA GUI/Projects/<Project Name>/   projects, results, uploads, chat
  <app-data>/pypsa-gui.db                        SQLite (WAL)
  <app-data>/config.json                         projects root, API key ref, version
```

The same `FastAPI` app object serves both the API and the SPA, same-origin. uvicorn
runs in-process on a worker thread. The solver already runs on a thread, so no
subprocess is introduced anywhere.

---

## 5. Verified constraints

Everything in this section was confirmed against the code or measured in the pixi
env on 2026-07-26. These are the reasons the workstreams are shaped as they are.

### 5.1 The auth gate is one block, not 68 call sites

> Line numbers re-verified 2026-07-27 against the Step 0b working tree.

`main.py:248` sets `request.state.auth_user = resolve_request_user(request, db)`
unconditionally and returns 401 at `main.py:267` when it is `None`. Starlette makes the
last-added middleware outermost, so an added middleware is either overwritten at `:248`
or never reached. **The change must be a branch inside that block.**

That block gates 118 of 172 routes that never touch `deps.optional_user` at all
(`network`, `simulation`, `results`, `io`, `clustering`, `vintage`, `chat`). One
edit therefore covers the whole surface.

`deps.optional_user` still honours a pre-populated `request.state.auth_user`
(`deps.py:112-113`), so the branch also covers the dependency-based routes.

Constraints on the branch:
- Must re-fetch the `User` per request. `expire_on_commit` defaults to `True`
  (`db/session.py:44`), so a module-cached ORM object raises `DetachedInstanceError`
  on `user.id` in `project_registry`, `project_acl`, and `projects.py:626`.
- Must also disarm `_csrf_rejection` in local mode. It exempts cookie-less requests,
  but a stale `pypsa_gui_session` cookie in the webview profile re-arms it.
- `routers/chat.py:263` already calls `set_acting_user` from `request.state.auth_user`,
  so chat tools follow automatically. (An earlier report claiming this was dead was wrong.)

### 5.2 The frontend's auth-off path is dead code

`AuthModeProvider.tsx:39-55` overwrites the compile-time `VITE_AUTH_ENABLED` flag
with the `/api/health` value, and `main.py:507` returns a hardcoded `True`.
Making health return `False` in local mode is the single change that unlocks:

- `spa.html:37-53` — the pre-React boot gate, which `main.tsx:31-33` **awaits**
  before `createRoot`. With `auth_enabled: false` it returns `'single-user'` and
  skips `/api/auth/me` entirely.
- `AuthMismatchGate.tsx` — otherwise renders a full-screen "Login UI is not enabled"
  card in place of the workbench.
- The login gate itself.

Two further frontend changes are required:
- `api/client.ts:133-139` converts any 401 carrying `"Authentication required"` into
  `setAuthEnabled(true)`, permanently re-arming the login UI mid-session. Must be
  suppressed in local mode.
- `AuthProvider.tsx:31-36` sets `user = null` when auth is off, which makes
  `hasAdminConsoleAccess(null)` false and the admin console unreachable. It must
  synthesize a local admin user.

### 5.3 Nothing serves the SPA, and the naive fix has two traps

`grep -rn "StaticFiles\|app.mount"` over the backend returns zero hits. The routing
brain is `frontend/vite.auth-gate.ts:41-67` (`decideGateRoute`), registered only via
`configureServer`/`configurePreviewServer` — it emits nothing into `dist/`. And
`vite.config.ts:13` sets `appType: 'mpa'`, which disables Vite's SPA history fallback.

- **Trap 1:** `dist/index.html` is the static **login** page with no React entry
  (byte-identical to `dist/login.html`). A conventional `StaticFiles(html=True)`
  catch-all serves the sign-in form for `/projects`.
- **Trap 2:** wiring `/` to `spa.html` instead creates an infinite redirect loop via
  `spa.html:46` (`location.replace('/?needLogin=…')`).

Assets are root-absolute (`/assets/…`, `/brand.css`), so the mount must be at
document root. Validation must not use `npm run preview` — it registers the same
Vite gate and will pass while the packaged path fails.

### 5.4 There are two project roots, and stored paths are absolute

- `settings.projects_root` (`settings.py:38`) — read by `services/storage_paths.py`.
- `routers/projects.py:48` — `PROJECTS_DIR = Path(__file__).parent.parent / "projects"`,
  a module constant that **never reads settings**. It backs `_safe_project_dir`,
  the legacy scan, `routers/compare.py:54`, `services/chat_service.py:754`, and
  `services/upload_service.py:159`.

Setting `PROJECTS_ROOT` alone produces a half-relocated app: projects save to the
new location while chat and uploads write into the read-only bundle.

`settings.legacy_root` (`settings.py:39`) is `__file__`-relative with no env override.

`Project.storage_path` is stored as an **absolute string** (`project_registry.py:171,207`).
Moving the app, changing the projects folder, or crossing between Windows and macOS
dangles every row. Paths must be stored relative to `projects_root` and joined on read.

`chat.jsonl` is written to `PROJECTS_DIR / ctx.loaded_project` — the flat display
name — while project data lives under the org/project UUID tree. They are different
directories today. This is why chat history cannot be in the export bundle.

### 5.5 A dynamic port breaks every mutation

`settings.py:23` pins `cors_allowed_origins` to the two Vite dev origins, and that
one string drives **both** CORS (`main.py:429`) and the CSRF Origin check
(`main.py:158-168`). Browsers send `Origin` on same-origin non-GET requests, so an
ephemeral port yields `403 csrf_origin_rejected` on every POST/PUT/PATCH/DELETE.
Reproduced independently by two reviewers against a live server.

Both `get_settings()` and `security.allowed_origins()` are `@lru_cache`d, so
`CORS_ALLOWED_ORIGINS` must be in `os.environ` **before `import main`**.

### 5.6 WITHDRAWN — the frontend does send the CSRF token

> **Correction, 2026-07-27.** This section claimed a live bug. It is false and the
> claim is withdrawn. `frontend/src/api/csrf.ts` exists (committed in `1d930244`) and
> exports `CSRF_COOKIE`, `CSRF_HEADER`, `CSRF_SAFE_METHODS`, `needsCsrfHeader()` and
> `readCsrfToken()`; `frontend/src/api/client.ts:5` imports them and `:114-122` wires
> the request interceptor, with a `csrf_token_invalid` refresh-and-retry path at `:164`.
>
> The original finding came from three independent greps over `frontend/src` and the
> built bundle, all returning zero matches. The frontend was being rebuilt by a
> concurrent session while those checks ran — `dist/assets/spa-B6BHlEqH.js` was
> replaced by `spa-vXDFmC4A.js`. The evidence was accurate when gathered and stale
> when reported.
>
> The only real residual gap is the handful of raw `fetch`/`sendBeacon` call sites that
> bypass the axios interceptor. That is now the whole of workstream C.
>
> Original text retained below for the record.

---

### 5.6 (original, superseded) The frontend never sends the CSRF token

`_csrf_rejection` (`main.py:170-183`) requires `X-CSRF-Token` to match the
`pypsa_gui_csrf` cookie. The frontend has **zero** occurrences of `csrf`/`xsrf` in
`src/`, the HTML entries, or the built bundle. `api/client.ts:13-17` sets no
`xsrfCookieName`/`xsrfHeaderName`, and axios's defaults (`XSRF-TOKEN` /
`X-XSRF-TOKEN`) do not match the backend's names. Login returns `csrf_token` in its
body (`routers/auth.py:175-182`) and nothing reads it. The backend test suite passes
because `tests/conftest.py:226` sets the header by hand.

**This is a live bug in the auth-enabled web deployment**, not a desktop-only issue.
Local mode does not hit it (no session cookie), but D7 keeps the web mode alive, so
it is in scope: add an axios request interceptor plus the same treatment for the raw
`fetch` mutation sites (`api/uploads.ts:76,88,102,130`, `api/chat.ts:63`,
`pages/TopologyCanvas.tsx:147,2361`).

### 5.7 Shutdown loses data and then hangs

- `lifespan` (`main.py:123-126`) has nothing after `yield`. No `atexit`, no signal handler.
- `PyPSAService` holds `_contexts` capped at `RESIDENT_CAP = 5` (`pypsa_service.py:34,51`).
  Reads resolve through `_active` (the registry is dormant per its own comment), but
  **up to five projects can be resident with unsaved edits**. The only path that
  persists a non-active one is LRU eviction. Autosave is a *frontend* 5-minute
  interval covering only the active project.
  Measured save cost on real projects: 0.37 s and 0.79 s. A full flush is 1–5 s.
- `chat_service.py:227` creates a module-level `ThreadPoolExecutor` that is never
  shut down. CPython's atexit joiner blocks interpreter exit until every worker returns.
- Solver threads are `daemon=True` (`simulation.py:510,727`; `solve_queue.py:234`).
  A hard quit skips the worker's `finally:`, so `restore_modelling()` never runs and
  the network is left carrying transient vintage rows, slack generators, and
  dispatch-fix `p_set` overrides.
- There are **two** solve paths. `POST /api/simulation/abort` only sets the active
  context's stop event; queue jobs run on the `solve-queue-dispatcher` thread with
  their own stop events and are invisible to `_solver_in_flight()`.
- uvicorn's `capture_signals` returns early off the main thread, so **no signal
  handlers are installed**. Shutdown must set `server.should_exit = True` and join.

Ordered shutdown sequence: confirm with user → abort both solve paths and wait →
flush all resident contexts → release project locks → `_TOOL_EXECUTOR.shutdown(wait=False,
cancel_futures=True)` → `server.should_exit` → join → exit.

### 5.8 SQLite needs configuration; Alembic needs stamping

Measured on the real engine: `QueuePool` size 5 (+10 overflow), `journal_mode: delete`,
`busy_timeout: 5000`. `db/session.py:37-41` sets only `check_same_thread: False`.

Without WAL a writer blocks all readers; after 5 s the `database is locked` error is
swallowed by the bare `except Exception` at `main.py:240` and returned as a **503 on
every API call** telling the desktop user to start Postgres.

Non-request threads that write: `chat_tools.py:1056-1069` opens `SessionLocal()` on a
`chat-tool` pool worker and calls handlers that commit. (`resolve_session` is
read-only and `solve_queue._run_job` never touches the DB — both confirmed.)

`auth_service.py:96` uses `.with_for_update()`, which SQLAlchemy's SQLite dialect
renders as nothing — the row lock is silently lost.

Alembic itself is **SQLite-clean** — `0001_tenancy.py` uses `sa.Uuid`, `sa.DateTime`,
`sa.Text` throughout, no Postgres-only types. The real problems:
- The existing dev DB has no `alembic_version` table (it was built with
  `create_all`), so `alembic upgrade head` fails with "table organizations already exists".
- `alembic/env.py` never sets `render_as_batch=True`. SQLite cannot `ALTER COLUMN`,
  so the *next* migration written will pass on Postgres and fail on SQLite.
- `alembic.ini:4` still hardcodes a Postgres URL, and `script_location`/`prepend_sys_path`
  are CWD-relative — unusable frozen.

### 5.9 The DB is the sole source of truth, and the reconcile tool does not exist

`routers/projects.py:637-641` cites `tools/reconcile_storage` as the drift channel.
`ls tools/` shows `auth_e2e_smoke.py`, `bootstrap_super_admin.py`, `openapi_diff.py`.
It was never written.

- A folder copied in by hand is invisible to `list_projects` (DB rows only).
- A folder deleted in Finder leaves a ghost row: `_project_info_db:568-581` falls back
  to `bus_count=0` and `activate_project:1699-1700` then 404s. Nothing cleans it.

The export bundle does **not** round-trip: `_BUNDLE_FILES` (`projects.py:57`) excludes
`snapshots/` (up to 50 versions per project) and `chat.jsonl`, and `import_bundle:769`
calls `create_root`, so an imported scenario loses its parent link.

Current dev data: 10 flat legacy directories on disk (113 MB) against **one** row in
`auth_dev.db`. The migrator must handle flat directories and preserve `parent_project`
from each `metadata.json`.

### 5.10 Packaging reality

Measured closure of `import main`: **~500 MB** of site-packages (287 MB of it `.so`)
plus ~151 MB of external conda dylibs reached via `@rpath`, plus `share/proj` (10 MB),
`mpl-data` (9.1 MB), `magic.mgc` (11 MB).

Verified present in the closure and **not excludable** — `pypsa/__init__` imports
`plot` unconditionally and `linopy.monkey_patch_xarray` imports polars:

| package | size | pulled in by |
|---|---|---|
| `_polars_runtime_32` | 180 MB | linopy |
| pandas | 69 MB | direct |
| scipy | 65 MB | pypsa |
| statsmodels | 52 MB | pypsa.plot |
| plotly | 44 MB | pypsa.plot.statistics.charts |
| numpy | 34 MB | direct |
| matplotlib | 29 MB | pypsa.common |
| pyarrow | 27 MB | pandas.compat |
| pydeck | 23 MB | pypsa.plot.maps.interactive |

Verified **absent** from the closure — excluding them saves nothing: `psycopg`,
`cartopy`, `rasterio`, `fiona`, `dask`, `snakemake`, `atlite`, `gdal`, `magic`,
`highspy`, `netCDF4`, `anthropic`. (`cartopy` is worth excluding *defensively*: it is
guarded by `find_spec` and pulling it in would cost +129 MB, mostly botocore.)

`highspy` (+5.3 MB) and `netCDF4` (+1.6 MB) load lazily at runtime and **must** be
hidden imports. `highspy/_core.so` statically links HiGHS and depends only on
`libc++`/`libSystem` — no external solver binary is needed.

Per D14 the frozen app is built from a pip-wheel venv, which is what makes PyInstaller's
stock hooks work: the conda env has no `shapely/.dylibs` and no `pyproj/proj_dir`,
so hooks-contrib would collect nothing and fail at runtime rather than build time.

Other packaging requirements:
- `copy_metadata` for `pypsa`, `linopy`, `xarray`, `plotly`, `argon2-cffi` — all call
  `importlib.metadata.version()` at import.
- Hidden imports for uvicorn's string-resolved classes:
  `uvicorn.protocols.http.{auto,h11_impl,httptools_impl}`,
  `uvicorn.protocols.websockets.{auto,websockets_impl}`, `uvicorn.lifespan.{on,off}`,
  `uvicorn.loops.{auto,asyncio}`. Pass the **app object**, never `"main:app"`.
- Hidden imports for `xarray.backends` entry points (netcdf4 is the one used).
- Hidden imports for the restricted unpickler's targets (`projects.py:292-317`):
  `pandas.core.internals.managers`, `pandas._libs.internals`,
  `pandas._libs.tslibs.offsets`, `numpy._core.multiarray`. If dropped, every saved
  project **silently** loses its cached results — the code fails soft.
- `MPLBACKEND=Agg` before `import pypsa`. matplotlib otherwise resolves to `macosx`,
  and a backend init from a worker thread while pywebview owns the Cocoa run loop
  is a crash, not a warning.
- `datas` must be an **explicit allowlist**. `backend/` currently contains a real
  `ANTHROPIC_API_KEY` and `SECRET_KEY` in `.env`, `auth_dev.db` with a password hash
  and absolute dev paths, and 113 MB of real projects. Only `project_templates/`
  (368 KB) and `templates/matpower.jinja2` are needed.
- `python-magic` is import-guarded (`routers/uploads.py:92-97`) and degrades safely,
  but the degradation loses the MIME anti-spoofing check. Either ship
  `libmagic` + `magic.mgc` or accept it in writing.
- Windows: pywebview's `edgechromium` backend needs the Edge WebView2 Runtime. The
  installer must chain the Evergreen bootstrapper or the window renders blank.

### 5.11 Startup budget

Measured warm on macOS arm64:

| stage | seconds |
|---|---|
| `import pypsa` | 1.55–1.60 |
| `import main` (after pypsa) | 0.48 |
| uvicorn boot + lifespan → `/api/health` 200 | 0.15–0.22 |
| **process start → health 200** | **2.30** |
| first project load (7.5 MB, 26 280 snapshots) | 2.36 |

Splash budget: **6–10 s** cold, with progress. `seaborn` accounts for ~0.5 s of import
time and the backend never plots — a cheap win if wanted later.

### 5.12 Server-deployment leftovers to disable in local mode

- `X-PyPSA-Replica` middleware (`main.py:402-415`) — dead weight.
- Login throttle (`security.py:116-118`): 10 attempts → **15-minute block** with a
  restart as the only escape. Unacceptable in a desktop app.
- `routers/admin.py` — 9 multi-tenant endpoints including a `shutil.move` claim path.
  Do not mount in local mode.
- `services/email_service.py:11` — `OUTBOX` list appended on every send, never trimmed.
  A slow leak in a long-running process.
- `sse-starlette` is in `requirements.txt` and **imported nowhere**; both streams are
  raw `StreamingResponse`. Drop it.
- `PYPSA_GUI_*` env vars collide with PyPSA's own `PYPSA_*` option namespace and
  already print `Unknown option` warnings on every boot. On a Windows GUI build with
  no console, writing to a closed stderr can raise. Rename to `PYPSAGUI_*`.
- `src/pages/IssuesPanel.tsx:179` renders a `pixi run … uvicorn` command as user help.
  Nonsense in a frozen build.
- `src/pages/OverviewPanel.tsx:147` uses `window.open` for bundle export. In pywebview
  this either does nothing or opens the system browser at an unauthenticated URL —
  project export is broken and needs a native save dialog.
- `src/pages/MapCanvas.tsx:182-191` uses ArcGIS tile URLs. The map is blank offline
  with no fallback.

---

## 6. Workstreams

Each is independently verifiable. A–C can be validated in the dev environment before
any packaging exists.

### A. Serve the SPA from the backend — 2.5–3 d
1. Port `decideGateRoute` from `vite.auth-gate.ts` into a FastAPI catch-all mounted
   after all routers, at document root.
2. Internal rewrite (not redirect): `/assets/*`, `/brand.css`, `/img/*`, `/favicon*` →
   static; `/` and `/login` → `index.html` when anonymous; everything else non-`/api`
   → `spa.html`.
3. In local mode, always serve `spa.html` at `/`.
4. Guard against the `spa.html:46` redirect loop.
5. Test against the built `dist/` served by FastAPI — never via `npm run preview`.

### B. Local mode — 3–4 d
1. `PYPSAGUI_LOCAL_MODE` setting.
2. Branch inside `main.py:232-262`: re-fetch the seeded user per request from the
   request's own session; skip the 401; short-circuit `_csrf_rejection`.
3. `/api/health` returns `auth_enabled: false` in local mode.
4. Seed on first run: `Organization` + `User` + `OrgMembership(role="admin")`,
   `is_super_admin=True`, `status="active"`, explicit `created_at` (NOT NULL, no
   default), idempotent select-then-insert. Extend `tools/bootstrap_super_admin.py`.
5. Frontend: suppress the `client.ts:133-139` 401 ratchet; synthesize a local admin
   user in `AuthProvider`; hide sign-out and the user menu.
6. Do not mount `routers/admin.py`; disable the replica header and login throttle.

### C. CSRF and origin — 1–1.5 d
1. Shell sets `CORS_ALLOWED_ORIGINS` to the chosen origin before `import main`.
2. Axios request interceptor copying `pypsa_gui_csrf` → `X-CSRF-Token`.
3. Same for the six raw `fetch`/`sendBeacon` mutation sites.
4. Regression test asserting a mutation succeeds on a non-5173 origin.

*This workstream also fixes the live web-mode bug in §5.6.*

### D. Path unification and app-data — 2–3 d
1. `app_data_dir()` helper (`~/Library/Application Support/PyPSA GUI`, `%LOCALAPPDATA%`).
2. `routers/projects.py:48` `PROJECTS_DIR` → a function reading `get_settings().projects_root`.
3. `settings.legacy_root` gets an env override; `settings.database_url` default becomes
   an absolute SQLite path under app-data.
4. `chat_service.get_persist_path` resolves from `ctx.storage_dir`, flat path as fallback.
5. Shell sets `DATABASE_URL`, `PROJECTS_ROOT`, `LEGACY_ROOT` before `import main`.
6. Assert in a test that no writable path resolves inside the bundle.

### E. Storage model — 3–4 d
1. Human-readable project directories, with Windows sanitising (reserved names
   `CON`/`PRN`/`AUX`/`NUL`, trailing dots and spaces, 260-char paths, case-insensitive
   collisions) and rename handling.
2. `Project.storage_path` stored **relative** to `projects_root`; joined on read;
   one-shot rebase migration for existing rows.
3. `tools/reconcile_storage`: on startup, import orphan directories containing a
   `network.nc`, and flag rows whose path is gone.
4. Sweep orphan `.tmp` siblings left by interrupted atomic writes.
5. Route bundle-import and snapshot-create through `_atomic_write_with` (both are
   currently direct truncating writes).

### F. Migration of existing projects — 2 d
1. First-run detection of a legacy `backend/projects/` tree.
2. Copy flat directories into the new root, insert rows directly, preserve
   `parent_project` from each `metadata.json`.
3. Do **not** use the export bundle as the channel — it drops `snapshots/` and
   `chat.jsonl` and flattens scenario lineage.
4. Idempotent and resumable; report what was imported and what was skipped.

### G. SQLite hardening — 1–1.5 d
1. `NullPool` (or `pool_size=1`), `timeout=30`, and a `connect` listener issuing
   `PRAGMA journal_mode=WAL; synchronous=NORMAL; busy_timeout=30000` alongside the
   existing `foreign_keys=ON`.
2. `render_as_batch=True` in both `context.configure` calls in `alembic/env.py`.
3. First run does `alembic upgrade head` on an empty file — never `create_all`.
4. `alembic stamp head` for pre-existing dev DBs.
5. Replace the bare `except Exception` → 503 with a message that makes sense locally.
6. Note or fix the silently-dropped `.with_for_update()`.

### H. Desktop shell and lifecycle — 3–4 d
1. Single-instance lock (mandatory: `_active` is process-global and `currentProject`
   lives in shared `localStorage`).
2. Free-port bind; env setup; `MPLBACKEND=Agg`; import; first-run bootstrap.
3. `uvicorn.Server` with the app **object** on a worker thread; `should_exit` +
   join for shutdown (no signal handlers exist off the main thread).
4. Splash with progress, 6–10 s budget.
5. Window-close: confirm if a solve is running → abort both solve paths and wait →
   flush all resident contexts → release project locks →
   `_TOOL_EXECUTOR.shutdown(wait=False, cancel_futures=True)` → stop server → exit.
6. Native save dialog for bundle export, replacing `window.open`.

### I. Packaging — 4–6 d
1. `gui-requirements.txt`: the ~15 packages `import main` actually needs, pinned,
   installed from PyPI wheels into a dedicated build venv.
2. PyInstaller `.spec` per platform: `copy_metadata`, hidden imports (uvicorn,
   xarray backends, highspy, netCDF4, unpickler targets), explicit `datas` allowlist,
   defensive `cartopy` exclude.
3. Free size wins: drop `pydeck/nbextension/static/index.js.map` (18 MB),
   `plotly/labextension` (4.6 MB), `plotly/package_data/widgetbundle.js` (5 MB).
4. Smoke test the frozen build on a clean machine, not the dev box.

### J. Installers — 1.5–2 d
1. Windows: Inno Setup, per-user install (no admin), WebView2 Evergreen bootstrapper.
2. macOS: `.app` bundle in a DMG.
3. Unsigned per D8. Document the Gatekeeper right-click-Open and SmartScreen bypass.
4. Version metadata and bundle layout structured for a later updater (D9).

### K. Chatbot key — 1–1.5 d
1. Settings field; store via `keyring` with a config-file fallback (0600).
2. Wire into `chat_service`; the panel already degrades to a disabled state without a key.

### L. Tests and CI — 3–4 d
1. Local-mode test module; keep the 541-test auth suite green (nothing is deleted).
2. Both modes in CI per D7, or local mode rots.
3. Frozen-build smoke: launch → create project → solve → export → close cleanly.
4. GitHub Actions matrix: `windows-latest`, `macos-14`.

---

## 7. Estimate

| Workstream | Days |
|---|---|
| A Serve SPA | 2.5–3 |
| B Local mode | 3–4 |
| C CSRF/origin | 1–1.5 |
| D Path unification | 2–3 |
| E Storage model | 3–4 |
| F Migration | 2 |
| G SQLite | 1–1.5 |
| H Shell and lifecycle | 3–4 |
| I Packaging | 4–6 |
| J Installers | 1.5–2 |
| K Chatbot key | 1–1.5 |
| L Tests and CI | 3–4 |
| **Total** | **27–37 days** |

Roughly **5.5–7.5 weeks** for one developer. The original 17–24 day estimate was made
before the verification pass and did not account for the SPA-serving subsystem, the
dual project roots, absolute stored paths, the shutdown/flush requirement, or the
true dependency closure.

Suggested order: D → G → B → C → A (a working local web app in the dev environment,
verifiable end to end) → E → F → H → I → J → K, with L continuous.

This splits cleanly into two phases if you want a checkpoint before committing to
packaging:

- **Phase 1 (A–G, ~13–17 d)** — the app runs locally with no login, SQLite, relocated
  user-visible storage, and migrated projects. Still launched from the dev environment.
  Everything here is independently useful and testable, and it is where all the
  correctness risk lives.
- **Phase 2 (H–L, ~13–20 d)** — shell, freeze, installers, key handling, CI. Pure
  delivery. If Phase 1 reveals the effort is not worth it, nothing in Phase 2 has
  been spent.

## 8. Risks

| Risk | Mitigation |
|---|---|
| Frozen build works on the dev box, fails on a clean machine | Pip-wheel venv (D14); mandatory clean-machine smoke test in CI |
| Human-readable names collide or hit Windows path limits | Sanitiser with a test matrix of reserved names, unicode, and long paths |
| Migration corrupts or loses dev projects | Copy, never move; idempotent; verify before deleting anything |
| Local mode rots because CI only covers web mode | L2 is not optional |
| Interrupted solve leaves a corrupt network | H5 ordering; the abort path is already sound (verified) |
| ~500–600 MB install rejected by users | Accepted per D15; §5.10 trim path documented if revisited |
| Unsigned build blocked by IT policy | D8 revisit; signing is additive, no rework |

## 9. Open items

1. Windows measurements are unverified — §5.10 and §5.11 are macOS arm64 only.
   Repeat on Windows before committing to workstream I's estimate.
2. Offline behaviour of `MapCanvas` (ArcGIS tiles) — decide whether v1 ships a blank
   map or a bundled fallback.
3. Whether to fix the live web-mode CSRF bug (§5.6) as a separate hotfix ahead of
   this work, since it affects the current deployment.

---

## 10. Conflict with in-flight Step 0b work

At the time of writing (2026-07-26 23:12) another working session is **actively
editing master**. The changes are uncommitted and the files were being written
minute-by-minute during this spec's verification pass.

**What it is:** *Step 0b of the cloud/SaaS migration — session-bound active project.*
It moves the "active project" pointer out of a process global and into
`sessions.active_project_id`, and gives each session its own scratch context keyed
`scratch:<session-id>` for the unbound (New Project) case. Its own docstring
describes it as a precondition for Step 3, which moves the resident
`pypsa.Network` payload out of process memory.

**Files affected:** `db/models.py`, `deps.py`, `main.py`, `routers/network.py`,
`routers/projects.py`, `services/auth_service.py`, `services/pypsa_service.py`
(+190 lines), plus new `services/active_project.py`,
`alembic/versions/0002_session_active_project.py`, `tests/test_qa_step0b.py`.

That set overlaps almost exactly with the files this spec depends on.

### Review of 2026-07-27

Still uncommitted (`master` at `1d930244`). The change grew from 11 files to 20 —
now also `routers/simulation.py`, `services/solve_queue.py`, and seven test files —
and has been idle since 23:22 the previous evening.

**How it works.** `bind_active_project` is registered as an **app-level async
dependency** (`main.py`, `dependencies=[Depends(bind_active_project)]`), with a
middleware-local twin inside the auth middleware. It resolves the caller's session
row from the cookie, calls `active_project.resolve_for_session(db, session)`, and
binds the result into `PyPSAService._request_ctx`, a `ContextVar`.
`PyPSAService._ensure_active` prefers that binding over the process foreground.

### CORRECTION to the previous impact assessment

**Points 1 and 2 below were wrong, and the error mattered.** They claimed local mode
would have to seed a `Session` row and mint its cookie, which would in turn make the
CSRF work mandatory. Reading the implementation shows the opposite:

```python
# services/pypsa_service.py:235-254
scoped = cls._request_ctx.get()      # ContextVar, default=None
if scoped is not None:
    return scoped
if cls._active is None: ...
return cls._active                   # process foreground
```

`bind_active_project` returns early when there is no session cookie — its own
docstring: *"a route that has no session simply gets no binding and falls through to
the process foreground."* Verified: no session → contextvar stays at its `None`
default → `_ensure_active` returns `cls._active`, exactly the pre-0b behaviour.

**Consequences, all in the simplifying direction:**

1. **D4 and §5.1 survive unchanged.** Local mode stays session-less: inject
   `request.state.auth_user`, never set a cookie. The entire 0b layer is inert
   without one, which is correct for a single-user desktop app — the process
   foreground *is* the right answer when there is exactly one user.
2. **CSRF stays exempt.** No session cookie means `_csrf_rejection` returns `None`
   at its first check. Workstream C remains "required for the web deployment,
   which is separately broken (§5.6)" rather than a local-mode blocker.
3. **Per-session scratch contexts are not needed locally.** `scratch:<session-id>`
   slots are only created inside `resolve_for_session`, which local mode never calls.

### Re-verification of §5

All §5 findings hold against the Step 0b tree. Line numbers updated where they moved:

| Finding | Then | Now |
|---|---|---|
| auth assignment / 401 | `main.py:239` / `:256` | `main.py:248` / `:267` |
| `optional_user` cache hook | `deps.py:24-27` | `deps.py:112-113` |
| `RESIDENT_CAP` | `pypsa_service.py:51` | `pypsa_service.py:52` |
| `PROJECTS_DIR` second root | `projects.py:48` | unchanged |
| `storage_path` stored absolute | `project_registry.py:171,207` | unchanged |
| CORS/CSRF origin default | `settings.py:23` | unchanged |
| chat executor never shut down | `chat_service.py:227` | unchanged |
| `lifespan` has nothing after `yield` | `main.py` | unchanged |
| SQLite `check_same_thread` only | `db/session.py:40,44` | unchanged |

`RESIDENT_CAP = 5` and the daemon solver threads are both still present, so §5.7's
shutdown-flush requirement is unaffected.

**Positive overlap.** Migration `0002` uses `batch_alter_table` for SQLite and its
docstring names SQLite "the documented local fallback" — partly discharging G2 and
confirming SQLite compatibility is already a project norm.

### Defect shipped in Step 0b (not ours to fix)

Three endpoints take `current_session` and write the pointer: `save_project`
(`projects.py:1075`), `activate_project` (`:1694`), `reset_network`
(`network.py:1733`). Five siblings that also swap the network context do **not**:
`create_from_template`, `import_bundle`, `import_unclaimed_project`, `load_project`,
`create_scenario`. **Verified against `09bd7020` as committed**, not against the draft.

`resolve_for_session` does not compensate — it resolves purely from
`sessions.active_project_id`. So on the next request the stale pointer re-resolves
and re-hydrates the previous project, which is the failure the patch author documents
for `reset_network`: the operation "would appear to silently undo itself."
(The mechanism is Verified; the end-to-end symptom is Assumed — not reproduced.)

Severity is higher than "unfinished work":
- `tests/test_qa_step0b.py` has **zero** references to any of the five.
- The frontend calls three of them in normal use: `createFromTemplate`
  (`frontend/src/api/projects.ts:233`), `import_bundle` (`:244`), and the scenario
  fork (`:127`).
- The commit message does not list them as follow-up.

Still **web-mode only** — all five need a session to misbehave, and local mode never
has one. Does not block this spec. Belongs to the cloud/SaaS workstream.

### Decision

**Unblocked.** Step 0b committed as `09bd7020` on 2026-07-27. The working tree is
clean, the premise survives, and every line reference in §5 and in the phase-1a plan
was re-verified against the committed code — `main.py:248`/`:267`, `projects.py:48`,
`pypsa_service.py:52`, `main.py:552` all unchanged from the draft review.

Implementation may begin. Task 0 of the phase-1a plan re-runs the concurrency check
regardless — cheap, and the repo has two other active worktrees.
