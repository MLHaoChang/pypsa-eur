"""LC-03 / LC-T08: a past run's kept sections recheck the project ACL.

Reads already resolve the project for the acting user. Navigation (the
assistant opening the run, the Results view reading it) and download (the
export tool, then the exported file) must do the same: a user who could read a
past run and then lost access to the project is refused, with a plain reason,
on every path and for every family that keeps a section.

oj-grid (gas at 0.2 tCO2/MWh, so every family captures) is solved by the owner
at Gas 50 EUR/MWh, re-priced to 60 and solved again: the first run is a past
run. A second user of the same org is a project member, reads the past run,
and then the membership row is deleted: a real ACL change, no monkeypatch.
"""
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import delete

import main
from db.models import OrgMembership, ProjectMembership, User
from services import chat_service, chat_tools, retained_results, run_evidence
from services.pypsa_service import PyPSAService
from tests.conftest import attach_session
from tests.oj_grid import LOAD_MW, NAME, SOLAR_MW, SOLAR_PU, build_oj_grid
from tests.test_oj01_latest_loadflow_journey import _solve_once
from tests.test_workflow_tools import helpers_state  # noqa: F401

GAS_MWH = sum(LOAD_MW - SOLAR_MW * pu for pu in SOLAR_PU)
# Every family that keeps a section for a past run, by the tab that shows it.
FAMILIES = {a.section: a for a in retained_results.ADAPTERS}
REASON = 'This project is not available to you'


@pytest.fixture
def past_run(client, session_ctx, install_network, monkeypatch, _auth_db, seeded_identity):
    import harness.confirm as harness_confirm

    n = build_oj_grid()
    n.add('Carrier', 'gas', co2_emissions=0.2)
    n.add('Carrier', 'solar', co2_emissions=0.0)
    install_network(n)
    assert client.post(f'/api/projects/{NAME}').status_code == 200
    ctx = session_ctx(client)
    binding = PyPSAService.bind_request_context(ctx)
    cell = PyPSAService.bind_turn_cell()
    _engine, session_local = _auth_db
    member_id = uuid.uuid4()
    try:
        with monkeypatch.context() as m:
            m.setattr(harness_confirm, 'AUTO_APPROVE_TIERS', frozenset({'execution'}))
            chat_tools.set_chat_session(chat_service.ChatSession())
            older = _solve_once(chat_tools.chat_session())
            response = client.put('/api/network/generators/Gas',
                                  json={'name': 'Gas', 'bus': 'B1', 'marginal_cost': 60})
            assert response.status_code == 200, response.text
            _solve_once(chat_tools.chat_session())
        # The owner's edits took the edit lock; the member's exports need it free.
        assert client.delete(f'/api/projects/{ctx.project_uuid}/lock').status_code in (200, 204)
        now = datetime.now(tz=timezone.utc)
        with session_local() as db:
            db.add(User(id=member_id, email=f'member-{member_id.hex[:8]}@example.com',
                        password_hash=None, status='active', is_super_admin=False, created_at=now))
            db.flush()
            db.add(OrgMembership(id=uuid.uuid4(), user_id=member_id,
                                 org_id=seeded_identity['org_id'], role='member'))
            db.add(ProjectMembership(project_id=uuid.UUID(ctx.project_uuid), user_id=member_id,
                                     assigned_by=seeded_identity['user_id'], assigned_at=now))
            db.commit()
        with TestClient(main.app) as raw:
            browser = attach_session(raw, session_local, member_id)
            assert browser.post(f'/api/projects/{ctx.project_uuid}/activate').status_code == 200

            def revoke():
                with session_local() as db:
                    db.execute(delete(ProjectMembership).where(ProjectMembership.user_id == member_id))
                    db.commit()

            previous = chat_tools.acting_user_id()
            chat_tools.set_acting_user(str(member_id))
            chat_tools.set_chat_session(chat_service.ChatSession(owner_user_id=str(member_id)))
            try:
                yield {'run_id': older['run_id'], 'browser': browser, 'revoke': revoke}
            finally:
                chat_tools.set_acting_user(previous)
                chat_tools.set_chat_session(None)
    finally:
        PyPSAService._turn_cell.reset(cell)
        PyPSAService.reset_request_context(binding)
        with session_local() as db:
            db.execute(delete(ProjectMembership).where(ProjectMembership.user_id == member_id))
            row = db.get(User, member_id)
            if row is not None:
                db.delete(row)
            db.commit()


