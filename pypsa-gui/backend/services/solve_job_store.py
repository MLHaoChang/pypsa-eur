"""
The one place the solve queue touches the database.

The dispatcher runs on a worker thread with no request, no user and no session,
and `services/solve_queue.py` deliberately imports no ORM — the module is pure
threading and bookkeeping. This module is the seam: every function opens its own
short-lived `SessionLocal()`, commits, and NEVER raises. A database hiccup must
degrade the queue to its pre-durability behaviour, not fail a solve that is
already running.

The prior design note that `_run_job` never touches the DB is relaxed here, and
deliberately: without a terminal write, every completed job stays `running` in
the table and boot reconciliation would mark it `interrupted`. The isolation the
note protected is preserved by keeping the ORM out of `solve_queue.py` and
behind this seam.
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)

# Kept in sync with `services/solve_queue._TERMINAL` by
# `tests/test_solve_jobs_table.py` rather than imported: this module is the
# ORM seam and deliberately imports nothing from the queue at module level.
_TERMINAL = ("completed", "failed", "aborted", "interrupted")


def _dt(epoch: float | None) -> datetime | None:
    """`SolveJob` timestamps are `time.time()` floats; the column is tz-aware."""
    return None if epoch is None else datetime.fromtimestamp(epoch, tz=timezone.utc)


def record_enqueued(
    job: Any, *, enqueued_by_user_id: uuid.UUID | None, solver_config_json: str | None
) -> None:
    """
    Insert the row for a freshly created job.

    TWO CLASSES OF FAILURE, HANDLED DIFFERENTLY ON PURPOSE.

    An OPERATIONAL failure — the table not migrated yet in web mode, the SQLite
    file locked, the connection dropped — is logged and swallowed. Durability is
    an upgrade to the queue, not a precondition for solving, and refusing an
    enqueue because a bookkeeping write failed would make a new feature able to
    break the old one.

    A PROGRAMMING failure is not swallowed. `except Exception` here would have
    hidden the exact defect this whole plan exists to stop: an id of the wrong
    type binds into `Uuid(as_uuid=True)`, whose bind processor calls
    `value.hex`, and the resulting `AttributeError` would be logged as an
    operational blip while every row silently went unwritten — a silent
    data-loss mode wearing a log line. So the catch is narrowed to
    `SQLAlchemyError`, and BOTH uuid columns this function writes — `job.id`
    AND `enqueued_by_user_id` — are type-checked UP FRONT, before SQLAlchemy
    can wrap the mistake in a `StatementError` that the narrowed catch would
    then swallow anyway. `enqueued_by_user_id` binds into the same
    `Uuid(as_uuid=True)` processor as `job.id`; a caller passing a `str` or an
    `int` here (a dashed-string user id off a serialized payload, say) hits
    the exact `value.hex` -> `AttributeError` -> swallowed-and-logged path
    this module exists to close, one column over from the one already guarded.
    """
    if not isinstance(job.id, uuid.UUID):
        raise TypeError(
            f"solve_jobs.id is a UUID column; got {type(job.id).__name__} "
            f"({job.id!r}). A SolveJob must carry a uuid.UUID id."
        )
    if enqueued_by_user_id is not None and not isinstance(enqueued_by_user_id, uuid.UUID):
        raise TypeError(
            "solve_jobs.enqueued_by_user_id is a UUID column; got "
            f"{type(enqueued_by_user_id).__name__} ({enqueued_by_user_id!r}). "
            "Pass a uuid.UUID or None."
        )
    try:
        from db.models import SolveJobRow
        from db.session import SessionLocal

        with SessionLocal() as db:
            db.add(SolveJobRow(
                id=job.id,
                project_id=job.project_id,
                project_key=job.project_key,
                storage_dir=job.storage_dir,
                status=job.status,
                enqueued_by_user_id=enqueued_by_user_id,
                solver_config=solver_config_json,
                enqueued_at=_dt(job.enqueued_at) or datetime.now(tz=timezone.utc),
            ))
            db.commit()
    except SQLAlchemyError:
        # Operational only. A programming error reaches the caller.
        logger.exception("solve_job_store: could not persist job %s", getattr(job, "id", None))


def record_status(job: Any) -> None:
    """
    Mirror a job's current status + result onto its row. Best-effort.

    Broad `except Exception` here, unlike `record_enqueued`, and the asymmetry
    is deliberate rather than an oversight: this runs inside a solver worker's
    `finally`, where an escaping exception would replace the job's real outcome
    with a bookkeeping traceback. `_run_job`'s own try/except around this call
    already logs it at `exception` level, so a programming error is loud without
    being fatal — which is the property `record_enqueued` gets by re-raising,
    reached the other way round.

    Called on the running claim and again from `_run_job`'s `finally`, so a
    process that dies mid-solve leaves the row at `running` — which is exactly
    what boot reconciliation reads to mark it `interrupted`.
    """
    try:
        from db.models import SolveJobRow
        from db.session import SessionLocal

        with SessionLocal() as db:
            row = db.get(SolveJobRow, job.id)
            if row is None:
                return
            if row.status in _TERMINAL and job.status not in _TERMINAL:
                # A terminal row never regresses to a live status. `abort()`
                # mirrors a job it read as `running` OUTSIDE the queue's lock,
                # so its commit can land AFTER the worker's terminal commit —
                # unguarded, that stale mirror left a finished job's row at
                # `running`, and the next boot flipped it to `interrupted`,
                # discarding the objective it actually produced. Terminal →
                # terminal stays allowed (a re-mirror of the same state is
                # idempotent); a genuine re-run is a NEW row by design (R31).
                return
            row.status = job.status
            row.objective = job.objective
            row.solve_time = job.solve_time
            row.condition = job.condition
            row.error = job.error
            row.started_at = _dt(job.started_at)
            row.finished_at = _dt(job.finished_at)
            db.commit()
    except Exception:  # noqa: BLE001 — a bookkeeping failure must not fail a solve
        logger.exception("solve_job_store: could not update job %s", getattr(job, "id", None))


def load_job(job_id: uuid.UUID) -> dict | None:
    """
    One row by id, as the same plain dict shape `load_by_status` returns, or
    None. Never raises — an unreadable table means "no such row", matching the
    rest of this module.

    Exists for the detail routes (`/log_history`, `/log_stream`, `/abort`):
    the listing merges persisted-only TERMINAL rows in (Task 16a), so an id it
    serves must also resolve when the caller clicks it — resolving through
    `solve_queue.get_job` alone answered the existence-oracle 404 for every
    `interrupted` job after a restart, indistinguishable from a bad id.
    """
    try:
        from db.models import SolveJobRow
        from db.session import SessionLocal

        with SessionLocal() as db:
            r = db.get(SolveJobRow, job_id)
            if r is None:
                return None
            return {
                "id": r.id,
                "project_id": r.project_id,
                "project_key": r.project_key,
                "storage_dir": r.storage_dir,
                "status": r.status,
                "solver_config": r.solver_config,
                "objective": r.objective,
                "solve_time": r.solve_time,
                "condition": r.condition,
                "error": r.error,
                "enqueued_at": r.enqueued_at,
                "started_at": r.started_at,
                "finished_at": r.finished_at,
            }
    except Exception:  # noqa: BLE001 — an unreadable table means "no such row"
        logger.exception("solve_job_store: could not load job %s", job_id)
        return None


def load_by_status(statuses: tuple[str, ...], *, limit: int | None = None) -> list[dict]:
    """
    Rows in any of `statuses`, as plain dicts.

    Dicts rather than ORM objects so the caller (the dispatcher, a worker
    thread — or the listing route, this module's second caller as of the
    read-side merge in `routers/solve_queue.py`) never holds a detached
    instance whose session is gone.

    Carries every column `SolveJob.to_public()` needs (Task 16a): boot
    reconciliation only ever reads `id` / `project_id` / `project_key` /
    `storage_dir` / `solver_config` / `enqueued_at` off the returned dicts
    (via `.get()`, so extra keys are inert to it), but the listing's merge
    also needs `objective` / `solve_time` / `condition` / `error` /
    `started_at` / `finished_at` to show a persisted TERMINAL job with its
    real result rather than blanks. One query, reused, not a second path.

    UNBOUNDED and OLDEST FIRST by default (`limit=None`). This is boot
    reconciliation's contract — its only caller — and it must see EVERY
    matching row (every `running` row to flip, every `queued` row to resume),
    never a capped recent slice; the ordering happens to not matter to it, but
    is left exactly as it always was so nothing about that path changes.

    `limit`, when given, switches to NEWEST first (`ORDER BY enqueued_at
    DESC`) before capping — the shape `routers/solve_queue.py::_merged_jobs`
    needs (Task 16a review round 1, Important 3): that call sits behind a
    listing polled every 1.5s while a job is active, and an unbounded query
    there would grow O(every terminal job the process has ever recorded) on
    EVERY poll for the life of the process. The caller re-sorts ascending for
    display afterwards; this only bounds what leaves the database.
    """
    try:
        from sqlalchemy import select

        from db.models import SolveJobRow
        from db.session import SessionLocal

        with SessionLocal() as db:
            stmt = select(SolveJobRow).where(SolveJobRow.status.in_(statuses))
            if limit is None:
                stmt = stmt.order_by(SolveJobRow.enqueued_at)
            else:
                stmt = stmt.order_by(SolveJobRow.enqueued_at.desc()).limit(limit)
            rows = db.scalars(stmt).all()
            return [
                {
                    "id": r.id,
                    "project_id": r.project_id,
                    "project_key": r.project_key,
                    "storage_dir": r.storage_dir,
                    "status": r.status,
                    "solver_config": r.solver_config,
                    "objective": r.objective,
                    "solve_time": r.solve_time,
                    "condition": r.condition,
                    "error": r.error,
                    "enqueued_at": r.enqueued_at,
                    "started_at": r.started_at,
                    "finished_at": r.finished_at,
                }
                for r in rows
            ]
    except Exception:  # noqa: BLE001 — an unreadable table means "nothing to restore"
        logger.exception("solve_job_store: could not load jobs by status %s", statuses)
        return []


def delete_jobs(ids: Iterable[uuid.UUID]) -> None:
    """
    Delete rows by id. Best-effort — never raises.

    The only caller is `SolveQueue.clear_finished()`. That is a super-admin,
    process-wide "wipe the terminal jobs" action — and once the listing route
    merges persisted TERMINAL rows back in (Task 16a), removing a job from
    `_jobs` alone is not enough: the very next `GET /api/simulation/queue`
    would pull the same row straight back out of `solve_jobs` and clear_finished
    would silently stop doing anything the caller could observe. So the row
    itself has to go too.

    Deliberately NOT how per-user dismissal works (`dismissed_by_user_id`,
    a later task): that is a per-user flag on a row that survives, because one
    user hiding a job must not affect another user's — or a future requeue's —
    view of it. `clear_finished` is the one place a row is actually deleted,
    matching its existing "empties the queue for every organization" contract.
    """
    id_list = list(ids)
    if not id_list:
        return
    try:
        from sqlalchemy import delete

        from db.models import SolveJobRow
        from db.session import SessionLocal

        with SessionLocal() as db:
            db.execute(delete(SolveJobRow).where(SolveJobRow.id.in_(id_list)))
            db.commit()
    except Exception:  # noqa: BLE001 — bookkeeping must not fail the clear
        logger.exception("solve_job_store: could not delete jobs %s", id_list)


def reconcile_on_boot() -> tuple[int, int]:
    """
    Bring the persisted queue back after a restart. Returns `(interrupted, resumed)`.

    Two rules, and the asymmetry between them is the point:

      * every job left `running` becomes `interrupted` and is NEVER re-enqueued.
        The process died under it; nobody stopped it. Auto-retrying is what
        would let a job that crashed the process crash-loop the boot.
      * every job left `queued` is re-enqueued under its own id and the
        dispatcher starts. That is the walk-away promise: a batch queued at
        18:00 survives a restart at 18:05.

    NEVER RAISES. Called from `lifespan`, where `ensure_schema` is local-mode
    only — in web mode Alembic is somebody else's deployment step, so the table
    may legitimately not exist yet, and a boot that dies on a missing table is a
    worse outcome than a queue that starts empty.
    """
    interrupted = 0
    resumed = 0
    try:
        from db.models import SolveJobRow
        from db.session import SessionLocal
        from services.solve_queue import solve_queue

        running = load_by_status(("running",))
        if running:
            with SessionLocal() as db:
                for row in running:
                    orm = db.get(SolveJobRow, row["id"])
                    if orm is None:
                        continue
                    orm.status = "interrupted"
                    orm.finished_at = datetime.now(tz=timezone.utc)
                    orm.condition = "process_exited"
                    interrupted += 1
                db.commit()

        for row in load_by_status(("queued",)):
            solve_queue.restore(row)
            resumed += 1
    except Exception:  # noqa: BLE001 — R26: this must never fail the boot
        logger.exception("solve-queue boot reconciliation failed; continuing without it")
        return interrupted, resumed
    logger.info(
        "solve-queue boot reconciliation: %d job(s) marked interrupted, %d resumed",
        interrupted, resumed,
    )
    return interrupted, resumed
