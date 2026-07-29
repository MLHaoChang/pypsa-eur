# Local Desktop App — Phase 2a (workstream H): shell and lifecycle

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A native window that launches the backend in-process, shows honest progress while it boots, and — the part that carries all the risk — **quits without losing the user's unsaved work and without hanging**.

**Architecture:** `backend/desktop/` — a webview-free `launcher.py` (lock, socket, environment, server, shutdown wiring) and a `gui.py` that is the only module importing `webview`. No subprocess.

**Builds on:** phases 1a and 1b, on `feature/local-app-impl`.

**Scope:** Workstream H only. I–L are re-planned once this exists.

---

## A note on citations

**This plan cites FUNCTION NAMES, not line numbers**, for any file phase 2a will edit.

v1 and v2 were each rejected partly for stale or wrong `file:line` references — v2's preamble claimed "every citation was re-derived by a reviewer" and carried five errors, two inside that same table. The cause is structural, not carelessness: a plan is written against one commit, reviewed against another, and executed against a third. Line numbers are a liability in a document with that lifecycle. Where a line number appears below it is for a file this phase does **not** touch, or it is marked *approximate*.

---

## Revision log

**v3 (2026-07-28)** — v2 was REJECTed by two independent reviewers. Each finding was re-verified before it was applied.

| # | v2 defect | v3 |
|---|---|---|
| 1 | **`solves_in_flight()` could not see the queue path** — the path constraint #5 says must be aborted. v2 prescribed "iterate `_contexts` plus `_active`", but `solve_queue._run_job` calls `PyPSAService.build_context()`, which creates a context *off to the side*, and the module never calls `register`. A running background solve is in neither collection. v2's own test design would have hidden it, because a hand-registered context passes. **This was a NEW defect introduced while fixing a v1 defect.** | Path 2's source of truth is `solve_queue`'s own job table. Its test drives `solve_queue.enqueue(...)`, never a hand-registered context. |
| 2 | **Quiesce-first + "Cancel = veto" wedged the app permanently.** Window hidden, mutations gated, idempotency latch set — and no reverse path. Worse than the re-entrancy bug it replaced. A confirm dialog raised against a just-hidden window is also invisible on macOS. | Quiesce splits into *gate* (reversible, no GUI, step 1) and *hide* (after the user chooses Quit). The veto branch un-gates and clears the latch, with a test. |
| 3 | **`shutdown_sequence()` was named but never given a signature**, and two of its steps live outside `services/` — so "assert the ORDER with a recording double", which v2 called the point of the task, was not writable. | Explicit signature taking its seven collaborators as injected callables. |
| 4 | **Task 7 asserted "first window focused" — no task built focus.** Byte-for-byte the defect v2's own revision log row 5 claims to have fixed, one row further down. | Downgraded to what Task 1 actually delivers: refused, with a visible message. Focus moves to a named follow-up. |
| 5 | ~~**13 download sites; there are 14.** `components/ChatPanel.tsx` has an `<a download>` … it depends *entirely* on the `download` attribute a webview ignores.~~ **SUPERSEDED 2026-07-29 — the premise was false.** A webview does NOT ignore the attribute: `ALLOW_DOWNLOADS` gates it, and with the setting on, ChatPanel's anchor saves correctly with no change. Measured; see the Task 6 table. The live inventory is 11 `createObjectURL` + 1 declarative anchor — the old count of 12 blob sites included one inside `saveBlobToDisk`, which is now deleted. | The dead helper is deleted; no site needed migrating. |
| 6 | **"A hand-rolled flush drops the transcript" was FALSE.** `chat_service.flush_to_disk` is documented as a Phase-0 no-op. Asserting a data loss the code does not exhibit is exactly what got v1's constraint #4 rejected. | Corrected: call it for contract stability, not for present data loss. |
| 7 | **Two headline tests were vacuous.** `apply_environment()` "raises if `main` is imported" is trivially true — `tests/conftest.py` imports `main` at module scope, so it holds for every test in the suite. And "stop() returns with a live SSE connection" passes without exercising the hang, because the stream returns immediately when no solve is running. | Both run in a subprocess; the SSE test seeds the log queue first. |
| 8 | **`launcher.py` under `backend/` is imported by two backend test files AND was where v2 put the pywebview surface** — violating the plan's own "the backend suite must not import `webview`". v2 stated that reasoning for `shutdown.py` and did not apply it here. | `gui.py` holds every `webview` reference; `launcher.py` is webview-free, with a test asserting `"webview" not in sys.modules` after importing it. |
| 9 | **The `[environments]` snippet does not parse** — `pixi.toml` already declares that table, so v2 added a duplicate key and `pixi` refuses the manifest. | Add to the existing table. |
| 10 | **The bind → environment → import order was never stated**, and Task 5's bootstrap sketch omitted the lock and the bind entirely — an engineer following it literally imports `main` before a port exists. | The full ordered chain is written once, in Task 5, and referenced. |
| 11 | **D10 was still not wired.** `build_environment(port, legacy_root)` took the value as a parameter and no task said where it comes from. Threading a parameter is not implementing the decision. | A resolution step, with the not-found behaviour stated. |
| 12 | **CORS "is the ONLY barrier" is overstated.** It gates reading responses and preflighted writes; a *simple* cross-origin request still reaches the handler. The narrowing is a confidentiality fix, framed as closing the hole. | Reframed, with the integrity gap in Residual risk and the real fix named for K/L. |
| 13 | Five wrong citations, "four polling intervals" (there are 20), and "→ 200" where the cited test asserts `!= 403`. | Line numbers dropped (see above); the two counts corrected. |
| 14 | Gaps: nothing logged where a user can reach it; nothing handles the backend failing to start; window geometry unspecified; first launch with zero projects never exercised; the SPA build is a Task 5 prerequisite, not a Task 7 one. | All added. |

