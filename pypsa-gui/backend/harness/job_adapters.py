"""Neutral domain job handles for nonblocking waits (issue 11).

Foreground simulation/AC-PF handoffs mint ``fg:…`` handles so ``wait_for_job``
can observe completion without holding the turn lease or using the solve
queue. Adequacy/FMEA/MC/frontier/coupling/EH/investment studies mint
``ad:…`` handles over the in-process study mesh. Queue jobs (including
GridSpine) keep their UUID ids and existing ACL path.
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

STUDY_PREFIX = 'ad:'
STUDY_FAMILY = 'adequacy_study'
_STUDY_TERMINAL = frozenset({
    'done', 'failed', 'aborted', 'refused', 'met', 'budget_exhausted',
})

REPORT_PREFIX = 'rpt:'
REPORT_FAMILY = 'report_job'
REPORT_STATE_KEY = 'report_job'
_REPORT_TERMINAL = frozenset({'done', 'failed', 'aborted'})
REPORT_TOOL_NAMES = frozenset({
    'generate_report',
    'regenerate_report_section',
    'propose_report_mapping',
})

# tool_name → solver_state study key (STUDY_KEYS / ABORTABLE_STUDIES).
STUDY_TOOL_KEYS: dict[str, str] = {
    'run_fmea_sweep': 'fmea_sweep',
    'run_frontier_study': 'frontier',
    'run_mc_study': 'mc',
    'run_coupling_loop': 'coupling_loop',
    'run_margin_loop': 'margin_loop',
    'run_eh_study': 'eh_study',
    'run_investment_case': 'investment_case',
}

Family = Literal[
    'foreground_simulation', 'adequacy_study', 'report_job', 'solve_queue',
]


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


@dataclass
class StudyJob:
    job_id: str
    family: str
    study_key: str
    tool_name: str
    tool_use_id: str
    owner_user_id: str | None
    project_uuid: str | None
    created_at: float


@dataclass
class ReportJobHandle:
    job_id: str
    family: str
    tool_name: str
    tool_use_id: str
    report_id: str | None
    owner_user_id: str | None
    project_uuid: str | None
    created_at: float


_LOCK = threading.RLock()
_FOREGROUND: dict[str, ForegroundJob] = {}
_STUDIES: dict[str, StudyJob] = {}
_REPORTS: dict[str, ReportJobHandle] = {}


def _refuse(kind: str, message: str, status: int = 404, **extra) -> None:
    detail = {'error_kind': kind, 'message': message}
    detail.update(extra)
    raise HTTPException(status, detail)


def is_foreground_job_id(job_id: str) -> bool:
    return isinstance(job_id, str) and job_id.startswith(FOREGROUND_PREFIX)


def is_study_job_id(job_id: str) -> bool:
    return isinstance(job_id, str) and job_id.startswith(STUDY_PREFIX)


def is_report_job_id(job_id: str) -> bool:
    return isinstance(job_id, str) and job_id.startswith(REPORT_PREFIX)


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


def register_study_job(
    *,
    study_key: str,
    tool_name: str,
    tool_use_id: str,
    owner_user_id: str | None,
    project_uuid: str | None,
) -> str:
    """Mint a stable handle for a running adequacy/study mesh worker."""
    if study_key not in set(STUDY_TOOL_KEYS.values()):
        _refuse(
            'invalid_study_key',
            f'Unknown study key for job adapter: {study_key!r}.',
            422,
        )
    job_id = STUDY_PREFIX + uuid.uuid4().hex
    with _LOCK:
        _STUDIES[job_id] = StudyJob(
            job_id=job_id,
            family=STUDY_FAMILY,
            study_key=study_key,
            tool_name=tool_name,
            tool_use_id=tool_use_id,
            owner_user_id=owner_user_id,
            project_uuid=project_uuid,
            created_at=time.monotonic(),
        )
    return job_id


def register_report_job(
    *,
    tool_name: str,
    tool_use_id: str,
    report_id: str | None,
    owner_user_id: str | None,
    project_uuid: str | None,
) -> str:
    """Mint a stable handle for a running report generation/mapping job."""
    job_id = REPORT_PREFIX + uuid.uuid4().hex
    with _LOCK:
        _REPORTS[job_id] = ReportJobHandle(
            job_id=job_id,
            family=REPORT_FAMILY,
            tool_name=tool_name,
            tool_use_id=tool_use_id,
            report_id=report_id,
            owner_user_id=owner_user_id,
            project_uuid=project_uuid,
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


def _require_study(job_id: str, actor_id: str | None) -> StudyJob:
    with _LOCK:
        job = _STUDIES.get(job_id)
    if job is None:
        _refuse('job_not_found', 'No study job is registered for that id.')
    if job.owner_user_id is not None:
        if actor_id is None or str(actor_id) != str(job.owner_user_id):
            _refuse('job_not_found', 'No study job is registered for that id.')
    return job


def _require_report(job_id: str, actor_id: str | None) -> ReportJobHandle:
    with _LOCK:
        job = _REPORTS.get(job_id)
    if job is None:
        _refuse('job_not_found', 'No report job is registered for that id.')
    if job.owner_user_id is not None:
        if actor_id is None or str(actor_id) != str(job.owner_user_id):
            _refuse('job_not_found', 'No report job is registered for that id.')
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


def _snapshot_study(study_key: str) -> dict[str, Any]:
    from services.pypsa_service import PyPSAService

    ctx = PyPSAService.get_active_context()
    with ctx.solver_state_lock:
        record = ctx.solver_state.get(study_key)
        if not isinstance(record, dict):
            return {'status': 'unknown', 'stage': None, 'progress': None}
        status = record.get('status')
        stage = record.get('stage')
        # Cheap progress cursor when the runner exposes iterative collections.
        progress = None
        for field in ('iterations', 'points', 'rows'):
            value = record.get(field)
            if isinstance(value, list):
                progress = {field: len(value)}
                break
        return {
            'status': status,
            'stage': stage,
            'progress': progress,
            'error': record.get('error'),
        }


def _study_job_view(handle: StudyJob, snap: dict[str, Any]) -> dict[str, Any]:
    status = snap.get('status') or 'unknown'
    monitor = (
        'get_investment_case'
        if handle.study_key == 'investment_case'
        else f"get_adequacy_results('{handle.study_key}')"
    )
    return {
        'id': handle.job_id,
        'family': handle.family,
        'study_key': handle.study_key,
        'status': status,
        'stage': snap.get('stage'),
        'progress': snap.get('progress'),
        'tool_name': handle.tool_name,
        'tool_use_id': handle.tool_use_id,
        'error': snap.get('error'),
        'running': status == 'running',
        'monitor': monitor,
    }


def wait_study_job(
    job_id: str,
    timeout_seconds: float,
    *,
    actor_id: str | None,
    stop: threading.Event,
    progress_slot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Bounded local wait on a registered adequacy/study handle."""
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) \
            or not math.isfinite(timeout_seconds) or not 0 <= timeout_seconds <= 25:
        _refuse(
            'invalid_wait_timeout',
            'timeout_seconds must be between 0 and 25.',
            422,
        )
    handle = _require_study(job_id, actor_id)
    started = time.monotonic()
    deadline = started + timeout_seconds
    while True:
        from services.pypsa_service import PyPSAService
        ctx = PyPSAService.get_active_context()
        if handle.project_uuid and ctx.project_uuid and (
            str(ctx.project_uuid) != str(handle.project_uuid)
        ):
            _refuse(
                'job_project_mismatch',
                'The active project no longer matches this study job.',
                409,
            )
        snap = _snapshot_study(handle.study_key)
        status = snap.get('status')
        terminal = status in _STUDY_TERMINAL
        cancelled = stop.is_set()
        if progress_slot is not None:
            progress_slot.clear()
            progress_slot.update({
                'job_id': job_id,
                'status': status,
                'position': None,
                'family': STUDY_FAMILY,
                'study_key': handle.study_key,
                'stage': snap.get('stage'),
                'progress': snap.get('progress'),
            })
        if terminal or cancelled or time.monotonic() >= deadline:
            view = _study_job_view(handle, snap)
            abort_hint = (
                'abort the investment case HTTP route'
                if handle.study_key == 'investment_case'
                else 'use abort_adequacy_study to stop it'
            )
            return {
                'job': view,
                'terminal': terminal,
                'completed': status == 'done',
                'timed_out': not terminal and not cancelled,
                'wait_cancelled': cancelled,
                'waited_seconds': round(time.monotonic() - started, 3),
                'note': (
                    'Cancelling this wait does not abort the study; '
                    f'{abort_hint}.'
                ),
            }
        stop.wait(min(0.25, max(0, deadline - time.monotonic())))