def _plain_refusal(exc: HTTPException) -> None:
    assert exc.status_code == 404
    assert exc.detail['error_kind'] == 'project_unavailable'
    assert exc.detail['message'].startswith(REASON)


def _browser_get(browser, url, **kwargs):
    """A browser request on its own session's resolution, not the test's turn cell."""
    token = PyPSAService._turn_cell.set(None)
    try:
        return browser.get(url, **kwargs)
    finally:
        PyPSAService._turn_cell.reset(token)


def _no_project_open(view) -> None:
    # LC03-ACCESS-1: access loss clears the session's project pointer, so the
    # browser's next read has no project bound and says so in plain words.
    assert view.status_code == 409, view.text
    assert view.json()['detail'] == {'error_kind': 'run_project_required',
                                     'message': run_evidence.NO_PROJECT_OPEN}
    assert 'data' not in view.json()


def test_navigation_is_refused_once_access_is_lost(past_run):
    run_id, browser = past_run['run_id'], past_run['browser']
    record = run_evidence.get_run_evidence(run_id)
    kept = {s['name'] for s in record['sections'] if s['available']}
    assert set(FAMILIES) <= kept, sorted(set(FAMILIES) - kept)
    # While a member: the assistant opens the run and the Results view reads it.
    for section in FAMILIES:
        opened = chat_tools.DISPATCHERS['discuss_project_run'](run_id=run_id, section=section)
        assert opened['kind'] == 'open_run_result' and opened['run_id'] == run_id
        view = _browser_get(browser, '/api/results/run-view', params={'run_id': run_id, 'section': section})
        assert view.status_code == 200, (section, view.text)
        assert view.json()['binding']['scope'] == 'historical'
        assert view.json()['data']['section'] == section

    past_run['revoke']()

    for section in FAMILIES:
        with pytest.raises(HTTPException) as refused:
            chat_tools.DISPATCHERS['discuss_project_run'](run_id=run_id, section=section)
        _plain_refusal(refused.value)
        _no_project_open(_browser_get(browser, '/api/results/run-view',
                                      params={'run_id': run_id, 'section': section}))


def test_download_is_refused_once_access_is_lost(past_run):
    run_id, browser = past_run['run_id'], past_run['browser']
    blobs = {}
    for section in FAMILIES:
        exported = chat_tools.DISPATCHERS['export_run_evidence'](run_id=run_id, section=section)
        blobs[section] = f"/api/projects/{NAME}/uploads/{exported['file_id']}/blob"
        got = _browser_get(browser, blobs[section])
        assert got.status_code == 200, (section, got.text)
        assert got.json()['section'] == section and got.json()['run_id'] == run_id

    past_run['revoke']()

    for section in FAMILIES:
        with pytest.raises(HTTPException) as refused:
            chat_tools.DISPATCHERS['export_run_evidence'](run_id=run_id, section=section)
        _plain_refusal(refused.value)
        # The file exported while they had access is not served either.
        assert _browser_get(browser, blobs[section]).status_code == 404, section


def test_the_answer_path_gives_the_same_plain_reason(past_run):
    run_id = past_run['run_id']
    old = chat_tools.get_results(result_kind='cost_breakdown', run_id=run_id)
    assert old['status'] == 'ok' and old['data']['opex'] == pytest.approx(50 * GAS_MWH, rel=1e-6)

    past_run['revoke']()

    with pytest.raises(HTTPException) as refused:
        chat_tools.get_results(result_kind='cost_breakdown', run_id=run_id)
    _plain_refusal(refused.value)
    with pytest.raises(HTTPException) as listed:
        chat_tools.list_project_runs()
    _plain_refusal(listed.value)


def _refusal(call) -> tuple:
    with pytest.raises(HTTPException) as refused:
        call()
    return refused.value.status_code, refused.value.detail


def _view(browser, run_id) -> tuple:
    view = _browser_get(browser, '/api/results/run-view',
                        params={'run_id': run_id, 'section': 'results_capacity'})
    return view.status_code, view.json()['detail']


