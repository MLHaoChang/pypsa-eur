import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

# Load `pypsa-gui/backend/.env` (gitignored) before any module reads
# `os.environ` — this is what keeps `ANTHROPIC_API_KEY` (and any other
# operator-supplied env vars) alive across backend restarts initiated
# outside the original launching shell. `python-dotenv` ships in the pixi
# env; the import is soft so a missing package degrades to "env-only" mode
# rather than crashing startup.
try:
    from dotenv import load_dotenv
    _ENV_PATH = Path(__file__).parent / ".env"
    if _ENV_PATH.exists():
        # override=False so an env var set in the launching shell wins over
        # the .env file value (matches python-dotenv's documented contract
        # and avoids surprising the operator who set a key inline).
        load_dotenv(dotenv_path=_ENV_PATH, override=False)
except ImportError:
    pass

import pypsa
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
import local_bootstrap
import local_mode
import security
import static_gate
from db import session as db_session_module
from deps import bind_active_project, resolve_request_session, resolve_request_user
from services import active_project
from services import shutdown as shutdown_service
from routers import (
    admin,
    auth,
    changelog,
    chat,
    clustering,
    compare,
    io,
    network,
    project_network,
    projects,
    results,
    simulation,
    snapshots,
    solve_queue,
    uploads,
    vintage,
)
from services.pypsa_service import PyPSAService
from settings import get_settings

logger = logging.getLogger(__name__)

# Prefixes whose non-GET mutations should be captured in the undo stack.
_UNDO_PREFIXES = ("/api/network/", "/api/io/")
# Exact paths that must never trigger a snapshot (the undo endpoint itself,
# and the info probe which is a GET anyway but listed for safety).
_UNDO_EXCLUDE = {"/api/network/undo", "/api/network/undo/info"}

# Path prefixes whose write methods (POST/PUT/PATCH/DELETE) are refused with
# 409 while the LP worker is alive. These touch the in-memory network
# directly — letting them through during a solve races with PyPSA's n.add()
# and n.remove() calls inside the worker (vintage expansion, slack injection,
# load scaling, dispatch fix) and can produce HDF5 errors, dim_0 crashes, or
# silently corrupt the LP solution.
#
# Why `/api/projects/` is included even though save (POST /api/projects/{name})
# already has an endpoint-level gate:
#   * /api/projects/import_bundle (projects.py)        replaces the network wholesale
#   * /api/projects/{base}/scenarios (POST)            forks the network mid-LP
#   * /api/projects/{name}/snapshots/{id}/restore POST resets + reloads the network
#
# Each of the above mutates the live in-memory network and was unguarded
# before this middleware. The /save endpoint's own gate now double-fires
# (middleware first, endpoint second) — harmless: the middleware's 409
# returns immediately and the endpoint's gate is unreachable. We lose the
# save endpoint's more contextual error message but keep correctness.
#
# GETs are NOT gated — reads never acquire the PyPSA lock by project policy
# and the transient-row filter in _get_component already hides solver-only
# rows. So the method check below restricts the gate to mutating verbs.
_SOLVER_BLOCKING_PREFIXES = ("/api/network/", "/api/io/", "/api/projects/")
# Exempt EXACT paths to allow during a solve. Empty today — every route
# under the blocking prefixes is either a network mutation (unsafe) or
# rare enough that a 409 isn't a real UX hit (rename, delete on a
# non-active project). The set is kept as the extension point if a
# future high-frequency safe-during-solve route appears (e.g. a static
# /api/projects/{name}/layout flush — but that has dynamic path
# segments that don't fit a flat-set match, so it'd need a regex
# variant if we ever add it).
_SOLVER_BLOCKING_EXEMPT: set[str] = set()
# Suffix-exempt routes (dynamic `{id}` path segment, so they can't be listed as
# exact paths). `POST /api/projects/{id}/activate` is the B8 instant switch — a
# pure active-pointer swap that does NOT mutate the in-memory network, so it is
# SAFE during a solve. It carries its OWN, smarter guard (allow switching away
# from a queue-dispatcher solve, which keeps solving its captured ctx in the
# background; refuse only a legacy /run). Without this exemption the blanket
# middleware 409 fires first and the user can't leave a solving project — the
# whole point of the resident multi-project work.
_SOLVER_BLOCKING_EXEMPT_SUFFIXES = ("/activate",)

