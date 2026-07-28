# Local Desktop App — Phase 2a (workstream H): shell and lifecycle

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A native window that launches the backend in-process, shows honest progress while it boots, and — the part that carries all the risk — **quits without losing the user's unsaved work and without hanging**.

**Architecture:** A `backend/desktop/` entrypoint that sets the environment, binds a socket, imports `main`, runs `uvicorn.Server` on a daemon thread, and opens a pywebview window at `http://127.0.0.1:<port>/`. No subprocess.

**Tech Stack:** pywebview (WKWebView / WebView2), uvicorn, the existing FastAPI app object.

**Builds on:** phases 1a and 1b, landed on `feature/local-app-impl`.

**Scope:** Workstream H only. I–L are re-planned once this exists.

---

## Revision log

**v2 (2026-07-28)** — v1 was REJECTed by two independent reviewers. Every finding was re-verified against the code before it was applied.

| # | v1 defect | v2 |
|---|---|---|
| 1 | **`os.kill(pid, 0)` was proposed as the Windows liveness probe.** v1 claimed it "raises on Windows". Python documents the opposite: any signal but the two CTRL events **unconditionally terminates the process via `TerminateProcess`**. A second launch would have KILLED the running app, skipping the daemon threads' `finally:` — the corruption constraint #4 exists to prevent. | Kernel-released lock: `fcntl.flock` / `msvcrt.locking`, held for the process lifetime. No pid probe, no staleness heuristic — a lock that dies with its process cannot go stale. |
| 2 | **`pypsa-gui/desktop/` is not importable from `backend/tests/`.** `conftest.py:24-25` inserts only `backend/`; `pytest.ini` sets no `pythonpath`. Tasks 1–3's first RED step would have been `ModuleNotFoundError`. | The shell lives at **`pypsa-gui/backend/desktop/`**. Zero path plumbing, and workstream I gets one `pathex` root. |
| 3 | **Constraint #4's failure model was wrong.** v1: a hard quit mid-solve "corrupts the project" with transient solver rows. `_apply_modelling_assumptions` mutates memory only, `atomic_io` makes a killed export leave the previous file intact, and `routers/projects.py:1236` **refuses to save at all** while a worker lives. A hard quit LOSES THE SOLVE. | The real hazard is the inverse and worse: if the abort does not complete, step 4's flush hits that 409 and **the user's unsaved edits are silently dropped while the app reports a clean quit**. Task 4 gets an explicit abort-timed-out branch. |
| 4 | **`should_exit` + join is unbounded.** `Server.shutdown` awaits `_wait_tasks_to_complete()` with `timeout_graceful_shutdown` defaulting to `None`, and `simulation.py:846-946` holds an SSE stream open until the client disconnects — which it has not, because the window is still open. | `timeout_graceful_shutdown` set, server on a **daemon** thread, then `should_exit` → bounded join → `force_exit` → bounded join → `os._exit(0)`. |
| 5 | **Nothing implemented D12's confirm, the close handler, or re-entrancy.** Task 7 asserted "the prompt appears" against a prompt no task created, and a second close click would have started a concurrent `shutdown_sequence()` that killed the server mid-flush. | Task 4 builds the handler, and the sequence is idempotent behind a lock. |
| 6 | **`flush_all()` would have silently lost data either way it was written.** `persist_user_ts` is per-context: `_save_evicted_ctx` passes `False` (`pypsa_service.py:694`), `solve_queue.py:468` passes `(ctx is PyPSAService._active)`. Copy the first and the active project's uploaded time series are not written; hardcode `True` and the foreground's profiles are stamped onto every other project. `_save_evicted_ctx` also calls `chat_service.flush_to_disk` (`:707`) — a hand-rolled flush drops the transcript. | The exact primitive is spelled out, with a test per branch. |
| 7 | **There are THREE solve paths, not two — and one cannot be aborted.** `run_ac_pf` creates a `stop_event` (`simulation.py:704`) that **nothing reads**: `grep -c stop_event services/ac_pf_service.py` → **0**. | Three paths, per-path bounded wait, and AC PF gets an explicit "cannot be interrupted" decision. |
| 8 | **The HTTP surface stayed live through steps 1–5.** Autosave, lock heartbeat and four polling intervals keep firing during the flush; a chat request arriving after the executor shutdown raises `RuntimeError: cannot schedule new futures after shutdown`. | Quiesce is step 1. |
| 9 | **Constraint #10 was wrong twice.** `run_first_run_import()` is synchronous before `yield` (`main.py:282`), so it blocks READINESS by a 113 MB `copytree`; and it does not run at all unless the shell sets `PYPSAGUI_LEGACY_IMPORT_ROOT`, which v1's `build_environment` did not. | Both corrected. The import splash stage polls; it has no timeout. (The misleading comment in `main.py` that caused this was fixed in `34863bf2`.) |
| 10 | **Constraint #1's conclusion was backwards on security.** v1 said the CORS allowlist is "not load-bearing". Local mode short-circuits CSRF *and* injects the user with no cookie, so CORS is the ONLY barrier left on the loopback API — and the default is `localhost:5173`, the Vite dev server every developer here runs. | The 403 observation stands; the conclusion is flipped. Task 2 asserts the allowlist is EXACTLY the bound origin. |
| 11 | Task 6 fixed 1 of 13 broken downloads with a per-feature bridge. | One `saveFile` chokepoint, gated on `pywebviewready`. |
| 12 | Task 0 rewrote the shared `pixi.lock` with no concurrency check, unscoped to 4 platforms, and told the engineer to commit the manifest without the lock. | Concurrency check first; pywebview behind its own pixi **feature/environment** so the default env PyPSA-Eur solves with is not re-solved at all. |
| 13 | Citations: `RESIDENT_CAP` is `pypsa_service.py:52`, not `:35`. The claim "the only path that persists a non-active context is LRU eviction" is **false** — `solve_queue.py:466-471` does too. `:206` is a test-only helper; the dispatcher is `:234`. Baseline is **1460**, not 1459. Task 2's "does not set `PROJECTS_ROOT`" names the wrong variable (`PYPSAGUI_PROJECTS_ROOT`). | All corrected. |
| 14 | Task 2 proposed a test phase 1a already ships: `tests/test_dynamic_origin.py:134`. | Extend that file, do not duplicate it. |

