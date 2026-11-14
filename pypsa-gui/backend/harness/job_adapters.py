"""Neutral domain job handles for nonblocking waits (issue 11).

Foreground simulation/AC-PF handoffs mint ``fg:…`` handles so ``wait_for_job``
can observe completion without holding the turn lease or using the solve
queue. Adequacy/FMEA/MC/frontier/coupling/EH/investment studies mint
``ad:…`` handles over the in-process study mesh. Queue jobs (including
GridSpine) keep their UUID ids and existing ACL path.

`job_status` and `cancel_job` (live-voice 11a) read every family, the queue
included, through one neutral view with a verified cancel.
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

from services.adequacy.coupling import LOOP_STATUSES

FOREGROUND_PREFIX = 'fg:'
FOREGROUND_FAMILY = 'foreground_simulation'
_FG_TERMINAL = frozenset({'completed', 'failed', 'aborted'})

STUDY_PREFIX = 'ad:'
STUDY_FAMILY = 'adequacy_study'
# The coupling and margin loops finish on one of `LOOP_STATUSES` (unreachable
# when no plan meets the target); leaving one non-terminal makes wait_for_job
# time out on a finished study.
_STUDY_TERMINAL = frozenset({'done', 'failed', 'aborted', 'refused'}) | LOOP_STATUSES
# `done` is the sweep/MC/frontier success label; `met` is the loop success label.
# `not_confident` (adequacy loops 04) is terminal through LOOP_STATUSES and never a success.
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
    'run_business_case': 'business_case',
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
    # live-voice 11a: the project's `network_revision` when the handle was minted.
    input_revision: int | None = None


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
    # live-voice 11a: the project's `network_revision` when the handle was minted.
    input_revision: int | None = None


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
    # live-voice 11a: the project's `network_revision` when the handle was minted.
    input_revision: int | None = None


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
            'started_at', 'finished_at', 'warnings',
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
        handle.input_revision = getattr(origin, 'network_revision', None)
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
        handle.input_revision = getattr(origin, 'network_revision', None)
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
        handle.input_revision = getattr(origin, 'network_revision', None)
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
        else 'get_business_case'
        if handle.study_key == 'business_case'
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
                else 'use abort_business_case to stop it'
                if handle.study_key == 'business_case'
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


# ── Neutral job contract (live-voice 11a) ───────────────────────────────────
#
# One view for every asynchronous family, so a monitor, a snapshot on
# reconnect and a cancel read the same shape whatever runs: identity, status,
# phase/progress, a per-job cursor, timestamps, input/result revision,
# warnings, the terminal outcome, and a verified cancel. The family views
# above stay as `detail` for the tools that already return them.

CONTRACT_VERSION = 1
QUEUE_FAMILY = 'solve_queue'
# How long a cancel waits for the worker to confirm it stopped.
CANCEL_SETTLE_SECONDS = 3.0

# Native terminal status -> neutral outcome. A loop that finished without
# meeting its target (`LOOP_STATUSES` other than `met`) is `not_met`.
_OUTCOMES: dict[str, dict[str, str]] = {
    FOREGROUND_FAMILY: {'completed': 'succeeded', 'failed': 'failed', 'aborted': 'cancelled'},
    # The loop statuses first, so the shared names (`aborted`, `met`) keep their own outcome.
    STUDY_FAMILY: {**{s: 'not_met' for s in LOOP_STATUSES},
                   'done': 'succeeded', 'met': 'succeeded', 'failed': 'failed',
                   'aborted': 'cancelled', 'refused': 'refused'},
    REPORT_FAMILY: {'done': 'succeeded', 'failed': 'failed', 'aborted': 'cancelled'},
    QUEUE_FAMILY: {'completed': 'succeeded', 'failed': 'failed', 'aborted': 'cancelled',
                   'interrupted': 'interrupted'},
}
OUTCOMES = frozenset(o for table in _OUTCOMES.values() for o in table.values()) | {'unknown'}

# job_id -> [cursor, (status, phase, progress) last seen, updated_at, finished_at]
_TRACK: OrderedDict[str, list] = OrderedDict()


def _family_of(job_id: str) -> str:
    if is_foreground_job_id(job_id):
        return FOREGROUND_FAMILY
    if is_study_job_id(job_id):
        return STUDY_FAMILY
    if is_report_job_id(job_id):
        return REPORT_FAMILY
    return QUEUE_FAMILY


def _terminal(family: str, status: str | None) -> bool:
    if family == FOREGROUND_FAMILY:
        return status in _FG_TERMINAL
    if family == STUDY_FAMILY:
        return status in _STUDY_TERMINAL
    if family == REPORT_FAMILY:
        return status in _REPORT_TERMINAL
    from services.solve_queue import _TERMINAL
    return status in _TERMINAL


def _outcome(family: str, status: str | None) -> str | None:
    """The neutral terminal outcome; None while the job still runs."""
    if status == 'unknown':
        return 'unknown'  # an exited worker or a lost origin proves nothing
    if not _terminal(family, status):
        return None
    return _OUTCOMES[family].get(status, 'unknown')


def _track(job_id: str, status, phase, progress, terminal: bool) -> tuple[int, float, float | None]:
    """Advance the job's cursor when what a monitor sees changed."""
    key = (status, phase, repr(progress))
    now = time.time()
    with _LOCK:
        row = _TRACK.get(job_id)
        if row is None:
            row = _TRACK[job_id] = [0, None, now, None]
        if row[1] != key:
            row[0] += 1
            row[1] = key
            row[2] = now
            if terminal and row[3] is None:
                row[3] = now
        _TRACK.move_to_end(job_id)
        while len(_TRACK) > 512:
            _TRACK.popitem(last=False)
        return row[0], row[2], row[3]


