"""Bounded factual Activity projection; no work admission or numerical store.

Snapshot cursors are actor/project/filter scoped and process-local. Reconnect or
restart replaces the view from authority; it never reconstructs work or approvals.
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path

from fastapi import HTTPException

from harness import job_adapters
from services.pypsa_service import PyPSAService

_LOCK = threading.Lock()
_SCOPES = OrderedDict()
_MAX_ROWS = 500
_MAX_FILES = 500
_TERMINAL = {'completed', 'failed', 'cancelled', 'partial', 'decision_recorded'}


def _status(native):
    if not isinstance(native, str):
        return 'unavailable'
    return {
        'done': 'completed', 'met': 'completed', 'completed': 'completed',
        'failed': 'failed', 'error': 'failed', 'refused': 'failed',
        'unreachable': 'failed', 'budget_exhausted': 'partial',
        'aborted': 'cancelled', 'cancelled': 'cancelled',
        'interrupted': 'needs_review', 'uncertain': 'needs_review',
        'needs_review': 'needs_review', 'running': 'running', 'queued': 'queued',
        'ready': 'paused', 'paused': 'paused', 'partial': 'partial',
        'waiting_for_approval': 'waiting_for_approval',
        'waiting_for_input': 'waiting_for_input', 'decision_recorded': 'decision_recorded',
    }.get(native, 'unavailable')


def _row(project, family, source_id, title, native, *, review_required=False, **extra):
    native = native[:80] if isinstance(native, str) else 'unknown'
    state = 'needs_review' if review_required else _status(native)
    return dict(
        id=f'{family}:{source_id}', source_id=source_id, family=family,
        project_id=project, title=str(title)[:120], native_status=native,
        status=state, evidence_available=False, evidence_status='not_checked',
        needs_you=state in {'needs_review', 'unavailable', 'waiting_for_approval', 'waiting_for_input'},
        # Native completion alone never certifies results or grants an action.
        **extra,
    )


def _tasks(root: Path, project: str, owner: str):
    from services.assistant_tasks import _LOCK as task_lock

    rows, warnings = [], []
    directory = root / 'assistant_tasks'
    if not directory.exists():
        return rows, warnings
    # Never follow task checkpoint links out of the authorized project store.
    if directory.is_symlink():
        return [], ['task_store_unavailable']
    with task_lock:
        for index, path in enumerate(directory.glob('*.json')):
            if index >= _MAX_FILES:
                warnings.append('task_scan_truncated')
                break
            try:
                if path.is_symlink() or path.stat().st_size > 2000000:
                    raise ValueError('unreadable checkpoint')
                task = json.loads(path.read_text())
                if not isinstance(task, dict):
                    raise ValueError('invalid checkpoint')
                if task.get('owner_id') != owner or task.get('project_id') != project:
                    continue
                source_id = str(uuid.UUID(task['task_id']))
                if path.stem != source_id or not isinstance(task.get('steps'), list):
                    raise ValueError('invalid checkpoint')
                state = task['status']
                valid_steps = {'pending', 'running', 'uncertain', 'failed', 'completed'}
                if (not isinstance(task.get('title'), str)
                        or not 1 <= len(task['steps']) <= 12
                        or any(not isinstance(step, dict) or step.get('status') not in valid_steps
                               for step in task['steps'])
                        or (state == 'completed' and any(step['status'] != 'completed' for step in task['steps']))):
                    raise ValueError('invalid task state')
                rows.append(_row(project, 'assistant_task', source_id, task['title'], state,
                                 reason='Inspect the original task before resuming.' if state == 'needs_review' else None))
            except (OSError, ValueError, KeyError, TypeError):
                # A malformed or unowned checkpoint is never rendered as a new
                # actionable task; disclose source incompleteness without bytes.
                warnings.append('task_checkpoint_unavailable')
    return rows, sorted(set(warnings))


def collect(*, db, user, project):
    from routers import solve_queue as queue_router
    from services import project_registry

    pid, owner = str(project.id), str(user.id)
    ctx = PyPSAService.get_context(project_registry.registry_key(project))
    if ctx is None:
        active = PyPSAService.get_active_context()
        if str(active.project_uuid) == pid:
            ctx = active
    rows, warnings = _tasks(project_registry.project_dir(project), pid, owner)
    from harness import activity_history
    native_jobs = job_adapters.activity_jobs(ctx, owner_id=owner, project_id=pid)
    observed_jobs, history_warnings = activity_history.project_rows(owner, pid, native_jobs)
    warnings.extend(history_warnings)
    for native in observed_jobs:
        rows.append(_row(pid, native['family'], native['source_id'], native['title'],
                         native['native_status'], stage=native['stage'],
                         observation=native['observation'], observed_at=native.get('observed_at'),
                         last_observed_status=native.get('last_observed_status'),
                         review_required=native.get('review_required', False),
                         reason=native.get('reason')))
    # Use native persisted/live precedence and project authorization. Unlike
    # the global queue UI, Activity excludes even redacted foreign queue rows.
    prefix = queue_router._org_prefix(db, user)
    for job in queue_router._merged_jobs(project_key_prefix=prefix):
        if (job.get('project_key') != project_registry.registry_key(project)
                or str(job.get(queue_router._OWNER_KEY)) != owner):
            continue
        family = 'gridspine' if job.get('kind') == 'gridspine' else 'solve_queue'
        rows.append(_row(pid, family, str(job['id']), family.replace('_', ' '), job['status']))
    from harness.session import pending_confirmation_origins
    from harness import attention
    live = pending_confirmation_origins(owner, pid)
    decisions, decision_warnings = attention.project_records(owner, pid, live)
    warnings.extend(decision_warnings)
    recorded_ids = {r['source_id'] for r in decisions}
    for decision in decisions:
        rows.append(_row(pid, decision['family'], decision['source_id'],
                         decision['question']['title'] if decision['question'] else decision['tool_name'].replace('_', ' '),
                         decision['status'], session_id=decision['session_id'], tool_use_id=decision['tool_use_id'],
                         decision_id=decision['source_id'], decision_revision=decision['revision'],
                         decision_digest=decision['decision_digest'], observed_at=decision['observed_at'],
                         last_observed_status=decision['native_status'], observation='retained',
                         live_approval_available=decision['live_approval_available'], reason=decision['reason']))
        rows[-1]['native_status'] = decision['native_status']
    for pending in live:
        if pending['source_id'] in recorded_ids:
            continue
        rows.append(_row(pid, 'confirmation', pending['source_id'], pending['tool_name'].replace('_', ' '),
                         'waiting_for_approval', session_id=pending['session_id'],
                         reason='Open the originating conversation to inspect and decide.'))
    warnings.append('activity_history_is_bounded_observation')
    # Fixed ordering means duplicate/reordered fetches cannot create new rows.
    rows.sort(key=lambda r: (0 if r['status'] in {'running', 'queued'} else 1 if r['needs_you'] else 2, r['id']))
    return rows, sorted(set(warnings))


def snapshot(*, db, user, project, filter_name='all', limit=20, cursor=None, page_token=None):
    collected_at = time.monotonic_ns()
    rows, warnings = collect(db=db, user=user, project=project)
    if filter_name == 'running':
        rows = [r for r in rows if r['status'] in {'running', 'queued'}]
    elif filter_name == 'needs_you':
        rows = [r for r in rows if r['needs_you']]
    elif filter_name == 'completed':
        rows = [r for r in rows if r['status'] in _TERMINAL]
    elif filter_name == 'scheduled':
        rows = []  # Scheduling is unavailable until CP-11, not simulated.
    if len(rows) > _MAX_ROWS:
        warnings = sorted(set(warnings + ['activity_truncated']))
    rows = rows[:_MAX_ROWS]
    key = (str(user.id), str(project.id), filter_name)
    digest = hashlib.sha256(json.dumps([rows, warnings], sort_keys=True).encode()).hexdigest()
    now = time.monotonic()
    with _LOCK:
        for old in list(_SCOPES):
            if now - _SCOPES[old]['touched'] > 3600:
                del _SCOPES[old]
        state = _SCOPES.get(key)
        if state is None:
            state = dict(epoch=uuid.uuid4().hex, revision=0, digest=None, pages=OrderedDict())
            _SCOPES[key] = state
        _SCOPES.move_to_end(key)
        while len(_SCOPES) > 32:
            _SCOPES.popitem(last=False)
        state['touched'] = now
        newer = collected_at > state.get('collected_at', 0)
        if newer:
            state['collected_at'] = collected_at
        if newer and digest != state['digest']:
            state['revision'] += 1
            state['digest'] = digest
            current = f"{state['epoch']}:{state['revision']}"
            state['pages'][current] = (copy.deepcopy(rows), warnings)
            while len(state['pages']) > 4:
                state['pages'].popitem(last=False)
        current = f"{state['epoch']}:{state['revision']}"
        offset, selected = 0, current
        if page_token:
            try:
                selected, offset_text, size_text = page_token.rsplit(':', 2)
                offset = int(offset_text)
                if selected != current or int(size_text) != limit or offset < 0 or offset > _MAX_ROWS:
                    raise ValueError()
            except (ValueError, TypeError):
                raise HTTPException(409, {'error_kind': 'activity_resnapshot_required',
                                          'message': 'Activity changed or the cursor expired. Reload its snapshot.'}) from None
        selected_rows, selected_warnings = state['pages'][selected]
        next_offset = offset + limit
        return dict(version=1, project_id=str(project.id), filter=filter_name,
                    cursor=selected, revision=int(selected.rsplit(':', 1)[1]),
                    resnapshot_required=bool(cursor and cursor not in state['pages']),
                    items=copy.deepcopy(selected_rows[offset:next_offset]),
                    next_page=f'{selected}:{next_offset}:{limit}' if next_offset < len(selected_rows) else None,
                    warnings=selected_warnings, total=len(selected_rows),
                    scheduling_available=False)
