"""Study harness issue 23 (HH-02): typed, user-confirmed requirements on the
durable parent thread, enforced on assistant edits at dispatch.

A requirement the user confirms on its card survives trims, a provider
switch and a restart; an assistant edit that breaks it is refused with no
write (an interactive session) or falls back to the card (a harness run).
It never blocks a manual edit and never authorizes a tool.
"""
from __future__ import annotations

from types import SimpleNamespace

import pypsa
import pytest

from harness import eventlog
from services import chat_service, chat_tools
from services import durable_conversation as dc
from services import utterance_admission as ua
from services.pypsa_service import PyPSAService

OWNER = 'owner-aaa'
PROJECT = '11111111-1111-1111-1111-111111111111'
SONNET = SimpleNamespace(id='sonnet', wire='anthropic', model='claude-sonnet')
GPT = SimpleNamespace(id='gpt', wire='openai', model='gpt-x')

BOUND = {'kind': 'scalar_bound', 'v': 1, 'component_class': 'StorageUnit', 'name': 'Battery',
         'field': 'max_hours', 'op': 'le', 'value': 6.0, 'unit': 'h'}


@pytest.fixture
def thread(tmp_path, monkeypatch, install_network):
    import security

    dc._reset_for_tests()
    chat_service._reset_sessions_for_tests()
    ua._reset_for_tests()
    root = tmp_path / PROJECT
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(dc, '_authority', lambda: (root, PROJECT, OWNER))
    monkeypatch.setattr(eventlog, '_root', lambda: tmp_path / 'chat-events')
    monkeypatch.setattr(security, 'replica_id', lambda: 'replica-alpha')
    n = pypsa.Network()
    n.set_snapshots(range(4))
    n.add('Bus', 'B')
    n.add('StorageUnit', 'Battery', bus='B', p_nom=20, max_hours=4)
    n.add('Generator', 'Diesel', bus='B', p_nom=10, p_nom_max=50)
    install_network(n, name=None)
    tid = dc.create_thread()['thread_id']
    view, session = dc.resume_episode(tid, generation=1)
    chat_tools.set_chat_session(session)
    yield SimpleNamespace(id=tid, session=session, root=root)
    chat_tools.set_chat_session(None)
    dc._reset_for_tests()
    chat_service._reset_sessions_for_tests()
    ua._reset_for_tests()


def _dispatch(session, name, args, decision='approve'):
    frames, collector, cards = [], [], []
    chat_tools.set_chat_session(session)
    gen = chat_service._dispatch_real_tool_call(session, {'id': 'tu-1', 'name': name, 'input': args}, collector)
    for event, data in gen:
        frames.append((event, data))
        if event == 'tool_pending_confirmation':
            cards.append(data)
            session.record_decision(data['confirmation_token'], decision)
    errors = [d.get('error_kind') for e, d in frames if e == 'tool_error']
    return frames, cards, errors


def _confirm(session, requirement, decision='approve', **extra):
    return _dispatch(session, 'set_requirement', {'requirement': requirement, **extra}, decision)


def _active(tid):
    return [r for r in dc.get_thread(tid)['requirements']['items'] if r['status'] == 'active']


def _max_hours():
    return float(PyPSAService.get_network().storage_units.at['Battery', 'max_hours'])


def test_confirmed_bound_survives_trims_switch_restart_and_refuses_the_edit(thread):
    """Acceptance 1 (written first): a confirmed scalar_bound on
    StorageUnit.max_hours, two trims, a provider switch and a restart; the
    assistant then proposes a value above the bound: refused, nothing written."""
    _, cards, errors = _confirm(thread.session, BOUND)
    assert errors == [] and len(cards) == 1
    card = cards[0]['edit_card']
    assert card['kind'] == 'requirement'
    assert any(row['new'] == 6.0 and row['unit'] == 'h' for row in card['rows'])
    assert len(_active(thread.id)) == 1

    session = thread.session
    for _ in range(2):
        session.messages.extend({'role': 'user', 'content': f'm{i}'} for i in range(8))
        dc.compact_model_context(thread.id, session.messages, max_len=2)
    view = dc.get_thread(thread.id)
    view, session = dc.switch_profile(thread.id, generation=view['generation'], profile=SONNET)
    view, session = dc.switch_profile(thread.id, generation=view['generation'], profile=GPT)
    assert session is not thread.session
    view, session = dc.resume_episode(thread.id, generation=view['generation'])
    assert [r['predicate'] for r in _active(thread.id)] == [{**BOUND, 'uid': _active(thread.id)[0]['predicate']['uid']}]

    _, cards, errors = _dispatch(session, 'update_component', {
        'component_class': 'StorageUnit', 'name': 'Battery', 'attrs': {'max_hours': 8.0}})
    assert errors == ['requirement_violated'] and cards == []
    assert _max_hours() == 4.0

    _, cards, errors = _dispatch(session, 'update_component', {
        'component_class': 'StorageUnit', 'name': 'Battery', 'attrs': {'max_hours': 5.0}})
    assert errors == [] and len(cards) == 1
    assert _max_hours() == 5.0


