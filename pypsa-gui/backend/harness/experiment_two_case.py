"""
Two sequential ordinary case admissions with shared permanent bounds.

No worker loop: the caller must submit each offered call through the harness.
Case projections reuse the single-case oracle and fenced native receipt protocol.
"""
from contextlib import nullcontext
import copy
import math

from harness import experiment_execution as engine
from harness.durable import network_guard
from services import experiments as ex


def select_case(record, case_id):
    if (len(record['cases']) != 2 or not isinstance(case_id, str)
            or case_id not in {c['id'] for c in record['cases']}):
        ex._refuse('invalid_experiment_comparison', 'Select an exact case from a two-case plan.', 422)
    index = next(i for i, c in enumerate(record['cases']) if c['id'] == case_id)
    out = copy.deepcopy(record)
    out['cases'] = [out['cases'][index]]
    if out.get('preflight'):
        out['preflight']['case_checks'] = [out['preflight']['case_checks'][index]]
    out.update(_parent_record=record, _case_id=case_id)
    return out


def _template(record):
    return dict(version=2, **{k: record[k] for k in ('experiment_id', 'project_id', 'owner_id', 'plan_sha256')},
        preflight_sha256=ex._digest(record['preflight']), started_at=0, cancelled=False, cases={})


def _read(root, record):
    path = engine._path(root, record['experiment_id'])
    if not path.exists() and not path.is_symlink():
        if record.get('operations'):
            ex._refuse('experiment_checkpoint_unreadable', 'The shared execution ledger is missing; it was not replaced.')
        return None
    if not record.get('operations'):
        ex._refuse('experiment_checkpoint_unreadable', 'The shared execution ledger has no plan reservation.')
    data = ex._read_json(path)
    try:
        template = _template(record)
        if (set(data) != set(template) or type(data['version']) is not int
                or any(data[k] != template[k] for k in template if k not in {'started_at', 'cancelled', 'cases'})
                or type(data['cancelled']) is not bool
                or isinstance(data['started_at'], bool) or not isinstance(data['started_at'], (int, float))
                or not math.isfinite(data['started_at']) or not isinstance(data['cases'], dict)
                or set(data['cases']) != {c['id'] for c in record['cases']}):
            raise ValueError()
        attempts = []
        for case in record['cases']:
            receipt = engine._validate(select_case(record, case['id']), data['cases'][case['id']])
            if receipt['started_at'] != data['started_at'] or receipt['cancelled'] != data['cancelled']:
                raise ValueError()
            attempts.extend(a['id'] for a in receipt['attempts'])
        if len(attempts) != len(set(attempts)) or len(attempts) > record['limits']['max_tool_attempts']:
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        ex._refuse('experiment_checkpoint_unreadable', 'Shared execution state is invalid; no operation was replayed.')
    return data


def read_case(root, projection):
    data = _read(root, projection['_parent_record'])
    if data is None:
        ex._refuse('experiment_checkpoint_unreadable', 'The shared case checkpoint is missing.')
    return copy.deepcopy(data['cases'][projection['_case_id']])


def save_case(root, projection, receipt):
    parent = projection['_parent_record']
    data = _read(root, parent)
    if data is None:
        ex._refuse('experiment_checkpoint_unreadable', 'The shared case checkpoint is missing.')
    data['cases'][projection['_case_id']] = engine._validate(projection, receipt)
    ex._save(engine._path(root, parent['experiment_id']), data)


def _budget(record, data):
    elapsed = engine._clock() - data['started_at']
    attempts = sum(len(c['attempts']) for c in data['cases'].values())
    return 0 <= elapsed < record['limits']['max_elapsed_seconds'] and attempts < record['limits']['max_tool_attempts']


def _views(root, record, data):
    out = []
    for case in record['cases']:
        projection = select_case(record, case['id'])
        ledger = data['cases'][case['id']]
        view = engine._view(projection, engine._observe(projection, ledger), root=root, ledger=ledger)
        view['case_id'] = case['id']
        view['case_name'] = ledger['case_name']
        # Only the parent may offer execution; individual proof controls stay explicit.
        view.pop('next_call', None)
        out.append(view)
    return out


def _selection(record, cases):
    """Uncertain/partial proof blocks every policy, including explicit continue."""
    for view in cases:
        state = view['status']
        if state == 'completed':
            continue
        if state in {'failed', 'not_started'}:
            if record['limits']['failure_policy'] == 'continue':
                continue
            return None, 'stopped'
        if state == 'ready':
            return view, 'ready'
        if state in {'submitted', 'running'}:
            return view, state
        return None, 'needs_review'
    return None, 'completed' if all(c['status'] == 'completed' for c in cases) else 'partial'


