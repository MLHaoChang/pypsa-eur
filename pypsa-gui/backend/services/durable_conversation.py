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
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from harness.history import trim_session_messages
from services.pypsa_service import PyPSAService

_LOCK = threading.RLock()

# Rotation keeps the newest episodes; older ones are counted, not kept.
EPISODE_LINEAGE_MAX = 32
# Unverified effects awaiting reconciliation; the overflow is counted.
RECONCILE_MAX = 64


def _wall_clock() -> float:
    """Wall time, as ``harness.runner`` leases use (one clock for all)."""
    return time.time()


def _lease_seconds() -> float:
    from harness import runner

    return float(runner.LEASE_SECONDS)


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


def _require_authoritative_replica(
    thread: dict[str, Any], *, adopt: bool = False, renew: bool = True,
) -> bool:
    """Refuse controls when this process is not the thread's pinned replica.

    The owner renews ``owner_lease_until`` (wall time, ``runner.LEASE_SECONDS``)
    on every control that saves (``renew``); reads leave it as persisted. Another replica is refused
    while the lease is live. Once it has lapsed the owner process is taken to
    be gone: only ``resume_episode`` (``adopt=True``) may take the thread over,
    which re-pins it here and counts as a restart. Returns True on takeover.
    """
    import security

    pinned = thread.get('owner_replica_id')
    current = security.replica_id()
    now = _wall_clock()
    takeover = False
    if pinned and pinned != current:
        lapsed = float(thread.get('owner_lease_until') or 0) <= now
        if not (lapsed and adopt):
            _refuse(
                'wrong_replica',
                'This durable thread is pinned to another application worker.',
                409,
                owner_replica_id=pinned,
                resume_required=lapsed,
            )
        thread['owner_replica_id'] = current
        takeover = True
    if renew or takeover:
        thread['owner_lease_until'] = now + _lease_seconds()
    return takeover