def _run_session(monkeypatch, thread, task_extra=None):
    """A harness run's own session (`run:<task>`) whose task recorded the thread."""
    from services import assistant_tasks, run_envelope

    task_id = 'task-23'
    record = dict(task_extra or {'thread_id': thread.id, 'rev': 0, 'items': []})
    task = {'task_id': task_id, 'status': 'ready', 'run': {}, 'steps': [], 'requirements': record}
    monkeypatch.setattr(assistant_tasks, '_read', lambda tid: (thread.root / 't.json', task))
    monkeypatch.setattr(assistant_tasks, '_authority', lambda: (thread.root, PROJECT, OWNER))
    admitted = []
    monkeypatch.setattr(run_envelope, 'admit', lambda *a, **k: admitted.append(k['tool']) or 'admitted')
    session = chat_service.ChatSession()
    session.session_id = run_envelope.SESSION_PREFIX + task_id
    session.task_id = task_id
    return session, admitted


def test_run_session_violation_falls_to_the_card_never_the_envelope(thread, monkeypatch):
    """Acceptance 2: inside a granted envelope the violating edit is not run
    unattended: the envelope is never consulted and the call reaches the card
    (which the headless turn withdraws and counts as a refusal)."""
    _confirm(thread.session, BOUND)
    session, admitted = _run_session(monkeypatch, thread)
    _, cards, errors = _dispatch(session, 'update_component', {
        'component_class': 'StorageUnit', 'name': 'Battery', 'attrs': {'max_hours': 8.0}}, decision='deny')
    assert admitted == []
    assert len(cards) == 1 and cards[0]['edit_card']['requirement_violations'][0]['requirement_id']
    assert errors == ['confirmation_denied']
    assert _max_hours() == 4.0
    # A call inside the bound is still the envelope's to admit.
    _dispatch(session, 'update_component', {
        'component_class': 'StorageUnit', 'name': 'Battery', 'attrs': {'max_hours': 5.0}})
    assert admitted == ['update_component']


def test_preserve_horizon_refuses_assistant_writers_and_marks_a_manual_change_stale(thread):
    from harness import requirements

    _, cards, errors = _confirm(thread.session, {'kind': 'preserve_horizon', 'v': 1})
    assert errors == [] and _active(thread.id)[0]['predicate']['digest']
    for tool, args in (('set_snapshots', {'snapshots': ['2030-01-01 00:00']}),

                       ('reset_network', {})):
        _, cards, errors = _dispatch(thread.session, tool, args)
        assert errors == ['requirement_violated'] and cards == [], tool
    assert len(PyPSAService.get_network().snapshots) == 4
    # A manual edit is never blocked; it marks the last evaluation stale.
    n = PyPSAService.get_network()
    with PyPSAService.get_lock():
        n.snapshot_weightings.loc[n.snapshots[0], 'objective'] = 2.0
    text = requirements.addendum(thread.session)
    assert '<untrusted_data>' in text and 'not instructions' in text
    assert _active(thread.id)[0]['last_evaluation']['state'] == 'stale'
    assert _active(thread.id)[0]['last_evaluation']['reason'] == 'horizon_changed'


def test_unconfirmed_or_unit_less_proposals_never_become_active(thread):
    _, cards, errors = _confirm(thread.session, BOUND, decision='deny')
    assert len(cards) == 1 and errors == ['confirmation_denied'] and _active(thread.id) == []
    for bad, kind in (({k: v for k, v in BOUND.items() if k != 'unit'}, 'requirement_unit_mismatch'),
                      ({**BOUND, 'unit': 'MWh'}, 'requirement_unit_mismatch'),
                      ({**BOUND, 'extra': 1}, 'invalid_requirement'),
                      ({**BOUND, 'field': 'p_nom_opt'}, 'invalid_requirement'),
                      ({**BOUND, 'v': 2}, 'invalid_requirement')):
        _, cards, errors = _confirm(thread.session, bad)
        assert errors == [kind] and cards == [], bad
    assert _active(thread.id) == []
    _confirm(thread.session, BOUND)
    _, cards, errors = _confirm(thread.session, {**BOUND, 'op': 'ge', 'value': 7.0})
    assert errors == ['requirement_contradicts'] and cards == []
    assert len(_active(thread.id)) == 1