---

## Verified constraints

Re-derived at `f367ecab` by two reviewers independently.

| # | Fact | Why it matters |
|---|---|---|
| 1 | **Spec §5.5 is stale for local mode.** `_csrf_rejection` returns `None` as its first statement in local mode, and phase 1a already tests this — `tests/test_dynamic_origin.py::test_local_mode_mutation_is_not_403ed_from_an_ephemeral_origin` asserts `!= 403` (not `== 200`; do not claim more than the test proves) | No CORS-before-import dance is needed for mutations to work. |
| 2 | **In local mode the loopback API is unauthenticated.** CSRF returns `None`; the local user is injected with no cookie. `cors_allowed_origins` defaults to the two Vite dev origins | CORS gates **reading responses** and **preflighted** writes. Narrowing it to the bound origin protects the `ANTHROPIC_API_KEY` from being read cross-origin. It does **not** stop a *simple* cross-origin POST from executing — see Residual risk. |
| 3 | `run_first_run_import()` is called **synchronously inside `lifespan`, before `yield`**, and uvicorn accepts no connection until lifespan startup returns. It returns early unless `PYPSAGUI_LEGACY_IMPORT_ROOT` is set | The first launch that matters blocks readiness by a `copytree` of the whole legacy tree. The splash's import stage must POLL, not time out. The shell must set the variable or D10 never fires. |
| 4 | **A hard quit mid-solve LOSES THE SOLVE; it does not corrupt the project.** The modelling assumptions are in-memory only, `atomic_io` leaves the previous file intact on a killed export, and `_save_context` **refuses with 409** while `_solver_in_flight_ctx(ctx)` | The real hazard is the inverse: an incomplete abort makes the flush 409 and unsaved edits are dropped **while the app reports a clean quit**. |
| 5 | **THREE solve paths.** (a) the active context's stop event via `/api/simulation/abort`; (b) queue jobs, each with its own event, aborted by `solve_queue.abort(job_id)`; (c) `run_ac_pf`, which creates a stop event that **nothing reads** — `services/ac_pf_service.py` contains zero occurrences of `stop_event` | (a) and (b) can be aborted; (c) **cannot be interrupted at all**. |
| 6 | **A running queue job's context is in neither `_contexts` nor `_active` — ONLY when the project is not already resident.** `solve_queue._run_job` first calls `PyPSAService.get_context(job.project_key)` and solves the RESIDENT instance in place when there is one, "so the user's unsaved edits are included"; only the non-resident branch calls `build_context()`, which is documented as "off to the side, not activated", and the module never calls `register` | `solves_in_flight()` must consult the job table for path (b): iterating the context registry misses the non-resident case entirely. **Corrected after Task 4 — the original wording was unqualified and is half wrong.** It also means `resident_contexts()` can hand `flush_all` a context with a live solver thread, which `_save_context` correctly 409s and the report now surfaces |
| 7 | `PyPSAService._contexts` is capped by `RESIDENT_CAP` (env `PYPSA_GUI_RESIDENT_CAP`, default 5). Non-active contexts are persisted by LRU eviction (`_save_evicted_ctx`) **and** by the solve queue | Up to five projects can hold unsaved edits at quit. |
| 8 | `_save_evicted_ctx` passes `persist_user_ts=False`; the solve queue passes `(ctx is PyPSAService._active)`, because `_serialize_user_ts` reads a process-global belonging to the foreground | Copying either blindly loses the active project's time series, or stamps the foreground's onto every other project. |
| 9 | `chat_service.flush_to_disk` is documented as a **Phase-0 no-op** — `append_turn` writes synchronously | Call it for a stable call site, NOT because omitting it loses data. v2 claimed a loss the code does not exhibit. |
| 10 | `chat_service` creates a module-level `ThreadPoolExecutor` never shut down; CPython's atexit joiner blocks interpreter exit. `cancel_futures=True` cancels only PENDING work | Necessary, not sufficient. Pair with the bounded exit in #11. |
| 11 | uvicorn 0.51.0: `capture_signals` returns early off the main thread; `Server.shutdown` wraps `_wait_tasks_to_complete()` in `asyncio.wait_for(..., timeout=self.config.timeout_graceful_shutdown)`, default **`None`**; `_wait_tasks_to_complete` polls `while ... and not self.force_exit`. The app holds an SSE stream open until the client disconnects | `should_exit` alone can wait forever. **`force_exit` set from another thread does break the wait** — verified in the installed source. |
| 12 | `Server.run/serve/startup` accept `sockets: list[socket.socket]` and go through `loop.create_server(..., sock=sock)`; `shutdown` closes them | The socket handoff is real and closes the bind→serve race. |
| 13 | ~~**14 download sites, three shapes** … 1 × `<a download>` … depends entirely on the attribute a webview ignores. Two entry points, not one signature.~~ **SUPERSEDED 2026-07-29.** The shapes were right; the conclusion was not. Measured inventory: **11** × `createObjectURL` (`layout/Sidebar.tsx`, `utils/projectActions.ts`, `pages/TimeSeriesManager.tsx` ×4, `pages/results/shared.tsx` ×2, `pages/LoadProfileManager.tsx`, `pages/ImportExport.tsx`, `pages/ModelHorizon.tsx`) + **1** × `<a download>` (`components/ChatPanel.tsx`). All 12 work unchanged under `ALLOW_DOWNLOADS=True`. The 13th, `window.open` in `pages/OverviewPanel.tsx`, never worked in a webview and now uses `downloadProjectBundle`. `saveBlobToDisk` had zero callers and is deleted. | One setting; no entry points; the dead helper deleted. |
| 14 | ~~`window.pywebview.api` is injected ASYNCHRONOUSLY; `pywebviewready` is the event~~ **ORPHANED 2026-07-29.** True of pywebview, but nothing shipped touches `window.pywebview.api`: the JS→Python bridge this guarded was never needed. Retained only so a future bridge does not rediscover it. | n/a — no feature detection shipped. |
| 15 | The frontend has **20** `refetchInterval` sites, a 5-minute autosave, and a 45 s lock heartbeat. Hiding a window does **not** stop its timers or close its SSE stream | Quiesce must gate the API, not hide the window. |
| 16 | `logging.getLogger(__name__)` with **no `basicConfig` and no `FileHandler` anywhere in the backend**. `local_bootstrap` already disables alembic's logger because "on a windowed Windows build with no console, writing to that handle can raise" | In a frozen windowed build every `logger.exception` goes nowhere. H creates that condition. |
| 17 | uvicorn calls `sys.exit(STARTUP_FAILURE)` on lifespan failure — on a worker thread that raises `SystemExit` in that thread only, silently | `wait_healthy` returning False needs a defined UX, or the splash sits on "Starting…" forever. |
| 18 | `main.py`'s SPA route returns **503 "Frontend not built"** when `dist/` is missing, and `frontend/dist/` is gitignored | The SPA build is a prerequisite of the FIRST window, not of acceptance. |
| 19 | Baseline: **1460** backend tests; frontend **23** files / **147** tests (measured, not quoted) | Task 0. |