---

## Decisions taken before planning

| # | Decision |
|---|---|
| 1 | **pywebview goes in a pixi FEATURE, not the default environment.** PyInstaller stays in a pip-wheel venv per D14. |
| 2 | **~500–600 MB install accepted** (D15). Free wins belong to workstream I. |
| 3 | **Plan H alone**, then re-plan. |

---

## Verified constraints

Checked at `34863bf2`. Every citation below was re-derived by a reviewer, not copied from v1 — v1 shipped two wrong ones and one false claim, and phase 1b shipped nine.

| # | Fact | Why it matters |
|---|---|---|
| 1 | **Spec §5.5 is STALE for local mode**, and phase 1a already tests it: `tests/test_dynamic_origin.py:134`. `_csrf_rejection`'s first statement is `if local_mode.is_local_mode(): return None` (`main.py:324`). A mutation from an unlisted ephemeral origin **succeeds** (`POST /api/network/reset` → 200). | No CORS-before-import dance is needed to make mutations work. **But see #2** — the allowlist matters for a different reason. |
| 2 | **In local mode the loopback API is unauthenticated and CORS is the only barrier.** CSRF returns `None`; local mode injects the user with no cookie. `cors_allowed_origins` defaults to `http://localhost:5173,http://127.0.0.1:5173` (`settings.py:33`) — the Vite dev server every developer here runs | If the shell leaves the default, any page on 5173 can drive the desktop app, which holds the user's real `ANTHROPIC_API_KEY`. Task 2 asserts the allowlist is EXACTLY the bound origin. |
| 3 | `run_first_run_import()` is called **synchronously at `main.py:282`, before `yield`**, and uvicorn accepts no connection until lifespan startup returns. It runs only when `PYPSAGUI_LEGACY_IMPORT_ROOT` is set (`main.py:199-203`, `settings.py:69-71`) | The first launch that matters blocks readiness by a `copytree` of the whole legacy tree. The splash's import stage must POLL, not time out. And the shell must set the variable or D10 is silently unimplemented. |
| 4 | **A hard quit mid-solve LOSES THE SOLVE; it does not corrupt the project.** `_apply_modelling_assumptions` mutates memory only; `atomic_io` leaves the previous file intact on a killed export; and `routers/projects.py:1236` **refuses** (`409`) to save while `_solver_in_flight_ctx(ctx)` | The real hazard: if the abort does not complete, the flush is refused and unsaved edits are dropped **while the app reports a clean quit**. |
| 5 | **THREE solve paths.** `/api/simulation/abort` sets the active context's event; queue jobs have their own (`solve_queue.py:88,163`; dispatcher at `:234`); and `run_ac_pf` creates an event at `simulation.py:704` that **nothing reads** — `services/ac_pf_service.py` contains zero occurrences of `stop_event` | Two must be aborted and waited on; the third **cannot be interrupted** and needs its own decision. |
| 6 | `PyPSAService._contexts` (`pypsa_service.py:35`) capped by `RESIDENT_CAP` (**`:52`**, env `PYPSA_GUI_RESIDENT_CAP`); `_active` at `:24`. Non-active contexts are persisted by LRU eviction (`_save_evicted_ctx`, `:658`) **and** by the solve queue (`solve_queue.py:466-471`) | Up to five projects can hold unsaved edits at quit. |
| 7 | `_save_evicted_ctx` passes `persist_user_ts=False` (`:694`); `solve_queue.py:468` passes `(ctx is PyPSAService._active)`. It also calls `chat_service.flush_to_disk` (`:707`) | Copying either blindly loses data — see revision-log row 6. |
| 8 | `chat_service.py:227` creates a module-level `ThreadPoolExecutor` never shut down; CPython's atexit joiner blocks interpreter exit. `cancel_futures=True` cancels only PENDING work — a running tool thread is still joined | Necessary, not sufficient. Pair with the bounded exit in #9. |
| 9 | uvicorn's `capture_signals` returns early off the main thread, AND `Server.shutdown` awaits `_wait_tasks_to_complete()` with `timeout_graceful_shutdown` defaulting to **`None`**. `simulation.py:846-946` holds an SSE stream until the client disconnects | `should_exit` alone can wait forever. Bounded escalation is mandatory. |
| 10 | `Server.run/serve/startup` all accept `sockets: list[socket.socket]` and call `loop.create_server(..., sock=sock)` | The socket handoff in Task 2 is achievable; a close-then-rebind would be a race. |
| 11 | 13 broken download sites, not 1: `OverviewPanel.tsx:147` (`window.open`) plus **12** `createObjectURL` sites across `utils/projectActions.ts`, `layout/Sidebar.tsx`, `pages/TimeSeriesManager.tsx`, `pages/results/shared.tsx`, `pages/LoadProfileManager.tsx`, `pages/ImportExport.tsx`, `pages/ModelHorizon.tsx` | One chokepoint, not one binding. |
| 12 | `window.pywebview.api` is injected ASYNCHRONOUSLY; `pywebviewready` is the event | Per-call feature detection races the injection and silently takes the dead path. |
| 13 | Baseline: **1460** backend tests; frontend 23 files / 147 | Task 0. Record the real numbers. |

