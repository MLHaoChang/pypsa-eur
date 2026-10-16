"""Durable bounded decision observations. Reading never restores authority.

Questions are display data captured only after the publication projector. A
confirmation records its native gate, without token, arguments or effect claims.
Checked source gaps retain published missing-slot reasons, never result values.
The original session and approval audit remain the only decision authority.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
import uuid
from collections import OrderedDict
from typing import get_args

from harness import owned_snapshots

MAX_RECORDS = 256
RETENTION_SECONDS = 30 * 24 * 3600
_FAILURES = OrderedDict()
_FAILURE_LOCK = threading.Lock()
_ERRORS = (OSError, ValueError, TypeError, KeyError, TimeoutError)
_FIELDS = {'source_id', 'family', 'session_id', 'tool_use_id', 'tool_name',
           'native_status', 'revision', 'created_at', 'observed_at', 'question'}
_QUESTION_STATES = {'waiting_for_input', 'followed_up', 'superseded'}
_CONFIRM_STATES = {'waiting_for_approval', 'approved', 'rejected', 'expired',
                   'aborted', 'revoked', 'unavailable'}


def _text(value, size):
    if not isinstance(value, str) or not value or len(value) > size:
        raise ValueError('invalid decision text')
    return value


def _question(payload):
    # Copy the native allow-listed display fields; never arbitrary frame data.
    if not isinstance(payload, dict):
        raise ValueError('invalid question')
    options = payload.get('options', [])
    if not isinstance(options, list) or not 1 <= len(options) <= 8:
        raise ValueError('invalid decision options')
    result = dict(title=_text(payload.get('title') or 'Question', 160),
                  question=_text(payload['question'], 800), options=[],
                  allow_free_text=payload.get('allow_free_text', True),
                  multi_select=payload.get('multi_select', False),
                  detail=payload.get('detail'), intent=payload.get('intent', 'choice'))
    if result['intent'] not in {'choice', 'plan_review'}:
        raise ValueError('invalid question intent')
    for name in ('allow_free_text', 'multi_select'):
        if type(result[name]) is not bool:
            raise ValueError('invalid question flag')
    if result['detail'] is not None:
        _text(result['detail'], 2000)
    for option in options:
        if not isinstance(option, dict):
            raise ValueError('invalid option')
        item = dict(label=_text(option['label'], 120),
                    description=option.get('description') or '',
                    recommended=option.get('recommended', False))
        if (not isinstance(item['description'], str) or len(item['description']) > 400
                or type(item['recommended']) is not bool):
            raise ValueError('invalid option')
        result['options'].append(item)
    return result


def _validate(record):
    fields = _FIELDS | {'source_gap'} if isinstance(record, dict) and record.get('family') == 'source_gap' else _FIELDS
    if not isinstance(record, dict) or set(record) != fields:
        raise ValueError('invalid decision record')
    source = _text(record['source_id'], 64)
    if len(source) != 64 or any(c not in '0123456789abcdef' for c in source):
        raise ValueError('invalid decision origin')
    for field, size in (('session_id', 200), ('tool_use_id', 200), ('tool_name', 120)):
        _text(record[field], size)
    family = record['family']
    if family not in {'question', 'confirmation', 'source_gap'}:
        raise ValueError('invalid decision family')
    states = (_QUESTION_STATES if family == 'question' else
              {'source_gap_recorded'} if family == 'source_gap' else _CONFIRM_STATES)
    if record['native_status'] not in states:
        raise ValueError('invalid decision status')
    if type(record['revision']) is not int or not 1 <= record['revision'] <= 1000000:
        raise ValueError('invalid decision revision')
    for key in ('created_at', 'observed_at'):
        value = record[key]
        if (isinstance(value, bool) or not isinstance(value, (float, int))
                or not math.isfinite(value) or not 0 < value <= time.time() + 300):
            raise ValueError('invalid decision time')
    if record['observed_at'] < record['created_at']:
        raise ValueError('invalid decision order')
    if family == 'question':
        if record['tool_name'] not in {'ask_user', 'checked_clarification'} or _question(record['question']) != record['question']:
            raise ValueError('invalid question')
    elif family == 'source_gap':
        if record['question'] is not None or record['tool_name'] != 'checked_answer':
            raise ValueError('invalid source gap origin')
        _gap(record['source_gap'])
        if (record['tool_use_id'] != record['source_gap']['turn_id']
                or record['source_id'] != _gap_id(record['source_gap']['answer_id'])):
            raise ValueError('invalid source gap identity')
    elif record['question'] is not None:
        raise ValueError('confirmation arguments prohibited')
    return record


def _uuid(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError('invalid checked origin')
    return value


def _scope_display(scope):
    """Closed request descriptors, never results, prose, paths or tool arguments."""
    from harness.assurance_scope import CostBasis, RequestScope

    allowed = {'version', 'profile', 'project_id', 'entity_id', 'metrics', 'start', 'end',
               'cost_basis', 'retain', 'evidence_id', 'comparison_evidence_id', 'window'}
    if (not isinstance(scope, dict) or set(scope) - allowed
            or not {'profile', 'project_id', 'entity_id'} <= set(scope)
            or ('start' in scope) != ('end' in scope)):
        raise ValueError('invalid gap scope')
    if scope.get('profile') not in get_args(RequestScope.model_fields['profile'].annotation):
        raise ValueError('invalid gap profile')
    # This is the requested descriptor, not the authoritative store binding;
    # a refused wrong-project request may itself contain a non-UUID identifier.
    _text(scope['project_id'], 128)
    for field, size in (('entity_id', 128), ('start', 64), ('end', 64)):
        if field in scope and (scope[field] is not None or field != 'entity_id'):
            _text(scope[field], size)
    if 'version' in scope and (type(scope['version']) is not int or scope['version'] != 1):
        raise ValueError('invalid gap scope version')
    if 'retain' in scope and type(scope['retain']) is not bool:
        raise ValueError('invalid gap retain flag')
    if 'window' in scope and scope['window'] != 'all_current_snapshots':
        raise ValueError('invalid gap window')
    for field in ('evidence_id', 'comparison_evidence_id'):
        if field in scope:
            _uuid(scope[field])
    if 'cost_basis' in scope:
        CostBasis.model_validate(scope['cost_basis'])
    if 'metrics' in scope:
        metrics = scope['metrics']
        if (not isinstance(metrics, list) or not 1 <= len(metrics) <= 20
                or any(not isinstance(m, str) or not 1 <= len(m) <= 64 for m in metrics)
                or len(set(metrics)) != len(metrics)):
            raise ValueError('invalid gap metrics')
    return scope


def _gap(gap):
    if not isinstance(gap, dict) or set(gap) != {'answer_id', 'turn_id', 'request_scope', 'slots'}:
        raise ValueError('invalid source gap')
    _uuid(gap['answer_id'])
    _uuid(gap['turn_id'])
    _scope_display(gap['request_scope'])
    slots = gap['slots']
    if not isinstance(slots, list) or not 1 <= len(slots) <= 20:
        raise ValueError('invalid source gap slots')
    for slot in slots:
        if (not isinstance(slot, dict) or set(slot) != {'metric', 'disposition', 'reason'}
                or slot['disposition'] not in {'unavailable', 'needs_clarification'}
                or not isinstance(slot['metric'], str) or not 1 <= len(slot['metric']) <= 64
                or not isinstance(slot['reason'], str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,119}', slot['reason'])):
            raise ValueError('invalid source gap slot')
    if len({s['metric'] for s in slots}) != len(slots):
        raise ValueError('duplicate source gap metric')
    return gap


def _gap_id(answer_id):
    return hashlib.sha256(('checked-gap:' + answer_id).encode()).hexdigest()


def checked_answer(session, project):
    """Observe only the native committed answer at its admitted publication seam."""
    def change(records):
        turn = session.assurance_turn
        answer = turn.approved
        owner, pid = _scope(session, project)
        if answer['owner_id'] != owner or answer['project_id'] != pid:
            raise ValueError('unbound checked answer')
        receipt = answer['receipt']
        slots = [{k: slot[k] for k in ('metric', 'disposition', 'reason')}
                 for slot in receipt.get('result_slots', []) if slot['disposition'] != 'answered']
        if not slots:
            return
        # reason is a server annotation outside the request schema, already in
        # the slots. Do not retain any answer text, values or evidence payloads.
        scope = {k: v for k, v in receipt['request_scope'].items() if k != 'reason'}
        gap = _gap(dict(answer_id=answer['answer_id'], turn_id=answer['turn_id'],
                        request_scope=scope, slots=slots))
        source = _gap_id(gap['answer_id'])
        if any(r['source_id'] == source for r in records):
            return
        now = time.time()
        records.append(_validate(dict(source_id=source, family='source_gap', session_id=session.session_id,
            tool_use_id=gap['turn_id'], tool_name='checked_answer', native_status='source_gap_recorded',
            revision=1, created_at=now, observed_at=now, question=None, source_gap=gap)))
    _observe(session, project, change)


def _failed(owner, project, failed):
    with _FAILURE_LOCK:
        key = (owner, project)
        if failed:
            _FAILURES[key] = True
            _FAILURES.move_to_end(key)
            while len(_FAILURES) > 32:
                _FAILURES.popitem(last=False)
        return key in _FAILURES


def _update(owner, project, change=None):
    directory = owned_snapshots.directory('chat-attention', owner, project)
    if change is None and not directory.exists() and not directory.is_symlink():
        return [], ['decision_history_unavailable'] if _failed(owner, project, False) else []
    with owned_snapshots.locked(directory):
        before, truncated = owned_snapshots.read(directory, owner, project,
            validate=_validate, max_records=MAX_RECORDS, filename='decisions.json')
        records = [r.copy() for r in before if time.time() - r['observed_at'] < RETENTION_SECONDS]
        # A previous truncation remains visible while any retained history does.
        was_truncated = truncated
        truncated = truncated and bool(records)
        if change:
            change(records)
        records.sort(key=lambda r: (r['created_at'], r['source_id']), reverse=True)
        truncated = truncated or len(records) > MAX_RECORDS
        records = records[:MAX_RECORDS]
        # Bound bytes as well as origins, including worst-case escaped Unicode.
        while len(json.dumps(records).encode()) > owned_snapshots.MAX_BYTES - 1000:
            records.pop()
            truncated = True
        if records != before or truncated != was_truncated:
            owned_snapshots.write(directory, owner, project, records, truncated, filename='decisions.json')
    warnings = ['decision_history_truncated'] if truncated else []
    if _failed(owner, project, False):
        warnings.append('decision_history_unavailable')
    return records, warnings


def _scope(session, project):
    return (getattr(session, 'owner_user_id', None), str(project) if project else None)


def _observe(session, project, change):
    owner, project = _scope(session, project)
    # Minimal seam stand-ins and unsaved projects have no durable owned scope.
    if not owner or not project:
        return
    try:
        _update(owner, project, change)
    except _ERRORS:
        _failed(owner, project, True)


def _advance(record, state):
    record.update(native_status=state, revision=record['revision'] + 1,
                  observed_at=max(record['observed_at'], time.time()))


def followed_up(session, project):
    """A later admitted native turn happened; no answer correctness inferred."""
    def change(records):
        for record in records:
            if (record['family'] == 'question' and record['session_id'] == session.session_id
                    and record['native_status'] == 'waiting_for_input'):
                _advance(record, 'followed_up')
    _observe(session, project, change)


def question_presented(session, project, payload, *, checked=False):
    def change(records):
        for record in records:
            if (record['family'] == 'question' and record['session_id'] == session.session_id
                    and record['native_status'] == 'waiting_for_input'):
                _advance(record, 'superseded')
        now = time.time()
        record = dict(source_id=hashlib.sha256(uuid.uuid4().bytes).hexdigest(), family='question',
                      session_id=session.session_id, tool_use_id=payload['tool_use_id'],
                      tool_name='checked_clarification' if checked else 'ask_user',
                      native_status='waiting_for_input', revision=1,
                      created_at=now, observed_at=now, question=_question(payload))
        records.append(_validate(record))
    _observe(session, project, change)


def confirmation(session, pc, tool_use_id, state='waiting_for_approval'):
    def change(records):
        source = hashlib.sha256(pc.token.encode()).hexdigest()
        previous = next((r for r in records if r['source_id'] == source), None)
        if previous:
            if previous['native_status'] == 'waiting_for_approval' and state != 'waiting_for_approval':
                _advance(previous, state)
            return
        now = time.time()
        records.append(_validate(dict(source_id=source, family='confirmation', session_id=session.session_id,
            tool_use_id=tool_use_id, tool_name=pc.tool_name, native_status=state, revision=1,
            created_at=now, observed_at=now, question=None)))
    _observe(session, getattr(pc, 'project_id', None), change)


def project_records(owner, project, live):
    """Private display projection, including exact current native availability."""
    try:
        records, warnings = _update(owner, project)
    except _ERRORS:
        return [], ['decision_history_unavailable']
    pending = {(r['source_id'], r['session_id'], r['tool_name']) for r in live}
    result = []
    for record in records:
        active = (record['family'] == 'confirmation'
                  and (record['source_id'], record['session_id'], record['tool_name']) in pending
                  and record['native_status'] == 'waiting_for_approval')
        state = record['native_status']
        if record['family'] == 'question':
            status = 'waiting_for_input' if state == 'waiting_for_input' else 'decision_recorded'
            reason = ('Inspect the recorded question. Continue through an ordinary chat turn; this view cannot answer it.'
                if state == 'waiting_for_input' else
                'A later native turn or question was recorded. This does not establish that the question was answered correctly or resolved.')
        elif record['family'] == 'source_gap':
            status = 'needs_review'
            reason = ('The published checked answer had unavailable metrics or needed clarification. '
                      'This is a historical gap, not a current evidence check. A later turn does not resolve it; '
                      'use an ordinary checked request to obtain a new answer.')
        else:
            if state == 'waiting_for_approval':
                status = 'waiting_for_approval' if active else 'needs_review'
                reason = ('Inspect and decide in the originating live chat.' if active else
                          'No matching live approval is available. Inspection cannot revive this approval.')
            elif state == 'unavailable':
                status = 'needs_review'
                reason = 'The confirmation outcome could not be audited; the native gate did not permit execution.'
            else:
                status = 'decision_recorded'
                reason = 'A native confirmation outcome was recorded. This does not establish that work ran or succeeded.'
        view = dict(record, project_id=project, status=status, live_approval_available=active,
                    inspection_only=True, evidence_available=False, reason=reason)
        view['decision_digest'] = hashlib.sha256(json.dumps([owner, view], sort_keys=True).encode()).hexdigest()
        result.append(view)
    return result, warnings
