"""Saved personal restrictions through HTTP and real ordinary tool dispatch."""
import contextlib
import uuid
import json
import pytest

from harness.loop import dispatch_tool_call
from harness.session import ChatSession
from services import chat_tools
from tests import test_workflow_tools as fixtures
from tests.test_chat_access import peer as peer_fixture

baseline = fixtures.baseline
helpers_state = fixtures.helpers_state
peer = peer_fixture


def save_rule(client, project, mode, revision=None):
    if revision is None:
        observed = client.get('/api/chat/access', params={'project_id': project})
        assert observed.status_code == 200, observed.text
        revision = observed.json()['restrictions']['revision']
    response = client.put('/api/chat/restrictions', json={
        'project_id': project, 'revision': revision, 'mode': mode})
    assert response.status_code == 200, response.text
    return response.json()


def test_saved_read_only_blocks_real_writes_but_preserves_reads(baseline, client):
    project = baseline[1]
    observed = client.get('/api/chat/access', params={'project_id': project})
    assert observed.status_code == 200, observed.text
    response = client.put('/api/chat/restrictions', json={
        'project_id': project, 'revision': str(uuid.UUID(int=0)), 'mode': 'read_only'})
    assert response.status_code == 200, response.text
    fresh = client.get('/api/chat/access', params={'project_id': project}).json()
    assert fresh['restrictions']['mode'] == 'read_only'
    assert fresh['restrictions']['revision'] == response.json()['revision']
    collector = []
    frames = list(dispatch_tool_call(ChatSession(), {
        'id': 'restricted-write', 'name': 'bulk_update_components', 'input': {
            'component_class': 'Generator', 'names': ['G1'],
            'updates': {'marginal_cost': 20}}}, collector))
    assert any(d.get('error_kind') == 'user_restriction' for _, d in frames), frames
    assert not any(e in {'tool_pending_confirmation', 'tool_running'} for e, _ in frames)
    assert chat_tools.get_component('Generator', 'G1')['marginal_cost'] == 10
    reads = []
    read_frames = list(dispatch_tool_call(ChatSession(), {
        'id': 'allowed-read', 'name': 'get_component',
        'input': {'component_class': 'Generator', 'name': 'G1'}}, reads))
    assert not any(e == 'tool_error' for e, _ in read_frames), read_frames
    assert reads and not reads[0].get('is_error')


def test_confirm_changes_requires_owned_http_approval_for_an_expert_write(baseline, client, seeded_identity):
    from harness import session as sessions

    save_rule(client, baseline[1], 'confirm_changes')
    session = sessions.get_or_create_session('restrict-' + uuid.uuid4().hex,
                                            owner_user_id=str(seeded_identity['user_id']))
    frames, collector = [], []
    try:
        for event, data in dispatch_tool_call(session, {
            'id': 'confirmed-write', 'name': 'bulk_update_components', 'input': {
                'component_class': 'Generator', 'names': ['G1'],
                'updates': {'marginal_cost': 20}}}, collector):
            frames.append((event, data))
            if event == 'tool_pending_confirmation':
                assert chat_tools.get_component('Generator', 'G1')['marginal_cost'] == 10
                response = client.post(f'/api/chat/{session.session_id}/confirm', json={
                    'token': data['confirmation_token'], 'decision': 'approve'})
                assert response.status_code == 200, response.text
        assert len([d for e, d in frames if e == 'tool_pending_confirmation']) == 1, frames
        assert not [d for e, d in frames if e == 'tool_error'], frames
        assert chat_tools.get_component('Generator', 'G1')['marginal_cost'] == 20
    finally:
        sessions.drop_session(session.session_id)


