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


def _refuse(kind: str, message: str, status: int = 409, **extra) -> None:
    detail = {'error_kind': kind, 'message': message}
    detail.update(extra)
    raise HTTPException(status, detail)


def _require_generation(thread: dict[str, Any], generation: int) -> None:
    current = int(thread.get('generation', 1))
    try:
        expected = int(generation)
    except (TypeError, ValueError):
        _refuse('invalid_generation', 'generation must be an integer.', 422)
    if current != expected:
        _refuse(
            'stale_generation',
            'Thread generation has advanced; reload and retry.',
            generation=current,
        )


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


def _require_authoritative_replica(thread: dict[str, Any]) -> None:
    """Refuse controls when this process is not the thread's pinned replica."""
    import security

    pinned = thread.get('owner_replica_id')
    if not pinned:
        return
    current = security.replica_id()
    if pinned != current:
        _refuse(
            'wrong_replica',
            'This durable thread is pinned to another application worker.',
            409,
            owner_replica_id=pinned,
        )


def _empty_thread(thread_id: str, *, owner_id: str, project_id: str) -> dict[str, Any]:
    import security

    now = datetime.now(timezone.utc).isoformat()
    return {
        'version': 1,
        'thread_id': thread_id,
        'owner_id': owner_id,
        'project_id': project_id,
        'owner_replica_id': security.replica_id(),
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
        _require_authoritative_replica(thread)
        return _view(thread)


def checkpoint_thread(
    thread_id: str,
    *,
    generation: int,
    **fields: Any,
) -> dict[str, Any]:
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
        _require_authoritative_replica(thread)
        _require_generation(thread, generation)
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


def _revoke_checked_episode(thread: dict[str, Any]) -> None:
    """A new parent generation cannot revive an old checked history anchor."""
    from harness import assurance_recovery as recovery
    from harness.durable import network_guard
    from harness.session import ChatSession
    from services import chat_service

    episode_id = thread.get('episode_id')
    if not episode_id:
        return
    session = chat_service.get_session(episode_id)
    if session is not None and session.owner_user_id != thread['owner_id']:
        _refuse('thread_not_found', 'Episode is outside this parent scope.', 404)
    identifier = recovery.checkpoint_id(
        thread['owner_id'], thread['project_id'], episode_id,
    )
    try:
        checkpoint_exists = recovery.records._path(thread['owner_id'], identifier).exists()
    except HTTPException:
        # Assurance records require UUID actors; legacy non-assurance adapters
        # cannot have a private checked checkpoint under such an actor.
        if session is None or session.assurance_context is None:
            return
        raise
    if not checkpoint_exists and (session is None or session.assurance_context is None):
        return
    with network_guard():
        session = chat_service.get_session(episode_id)
        if session is None:
            # Revoke the private pointer even after process/registry restart.
            session = ChatSession(session_id=episode_id, owner_user_id=thread['owner_id'])
        with session._lock:
            if session.owner_user_id != thread['owner_id'] or (
                session.assurance_checkpoint_project
                and session.assurance_checkpoint_project != thread['project_id']
            ):
                _refuse('thread_not_found', 'Episode is outside this parent scope.', 404)
            if session._turn_in_flight:
                _refuse('turn_already_in_flight', 'Wait for the episode turn to finish.')
            if recovery.records._path(thread['owner_id'], identifier).exists():
                session.assurance_checkpoint_project = thread['project_id']
                recovery.invalidate(session)
            session.assurance_context = None


def resume_episode(thread_id: str, *, generation: int):
    """Mint a new ChatSession episode; drop the prior episode and its approvals."""
    from services import chat_service

    with _LOCK:
        path, thread = _read(thread_id)
        _require_authoritative_replica(thread)
        _require_generation(thread, generation)
        old_episode = thread.get('episode_id')
        _revoke_checked_episode(thread)
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


def _mint_bridge_credentials(thread: dict[str, Any]) -> dict[str, Any]:
    """Opaque stub credentials scoped to actor/project/generation (no media SDK)."""
    token = uuid.uuid4().hex
    return {
        'token': token,
        'owner_id': thread['owner_id'],
        'project_id': thread['project_id'],
        'thread_id': thread['thread_id'],
        'generation': int(thread.get('generation', 1)),
        'episode_id': thread.get('episode_id'),
        'expires_at': (
            datetime.now(timezone.utc).timestamp() + 3600
        ),
    }


def authorize_bridge_credentials(thread_id: str, token: str) -> dict[str, Any]:
    """Validate a previously minted bridge token against the current generation."""
    with _LOCK:
        _, thread = _read(thread_id)
        _require_authoritative_replica(thread)
        creds = thread.get('bridge_credentials') or {}
        if not token or creds.get('token') != token:
            _refuse('bridge_credentials_invalid', 'Bridge credentials are not valid.', 401)
        if int(creds.get('generation', -1)) != int(thread.get('generation', 1)):
            _refuse('bridge_credentials_stale', 'Bridge credentials were revoked by rebind.', 401)
        if creds.get('owner_id') != thread.get('owner_id'):
            _refuse('bridge_credentials_invalid', 'Bridge credentials are not valid.', 401)
        return copy.deepcopy(creds)


def rebind_scope(thread_id: str, *, generation: int) -> dict[str, Any]:
    """Bump generation, mint scoped bridge credentials, invalidate prior token.

    Clears pending approvals on the bound episode and bumps utterance-admission
    generation so stale Live/stream generations cannot admit. Parent facts stay.
    """
    from services import chat_service, utterance_admission as ua

    with _LOCK:
        path, thread = _read(thread_id)
        _require_authoritative_replica(thread)
        _require_generation(thread, generation)
        _revoke_checked_episode(thread)
        thread['generation'] = int(thread.get('generation', 1)) + 1
        thread['checkpoint_rev'] = int(thread.get('checkpoint_rev', 0)) + 1
        thread['updated_at'] = datetime.now(timezone.utc).isoformat()
        creds = _mint_bridge_credentials(thread)
        thread['bridge_credentials'] = creds
        episode_id = thread.get('episode_id')
        if episode_id:
            sess = chat_service.get_session(episode_id)
            if sess is not None:
                with sess._lock:
                    sess.pending_confirmations.clear()
                    sess.confirmation_decisions.clear()
                    sess._decision_events.clear()
            ua._DEFAULT.bump_generation(session_id=episode_id)
        _save(path, thread)
        view = _view(thread)
        # Return credentials once; disk retains them for authorize_bridge_credentials.
        return {
            **view,
            'credentials': {
                'token': creds['token'],
                'generation': creds['generation'],
                'expires_at': creds['expires_at'],
                'thread_id': creds['thread_id'],
                'project_id': creds['project_id'],
                'owner_id': creds['owner_id'],
                'episode_id': creds.get('episode_id'),
            },
        }


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
