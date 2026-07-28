# Local Desktop App — Phase 2a (workstream H): shell and lifecycle

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A native window that launches the backend in-process, shows progress while it boots, and — the part that carries all the risk — **shuts down without losing the user's unsaved work or hanging**.

**Architecture:** A `desktop/` entrypoint that sets the environment, binds a port, imports `main`, runs `uvicorn.Server` on a worker thread, and opens a pywebview window at `http://127.0.0.1:<port>/`. No subprocess. The solver already runs on a thread.

**Tech Stack:** pywebview (WKWebView / WebView2), uvicorn, the existing FastAPI app object.

**Builds on:** phase 1a (local mode, SPA served same-origin) and phase 1b (readable relative storage, importer), landed on `feature/local-app-impl`.

**Scope:** Workstream H only. Packaging (I), installers (J), the API-key setting (K) and CI (L) are re-planned once this exists — freezing is much easier to reason about against a shell that runs.

---

## Decisions taken before planning

| # | Decision |
|---|---|
| 1 | **pywebview goes in `pixi.toml`; PyInstaller does not.** The shell must be runnable and testable in the normal dev environment. PyInstaller stays in a throwaway pip-wheel venv per D14, because freezing the conda env is what produces a build that works here and fails on a clean machine. |
| 2 | **~500–600 MB install accepted** (D15). Free wins only, and they belong to workstream I. |
| 3 | **Plan H alone**, then re-plan. |

---

## Verified constraints

Checked at `07b3427e`. **Every line number below was re-checked at that commit** — phase 1b's own review found nine stale citations, most shifted by the commits that wrote them.

| # | Fact | Why it matters |
|---|---|---|
| 1 | **Spec §5.5 is STALE for local mode.** It says an ephemeral port yields `403 csrf_origin_rejected` on every mutation. Phase 1a made `_csrf_rejection` return `None` immediately in local mode, and phase 1a serves the SPA same-origin so CORS never engages. **Probed at `07b3427e`:** a `PUT` from an unlisted `http://127.0.0.1:49999` origin returns **404 (project not found)**, not 403. | The free-port bind does **not** need the `CORS_ALLOWED_ORIGINS`-before-import dance in local mode. Task 2 still sets it — one line, and it keeps web mode honest — but the plan must not claim it as load-bearing, and Task 2 asserts the real behaviour rather than the spec's. |
| 2 | `PyPSAService._contexts` is capped at `RESIDENT_CAP = 5` (`pypsa_service.py:35`, overridable via `PYPSA_GUI_RESIDENT_CAP`); `_active` at `:24`. The only path that persists a NON-active context is LRU eviction (`_save_evicted_ctx`, `:658`) | Up to five projects can hold unsaved edits at quit. This is the data-loss surface H5 exists for. |
| 3 | **Two solve paths.** `POST /api/simulation/abort` sets only the active context's stop event; queue jobs run on the `solve-queue-dispatcher` thread with their own (`solve_queue.py:88,163,206`) | Aborting one leaves the other running. Both must be signalled and waited on. |
| 4 | Solver threads are `daemon=True`. A hard quit skips their `finally:`, so `restore_modelling()` never runs | The saved network keeps transient vintage rows, slack generators and dispatch-fix `p_set` overrides. Quitting mid-solve without aborting **corrupts the project**. |
| 5 | `chat_service.py:227` creates a module-level `ThreadPoolExecutor` that is never shut down. CPython's atexit joiner blocks interpreter exit until every worker returns | Without `shutdown(wait=False, cancel_futures=True)` the app hangs on quit with the window already gone. |
| 6 | uvicorn's `capture_signals` returns early off the main thread | No signal handlers exist. Shutdown is `server.should_exit = True` then join — nothing else works. |
| 7 | `project_locks.release_all_for_user` (`project_locks.py:97`) exists and is what logout uses | Quitting without it leaves the user's own locks held until they expire. |
| 8 | `frontend/src/pages/OverviewPanel.tsx:147` exports a bundle via `window.open('/api/projects/…/bundle')` | In a webview with no download UI this is a dead button. H6. |
| 9 | Measured save cost on real projects: 0.37 s and 0.79 s; a full flush is 1–5 s. Startup to `/api/health` 200 is **2.30 s** warm (spec §5.11) | The splash budget is 6–10 s and the quit confirm must not look hung. |
| 10 | Phase 1b's `run_first_run_import` already runs inside `lifespan` and never blocks startup | The shell inherits first-run import for free; it must not re-implement it. |

---

## Global constraints