---

## Global constraints

- **Both modes must keep working.** Nothing in `backend/desktop/` may be imported by `main`.
- **Only `backend/desktop/gui.py` may import `webview`.** The backend suite runs on a headless box.
- **Apply the environment BEFORE `import main`, and enforce it in code** — `get_settings()` and `security.allowed_origins()` are `lru_cache`d and the CORS allowlist is frozen at module scope.
- **`MPLBACKEND=Agg` before `import pypsa`.**
- **Never hardcode an interpreter path**: `pixi run …`.
- **Serialize strictly: edit → gate → commit → next task.**

---

## Execution order

```
0  concurrency check, pixi feature, SPA build, baseline
1  single-instance lock (kernel-released)
2  environment, socket, import ordering
3  bounded threaded uvicorn
4  shutdown: gate, confirm, abort, flush, exit   ← the workstream
5  gui.py: splash, window, bootstrap chain
6  downloads: one pywebview setting + one call site (was: a chokepoint for 14 sites)
7  acceptance — on BOTH platforms
```

---

## Task 0: Concurrency check, pixi feature, SPA, baseline

- [ ] **Step 1: Concurrency check FIRST.** This task rewrites `pixi.lock`, the artifact every session in this worktree shares.

```bash
git branch --show-current && git status --porcelain && git log --oneline -1
```

- [ ] **Step 2: Add the feature, and add `desktop` to the EXISTING `[environments]` table.** `pixi.toml` already declares `[environments]` with `doc`/`test`/`dev`; a second table is a duplicate-key error and `pixi` refuses the manifest.

```toml
[feature.desktop.dependencies]
python = "3.13.*"        # see below — NOT optional

[feature.desktop.pypi-dependencies]
pywebview = "*"
```
then add `desktop = ["desktop"]` to the existing `[environments]`.

**Pin the interpreter.** Adding a pypi dependency freed the solver to pick a
newer Python for the new environment: measured, `desktop` resolved **3.14.6**
while the default environment — the one the 1460 backend tests validate — stayed
on **3.13.13**. The shell RUNS that backend, so an unpinned desktop environment
means the tested configuration is not the shipped one, and workstream I would
freeze against the untested interpreter. Pinning brought it to 3.13.14.

The default environment is not re-solved, but the lock **does** gain a complete new environment across all four platforms — and they resolve different interpreters (linux-64 is on 3.12, the others 3.13). Scope by target if the win-64 leg misbehaves: pywebview pulls `pythonnet` there and `pyobjc-*` on darwin.

- [ ] **Step 3: Commit the manifest AND `pixi.lock` together.** A manifest-only commit makes the Windows box re-solve independently and diverge.

- [ ] **Step 4: Build the SPA** (`npm --prefix pypsa-gui/frontend run build`). Constraint #18: without it the first window opens on a 503 JSON page.

- [ ] **Step 5: Verify a real GUI backend, not just the import.** `import webview` succeeds on Windows with no WebView2 Runtime; the failure is a blank window at render time.

The probe is **`webview.initialize()`**, which returns the platform module and sets `webview.guilib`. It is NOT `webview.guilib.initialize()` — `webview.guilib` is `None` until `initialize()` runs, so that spelling raises `AttributeError: 'NoneType' object has no attribute 'initialize'` and reads like a broken install:

```python
import webview
lib = webview.initialize()
print(lib.renderer)          # macOS: 'wkwebview' (webview.platforms.cocoa)
```

**On the Windows box** check the WebView2 runtime is present — that confirms workstream J's bootstrapper requirement now, when it is one line.

- [ ] **Step 6: Baseline.** Expect **1460** / 23 / 147. Record the real numbers.

---

## Task 1: Single-instance lock

**Files:** Create `backend/desktop/__init__.py`, `backend/desktop/single_instance.py`, `backend/tests/test_single_instance.py`

**A kernel-released lock, NOT a pid file.** `fcntl.flock(fd, LOCK_EX | LOCK_NB)` on POSIX, `msvcrt.locking(fd, LK_NBLCK, 1)` on win32, holding the fd for the process lifetime. The OS drops it when the process dies, so there is no staleness window to size. `os.kill(pid, 0)` is not merely unportable — on Windows it **terminates the target**.

`app_data_dir()` may not exist yet: `ensure_app_dirs()` runs in `lifespan`, after this. `mkdir(parents=True, exist_ok=True)` first, as `run_first_run_import` already does for its own lock.