# Exempt from the SHUTTING-DOWN gate. `/api/simulation/abort` is step 4's own
# mechanism, and step 1 closes the gate before step 4 runs — so without this
# the sequence refuses its own abort, the flush then 409s, and the user is told
# the quit was clean. The solver gate three blocks below has had an exemption
# list from the start; this one shipped without one.
_SHUTDOWN_GATE_EXEMPT: set[str] = {"/api/simulation/abort"}

# Prefixes whose mutations should auto-invalidate solver dispatch. Narrower
# than _UNDO_PREFIXES: importing a bundle (/api/io/*) replaces the network
# wholesale with a state that may already include a solved dispatch — we
# don't want to clear that. Restoring a project snapshot is handled by the
# /api/projects/* router which also brings its own dispatch.
_DISPATCH_INVALIDATE_PREFIXES = ("/api/network/",)
_DISPATCH_INVALIDATE_EXCLUDE = {"/api/network/undo", "/api/network/undo/info"}
_AUTH_PUBLIC_PATHS = {
    "/api/auth/forgot-password",
    "/api/auth/login",
    "/api/auth/reset-password",
    "/api/auth/set-password",
    "/api/health",
}

# Undo-snapshot push coalescing now lives in services.undo_service
# (claim_push_slot) so the timer travels with the undo subsystem — the
# rationale (drag-burst collapse, granularity trade-off) is documented there.
# The middleware calls it below, before the expensive netcdf export.


# ── First-run legacy import (spec F1) ────────────────────────────────────────

_IMPORT_LOCK_NAME = "import.lock"

# A lock left behind by a KILLED process must not wedge every future launch.
# A fresh lock is trusted — that is a real second instance mid-copy — and an
# old one is reclaimed. The window is generous because the thing it guards is
# copying 100+ MB.
_LOCK_STALE_SECONDS = 3600


def _import_lock_path() -> Path:
    import app_paths

    return app_paths.app_data_dir() / _IMPORT_LOCK_NAME