@pytest.mark.parametrize('change_at', ['tool_pending_confirmation', 'tool_running'])
@pytest.mark.parametrize('restore', [False, True])
def test_changed_saved_revision_revokes_waiting_and_not_started_work(baseline, client, change_at, restore):
    save_rule(client, baseline[1], 'confirm_changes')
    session, frames, collector = ChatSession(), [], []
    for event, data in dispatch_tool_call(session, {
        'id': 'changed-restriction', 'name': 'bulk_update_components', 'input': {
            'component_class': 'Generator', 'names': ['G1'],
            'updates': {'marginal_cost': 20}}}, collector):
        frames.append((event, data))
        if event == change_at:
            save_rule(client, baseline[1], 'read_only')
            if restore:
                save_rule(client, baseline[1], 'confirm_changes')
        if event == 'tool_pending_confirmation':
            session.record_decision(data['confirmation_token'], 'approve')
    assert any(d.get('error_kind') == 'confirmation_revoked' for _, d in frames), frames
    assert chat_tools.get_component('Generator', 'G1')['marginal_cost'] == 10
    assert len(collector) == 1 and collector[0]['is_error']


def test_stale_save_cannot_overwrite_and_clearing_keeps_a_new_revision(baseline, client):
    pid = baseline[1]
    first = save_rule(client, pid, 'read_only')
    stale = client.put('/api/chat/restrictions', json={
        'project_id': pid, 'revision': str(uuid.UUID(int=0)), 'mode': 'standard'})
    assert stale.status_code == 409, stale.text
    assert client.get('/api/chat/access').json()['restrictions'] == {
        'mode': 'read_only', 'revision': first['revision']}
    cleared = save_rule(client, pid, 'standard', first['revision'])
    assert cleared['revision'] not in {first['revision'], str(uuid.UUID(int=0))}
    observed = client.get('/api/chat/access').json()
    assert next(r for r in observed['confirmation_rules'] if r['tier'] == 'destructive')['native'] == 'user'
    assert next(r for r in observed['confirmation_rules'] if r['tier'] == 'write')['external'] == 'user'
    assert observed['restrictions']['mode'] == 'standard'


def test_personal_rules_are_not_another_members_policy(baseline, client, peer):
    browser, peer_id = peer
    pid = baseline[1]
    assert client.put('/api/projects/Tool Baseline/members', json={'user_ids': [peer_id]}).status_code == 200
    assert browser.post(f'/api/projects/{pid}/activate').status_code == 200
    assert browser.get('/api/chat/access').json()['project_lock'] == 'other'
    save_rule(browser, pid, 'read_only')
    assert browser.get('/api/chat/access').json()['restrictions']['mode'] == 'read_only'
    assert client.get('/api/chat/access').json()['restrictions']['mode'] == 'standard'
    save_rule(client, pid, 'confirm_changes')
    assert browser.get('/api/chat/access').json()['restrictions']['mode'] == 'read_only'


def test_foreign_and_no_longer_current_projects_cannot_be_edited(baseline, client, other_org_client):
    pid = baseline[1]
    body = {'project_id': pid, 'revision': str(uuid.UUID(int=0)), 'mode': 'read_only'}
    assert other_org_client.put('/api/chat/restrictions', json=body).status_code == 404
    assert client.post('/api/projects/Another', params={'force': True, 'rebind': True}).status_code == 200
    assert client.put('/api/chat/restrictions', json=body).status_code == 409
    assert client.get('/api/chat/access').json()['restrictions']['mode'] == 'standard'


def test_a_user_without_project_access_is_refused_as_no_access_not_unavailable(
        baseline, client, second_identity):
    """Issue 17: admission tells "no access to this project" (404, its own kind)
    apart from "the rule store is unreadable" (503 restriction_unavailable)."""
    from fastapi import HTTPException
    from harness import restrictions

    token = chat_tools._ACTING_USER_ID.set(str(second_identity['user_id']))
    try:
        with pytest.raises(HTTPException) as refused:
            restrictions.capture(ChatSession())
        frames = list(dispatch_tool_call(ChatSession(), {
            'id': 'no-access', 'name': 'bulk_update_components', 'input': {
                'component_class': 'Generator', 'names': ['G1'],
                'updates': {'marginal_cost': 20}}}, []))
    finally:
        chat_tools._ACTING_USER_ID.reset(token)
    assert refused.value.status_code == 404
    assert refused.value.detail['error_kind'] == 'project_access_denied'
    assert 'access to this project' in refused.value.detail['message']
    assert [d.get('error_kind') for e, d in frames if e == 'tool_error'] == ['project_access_denied']
    assert not any(e == 'tool_running' for e, _ in frames)
    assert chat_tools.get_component('Generator', 'G1')['marginal_cost'] == 10