def test_no_existence_oracle(past_run, client, session_ctx, _auth_db, seeded_identity, monkeypatch):
    """Never existed, never a member, revoked and deleted refuse byte-equal.

    The assistant's read (context bound for the turn) refuses `project_unavailable`;
    the browser's run view, on its own session, has no project bound once access
    is gone (LC03-ACCESS-1) and refuses `run_project_required` in plain words.
    """
    run_id, browser = past_run['run_id'], past_run['browser']
    ctx = session_ctx(client)
    read = lambda: run_evidence.get_run_evidence(run_id)  # noqa: E731
    _engine, session_local = _auth_db
    outsider_id = uuid.uuid4()
    with session_local() as db:
        db.add(User(id=outsider_id, email=f'outsider-{outsider_id.hex[:8]}@example.com',
                    password_hash=None, status='active', is_super_admin=False,
                    created_at=datetime.now(tz=timezone.utc)))
        db.flush()
        db.add(OrgMembership(id=uuid.uuid4(), user_id=outsider_id,
                             org_id=seeded_identity['org_id'], role='member'))
        db.commit()
    outsider = attach_session(TestClient(main.app), session_local, outsider_id)
    # A never-member cannot open the project, so its session has none bound.
    assert outsider.post(f'/api/projects/{ctx.project_uuid}/activate').status_code == 404
    member = chat_tools.acting_user_id()
    engine, browser_seen = {}, {}
    try:
        with monkeypatch.context() as m:
            m.setattr(ctx, 'project_uuid', str(uuid.uuid4()))
            engine['never existed'] = _refusal(read)
        # A session pointer can never name a project that does not exist: the
        # browser's form is a run id that was never recorded, from a session.
        browser_seen['never existed'] = _view(outsider, 'never-recorded-run')
        chat_tools.set_acting_user(str(outsider_id))
        engine['never a member'] = _refusal(read)
        browser_seen['never a member'] = _view(outsider, run_id)
        chat_tools.set_acting_user(member)
        past_run['revoke']()
        engine['revoked'] = _refusal(read)
        browser_seen['revoked'] = _view(browser, run_id)
        assert client.delete(f'/api/projects/{NAME}').status_code in (200, 204)
        engine['deleted'] = _refusal(read)
        browser_seen['deleted'] = _view(browser, run_id)
    finally:
        chat_tools.set_acting_user(member)
        with session_local() as db:
            db.execute(delete(OrgMembership).where(OrgMembership.user_id == outsider_id))
            row = db.get(User, outsider_id)
            if row is not None:
                db.delete(row)
            db.commit()
    status, detail = engine['revoked']
    assert status == 404 and detail['error_kind'] == 'project_unavailable'
    assert detail['message'].startswith(REASON)
    assert browser_seen['revoked'] == (409, {'error_kind': 'run_project_required',
                                             'message': run_evidence.NO_PROJECT_OPEN})
    for case in engine:
        assert engine[case] == engine['revoked'], case
        assert browser_seen[case] == browser_seen['revoked'], case


def test_nothing_held_outlives_revocation(past_run):
    """A past run already opened and its evidence held: the next read refuses."""
    run_id, browser = past_run['run_id'], past_run['browser']
    opened = chat_tools.DISPATCHERS['discuss_project_run'](run_id=run_id, section='results_capacity')
    assert opened['kind'] == 'open_run_result'
    held = run_evidence.get_run_evidence(run_id, section='results_capacity')
    assert held['run_id'] == run_id
    exported = chat_tools.DISPATCHERS['export_run_evidence']()  # the discussed run
    blob = f"/api/projects/{NAME}/uploads/{exported['file_id']}/blob"
    assert _browser_get(browser, blob).status_code == 200
    assert _browser_get(browser, '/api/results/run-view',
                        params={'run_id': run_id, 'section': 'results_capacity'}).status_code == 200

    past_run['revoke']()

    for call in (lambda: chat_tools.DISPATCHERS['discuss_project_run'](run_id=run_id, section='results_capacity'),
                 lambda: chat_tools.DISPATCHERS['export_run_evidence'](),
                 lambda: run_evidence.get_run_evidence(run_id, section='results_capacity')):
        _plain_refusal(pytest.raises(HTTPException, call).value)
    _no_project_open(_browser_get(browser, '/api/results/run-view',
                                  params={'run_id': run_id, 'section': 'results_capacity'}))
    assert _browser_get(browser, blob).status_code == 404