def _active_ctx_for(handle):
    from services.pypsa_service import PyPSAService
    ctx = PyPSAService.get_active_context()
    if handle.project_uuid and ctx.project_uuid and str(ctx.project_uuid) != str(handle.project_uuid):
        _refuse('job_project_mismatch', 'The active project no longer matches this job.', 409)
    return ctx


def _warnings(handle, snap) -> list[str]:
    from harness import completion_observer
    out = [str(w)[:200] for w in (snap.get('warnings') or []) if w][:10]
    if handle.owner_user_id is not None and handle.project_uuid is not None:
        out += completion_observer.warnings(str(handle.owner_user_id), str(handle.project_uuid))
    return out


def _result_revision(handle, snap, ctx) -> str | None:
    """What identifies the results a succeeded job wrote, or None."""
    if handle.family == FOREGROUND_FAMILY:
        network = getattr(ctx, 'network', None)
        meta = getattr(network, 'meta', None) or {}
        fingerprint = meta.get('assistant_solve_input_fingerprint')
        return f'solve:{fingerprint}' if fingerprint else None
    if handle.family == STUDY_FAMILY:
        return f"{handle.study_key}:{snap.get('started_at')}"
    report_id = snap.get('report_id') or handle.report_id
    return f"report:{report_id}@{snap.get('version')}" if report_id else None


def _native_view(job_id: str, actor_id: str | None) -> dict[str, Any]:
    family = _family_of(job_id)
    if family == FOREGROUND_FAMILY:
        handle, view_of = _require_foreground(job_id, actor_id), _job_view
    elif family == STUDY_FAMILY:
        handle, view_of = _require_study(job_id, actor_id), _study_job_view
    else:
        handle, view_of = _require_report(job_id, actor_id), _report_job_view
    ctx = _active_ctx_for(handle)
    snap = _snapshot_bound_job(job_id, ctx)
    status = snap.get('status') or 'unknown'
    outcome = _outcome(family, status)
    phase = snap.get('stage') or snap.get('section') or snap.get('kind') or getattr(handle, 'kind', None)
    progress = snap.get('progress')
    cursor, updated, finished = _track(job_id, status, phase, progress, outcome is not None)
    return {
        'contract': CONTRACT_VERSION,
        'id': job_id,
        'family': family,
        'kind': handle.tool_name,
        'tool_use_id': handle.tool_use_id,
        'owner_user_id': None if handle.owner_user_id is None else str(handle.owner_user_id),
        'project_id': None if handle.project_uuid is None else str(handle.project_uuid),
        'status': status,
        'phase': None if phase is None else str(phase)[:120],
        'progress': progress,
        'cursor': cursor,
        'created_at': handle.registered_at,
        'started_at': snap.get('started_at'),
        'updated_at': updated,
        'finished_at': snap.get('finished_at') or finished,
        'input_revision': handle.input_revision,
        'result_revision': _result_revision(handle, snap, ctx) if outcome == 'succeeded' else None,
        'warnings': _warnings(handle, snap),
        'terminal': outcome is not None,
        'outcome': outcome,
        'detail': view_of(handle, snap),
    }


def _queue_job(job_id: str) -> dict[str, Any]:
    """The queue's own ACL: 404 unless the acting user may see the job."""
    from routers.solve_queue import _visible_job_or_404
    from services import chat_tools
    with chat_tools._acting() as (db, user):
        return _visible_job_or_404(db, user, job_id)