WRITE_G1 = {'component_class': 'Generator', 'names': ['G1'], 'updates': {'marginal_cost': 20}}


@contextlib.contextmanager
def acting_as(user_id):
    token = chat_tools._ACTING_USER_ID.set(str(user_id))
    try:
        yield
    finally:
        chat_tools._ACTING_USER_ID.reset(token)


def refused_write():
    """One ordinary write refused at admission: what the user and the model
    see, minus the call id, after proving nothing ran."""
    collector = []
    frames = list(dispatch_tool_call(ChatSession(), {
        'id': 'refused-' + uuid.uuid4().hex, 'name': 'bulk_update_components',
        'input': WRITE_G1}, collector))
    assert not any(e in {'tool_pending_confirmation', 'tool_running'} for e, _ in frames), frames
    assert chat_tools.get_component('Generator', 'G1')['marginal_cost'] == 10
    errors = [{k: v for k, v in d.items() if k != 'tool_use_id'} for e, d in frames if e == 'tool_error']
    return json.dumps({'errors': errors, 'results': [r['content'] for r in collector]}, sort_keys=True)


def test_no_access_and_a_missing_project_are_refused_byte_for_byte_alike(
        baseline, client, second_identity):
    """Issue 17 V2: the refusal never tells "exists, not yours" from "no such
    project", and names neither."""
    from services.pypsa_service import PyPSAService

    pid = baseline[1]
    with acting_as(second_identity['user_id']):
        foreign = refused_write()
    active = PyPSAService.get_active_context()
    missing_id = str(uuid.uuid4())
    active.project_uuid = missing_id
    try:
        missing = refused_write()
    finally:
        active.project_uuid = pid
    assert foreign == missing
    assert json.loads(foreign)['errors'][0]['error_kind'] == 'project_access_denied'
    assert pid not in foreign and missing_id not in missing


def test_a_deleted_project_is_refused_like_one_without_access(baseline, client, second_identity):
    """Issue 17 V2: the bound project deleted from under the chat reads exactly
    like no access."""
    from services.pypsa_service import PyPSAService

    pid = baseline[1]
    with acting_as(second_identity['user_id']):
        foreign = refused_write()
    assert client.delete('/api/projects/Tool Baseline').status_code == 200
    assert PyPSAService.get_active_context().project_uuid == pid
    deleted = refused_write()
    assert foreign == deleted and pid not in deleted


def test_a_changed_binding_is_not_turned_into_no_access(baseline, client, monkeypatch):
    """Issue 17: only current_project's 404 becomes project_access_denied; its
    409 (binding changed) passes through unchanged."""
    from fastapi import HTTPException
    from harness import restrictions

    message = 'Project binding changed. Refresh access before saving restrictions.'

    def rebound(db, user, expected):
        raise HTTPException(409, message)

    monkeypatch.setattr(restrictions, 'current_project', rebound)
    with pytest.raises(HTTPException) as refused:
        restrictions.capture(ChatSession())
    assert refused.value.status_code == 409
    assert refused.value.detail == message


def test_a_change_set_never_offers_rows_to_an_actor_without_access(
        baseline, client, second_identity, monkeypatch):
    """Issue 17 V3 (b): change_set._admitted fails closed, so no set card shows
    for an actor without project access; each call keeps its own refused path.
    Only the admission check runs as that actor: the previews that follow
    would refuse on their own and hide a failing _admitted."""
    from harness import change_set, loop

    calls = [
        {'id': 'cs-0', 'name': 'update_component', 'input': {
            'component_class': 'Generator', 'name': 'G1', 'attrs': {'marginal_cost': 20}}},
        {'id': 'cs-1', 'name': 'update_component', 'input': {
            'component_class': 'Load', 'name': 'L1', 'attrs': {'p_set': 7}}},
    ]

    def members():
        session = ChatSession()
        return change_set.members(session, calls, tier_of=lambda name: loop._tool_tier(session, name),
                                  external_of=lambda name: False, redirects=None)

    assert [i['tool_use_id'] for i in members()] == ['cs-0', 'cs-1']  # the owner gets a set
    admitted = change_set._admitted

    def admitted_without_access(*args):
        with acting_as(second_identity['user_id']):
            return admitted(*args)

    monkeypatch.setattr(change_set, '_admitted', admitted_without_access)
    assert members() == []