def _persist_import_report(report, source) -> None:
    """
    Write the last import's outcome where a human can find it.

    Logging alone is not a channel: a project skipped as a collision is
    indistinguishable from one that was never there, and on a packaged
    windowless build nobody reads the log at all. This does not deliver a UI —
    that belongs to workstream H — but it makes "why is my project missing"
    answerable with a file path rather than a rebuild.
    """
    import app_paths

    try:
        payload = {
            "source": str(source),
            "at": datetime.now(tz=timezone.utc).isoformat(),
            "imported": report.imported,
            "already_imported": report.already_imported,
            "skipped": report.skipped,
            "collisions": report.collisions,
            "failed": report.failed,
            "warnings": report.warnings,
        }
        (app_paths.app_data_dir() / "last-import-report.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
    except OSError:
        logger.exception("could not write the import report")


def run_first_run_import() -> None:
    """
    Import a pre-desktop project tree, once, on launch.

    **Never FAILS startup** — and the distinction matters to the desktop
    shell. Anything that goes wrong is logged and the app boots anyway:
    `tools/import_legacy` is the retry path, and an app that will not start is
    a worse outcome than an import that did not happen. But the call itself is
    synchronous inside `lifespan`, so the first request waits for the whole
    copy. A launcher showing a splash must budget for that, not for
    `/api/health` answering in the measured 2.3 s.

    **Idempotence comes from the receipts `legacy_import` writes**, not from a
    marker here. A marker is what lets a reinstall — new app-data, same
    projects folder — copy stale data over live work. It also means a launch
    with nothing to import records nothing, so a machine whose legacy root was
    not configured yet does not skip the import permanently.

    The `O_EXCL` lock stands in for the single-instance guard (D11/H1), which
    has not landed. Two launches can genuinely overlap today, and without it
    they interleave `copytree` calls into the same staging directory.
    """
    from services import legacy_import

    configured = get_settings().legacy_import_root
    if not configured:
        # No import configured. Until the packaged shell sets
        # PYPSAGUI_LEGACY_IMPORT_ROOT this is the normal path for a plain
        # checkout — a declared F1 deviation, not a silent no-op.
        return

    # Through `legacy_import.source_root_str`, not a local spelling: the
    # ledger and the receipts are keyed on this string, and the CLI resolving
    # symlinks while this did not made a second `--apply` re-copy everything.
    source = Path(legacy_import.source_root_str(configured))
    if not source.is_dir():
        logger.info("first-run import: %s does not exist; nothing to do", source)
        return

    lock = _import_lock_path()
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            age = time.time() - lock.stat().st_mtime
            if age < _LOCK_STALE_SECONDS:
                logger.info("first-run import: another instance holds the lock")
                return
            logger.warning("first-run import: reclaiming a stale lock (%.0fs old)", age)
            lock.unlink(missing_ok=True)
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    except OSError:
        logger.exception("first-run import: could not take the lock")
        return

    try:
        with db_session_module.SessionLocal() as db:
            user = local_mode.get_local_user(db)
            if user is None:
                logger.warning("first-run import: no local identity; skipping")
                return
            # A manifest, exactly as the CLI writes: it is what `--rollback`
            # undoes, and it is the ledger that stops a project the user
            # DELETED from being re-imported on the next launch. Without it,
            # rollback simply does not exist on the path a real user takes.
            import app_paths

            stamp = datetime.now(tz=timezone.utc)
            manifest = (
                app_paths.app_data_dir()
                / f"import-manifest-{stamp:%Y%m%dT%H%M%S}.json"
            )
            report = legacy_import.import_all(
                db, source, local_mode.LOCAL_ORG_ID, user.id,
                apply=True, manifest_path=manifest,
            )
            if not report.records:
                manifest.unlink(missing_ok=True)
        if report.imported:
            logger.info(
                "first-run import: %d project(s) imported from %s",
                len(report.imported),
                source,
            )
        for line in report.failed + report.collisions + report.warnings:
            logger.warning("first-run import: %s", line)
        _persist_import_report(report, source)
    except Exception:  # noqa: BLE001 — a failed import must still yield an app
        logger.exception("first-run import failed; continuing without it")
    finally:
        lock.unlink(missing_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if local_mode.is_local_mode():
        # First run on a fresh machine has no app-data directory, no database
        # and no identity. Order matters: the directories must exist before
        # Alembic opens the database file, or `upgrade` dies with "unable to
        # open database file".
        local_bootstrap.ensure_app_dirs()
        local_bootstrap.ensure_schema(get_settings().database_url)
        with db_session_module.SessionLocal() as db:
            local_mode.ensure_local_identity(db)
        # Fourth, and after the identity: imported rows need an org and a
        # creator.
        #
        # This is SYNCHRONOUS and uvicorn accepts no connection until lifespan
        # startup returns, so it delays the first request by however long the
        # copy takes — 113 MB on the tree this was written against. What it
        # never does is FAIL startup: every exception is swallowed and the app
        # boots anyway. An earlier version of this comment said "never blocks
        # startup" meaning the second thing, and a reviewer planning the
        # desktop splash reasonably read it as the first.
        run_first_run_import()
    PyPSAService.initialize()
    yield


app = FastAPI(
    title="PyPSA GUI API",
    version="1.0.0",
    lifespan=lifespan,
    # Runs before every endpoint on every router: binds the caller's session to
    # its active project so the ~110 routes that name no project resolve
    # per-user instead of through a process global (Step 0b).
    dependencies=[Depends(bind_active_project)],
)

def _csrf_rejection(request: Request) -> JSONResponse | None:
    """
    Refuse a state-changing request that cannot prove same-origin intent.

    Two independent checks, both required — either alone is bypassable:

      1. **Origin/Referer must be allowlisted when present.** A browser always
         attaches one to a cross-origin state-changing request. A request with
         neither header did not come from a cross-site browser context, so it
         is allowed through to check 2; that is not a hole, because the CSRF
         threat model is specifically "attacker page drives the victim's
         browser", and a non-browser client attacking directly has no victim
         cookie to ride on.
      2. **Double-submit token.** The `X-CSRF-Token` header must equal the
         CSRF cookie. An attacker page can make the browser *send* the cookie
         (`SameSite=None`) but cannot *read* it to copy into a header — unless
         its origin is allowlisted and credentialed, which is exactly why
         check 1 and the CORS allowlist are the same decision.

    Requests carrying no session cookie are exempt: there is no authority to
    forge, and refusing them would turn every anonymous 401 into a confusing
    403.
    """
    # Local mode issues no session cookie, so the exemption below would already
    # let every request through — but a stale cookie left in a packaged webview
    # profile would re-arm the check against a user who has no way to obtain a
    # matching CSRF token.
    if local_mode.is_local_mode():
        return None

    settings = get_settings()
    if not request.cookies.get(settings.session_cookie_name):
        return None

    origin = request.headers.get("origin")
    if origin is None:
        origin = security.origin_of_referer(request.headers.get("referer"))
    if origin is not None and not security.is_allowed_origin(origin):
        return JSONResponse(
            status_code=403,
            content={
                "detail": "Cross-origin request rejected.",
                "code": "csrf_origin_rejected",
            },
        )

    if not security.csrf_tokens_match(
        request.cookies.get(settings.csrf_cookie_name),
        request.headers.get(settings.csrf_header_name),
    ):
        return JSONResponse(
            status_code=403,
            content={
                "detail": (
                    "Missing or invalid CSRF token. Reload the page to obtain a "
                    "fresh token, then retry."
                ),
                "code": "csrf_token_invalid",
            },
        )
    return None


# NOTE ON MIDDLEWARE ORDER — Starlette makes the LAST-added middleware the
# OUTERMOST. The three below are therefore declared innermost-first:
#
#   CORS  →  replica id  →  auth + CSRF + undo  →  routes
#
# That order is load-bearing in both directions. CORS must be outermost or a
# 401 from the auth gate returns with no `Access-Control-Allow-Origin` and the
# browser reports it as an opaque CORS failure instead of "log in" (it did,
# before this note). The replica header must sit outside the auth gate so that
# a rejected request still identifies which replica rejected it — otherwise the
# multi-replica QA cases cannot attribute a failure.


@app.middleware("http")
async def undo_snapshot_middleware(request: Request, call_next):
    """
    Push an undo snapshot before every mutating network/io request.

    If the downstream handler returns a 4xx/5xx error the snapshot is popped
    back off so failed mutations don't consume undo slots.

    Also short-circuits with 409 when the solver worker is in-flight — gate
    sits at the top of the chain so a blocked request never costs an undo
    snapshot or a dispatch-invalidation probe.
    """
    path = request.url.path
    normalized_path = path.rstrip("/") or "/"
    is_write = request.method in ("POST", "PUT", "DELETE", "PATCH")

    is_api = normalized_path == "/api" or normalized_path.startswith("/api/")

    # ── CSRF: Origin/Referer check + double-submit token ──────────────────
    # Runs BEFORE session resolution so a forged request is refused without
    # touching the database. Scoped to state-changing methods on /api; safe
    # methods and the public auth paths (login/reset, which have no session to
    # ride on) are exempt.
    if (
        is_api
        and request.method not in security.CSRF_SAFE_METHODS
        and normalized_path not in _AUTH_PUBLIC_PATHS
    ):
        rejection = _csrf_rejection(request)
        if rejection is not None:
            return rejection

    if (
        request.method != "OPTIONS"
        and is_api
        and normalized_path not in _AUTH_PUBLIC_PATHS
    ):
        try:
            with db_session_module.SessionLocal() as db:
                if local_mode.is_local_mode():
                    # The desktop build has no login, so the seeded identity is
                    # injected here rather than resolved from a session cookie.
                    # Re-fetched per request, never cached: sessionmaker runs
                    # with expire_on_commit=True, so a cached User goes detached
                    # and reading user.id raises DetachedInstanceError inside
                    # project_registry / project_acl.
                    request.state.auth_user = local_mode.get_local_user(db)
                else:
                    request.state.auth_user = resolve_request_user(request, db)
        except Exception:
            # Misconfigured/unreachable DB must not surface as an opaque 500 on
            # every /api call (that looked like a broken workbench to reviewers
            # when auth was on without Postgres).
            logger.exception("auth database unavailable while enforcing session")
            if local_mode.is_local_mode():
                # A desktop user has no Postgres to start and no alembic to run;
                # the realistic cause is a second copy holding the SQLite file.
                detail = (
                    "Local database unavailable. Close any other running copy "
                    "of PyPSA GUI and try again."
                )
            else:
                detail = (
                    "Auth database unavailable. Start Postgres (or use a "
                    "sqlite DATABASE_URL), run alembic upgrade head, then "
                    "restart the backend."
                )
            return JSONResponse(status_code=503, content={"detail": detail})
        if request.state.auth_user is None:
            return JSONResponse(
                status_code=401,
                content={"detail": "Authentication required"},
                # Nudge stale Cursor preview sessions to drop cached SPA modules
                # so the next real navigation picks up the login gate.
                headers={"Clear-Site-Data": '"cache"'},
            )

        # ── Session-bound active project, MIDDLEWARE-LOCAL half (Step 0b) ──
        # The binding the ENDPOINT sees is made in `deps.bind_active_project`,
        # an app-level dependency: `BaseHTTPMiddleware` runs the downstream app
        # in a task spawned by its own task group, and that task does not
        # inherit contextvars set here.
        #
        # But this middleware ALSO runs code of its own against "the active
        # network" — the undo snapshot push and the dispatch-invalidation probe
        # below — and that code executes in THIS context. Without the binding
        # here, both silently operated on the process foreground: undo stopped
        # recording, and a topology edit no longer cleared stale solver
        # dispatch, so the Results tab kept showing numbers for a network that
        # had changed. Hence both bindings; they are not redundant.
        try:
            # Local re-import, deliberately. Later in THIS function several
            # blocks do `from services.pypsa_service import PyPSAService`, which
            # makes the name function-local for the whole body — so the
            # module-level import is shadowed here and reading it raises
            # UnboundLocalError. The binding then silently failed (swallowed by
            # the except below) and every middleware-local read fell back to the
            # process foreground, which is precisely the bug this block exists
            # to prevent. Keep this import, or hoist every other one.
            from services.pypsa_service import PyPSAService

            with db_session_module.SessionLocal() as db:
                session_row = resolve_request_session(request, db)
                if session_row is not None:
                    ctx, slot = active_project.resolve_for_session(db, session_row)
                    PyPSAService.bind_request_context(ctx)
                    PyPSAService.bind_request_slot(slot)
                    PyPSAService.bind_request_scratch(
                        active_project.scratch_key(session_row)
                    )
        except Exception:  # noqa: BLE001
            logger.exception("could not bind the session's active project")

    # ── Shutting-down gate (workstream H, step 1) ─────────────────────
    # Set before the quit confirmation is even shown, and cleared again if the
    # user cancels. It lives here for the same reason the solver gate does:
    # this is the one place every mutating request passes through, and the
    # alternative is a check in ~30 endpoints.
    #
    # Hiding the window does NOT do this. The frontend keeps 20 refetch
    # intervals, a 5-minute autosave and a 45 s lock heartbeat running behind a
    # hidden window, and its SSE stream stays connected — so without an actual
    # gate the app carries on writing while the shutdown sequence is flushing.
    #
    # GETs pass: the window is still visible during the confirm dialog and a
    # blank workbench behind it would be alarming for no benefit.
    if (is_write
            and shutdown_service.mutations_gated()
            and path not in _SHUTDOWN_GATE_EXEMPT):
        return JSONResponse(
            status_code=503,
            content={
                "code": shutdown_service.SHUTTING_DOWN_CODE,
                "detail": (
                    "PyPSA GUI is shutting down and is no longer accepting "
                    "changes. If you cancelled the quit, this clears itself."
                ),
            },
        )

    # ── Solver-in-flight gate ─────────────────────────────────────────
    # Refuse writes to /api/network/* and /api/io/* while the LP worker
    # thread is alive (running OR in the post-LP restore window). Without
    # this, the request races the worker's n.add/n.remove and either
    # corrupts the in-memory network or blocks on the PyPSA lock until
    # axios times out at 30 s. Endpoint-level gates on save + preflight
    # cover those two routes with context-specific messages; this is the
    # catch-all for everything else.
    if (is_write
            and any(path.startswith(p) for p in _SOLVER_BLOCKING_PREFIXES)
            and path not in _SOLVER_BLOCKING_EXEMPT
            and not any(path.endswith(s) for s in _SOLVER_BLOCKING_EXEMPT_SUFFIXES)):
        from routers.simulation import _solver_in_flight
        if _solver_in_flight():
            return JSONResponse(
                status_code=409,
                content={
                    "detail": (
                        "Cannot mutate network state while a solver worker is "
                        "active. The LP build is in-flight and mutating the "
                        "in-memory network mid-solve would race with PyPSA's "
                        "internal n.add()/n.remove() calls (vintage expansion, "
                        "slack generators, dispatch fix). Wait for the solver "
                        "to finish or abort it via /api/simulation/abort and "
                        "retry — the lock-status indicator in the header polls "
                        "every 0.5 s."
                    ),
                    "code": "solver_in_flight",
                },
            )

    should_snapshot = (
        is_write
        and path not in _UNDO_EXCLUDE
        and any(path.startswith(p) for p in _UNDO_PREFIXES)
    )

    pushed = False
    if should_snapshot:
        # Coalesce rapid bursts (a canvas drag fires many sequential PUTs) into
        # one snapshot — claim_push_slot() returns False inside the coalesce
        # window so we skip the (100ms+) export. The timer state lives in
        # undo_service so it travels with the undo subsystem.
        from services import undo_service
        if undo_service.claim_push_slot():
            try:
                from routers.network import _push_undo_snapshot
                # _push_undo_snapshot does an n.export_to_netcdf() round-trip which
                # can take seconds on a large network. Running it directly here
                # would freeze the event loop and starve concurrent GETs (notably
                # the /undo/info poll, which then hits the 30s axios timeout and
                # makes the UI look like project data has been lost). Off-load to
                # a worker thread so the loop keeps serving other requests.
                await asyncio.to_thread(_push_undo_snapshot)
                pushed = True
            except Exception:
                pass  # never crash a request because of undo machinery

    # Capture pre-mutation dispatch state. We snapshot the status BEFORE
    # `call_next` so we can compare AFTER — only invalidate if there WAS
    # dispatch beforehand. This lets us skip the clear when the network was
    # already unsolved (no `_t` data to clear) or when the mutation is the
    # very FIRST after a clear (no dispatch state to lose).
    pre_dispatch = None
    will_check_dispatch = (
        is_write
        and path not in _DISPATCH_INVALIDATE_EXCLUDE
        and any(path.startswith(p) for p in _DISPATCH_INVALIDATE_PREFIXES)
    )
    if will_check_dispatch:
        try:
            from services.dispatch_status import dispatch_status
            from services.pypsa_service import PyPSAService
            pre_dispatch = dispatch_status(PyPSAService.get_network())
        except Exception:
            pre_dispatch = None  # treat as "unknown" → don't clear

    response = await call_next(request)

    # Rollback the snapshot if the mutation was rejected by the server.
    if pushed and response.status_code >= 400:
        try:
            from services import undo_service
            undo_service.pop()
        except Exception:
            pass

    # Auto-invalidate solver dispatch on any successful /api/network/*
    # mutation when there WAS dispatch to clear. Centralising this in the
    # middleware means cascade-deletes, /bulk writes, rename, global-
    # constraint mutations, clustering, snapshot reshapes, and any future
    # /api/network/* endpoint all benefit — without each having to call
    # the invalidation helper. PyPSA's `n.add()` / `n.remove()` doesn't
    # auto-clear `_t` tables on topology mutations; without this, dispatch
    # lingers in a misleading half-state ("stale" per the Phase 4 check)
    # that the user sees as "Results tab still has numbers but the solver
    # was never re-run for this topology".
    if (
        will_check_dispatch
        and pre_dispatch is not None
        and pre_dispatch != "none"
        and 200 <= response.status_code < 300
    ):
        try:
            from services import change_log_service
            from services.dispatch_status import clear_dispatch
            from services.pypsa_service import PyPSAService
            with PyPSAService.get_lock():
                cleared = clear_dispatch(PyPSAService.get_network())
            if cleared:
                # Reset simulation lifecycle state so the StatusBar transitions
                # back to "Idle" and /results/* gates return 204 until the
                # user re-runs the solver. Use `_state_update` (lock-protected
                # multi-key write) rather than direct dict writes — the /status
                # reader polls via `_state_snapshot()` which holds the same
                # lock; without it a concurrent reader could observe
                # `status="completed"` paired with `objective=None` mid-write.
                try:
                    from routers.simulation import _state_update
                    _state_update(
                        status="idle",
                        condition=None,
                        objective=None,
                        solve_time=None,
                    )
                except ImportError:
                    pass
                change_log_service.log(
                    "dispatch_invalidated", "Network", "",
                    f"Solver dispatch cleared after mutation: {request.method} {path}",
                )
        except Exception:
            # Never let invalidation machinery crash a successful mutation.
            pass

    return response


@app.middleware("http")
async def replica_identity_middleware(request: Request, call_next):
    """
    Stamp every response with an opaque per-process id.

    Mandated test infrastructure, not diagnostics: without it a sticky proxy
    makes every multi-replica assertion pass while state is still process-local
    — the exact failure mode `smoke/qa_e2e.py` already had when it asserted on
    HTTP 200 alone. Opaque (keyed hash of pid + boot nonce) so it cannot be
    read as topology.
    """
    if local_mode.is_local_mode():
        # Replica identity is multi-replica test infrastructure. One process,
        # one machine, no proxy — the header is dead weight locally.
        return await call_next(request)
    response = await call_next(request)
    response.headers[security.REPLICA_HEADER] = security.replica_id()
    return response


app.add_middleware(
    CORSMiddleware,
    # Explicit allowlist, env-driven via `cors_allowed_origins`.
    #
    # This replaced `allow_origin_regex=r"https://.*\.cursorusercontent\.com"`,
    # and the replacement had to ship together with the CSRF check above. Under
    # the wildcard, ANY page on that domain was an allowlisted *credentialed*
    # origin: it passed the Origin/Referer check, CORS let it read the response,
    # and it could therefore read the double-submit token and forge with it.
    # `SameSite=None` means the session cookie rides along. Adding an origin
    # here grants it CSRF-forging capability — review the two together.
    allow_origins=sorted(security.allowed_origins()),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[security.REPLICA_HEADER],
)

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
# Admin is a multi-tenant surface, and there is no second tenant locally.
# `/api/admin/legacy-projects/{name}/claim` shutil.moves whole project
# directories, which is the one that actually matters.
#
# A router-level dependency, NOT an `if` around include_router: conftest
# imports `main` with local mode unset, so an import-time guard would register
# the router permanently and no fixture could un-register it. The guard itself
# lives in `local_mode` so the four `legacy_migrate` doors — two here, two on
# the projects router, which cannot be gated wholesale — state the reason once.
app.include_router(
    admin.router,
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(local_mode.reject_in_local_mode)],
)
app.include_router(network.router, prefix="/api/network", tags=["network"])
# Mount /api/network/cluster from the dedicated clustering router. Sharing the
# /api/network prefix keeps the endpoint adjacent to other network mutations.
app.include_router(clustering.router, prefix="/api/network", tags=["clustering"])
# Per-period capacity bounds (vintage expansion). Sits under /api/network so
# its mutations pick up the auto-undo + dispatch-invalidation middleware.
app.include_router(vintage.router, prefix="/api/network", tags=["vintage"])
app.include_router(changelog.router, prefix="/api/changelog", tags=["changelog"])
app.include_router(simulation.router, prefix="/api/simulation", tags=["simulation"])
app.include_router(results.results_router, prefix="/api/results", tags=["results"])
# Sequential multi-project solve queue (Phase A / C1). Sits under /api/simulation
# so it's adjacent to the foreground solve lifecycle it shares (_state, log_stream).
app.include_router(solve_queue.router, prefix="/api/simulation/queue", tags=["solve-queue"])
app.include_router(io.router, prefix="/api/io", tags=["io"])
# Path-scoped per-project READ endpoints (B6). Mounted BEFORE projects/snapshots
# so its `{project_id}/network/...` routes (static `network` segment) resolve
# first — they don't collide with projects.py's `{name}/<static>` routes, but
# registering ahead keeps the more-specific path templates unambiguous. These
# reads resolve a SPECIFIC project's context via ProjectDep WITHOUT moving the
# active foreground; GETs aren't gated by the write-middleware.
app.include_router(project_network.router, prefix="/api/projects", tags=["project-network"])
# Compare/results-summary analytics (specific `/{name}/compare-state` +
# `/{name}/results-summary` paths) — registered before projects.router for
# clarity; the extra path segment means the `/{name}` catch-all never shadows it.
app.include_router(compare.router, prefix="/api/projects", tags=["compare"])
app.include_router(projects.router, prefix="/api/projects", tags=["projects"])
app.include_router(snapshots.router, prefix="/api/projects", tags=["snapshots"])
# Chatbot file uploads (Phase A) — per-project file storage at
# `projects/<name>/uploads/`. Mounted under the same /api/projects prefix
# so its routes (`/{name}/uploads`, `/{name}/uploads/{file_id}/...`) follow
# the existing project-scoped URL pattern. Phase B adds chat tools that
# consume these uploads; Phase C wires them into the Anthropic multimodal API.
app.include_router(uploads.router, prefix="/api/projects", tags=["uploads"])
# Chatbot integration v6 (Phase 2 stub — no Anthropic SDK yet). SSE stream +
# confirmation card lifecycle + abort endpoint. The router is mounted under
# /api/chat; Phase 3 wires the real LLM call without changing route shapes.
app.include_router(chat.router, prefix="/api/chat", tags=["chat"])


@app.on_event("startup")
def _chatbot_startup_check() -> None:
    """
    Soft probe — log whether ANTHROPIC_API_KEY is set so operators see a
    clear message at startup. NEVER fail boot for a missing key; the
    chatbot panel renders disabled on the frontend if the key is absent
    (chat_health() reports `anthropic_api_key_present`).
    """
    import logging
    log = logging.getLogger("pypsa_gui.chat")
    if os.environ.get("ANTHROPIC_API_KEY"):
        log.info("chatbot: ANTHROPIC_API_KEY present — chat panel enabled.")
    else:
        log.warning(
            "chatbot: ANTHROPIC_API_KEY not set. The chat panel will render "
            "disabled until the env var is configured. Set it in the "
            "backend process environment to enable the assistant.",
        )


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "pypsa_version": pypsa.__version__,
        # True for the web deployment, False for the desktop build. This field
        # is the SPA's boot contract — `AuthModeProvider` overwrites its
        # compile-time flag from it, and spa.html's pre-React gate skips
        # /api/auth/me when it is false — so flipping it is what turns the login
        # gate off locally. Remove it only together with the frontend read: an
        # older cached bundle reading a missing field falls back to "auth off"
        # and would render the web workbench with no login gate.
        "auth_enabled": not local_mode.is_local_mode(),
    }


