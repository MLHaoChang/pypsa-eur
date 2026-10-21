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
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Literal

from fastapi import HTTPException

FOREGROUND_PREFIX = 'fg:'
FOREGROUND_FAMILY = 'foreground_simulation'
_FG_TERMINAL = frozenset({'completed', 'failed', 'aborted'})

STUDY_PREFIX = 'ad:'
STUDY_FAMILY = 'adequacy_study'
_STUDY_TERMINAL = frozenset({
    'done', 'failed', 'aborted', 'refused', 'met', 'budget_exhausted',
    # Coupling and margin loops finish here when no plan meets the target.
    # Leaving it non-terminal makes wait_for_job time out on a finished study.
    'unreachable',
})
# `done` is the sweep/MC/frontier success label; `met` is the loop success label.
_STUDY_SUCCESS = frozenset({'done', 'met'})

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
    registered_at: float = field(default_factory=time.time)


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
    registered_at: float = field(default_factory=time.time)


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
    registered_at: float = field(default_factory=time.time)


_LOCK = threading.RLock()
_FOREGROUND: dict[str, ForegroundJob] = {}
_STUDIES: dict[str, StudyJob] = {}
_REPORTS: dict[str, ReportJobHandle] = {}
# Small identity-only bindings; never retain networks, results or provider data.
_ACTIVITY_BINDINGS: OrderedDict[str, tuple] = OrderedDict()


def _bind_activity(job_id, ctx, key=None):
    if ctx is None:
        from services.pypsa_service import PyPSAService
        ctx = PyPSAService.get_active_context()
    with ctx.solver_state_lock:
        record = ctx.solver_state if key is None else ctx.solver_state.get(key)
        if isinstance(record, dict):
            _ACTIVITY_BINDINGS[job_id] = (
                id(ctx), key, record.get('thread'), record.get('started_at'),
            )
            while len(_ACTIVITY_BINDINGS) > 512:
                _ACTIVITY_BINDINGS.popitem(last=False)
    return ctx


def _remember_activity(handle, ctx, snap=None):
    from harness import activity_history

    if ctx is None or str(ctx.project_uuid) != str(handle.project_uuid):
        return
    if snap is None:
        snap = _snapshot_bound_job(handle.job_id, ctx)
    activity_history.remember(str(handle.owner_user_id), str(handle.project_uuid), {
        'source_id': handle.job_id, 'family': handle.family,
        'tool_name': handle.tool_name, 'native_status': snap['status'],
        'registered_at': handle.registered_at,
    })


def _snapshot_bound_job(job_id, ctx):
    """Read one exact native origin under its short lifecycle lock.

    Check and read are atomic against replacement. Never acquire the network
    mutation lock, and never use a latest-project result for an old handle.
    """
    with _LOCK:
        binding = _ACTIVITY_BINDINGS.get(job_id)
    if ctx is None or binding is None or binding[0] != id(ctx):
        return {'status': 'unknown'}
    _, key, worker, started = binding
    with ctx.solver_state_lock:
        record = ctx.solver_state if key is None else ctx.solver_state.get(key)
        if not _same_origin(record, worker, started):
            return {'status': 'unknown'}
        snap = {k: record.get(k) for k in (
            'status', 'kind', 'objective', 'condition', 'stage', 'error',
            'report_id', 'mode', 'section', 'version',
        )}
        snap['section'] = snap['section'] or record.get('current_section')
        snap['status'] = _native_status(record, worker)
        snap['running'] = snap['status'] == 'running'
        snap['progress'] = None
        for field in ('iterations', 'points', 'rows'):
            value = record.get(field)
            if isinstance(value, list):
                snap['progress'] = {field: len(value)}
                break
        return snap


def _same_origin(record, worker, started):
    return isinstance(record, dict) and (
        (worker is not None and record.get('thread') is worker)
        or (worker is None and started is not None and record.get('started_at') == started))


def _native_status(record, worker):
    status = record.get('status')
    if (status == 'running' and isinstance(worker, threading.Thread)
            and worker.ident is not None and not worker.is_alive()):
        return 'unknown'  # An exited thread does not prove success/failure/no effect.
    return status or 'unknown'