---

## Global constraints

- **Both modes must keep working.** Nothing in `backend/desktop/` may be imported by `main`.
- **Apply the environment BEFORE `import main`, and enforce it in code.** `get_settings()` and `security.allowed_origins()` are `lru_cache`d and `main.py:645-655` freezes the CORS allowlist at module scope.
- **`MPLBACKEND=Agg` before `import pypsa`.**
- **Never hardcode an interpreter path**: `pixi run …`.
- **The backend suite must not import `webview`.** A headless CI box has none.
- **Serialize strictly: edit → gate → commit → next task.**

---

## Execution order

```
0  concurrency check + pixi feature + baseline
1  single-instance lock (kernel-released)
2  environment + socket + import ordering
3  bounded threaded uvicorn
4  quiesce, confirm, abort, flush, exit    ← the workstream
5  splash (main thread owns the GUI)
6  one download chokepoint for 13 sites
7  acceptance — on BOTH platforms
```

---

## Task 0: Concurrency check, pixi feature, baseline

- [ ] **Step 1: Concurrency check — FIRST.** CLAUDE.md mandates it and records a session that committed to `master`. This task rewrites `pixi.lock`, the artifact every session in this worktree shares.

```bash
git branch --show-current && git status --porcelain && git log --oneline -1
```

- [ ] **Step 2: Add pywebview behind its own feature**, so the default environment PyPSA-Eur solves with is **not re-solved**:

