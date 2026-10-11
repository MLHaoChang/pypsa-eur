"""Durable parent conversation thread (issue 09), distinct from ChatSession.

Structured goals/refs/budgets/tasks survive model-context compaction and
episode restart. Persistence follows assistant_tasks: per-project JSON with
tmp+fsync+replace and fail-closed owner/project checks.
"""
from __future__ import annotations

import collections
import copy
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from harness.history import trim_session_messages
from services.pypsa_service import PyPSAService

_LOCK = threading.RLock()


def _refuse(kind: str, message: str, status: int = 409) -> None:
    raise HTTPException(status, {'error_kind': kind, 'message': message})


def _authority() -> tuple[Path, str, str]:
    from services import chat_tools, project_registry

    ctx = PyPSAService.get_active_context()
    if not ctx.loaded_project or not ctx.project_uuid:
        _refuse(
            'thread_project_required',
            'Create, save and activate a project before using durable threads.',
        )
    with chat_tools._acting() as (db, user):
        project = project_registry.resolve_project(db, user, ctx.project_uuid)
        return project_registry.project_dir(project), str(project.id), str(user.id)


def _empty_thread(thread_id: str, *, owner_id: str, project_id: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        'version': 1,
        'thread_id': thread_id,
        'owner_id': owner_id,
        'project_id': project_id,
        'generation': 1,
        'episode_id': None,
        'goals': [],
        'constraints': [],
        'units': {},
        'refs': {'current': {}, 'discussed': {}},
        'task_ids': [],
        'job_ids': [],
        'experiment_ids': [],
        'evidence_revisions': [],
        'profile_lineage': [],
        'budgets': {
            'reservations': [],
            'final_charges': [],
            'cached_usage': {},
        },
        'checkpoint_rev': 0,
        'created_at': now,
        'updated_at': now,
    }


def _path(thread_id: str) -> tuple[Path, str, str]:
    try:
        if str(uuid.UUID(thread_id)) != thread_id:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        _refuse('thread_not_found', 'Thread not found in this project.', 404)
    root, project_id, owner = _authority()
    return root / 'durable_conversations' / (thread_id + '.json'), project_id, owner


