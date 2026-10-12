"""Issue 11 D-C1 — several slow job families at once, reads stay available.

Fake slow foreground (fg:), adequacy study (ad:) and report (rpt:) handles run
together. Waiting on one handle must neither finish nor disturb the others, and
read / navigation tools must answer while a wait is in flight. No real solves:
solver state is flipped by the test, as in the other issue 11 tests.
"""
from __future__ import annotations

import contextvars
import threading
import time

import pytest

from services import chat_service, chat_tools, utterance_admission as ua
from services import run_evidence as re_
from services.pypsa_service import PyPSAService

STUDY_KEY = 'fmea_sweep'


@pytest.fixture(autouse=True)
def _reset():
    ua._reset_for_tests()
    from harness import job_adapters as ja
    ja._reset_for_tests()
    yield
    ua._reset_for_tests()
    ja._reset_for_tests()


@pytest.fixture
def families(tmp_projects_dir, install_network, tmp_path, monkeypatch):
    import pypsa
    from harness import job_adapters as ja

    n = pypsa.Network()
    n.add('Bus', 'B1')
    install_network(n, name=None)
    ctx = PyPSAService.get_active_context()
    with ctx.solver_state_lock:
        ctx.solver_state.update(status='running', kind='lopf', objective=None, condition=None)
        ctx.solver_state[STUDY_KEY] = {'status': 'running', 'stage': 'sweep', 'iterations': []}
        ctx.solver_state['report_job'] = {
            'status': 'running', 'report_id': 'rep-1', 'mode': 'generate',
            'section': 'executive_summary', 'error': None, 'started_at': time.time(),
        }
    root = tmp_path / 'proj'
    root.mkdir()
    monkeypatch.setattr(re_, '_authority', lambda: (root, 'proj-d-c1', 'owner'))

    project = getattr(ctx, 'project_uuid', None)
    handles = {
        'fg': ja.register_foreground_solve(
            tool_name='run_simulation', tool_use_id='tu-fg', owner_user_id=None,
            project_uuid=project, kind='lopf'),
        'ad': ja.register_study_job(
            study_key=STUDY_KEY, tool_name='run_fmea_sweep', tool_use_id='tu-ad',
            owner_user_id=None, project_uuid=project),
        'rpt': ja.register_report_job(
            tool_name='generate_report', tool_use_id='tu-rpt', report_id='rep-1',
            owner_user_id=None, project_uuid=project),
    }
    session = chat_service.ChatSession()
    chat_tools.set_chat_session(session)

    def finish(family: str) -> None:
        with ctx.solver_state_lock:
            if family == 'fg':
                ctx.solver_state.update(status='completed', objective=1.0)
            elif family == 'ad':
                ctx.solver_state[STUDY_KEY]['status'] = 'done'
            else:
                ctx.solver_state['report_job'].update(status='done', version=1)

    def status(family: str) -> str:
        with ctx.solver_state_lock:
            if family == 'fg':
                return ctx.solver_state['status']
            if family == 'ad':
                return ctx.solver_state[STUDY_KEY]['status']
            return ctx.solver_state['report_job']['status']

    yield {'ctx': ctx, 'handles': handles, 'session': session, 'finish': finish,
           'status': status}
    for family in ('fg', 'ad', 'rpt'):
        finish(family)


def _wait_in_thread(job_id, timeout):
    box: dict = {}
    run_ctx = contextvars.copy_context()

    def _run():
        try:
            box['out'] = run_ctx.run(chat_tools.wait_for_job, job_id, timeout)
        except Exception as exc:  # noqa: BLE001 — surfaced to the asserting test
            box['error'] = exc

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread, box


def _await_wait_started(session, job_id, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with session._lock:
            progress = session.job_wait_progress
        if progress and progress.get('job_id') == job_id:
            return progress
        time.sleep(0.02)
    raise AssertionError('wait_for_job never published progress')


def _dispatch(session, name, args=None):
    frames = list(chat_service._dispatch_real_tool_call(
        session, {'id': f'tu-{name}', 'name': name, 'input': args or {}}, []))
    return {ev: p for ev, p in frames}


def _assert_reads_and_navigation_work(session, running_families):
    status = _dispatch(session, 'get_simulation_status')
    assert 'tool_result' in status and 'tool_error' not in status
    sim = status['tool_result']['result']
    assert sim['running'] is ('fg' in running_families)

    nav = _dispatch(session, 'ui_open_panel', {'panel_id': 'results'})
    assert 'tool_error' not in nav
    event = nav.get('ui_event') or nav['tool_result']['result']
    assert event['kind'] == 'navigate' and event['panel_id'] == 'results'

    runs = re_.list_project_runs()
    assert runs['items'] == [] and runs['has_more'] is False


@pytest.mark.parametrize('waited', ['fg', 'ad', 'rpt'])
def test_waiting_on_one_family_leaves_the_others_running_and_reads_answer(families, waited):
    handles, session = families['handles'], families['session']
    thread, box = _wait_in_thread(handles[waited], 5)
    _await_wait_started(session, handles[waited])

    others = [f for f in ('fg', 'ad', 'rpt') if f != waited]
    t0 = time.monotonic()
    _assert_reads_and_navigation_work(session, {'fg', 'ad', 'rpt'})
    assert time.monotonic() - t0 < 2.0, 'reads waited on the in-flight wait'
    assert thread.is_alive()

    for other in others:
        probe = chat_tools.wait_for_job(handles[other], 0)
        assert probe['terminal'] is False and probe['timed_out'] is True
        assert probe['job']['running'] is True

    families['finish'](waited)
    thread.join(timeout=3)
    assert not thread.is_alive() and 'error' not in box
    out = box['out']
    assert out['terminal'] is True and out['completed'] is True and out['timed_out'] is False
    assert out['job']['id'] == handles[waited]
    for other in others:
        assert families['status'](other) == 'running'
        assert chat_tools.wait_for_job(handles[other], 0)['terminal'] is False
    _assert_reads_and_navigation_work(session, set(others))


def test_cancelling_a_wait_does_not_stop_any_family(families):
    handles, session = families['handles'], families['session']
    thread, box = _wait_in_thread(handles['ad'], 10)
    _await_wait_started(session, handles['ad'])
    session.abort_event.set()
    thread.join(timeout=3)
    assert not thread.is_alive() and 'error' not in box
    assert box['out']['wait_cancelled'] is True and box['out']['terminal'] is False
    session.abort_event.clear()
    for family in ('fg', 'ad', 'rpt'):
        assert families['status'](family) == 'running'
    _assert_reads_and_navigation_work(session, {'fg', 'ad', 'rpt'})


def test_each_handle_reports_only_its_own_family(families):
    handles = families['handles']
    families['finish']('ad')
    families['finish']('rpt')
    fg = chat_tools.wait_for_job(handles['fg'], 0)
    ad = chat_tools.wait_for_job(handles['ad'], 0)
    rpt = chat_tools.wait_for_job(handles['rpt'], 0)
    assert (fg['job']['family'], ad['job']['family'], rpt['job']['family']) == (
        'foreground_simulation', 'adequacy_study', 'report_job')
    assert fg['terminal'] is False and fg['job']['status'] == 'running'
    assert ad['terminal'] is True and ad['job']['status'] == 'done'
    assert rpt['terminal'] is True and rpt['job']['status'] == 'done'