- [ ] **Step 1: Write the failing test** — acquire succeeds; a second acquire fails **while the first is held**; release then re-acquire succeeds; the lock is released when the holding process dies (subprocess); a garbage lock file does not wedge future launches.

- [ ] **Steps 2–4:** RED, implement, gate, commit.

**Not in scope:** raising the incumbent window. A held lock says "someone is here"; it is not a channel. Task 7 Step 5 asserts only what this delivers.

---

## Task 2: Environment, socket, import ordering

**Files:** Create `backend/desktop/launcher.py`; modify `backend/tests/test_dynamic_origin.py`; create `backend/tests/test_desktop_launcher.py`

- [ ] **Step 1: Resolve the legacy root (D10).** `build_environment` cannot take it as a parameter nothing supplies. Add `resolve_legacy_root()`: probe the pre-desktop default (`<repo>/pypsa-gui/backend/projects` for a dev checkout; the packaged equivalent later), return `None` when absent, and **state that `None` means the import never fires** — which is the declared F1 deviation, not a bug.

- [ ] **Step 2: Write the failing test.**

`build_environment(port, legacy_root)` returns `PYPSAGUI_LOCAL_MODE=1`, `MPLBACKEND=Agg`, `CORS_ALLOWED_ORIGINS` **exactly** `http://127.0.0.1:<port>` (assert the 5173 defaults are absent), and `PYPSAGUI_LEGACY_IMPORT_ROOT` when resolved. It must NOT set any of the six storage variables — `DATABASE_URL`, `PROJECTS_ROOT`, `LEGACY_ROOT`, `FLAT_PROJECTS_ROOT`, `PYPSAGUI_PROJECTS_ROOT`, `PYPSAGUI_APP_DATA_DIR`. Both spellings are live (`settings` binds the bare names; `app_paths` reads the prefixed ones), so assert the absence of all six.

`bind_socket()` binds **`127.0.0.1` literally** — assert the address, not just the port. `localhost` resolves to `::1` on macOS (CLAUDE.md records this costing a session); `0.0.0.0` triggers a Windows Firewall prompt. Do **not** set `SO_REUSEADDR`: on Windows it permits binding a port another socket is actively using.

**`apply_environment()` raises if `main` or `pypsa` is already imported — and the test MUST be a subprocess.** `tests/conftest.py` imports `main` at module scope, so an in-process assertion is true for every test in the suite and proves nothing. One `subprocess.run([sys.executable, "-c", ...])` per direction: raises when `main` is pre-imported, does not when it is not.

Extend `test_dynamic_origin.py` rather than restating what it already proves.

- [ ] **Steps 3–5:** RED, implement, gate, commit.

---

## Task 3: Bounded threaded uvicorn

**Files:** Modify `backend/desktop/launcher.py`; create `backend/tests/test_desktop_server.py`

- [ ] **Step 1: Write the failing test.**
  - `wait_healthy(timeout)` returns True once `/api/health` answers, and False rather than hanging when the server never starts.
  - **`stop()` returns within its bound with a live SSE connection open.** The stream returns immediately when no solve is running, so the test must **seed the log queue first** and assert the stream was still open when `stop()` was called. Without that seeding the test passes without ever exercising the hang.
  - `wait_healthy` returning False has a defined outcome (constraint #17) — assert the launcher surfaces it rather than looping.

- [ ] **Step 2: Implement.** `uvicorn.Config(app_object, …, timeout_graceful_shutdown=N)` — the app **OBJECT**, never `"main:app"`; a frozen build has no importable module path. Server on a `daemon=True` thread. `stop()` escalates: `should_exit` → bounded join → `force_exit` → bounded join → `os._exit(HARD_EXIT_STATUS)` (70, not 0 — an abandoned shutdown must not report success).

- [ ] **Step 3: Gate and commit.**

---

## Task 4: Shutdown

**Files:** Create `backend/services/shutdown.py`, `backend/tests/test_shutdown.py`; modify `backend/main.py` (the mutation gate)

**The workstream.** Note `main.py` is in the file list — the gate is HTTP middleware and has nowhere else to live. Put it beside the existing solver-in-flight gate, which already has the `is_write` / prefix machinery and returns a typed 409.

```
1. GATE           mutations → 503 with a typed code. Reversible, no GUI.
                  Hiding the window does NOT do this: 20 refetch intervals,
                  autosave and the lock heartbeat keep firing, and the SSE
                  stream stays connected.
2. CONFIRM (D12)  only when a solve is in flight. Cancel → UN-GATE, clear the
                  latch, return False. The window is still visible because
                  step 1 did not hide it.
3. HIDE           only now, after the user chose Quit.
4. ABORT + WAIT   three paths (#5), per-path bound. AC PF cannot be
                  interrupted — see the decision below.
5. FLUSH          persist_user_ts=(ctx is _active) (#8) + flush_to_disk (#9).
                  REFUSED with 409 if step 4 timed out (#4) — REPORT it.
6. RELEASE LOCKS  release_all_for_user(db, local_mode.LOCAL_USER_ID)
7. EXECUTOR       (#10)
8. SERVER         Task 3's bounded escalation
```

**The AC PF decision, taken rather than deferred:** wait up to N seconds, then **warn and skip the flush for that context**, reporting it. Abandoning the wait silently would report a clean quit over dropped edits, which is exactly constraint #4's hazard.

- [ ] **Step 1: Signature first**, because the recording double needs a seam. Steps 6 and 8 live outside `services/`, so they are injected:

```python
def shutdown_sequence(
    *, gate, confirm, hide, abort_and_wait, flush, release_locks,
    stop_executor, stop_server,
) -> ShutdownReport: ...
```

- [ ] **Step 2: Write the failing test.**
  - `solves_in_flight()` sees **each of the three paths, one test per path** — a single `or` looks correct while covering one. **Path (b)'s test drives `solve_queue.enqueue(...)`, never a hand-registered context** (constraint #6: a queue job's context is in neither collection, and a hand-registered one would make a broken implementation pass).
  - `flush_all()` saves the active context with `persist_user_ts=True` and a non-active one with `False`; calls `flush_to_disk`; one context raising does not strand the others; **a 409 is caught specifically (`HTTPException`, not `Exception`) and reported**.
  - `shutdown_sequence()` runs the eight steps **in order** — recording double over the injected callables.
  - **Cancel un-gates and clears the latch**, and a second close after a Cancel starts a NEW sequence rather than no-opping.
  - **Idempotent while running:** a second call mid-sequence is a no-op.

