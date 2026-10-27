"""Issue 06 close-out — run markers no live process will publish.

A restart (or crash) leaves ``running.json`` with no manifest: reads report it
as ``interrupted`` with ``process_exited`` and no evidence, never as running.
A session abort ends the solver monitor but not the solve, so the run is still
published once when the solve ends. Orphaned markers count toward retention.
"""
from __future__ import annotations

import json
import os
import threading
import time

import pytest
from fastapi import HTTPException

from harness import solver_bridge
from services import run_evidence as re_

OWNER = 'owner-aaa'
PROJECT = '11111111-1111-1111-1111-111111111111'


@pytest.fixture
def root(tmp_path, monkeypatch):
    root = tmp_path / PROJECT
    root.mkdir()
    monkeypatch.setattr(re_, '_authority', lambda: (root, PROJECT, OWNER))
    return root


def _start(root, run_id, started_at, **kw):
    return re_.start_run(root, PROJECT, run_id=run_id, kind='simulation',
                         started_at=started_at, mode='lopf', job_id=run_id, **kw)


def _complete(root, run_id, started_at, fingerprint='fp-1'):
    return re_.publish_run(
        root, PROJECT, run_id=run_id, kind='simulation', status='completed',
        started_at=started_at, finished_at=started_at, mode='lopf', fingerprint=fingerprint,
        metrics={'objective': {'value': 10.0, 'unit': 'EUR'}},
        sections={'summary': {'objective': {'value': 10.0, 'unit': 'EUR'}}})


def _restart(monkeypatch):
    monkeypatch.setattr(re_, '_LIVE', set())


def test_live_marker_reads_running(root):
    _start(root, 'r-live', '2026-10-01T10:00:00+00:00')
    item = re_.list_project_runs()['items'][0]
    assert item['status'] == 'running' and item['finished_at'] is None


def _snapshot(root):
    return {p: (p.read_bytes(), p.stat().st_mtime_ns)
            for p in sorted((root / 'run_evidence').rglob('*')) if p.is_file()}


def test_marker_after_restart_reads_interrupted_everywhere(root, monkeypatch):
    _complete(root, 'r-done', '2026-10-01T10:00:00+00:00')
    _start(root, 'r-orphan', '2026-10-02T10:00:00+00:00', unsaved_inputs=True)
    _restart(monkeypatch)
    before = _snapshot(root)

    listed = re_.list_project_runs()
    assert [(r['run_id'], r['status']) for r in listed['items']] == [
        ('r-orphan', 'interrupted'), ('r-done', 'completed')]
    orphan = listed['items'][0]
    assert orphan['finished_at'] is None and orphan['evidence_available'] is False
    assert orphan['unsaved_inputs'] is True
    assert re_.list_project_runs(status='running')['total_count'] == 0
    assert re_.list_project_runs(status='interrupted')['items'][0]['run_id'] == 'r-orphan'

    got = re_.get_run_evidence('r-orphan')
    assert got['status'] == 'interrupted' and got['warnings'] == ['process_exited']
    assert got['metrics'] == {} and got['sections'] == []
    with pytest.raises(HTTPException) as exc:
        re_.get_run_evidence('r-orphan', section='summary')
    assert exc.value.detail['error_kind'] == 'evidence_unavailable'
    assert exc.value.detail['run_id'] == 'r-orphan'
    assert exc.value.detail['run_status'] == 'interrupted'

    cmp = re_.compare_project_runs('r-orphan', 'r-done')
    assert cmp['comparable'] is False and cmp['deltas'] == {}
    assert {'reason': 'run_not_completed', 'role': 'run', 'run_id': 'r-orphan',
            'status': 'interrupted'} in cmp['incomparable_reasons']

    # Reads never write: every file keeps its bytes and mtime, and no manifest appears.
    assert _snapshot(root) == before
    assert not (root / 'run_evidence' / 'r-orphan' / 'manifest.json').exists()


def test_restarted_run_goes_live_again(root, monkeypatch):
    """A queued job restored after a restart re-claims its run id."""
    _start(root, 'q-abc', '2026-10-01T10:00:00+00:00')
    _restart(monkeypatch)
    assert re_.get_run_evidence('q-abc')['status'] == 'interrupted'
    _start(root, 'q-abc', '2026-10-01T11:00:00+00:00')
    assert re_.get_run_evidence('q-abc')['status'] == 'running'
    _complete(root, 'q-abc', '2026-10-01T11:00:00+00:00')
    assert re_.get_run_evidence('q-abc')['status'] == 'completed'
    assert re_._live_key(root, 'q-abc') not in re_._LIVE


def test_retention_prunes_orphaned_markers_and_keeps_live_ones(root, monkeypatch):
    monkeypatch.setattr(re_, 'MAX_MANIFESTS', 2)
    _start(root, 'r-orphan-old', '2026-10-01T10:00:00+00:00')
    _restart(monkeypatch)
    _start(root, 'r-live-old', '2026-10-01T11:00:00+00:00')
    _complete(root, 'r-2', '2026-10-02T10:00:00+00:00')
    _complete(root, 'r-3', '2026-10-03T10:00:00+00:00')

    ids = {r['run_id'] for r in re_.list_project_runs()['items']}
    assert ids == {'r-3', 'r-2', 'r-live-old'}
    assert not (root / 'run_evidence' / 'r-orphan-old').exists()