def test_a_harness_runner_asks_the_user_for_an_actor_without_access(baseline, client, second_identity):
    """Issue 17 V3 (c): Runner._policy fails closed: no access asks the user."""
    from harness.runner import Runner

    assert Runner._policy(None, ChatSession(), 'get_component') == 'none'
    with acting_as(second_identity['user_id']):
        assert Runner._policy(None, ChatSession(), 'get_component') == 'user'


def approve_then(session, switch, collector):
    """Dispatch the G1 write to its card and approve it; `switch` runs in the
    generator's own context as the card is answered."""
    frames = []
    for event, data in dispatch_tool_call(session, {
            'id': 'switched-' + uuid.uuid4().hex, 'name': 'bulk_update_components',
            'input': WRITE_G1}, collector):
        frames.append((event, data))
        if event == 'tool_pending_confirmation':
            switch()
            session.record_decision(data['confirmation_token'], 'approve')
    return frames


def test_access_lost_while_a_card_waits_revokes_its_approval(baseline, client, second_identity):
    """Issue 17 V3 (d): confirm.py re-reads the rule at approval; an actor who
    lost access by then revokes the approval and nothing runs."""
    save_rule(client, baseline[1], 'confirm_changes')
    collector, token = [], []
    try:
        frames = approve_then(ChatSession(), lambda: token.append(
            chat_tools._ACTING_USER_ID.set(str(second_identity['user_id']))), collector)
    finally:
        for t in token:
            chat_tools._ACTING_USER_ID.reset(t)
    assert token, 'the card never showed'
    assert [d['error_kind'] for e, d in frames if e == 'tool_error'] == ['confirmation_revoked'], frames
    assert not any(e == 'tool_running' for e, _ in frames)
    assert len(collector) == 1 and collector[0]['is_error']
    assert chat_tools.get_component('Generator', 'G1')['marginal_cost'] == 10


def test_access_lost_after_the_card_decision_is_refused_before_the_handler(
        baseline, client, second_identity):
    """Issue 17 V3 (f): loop.require_current_confirmation re-reads the rule
    after the card's own check; access lost between the two refuses the write."""
    save_rule(client, baseline[1], 'confirm_changes')
    session, collector, token = ChatSession(), [], []

    def lose_access_after_the_card_check(tool_name, args):
        token.append(chat_tools._ACTING_USER_ID.set(str(second_identity['user_id'])))
        return True

    session.decision_guard = lose_access_after_the_card_check
    try:
        frames = approve_then(session, lambda: None, collector)
    finally:
        for t in token:
            chat_tools._ACTING_USER_ID.reset(t)
    assert token, 'the card was never decided'
    assert [d['error_kind'] for e, d in frames if e == 'tool_error'] == ['project_access_denied'], frames
    assert not any(e == 'tool_running' for e, _ in frames)
    assert len(collector) == 1 and collector[0]['is_error']
    assert chat_tools.get_component('Generator', 'G1')['marginal_cost'] == 10