def _view(root, record, data):
    cases = _views(root, record, data)
    selected, status = _selection(record, cases)
    allowed = _budget(record, data) and not data['cancelled']
    if selected is not None and not allowed:
        status = 'cancelled' if data['cancelled'] else 'budget_stopped'
    out = dict(experiment_id=record['experiment_id'], project_id=record['project_id'],
        status=status, cancelled=data['cancelled'], execution_available=True,
        cases=cases, job_ids=[c['job_id'] for c in cases if c['job_id']],
        executed=True if any(c['executed'] is True for c in cases) else
            None if any(c['executed'] is None for c in cases) else False,
        result_available=all(c['result_available'] for c in cases),
        attempts_used=sum(len(c['attempts']) for c in data['cases'].values()),
        started_at=data['started_at'], failure_policy=record['limits']['failure_policy'],
        limits=copy.deepcopy(record['limits']),
        note='Each next_call needs ordinary harness admission and its own confirmation. '
            'Cases share permanent attempt/time caps. Cancel stops admissions, not native jobs. '
            'Uncertain/partial effects block advancement. No checked-answer certification is implied.')
    if selected is not None and allowed:
        projection = select_case(record, selected['case_id'])
        if status == 'ready':
            out['next_call'] = dict(tool='run_sensitivity_sweep', args=engine._submission(projection))
        else:
            remaining = record['limits']['max_elapsed_seconds'] - (engine._clock() - data['started_at'])
            out['next_call'] = dict(tool='wait_for_job', args=dict(job_id=selected['job_id'], timeout_seconds=min(20, max(0, remaining))))
    return out


def describe(root, record):
    data = _read(root, record)
    return None if data is None else _view(root, record, data)


def advance(root, record, session):
    if record.get('version') != 2 or ex._public_preflight(record).get('status') != 'validated':
        ex._refuse('experiment_preflight_required', 'Prepare the unchanged current network before advancing.')
    data = _read(root, record)
    if data is None:
        from services import chat_tools, dirty_state, project_registry, workflow_tools as wt
        with chat_tools._acting() as (db, user):
            saved = wt._load_context(project_registry.resolve_project(db, user, record['project_id']))
        if (dirty_state.is_dirty(ex.PyPSAService.get_active_context())
                or wt._network_input_fingerprint(saved, exact=True) != record['preflight']['exact_input_fingerprint']):
            ex._refuse('experiment_baseline_not_saved', 'Save the exact baseline before arming cases.')
        data = _template(record)
        data['started_at'] = engine._clock()
        record['operations'] = [dict(kind='two-network-case-ledger', version=2,
                                    preflight_sha256=ex._digest(record['preflight']))]
        for case in record['cases']:
            data['cases'][case['id']] = engine._new(select_case(record, case['id']))
            data['cases'][case['id']]['started_at'] = data['started_at']
        # Reserve the parent before creating any receipt; no missing-ledger reset.
        ex._save(root / 'experiments' / (record['experiment_id'] + '.json'), record)
        ex._save(engine._path(root, record['experiment_id']), data)
    session.experiment_id, session.experiment_project_id = record['experiment_id'], record['project_id']
    return _view(root, record, data)


def refresh(root, record):
    data = _read(root, record)
    if data is None:
        ex._refuse('experiment_not_armed', 'Use advance_experiment before observing case execution.')
    for case in record['cases']:
        key = case['id']
        data['cases'][key] = engine._observe(select_case(record, key), data['cases'][key])
    ex._save(engine._path(root, record['experiment_id']), data)
    return _view(root, record, data)


def cancel(root, record):
    data = _read(root, record)
    if data is None:
        ex._refuse('experiment_not_armed', 'This experiment has no advancement to cancel.')
    data['cancelled'] = True
    for receipt in data['cases'].values():
        receipt['cancelled'] = True
    ex._save(engine._path(root, record['experiment_id']), data)
    return _view(root, record, data)


def check_admission(root, projection, tool, args):
    record = projection['_parent_record']
    data = _read(root, record)
    if data is None:
        ex._refuse('experiment_checkpoint_unreadable', 'The shared execution checkpoint is missing.')
    if not _budget(record, data):
        ex._refuse('experiment_budget_exhausted', 'The shared attempt/time cap was reached; receipts remain inspectable.')
    selected, status = _selection(record, _views(root, record, data))
    if data['cancelled'] or selected is None or selected['case_id'] != projection['_case_id']:
        ex._refuse('experiment_effect_already_reserved', 'This case is not eligible; inspect the shared plan and its receipts.')
    expected = engine._submission(projection) if tool == 'run_sensitivity_sweep' else None
    if tool == 'run_sensitivity_sweep' and (status != 'ready' or ex._digest(args) != ex._digest(expected)):
        ex._refuse('experiment_call_mismatch', 'Use the exact currently offered case call.')


def invoke(session, root, record, tool, args, handler):
    # Identify from exact planned calls / retained job IDs, never caller verdicts.
    if tool == 'run_sensitivity_sweep':
        # Derive the candidate before the network lock so a live native fence
        # rejects promptly. Eligibility is rechecked inside the reservation.
        keys = [c['id'] for c in record['cases'] if
                ex._digest(args) == ex._digest(engine._submission(select_case(record, c['id'])))]
    else:
        with network_guard(), ex._LOCK, ex._transaction(root):
            data = _read(root, record)
            if data is None:
                ex._refuse('experiment_checkpoint_unreadable', 'The shared execution checkpoint is missing.')
            keys = [c['id'] for c in record['cases'] if data['cases'][c['id']]['job_id'] is not None
                    and data['cases'][c['id']]['job_id'] == args.get('job_id')]
    if not keys:
        if tool == 'wait_for_job':
            return engine._ordinary(session, args, handler)
        ex._refuse('experiment_call_mismatch', 'Use the exact currently offered case call.')
    from harness.experiment_reconciliation import submission_guard
    with submission_guard(record['experiment_id'], keys[0]) if tool == 'run_sensitivity_sweep' else nullcontext():
        with network_guard() if tool == 'run_sensitivity_sweep' else nullcontext():
            return engine._invoke(session, record['experiment_id'], tool, args, handler, case_id=keys[0])
