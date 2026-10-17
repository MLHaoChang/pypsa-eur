"""Fenced native submission proof; explicit read-back, never execution/retry."""
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
import copy
import math
import os
import re
import time

from harness.durable import network_guard, transaction
from services import experiments as ex

_CURRENT = ContextVar('experiment_native_submission', default=None)
_PHASES = ('reserved', 'effect_started', 'child_created', 'child_saved', 'queue_started', 'job_recorded')
_OUTCOMES = {'no_effects_verified', 'partial_effects', 'receipt_recovered', 'unresolved'}
_REFUSALS = {
    'running': {'error_kind': 'experiment_worker_running',
                'message': 'The native submission is still active; no reconciliation occurred.'},
    'stale': {'error_kind': 'experiment_reconciliation_stale',
              'message': 'The receipt changed; inspect it and confirm the new request.'},
    'unavailable': {'error_kind': 'experiment_reconciliation_unavailable',
                    'message': 'This submission has no fenced native proof; its reservation is retained.'},
}


def _refuse(reason):
    raise ex.HTTPException(409, dict(_REFUSALS[reason]))


def _save_proof(path, data):
    ex._save(path, data)
    if os.name != 'nt':
        fd = os.open(path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _paths(root, record):
    from harness.experiment_execution import _path
    base = _path(root, record['experiment_id']).parent
    directory = base / 'submission_receipts'
    lease = base / 'submission_leases' / record['experiment_id']
    for path in (directory, lease.parent, lease, lease / '.owner.lock'):
        if path.is_symlink():
            ex._refuse('experiment_checkpoint_unreadable', 'Submission proof contains an unsafe link.')
    key = record['experiment_id'] + ('-' + record['_case_id'] if '_case_id' in record else '')
    return directory / (key + '.json'), lease


@contextmanager
def _lease(root, record):
    _, directory = _paths(root, record)
    with ExitStack() as stack:
        try:
            stack.enter_context(transaction(directory, timeout=0))
        except TimeoutError:
            _refuse('running')
        except OSError:
            ex._refuse('experiment_storage_unavailable', 'Submission proof is unavailable; no operation was retried.')
        yield


@contextmanager
def submission_guard(experiment_id, case_id=None):
    from harness.experiment_execution import _load
    root, record = _load(experiment_id, case_id)
    with _lease(root, record):
        current = dict(root=root, record=record, attempt_id=None)
        token = _CURRENT.set(current)
        try:
            yield
        finally:
            try:
                if current['attempt_id'] is not None:
                    from harness.experiment_execution import _read as read_ledger
                    with ex._LOCK, ex._transaction(root):
                        proof = _read(root, record, read_ledger(root, record))
                        if proof is not None and proof['attempt_id'] == current['attempt_id'] and proof['resolution'] is None:
                            proof['finished'] = True
                            _save_proof(_paths(root, record)[0], proof)
            except (OSError, ValueError, ex.HTTPException):
                pass  # Lost close proof stays unresolved; never break native work.
            finally:
                _CURRENT.reset(token)


def _template(record, attempt):
    return dict(version=1, protocol='fenced-native-sweep/1',
        **{k: record[k] for k in ('experiment_id', 'project_id', 'owner_id', 'plan_sha256')},
        preflight_sha256=ex._digest(record['preflight']), attempt_id=attempt['id'],
        phase='reserved', finished=False, case_project_id=None, job_id=None, resolution=None)


def prepare(root, record, attempt):
    current = _CURRENT.get()
    if current is None or current['root'] != root or current['record'] != record:
        ex._refuse('experiment_checkpoint_unreadable', 'Submission has no matching native fence.')
    path, _ = _paths(root, record)
    if path.exists() or path.is_symlink():
        ex._refuse('experiment_checkpoint_unreadable', 'A submission checkpoint already exists; it was not replaced.')
    _save_proof(path, _template(record, attempt))
    current['attempt_id'] = attempt['id']


def _read(root, record, ledger):
    path, _ = _paths(root, record)
    if not path.exists() and not path.is_symlink():
        return None  # Legacy/unfenced submissions acquire no invented proof.
    data = ex._read_json(path)
    try:
        attempt, = (a for a in ledger['attempts'] if a['tool'] == 'run_sensitivity_sweep')
        template = _template(record, attempt)
        if (set(data) != set(template) or type(data['version']) is not int
                or any(data[k] != template[k] for k in template
                       if k not in {'phase', 'finished', 'case_project_id', 'job_id', 'resolution'})
                or type(data['finished']) is not bool
                or data['phase'] not in _PHASES):
            raise ValueError()
        for name in ('case_project_id', 'job_id'):
            if data[name] is not None and not ex._valid_id(data[name]):
                raise ValueError()
            if ledger[name] is not None and data[name] != ledger[name]:
                raise ValueError()
        position = _PHASES.index(data['phase'])
        if ((position < 2 and data['case_project_id'] is not None)
                or (position >= 2 and data['case_project_id'] is None)
                or (position < 5 and data['job_id'] is not None)
                or (position == 5 and data['job_id'] is None)):
            raise ValueError()
        resolution = data['resolution']
        if resolution is not None:
            if (set(resolution) != {'receipt_sha256', 'ledger_sha256', 'submission_sha256', 'outcome', 'at'}
                    or resolution['outcome'] not in _OUTCOMES
                    or any(not isinstance(resolution[k], str) or not re.fullmatch('[0-9a-f]{64}', resolution[k])
                           for k in ('receipt_sha256', 'ledger_sha256', 'submission_sha256'))
                    or resolution['submission_sha256'] != ex._digest(dict(data, resolution=None))
                    or isinstance(resolution['at'], bool) or not isinstance(resolution['at'], (int, float))
                    or not math.isfinite(resolution['at'])):
                raise ValueError()
            if resolution['outcome'] == 'no_effects_verified' and (
                    data['phase'] != 'reserved' or not data['finished']
                    or data['case_project_id'] is not None or data['job_id'] is not None):
                raise ValueError()
            if resolution['outcome'] == 'receipt_recovered' and data['phase'] != 'job_recorded':
                raise ValueError()
            if resolution['outcome'] == 'partial_effects' and data['phase'] not in {'child_created', 'child_saved'}:
                raise ValueError()
    except (ValueError, TypeError, KeyError):
        ex._refuse('experiment_checkpoint_unreadable', 'Submission proof is invalid; no operation was retried.')
    return data


def _reference(ledger, proof):
    return ex._digest(dict(ledger=ledger, submission=dict(proof, resolution=None)))


def checkpoint(phase, *, case_project_id=None, job_id=None):
    """Native sweep boundary only; ordinary non-experiment sweeps are unchanged."""
    current = _CURRENT.get()
    if current is None:
        return
    from harness import experiment_execution as engine
    root, record = current['root'], current['record']
    with ex._LOCK, ex._transaction(root):
        ledger = engine._read(root, record)
        proof = _read(root, record, ledger)
        if (proof is None or proof['finished'] or proof['resolution'] is not None
                or _PHASES.index(phase) != _PHASES.index(proof['phase']) + 1):
            ex._refuse('experiment_checkpoint_unreadable', 'The native submission boundary changed.')
        proof['phase'] = phase
        if case_project_id is not None:
            if not ex._valid_id(case_project_id):
                ex._refuse('experiment_checkpoint_unreadable', 'The native child receipt is invalid.')
            proof['case_project_id'] = case_project_id
        if job_id is not None:
            if not ex._valid_id(job_id):
                ex._refuse('experiment_checkpoint_unreadable', 'The native job receipt is invalid.')
            proof['job_id'] = job_id
        if phase == 'job_recorded':
            _record_native_job(record, proof)
        _save_proof(_paths(root, record)[0], proof)


def _job_origin(record, proof):
    return dict(version=1, protocol='native-experiment-job/1',
        **{k: record[k] for k in ('experiment_id', 'project_id', 'owner_id', 'plan_sha256')},
        **{k: proof[k] for k in ('preflight_sha256', 'attempt_id', 'case_project_id', 'job_id')})


def _record_native_job(record, proof):
    """Separate native child receipt prevents substituting a later same-input job."""
    from harness.experiment_execution import _name
    from services import chat_tools, project_registry
    with chat_tools._acting() as (db, user):
        child = project_registry.resolve_project(db, user, proof['case_project_id'])
        if child.name != _name(record) or str(child.parent_project_id) != record['project_id']:
            ex._refuse('experiment_checkpoint_unreadable', 'The native child origin changed.')
        path = project_registry.project_dir(child) / 'experiment_submission.json'
        origin = _job_origin(record, proof)
        if path.exists() or path.is_symlink():
            if ex._read_json(path) != origin:
                ex._refuse('experiment_checkpoint_unreadable', 'A native job origin already exists; it was not replaced.')
        _save_proof(path, origin)


def _recover(record, ledger, proof):
    """Use owned native records, never a caller's verdict or latest-job guess."""
    from harness import experiment_execution as engine
    from services import chat_tools, project_registry, workflow_tools as wt
    from routers.solve_queue import _visible_job_or_404
    data = copy.deepcopy(ledger)
    if (proof['phase'] == 'reserved' and proof['finished']
            and data['case_project_id'] is None and data['job_id'] is None):
        return data, 'no_effects_verified'
    if proof['case_project_id'] is None:
        return data, 'unresolved'
    try:
        with chat_tools._acting() as (db, user):
            child = project_registry.resolve_project(db, user, proof['case_project_id'])
            if str(child.parent_project_id) != record['project_id'] or child.name != engine._name(record):
                raise ValueError()
            saved = wt._load_context(child)
            exact = wt._network_input_fingerprint(saved, exact=True)
            if proof['phase'] == 'child_created':
                if exact != record['preflight']['exact_input_fingerprint']:
                    raise ValueError()
            else:
                spec = ex._read_json(project_registry.project_dir(child) / 'sensitivity_case.json')
                expected = record['preflight']['case_checks'][0]
                if (exact != expected['exact_input_fingerprint']
                        or spec.get('baseline_id') != record['project_id']
                        or spec.get('baseline_fingerprint') != record['preflight']['input_fingerprint']
                        or spec.get('input_fingerprint') != expected['input_fingerprint']
                        or spec.get('case') != engine._submission(record)['cases'][0]):
                    raise ValueError()
            data['case_project_id'] = str(child.id)
            if proof['phase'] in {'child_created', 'child_saved'}:
                return data, 'partial_effects'
            if proof['job_id'] is None:
                return data, 'unresolved'  # Enqueue may have happened; never guess its job.
            origin = ex._read_json(project_registry.project_dir(child) / 'experiment_submission.json')
            if type(origin.get('version')) is not int or origin != _job_origin(record, proof):
                raise ValueError()
            job = _visible_job_or_404(db, user, proof['job_id'])
            if job.get('project_key') != project_registry.registry_key(child) or job.get('kind') != 'solve':
                raise ValueError()
            data['job_id'] = proof['job_id']
        data = engine._observe(record, data)
        return data, 'unresolved' if data['status'] == 'unavailable' else 'receipt_recovered'
    except (ValueError, KeyError, TypeError, RuntimeError, OSError, ex.HTTPException):
        return copy.deepcopy(ledger), 'unresolved'


def decorate(root, record, ledger, out):
    if not any(a['tool'] == 'run_sensitivity_sweep' for a in ledger['attempts']):
        return out
    proof = _read(root, record, ledger)
    state = dict(available=proof is not None, retry_available=False,
                 note='Original reservation is retained. Any new attempt needs a new prepared plan and ordinary approvals.')
    out['reconciliation'] = state
    if proof is None:
        state['reason'] = 'legacy_or_unfenced_submission'
        return out
    resolution = proof['resolution']
    if resolution is not None:
        state.update(outcome=resolution['outcome'], observed_at=resolution['at'], historical=True)
        if (resolution['outcome'] == 'no_effects_verified' and proof['phase'] == 'reserved'
                and proof['finished']
                and ledger['case_project_id'] is None and ledger['job_id'] is None):
            out.update(status='not_started', executed=False)
            return out
    if ledger['status'] in {'submitting', 'uncertain', 'unavailable'}:
        state['next_call'] = dict(tool='reconcile_experiment', args=dict(
            experiment_id=record['experiment_id'], receipt_sha256=_reference(ledger, proof)))
    if '_case_id' in record and 'next_call' in state:
        state['next_call']['args']['case_id'] = record['_case_id']
    return out


def reconcile_experiment(experiment_id: str, receipt_sha256: str, case_id: str | None = None) -> dict:
    from harness import experiment_execution as engine
    if _CURRENT.get() is not None:
        _refuse('running')
    root, record = engine._load(experiment_id, case_id)
    if len(record["cases"]) != 1:
        _refuse("unavailable")
    # Authorize before looking up any edit-lock holder; foreign scopes are 404.
    from services.chat_tools import _check_foreign_lock
    _check_foreign_lock('reconcile_experiment')
    with _lease(root, record), network_guard(), ex._LOCK, ex._transaction(root):
        actual_root, actual_record = engine._load(experiment_id, case_id)
        if actual_root != root or actual_record != record:
            _refuse('stale')
        ledger = engine._read(root, record)
        proof = _read(root, record, ledger) if ledger is not None else None
        if proof is None:
            _refuse('unavailable')
        reference = _reference(ledger, proof)
        previous = proof['resolution']
        replay = (previous is not None and previous['receipt_sha256'] == receipt_sha256
                  and previous['ledger_sha256'] == ex._digest(ledger))
        if receipt_sha256 != reference and not replay:
            _refuse('stale')
        if not replay:
            ledger, outcome = _recover(record, ledger, proof)
            engine._save(root, record, ledger)
            proof['resolution'] = dict(receipt_sha256=reference, ledger_sha256=ex._digest(ledger),
                                      submission_sha256=ex._digest(dict(proof, resolution=None)),
                                      outcome=outcome, at=time.time())
            _save_proof(_paths(root, record)[0], proof)
        return engine._view(record, engine._observe(record, ledger), root=root, ledger=ledger)