def test_retention_orders_a_marker_without_started_at_by_its_mtime(root, monkeypatch):
    monkeypatch.setattr(re_, 'MAX_MANIFESTS', 2)
    for run_id, bad in (('r-no-start', None), ('r-bad-start', 'not-a-date')):
        _start(root, run_id, '2026-10-01T10:00:00+00:00')
        marker = root / 'run_evidence' / run_id / 'running.json'
        data = json.loads(marker.read_bytes())
        data['started_at'] = bad
        marker.write_text(json.dumps(data))
    # Missing or unparsable started_at: the marker's mtime orders it instead.
    os.utime(root / 'run_evidence' / 'r-no-start' / 'running.json', (1e9, 1e9))  # 2001
    os.utime(root / 'run_evidence' / 'r-bad-start' / 'running.json', (1.9e9, 1.9e9))  # 2030
    _restart(monkeypatch)
    _complete(root, 'r-2', '2026-10-02T10:00:00+00:00')
    remaining = {p.name for p in (root / 'run_evidence').iterdir()}
    assert 'r-no-start' not in remaining
    assert remaining == {'r-bad-start', 'r-2'}


# ── the solver monitor outlives a session abort ─────────────────────────

class _Ctx:
    def __init__(self):
        self.solver_state = {'status': 'running', 'log_queue': None, 'thread': object()}
        self.solver_state_lock = threading.Lock()

    def set(self, **kw):
        with self.solver_state_lock:
            self.solver_state.update(kw)


class _Session:
    def __init__(self):
        self.active: list[bool] = []

    def mark_detached_monitor(self, tool_use_id, tool_name, *, active):
        self.active.append(active)

    def append_detached_progress(self, payload):
        pass


def _wait(predicate, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _monitor(ctx, session, calls, *, pending=None, tool_use_id='tu-1'):
    # No log queue: the bridge ends at once, as a session abort ends it.
    solver_bridge.start_detached_solver_monitor(
        session, ctx, tool_use_id=tool_use_id, tool_name='run_simulation',
        on_terminal=lambda: calls.append(('terminal', ctx.solver_state['status'])),
        on_superseded=lambda: calls.append(('superseded', ctx.solver_state['status'])),
        is_pending=pending)


@pytest.fixture
def fast_poll(monkeypatch):
    monkeypatch.setattr(solver_bridge, '_TERMINAL_POLL_S', 0.01)


def test_monitor_publishes_once_when_the_solve_ends_after_an_abort(fast_poll):
    ctx, session, calls = _Ctx(), _Session(), []
    _monitor(ctx, session, calls)
    assert _wait(lambda: session.active == [True, False])
    time.sleep(0.05)
    assert calls == []  # still solving: nothing published yet
    ctx.set(status='completed')
    assert _wait(lambda: calls == [('terminal', 'completed')])
    time.sleep(0.05)
    assert calls == [('terminal', 'completed')]  # once, and the poll has stopped


def test_monitor_never_attaches_a_newer_solves_outcome(fast_poll):
    ctx, session, calls = _Ctx(), _Session(), []
    _monitor(ctx, session, calls)
    assert _wait(lambda: session.active == [True, False])
    # Solve A ends and solve B starts between two polls; B then completes.
    ctx.set(status='running', thread=object())
    time.sleep(0.05)
    ctx.set(status='completed')
    assert _wait(lambda: calls != [])
    time.sleep(0.05)
    assert [kind for kind, _ in calls] == ['superseded']


def test_monitor_stops_waiting_once_the_run_is_no_longer_pending(fast_poll):
    ctx, session, calls, pending = _Ctx(), _Session(), [], [True]
    _monitor(ctx, session, calls, pending=lambda: pending[0])
    assert _wait(lambda: session.active == [True, False])
    pending[0] = False
    time.sleep(0.05)
    ctx.set(status='completed')
    time.sleep(0.05)
    assert calls == []


def test_monitor_publishes_once_when_already_terminal():
    ctx, session, calls = _Ctx(), _Session(), []
    ctx.set(status='failed')
    solver_bridge.start_detached_solver_monitor(
        session, ctx, tool_use_id='tu-done-1', tool_name='run_simulation',
        on_terminal=lambda: calls.append(session.active[:]))
    assert _wait(lambda: session.active == [True, False])
    time.sleep(0.05)
    # Published before the monitor reports itself inactive, as before.
    assert calls == [[True]]


# ── the foreground hooks the monitor calls ──────────────────────────────

def _token(root, run_id='fg-abc'):
    re_.start_run(root, PROJECT, run_id=run_id, kind='simulation',
                  started_at='2026-10-01T10:00:00+00:00', mode='lopf', job_id='fg:abc')
    return {'root': root, 'project_id': PROJECT, 'actor_id': OWNER, 'run_id': run_id,
            'job_id': 'fg:abc', 'kind': 'simulation', 'solver_kind': 'lopf',
            'tool_name': 'run_simulation', 'started_at': '2026-10-01T10:00:00+00:00',
            'unsaved_inputs': True, 'mode': 'lopf'}


def test_superseded_foreground_run_is_recorded_interrupted(root):
    token = _token(root)
    assert re_.foreground_run_pending(token) is True
    re_.publish_foreground_superseded(token)
    assert re_.foreground_run_pending(token) is False
    got = re_.get_run_evidence('fg-abc')
    assert got['status'] == 'interrupted' and got['warnings'] == ['outcome_superseded']
    assert got['mode'] == 'lopf' and got['unsaved_inputs'] is True and got['metrics'] == {}
    assert re_.publish_foreground_superseded(token)['status'] == 'interrupted'  # idempotent