def test_access_lost_at_a_change_set_member_gate_revokes_that_member(
        baseline, client, second_identity, monkeypatch):
    """Issue 17 V3 (e): change_set.member_gate re-reads the rule for each
    ticked member; an actor who lost access there writes nothing."""
    import contextvars
    import threading
    import time
    from harness import change_set
    from services import chat_service
    from tests.test_change_set import VOLL, _client, _voll

    monkeypatch.setattr(change_set, 'ENABLED', True)
    still_shown = change_set._still_shown

    def lose_access_then_check(*args, **kwargs):
        chat_tools._ACTING_USER_ID.set(str(second_identity['user_id']))  # the turn thread's own context
        return still_shown(*args, **kwargs)

    monkeypatch.setattr(change_set, '_still_shown', lose_access_then_check)
    session, events, done = chat_service.ChatSession(), [], threading.Event()
    calls = [('update_component', {'component_class': 'Generator', 'name': 'G1',
                                   'attrs': {'marginal_cost': 20}}),
             VOLL]  # two asset edits in a saved project route to one preview card instead
    voll = _voll()

    def turn():
        try:
            events.extend(chat_service.run_turn(session, 'do it', client=_client(calls),
                                                ui_context={'ui_mode': 'expert'}))
        finally:
            done.set()

    worker = threading.Thread(target=contextvars.copy_context().run, args=(turn,))
    worker.start()
    deadline, pending = time.monotonic() + 5.0, None
    while time.monotonic() < deadline and pending is None:
        with session._lock:
            pending = next(iter(session.pending_confirmations), None)
        time.sleep(0.02)
    assert pending, 'no change-set card appeared'
    session.record_decision(pending, 'approve')
    assert done.wait(10.0), 'turn did not finish'
    worker.join()
    cards = [d for e, d in events if e == 'tool_pending_confirmation']
    assert len(cards) == 1 and cards[0].get('change_set'), events
    errors = {d['tool_use_id']: d['error_kind'] for e, d in events if e == 'tool_error'}
    assert errors['tu-0'] == 'confirmation_revoked', events
    assert errors.get('tu-1') in {'confirmation_revoked', 'project_access_denied'}, events
    assert not any(e == 'tool_running' for e, _ in events)
    assert chat_tools.get_component('Generator', 'G1')['marginal_cost'] == 10
    assert _voll() == voll


@pytest.mark.parametrize('invalid', [
    {'mode': 'approve_all'}, {'mode': True}, {'owner_id': 'someone-else'},
    {'auto_approve': True}, {'revision': 'grant'}, {'project_id': None},
])
def test_document_or_client_fields_cannot_grant_permission(baseline, client, invalid):
    body = {'project_id': baseline[1], 'revision': str(uuid.UUID(int=0)), 'mode': 'standard', **invalid}
    assert client.put('/api/chat/restrictions', json=body).status_code == 422
    assert client.get('/api/chat/access').json()['restrictions']['mode'] == 'standard'


def test_edit_requires_authentication(anon_client):
    assert anon_client.put('/api/chat/restrictions', json={
        'project_id': str(uuid.uuid4()), 'revision': str(uuid.UUID(int=0)),
        'mode': 'read_only'}).status_code == 401


def test_unavailable_persistence_never_defaults_to_standard(baseline, client, _auth_db):
    from sqlalchemy import text

    save_rule(client, baseline[1], 'read_only')
    engine, _ = _auth_db
    with engine.begin() as connection:
        connection.execute(text('ALTER TABLE chat_restrictions RENAME TO unavailable_chat_restrictions'))
    try:
        assert client.get('/api/chat/access').status_code == 503
        frames = list(dispatch_tool_call(ChatSession(), {
            'id': 'unavailable-policy', 'name': 'bulk_update_components', 'input': {
                'component_class': 'Generator', 'names': ['G1'],
                'updates': {'marginal_cost': 20}}}, []))
        assert any(d.get('error_kind') == 'restriction_unavailable' for _, d in frames), frames
        assert not any(e == 'tool_running' for e, _ in frames)
        assert chat_tools.get_component('Generator', 'G1')['marginal_cost'] == 10
    finally:
        with engine.begin() as connection:
            connection.execute(text('ALTER TABLE unavailable_chat_restrictions RENAME TO chat_restrictions'))


