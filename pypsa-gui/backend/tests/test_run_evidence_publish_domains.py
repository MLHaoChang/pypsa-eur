"""Issue 06 — queue / study / report completions publish listable runs."""
from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path

import pytest

from services import run_evidence as re_


PROJECT = '44444444-4444-4444-4444-444444444444'
OWNER = 'owner-domain'


@pytest.fixture
def evidence_root(tmp_path, monkeypatch):
    root = tmp_path / PROJECT
    root.mkdir()
    monkeypatch.setattr(re_, '_authority', lambda: (root, PROJECT, OWNER))
    return root


def test_map_worker_status_covers_family_labels():
    assert re_.map_worker_status('done') == 'completed'
    assert re_.map_worker_status('met') == 'completed'
    assert re_.map_worker_status('completed') == 'completed'
    assert re_.map_worker_status('failed') == 'failed'
    assert re_.map_worker_status('budget_exhausted') == 'failed'
    assert re_.map_worker_status('refused') == 'failed'
    assert re_.map_worker_status('unreachable') == 'failed'
    assert re_.map_worker_status('aborted') == 'interrupted'
    assert re_.map_worker_status('running') is None


def test_queue_job_claim_and_finish_publish_run(evidence_root, monkeypatch):
    from services.solve_queue import SolveJob, KIND_SOLVE

    job = SolveJob(
        id=uuid.uuid4(),
        project_id='Demo',
        project_key=f'org:{PROJECT}',
        storage_dir=str(evidence_root),
        kind=KIND_SOLVE,
        enqueued_by_user_id=OWNER,
    )
    token = re_.start_queue_run(job)
    assert token is not None
    items = re_.list_project_runs()['items']
    assert len(items) == 1
    assert items[0]['status'] == 'running'
    assert items[0]['kind'] == 'solve_queue'

    job.status = 'completed'
    job.objective = 42.0
    job.solve_time = 1.5
    job.condition = 'optimal'
    out = re_.publish_queue_run(job)
    assert out is not None and out['status'] == 'completed'
    detail = re_.get_run_evidence(token['run_id'])
    assert detail['metrics']['objective']['value'] == 42.0


def test_gridspine_queue_kind_is_recorded(evidence_root):
    from services.solve_queue import SolveJob, KIND_GRIDSPINE

    job = SolveJob(
        id=uuid.uuid4(),
        project_id='Demo',
        project_key=f'org:{PROJECT}',
        storage_dir=str(evidence_root),
        kind=KIND_GRIDSPINE,
        enqueued_by_user_id=OWNER,
    )
    re_.start_queue_run(job)
    job.status = 'aborted'
    re_.publish_queue_run(job)
    items = re_.list_project_runs()['items']
    assert items[0]['kind'] == 'gridspine'
    assert items[0]['status'] == 'interrupted'


def test_study_watcher_publishes_when_record_finishes(evidence_root, monkeypatch):
    monkeypatch.setattr(re_, '_authority', lambda: (evidence_root, PROJECT, OWNER))
    record = {'status': 'running', 'rows': [], 'error': None}
    re_.watch_study_run('fmea_sweep', record)
    assert record.get('_run_evidence')
    run_id = record['_run_evidence']['run_id']
    assert re_.list_project_runs()['items'][0]['status'] == 'running'

    def _finish():
        time.sleep(0.2)
        record['status'] = 'done'
        record['rows'] = [{'id': 1}]

    threading.Thread(target=_finish, daemon=True).start()
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        items = re_.list_project_runs()['items']
        if items and items[0]['status'] == 'completed':
            break
        time.sleep(0.05)
    else:
        raise AssertionError(re_.list_project_runs())
    detail = re_.get_run_evidence(run_id)
    assert detail['kind'] == 'fmea_sweep'
    assert detail['status'] == 'completed'


def test_unreachable_study_publishes_instead_of_staying_running(evidence_root, monkeypatch):
    """A coupling/margin 'unreachable' verdict is terminal, not still running."""
    monkeypatch.setattr(re_, '_authority', lambda: (evidence_root, PROJECT, OWNER))
    record = {'status': 'unreachable', 'error': None}
    re_.watch_study_run('coupling_loop', record)
    run_id = record['_run_evidence']['run_id']
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        items = re_.list_project_runs()['items']
        if items and items[0]['status'] == 'failed':
            break
        time.sleep(0.05)
    else:
        raise AssertionError(re_.list_project_runs())
    detail = re_.get_run_evidence(run_id)
    assert detail['status'] == 'failed'
    assert 'unreachable' in detail['error']['message']


def test_study_failed_publishes_failed_run(evidence_root, monkeypatch):
    monkeypatch.setattr(re_, '_authority', lambda: (evidence_root, PROJECT, OWNER))
    record = {'status': 'running', 'error': None}
    re_.start_study_run('mc', record)
    record['status'] = 'failed'
    record['error'] = 'budget'
    out = re_.publish_study_run('mc', record)
    assert out['status'] == 'failed'
    detail = re_.get_run_evidence(record['_run_evidence']['run_id'])
    assert detail['error']['error_kind'] == 'study_failed'


