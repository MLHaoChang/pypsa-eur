"""Typed cross-domain experiments (live-voice issue 10, first slice).

Persists an isolated experiment plan (baseline, typed cases, limits) under
``<project>/experiments/<id>.json`` without executing cases or bypassing
harness admission. Later slices advance cases through ordinary tools/queues.
"""
from __future__ import annotations

import copy
import json
import math
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from services.pypsa_service import PyPSAService

_LOCK = threading.RLock()
DOMAINS = frozenset({'network', 'commercial', 'reliability', 'gridspine'})
VALUE_TYPES = frozenset({'number', 'boolean', 'string', 'enum'})
_PATH = re.compile(r'^[A-Za-z][A-Za-z0-9_]{0,31}(\.[A-Za-z][A-Za-z0-9_.-]{0,63}){1,6}$')
_CASE_ID = re.compile(r'^[A-Za-z][A-Za-z0-9_-]{0,31}$')
MAX_CASES = 8
MAX_CHANGES = 12


def _refuse(kind: str, message: str, status: int = 409, **extra: Any) -> None:
    detail = {'error_kind': kind, 'message': message}
    detail.update(extra)
    raise HTTPException(status, detail)


def _authority() -> tuple[Path, str, str]:
    from services import chat_tools, project_registry

    ctx = PyPSAService.get_active_context()
    if not ctx.loaded_project or not ctx.project_uuid:
        _refuse(
            'experiment_project_required',
            'Create, save and activate a project before using experiments.',
        )
    with chat_tools._acting() as (db, user):
        project = project_registry.resolve_project(db, user, ctx.project_uuid)
        return project_registry.project_dir(project), str(project.id), str(user.id)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _save(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('w', encoding='utf-8') as stream:
            json.dump(record, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _validate_change(change: Any) -> dict[str, Any]:
    if not isinstance(change, dict):
        _refuse('invalid_experiment_change', 'Each change must be an object.', 422)
    path = change.get('path')
    value_type = change.get('value_type')
    value = change.get('value')
    if not isinstance(path, str) or not _PATH.fullmatch(path):
        _refuse('invalid_experiment_change', 'change.path must be a dotted component path.', 422)
    if value_type not in VALUE_TYPES:
        _refuse(
            'invalid_experiment_change',
            f'value_type must be one of {sorted(VALUE_TYPES)}.',
            422,
        )
    if value_type == 'number':
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            _refuse('invalid_experiment_change', 'number changes need a finite numeric value.', 422)
    elif value_type == 'boolean':
        if value is not True and value is not False:
            _refuse('invalid_experiment_change', 'boolean changes need true or false.', 422)
    elif value_type in ('string', 'enum'):
        if not isinstance(value, str) or not 1 <= len(value) <= 120:
            _refuse('invalid_experiment_change', 'string/enum values must be 1–120 characters.', 422)
    unit = change.get('unit')
    out = {'path': path, 'value': value, 'value_type': value_type}
    if unit is not None:
        if not isinstance(unit, str) or not 1 <= len(unit) <= 24:
            _refuse('invalid_experiment_change', 'unit must be a short string when provided.', 422)
        out['unit'] = unit
    return out


def _validate_cases(cases: Any) -> list[dict[str, Any]]:
    if not isinstance(cases, list) or not 1 <= len(cases) <= MAX_CASES:
        _refuse('invalid_experiment_cases', f'Provide between 1 and {MAX_CASES} cases.', 422)
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for raw in cases:
        if not isinstance(raw, dict):
            _refuse('invalid_experiment_cases', 'Each case must be an object.', 422)
        case_id = raw.get('id')
        if not isinstance(case_id, str) or not _CASE_ID.fullmatch(case_id):
            _refuse('invalid_experiment_cases', 'case.id must be a short identifier.', 422)
        if case_id in seen:
            _refuse('invalid_experiment_cases', 'case ids must be unique.', 422)
        seen.add(case_id)
        changes_raw = raw.get('changes')
        if not isinstance(changes_raw, list) or not 1 <= len(changes_raw) <= MAX_CHANGES:
            _refuse(
                'invalid_experiment_cases',
                f'Each case needs 1–{MAX_CHANGES} typed changes.',
                422,
            )
        label = raw.get('label')
        case: dict[str, Any] = {
            'id': case_id,
            'changes': [_validate_change(c) for c in changes_raw],
            'status': 'planned',
            'job_id': None,
            'run_id': None,
            'error': None,
        }
        if label is not None:
            if not isinstance(label, str) or not 1 <= len(label) <= 120:
                _refuse('invalid_experiment_cases', 'case.label must be 1–120 characters.', 422)
            case['label'] = label
        out.append(case)
    return out


def _index_path(root: Path) -> Path:
    return root / 'experiments' / 'index.json'


def _load_index(root: Path) -> dict[str, Any]:
    path = _index_path(root)
    if not path.is_file():
        return {'by_request': {}, 'order': []}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'by_request': {}, 'order': []}
    if not isinstance(data, dict):
        return {'by_request': {}, 'order': []}
    data.setdefault('by_request', {})
    data.setdefault('order', [])
    return data


def _view(record: dict[str, Any], *, detail: bool = False) -> dict[str, Any]:
    out = {
        'experiment_id': record['experiment_id'],
        'title': record['title'],
        'project_id': record['project_id'],
        'domain': record['domain'],
        'status': record['status'],
        'baseline_run_id': record.get('baseline_run_id'),
        'case_count': len(record.get('cases') or []),
        'executed': False,
        'job_ids': list(record.get('job_ids') or []),
        'created_at': record.get('created_at'),
        'updated_at': record.get('updated_at'),
        'note': (
            'Persisted only — no cases were executed. Advance through ordinary '
            'harness tools after approval; this record does not run hidden actions.'
        ),
    }
    if detail:
        out['cases'] = copy.deepcopy(record.get('cases') or [])
        out['limits'] = copy.deepcopy(record.get('limits') or {})
        out['request_id'] = record.get('request_id')
    return out


def start_experiment(
    title: str,
    cases: list[dict],
    request_id: str,
    domain: str = 'network',
    baseline_run_id: str | None = None,
    limits: dict | None = None,
) -> dict:
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120:
        _refuse('invalid_experiment', 'title must be 1–120 characters.', 422)
    if domain not in DOMAINS:
        _refuse('invalid_experiment_domain', f'domain must be one of {sorted(DOMAINS)}.', 422)
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 64:
        _refuse('invalid_experiment', 'request_id must be 1–64 characters.', 422)
    if baseline_run_id is not None and (
        not isinstance(baseline_run_id, str) or not 1 <= len(baseline_run_id) <= 64
    ):
        _refuse('invalid_experiment', 'baseline_run_id must be a short run id when set.', 422)
    validated = _validate_cases(cases)
    if limits is not None and not isinstance(limits, dict):
        _refuse('invalid_experiment', 'limits must be an object when provided.', 422)

    root, project_id, owner = _authority()
    with _LOCK:
        index = _load_index(root)
        existing_id = index.get('by_request', {}).get(request_id)
        if existing_id:
            path = root / 'experiments' / (existing_id + '.json')
            try:
                record = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError, FileNotFoundError):
                pass
            else:
                if record.get('owner_id') == owner and record.get('project_id') == project_id:
                    return _view(record, detail=True)

        experiment_id = str(uuid.uuid4())
        now = _now()
        record = {
            'version': 1,
            'experiment_id': experiment_id,
            'title': title.strip(),
            'project_id': project_id,
            'owner_id': owner,
            'domain': domain,
            'baseline_run_id': baseline_run_id,
            'cases': validated,
            'limits': copy.deepcopy(limits) if limits else {},
            'job_ids': [],
            'status': 'planned',
            'request_id': request_id,
            'created_at': now,
            'updated_at': now,
        }
        path = root / 'experiments' / (experiment_id + '.json')
        _save(path, record)
        index.setdefault('by_request', {})[request_id] = experiment_id
        order = index.setdefault('order', [])
        order.insert(0, experiment_id)
        index['order'] = order[:100]
        _save(_index_path(root), index)
        return _view(record, detail=True)