@pytest.mark.parametrize(('mode', 'disposition', 'cost'), [
    ('standard', 'policy', 20), ('confirm_changes', 'user', 10), ('read_only', 'blocked', 10),
])
def test_saved_rules_add_to_native_policy_exemptions(baseline, client, monkeypatch, mode, disposition, cost):
    from harness import confirm

    preview = chat_tools.preview_project_changes([{
        'component_class': 'Generator', 'names': ['G1'], 'attribute': 'marginal_cost', 'value': 20}])
    monkeypatch.setattr(confirm, 'AUTO_APPROVE_TIERS', frozenset({'execution'}))
    save_rule(client, baseline[1], mode)
    policy = client.get('/api/chat/access').json()
    assert next(r for r in policy['confirmation_rules'] if r['tier'] == 'execution')['native'] == disposition
    session, frames = ChatSession(), []
    for event, data in dispatch_tool_call(session, {
        'id': 'native-exemption', 'name': 'apply_project_changes',
        'input': {'preview_id': preview['preview_id']}}, []):
        frames.append((event, data))
        if event == 'tool_pending_confirmation':
            session.record_decision(data['confirmation_token'], 'deny')
    assert bool([d for e, d in frames if e == 'tool_pending_confirmation']) == (mode == 'confirm_changes')
    assert chat_tools.get_component('Generator', 'G1')['marginal_cost'] == cost


@pytest.mark.parametrize('mode', ['standard', 'confirm_changes', 'read_only'])
def test_external_write_floors_and_read_tools_keep_native_dispatch(baseline, client, seeded_identity, monkeypatch, tmp_path, mode):
    from harness import confirm, mcp_client
    from tests.test_harness_mcp_client import _stub

    config = tmp_path / 'mcp-servers.json'
    config.write_text(json.dumps({'servers': [_stub()]}))
    monkeypatch.setattr(mcp_client, 'config_path', lambda: config)
    monkeypatch.setattr(confirm, 'AUTO_APPROVE_TIERS', frozenset({'destructive'}))
    save_rule(client, baseline[1], mode)
    mcp_client.reset_for_tests()
    session = ChatSession(owner_user_id=str(seeded_identity['user_id']))
    try:
        mcp_client.session_tools(session)
        frames = []
        for event, data in dispatch_tool_call(session, {
            'id': 'external-write', 'name': 'mcp__stub__wipe', 'input': {}}, []):
            frames.append((event, data))
            if event == 'tool_pending_confirmation':
                assert data['external'] == {'server': 'stub'}
                session.record_decision(data['confirmation_token'], 'deny')
        assert bool([d for e, d in frames if e == 'tool_pending_confirmation']) == (mode != 'read_only')
        assert not any(e == 'tool_running' for e, _ in frames)
        results = []
        reads = list(dispatch_tool_call(session, {
            'id': 'external-read', 'name': 'mcp__stub__lookup', 'input': {'key': 'safe'}}, results))
        assert not any(e in {'tool_pending_confirmation', 'tool_error'} for e, _ in reads), reads
        assert 'value of safe' in json.dumps(results)
    finally:
        mcp_client.reset_for_tests()


def test_read_only_refuses_a_valid_checked_effect_before_reservation(baseline, client, monkeypatch):
    from tests.test_harness_reliability import effect_contract, effect_turns
    from tests.assurance_acceptance.support import Harness, approval
    from harness import session as sessions

    monkeypatch.setenv('PYPSA_GUI_ANSWER_ASSURANCE_MODE', 'strict')
    monkeypatch.setenv('PYPSA_GUI_ANSWER_ASSURANCE_EFFECTS', 'checked-input-update/1')
    save_rule(client, baseline[1], 'read_only')
    harness = Harness(client, monkeypatch)
    contract = effect_contract(baseline[0])
    try:
        frames = harness.run(effect_turns(contract), checked_operation=contract)
        assert not any(e in {'tool_pending_confirmation', 'tool_running'} for e, _ in frames), frames
        assert any(d.get('error_kind') == 'user_restriction' for _, d in frames), frames
        assert chat_tools.get_component('Load', 'L1')['p_set'] == 5
        assert approval(frames)['verdict'] == 'limited'
        assert client.get('/api/chat/operations/' + contract['operation_id']).status_code == 404
    finally:
        for sid in harness.session_ids:
            sessions.drop_session(sid)