- [ ] **Step 3: The close handler is TRI-STATE.** not-started → start worker, return `False`; in-progress → return `False`; complete → return `True`. The completion path flips the state **before** `window.destroy()`, or `destroy()` re-enters `closing` and deadlocks against its own veto. Only steps 2 and 3 touch the GUI; the rest runs on a worker (a UI thread blocked past ~5 s is ghosted as *Not Responding* on Windows).

- [ ] **Steps 4–5:** RED, implement, gate, commit.

---

## Task 5: `gui.py` — splash, window, bootstrap

**Files:** Create `backend/desktop/gui.py`, `backend/desktop/splash.py`; create `backend/tests/test_launcher_is_webview_free.py`

**`webview.start()` blocks, must own the main thread, and may be called once per process.** So the chain is fixed, and this is the ONE place it is written down:

```
main thread:  acquire lock → bind_socket() → build_environment(port, legacy_root)
              → create_window(splash) → webview.start(bootstrap)

bootstrap (worker):  apply_environment() → import main → serve(sockets=[sock])
                     → wait_healthy() → create_window(main_url)
                     → splash.destroy()
```

The port comes from `sock.getsockname()[1]`, so **bind precedes `build_environment`** — get that wrong and the wrong allowlist is frozen at `import main`. Creating the main window **before** destroying the splash is not optional: destroying the last window makes `start()` return.

- [ ] **Step 1: `launcher.py` stays webview-free.** Test: `import desktop.launcher` then assert `"webview" not in sys.modules`. Otherwise Tasks 2 and 3's test files fail collection on any box without pywebview — which is every CI box.

- [ ] **Step 2: Stages as data** — "Starting…", "Loading PyPSA…", "Importing your projects…", "Opening…" — and say the mechanism: the splash is a local HTML string (it cannot be backend-served; the backend is not up), updated via `window.evaluate_js`. Test the SEQUENCE; the window is not unit-testable and must not be faked into looking tested.