- **Both modes must keep working.** Nothing in `desktop/` may be imported by `main`.
- **Set the environment BEFORE `import main`.** `get_settings()` and `security.allowed_origins()` are `lru_cache`d.
- **Never hardcode an interpreter path** (CLAUDE.md): `pixi run …`.
- **The backend test suite must stay green and must not import pywebview.** A headless CI box has no webview.
- **`MPLBACKEND=Agg` before `import pypsa`.** matplotlib otherwise resolves `macosx`, and initialising it off the main thread while pywebview owns the Cocoa run loop is a crash.
- **Serialize strictly: edit → gate → commit → next task.**

---

## Execution order

```
0  pixi dependency + baseline               ← nothing else can be tested first
1  single-instance lock                     ← independent, lands immediately
2  launcher: env, free port, import main
3  threaded uvicorn + health wait
4  ordered shutdown                         ← the whole point of the workstream
5  splash with progress
6  native save dialog for bundle export
7  acceptance
```

Task 4 is the one with real correctness risk. Tasks 1–3 exist to make it reachable.

---

## Task 0: Dependency and baseline

- [ ] **Step 1: Add pywebview to `pixi.toml`.** It is a pip package; add it under `[pypi-dependencies]`, not `[dependencies]` — conda-forge's build lags and D14 wants the pip wheel anyway.

- [ ] **Step 2: Verify it imports and can name a GUI backend**

```bash
pixi run python -c "import webview; print(webview.__version__)"
```

- [ ] **Step 3: Baseline.** Expect **1459 backend tests** green, frontend **23 files / 147 tests**. Record the real numbers; do not quote these.

- [ ] **Step 4: Commit** the manifest change alone, so a dependency bisect is one commit.

---

## Task 1: Single-instance lock

**Files:** Create `pypsa-gui/desktop/single_instance.py`, `pypsa-gui/backend/tests/test_single_instance.py`

**Context:** D11 makes this mandatory, and constraint #2 is why: `_active` is process-global and the frontend keeps `currentProject` in shared `localStorage`, so two windows fight over one pointer and one in-memory network.

**This is NOT the same lock as phase 1b's import lock** (`main.py::_import_lock_path`). That one guards a 113 MB copy for the duration of one import; this one is held for the life of the process. Reusing it would make a second launch skip the import *and* run.

- [ ] **Step 1: Write the failing test** — acquire succeeds; a second acquire in the same process fails; release then re-acquire succeeds; a lock whose recorded pid is not running is reclaimed; a lock file containing garbage is reclaimed rather than wedging every future launch (phase 1b shipped exactly that bug in `_ledger` — a shape-valid file that crashed the reader forever).

- [ ] **Step 2: RED**, then implement with `O_EXCL` plus a pid, in `app_data_dir()`.

`os.kill(pid, 0)` is **not** portable — it raises on Windows. Use it under `sys.platform != "win32"` and fall back to an mtime staleness window elsewhere, the way `main.py::_LOCK_STALE_SECONDS` already does.

- [ ] **Step 3: Gate and commit.**

---

## Task 2: The launcher — environment, free port, import

**Files:** Create `pypsa-gui/desktop/__init__.py`, `pypsa-gui/desktop/launcher.py`, `backend/tests/test_desktop_launcher.py`

- [ ] **Step 1: Write the failing test.** Assert, without importing `webview`:
  - `build_environment(port)` returns a mapping containing `PYPSAGUI_LOCAL_MODE=1`, `MPLBACKEND=Agg`, and a `CORS_ALLOWED_ORIGINS` naming **the bound port**;
  - it does **not** set `DATABASE_URL` or `PROJECTS_ROOT` — `app_paths` already derives those, and phase 1b's `.env` incident came from a manual run that set one and not the other;
  - `bind_free_port()` returns a port that is actually bound, and the socket is handed to uvicorn rather than closed and re-opened (a closed port is a race, not a reservation).

  **Do not assert the 403 the spec predicts.** Constraint #1: local mode short-circuits the origin check and the SPA is same-origin, so an unlisted port returns 404 for an unrelated reason. Assert instead that a mutation from the bound origin **succeeds**, which is the property that matters and is true for the right reason.

- [ ] **Step 2–4:** RED, implement, gate, commit.

---

## Task 3: uvicorn on a worker thread

**Files:** Modify `desktop/launcher.py`; create `backend/tests/test_desktop_server.py`

- [ ] **Step 1: Write the failing test** — `serve_in_thread(app, port)` returns a handle whose `wait_healthy(timeout)` returns True once `/api/health` answers; `stop()` sets `should_exit` and joins within a bound; `wait_healthy` returns False rather than hanging when the server never starts.