def _empty_thread(thread_id: str, *, owner_id: str, project_id: str) -> dict[str, Any]:
    import security

    now = datetime.now(timezone.utc).isoformat()
    return {
        'version': 1,
        'thread_id': thread_id,
        'owner_id': owner_id,
        'project_id': project_id,
        'owner_replica_id': security.replica_id(),
        'owner_lease_until': _wall_clock() + _lease_seconds(),
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
        'episode_lineage': [],
        'episode_lineage_dropped': 0,
        'reconcile': [],
        'reconcile_dropped': 0,
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
        _require_authoritative_replica(thread, renew=False)
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
    """Mint a new ChatSession episode; drop the prior episode and its approvals.

    Rehydrates ``discussed_refs`` from durable ``refs.discussed`` so a restart
    can still export or continue the discussed run without rebinding. Live
    evidence fields (status/mode/fingerprint) are refreshed when the run is
    still readable under the current ACL; otherwise the durable identifiers
    alone are restored.
    """
    with _LOCK:
        path, thread = _read(thread_id)
        takeover = _require_authoritative_replica(thread, adopt=True)
        _require_generation(thread, generation)
        new_session = _rotate_episode(
            thread, reason='takeover' if takeover else 'resume', takeover=takeover,
        )
        _save(path, thread)
        _hydrate_discussed_refs(new_session, thread)
        return _view(thread), new_session


def _live_episode(thread: dict[str, Any]):
    """The current episode's session; refuse while its turn is running."""
    from services import chat_service

    episode_id = thread.get('episode_id')
    session = chat_service.get_session(episode_id) if episode_id else None
    if session is not None and session._turn_in_flight:
        # A live turn on this worker: never drop or rebind it mid-turn.
        _refuse('turn_already_in_flight', 'Wait for the episode turn to finish.')
    return session


def _rotate_episode(thread: dict[str, Any], *, reason: str, takeover: bool = False):
    """Replace the episode: reconcile, revoke and drop the old, mint a new one.

    The caller holds ``_LOCK``, has checked authority and generation, and
    saves. Only structured facts and budgets carry; the new history is empty.
    """
    from services import chat_service

    _live_episode(thread)
    old_episode = thread.get('episode_id')
    effects = _uncertain_effects(thread, old_episode, takeover=takeover)
    _revoke_checked_episode(thread)
    if old_episode:
        chat_service.drop_session(old_episode)
    new_session = chat_service.get_or_create_session(owner_user_id=thread['owner_id'])
    now = datetime.now(timezone.utc).isoformat()
    thread['episode_id'] = new_session.session_id
    thread['generation'] = int(thread.get('generation', 1)) + 1
    thread['checkpoint_rev'] = int(thread.get('checkpoint_rev', 0)) + 1
    _bounded_extend(thread, 'episode_lineage', [{
        'episode_id': new_session.session_id,
        'generation': thread['generation'],
        'reason': reason,
        'at': now,
    }], EPISODE_LINEAGE_MAX, keep='newest')
    _bounded_extend(thread, 'reconcile', effects, RECONCILE_MAX, keep='oldest')
    thread['updated_at'] = now
    return new_session


def switch_profile(thread_id: str, *, generation: int, profile: Any):
    """Bind a resolved, ready provider profile to the thread's episode.

    The router resolves the profile and refuses an unknown or keyless one
    before this runs. A same-wire switch (or an unbound episode) rebinds the
    episode in place, since messages are model-agnostic within a wire. A
    cross-wire switch never replays the old history: it starts a child
    episode that carries only the thread's structured facts and budgets.
    """
    with _LOCK:
        path, thread = _read(thread_id)
        _require_authoritative_replica(thread)
        _require_generation(thread, generation)
        session = _live_episode(thread)
        bound = getattr(session, 'bound_wire', None) if session is not None else None
        if session is not None and bound in (None, profile.wire):
            carried = 'episode'
            thread['generation'] = int(thread.get('generation', 1)) + 1
            thread['checkpoint_rev'] = int(thread.get('checkpoint_rev', 0)) + 1
            thread['updated_at'] = datetime.now(timezone.utc).isoformat()
        else:
            carried = 'facts_budgets'
            session = _rotate_episode(thread, reason='profile_switch')
        with session._lock:
            session.profile_id = profile.id
            session.bound_wire = profile.wire
            session.model = profile.model
        _bounded_extend(thread, 'profile_lineage', [{
            'profile_id': profile.id,
            'wire': profile.wire,
            'episode_id': session.session_id,
            'generation': thread['generation'],
            'carried': carried,
            'at': thread['updated_at'],
        }], EPISODE_LINEAGE_MAX, keep='newest')
        _save(path, thread)
        if carried == 'facts_budgets':
            _hydrate_discussed_refs(session, thread)
        return _view(thread), session


def _bounded_extend(
    thread: dict[str, Any], field: str, items: list[dict[str, Any]],
    cap: int, *, keep: str,
) -> None:
    """Append under a cap; the overflow is counted in ``<field>_dropped``."""
    merged = list(thread.get(field) or []) + items
    overflow = max(0, len(merged) - cap)
    if overflow:
        merged = merged[overflow:] if keep == 'newest' else merged[:cap]
    thread[field] = merged
    thread[field + '_dropped'] = int(thread.get(field + '_dropped') or 0) + overflow


def _uncertain_effects(
    thread: dict[str, Any], episode_id: str | None, *, takeover: bool,
) -> list[dict[str, Any]]:
    """What the prior episode may have done without a recorded outcome.

    Read-only: the episode's event log is read, never repaired or replayed,
    and no tool is called. A turn that started and never ended (or that a
    repair closed as interrupted) is unverified, and so is each tool call in
    it without a real result. Only identifiers and tool names are kept, never
    tool inputs. A takeover (the owner worker died) also lists the thread's
    jobs, whose state must be read back before anything relies on it.
    """
    from types import SimpleNamespace

    from harness import eventlog

    known = {
        (e.get('kind'), e.get('episode_id'), e.get('tool_use_id'), e.get('job_id'))
        for e in thread.get('reconcile') or []
    }
    found: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc).isoformat()

    def add(entry: dict[str, Any]) -> None:
        marker = (entry['kind'], entry.get('episode_id'),
                  entry.get('tool_use_id'), entry.get('job_id'))
        if marker not in known:
            known.add(marker)
            found.append({**entry, 'status': 'unverified', 'recorded_at': now})

    if episode_id:
        path = eventlog.path_for(SimpleNamespace(
            session_id=episode_id, owner_user_id=thread['owner_id'],
        ))
        events = eventlog.read_events(path) if path.exists() else []
        starts = [i for i, e in enumerate(events) if e.get('type') == 'turn/start']
        tail = events[starts[-1]:] if starts else []
        ends = [e for e in tail if e.get('type') == 'turn/end']
        if tail and (not ends or ends[-1].get('reason') == 'interrupted'):
            add({'kind': 'turn', 'episode_id': episode_id})
            answered = {
                block.get('tool_use_id')
                for e in tail
                if e.get('type') == 'tool/result' and e.get('error_kind') != 'interrupted'
                for block in (e.get('message') or {}).get('content') or []
                if isinstance(block, dict)
            }
            for e in tail:
                if e.get('type') != 'assistant/attempt':
                    continue
                for block in (e.get('message') or {}).get('content') or []:
                    if (isinstance(block, dict) and block.get('type') == 'tool_use'
                            and block.get('id') not in answered):
                        add({
                            'kind': 'tool_call', 'episode_id': episode_id,
                            'tool_use_id': str(block.get('id')),
                            'tool': str(block.get('name')),
                        })
    if takeover:
        for job_id in thread.get('job_ids') or []:
            add({'kind': 'job', 'job_id': str(job_id)})
    return found