def get_experiment(experiment_id: str) -> dict:
    root, project_id, owner = _authority()
    try:
        if str(uuid.UUID(experiment_id)) != experiment_id:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        _refuse('experiment_not_found', 'Experiment not found in this project.', 404)
    path = root / 'experiments' / (experiment_id + '.json')
    try:
        record = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        _refuse('experiment_not_found', 'Experiment not found in this project.', 404)
    except (OSError, ValueError):
        _refuse('experiment_unreadable', 'The experiment checkpoint needs repair.')
    if record.get('project_id') != project_id or record.get('owner_id') != owner:
        _refuse('experiment_not_found', 'Experiment not found in this project.', 404)
    return _view(record, detail=True)


def list_experiments(limit: int = 10) -> dict:
    if type(limit) is not int or not 1 <= limit <= 20:
        _refuse('invalid_experiment_limit', 'limit must be 1–20.', 422)
    root, project_id, owner = _authority()
    with _LOCK:
        index = _load_index(root)
        items = []
        for experiment_id in index.get('order', []):
            path = root / 'experiments' / (experiment_id + '.json')
            try:
                record = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError, FileNotFoundError):
                continue
            if record.get('project_id') != project_id or record.get('owner_id') != owner:
                continue
            items.append(_view(record, detail=False))
            if len(items) >= limit:
                break
        return {
            'items': items,
            'total_count': len(items),
            'limit': limit,
            'note': 'Most recent first. Use get_experiment for case detail. Plans are not executed by listing.',
        }


def _reset_for_tests() -> None:
    with _LOCK:
        pass