- [ ] **Step 2: Implement.** `uvicorn.Config(app_object, …)` — **the app OBJECT, never `"main:app"`**. A frozen build has no importable module path, and this is the single most common PyInstaller failure for uvicorn.

Constraint #6: no signal handlers exist off the main thread. `stop()` is `should_exit = True` then `thread.join(timeout)`.

- [ ] **Step 3: Gate and commit.**

---

## Task 4: Ordered shutdown

**Files:** Create `pypsa-gui/backend/services/shutdown.py`, `backend/tests/test_shutdown.py`; modify `desktop/launcher.py`

**This is the task the workstream exists for.** Constraints #2–#7. The sequence is not arbitrary — each step is ordered against a specific failure:

```
1. is a solve in flight?        both paths (#3), or the user is asked the wrong question
2. abort and WAIT               daemon threads skip `finally:` (#4) -> corrupt network
3. flush resident contexts      up to five hold unsaved edits (#2)
4. release project locks        (#7)
5. _TOOL_EXECUTOR.shutdown      atexit joiner blocks exit otherwise (#5)
6. server.should_exit + join    no signal handlers (#6)
```

Flush **before** the executor shutdown: a chat tool may still be writing into a project directory.

- [ ] **Step 1: Write the failing test.** `solves_in_flight()` sees a queue job AND an active-context solve — one test per path, because a single `or` looks correct while covering one. `flush_all()` saves every resident context and returns what it saved; a context that raises does not prevent the others being saved (a corrupt project must not strand four healthy ones); `shutdown_sequence()` calls the six steps in order — assert the ORDER, with a recording double, not just that each ran.

- [ ] **Step 2–4:** RED, implement, gate, commit.

`services/shutdown.py` lives in the backend, not `desktop/`, so the backend suite covers it with no webview import.

---

## Task 5: Splash with progress

**Files:** Create `pypsa-gui/desktop/splash.py`; modify `desktop/launcher.py`

**Context:** 2.30 s warm to health (#9), 6–10 s cold budget. `import pypsa` alone is 1.55–1.60 s and happens before anything can be displayed — so the splash must be up **before** `import main`, which means it cannot be a webview window served by the backend.

- [ ] **Steps:** a minimal pywebview window (or the platform's native splash) showing staged progress — "Starting…", "Loading PyPSA…", "Opening your projects…" — closed when `wait_healthy` returns. Test the STAGE SEQUENCE as data; the window itself is not unit-testable and should not be faked into looking tested.

---

## Task 6: Native save dialog for bundle export

**Files:** Modify `frontend/src/pages/OverviewPanel.tsx:147`; create the JS-API binding in `desktop/launcher.py`

**Context:** Constraint #8 — `window.open` on a bundle URL is a dead button in a webview with no download chrome.

- [ ] **Steps:** expose a `save_bundle(name)` through pywebview's JS API, call `window.pywebview.api.save_bundle(...)` when it exists and fall back to `window.open` when it does not — so the web deployment is untouched and the browser dev workflow keeps working. Frontend test asserts the fallback.

---

## Task 7: Acceptance

- [ ] **Step 1:** both suites green against the Task 0 baseline plus this phase's additions.
- [ ] **Step 2:** launch the real shell. Window opens, projects list, one opens and returns buses.
- [ ] **Step 3:** **the quit test that matters** — open a project, make an edit, close the window, relaunch, and assert the edit survived. This is the only check that proves H5 rather than describing it.
- [ ] **Step 4:** start a solve, close the window, confirm the prompt appears, confirm quit completes within a bound, relaunch and assert the project is not carrying transient solver rows (constraint #4).
- [ ] **Step 5:** second launch while the first is open — refused, first window focused.
- [ ] **Step 6:** confirm the process actually exits (`ps`), not just that the window closed.

---

## Rollback

Every task is additive under `pypsa-gui/desktop/` plus one backend service and one frontend line. `git revert` the range; the web deployment never imported any of it.

---

## Residual risk, stated up front

- The splash cannot cover `import pypsa` unless it is drawn by a separate mechanism from the main window; if that proves awkward on one platform, the honest fallback is a longer blank-window period, not a fake progress bar.
- `flush_all()` is bounded by measured save cost (#9) but a pathological project could exceed it. The quit path needs a cap and a "still saving…" message rather than an unbounded wait.
- pywebview's macOS and Windows backends differ in how they report window-close; Task 4's sequence must be driven by an explicit handler, not by process exit.
- Nothing here is frozen yet. Everything in workstream I can still invalidate an assumption made in this plan — which is exactly why H is planned and executed alone.