def test_rule_changed_while_queued_is_checked_at_actual_worker_pickup(baseline, client, monkeypatch):
    import contextvars
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from harness import loop

    queued, occupied, release = threading.Event(), threading.Event(), threading.Event()

    def hog():
        occupied.set()
        release.wait(10)

    class ObservedPool(ThreadPoolExecutor):
        def submit(self, fn, *args, **kwargs):
            future = super().submit(fn, *args, **kwargs)
            if fn is not hog:
                queued.set()
            return future

    save_rule(client, baseline[1], 'confirm_changes')
    session, collector, frames = ChatSession(), [], []
    gen = dispatch_tool_call(session, {'id': 'queued-write', 'name': 'bulk_update_components', 'input': {
        'component_class': 'Generator', 'names': ['G1'], 'updates': {'marginal_cost': 20}}}, collector)
    with ObservedPool(max_workers=1) as pool, ThreadPoolExecutor(max_workers=1) as driver:
        monkeypatch.setattr(loop, '_TOOL_EXECUTOR', pool)
        pool.submit(hog)
        assert occupied.wait(2)
        try:
            for event, data in gen:
                frames.append((event, data))
                if event == 'tool_pending_confirmation':
                    session.record_decision(data['confirmation_token'], 'approve')
                if event == 'tool_running':
                    break
            later = driver.submit(contextvars.copy_context().run, lambda: list(gen))
            assert queued.wait(2), 'native call was not submitted to the occupied pool'
            save_rule(client, baseline[1], 'read_only')
            release.set()
            frames.extend(later.result(timeout=5))
        finally:
            release.set()
    assert any(d.get('error_kind') == 'confirmation_revoked' for _, d in frames), frames
    assert chat_tools.get_component('Generator', 'G1')['marginal_cost'] == 10


@pytest.mark.parametrize('existing', [False, True])
def test_concurrent_saves_have_one_winner_without_a_lost_update(baseline, client, existing):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    pid = baseline[1]
    if existing:
        save_rule(client, pid, 'standard')
    revision = client.get('/api/chat/access').json()['restrictions']['revision']
    ready = threading.Barrier(2)

    def request(mode):
        ready.wait(timeout=3)
        return mode, client.put('/api/chat/restrictions', json={
            'project_id': pid, 'revision': revision, 'mode': mode})

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(request, ['read_only', 'confirm_changes']))
    assert sorted(response.status_code for _, response in results) == [200, 409]
    mode, winner = next((mode, response) for mode, response in results if response.status_code == 200)
    current = client.get('/api/chat/access').json()['restrictions']
    assert current == {'mode': mode, 'revision': winner.json()['revision']}


def test_saved_rules_survive_new_connections_and_a_new_chat(baseline, client, _auth_db):
    saved = save_rule(client, baseline[1], 'read_only')
    _auth_db[0].dispose()
    fresh = client.get('/api/chat/access').json()['restrictions']
    assert fresh == {'mode': 'read_only', 'revision': saved['revision']}
    frames = list(dispatch_tool_call(ChatSession(), {
        'id': 'new-chat', 'name': 'bulk_update_components', 'input': {
            'component_class': 'Generator', 'names': ['G1'],
            'updates': {'marginal_cost': 20}}}, []))
    assert any(d.get('error_kind') == 'user_restriction' for _, d in frames), frames
    assert chat_tools.get_component('Generator', 'G1')['marginal_cost'] == 10


def test_a_persisted_initial_revision_is_corruption_not_an_absent_rule(baseline, client, _auth_db):
    from sqlalchemy import text

    save_rule(client, baseline[1], 'read_only')
    with _auth_db[0].begin() as connection:
        connection.execute(text('UPDATE chat_restrictions SET revision = :invalid'),
                           {'invalid': uuid.UUID(int=0).hex})
    assert client.get('/api/chat/access').status_code == 503
    frames = list(dispatch_tool_call(ChatSession(), {
        'id': 'corrupt-rule', 'name': 'bulk_update_components', 'input': {
            'component_class': 'Generator', 'names': ['G1'],
            'updates': {'marginal_cost': 20}}}, []))
    assert any(d.get('error_kind') == 'restriction_unavailable' for _, d in frames), frames
    assert chat_tools.get_component('Generator', 'G1')['marginal_cost'] == 10