def _completion_status(job_id, ctx, project):
    """Nonblocking, identity-bound status only; None means a busy lifecycle lock."""
    if not _LOCK.acquire(blocking=False):
        return None
    try:
        binding = _ACTIVITY_BINDINGS.get(job_id)
    finally:
        _LOCK.release()
    if binding is None or binding[0] != id(ctx):
        return 'unknown'
    _, key, worker, started = binding
    if not ctx.solver_state_lock.acquire(blocking=False):
        return None
    try:
        if str(ctx.project_uuid) != project:
            return 'unknown'
        record = ctx.solver_state if key is None else ctx.solver_state.get(key)
        return _native_status(record, worker) if _same_origin(record, worker, started) else 'unknown'
    finally:
        ctx.solver_state_lock.release()


def _watch_activity(handle, ctx):
    from harness import completion_observer
    with _LOCK:
        binding = _ACTIVITY_BINDINGS.get(handle.job_id)
    if binding is not None:
        try:
            completion_observer.watch(handle, ctx, binding[2])
        except Exception:  # noqa: BLE001 -- observation cannot change job handoff.
            completion_observer.note_incomplete(handle.owner_user_id, handle.project_uuid)


def activity_jobs(ctx, *, owner_id: str, project_id: str) -> list[dict]:
    """Exact owned registered jobs, bounded to 512 recent identity bindings."""
    with _LOCK:
        handles = [(_FOREGROUND.get(j) or _STUDIES.get(j) or _REPORTS.get(j))
                   for j in _ACTIVITY_BINDINGS]
    rows = []
    for handle in handles:
        if handle is None or str(handle.owner_user_id) != owner_id or str(handle.project_uuid) != project_id:
            continue
        snap = _snapshot_bound_job(handle.job_id, ctx)
        rows.append({
            'source_id': handle.job_id, 'family': handle.family,
            'title': handle.tool_name.replace('_', ' '),
            'tool_name': handle.tool_name,
            'registered_at': handle.registered_at,
            'native_status': snap.get('status') or 'unknown',
            'stage': str(snap['stage'])[:120] if snap.get('stage') else None,
            'tool_use_id': handle.tool_use_id,
        })
    return rows


def _refuse(kind: str, message: str, status: int = 404, **extra) -> None:
    detail = {'error_kind': kind, 'message': message}
    detail.update(extra)
    raise HTTPException(status, detail)


def is_registered(job_id: str) -> bool:
    """Whether this process minted the handle; a restart forgets every one."""
    with _LOCK:
        return job_id in _FOREGROUND or job_id in _STUDIES or job_id in _REPORTS


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
    ctx=None,
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
        origin = _bind_activity(job_id, ctx)
        handle = _FOREGROUND[job_id]
    _remember_activity(handle, origin)
    _watch_activity(handle, origin)
    return job_id


def register_study_job(
    *,
    study_key: str,
    tool_name: str,
    tool_use_id: str,
    owner_user_id: str | None,
    project_uuid: str | None,
    ctx=None,
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
        origin = _bind_activity(job_id, ctx, study_key)
        handle = _STUDIES[job_id]
    _remember_activity(handle, origin)
    _watch_activity(handle, origin)
    return job_id


def register_report_job(
    *,
    tool_name: str,
    tool_use_id: str,
    report_id: str | None,
    owner_user_id: str | None,
    project_uuid: str | None,
    ctx=None,
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
        origin = _bind_activity(job_id, ctx, REPORT_STATE_KEY)
        handle = _REPORTS[job_id]
    _remember_activity(handle, origin)
    _watch_activity(handle, origin)
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
        snap = _snapshot_bound_job(job_id, ctx)
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
            if terminal:
                _remember_activity(handle, ctx, snap)
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
        snap = _snapshot_bound_job(job_id, ctx)
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
            if terminal:
                _remember_activity(handle, ctx, snap)
            view = _study_job_view(handle, snap)
            abort_hint = (
                'abort the investment case HTTP route'
                if handle.study_key == 'investment_case'
                else 'use abort_adequacy_study to stop it'
            )
            return {
                'job': view,
                'terminal': terminal,
                'completed': status in _STUDY_SUCCESS,
                'timed_out': not terminal and not cancelled,
                'wait_cancelled': cancelled,
                'waited_seconds': round(time.monotonic() - started, 3),
                'note': (
                    'Cancelling this wait does not abort the study; '
                    f'{abort_hint}.'
                ),
            }
        stop.wait(min(0.25, max(0, deadline - time.monotonic())))




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
        snap = _snapshot_bound_job(job_id, ctx)
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
            if terminal:
                _remember_activity(handle, ctx, snap)
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
    from harness import completion_observer
    completion_observer._reset_for_tests()
    with _LOCK:
        _FOREGROUND.clear()
        _STUDIES.clear()
        _REPORTS.clear()
        _ACTIVITY_BINDINGS.clear()
