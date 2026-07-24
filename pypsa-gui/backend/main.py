import asyncio
import os
from contextlib import asynccontextmanager
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
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from routers import (
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

# Prefixes whose mutations should auto-invalidate solver dispatch. Narrower
# than _UNDO_PREFIXES: importing a bundle (/api/io/*) replaces the network
# wholesale with a state that may already include a solved dispatch — we
# don't want to clear that. Restoring a project snapshot is handled by the
# /api/projects/* router which also brings its own dispatch.
_DISPATCH_INVALIDATE_PREFIXES = ("/api/network/",)
_DISPATCH_INVALIDATE_EXCLUDE = {"/api/network/undo", "/api/network/undo/info"}

# Undo-snapshot push coalescing now lives in services.undo_service
# (claim_push_slot) so the timer travels with the undo subsystem — the
# rationale (drag-burst collapse, granularity trade-off) is documented there.
# The middleware calls it below, before the expensive netcdf export.


@asynccontextmanager
async def lifespan(app: FastAPI):
    PyPSAService.initialize()
    yield


app = FastAPI(title="PyPSA GUI API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
    is_write = request.method in ("POST", "PUT", "DELETE", "PATCH")

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
            from fastapi.responses import JSONResponse
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
    return {"status": "ok", "pypsa_version": pypsa.__version__}