def _hydrate_discussed_refs(session, thread: dict[str, Any]) -> None:
    discussed = dict((thread.get('refs') or {}).get('discussed') or {})
    run_id = discussed.get('run_id')
    if not isinstance(run_id, str) or not run_id:
        return
    hydrated = {
        'project_id': discussed.get('project_id') or thread.get('project_id'),
        'run_id': run_id,
    }
    for key in ('section', 'source', 'baseline_run_id', 'window', 'asset_id'):
        if discussed.get(key) is not None:
            hydrated[key] = discussed[key]
    try:
        from services import run_evidence as re_

        summary = re_.get_run_evidence(run_id)
        hydrated['status'] = summary.get('status')
        hydrated['mode'] = summary.get('mode')
        hydrated['fingerprint'] = summary.get('fingerprint')
        hydrated['evidence_available'] = bool(summary.get('evidence_available'))
    except HTTPException:
        pass
    session.discussed_refs = hydrated


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
        _require_authoritative_replica(thread, renew=False)
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
                # Issue 18: open cards end as `revoked` on the approval log,
                # and the waiting turn is released now rather than timing out.
                sess.revoke_confirmations()
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


def find_thread_for_episode(episode_id: str) -> str | None:
    """Return the parent thread_id bound to ``episode_id`` in this project, if any."""
    if not episode_id:
        return None
    root, project_id, owner = _authority()
    base = root / 'durable_conversations'
    if not base.is_dir():
        return None
    for path in base.glob('*.json'):
        try:
            thread = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if (
            thread.get('episode_id') == episode_id
            and thread.get('project_id') == project_id
            and thread.get('owner_id') == owner
        ):
            return thread.get('thread_id')
    return None


def set_discussed_run(
    thread_id: str,
    *,
    generation: int,
    run_id: str,
    project_id: str | None = None,
    baseline_run_id: str | None = None,
    section: str | None = None,
    source: str | None = None,
    window: str | None = None,
    asset_id: str | None = None,
    clear_baseline: bool = False,
) -> dict[str, Any]:
    """Patch ``refs.discussed`` under generation CAS. Does not bump generation."""
    if not isinstance(run_id, str) or not run_id:
        _refuse('invalid_discussed_run', 'run_id is required.', 422)
    with _LOCK:
        path, thread = _read(thread_id)
        _require_authoritative_replica(thread)
        _require_generation(thread, generation)
        refs = thread.setdefault('refs', {'current': {}, 'discussed': {}})
        discussed = dict(refs.get('discussed') or {})
        discussed['run_id'] = run_id
        discussed['project_id'] = project_id if project_id is not None else thread['project_id']
        for key, value in (
            ('section', section),
            ('source', source),
            ('window', window),
            ('asset_id', asset_id),
        ):
            if value is not None:
                discussed[key] = value
        if baseline_run_id is not None:
            discussed['baseline_run_id'] = baseline_run_id
        elif clear_baseline:
            discussed.pop('baseline_run_id', None)
        refs['discussed'] = discussed
        thread['refs'] = refs
        thread['checkpoint_rev'] = int(thread.get('checkpoint_rev', 0)) + 1
        thread['updated_at'] = datetime.now(timezone.utc).isoformat()
        _save(path, thread)
        return _view(thread)


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
