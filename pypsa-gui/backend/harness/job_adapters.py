"""Neutral domain job handles for nonblocking waits (issue 11).

Foreground simulation/AC-PF handoffs mint ``fg:…`` handles so ``wait_for_job``
can observe completion without holding the turn lease or using the solve
queue. Queue jobs keep their UUID ids and existing ACL path.
"""
from __future__ import annotations

import math
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import HTTPException

FOREGROUND_PREFIX = 'fg:'
FOREGROUND_FAMILY = 'foreground_simulation'
_FG_TERMINAL = frozenset({'completed', 'failed', 'aborted'})

Family = Literal['foreground_simulation', 'solve_queue']


@dataclass
class ForegroundJob:
    job_id: str
    family: str
    tool_name: str
    tool_use_id: str
    owner_user_id: str | None
    project_uuid: str | None
    kind: str | None
    created_at: float


_LOCK = threading.RLock()
_FOREGROUND: dict[str, ForegroundJob] = {}


def _refuse(kind: str, message: str, status: int = 404, **extra) -> None:
    detail = {'error_kind': kind, 'message': message}
    detail.update(extra)
    raise HTTPException(status, detail)


def is_foreground_job_id(job_id: str) -> bool:
    return isinstance(job_id, str) and job_id.startswith(FOREGROUND_PREFIX)


def register_foreground_solve(
    *,
    tool_name: str,
    tool_use_id: str,
    owner_user_id: str | None,
    project_uuid: str | None,
    kind: str | None,
) -> str:
    """Mint a stable handle for a running foreground simulation/AC-PF."""
    job_id = FOREGROUND_PREFIX + uuid.uuid4().hex
    with _LOCK:
        _FOREGROUND[job_id] = ForegroundJob(
            job_id=job_id,
            family=FOREGROUND_FAMILY,
            tool_name=tool_name,
            tool_use_id=tool_use_id,
            owner_user_id=owner_user_id,
            project_uuid=project_uuid,
            kind=kind,
            created_at=time.monotonic(),
        )
    return job_id


def _require_foreground(job_id: str, actor_id: str | None) -> ForegroundJob:
    with _LOCK:
        job = _FOREGROUND.get(job_id)
    if job is None:
        _refuse('job_not_found', 'No foreground job is registered for that id.')
    # Fail closed when an owner was recorded: same spirit as chat sessions.
    if job.owner_user_id is not None:
        if actor_id is None or str(actor_id) != str(job.owner_user_id):
            _refuse('job_not_found', 'No foreground job is registered for that id.')
    return job


def _snapshot_solver() -> dict[str, Any]:
    from services.pypsa_service import PyPSAService

    ctx = PyPSAService.get_active_context()
    with ctx.solver_state_lock:
        status = ctx.solver_state.get('status')
        kind = ctx.solver_state.get('kind')
        objective = ctx.solver_state.get('objective')
        condition = ctx.solver_state.get('condition')
    return {
        'status': status,
        'kind': kind,
        'objective': objective,
        'condition': condition,
        'running': status == 'running',
    }


def _job_view(handle: ForegroundJob, snap: dict[str, Any]) -> dict[str, Any]:
    status = snap.get('status') or 'unknown'
    return {
        'id': handle.job_id,
        'family': handle.family,
        'status': status,
        'kind': snap.get('kind') or handle.kind,
        'tool_name': handle.tool_name,
        'tool_use_id': handle.tool_use_id,
        'objective': snap.get('objective'),
        'condition': snap.get('condition'),
        'running': bool(snap.get('running')),
        'monitor': 'get_simulation_status',
    }


def wait_foreground_job(
    job_id: str,
    timeout_seconds: float,
    *,
    actor_id: str | None,
    stop: threading.Event,
    progress_slot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Bounded local wait on a registered foreground solve handle."""
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) \
            or not math.isfinite(timeout_seconds) or not 0 <= timeout_seconds <= 25:
        _refuse(
            'invalid_wait_timeout',
            'timeout_seconds must be between 0 and 25.',
            422,
        )
    handle = _require_foreground(job_id, actor_id)
    started = time.monotonic()
    deadline = started + timeout_seconds
    while True:
        snap = _snapshot_solver()
        # Project rebind under the wait must not report another project's
        # solver as this handle's outcome — require matching project when known.
        from services.pypsa_service import PyPSAService
        ctx = PyPSAService.get_active_context()
        if handle.project_uuid and ctx.project_uuid and (
            str(ctx.project_uuid) != str(handle.project_uuid)
        ):
            _refuse(
                'job_project_mismatch',
                'The active project no longer matches this foreground job.',
                409,
            )
        status = snap.get('status')
        terminal = status in _FG_TERMINAL
        cancelled = stop.is_set()
        if progress_slot is not None:
            progress_slot.clear()
            progress_slot.update({
                'job_id': job_id,
                'status': status,
                'position': None,
                'family': FOREGROUND_FAMILY,
            })
        if terminal or cancelled or time.monotonic() >= deadline:
            view = _job_view(handle, snap)
            return {
                'job': view,
                'terminal': terminal,
                'completed': status == 'completed',
                'timed_out': not terminal and not cancelled,
                'wait_cancelled': cancelled,
                'waited_seconds': round(time.monotonic() - started, 3),
                'note': (
                    'Cancelling this wait does not abort the solve; '
                    'use abort_simulation to stop it.'
                ),
            }
        stop.wait(min(0.25, max(0, deadline - time.monotonic())))


def _reset_for_tests() -> None:
    with _LOCK:
        _FOREGROUND.clear()