def _queue_view(job_id: str) -> dict[str, Any]:
    job = _queue_job(job_id)
    status = job.get('status') or 'unknown'
    outcome = _outcome(QUEUE_FAMILY, status)
    progress = {'position': job['position']} if job.get('position') is not None else None
    cursor, updated, _ = _track(job_id, status, job.get('kind'), progress, outcome is not None)
    return {
        'contract': CONTRACT_VERSION,
        'id': str(job['id']),
        'family': QUEUE_FAMILY,
        'kind': job.get('kind'),
        'tool_use_id': None,
        'owner_user_id': None,  # the queue's ACL is the project's, not one owner's
        'project_id': job.get('project_id'),
        'status': status,
        'phase': job.get('kind'),
        'progress': progress,
        'cursor': cursor,
        'created_at': job.get('enqueued_at'),
        'started_at': job.get('started_at'),
        'updated_at': updated,
        'finished_at': job.get('finished_at'),
        'input_revision': None,  # a queued job solves the saved file, not an open-network revision
        'result_revision': (f"queue:{job['id']}@{job.get('finished_at')}"
                            if outcome == 'succeeded' else None),
        'warnings': [],  # the queue records an error, read as `outcome: failed`
        'terminal': outcome is not None,
        'outcome': outcome,
        'detail': job,
    }


def job_status(job_id: str, *, actor_id: str | None) -> dict[str, Any]:
    """The neutral view of any job this actor may see; 404 `job_not_found` otherwise."""
    if not isinstance(job_id, str) or not job_id:
        _refuse('job_not_found', 'No job is registered for that id.')
    if _family_of(job_id) == QUEUE_FAMILY:
        return _queue_view(job_id)
    return _native_view(job_id, actor_id)


def _origin_live(job_id: str, ctx) -> bool:
    """Whether the handle's own worker still owns its record (not a newer run)."""
    with _LOCK:
        binding = _ACTIVITY_BINDINGS.get(job_id)
    if ctx is None or binding is None or binding[0] != id(ctx):
        return False
    _, key, worker, started = binding
    with ctx.solver_state_lock:
        record = ctx.solver_state if key is None else ctx.solver_state.get(key)
        return _same_origin(record, worker, started)


def _abort_native(job_id: str) -> None:
    """The existing abort for the handle's family; never a new stop path."""
    from services import chat_tools
    family = _family_of(job_id)
    if family == FOREGROUND_FAMILY:
        chat_tools.abort_simulation()
    elif family == REPORT_FAMILY:
        chat_tools.abort_report_generation()
    else:
        key = _STUDIES[job_id].study_key
        if key == 'investment_case':
            chat_tools.abort_investment_case()
        elif key == 'business_case':
            chat_tools.abort_business_case()
        else:
            chat_tools.abort_adequacy_study(key)


def cancel_job(
    job_id: str,
    *,
    actor_id: str | None,
    settle_seconds: float = CANCEL_SETTLE_SECONDS,
    stop: threading.Event | None = None,
) -> dict[str, Any]:
    """
    Cancel one job by handle and report the VERIFIED outcome.

    `cancel` is one of:
      - `cancelled`: the worker confirmed it stopped (outcome `cancelled`);
      - `cancel_requested`: the stop was sent and the worker had not yet
        confirmed within `settle_seconds` (it stops at its next boundary);
      - `already_finished`: the job was terminal before the request, nothing sent;
      - `finished_first`: the job ended another way while the stop was in flight;
      - `origin_lost`: the handle's worker no longer owns a live record (a
        newer run replaced it, or it exited without a status), so nothing was
        sent: whatever runs now is not this job.
    The ACL and project checks are `job_status`'s: a foreign handle is 404.
    """
    stop = stop or threading.Event()
    before = job_status(job_id, actor_id=actor_id)
    if before['outcome'] == 'unknown':
        return {'cancel': 'origin_lost', 'job': before}
    if before['terminal']:
        return {'cancel': 'already_finished', 'job': before}
    if before['family'] == QUEUE_FAMILY:
        from services import chat_tools
        chat_tools.solve_queue_abort(job_id)
    else:
        from services.pypsa_service import PyPSAService
        ctx = PyPSAService.get_active_context()
        if not _origin_live(job_id, ctx):
            return {'cancel': 'origin_lost', 'job': before}
        try:
            _abort_native(job_id)
        except HTTPException as exc:
            # The abort found nothing running: re-read below decides what happened.
            if exc.status_code not in (400, 404, 409):
                raise
    deadline = time.monotonic() + max(0.0, settle_seconds)
    while True:
        after = job_status(job_id, actor_id=actor_id)
        if after['terminal'] or stop.is_set() or time.monotonic() >= deadline:
            break
        stop.wait(min(0.05, max(0.0, deadline - time.monotonic())))
    if after['outcome'] == 'cancelled':
        verdict = 'cancelled'
    elif after['terminal']:
        verdict = 'finished_first'
    else:
        verdict = 'cancel_requested'
    return {'cancel': verdict, 'job': after}


def _reset_for_tests() -> None:
    from harness import completion_observer
    completion_observer._reset_for_tests()
    with _LOCK:
        _FOREGROUND.clear()
        _STUDIES.clear()
        _REPORTS.clear()
        _ACTIVITY_BINDINGS.clear()
        _TRACK.clear()
