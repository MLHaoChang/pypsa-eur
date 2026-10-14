"""
Typed cross-domain plans and network execution prerequisites.

Persists an isolated experiment plan (baseline, typed cases, limits) under
``<project>/experiments/<id>.json`` without executing cases or bypassing
harness admission. Later slices advance cases through ordinary tools/queues.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import threading
import uuid
from datetime import UTC, datetime
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError

from harness import experiment_contract as contract
from harness.durable import network_guard, transaction
from services.pypsa_service import PyPSAService

_LOCK = threading.RLock()
DOMAINS = frozenset({'network', 'commercial', 'reliability', 'gridspine'})
VALUE_TYPES = frozenset({'number', 'boolean', 'string', 'enum'})
_PATH = re.compile(r'^[A-Za-z][A-Za-z0-9_]{0,31}(\.[A-Za-z][A-Za-z0-9_.-]{0,63}){1,6}$')
_CASE_ID = re.compile(r'^[A-Za-z][A-Za-z0-9_-]{0,31}$')
MAX_CASES = contract.MAX_CASES
MAX_CHANGES = contract.MAX_CHANGES
MAX_RECORDS = 100
MAX_RECORD_BYTES = 128 * 1024
MAX_INDEX_BYTES = 32 * 1024


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
    return datetime.now(UTC).isoformat()


@contextmanager
def _transaction(root):
    try:
        with transaction(root / 'experiments'):
            yield
    except (TimeoutError, OSError):
        _refuse('experiment_storage_unavailable', 'The experiment store is unavailable; no operation was started.')


def _save(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('w', encoding='utf-8') as stream:
            json.dump(record, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _validate_change(change: Any) -> dict[str, Any]:
    if not isinstance(change, dict):
        _refuse('invalid_experiment_change', 'Each change must be an object.', 422)
    path = change.get('path')
    value_type = change.get('value_type')
    value = change.get('value')
    if set(change) - {'path', 'value', 'value_type', 'unit'}:
        _refuse('invalid_experiment_change', 'Unexpected change fields.', 422)
    if not isinstance(path, str) or len(path) > 128 or not _PATH.fullmatch(path):
        _refuse('invalid_experiment_change', 'change.path must be a dotted component path.', 422)
    if not isinstance(value_type, str) or value_type not in VALUE_TYPES:
        _refuse(
            'invalid_experiment_change',
            f'value_type must be one of {sorted(VALUE_TYPES)}.',
            422,
        )
    if value_type == 'number':
        try:
            finite = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
        except OverflowError:
            finite = False
        if not finite:
            _refuse('invalid_experiment_change', 'number changes need a finite numeric value.', 422)
        if isinstance(value, float) and value.is_integer():
            value = int(value)
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
        if not isinstance(raw, dict) or set(raw) - {'id', 'label', 'changes'}:
            _refuse('invalid_experiment_cases', 'Each case must contain only id, label and changes.', 422)
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


def _read_json(path: Path, maximum=MAX_RECORD_BYTES):
    def constant(_value):
        raise ValueError('nonfinite_json')

    def pairs(rows):
        out = {}
        for key, value in rows:
            if key in out:
                raise ValueError()
            out[key] = value
        return out

    try:
        if path.is_symlink():
            raise ValueError()
        with path.open('rb') as stream:
            blob = stream.read(maximum + 1)
        if len(blob) > maximum:
            raise ValueError()
        value = json.loads(blob, object_pairs_hook=pairs, parse_constant=constant)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except FileNotFoundError:
        raise
    except (OSError, ValueError, TypeError, RecursionError):
        _refuse('experiment_checkpoint_unreadable', 'The experiment checkpoint needs repair.')


def _valid_id(value):
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def _load_index(root: Path) -> dict[str, Any]:
    path = _index_path(root)
    try:
        data = _read_json(path, MAX_INDEX_BYTES)
    except FileNotFoundError:
        if any(path.parent.glob('*.json')):
            _refuse('experiment_checkpoint_unreadable', 'The experiment index is missing; no replacement was created.')
        return {'by_request': {}, 'order': []}
    requests, order = data.get('by_request'), data.get('order')
    if (set(data) != {'by_request', 'order'} or not isinstance(requests, dict)
            or not isinstance(order, list) or len(order) > MAX_RECORDS
            or len(requests) > MAX_RECORDS or len(set(map(str, order))) != len(order)
            or any(not _valid_id(v) for v in order)
            or any(not isinstance(k, str) or not 1 <= len(k) <= 64 or not _valid_id(v)
                   or v not in order for k, v in requests.items())):
        _refuse('experiment_checkpoint_unreadable', 'The experiment index needs repair.')
    files = set()
    for item in path.parent.glob('*.json'):
        if item.name == 'index.json':
            continue
        if item.is_symlink() or not _valid_id(item.stem) or len(files) >= MAX_RECORDS:
            _refuse('experiment_checkpoint_unreadable', 'An experiment checkpoint needs reconciliation.')
        files.add(item.stem)
    if files != set(order):
        _refuse('experiment_checkpoint_unreadable', 'The index and checkpoints disagree; no replacement was created.')
    return data


def _limits(value, count):
    try:
        return contract.limits(value, count)
    except ValueError:
        _refuse('invalid_experiment_limits', 'Use bounded case, attempt, time and cost caps and stop/continue failure policy.', 422)


def _plan(record):
    cases = []
    raw = [{k: c[k] for k in ('id', 'label', 'changes') if k in c} for c in record['cases']]
    for case in _validate_cases(raw):
        cases.append({k: copy.deepcopy(case[k]) for k in ('id', 'label', 'changes') if k in case})
    return {'title': record['title'], 'domain': record['domain'],
            'baseline_run_id': record.get('baseline_run_id'),
            'cases': cases, 'limits': _limits(record.get('limits'), len(cases))}


def _digest(plan):
    try:
        blob = json.dumps(plan, sort_keys=True, allow_nan=False).encode()
        if len(blob) > MAX_RECORD_BYTES // 2:
            raise ValueError()
        return hashlib.sha256(blob).hexdigest()
    except (TypeError, ValueError, RecursionError):
        _refuse('invalid_experiment', 'The plan must contain bounded finite JSON values.', 422)


def _read_record(root, experiment_id, project, owner):
    if not _valid_id(experiment_id):
        _refuse('experiment_not_found', 'Experiment not found in this project.', 404)
    try:
        record = _read_json(root / 'experiments' / (experiment_id + '.json'))
    except FileNotFoundError:
        _refuse('experiment_not_found', 'Experiment not found in this project.', 404)
    if record.get('owner_id') != owner or record.get('project_id') != project:
        _refuse('experiment_not_found', 'Experiment not found in this project.', 404)
    try:
        fields = {'version', 'experiment_id', 'project_id', 'owner_id', 'request_id',
                  'title', 'domain', 'baseline_run_id', 'cases', 'limits', 'job_ids',
                  'status', 'created_at', 'updated_at'}
        if record.get('version') == contract.VERSION:
            fields |= {'plan_sha256', 'preflight', 'operations'}
        if set(record) != fields:
            raise ValueError()
        operations = record.get('operations', [])
        if operations:
            reference = {'kind': 'single-network-case-ledger', 'version': 1,
                         'preflight_sha256': _digest(record.get('preflight'))}
            if (record.get('version') != contract.VERSION or record.get('domain') != 'network'
                    or len(record.get('cases', [])) != 1 or not isinstance(operations, list)
                    or len(operations) != 1 or _digest(operations[0]) != _digest(reference)):
                raise ValueError()
        if (type(record.get('version')) is not int or record.get('version') not in (1, contract.VERSION)
                or record['experiment_id'] != experiment_id or record['status'] != 'planned'
                or record.get('job_ids')
                or not isinstance(record.get('job_ids', []), list)
                or not isinstance(record.get('operations', []), list)
                or not isinstance(record['title'], str) or not 1 <= len(record['title']) <= 120
                or record['domain'] not in DOMAINS
                or (record.get('baseline_run_id') is not None and
                    (not isinstance(record['baseline_run_id'], str) or not 1 <= len(record['baseline_run_id']) <= 64))
                or not isinstance(record['request_id'], str) or not 1 <= len(record['request_id']) <= 64
                or not isinstance(record['cases'], list) or not 1 <= len(record['cases']) <= MAX_CASES
                or any(c['status'] != 'planned' or c.get('job_id') or c.get('run_id') or c.get('error')
                       or set(c) - {'id', 'label', 'changes', 'status', 'job_id', 'run_id', 'error'}
                       for c in record['cases'])):
            raise ValueError()
        bare_cases = [{k: c[k] for k in ('id', 'label', 'changes') if k in c} for c in record['cases']]
        _validate_cases(bare_cases)
        if record['version'] == contract.VERSION and record['plan_sha256'] != _digest(_plan(record)):
            raise ValueError()
        if record.get('preflight') is not None:
            preflight = record['preflight']
            if (not isinstance(preflight, dict) or set(preflight) != {
                    'version', 'status', 'project_id', 'input_fingerprint',
                    'plan_sha256', 'baseline', 'prepared_at', 'case_checks', 'exact_input_fingerprint'}
                    or preflight['version'] != contract.PREFLIGHT_VERSION
                    or preflight['status'] != 'validated' or preflight['project_id'] != project
                    or preflight['plan_sha256'] != _digest(_plan(record))
                    or not isinstance(preflight['input_fingerprint'], str)
                    or not re.fullmatch(r'[0-9a-f]{64}', preflight['input_fingerprint'])
                    or not isinstance(preflight['exact_input_fingerprint'], str)
                    or not re.fullmatch(r'[0-9a-f]{64}', preflight['exact_input_fingerprint'])
                    or not isinstance(preflight['baseline'], dict)
                    or not isinstance(preflight['case_checks'], list)
                    or len(preflight['case_checks']) != len(record['cases'])):
                raise ValueError()
            expected_baseline = {'kind', 'input_fingerprint'}
            if record.get('baseline_run_id'):
                expected_baseline |= {'run_id', 'mode', 'evidence_sha256'}
            baseline = preflight['baseline']
            if (set(baseline) != expected_baseline
                    or baseline['input_fingerprint'] != preflight['input_fingerprint']
                    or baseline['kind'] != ('recorded_run' if record.get('baseline_run_id') else 'current_inputs')
                    or baseline.get('run_id') != record.get('baseline_run_id')):
                raise ValueError()
            for check, case in zip(preflight['case_checks'], record['cases']):
                if (not isinstance(check, dict) or set(check) != {'case_id', 'input_fingerprint', 'exact_input_fingerprint', 'warning_count'}
                        or check['case_id'] != case['id']
                        or not isinstance(check['input_fingerprint'], str)
                        or not re.fullmatch(r'[0-9a-f]{64}', check['input_fingerprint'])
                        or not isinstance(check['exact_input_fingerprint'], str)
                        or not re.fullmatch(r'[0-9a-f]{64}', check['exact_input_fingerprint'])
                        or type(check['warning_count']) is not int or check['warning_count'] < 0):
                    raise ValueError()
    except (ValueError, TypeError, KeyError, HTTPException):
        _refuse('experiment_checkpoint_unreadable', 'The experiment has invalid or uncertain state; no operation was retried.')
    return record



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
        'execution_available': False,
        'version': record['version'],
        'preflight': _public_preflight(record),
        'job_ids': list(record.get('job_ids') or []),
        'created_at': record.get('created_at'),
        'updated_at': record.get('updated_at'),
        'note': (
            'Persisted only — this record has no case-execution receipts. '
            'Preflight validates current inputs without starting operations. '
            'Case advancement still needs an executor and ordinary harness approvals.'
        ),
    }
    if detail:
        out['cases'] = copy.deepcopy(record.get('cases') or [])
        out['limits'] = copy.deepcopy(record.get('limits') or {})
        out['request_id'] = record.get('request_id')
    from harness.experiment_execution import describe
    execution = describe(record)
    if execution is not None:
        out.update(status=execution['status'], executed=execution['executed'],
            execution_available=True, execution=execution, note=execution['note'],
            job_ids=[execution['job_id']] if execution['job_id'] else [])
        if detail:
            out['cases'][0].update(status=execution['status'],
                job_id=execution['job_id'], run_id=execution['run_id'])
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
    if not isinstance(domain, str) or domain not in DOMAINS:
        _refuse('invalid_experiment_domain', f'domain must be one of {sorted(DOMAINS)}.', 422)
    if not isinstance(request_id, str) or not request_id.strip() or not 1 <= len(request_id) <= 64:
        _refuse('invalid_experiment', 'request_id must be 1–64 characters.', 422)
    if baseline_run_id is not None and (
        not isinstance(baseline_run_id, str) or not 1 <= len(baseline_run_id) <= 64
    ):
        _refuse('invalid_experiment', 'baseline_run_id must be a short run id when set.', 422)
    validated = _validate_cases(cases)
    bounded_limits = _limits(limits, len(validated))
    plan = {'title': title.strip(), 'domain': domain, 'baseline_run_id': baseline_run_id,
            'cases': [{k: c[k] for k in ('id', 'label', 'changes') if k in c} for c in validated],
            'limits': bounded_limits}
    digest = _digest(plan)
    root, project_id, owner = _authority()
    request_key = hashlib.sha256(f'{project_id}:{owner}:{request_id}'.encode()).hexdigest()
    with network_guard(), _LOCK, _transaction(root):
        if _authority() != (root, project_id, owner):
            _refuse('experiment_context_changed', 'Project or ownership changed.')
        index = _load_index(root)
        existing_id = index['by_request'].get(request_key) or index['by_request'].get(request_id)
        if existing_id:
            try:
                record = _read_record(root, existing_id, project_id, owner)
            except HTTPException as error:
                if error.status_code != 404:
                    raise
                # The old index was project-wide. Another owner's request ID
                # does not grant access or deduplicate this owner's request.
                if index['by_request'].get(request_key):
                    _refuse('experiment_checkpoint_unreadable', 'An indexed experiment is unavailable; no replacement was created.')
                record = None
            if record is not None:
                if record['request_id'] != request_id:
                    _refuse('experiment_checkpoint_unreadable', 'The indexed request does not match its checkpoint.')
                if _digest(_plan(record)) != digest:
                    _refuse('experiment_request_conflict', 'This request_id already identifies a different plan.')
                return _view(record, detail=True)
        if len(index['order']) >= MAX_RECORDS:
            _refuse('experiment_storage_full', 'The project experiment store is full; no plan was discarded.')
        experiment_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f'{project_id}:{owner}:{request_id}'))
        path = root / 'experiments' / (experiment_id + '.json')
        if path.exists():
            _refuse('experiment_checkpoint_unreadable', 'An unindexed checkpoint needs reconciliation.')
        now = _now()
        record = {'version': contract.VERSION, 'experiment_id': experiment_id,
                  'project_id': project_id, 'owner_id': owner, 'request_id': request_id,
                  **plan, 'cases': validated, 'plan_sha256': digest,
                  'preflight': None, 'operations': [], 'job_ids': [], 'status': 'planned',
                  'created_at': now, 'updated_at': now}
        _save(path, record)
        index['by_request'][request_key] = experiment_id
        index['order'].insert(0, experiment_id)
        _save(_index_path(root), index)
        return _view(record, detail=True)



def get_experiment(experiment_id: str) -> dict:
    root, project, owner = _authority()
    with network_guard(), _LOCK, _transaction(root):
        return _view(_read_record(root, experiment_id, project, owner), detail=True)


def list_experiments(limit: int = 10) -> dict:
    if type(limit) is not int or not 1 <= limit <= 20:
        _refuse('invalid_experiment_limit', 'limit must be 1–20.', 422)
    root, project, owner = _authority()
    with network_guard(), _LOCK, _transaction(root):
        index = _load_index(root)
        items = []
        count = 0
        for experiment_id in index['order']:
            try:
                record = _read_record(root, experiment_id, project, owner)
            except HTTPException as error:
                if error.status_code == 404:
                    continue
                raise
            count += 1
            if len(items) < limit:
                items.append(_view(record))
        return {'items': items, 'total_count': count, 'limit': limit, 'has_more': count > len(items),
                'note': 'Most recent first. Plans are not executed by listing.'}


def _public_preflight(record):
    preflight = record.get('preflight')
    if not preflight:
        return {'status': 'not_prepared'}
    try:
        ctx = PyPSAService.get_active_context()
        from services.workflow_tools import _network_input_fingerprint
        if (str(ctx.project_uuid) != record['project_id'] or ctx.network is None
                or preflight['input_fingerprint'] != _network_input_fingerprint(ctx)
                or preflight['exact_input_fingerprint'] != _network_input_fingerprint(ctx, exact=True)):
            return {'status': 'stale'}
        if record.get('baseline_run_id'):
            from services.run_evidence import get_run_evidence
            evidence = get_run_evidence(record['baseline_run_id'])
            if (evidence['status'] != 'completed' or not evidence['evidence_available']
                    or evidence['fingerprint'] != preflight['input_fingerprint']
                    or _digest(evidence) != preflight['baseline']['evidence_sha256']):
                return {'status': 'stale'}
    except (KeyError, TypeError, AttributeError, RuntimeError, HTTPException):
        return {'status': 'stale'}
    return copy.deepcopy(preflight)


def prepare_experiment(experiment_id: str) -> dict:
    """Validate current network inputs and authorized run references; never execute."""
    from services import assistant_tools, chat_tools, run_evidence, workflow_tools

    with network_guard():
        root, project, owner = _authority()
        with _LOCK, _transaction(root):
            _load_index(root)
            record = _read_record(root, experiment_id, project, owner)
            from harness.experiment_execution import _path
            if record.get('operations') or _path(root, experiment_id).exists():
                _refuse('experiment_already_armed', 'Inspect the existing execution ledger; preparation cannot replace its frozen inputs.')
            if record['domain'] != 'network':
                _refuse('experiment_adapter_unavailable', 'This domain has no execution preflight adapter yet.')
            ctx = PyPSAService.get_active_context()
            if str(ctx.project_uuid) != project or ctx.network is None:
                _refuse('experiment_context_changed', 'The active network does not match this experiment.')
            actual_project, _, actual_owner = assistant_tools._project()
            if str(actual_project.id) != project or actual_owner != owner:
                _refuse('experiment_context_changed', 'Project or ownership changed.')
            assistant_tools._idle(ctx, actual_project)
            plan = _plan(record)
            fingerprint = workflow_tools._network_input_fingerprint(ctx)
            exact_fingerprint = workflow_tools._network_input_fingerprint(ctx, exact=True)
            baseline = {'kind': 'current_inputs', 'input_fingerprint': fingerprint}
            if plan['baseline_run_id']:
                evidence = run_evidence.get_run_evidence(plan['baseline_run_id'])
                if (evidence['status'] != 'completed' or not evidence['evidence_available']
                        or evidence['fingerprint'] != fingerprint):
                    _refuse('experiment_baseline_unavailable', 'The baseline must be completed, intact and match current inputs; historical inputs were not restored.')
                baseline = {'kind': 'recorded_run', 'run_id': evidence['run_id'],
                            'input_fingerprint': evidence['fingerprint'], 'mode': evidence['mode'],
                            'evidence_sha256': _digest(evidence)}
            case_checks = []
            for case in plan['cases']:
                ordinary = []
                for change in case['changes']:
                    component, _, rest = change['path'].partition('.')
                    name, _, attribute = rest.rpartition('.')
                    if component not in workflow_tools._ATTRIBUTES or attribute not in workflow_tools._ATTRIBUTES[component]:
                        _refuse('experiment_target_unavailable', 'The target is outside the supported static engineering input schema.', 422)
                    frame = ctx.network.components[component]
                    if name not in frame.static.index:
                        _refuse('experiment_target_unavailable', 'The target asset does not exist.', 422)
                    expected_type = 'boolean' if attribute.endswith('_extendable') else 'number'
                    if change['value_type'] != expected_type:
                        _refuse('experiment_target_unavailable', 'The declared value type does not match the input schema.', 422)
                    unit = str(frame.defaults.at[attribute, 'unit'])
                    if 'unit' in change and change['unit'] != unit:
                        _refuse('experiment_unit_mismatch', 'Use the exact schema unit; values are not silently converted.', 422)
                    try:
                        checked_value = chat_tools._validated_update_payload(component, name, {attribute: change['value']})
                    except ValidationError:
                        _refuse('experiment_target_invalid', 'The value fails the actual component input schema.', 422)
                    if checked_value.get(attribute) != change['value']:
                        _refuse('experiment_target_invalid', 'Schema coercion would change the requested value.', 422)
                    ordinary.append({'component_class': component, 'names': [name],
                                     'attribute': attribute, 'value': change['value']})
                candidate, _, validation = assistant_tools._candidate(ctx, assistant_tools._changes(ordinary))
                for change in ordinary:
                    actual = candidate.network.components[change['component_class']].static.at[
                        change['names'][0], change['attribute']]
                    actual = actual.item() if hasattr(actual, 'item') else actual
                    if actual != change['value']:
                        _refuse('experiment_target_invalid', 'The candidate does not preserve the exact requested value.', 422)
                case_checks.append({'case_id': case['id'],
                    'input_fingerprint': workflow_tools._network_input_fingerprint(candidate),
                    'exact_input_fingerprint': workflow_tools._network_input_fingerprint(candidate, exact=True),
                    'warning_count': validation.get('warnings', 0)})
            if (_authority() != (root, project, owner) or fingerprint != workflow_tools._network_input_fingerprint(ctx)
                    or exact_fingerprint != workflow_tools._network_input_fingerprint(ctx, exact=True)):
                _refuse('experiment_context_changed', 'Inputs or authority changed during preflight.')
            record.update(version=contract.VERSION, limits=plan['limits'],
                plan_sha256=_digest(plan), operations=[],
                preflight={'version': contract.PREFLIGHT_VERSION, 'status': 'validated',
                           'project_id': project, 'input_fingerprint': fingerprint,
                           'exact_input_fingerprint': exact_fingerprint,
                           'plan_sha256': _digest(plan), 'baseline': baseline,
                           'case_checks': case_checks, 'prepared_at': _now()},
                updated_at=_now())
            _save(root / 'experiments' / (experiment_id + '.json'), record)
            return _view(record, detail=True)



def _reset_for_tests() -> None:
    with _LOCK:
        pass