def test_report_start_and_done_publish(evidence_root, monkeypatch):
    monkeypatch.setattr(re_, '_authority', lambda: (evidence_root, PROJECT, OWNER))
    record = {
        'status': 'running',
        'report_id': 'rep-domain-1',
        'mode': 'generate',
        'version': None,
        'error': None,
    }
    token = re_.start_report_run(record)
    assert token is not None
    assert re_.list_project_runs()['items'][0]['status'] == 'running'
    record['status'] = 'done'
    record['version'] = 1
    out = re_.publish_report_run(record)
    assert out['status'] == 'completed'
    detail = re_.get_run_evidence(token['run_id'])
    assert detail['kind'] == 'report'
    section = re_.get_run_evidence(token['run_id'], section='summary')
    assert section['data']['report_id'] == 'rep-domain-1'


def test_queue_finish_hook_via_solve_queue(evidence_root, monkeypatch):
    """_finish on the singleton publishes without going through chat."""
    from services import solve_queue as sq

    job = sq.SolveJob(
        id=uuid.uuid4(),
        project_id='Demo',
        project_key=f'org:{PROJECT}',
        storage_dir=str(evidence_root),
        kind=sq.KIND_SOLVE,
        enqueued_by_user_id=OWNER,
    )
    re_.start_queue_run(job)
    sq.solve_queue._finish(job, 'failed', error='boom')
    items = re_.list_project_runs()['items']
    assert items[0]['status'] == 'failed'


def test_study_watcher_publishes_in_the_callers_context(monkeypatch):
    """The watcher resolves the run's authority from the REQUEST's context.

    Started as a bare thread, it had no request-scoped context, so
    `_authority()` fell through to `PyPSAService._ensure_active()`, whose
    unlocked self-heal could overwrite a network another thread had just
    installed in the process foreground with an empty one (the intermittent
    `qa_eh_reference_design` failure: the next study saw no import Links).
    """
    import pypsa

    from services.pypsa_service import PyPSAService, ProjectContext

    seen = []
    done = threading.Event()

    def _publish(study_key, record):
        seen.append(PyPSAService._scoped())
        done.set()

    # A started run (a token); with none, no watcher starts at all.
    monkeypatch.setattr(re_, 'start_study_run', lambda key, record: {'run_id': 'stub'})
    monkeypatch.setattr(re_, 'publish_study_run', _publish)
    ctx = ProjectContext(network=pypsa.Network())
    token = PyPSAService._request_ctx.set(ctx)
    try:
        record = {'status': 'running'}
        re_.watch_study_run('eh_study', record)
    finally:
        PyPSAService._request_ctx.reset(token)
    record['status'] = 'done'
    assert done.wait(5.0), 'the watcher never published'
    assert seen == [ctx]


# ── the authority is bound when the watch starts, never resolved late ──────
#
# A watcher used to resolve `_authority()` again when its record turned
# terminal if the start had no marker. Whatever project was active THEN got
# the run: in tests, the next test's monkeypatched root (an `ad-mc-*` run from
# `test_adequacy_mc_endpoint` landing in the foreground test's project); in the
# app, the project a user had switched to while the study ran.

def _refuse_authority():
    from fastapi import HTTPException

    raise HTTPException(409, {'error_kind': 'run_project_required', 'message': 'x'})


def _watchers(key):
    return [t for t in threading.enumerate() if t.name == f'run-evidence-{key}' and t.is_alive()]


def test_a_study_with_no_authority_at_start_starts_no_watcher(tmp_path, monkeypatch):
    monkeypatch.setattr(re_, '_authority', _refuse_authority)
    record = {'status': 'running'}
    re_.watch_study_run('margin_loop', record)
    assert '_run_evidence' not in record
    assert _watchers('margin_loop') == []

    # Even when another project is active by the time the study finishes.
    later = tmp_path / 'later'
    later.mkdir()
    monkeypatch.setattr(re_, '_authority', lambda: (later, PROJECT, OWNER))
    record['status'] = 'done'
    assert re_.publish_study_run('margin_loop', record) is None
    assert not (later / 'run_evidence').exists()


def test_a_watcher_publishes_into_the_project_bound_at_start(tmp_path, monkeypatch):
    first, later = tmp_path / 'first', tmp_path / 'later'
    first.mkdir()
    later.mkdir()
    monkeypatch.setattr(re_, '_authority', lambda: (first, PROJECT, OWNER))
    record = {'status': 'running'}
    re_.watch_study_run('mc', record)
    run_id = record['_run_evidence']['run_id']

    monkeypatch.setattr(re_, '_authority', lambda: (later, PROJECT, OWNER))
    record['status'] = 'done'
    deadline = time.monotonic() + 5.0
    while _watchers('mc') and time.monotonic() < deadline:
        time.sleep(0.05)
    assert _watchers('mc') == []
    assert (first / 'run_evidence' / run_id / 'manifest.json').is_file()
    assert not (later / 'run_evidence').exists()


def test_a_report_without_a_start_marker_is_not_published_late(tmp_path, monkeypatch):
    """The report path had the same late fallback."""
    later = tmp_path / 'later'
    later.mkdir()
    monkeypatch.setattr(re_, '_authority', lambda: (later, PROJECT, OWNER))
    record = {'status': 'done', 'report_id': 'rep-late', 'mode': 'generate', 'version': 1}
    assert re_.publish_report_run(record) is None
    assert not (later / 'run_evidence').exists()