- [ ] **Step 3: The import stage has no timeout** (constraint #3). Progress budget (6–10 s) and give-up timeout are **different numbers**.

- [ ] **Step 4: Window geometry.** Title, initial size, minimum size. Persist across launches in app-data, or state that it does not.

- [ ] **Step 5: Assert no windowing toolkit was resolved at import** — after `import main`, no `matplotlib.backends.backend_macosx`, no `tkinter`, no `PyQt*`/`PySide*`. **Subprocess**, for the same reason as Task 2. `MPLBACKEND=Agg` fixes one known offender in a ~500 MB closure; this catches the class. Run on both platforms.

- [ ] **Step 6: File logging** (constraint #16). A rotating handler under `app_data_dir()`, installed by the shell before `import main`. Without it, every `logger.exception` in a frozen windowed build goes nowhere — including the first-run import's and the shutdown's.

---

## Task 6: Downloads — REWRITTEN 2026-07-29 after measuring

> **The original design here was wrong and has been replaced.** It specified a
> `frontend/src/utils/download.ts` chokepoint, a `js_api` bridge, feature
> detection behind `pywebviewready`, and migration of all 14 sites. It was
> written against an assumed pywebview API. Measured against the real cocoa
> WKWebView, **13 of the 14 sites need no frontend change at all** and the
> bridge solves a problem that does not exist. The original text is in git
> history at `c1d8445d`.

### What was measured

Real `webview.create_window` + `webview.start()` on macOS arm64, pywebview
6.2.1, one case per process (sequencing them in one page is unsound — the
failure mode under test destroys the JS context, so every later case would
report nothing and be scored as the wrong failure). Only the human clicking
Save was faked; whether the delegate fires at all is WebKit's decision, and
that is the measurement. Harness verified honest before use: 14 heartbeats and
a byte-exact file on the first positive case.

| Site shape | `ALLOW_DOWNLOADS=True` | `ALLOW_DOWNLOADS=False` (the default) |
|---|---|---|
| blob + `a.download`, `text/csv` | saves, page intact | **navigates to `blob:…`; the SPA is gone** |
| blob + `a.download`, `application/json` | saves, page intact | **navigates away; the SPA is gone** |
| blob + `a.download`, `.xlsx` | saves, page intact | silent no-op |
| blob + revoke on the same tick, 1 MB | saves **byte-exact** (1048576) | — |
| `<a href download>` at a URL (ChatPanel) | saves, page intact | **navigates to the file; the SPA is gone** |
| `window.open(url, '_blank')` (bundle) | **nothing; returns `null`** | nothing |
| `<a href download>` at the bundle URL | saves, page intact | nothing |
| the same, 64 MB | saves **byte-exact** (67108864), page responsive | — |

Three findings the original design had backwards:

1. **`webview.settings['ALLOW_DOWNLOADS']` defaults to `False`**, and
   [`cocoa.py:279`](../../../.pixi/envs/desktop/lib/python3.13/site-packages/webview/platforms/cocoa.py)
   gates the whole download path on it:
   `if action.shouldPerformDownload() and webview_settings['ALLOW_DOWNLOADS']`.
   With it off, a `download` anchor falls through to *ordinary navigation* —
   so for any MIME type WebKit can render, the app replaces itself with the
   file, in a window with no back button and no address bar. That is worse
   than the "downloads don't work" the plan assumed.
2. **Turning it on needs no frontend change for 13 sites.** The native
   `NSSavePanel` path saves the correct bytes, survives `revokeObjectURL` on
   the same tick (which all 12 real sites do), and streams 64 MB without
   marshalling. The plan's own objection to `saveFile` — that a large bundle
   "would become a JSON array of integers" — was an argument against a bridge
   that is not needed.
3. **Windows uses the same switch.**
   [`edgechromium.py:316`](../../../.pixi/envs/desktop/lib/python3.13/site-packages/webview/platforms/edgechromium.py)
   opens `on_download_starting` with `if not webview_settings['ALLOW_DOWNLOADS']: args.Cancel = True`.
   One setting covers both platforms. *Unverified on Windows* — read, not run.

`window.open` is the sole genuine failure, on both settings, because
`webView_createWebViewWithConfiguration_…` acts only on
`WKNavigationTypeLinkActivated` and a JS `window.open` is not that, so it
returns `nil`.

### What to build

**Files:** create `backend/desktop/downloads.py`; modify `backend/desktop/gui.py`,
`frontend/src/pages/OverviewPanel.tsx`, `frontend/src/utils/projectActions.ts`,
`pixi.toml`

- [x] **Step 1:** `desktop/downloads.py` — webview-free, so the headless suite
      covers it, same rule as `launcher.py` and `bootstrap.py`. One function
      that sets `ALLOW_DOWNLOADS` on a settings mapping, carrying the measured
      reason in a comment. The test pins the invariant: flipping it off does
      not merely disable saving, it makes every CSV export navigate the app
      away from itself.
- [x] **Step 2:** `gui.py` calls it **before the first window is created**.
      The test asserts from inside the `create_window` stub, not after
      `main()` returns — asserting afterwards passed with the call moved below
      `_start_gui()`, where in the real app it would land after
      `webview.start()` had blocked for the entire session.
- [x] **Step 3:** `OverviewPanel` — replace `window.open(bundleUrl, '_blank')`
      with `downloadProjectBundle(currentProject, { askLocation: true,
      skipCache: true, skipErrorToast: true })`, the helper `AppHeader` and the
      Sidebar modal already use. **Fetch through axios, THEN save the blob.**
      This step took two review rounds and every option in it is a finding:

      * *Round 1* — the first attempt used a bare anchor at the API URL. It
        worked in the shell, and two independent reviewers caught that it also
        writes 401/403/404 JSON bodies to disk as a valid-looking
        `.pypsaproj.zip`, because an anchor cannot see a status code.
        `window.open` at least showed the error in a tab.
      * *Round 2* — calling the helper with its DEFAULT options reused the
        **Save-destination handle cache**. Export then silently rewrote the
        file the user had picked for Save, showing no picker and a success
        toast; and the export's own destination was cached, so a later Save
        silently overwrote the archive the user had just exported. Both are
        data loss. `askLocation` + `skipCache` are what "Save a Copy" passes,
        for exactly this reason.
      * *Round 2* — `skipErrorToast` because `downloadBundle` sets
        `responseType: 'blob'`, so the interceptor's `data?.detail` lookup
        reads a **Blob** and can never find the server's message. It toasted
        the opaque "Request failed with status code 404" *and* the handler
        toasted again. `bundleErrorMessage` decodes the blob; one accurate
        toast replaces two useless ones.
      * *Round 2* — `disabled={exporting}`: a double-click ran two exports,
        two save dialogs, two full server-side zip builds.
      * *Round 2* — `downloadBundle` now sets `timeout: 180_000`, matching
        `importBundle` for the same artifact. It had inherited the 30 s
        default, where the plain anchor it replaced had no timeout at all — a
        regression for any project big enough to matter.

      Regression tests in `OverviewPanel.download.test.tsx`; all six
      corresponding mutations verified to kill.
- [x] **Step 4:** delete `utils/projectActions.ts::saveBlobToDisk` (zero
      callers, verified across every ref in the repo). It is a second path
      competing with the one every other export uses, and its
      `showSaveFilePicker` branch does not exist in WKWebView anyway.
- [x] **Step 5:** `pixi.toml` — the `test` environment gains the `desktop`
      feature (for `pywebview` alone), **and `gui-tests` moves into
      `[feature.test.tasks]`**. The first half alone was useless: `gui-tests`
      sat in the root `[tasks]` table, so the canonical command resolved to
      `default`, where both guarding tests still SKIPPED and the suite still
      read green. `pywebview` is pinned `==6.2.1` now that it is in a gating
      environment — this workstream reads its non-public behaviour in three
      places.
- [x] **Step 6:** guard the guard. Two edits silently re-open the hole —
      dropping `desktop` from `test`, or moving the task back — and neither
      failed anything. `test_desktop_environment.py` now asserts pywebview is
      in `test` on every platform, and that `gui-tests` is defined under
      `[feature.test.tasks]` and not in the root table. The `importorskip` in
      `test_desktop_downloads.py` is replaced by a hard failure with an
      actionable message: a skip is the exact failure mode this task is about.

### Task 6 acceptance — macOS arm64, measured 2026-07-29

Driven through the shipped `gui.main()` — real lock, real socket, real backend,
real uvicorn, real SPA — clicking the real controls with `evaluate_js`. The
only substitution is the human clicking Save in the native panel; whether the
panel is reached is WebKit's decision and is what is being measured. Driver:
`pypsa-gui/backend/smoke/accept_downloads.py`.

| What | Result |
|---|---|
| `ALLOW_DOWNLOADS` read live from `webview.settings` in the shipped path | `true` |
| Open project through the launcher, open the Project info panel | `/app`, panel opens |
| Click the real **Export bundle** button | anchor clicked with `href=blob:http://127.0.0.1:…`, `download=DownloadProject.pypsaproj.zip`, **detached** |
| File lands | `DownloadProject.pypsaproj.zip`, 3682 B, **1 s** |
| A real **blob** export (Sidebar export modal → Download File) | `downloadproject.pypsaproj.zip`, 3723 B, **1 s** |
| The SPA after both | still `/app`, no page errors |

The `attached: false` is worth keeping. An earlier version of this work asserted
that WebKit ignores a click on a detached anchor, and attached one on that
basis — an assumption, never measured. Every one of the 11 real
`createObjectURL` sites clicks a **detached** anchor, so had the assumption
been true, "they need no change" would have been false for all of them. It is
measured here and it is false: detached works.

The blob case is there because "13 sites need no change" otherwise rests on
the audit page rather than on the app.

**Three false results this run produced before it produced a true one**, all
worth knowing:

1. There are **two** "Export bundle" buttons. The sidebar one opens an export
   *modal* and downloads nothing by itself. Clicking the first match hit that
   one and landed no file — indistinguishable from a broken download.
2. The bundle endpoint's `Content-Disposition` **overrides the anchor's
   `download` attribute**, so a hand-built probe anchor and the real button
   produce the same filename. Running both in one session made the single
   landed file unattributable; they had to be split into separate runs.
3. **The desktop app serves the BUILT SPA.** The first honest run showed the
   button firing no anchor at all, with no error — because `dist/` was a day
   old and still contained `window.open`. Any frontend change needs
   `npm run build` before an end-to-end means anything. `dist/` is gitignored,
   so nothing in the repo records this.

### What this does NOT cover

`ALLOW_DOWNLOADS=True` also lets *any* navigation response with a
non-renderable MIME type raise a save panel. In this app navigations to API
URLs do not otherwise happen — the SPA routes client-side and fetches with
XHR, which is not a navigation — but this is a widened surface, stated rather
than discovered later.

---

## Task 7: Acceptance — on both platforms

- [ ] **Step 1:** both suites green against Task 0's baseline.
- [ ] **Step 2: first launch with ZERO projects** — window opens, empty state renders, a new project can be created. On a fresh machine there is nothing to list.
- [ ] **Step 3:** launch with projects; one opens and returns buses.
- [ ] **Step 4: the test that proves H5** — open a project, edit, close, relaunch, **assert the edit survived**.
- [ ] **Step 5:** start a solve, close, confirm the prompt appears, **Cancel — assert the app is still usable** (mutations work, the window is visible), then close again and Quit; **assert the edit survived**. Do not assert the absence of transient solver rows; nothing writes them (constraint #4).
- [ ] **Step 6:** second launch while the first is open — **refused with a visible message**, first instance unaffected. (Focus is not built; see Task 1.)
- [ ] **Step 7:** the process actually exits (`ps`), not just the window.
- [ ] **Step 8: run steps 2–7 on Windows.** D1 makes it first-class, spec §9.1 says it is unmeasured, and this is the workstream whose entire risk is the platform the author cannot see.

---

## Acceptance results — macOS arm64, measured 2026-07-29

Run against a throwaway app-data directory with the real `gui.main()`, two
sequential launches sharing that directory.

| Step | Result |
|---|---|
| 2 — first launch, ZERO projects | window opens, `/api/network/reset` 200, bus created 201, project saved 200, clean quit, **exit status 0** |
| 3 — a project opens and returns buses | `GET /api/projects/AcceptanceProject` 200, `/api/network/buses` returns the bus |
| 4 — **H5: the edit survives a relaunch** | launch 2 lists `AcceptanceProject`, loads it, and `AcceptanceBus` is present. **This is the property the whole conversion exists for.** |
| 6 — second launch while the first runs | refused, "already running" message shown (NOT the lock-failed one), incumbent's lock unaffected, status 0 |
| 7 — the process actually exits | both sessions exited; `ps` shows no strays |

Also observed in a separate live run: `/api/health` reports **pypsa 1.1.2** —
direct evidence the solve-group pinning holds in the shipped configuration, an
unpinned `desktop` env would report 1.2.4 — `auth_enabled: false`, the SPA
served as real HTML rather than the 503 page, `pypsa-gui.log` written and
ending "Application shutdown complete" (the graceful uvicorn path, not the
forced one), and the first-run import firing so D10 works end to end.

**NOT run: step 5** (start a solve, close, Cancel, assert still usable, quit).
It needs a real solve and was not automated here.

**NOT run: step 8, Windows.** Everything platform-specific in this workstream
is still reasoned-from-documentation: the `msvcrt` lock and its
release-after-death delay, `SO_EXCLUSIVEADDRUSE`, the ProactorEventLoop under
the socket handoff, `pandas 3.0.3` on win-64 which no test has executed, and
the `destroy()` re-entrancy that `CloseHandler` depends on — which macOS
provably CANNOT exercise, so the ordering has no coverage on the platform that
needs it.

---

## Rollback

Additive under `backend/desktop/`, one backend service, one middleware gate, one frontend helper, 14 call-site edits. `git revert` the range; the web deployment imports none of it.

---

## Residual risk

- **The loopback API remains reachable for cross-origin SIMPLE requests** from any page in any browser. The narrowed allowlist protects response confidentiality — the API key cannot be read — but not integrity: a no-body `POST` still executes. The ephemeral port is the only thing making it hard to hit. A per-launch bearer token or a `Host` check is the real fix and belongs to K/L.
- **A never-saved draft is not flushed.** It lives only in `_active` with `loaded_project is None`, is absent from `_contexts`, and `_save_evicted_ctx` returns early on it. Task 7 Step 4 sidesteps this by opening a saved project. Prompt, or accept it.
- **AC PF cannot be interrupted** (#5). The decision is wait-then-warn-and-skip; there is no third option until `ac_pf_service` takes a stop event.
- **The 6–10 s budget is macOS-arm64, warm, unfrozen.** A frozen unsigned Windows build adds Defender scanning of ~287 MB of native extensions, SmartScreen, and WebView2 first-init. `Documents/` is often OneDrive-redirected, so the first-run copy and `flush_all()` hit a sync-on-write filesystem.
- **`PYPSA_GUI_RESIDENT_CAP`** still uses the prefix spec §5.12 says collides with PyPSA's option namespace, printing `Unknown option` on every boot — and on a windowless build writing to a closed stderr can raise. H creates the windowless condition, so it is H's to hand over.
- **`settings.frontend_dist` binds bare `FRONTEND_DIST`** with no `PYPSAGUI_` alias. Workstream I will set it through `build_environment`; it needs the alias first.
- **Raising the incumbent window on a second launch is not implemented.** Named follow-up, not a gap discovered later.

### pywebview 6.2.1 API, measured before Task 5 rather than assumed

Run against the installed package, because Task 4's worst defect was code
written against `_state["ac_pf_thread"]` — a key nothing has ever written —
with a test that mocked the function containing the assumption.

- `webview.__version__` **does not exist**; use `importlib.metadata.version("pywebview")`.
- Present: `create_window`, `start`, `initialize`, `token`, `settings`, `screens`, `windows`; on a window `destroy`, `hide`, `show`, `load_url`, `evaluate_js`, `create_file_dialog`, `set_title`.
- `create_window` accepts `hidden`, `confirm_close`, `min_size`, `js_api`, `focus`, `on_top` — the splash can be created hidden, and pywebview has its OWN confirm-close dialog which the shell must NOT enable, since D12 is our sequence's step 2.
- Events: `before_load, before_show, closed, closing, initialized, loaded, maximized, minimized, moved, request_sent, resized, response_received, restored, shown`. **`closing` is the only one with `_should_lock=True`** — handlers run synchronously on the caller's thread and the return value vetoes. That is what makes the tri-state handler work.
- **`destroy()` re-fires `closing` on winforms and gtk but NOT on cocoa** — verified with a real window: `closing fired 0 time(s)`. `CloseHandler`'s ordering is load-bearing on Windows and inert on macOS, so the macOS half of Task 7 cannot detect a regression in it.

### Added after review rounds 1 and 2 (Tasks 0–3 implemented)

- **DECLARED DEVIATION from spec §4.** The spec lists `DATABASE_URL`, `PROJECTS_ROOT` and `LEGACY_ROOT` among the variables the entrypoint sets before `import main`. `build_environment` deliberately sets **none** of the six storage variables, and a parametrised test asserts their absence: `app_paths` already resolves them per-user and per-platform, and anything the shell computed would be relative to wherever the frozen app runs — a Finder-launched `.app` has cwd `/`. Sound, but it was an undeclared deviation until a reviewer caught it. Declared now.
- **D5 ("projects root changeable in Settings") has no mechanism and no owner.** `get_settings()` is `lru_cache`d and `app_paths.default_projects_root()` reads `PYPSAGUI_PROJECTS_ROOT` at call time, so a user-chosen root can only be applied by the shell *before* `import main` — and the deviation above has closed that door. Spec §4 puts it in `<app-data>/config.json`; nothing reads that file. Needs an owner in I–L.
- **Environment parity is now structural, not enumerated.** `default`, `desktop`, `test` and `dev` share one pixi solve group; measured max divergence across every pair on every platform is 0. `doc` is deliberately outside it. The packages that decide optimisation results are pinned in `[dependencies]` (`pypsa`, `highspy`, `linopy`, `scip`, `pyscipopt`, `atlite`, `tsam`) because a solve group re-solves everything and `[exclude-newer]` deliberately lets several of them float. `uvicorn` is pinned for a different reason: the shutdown design reads its non-public internals.
- **Adopted, and worth knowing:** on linux-64 every environment moved from `libgomp` + `pthreads_` OpenBLAS to `llvm-openmp` + `openmp_` OpenBLAS. Uniform now rather than split, but it is a change to dense-linear-algebra threading on a platform nobody here can run.
- **`pixi run integration-tests` has not been run** since the environments were re-solved. It drives full Snakemake workflows and downloads datasets. The at-risk surface — solvers, pypsa, linopy, atlite, tsam — is pinned.
- **`.pixi/envs/desktop` on the development machine is stale** relative to the fixed lockfile. `pixi run` re-syncs, so this is a verification gap rather than a shipped defect — but nobody has yet run `pixi run -e desktop python -c "import pypsa"` since the pin landed.
- **`SingleInstance.acquire()` now propagates a non-contended `OSError`** (ENOLCK/EOPNOTSUPP on a network `PYPSAGUI_APP_DATA_DIR`) instead of mislabelling it "already running". Correct, but no caller exists yet to turn it into a message, so today it would surface as a traceback and no window. **Task 5 owns this.**
- **`stop()`'s worst case is 240 s during lifespan startup** (2 × `STARTUP_JOIN_TIMEOUT`; the ladder waits after `should_exit` and again after `force_exit`). Task 4 drives `stop()` from the close handler, and a UI thread blocked past ~5 s is ghosted as *Not Responding* on Windows — so that step must run on a worker.
- **Everything Windows-specific remains reasoned-from-documentation, not measured.** The `msvcrt` lock's release-after-death delay (mitigated by a bounded retry), `SO_EXCLUSIVEADDRUSE` not being set, the ProactorEventLoop rather than uvloop under the socket handoff, and `app_paths.default_projects_root()` using `Path.home()/"Documents"` rather than `SHGetKnownFolderPath` — which OneDrive Known Folder Move and Group Policy both redirect. Task 7 Step 8.