```toml
[feature.desktop.pypi-dependencies]
pywebview = "*"

[environments]
desktop = ["desktop"]
```

The repo already isolates `doc`/`test`/`dev` this way. Run the shell as `pixi run -e desktop …`. Scope by target if the win-64 leg resolves differently — `pixi.toml` targets four platforms with **different interpreters per platform**, and pywebview pulls `pythonnet` on Windows and `pyobjc-*` on macOS.

- [ ] **Step 3: Commit the manifest AND `pixi.lock` together.** A manifest-only commit makes the Windows box re-solve independently and diverge.

- [ ] **Step 4: Verify a real GUI backend, not just the import.** `import webview` succeeds on Windows with no WebView2 Runtime — the failure is a blank window at render time. Probe the backend (`webview.guilib.initialize()`), and **on the Windows box** check the WebView2 runtime is present. That confirms workstream J's Evergreen-bootstrapper requirement now, when it is one line.

- [ ] **Step 5: Baseline.** Expect **1460** backend / 23 files / 147 frontend. Record the real numbers.

---

## Task 1: Single-instance lock

**Files:** Create `backend/desktop/__init__.py`, `backend/desktop/single_instance.py`, `backend/tests/test_single_instance.py`

**Context:** D11 is mandatory: `_active` is process-global and the frontend keeps `currentProject` in shared `localStorage`.

**A kernel-released lock, NOT a pid file.** `fcntl.flock(fd, LOCK_EX | LOCK_NB)` on POSIX, `msvcrt.locking(fd, LK_NBLCK, 1)` on win32, holding the fd for the process lifetime. The OS drops it when the process dies, so there is no staleness window to size — and `os.kill(pid, 0)` is not merely unportable, it **terminates the target on Windows**.

`app_data_dir()` may not exist yet: `ensure_app_dirs()` runs in `lifespan`, and this lock is taken before `import main`. `mkdir(parents=True, exist_ok=True)` first, as `main.py:212` already does.

- [ ] **Step 1: Write the failing test** — acquire succeeds; a second acquire fails **while the first is held** (the half v1 omitted); release then re-acquire succeeds; the lock is released when the holding process dies; a garbage lock file does not wedge future launches.

- [ ] **Steps 2–4:** RED, implement, gate, commit.

---

## Task 2: Environment, socket, and import ordering

**Files:** Create `backend/desktop/launcher.py`; modify `backend/tests/test_dynamic_origin.py`; create `backend/tests/test_desktop_launcher.py`

- [ ] **Step 1: Write the failing test.**

