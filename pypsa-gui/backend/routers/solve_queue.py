"""
HTTP surface for the sequential multi-project solve queue (Phase A / C1).

Thin layer over `services.solve_queue.solve_queue` — the dispatcher singleton
owns all state and threading. Mounted under `/api/simulation/queue` in main.py.

Endpoints:
    POST   /api/simulation/queue                        enqueue a saved project
    GET    /api/simulation/queue                        list all jobs (FIFO order)
    GET    /api/simulation/queue/{job_id}/log_history   a job's log lines so far
    GET    /api/simulation/queue/{job_id}/log_stream    SSE live tail of a job's log
    POST   /api/simulation/queue/{job_id}/abort         abort running / cancel queued
    POST   /api/simulation/queue/clear_finished         drop terminal jobs from listing

AUTHORIZATION. All six routes take `db`/`user` and authorize against the
caller. Three of the original four did not until P-1, and the queue is a
PROCESS-GLOBAL singleton shared by every org — see the per-route docstrings
for what each one now refuses, and `_may_see` / `_may_abort` for the two
different boundaries they use and why they differ.
"""
from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

import local_mode
from db.models import User
from db.session import get_db
from deps import optional_user
from services.solve_queue import _TERMINAL, _row_epoch, solve_queue

router = APIRouter()


class EnqueueRequest(BaseModel):
    project_id: str


def _config_snapshot_for(project_key: str, project_dir) -> str | None:
    """
    The solver config this enqueue should freeze onto the job, as JSON.

    Resolution mirrors what the dispatcher used to do at run time, but ONCE and
    HERE, where the request exists: the resident context's live config when the
    project is resident (so the user's unsaved solver edits are honoured, which
    is what they expect from the Run button), else the `solver_config.json` the
    project has on disk. Returns None only if neither can be read, which leaves
    the dispatcher on its pre-snapshot fallback.
    """
    import json as _json
    from dataclasses import asdict

    from services.pypsa_service import PyPSAService

    try:
        resident = PyPSAService.get_context(project_key)
        if resident is not None:
            cfg = resident.solver_state.get("solver_config")
            if cfg is not None:
                return _json.dumps(asdict(cfg))
        on_disk = project_dir / "solver_config.json"
        if on_disk.exists():
            return on_disk.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001 — never fail an enqueue over bookkeeping
        pass
    return None