def test_bulk_and_replacing_writers_are_checked_too(thread):
    """GK B: not only update_component. bulk rows are checked; a writer that
    cannot show the bounded field fails closed."""
    _confirm(thread.session, {**BOUND, 'component_class': 'Generator', 'name': 'Diesel', 'field': 'p_nom_max',
                              'value': 40.0, 'unit': 'MW'})
    _, cards, errors = _dispatch(thread.session, 'bulk_update_components', {
        'component_class': 'Generator', 'names': ['Diesel'], 'updates': {'p_nom_max': 60.0}})
    assert errors == ['requirement_violated'] and cards == []
    _, cards, errors = _dispatch(thread.session, 'reset_network', {})
    assert errors == ['requirement_violated'] and cards == []
    assert float(PyPSAService.get_network().generators.at['Diesel', 'p_nom_max']) == 50.0


def test_rename_keeps_the_bound_and_a_deleted_target_is_stale_not_dropped(thread):
    from harness import requirements

    _confirm(thread.session, BOUND)
    _, _, errors = _dispatch(thread.session, 'update_component', {
        'component_class': 'StorageUnit', 'name': 'Battery', 'attrs': {'name': 'BESS'}})
    assert errors == []
    _, cards, errors = _dispatch(thread.session, 'update_component', {
        'component_class': 'StorageUnit', 'name': 'BESS', 'attrs': {'max_hours': 9.0}})
    assert errors == ['requirement_violated'] and cards == []
    n = PyPSAService.get_network()
    with PyPSAService.get_lock():
        n.remove('StorageUnit', 'BESS')  # a manual delete
    requirements.addendum(thread.session)
    (record,) = _active(thread.id)
    assert record['last_evaluation']['reason'] == 'target_missing'


def test_retire_is_carded_and_a_denied_retire_keeps_the_requirement(thread):
    _confirm(thread.session, BOUND)
    (record,) = _active(thread.id)
    rev = dc.get_thread(thread.id)['requirements']['rev']
    _, cards, errors = _dispatch(thread.session, 'set_requirement', {'retire': record['id']}, decision='deny')
    assert len(cards) == 1 and errors == ['confirmation_denied']
    assert [r['id'] for r in _active(thread.id)] == [record['id']]
    assert dc.get_thread(thread.id)['requirements']['rev'] == rev
    _, cards, errors = _dispatch(thread.session, 'set_requirement', {'retire': record['id']})
    assert errors == [] and _active(thread.id) == []
    _, _, errors = _dispatch(thread.session, 'update_component', {
        'component_class': 'StorageUnit', 'name': 'Battery', 'attrs': {'max_hours': 8.0}})
    assert errors == [] and _max_hours() == 8.0


def test_brief_goes_stale_on_activate_or_retire_not_on_an_evaluation_mark(thread, monkeypatch):
    from harness import requirements
    from services import assistant_tasks

    monkeypatch.setattr(assistant_tasks, '_fingerprint', lambda: 'fp')
    monkeypatch.setattr(assistant_tasks, '_authority', lambda: (thread.root, PROJECT, OWNER))
    _confirm(thread.session, BOUND)
    task = {'status': 'ready', 'brief': {'objective': 'x'}, 'fingerprint': 'fp',
            'requirements': requirements.brief_record(thread.session)}
    assert task['requirements']['items'][0]['predicate']['field'] == 'max_hours'
    assert not assistant_tasks._stale(task)
    n = PyPSAService.get_network()
    with PyPSAService.get_lock():
        n.storage_units.loc['Battery', 'max_hours'] = 9.0  # a manual edit
    requirements.addendum(thread.session)
    assert _active(thread.id)[0]['last_evaluation']['state'] == 'stale'
    assert not assistant_tasks._stale(task)
    _dispatch(thread.session, 'set_requirement', {'retire': _active(thread.id)[0]['id']})
    assert assistant_tasks._stale(task)


def test_not_an_edit_lock_and_never_on_a_run_session(thread, monkeypatch):
    """A requirement never blocks a manual edit and never authorizes a tool:
    a run's session cannot set one, and the envelope never admits it."""
    _confirm(thread.session, BOUND)
    from routers import network as network_router  # noqa: F401 — the manual path never passes dispatch
    n = PyPSAService.get_network()
    with PyPSAService.get_lock():
        n.storage_units.loc['Battery', 'max_hours'] = 12.0
    assert _max_hours() == 12.0
    session, admitted = _run_session(monkeypatch, thread)
    _, cards, errors = _dispatch(session, 'set_requirement', {'retire': _active(thread.id)[0]['id']})
    assert errors == ['requirement_thread_required'] and cards == [] and admitted == []
    assert len(_active(thread.id)) == 1


def test_checkpoint_thread_never_writes_requirements(thread):
    with pytest.raises(Exception) as refused:
        dc.checkpoint_thread(thread.id, generation=dc.get_thread(thread.id)['generation'],
                             requirements={'rev': 0, 'items': []})
    assert refused.value.detail['error_kind'] == 'invalid_thread_field'