`build_environment(port, legacy_root)` returns a mapping with `PYPSAGUI_LOCAL_MODE=1`, `MPLBACKEND=Agg`, `CORS_ALLOWED_ORIGINS` **exactly** `http://127.0.0.1:<port>` (constraint #2 — assert the 5173 defaults are ABSENT), and `PYPSAGUI_LEGACY_IMPORT_ROOT` (constraint #3, or D10 never fires). It must NOT set `DATABASE_URL`, `PYPSAGUI_PROJECTS_ROOT` or `PYPSAGUI_APP_DATA_DIR` — `app_paths` derives those, and phase 1b's `.env` incident came from setting one and not the other.

**`apply_environment()` raises if `"main" in sys.modules or "pypsa" in sys.modules`**, with a test asserting the raise. A launcher that gets the order wrong produces a mapping that passes every other test here and an app that is silently misconfigured — the allowlist is frozen at `main` import.

`bind_socket()` returns a bound socket handed to uvicorn (constraint #10), on **`127.0.0.1` literally** — assert the address, not just the port. `localhost` resolves to `::1` on macOS (CLAUDE.md records this costing a session) and `0.0.0.0` triggers a Windows Firewall prompt. Do **not** set `SO_REUSEADDR`: on Windows it permits binding a port another socket is actively using.

Extend `test_dynamic_origin.py` rather than restating what `:134` already proves.

- [ ] **Steps 2–4:** RED, implement, gate, commit.

---

## Task 3: Bounded threaded uvicorn

**Files:** Modify `backend/desktop/launcher.py`; create `backend/tests/test_desktop_server.py`

- [ ] **Step 1: Write the failing test** — `wait_healthy(timeout)` returns True once `/api/health` answers and False rather than hanging when the server never starts; `stop()` returns within its bound **even with a live SSE connection open** (constraint #9 — this is the test that matters).

- [ ] **Step 2: Implement.** `uvicorn.Config(app_object, …, timeout_graceful_shutdown=N)` — the app **OBJECT**, never `"main:app"`; a frozen build has no importable module path. Server on a `daemon=True` thread. `stop()` escalates: `should_exit` → bounded join → `force_exit` → bounded join → `os._exit(0)`.

- [ ] **Step 3: Gate and commit.**

---

## Task 4: Quiesce, confirm, abort, flush, exit

**Files:** Create `backend/services/shutdown.py`, `backend/tests/test_shutdown.py`; modify `backend/desktop/launcher.py`

**The workstream.** The order is argued, not chosen:

```
1. QUIESCE            hide the window / gate mutations. Autosave, the lock
                      heartbeat and four polling intervals keep firing
                      otherwise, and a chat request arriving after step 6
                      raises "cannot schedule new futures after shutdown".
2. confirm (D12)      only when a solve is in flight. Cancel = veto the close.
3. abort + WAIT       three paths (#5), per-path bound. AC PF cannot be
                      interrupted at all.
4. flush              persist_user_ts=(ctx is _active) + flush_to_disk (#7).
                      REFUSED with 409 if step 3 timed out (#4) — say so, do
                      not report a clean quit.
5. release locks
6. executor shutdown  (#8)
7. server stop        bounded escalation (Task 3)
```

- [ ] **Step 1: Write the failing test.**
  - `solves_in_flight()` sees each of the three paths — **one test per path**, because a single `or` looks correct while covering one. Iterate `_contexts` plus `_active` with `_solver_in_flight_ctx`, not `_state`, which self-heals a fresh context when `_active is None`.
  - `flush_all()` saves the active context with `persist_user_ts=True` and a non-active one with `False`; calls `chat_service.flush_to_disk`; one context raising does not strand the others.
  - **A flush refused by the in-flight 409 is REPORTED, not swallowed.**
  - `shutdown_sequence()` runs the seven steps **in order** — recording double, assert the order.
  - **Idempotent:** a second call while the first runs is a no-op. Without this, a second close click sets `should_exit` mid-flush.

- [ ] **Step 2: The close handler.** `window.events.closing` returns `False` to hold the window; `create_confirmation_dialog` only when `solves_in_flight()`; the sequence runs on a **worker**, not the GUI thread (a UI thread blocked past ~5 s is ghosted as *Not Responding* on Windows); `window.destroy()` from its completion callback.

- [ ] **Steps 3–4:** RED, implement, gate, commit.

`services/shutdown.py` is under `backend/`, so the suite covers it with no webview import.

---

## Task 5: Splash

**Files:** Create `backend/desktop/splash.py`; modify `backend/desktop/launcher.py`

**The load-bearing constraint the plan must state:** `webview.start()` blocks, must own the main thread (the Cocoa run loop), and may be called **once per process**. So the shape is fixed:

```
create_window(splash) → webview.start(bootstrap)
    bootstrap (worker): apply_environment → import main → serve
                        → wait_healthy → create_window(main_url)
                        → splash.destroy()
```

Destroying the last window ends the run loop and exits the process, so creating the main window **before** destroying the splash is not optional. This is also exactly why `MPLBACKEND=Agg` exists: `import pypsa` lands off the main thread.

- [ ] **Step 1:** stages as data — "Starting…", "Loading PyPSA…", "Importing your projects…", "Opening…". Test the SEQUENCE; the window is not unit-testable and must not be faked into looking tested.

- [ ] **Step 2: The import stage has no timeout** (constraint #3). Progress budget 6–10 s and give-up timeout are **different numbers**; the latter is ≥120 s or unbounded-with-a-visible-stage.

- [ ] **Step 3: Assert no windowing toolkit was resolved at import.** After `import main`, `sys.modules` must contain no `matplotlib.backends.backend_macosx`, no `tkinter`, no `PyQt*`/`PySide*`. `MPLBACKEND=Agg` fixes one known offender in a ~500 MB closure; this catches the class. **Run on both platforms.**

---

## Task 6: One download chokepoint

**Files:** Create `frontend/src/utils/download.ts`; modify the 13 sites (constraint #11); add the JS API in `backend/desktop/launcher.py`

**One `saveFile(filename, mimetype, bytes)` on the pywebview API, and one helper every site routes through** — this is the repo's own rule ("cross-cutting concern → edit the generic helpers"). A `save_bundle`-shaped binding bakes in a per-feature bridge that a later workstream fans out twelve more times.

Feature-detect **once, behind `pywebviewready`** (constraint #12), not per call. Fall back to the existing `createObjectURL`/`window.open` path so the web deployment and the browser dev workflow are untouched — the frontend test asserts the fallback.

The Python side writes to the path from `create_file_dialog(webview.SAVE_DIALOG)`. For `OverviewPanel`'s bundle the bytes come from `GET /api/projects/{name}/bundle` on the in-process server — budget that HTTP call; it is not free.

---

## Task 7: Acceptance — on both platforms

- [ ] **Step 1:** both suites green against Task 0's baseline. Build the SPA first — `frontend/dist/` is gitignored and Task 0 builds only tests.
- [ ] **Step 2:** launch; window opens; projects list; one opens and returns buses.
- [ ] **Step 3: the test that proves H5** — open a project, edit, close the window, relaunch, **assert the edit survived**.
- [ ] **Step 4:** start a solve, close, confirm the prompt appears, quit completes within its bound, **and the edit survived** (constraint #4 — do not assert the absence of transient rows; nothing writes them).
- [ ] **Step 5:** second launch while the first is open — refused, first window focused.
- [ ] **Step 6:** the process actually exits (`ps`), not just the window.
- [ ] **Step 7: run steps 2–6 on Windows.** D1 makes it first-class, spec §9.1 says it is unmeasured, and this is the workstream whose entire risk is the platform the author cannot see. Without this, H can be declared done with half the matrix untouched and the discovery lands in I/J where it is most expensive.

---

## Rollback

Additive under `backend/desktop/` plus one backend service, one frontend helper, and 13 call-site edits. `git revert` the range; the web deployment imports none of it.

---

## Residual risk

- **A never-saved draft is not flushed.** It lives only in `_active` with `loaded_project is None`, is absent from `_contexts`, and `_save_evicted_ctx` returns early on it (`pypsa_service.py:685`). Task 7 Step 3 sidesteps this by opening a saved project. Prompt, or state it.
- **AC PF cannot be interrupted** (#5). Quitting during one waits or abandons it; there is no third option until `ac_pf_service` takes a stop event.
- **The 6–10 s budget is macOS-arm64, warm, unfrozen.** A frozen unsigned Windows build adds Defender scanning of ~287 MB of native extensions, SmartScreen, and WebView2 first-init. `Documents/` is often OneDrive-redirected, so the first-run copy and `flush_all()` hit a sync-on-write filesystem.
- **`PYPSA_GUI_RESIDENT_CAP`** still uses the prefix spec §5.12 says collides with PyPSA's option namespace, printing `Unknown option` on every boot — and on a windowless Windows build writing to a closed stderr can raise. Not H's to fix; H creates the windowless condition, so it is H's to hand over.
- **`settings.frontend_dist` binds bare `FRONTEND_DIST`** with no `PYPSAGUI_` alias — the trap the adjacent `legacy_import_root` comment documents. Workstream I will set it through `build_environment`; it needs the alias first.