# ── Static SPA (must be LAST) ────────────────────────────────────────────────
# FastAPI matches in registration order, and `health` above is declared AFTER
# the routers — so this has to come after BOTH or the catch-all swallows
# /api/health, which breaks spa.html's boot gate and AuthModeProvider.
#
# Mounted at the document root because every asset reference in dist/ is
# root-absolute (/assets/…, /brand.css); a sub-path mount 404s every asset.
def _dist() -> Path:
    # Read per call, not captured at import: tests point FRONTEND_DIST at a
    # tmpdir, and a frozen app points it at its bundled copy.
    return Path(get_settings().frontend_dist)


class _DistAssets(StaticFiles):
    """
    StaticFiles whose directory is resolved per request.

    The stock class captures `directory` at construction. That is wrong here in
    both directions: `frontend_dist` already points at a real tree when this
    module is imported, so a captured directory would ignore any later
    FRONTEND_DIST (tests, and a frozen app that sets it from the bundle path);
    and a checkout with no `dist/` yet must not fail at import.

    It stays a Mount rather than folding into the catch-all below because
    Starlette Mounts do NOT run app-level dependencies, and this app applies
    `Depends(bind_active_project)` to every APIRoute. Serving assets through the
    catch-all would cost a session lookup plus a project resolve for every
    JS/CSS/image on a cold SPA load.

    `lookup_path` is the right seam: it keeps Starlette's own containment check,
    which is what refuses `..` traversal out of the asset directory.
    """

    def lookup_path(self, path: str) -> tuple[str, os.stat_result | None]:
        self.all_directories = [_dist() / "assets"]
        return super().lookup_path(path)


app.mount("/assets", _DistAssets(check_dir=False), name="assets")


@app.api_route("/{full_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
def serve_spa(full_path: str, request: Request):
    dist = _dist()
    if not dist.is_dir():
        raise HTTPException(status_code=503, detail="Frontend not built. Run `npm run build`.")

    path = "/" + full_path
    if static_gate.is_static_asset(path):
        candidate = (dist / full_path).resolve()
        if not candidate.is_relative_to(dist.resolve()) or not candidate.is_file():
            raise HTTPException(status_code=404, detail="Not found")
        return FileResponse(candidate)

    # NOT request.state.auth_user: the auth middleware only sets it for /api/*
    # paths, so for an HTML route it is always None and every authenticated deep
    # link would fall through to the login document.
    authed = local_mode.is_local_mode()
    if not authed:
        try:
            with db_session_module.SessionLocal() as db:
                authed = resolve_request_user(request, db) is not None
        except Exception:  # noqa: BLE001 — a DB outage must still serve the shell
            authed = False

    kind, target = static_gate.decide_route(
        path, local_mode=local_mode.is_local_mode(), authed=authed
    )
    if kind == "redirect":
        return RedirectResponse(url=target, status_code=302)
    return FileResponse(dist / target)
