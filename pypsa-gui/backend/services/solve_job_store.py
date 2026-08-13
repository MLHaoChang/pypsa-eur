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
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)


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


def load_by_status(statuses: tuple[str, ...]) -> list[dict]:
    """
    Rows in any of `statuses`, oldest first, as plain dicts.

    Dicts rather than ORM objects so the caller (the dispatcher, a worker
    thread) never holds a detached instance whose session is gone.
    """
    try:
        from sqlalchemy import select

        from db.models import SolveJobRow
        from db.session import SessionLocal

        with SessionLocal() as db:
            rows = db.scalars(
                select(SolveJobRow)
                .where(SolveJobRow.status.in_(statuses))
                .order_by(SolveJobRow.enqueued_at)
            ).all()
            return [
                {
                    "id": r.id,
                    "project_id": r.project_id,
                    "project_key": r.project_key,
                    "storage_dir": r.storage_dir,
                    "status": r.status,
                    "solver_config": r.solver_config,
                    "enqueued_at": r.enqueued_at,
                }
                for r in rows
            ]
    except Exception:  # noqa: BLE001 — an unreadable table means "nothing to restore"
        logger.exception("solve_job_store: could not load jobs by status %s", statuses)
        return []


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