def _snapshot_report() -> dict[str, Any]:
    from services.pypsa_service import PyPSAService
    from services.reports.report_job import public_record

    ctx = PyPSAService.get_active_context()
    with ctx.solver_state_lock:
        record = public_record(ctx.solver_state.get(REPORT_STATE_KEY))
    if not isinstance(record, dict):
        return {
            'status': 'unknown',
            'report_id': None,
            'mode': None,
            'section': None,
        }
    return {
        'status': record.get('status'),
        'report_id': record.get('report_id'),
        'mode': record.get('mode'),
        'section': record.get('section') or record.get('current_section'),
        'error': record.get('error'),
        'version': record.get('version'),
    }


def _report_job_view(handle: ReportJobHandle, snap: dict[str, Any]) -> dict[str, Any]:
    status = snap.get('status') or 'unknown'
    return {
        'id': handle.job_id,
        'family': handle.family,
        'status': status,
        'report_id': snap.get('report_id') or handle.report_id,
        'mode': snap.get('mode'),
        'section': snap.get('section'),
        'version': snap.get('version'),
        'tool_name': handle.tool_name,
        'tool_use_id': handle.tool_use_id,
        'error': snap.get('error'),
        'running': status == 'running',
        'monitor': 'get_report_status',
    }


def wait_report_job(
    job_id: str,
    timeout_seconds: float,
    *,
    actor_id: str | None,
    stop: threading.Event,
    progress_slot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Bounded local wait on a registered report generation handle."""
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) \
            or not math.isfinite(timeout_seconds) or not 0 <= timeout_seconds <= 25:
        _refuse(
            'invalid_wait_timeout',
            'timeout_seconds must be between 0 and 25.',
            422,
        )
    handle = _require_report(job_id, actor_id)
    started = time.monotonic()
    deadline = started + timeout_seconds
    while True:
        from services.pypsa_service import PyPSAService
        ctx = PyPSAService.get_active_context()
        if handle.project_uuid and ctx.project_uuid and (
            str(ctx.project_uuid) != str(handle.project_uuid)
        ):
            _refuse(
                'job_project_mismatch',
                'The active project no longer matches this report job.',
                409,
            )
        snap = _snapshot_report()
        status = snap.get('status')
        terminal = status in _REPORT_TERMINAL
        cancelled = stop.is_set()
        if progress_slot is not None:
            progress_slot.clear()
            progress_slot.update({
                'job_id': job_id,
                'status': status,
                'position': None,
                'family': REPORT_FAMILY,
                'report_id': snap.get('report_id') or handle.report_id,
                'section': snap.get('section'),
            })
        if terminal or cancelled or time.monotonic() >= deadline:
            view = _report_job_view(handle, snap)
            return {
                'job': view,
                'terminal': terminal,
                'completed': status == 'done',
                'timed_out': not terminal and not cancelled,
                'wait_cancelled': cancelled,
                'waited_seconds': round(time.monotonic() - started, 3),
                'note': (
                    'Cancelling this wait does not abort the report job; '
                    'use abort_report_generation to stop it.'
                ),
            }
        stop.wait(min(0.25, max(0, deadline - time.monotonic())))


def _reset_for_tests() -> None:
    with _LOCK:
        _FOREGROUND.clear()
        _STUDIES.clear()
        _REPORTS.clear()