def _save(path: Path, thread: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('w', encoding='utf-8') as stream:
            json.dump(thread, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _read(thread_id: str) -> tuple[Path, dict[str, Any]]:
    path, project_id, owner = _path(thread_id)
    try:
        thread = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        _refuse('thread_not_found', 'Thread not found in this project.', 404)
    except (OSError, ValueError):
        _refuse('thread_unreadable', 'The durable thread checkpoint needs repair.')
    if thread.get('project_id') != project_id or thread.get('owner_id') != owner:
        _refuse('thread_not_found', 'Thread not found in this project.', 404)
    if not thread.get('owner_id'):
        _refuse('thread_not_found', 'Thread not found in this project.', 404)
    return path, thread


def _view(thread: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(thread)


def create_thread() -> dict[str, Any]:
    root, project_id, owner = _authority()
    thread_id = str(uuid.uuid4())
    path = root / 'durable_conversations' / (thread_id + '.json')
    thread = _empty_thread(thread_id, owner_id=owner, project_id=project_id)
    with _LOCK:
        _save(path, thread)
        return _view(thread)


def get_thread(thread_id: str) -> dict[str, Any]:
    with _LOCK:
        _, thread = _read(thread_id)
        return _view(thread)


def checkpoint_thread(thread_id: str, **fields: Any) -> dict[str, Any]:
    """Update structured parent facts. Compaction must never call this for budgets."""
    allowed = {
        'goals', 'constraints', 'units', 'refs', 'task_ids', 'job_ids',
        'experiment_ids', 'evidence_revisions', 'profile_lineage', 'episode_id',
        'budgets',
    }
    unknown = set(fields) - allowed
    if unknown:
        _refuse('invalid_thread_field', f'Unsupported fields: {sorted(unknown)}', 422)
    with _LOCK:
        path, thread = _read(thread_id)
        for key, value in fields.items():
            thread[key] = copy.deepcopy(value)
        thread['checkpoint_rev'] = int(thread.get('checkpoint_rev', 0)) + 1
        thread['updated_at'] = datetime.now(timezone.utc).isoformat()
        _save(path, thread)
        return _view(thread)


def compact_model_context(
    thread_id: str,
    messages: collections.deque | None = None,
    *,
    max_len: int | None = None,
) -> dict[str, Any]:
    """Trim a child model-message deque; parent structured fields stay authoritative."""
    with _LOCK:
        _, thread = _read(thread_id)
        # Snapshot so a concurrent checkpoint still returns authoritative disk state.
        before = json.dumps(thread, sort_keys=True, default=str)
    if messages is not None:
        if not isinstance(messages, collections.deque):
            _refuse('invalid_model_context', 'messages must be a deque', 422)
        trim_session_messages(messages, max_len=max_len)
    with _LOCK:
        _, after = _read(thread_id)
        if json.dumps(after, sort_keys=True, default=str) != before:
            return _view(after)
        return _view(after)


def resume_episode(thread_id: str):
    """Mint a new ChatSession episode; drop the prior episode and its approvals."""
    from services import chat_service

    with _LOCK:
        path, thread = _read(thread_id)
        old_episode = thread.get('episode_id')
        if old_episode:
            chat_service.drop_session(old_episode)
        new_session = chat_service.get_or_create_session(
            owner_user_id=thread['owner_id'],
        )
        thread['episode_id'] = new_session.session_id
        thread['generation'] = int(thread.get('generation', 1)) + 1
        thread['checkpoint_rev'] = int(thread.get('checkpoint_rev', 0)) + 1
        thread['updated_at'] = datetime.now(timezone.utc).isoformat()
        _save(path, thread)
        return _view(thread), new_session


def authorize_thread(thread_id: str) -> dict[str, Any]:
    """Explicit ACL recheck used by later HTTP seams (B-I1)."""
    return get_thread(thread_id)


def reserve_budget(
    thread_id: str,
    *,
    key: str,
    kind: str,
    amount: int | float,
    unit: str,
) -> dict[str, Any]:
    """Reserve once under ``key``. Idempotent if the same open reservation exists."""
    if not isinstance(key, str) or not 1 <= len(key) <= 64:
        _refuse('invalid_budget_key', 'Provide a stable budget key (1–64 characters).', 422)
    if not isinstance(amount, (int, float)) or amount < 0:
        _refuse('invalid_budget_amount', 'amount must be a non-negative number.', 422)
    with _LOCK:
        path, thread = _read(thread_id)
        budgets = thread.setdefault('budgets', {
            'reservations': [], 'final_charges': [], 'cached_usage': {},
        })
        if any(c.get('key') == key for c in budgets.get('final_charges', [])):
            _refuse('budget_already_settled', 'This budget key is already settled.')
        existing = next(
            (r for r in budgets.get('reservations', []) if r.get('key') == key),
            None,
        )
        if existing is not None:
            if (
                existing.get('kind') != kind
                or existing.get('amount') != amount
                or existing.get('unit') != unit
            ):
                _refuse('budget_key_conflict', 'This key already reserves a different amount.')
            return {'status': 'reserved', 'key': key}
        budgets.setdefault('reservations', []).append({
            'key': key, 'kind': kind, 'amount': amount, 'unit': unit,
        })
        thread['checkpoint_rev'] = int(thread.get('checkpoint_rev', 0)) + 1
        thread['updated_at'] = datetime.now(timezone.utc).isoformat()
        _save(path, thread)
        return {'status': 'reserved', 'key': key}


def settle_budget(
    thread_id: str,
    *,
    key: str,
    amount: int | float,
    unit: str,
    cached: int | float = 0,
) -> dict[str, Any]:
    """Move a reservation into final_charges once; accumulate cached_usage."""
    if not isinstance(amount, (int, float)) or amount < 0:
        _refuse('invalid_budget_amount', 'amount must be a non-negative number.', 422)
    if not isinstance(cached, (int, float)) or cached < 0:
        _refuse('invalid_budget_amount', 'cached must be a non-negative number.', 422)
    with _LOCK:
        path, thread = _read(thread_id)
        budgets = thread.setdefault('budgets', {
            'reservations': [], 'final_charges': [], 'cached_usage': {},
        })
        if any(c.get('key') == key for c in budgets.get('final_charges', [])):
            _refuse('budget_already_settled', 'This budget key is already settled.')
        reservation = next(
            (r for r in budgets.get('reservations', []) if r.get('key') == key),
            None,
        )
        if reservation is None:
            _refuse('budget_not_reserved', 'Reserve this key before settling.', 404)
        budgets['reservations'] = [
            r for r in budgets.get('reservations', []) if r.get('key') != key
        ]
        budgets.setdefault('final_charges', []).append({
            'key': key,
            'kind': reservation.get('kind'),
            'amount': amount,
            'unit': unit,
        })
        usage = budgets.setdefault('cached_usage', {})
        usage[unit] = float(usage.get(unit, 0)) + float(cached)
        # Keep integer tokens as ints when both sides are whole numbers.
        if float(usage[unit]).is_integer():
            usage[unit] = int(usage[unit])
        thread['checkpoint_rev'] = int(thread.get('checkpoint_rev', 0)) + 1
        thread['updated_at'] = datetime.now(timezone.utc).isoformat()
        _save(path, thread)
        return {'status': 'settled', 'key': key}


def _reset_for_tests() -> None:
    """Test-only: no in-memory registry beyond the lock; disk is fixture-scoped."""
    with _LOCK:
        pass