@router.post("")
def enqueue_solve(
    req: EnqueueRequest,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """
    Queue a project to solve. The dispatcher loads the SAVED version from disk,
    so the project must already exist on disk with a network — the caller (the
    frontend) saves the foreground project before enqueuing it. Returns the job,
    including its queue position and an `already_queued` flag that is true when
    an existing job was returned instead of a new one.

    AUTHORIZATION (Step 0a): the project arrives in the BODY, not the path, so
    this route is invisible to a path-parameter inventory — it was not among
    the 14 the plan counted, and it was the widest remaining hole: it used
    `_safe_project_dir(name)`, which resolves any org's project under the
    shared root, and then handed that path to a background thread that solves
    and SAVES it. Resolution now goes through the caller's org and ACL, and the
    authorized directory travels with the job, because the dispatcher runs with
    no request and no user and cannot authorize anything itself.
    """
    from services import project_registry

    project_registry.require_user(user)
    project = project_registry.resolve_project(db, user, req.project_id)
    project_dir = project_registry.project_dir(project)
    if not (project_dir / "network.nc").exists():
        raise HTTPException(
            404,
            f"Project '{project.name}' has no saved network on disk. Save the "
            "project before queuing it to solve.",
        )
    # Idempotent by project: a second enqueue of a project that already has a
    # queued or running job returns THAT job with `already_queued: true` and
    # creates nothing. 200, not 409 — the caller's intent ("this project should
    # be solving") is already satisfied, and an error would make every client
    # re-implement the check it just handed to the server.
    #
    # The snapshot is resolved BEFORE `enqueue_unique`, here, where the request
    # and the authorized directory both still exist — the dispatcher runs on a
    # worker thread with neither (R24). It is passed INTO `enqueue_unique` as a
    # constructor argument, not assigned onto `job` afterward: `enqueue_unique`
    # publishes the job to the dispatcher's queue (`self._q.put(jid)`, which
    # wakes a possibly-idle dispatcher thread) before returning, so an
    # assign-after-return has a real TOCTOU window where the dispatcher reads
    # the job's config before this route gets back to setting it — the
    # pre-fix bug, reintroduced for one job in one race. Passing it in means
    # the job is never visible to the dispatcher with a missing snapshot.
    # Resolving it even when `created` turns out False is wasted work but not
    # wrong: `enqueue_unique` discards it for an idempotent re-enqueue and the
    # existing job keeps the config IT was created with.
    snapshot = _config_snapshot_for(project_registry.registry_key(project), project_dir)
    # `enqueued_by_user_id` stamps the ACTING user alongside the org-scoped
    # directory this route already resolved. Keying per-user dismiss on project
    # access instead would let two users sharing a project dismiss each other's
    # rows — the exact thing per-user dismiss exists to prevent. The row INSERT
    # itself happens INSIDE `enqueue_unique`, before the job is published to
    # the dispatcher — inserting here, after it returned, left a window where a
    # fast-failing job went terminal against a missing row and boot
    # reconciliation re-ran it (see `enqueue_unique`'s docstring).
    job, created = solve_queue.enqueue_unique(
        project.name,
        project_key=project_registry.registry_key(project),
        storage_dir=str(project_dir),
        solver_config_json=snapshot,
        enqueued_by_user_id=user.id,
    )
    return {**(solve_queue.get_job(job.id) or job.to_public(None)), "already_queued": not created}


# ── Authorization helpers (P-1) ─────────────────────────────────────────────
# The queue singleton is process-global and its jobs are ordered against ONE
# shared solver, so every caller in every org reads the same list. `project_key`
# is `org_uuid:project_uuid` (`services/solve_queue.py:374`), which is the only
# tenancy information a job carries — the dispatcher runs with no request and no
# user, so nothing downstream can authorize anything.
#
# Fields that name the work rather than measure it. `error` is included because
# a failure message routinely quotes a project name, a path or a solver detail.
#
# `objective` / `solve_time` / `condition` joined this set in Task 16a's review
# round 1 (Important 2, human-adjudicated): before persisted rows were merged
# into the listing, these were never redacted because the queue was
# self-clearing — a finished job eventually left `_jobs` and took its result
# with it. Once a terminal row can survive indefinitely (interrupted jobs are
# permanently un-restartable back into memory; any job's history now outlives
# a restart), leaving them un-redacted let any authenticated caller read every
# organisation's solved system cost, forever. Redacting them still preserves
# the queue-depth rationale below — a foreign job still shows its `status` and
# `position`, so a caller can see someone is ahead of them, just not what that
# job's model cost.
_REDACTED = ("project_id", "project_key", "error", "objective", "solve_time", "condition")


def _org_prefix(db: DBSession, user: User) -> str | None:
    """
    The `project_key` prefix identifying the caller's org, or None if they have
    no membership. ONE membership lookup for the whole listing — the alternative
    (an ACL query per job) turns a polled endpoint into an N-query endpoint.
    """
    from services.tenancy_service import get_user_membership

    membership = get_user_membership(db, user.id)
    return None if membership is None else f"{membership.org_id}:"


def _project_uuid(job: dict, prefix: str | None) -> uuid.UUID | None:
    """
    The project a job names, but only when the key belongs to the caller's org.

    Returns None for another org's job, an unkeyed one, and a key whose uuid
    half does not parse — all three are "no project you could be entitled to",
    which is the only distinction the callers below need.
    """
    key = job.get("project_key")
    if key is None or prefix is None or not key.startswith(prefix):
        return None
    try:
        return uuid.UUID(key[len(prefix):])
    except ValueError:
        return None


def _may_see(job: dict, prefix: str | None, allowed: set[uuid.UUID]) -> bool:
    """
    Whether the caller may see WHOSE job this is — the PROJECT ACL, not the org.

    `allowed` is resolved once per request by `project_acl.accessible_project_ids`,
    so this stays a set membership test however long the queue is. Org
    membership alone would be the wrong boundary: `GET /api/projects/` already
    filters a caller's own project list through `can_access_project`, so an
    org-wide answer here would hand back names that endpoint refuses them.

    A job with no `project_key` is a legacy or hand-made artefact —
    `enqueue_solve` has stamped a key on every job since Step 0a. Under auth it
    cannot be attributed to an org, therefore it cannot be authorized, so it
    fails closed. In local mode there is exactly one tenant (`local_mode`, one
    seeded org + user), so the only possible owner IS the caller.

    A job whose project row has been DELETED is redacted for the same reason:
    there is no ACL left to consult. `_may_abort` deliberately answers
    differently — see there.
    """
    if job.get("project_key") is None:
        return local_mode.is_local_mode()
    project_id = _project_uuid(job, prefix)
    return project_id is not None and project_id in allowed


def _redact(job: dict) -> dict:
    """Blank the identifying fields, keep the shared-queue facts."""
    return {**job, **{field: None for field in _REDACTED}}


def _may_abort(db: DBSession, user: User, job: dict) -> bool:
    """
    Whether the caller may STOP this job.

    Same predicate as `_may_see` — `can_access_project` — reached directly
    rather than through the batch, because abort concerns exactly one job and
    the single-project path is the authoritative one `enqueue_solve` uses.
    `tests/test_project_acl.py` pins that the batch agrees with it.

    ONE deliberate difference. A job whose project row has since been DELETED
    is invisible in the listing (no ACL to consult, so identity fails closed)
    but stays abortable by its own org. `_delete_project_db` removes the row and
    the directory without touching the queue, so a running solve outlives its
    project; refusing here would strand it with no way to stop it and no way to
    free the shared solver. The listing still shows the job's id and status, so
    an operator can find it — they just cannot learn whose it was.
    """
    key = job.get("project_key")
    if key is None:
        return local_mode.is_local_mode()
    prefix = _org_prefix(db, user)
    project_id = _project_uuid(job, prefix)
    if project_id is None:
        return False

    from db.models import Project
    from services import project_acl

    project = db.get(Project, project_id)
    if project is None:
        return True  # orphaned by a delete; see above
    if f"{project.org_id}:" != prefix:
        return False  # the key's org half lied about the row's real owner
    return project_acl.can_access_project(db, user, project)


def _parse_job_id(job_id: str) -> uuid.UUID | None:
    """
    The path parameter as a UUID, or None if it is not one.

    Declared `str` in the signature rather than `uuid.UUID` deliberately: FastAPI
    would answer 422 for a malformed id, which is a different answer from the 404
    an unknown id gets — and telling a caller "that is not even a valid id"
    re-opens the oracle the byte-identical 404 exists to close.
    """
    try:
        return uuid.UUID(job_id)
    except (ValueError, AttributeError, TypeError):
        return None


def _visible_job_or_404(db: DBSession, user: User, job_id: str) -> dict:
    """
    The job, if the caller may SEE it; the genuine not-found 404 otherwise.

    404, never 403, with the not-found message BYTE FOR BYTE — same reasoning as
    `abort_job`: a 403 would confirm the id exists and hand back the enumeration
    the redacted listing just took away.

    `_may_see`, not `_may_abort`. The two deliberately disagree for a job
    orphaned by a project delete: it stays ABORTABLE by its own org so a running
    solve can still be stopped and the shared solver freed. That exception is
    about stopping work, not about reading the deleted project's log, so the
    listing predicate is the right one here.
    """
    from services import project_acl

    not_found = HTTPException(404, f"No solve job with id {job_id}.")
    parsed = _parse_job_id(job_id)
    if parsed is None:
        raise not_found
    job = solve_queue.get_job(parsed) or _persisted_public_or_none(parsed)
    if job is None:
        raise not_found
    prefix = _org_prefix(db, user)
    allowed = project_acl.accessible_project_ids(db, user, [_project_uuid(job, prefix)])
    if not _may_see(job, prefix, allowed):
        raise not_found
    return job


def _persisted_public_or_none(parsed: uuid.UUID) -> dict | None:
    """
    A persisted-only job (its row survives, its `SolveJob` did not — every
    `interrupted` job after a restart, by construction) in `to_public()`
    shape, or None. The listing merges these rows in (Task 16a), so an id it
    serves must ALSO resolve on the detail routes — resolving through
    `solve_queue.get_job` alone answered the existence-oracle 404 for every
    such job, indistinguishable from a bad id, with no way for the caller to
    tell "no retained log" from "this job never existed".
    """
    from services import solve_job_store

    row = solve_job_store.load_job(parsed)
    return _persisted_job_public(row) if row is not None else None


def _sse_line(text: object) -> str:
    """One SSE `data:` frame. Newlines would terminate the frame early."""
    safe = str(text).replace("\n", " ").replace("\r", "")
    return f"data: {safe}\n\n"


def _done_event(job_id: uuid.UUID) -> str:
    """The `event: done` SSE frame carrying a job's current/terminal snapshot."""
    job = solve_queue.get_job(job_id) or _persisted_public_or_none(job_id) or {}
    payload = json.dumps({
        "status": job.get("status"),
        "objective": job.get("objective"),
        "solve_time": job.get("solve_time"),
        "condition": job.get("condition"),
    })
    return f"event: done\ndata: {payload}\n\n"


@router.get("/{job_id}/log_history")
def job_log_history(
    job_id: str,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """
    The lines this job emitted — live while it runs, retained once terminal.

    Readable regardless of which project the caller is viewing and regardless
    of whether the job's context is still resident, because the queue lives on
    the JOB. `/api/simulation/log_history` answers a different question (what
    the caller's ACTIVE context last logged) and is unchanged.
    """
    from services import project_registry

    project_registry.require_user(user)
    job = _visible_job_or_404(db, user, job_id)
    parsed = _parse_job_id(job_id)
    q = solve_queue.get_log_queue(parsed)
    lines = q.history() if q is not None else []
    return {"lines": lines, "status": job["status"]}


@router.get("/{job_id}/log_stream")
async def job_log_stream(
    job_id: str,
    request: Request,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """
    Server-Sent Events for ONE job's log.

    Reads through `BufferedLogQueue.subscribe()`, not `get()`. The legacy `get()`
    consumer is DESTRUCTIVE — it pops — so draining the same queue here would
    race `/api/simulation/log_stream` for the foreground's lines and each would
    see half of them. The fanout deque is exactly the side channel that exists
    for a second consumer, and it never observes the `None` close sentinel.

    SUBSCRIBE BEFORE REPLAY. `history()` snapshots under a lock and returns a
    copy; `subscribe()` only sees lines `put()` AFTER it runs. A line landing
    in the gap between the two belongs to neither and is silently lost — and
    that gap is not microseconds: every `yield` in the replay loop suspends
    this generator while Starlette writes the frame to the socket, so with a
    full 5000-line history and a slow consumer the gap is the wall-clock time
    to write 5000 frames. Subscribing first trades that gap for an occasional
    DUPLICATE at the seam (a line already in `history()` may also arrive again
    via the live deque) — the correct direction: the legacy
    `/api/simulation/log_stream` documents the same choice already
    (`routers/simulation.py:912-914`, duplicate-prone rather than gap-prone).
    That duplicate window is tiny by comparison — `subscribe()` and
    `history()` are adjacent statements with no I/O between them, so at most
    one line can land in it — and harmless regardless of size: every consumer
    (`SolveQueuePanel.tsx`'s `JobLogPanel`) renders lines as plain strings
    into a `<pre>`, not React-keyed and not de-duplicated, so a repeated line
    is just a repeated row.

    Termination is therefore the job's own status, not a sentinel: stop once
    the job is terminal AND the deque has drained, then emit `done`. A job
    cleared out of the queue entirely (`clear_finished` ran mid-stream) reads
    back as `get_job() is None` — also treated as terminal, or the loop would
    poll forever for a status that can no longer arrive.
    """
    from services import project_registry

    project_registry.require_user(user)
    _visible_job_or_404(db, user, job_id)
    parsed = _parse_job_id(job_id)

    async def generate():
        q = solve_queue.get_log_queue(parsed)
        if q is None:
            # No queue was ever published for this job — most commonly a
            # caller attaching in the `queued` window, before the dispatcher's
            # claim block sets `log_queue` at the `running` flip. Emit `done`
            # immediately: without it, a browser EventSource auto-reconnects
            # on this frame-then-close, looping every few seconds until the
            # job starts, and any consumer awaiting `done` waits forever.
            yield "data: No log for this job\n\n"
            yield _done_event(parsed)
            return

        sub_id, dq = q.subscribe()
        try:
            for line in q.history():
                if await request.is_disconnected():
                    return
                yield _sse_line(line)

            while True:
                if await request.is_disconnected():
                    return
                drained = False
                while dq:
                    drained = True
                    yield _sse_line(dq.popleft())
                if drained:
                    continue
                snapshot = solve_queue.get_job(parsed) or {}
                if not snapshot or snapshot.get("status") in _TERMINAL:
                    # The producer's guarantee is "put lines, then flip status":
                    # by the time `status` reads as terminal, every line the
                    # solve emitted has already been queued. But there is a GIL
                    # switch point between the `while dq` drain above and this
                    # status read, wide enough for that final `put()` to land
                    # in it — so drain once more before stopping, or the tail
                    # (often the `[PHASE] Failed: ...` / `TRACEBACK: ...` lines)
                    # is silently dropped.
                    while dq:
                        yield _sse_line(dq.popleft())
                    break
                await asyncio.sleep(0.25)
        finally:
            # A closed browser tab would otherwise leak the deque + dict entry.
            q.unsubscribe(sub_id)

        yield _done_event(parsed)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# Bounds the persisted-history slice `_merged_jobs` reads on every poll
# (Task 16a review round 1, Important 3): unbounded, that query's cost grows
# with every terminal job the process has EVER recorded, on a route polled
# every 1.5s while any job is active. 200 is generous for "what a caller would
# plausibly want to see" while keeping the worst case bounded regardless of
# process uptime. `load_by_status` returns the NEWEST rows first when capped,
# so the ones dropped are the oldest history, not the most relevant. Deliberately
# NOT applied to `clear_finished` — that is a rare, deliberate, super-admin
# wipe, not a hot poll, and capping it would leave un-wiped rows behind.
_PERSISTED_HISTORY_LIMIT = 200


def _persisted_job_public(row: dict) -> dict:
    """
    A `solve_jobs` row (from `solve_job_store.load_by_status`), reshaped to
    the same shape `SolveJob.to_public()` returns, so `_merged_jobs` can hand
    `list_queue` ONE uniform list and the existing authorization pass
    (`_may_see` / `_redact`, keyed on `project_key`) needs no branch for
    "was this job live or persisted".

    `position` is always `None`. Every row this is ever called with is
    TERMINAL — `_merged_jobs` only loads `_TERMINAL` statuses — and a queue
    position is only meaningful for a job still waiting to run. Fabricating
    one for a job that finished, possibly restarts ago, would be a made-up
    number with no actual queue behind it.
    """
    started_at = row.get("started_at")
    finished_at = row.get("finished_at")
    return {
        "id": str(row["id"]),
        "project_id": row.get("project_id"),
        "project_key": row.get("project_key"),
        "status": row.get("status"),
        "position": None,
        "objective": row.get("objective"),
        "solve_time": row.get("solve_time"),
        "condition": row.get("condition"),
        "error": row.get("error"),
        "enqueued_at": _row_epoch(row.get("enqueued_at")),
        "started_at": _row_epoch(started_at) if started_at is not None else None,
        "finished_at": _row_epoch(finished_at) if finished_at is not None else None,
    }


def _merged_jobs() -> list[dict]:
    """
    Every in-memory job, plus persisted TERMINAL rows for jobs that are no
    longer resident — the gap Task 16a closes.

    Why a job can be persisted but not resident: a restart drops EVERY entry
    from `_jobs` (a fresh `SolveQueue()` singleton), and boot reconciliation
    (`solve_job_store.reconcile_on_boot`) only ever re-admits `queued` rows
    back into memory — a `running` row becomes `interrupted` and is
    DELIBERATELY never re-enqueued (R25's crash-loop guard: auto-retrying a
    job that crashed the process would crash-loop the boot). So the only way
    an `interrupted` job — or any terminal job from before the last restart —
    can ever reach a caller is by being read back from the table here, at the
    listing's read boundary. Nothing is re-admitted to `_jobs`; a persisted
    row that matches a live job is simply not fetched into the result at all
    (see below), so this can never put a terminal job back in front of the
    dispatcher.

    IN-MEMORY WINS for any id present in both. `solve_queue.list_jobs()` is
    the FIRST source and its ids are excluded from what the DB query is
    allowed to contribute — a live job's `status` / `started_at` / `position`
    can be ahead of what was last mirrored to its row (`record_status` runs
    outside the dispatcher's lock and is best-effort), and a stale row must
    never overwrite a job the dispatcher is actively running. Filtering by id
    achieves this without ever inspecting the persisted row's own status: even
    a persisted row that happens to read TERMINAL for a job that is actually
    still live (a lagging mirror) is dropped, because its id is already
    accounted for by the in-memory copy.

    ORDERING: `_order` is already ascending by `enqueued_at` by construction
    (a job is appended to `_jobs` and `_order` together, under `_lock`, at the
    moment its `enqueued_at = time.time()` is stamped). `load_by_status` is
    asked for the `_PERSISTED_HISTORY_LIMIT` NEWEST rows (see there), then
    re-sorted ascending together with the live ones by that one key.
    Combining and re-sorting gives a single globally chronological list — a
    caller's queue view and job history read oldest-enqueued-first either way,
    restart or not — and a STABLE sort (Python's `sorted`) means the common
    case (no persisted-only rows to add) reproduces `list_jobs()`'s existing
    order exactly, so this is additive over the pre-Task-16a behaviour rather
    than a new ordering rule.
    """
    from services import solve_job_store

    jobs = solve_queue.list_jobs()
    live_ids = {job["id"] for job in jobs}
    persisted_only = [
        _persisted_job_public(row)
        for row in solve_job_store.load_by_status(_TERMINAL, limit=_PERSISTED_HISTORY_LIMIT)
        if str(row["id"]) not in live_ids
    ]
    return sorted(jobs + persisted_only, key=lambda job: job["enqueued_at"])


@router.get("")
def list_queue(
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """
    All jobs in FIFO order, live ones merged with persisted history
    (`_merged_jobs`, Task 16a). `current` is the id of the running job, if any.

    AUTHORIZATION (P-1): this REDACTS, it does not filter. `position`
    (`SolveJob.to_public`) is the 1-based place in a GLOBALLY sequential queue,
    because one solver serves every org — a caller's wait genuinely depends on
    jobs they do not own. Dropping other orgs' rows would leave them at
    "position 4" with one job visible and no way to reconcile the number, so
    queue depth (the thing they legitimately need) becomes unreadable. The
    same predicate applies uniformly to a persisted row — `_may_see` /
    `_project_uuid` only ever look at `project_key`, which a persisted row
    carries exactly like a live one, so a caller cannot see another
    organisation's job merely because it was read back from `solve_jobs`
    instead of `_jobs`.

    So every job comes back with `id`, `status`, `position` and its timings
    intact, and the fields that say WHOSE work it is (`project_id`,
    `project_key`, `error`) OR WHAT IT PRODUCED (`objective`, `solve_time`,
    `condition` — added in Task 16a's review, since a persisted row can now
    outlive the queue that used to self-clear it) are nulled for jobs the
    caller cannot access. `current` is the true running job id only when the
    caller may see it — otherwise null, since the id alone was enough to abort
    it before this change.

    "Cannot access" is the PROJECT ACL, resolved for the whole listing in one
    batch (`accessible_project_ids`). This endpoint is polled every 1.5s while
    a job is active, so a `can_access_project` call per job would be an N+1 on
    a hot path — and answering on org membership instead would leak names that
    `GET /api/projects/` already hides from the same caller.
    """
    from services import project_acl, project_registry

    project_registry.require_user(user)
    prefix = _org_prefix(db, user)
    jobs = _merged_jobs()
    allowed = project_acl.accessible_project_ids(
        db, user, (_project_uuid(job, prefix) for job in jobs)
    )
    seen = [_may_see(job, prefix, allowed) for job in jobs]
    current = next(
        (job["id"] for job, ok in zip(jobs, seen) if ok and job["status"] == "running"),
        None,
    )
    return {
        "jobs": [job if ok else _redact(job) for job, ok in zip(jobs, seen)],
        "current": current,
    }


@router.post("/{job_id}/abort")
def abort_job(
    job_id: str,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """
    Abort a running job (signals its stop_event) or cancel a queued one.

    AUTHORIZATION (P-1): 404, never 403, when the caller may not touch this
    job, with the message the genuine not-found case uses BYTE FOR BYTE. A 403
    would confirm that a job with this id exists and hand back the enumeration
    the redacted listing just took away — the same reasoning
    `project_registry.find_project` documents for projects. A malformed id gets
    the SAME 404, for the same reason `_parse_job_id` declares `job_id: str`
    rather than `uuid.UUID`.

    The job is fetched and authorized BEFORE `abort()`, which is destructive:
    it flips a queued job to cancelled or sets a running job's stop_event.
    """
    from services import project_registry

    project_registry.require_user(user)
    not_found = HTTPException(404, f"No solve job with id {job_id}.")
    parsed = _parse_job_id(job_id)
    if parsed is None:
        raise not_found
    job = solve_queue.get_job(parsed)
    if job is None:
        # Persisted-only: the row survived a restart, the `SolveJob` did not —
        # terminal by construction (`queued` rows are re-admitted at boot,
        # `running` rows become `interrupted`). Nothing to stop; answer the
        # same authorized no-op an in-memory TERMINAL job gets from `abort()`
        # rather than the existence-oracle 404 for an id the listing serves.
        persisted = _persisted_public_or_none(parsed)
        if persisted is None or not _may_abort(db, user, persisted):
            raise not_found
        return persisted
    if not _may_abort(db, user, job):
        raise not_found
    res = solve_queue.abort(parsed)
    if res is None:
        # Cleared by a concurrent clear_finished between the two lookups.
        raise not_found
    return res


@router.post("/clear_finished")
def clear_finished(
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """
    Remove completed/failed/aborted jobs from the listing. Super-admin only.

    AUTHORIZATION (P-1): the queue is one process-global list, so this drops
    EVERY org's terminal jobs — an operation that crosses org boundaries by
    construction. `_is_org_admin` (`services/project_acl.py:37-39`) is the wrong
    gate for it: an org admin's authority stops at their own org. The
    instance-wide tier is `User.is_super_admin`, which is what `routers/admin.py`
    already treats as global.

    The service call is deliberately left unfiltered rather than given a
    per-caller predicate: this gate is what makes "clear everything" legitimate,
    and a predicate here would be dead code that reads like a second, weaker
    authorization path.
    """
    from services import project_registry

    project_registry.require_user(user)
    if not user.is_super_admin:
        raise HTTPException(
            403,
            "Clearing finished jobs empties the queue for every organization, "
            "so it is restricted to super-admins.",
        )
    return {"removed": solve_queue.clear_finished()}
