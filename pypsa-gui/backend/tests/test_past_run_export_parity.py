"""LC-03 past-run UI/export parity.

A past run on Results exports as that run, never the latest: the Export
chooser's `GET /api/results/run-evidence.json` and the assistant's
`export_run_evidence` write the same bytes, name and type (one
`run_evidence.export_bytes`), under the run view's own project check. The
results workbook names the run it holds and refuses another run's id, on the
route and the chat tool alike, so it is never the latest workbook in place of
the run the user is looking at.

oj-grid, solved at Gas 50 and again at 60, read by a project member (the
LC-T08 fixture): the first run is the past run.
"""
import io

import pytest
from fastapi import HTTPException
from openpyxl import load_workbook

from services import chat_tools, run_evidence
from tests.oj_grid import NAME
from tests.test_retained_acl import _browser_get, past_run  # noqa: F401 — registers the fixture

URL = '/api/results/run-evidence.json'
WORKBOOK = '/api/results/workbook.xlsx'
SECTIONS = (None, 'results_capacity')


@pytest.fixture
def parity(request):
    """The LC-T08 past run: a member reads oj-grid's first of two runs."""
    return request.getfixturevalue('past_run')


def _latest() -> str:
    return run_evidence.list_project_runs(limit=1, status='completed')['items'][0]['run_id']


def _file(browser, run_id, section):
    params = {'run_id': run_id} | ({'section': section} if section else {})
    return _browser_get(browser, URL, params=params)


def _sheets(blob: bytes) -> dict:
    wb = load_workbook(io.BytesIO(blob))
    # Every cell but About's own clock ("Generated at").
    return {ws.title: [tuple(c.value for c in row) for row in ws.iter_rows()
                       if not (ws.title == 'About' and row[0].value == 'Generated at')] for ws in wb}


@pytest.mark.parametrize('section', SECTIONS)
def test_the_chooser_file_is_the_assistant_export_byte_for_byte(parity, section):
    run_id, browser = parity['run_id'], parity['browser']
    assert run_id != _latest()
    exported = chat_tools.DISPATCHERS['export_run_evidence'](run_id=run_id, section=section)
    blob = _browser_get(browser, f"/api/projects/{NAME}/uploads/{exported['file_id']}/blob")
    assert blob.status_code == 200, blob.text

    got = _file(browser, run_id, section)

    assert got.status_code == 200, got.text
    assert got.content == blob.content  # a route that re-serialises fails here
    assert got.headers['content-type'].split(';')[0] == 'application/json'
    assert blob.headers['content-type'].split(';')[0] == 'application/json'
    name = f'run_{run_id}_{section}.json' if section else f'run_{run_id}.json'
    assert exported['filename'] == name
    assert name in got.headers['content-disposition']
    body = got.json()
    assert body['run_id'] == run_id and body.get('section') == section
    if section:  # the past run's own number, not the latest run's (hand oracle: gas at 50)
        assert body['data']['economics_by_carrier']['by_carrier']['gas']['lcoe_eur_per_mwh']['total'] == 50


def test_the_file_names_a_run_or_refuses(parity):
    browser = parity['browser']
    missing = _browser_get(browser, URL)
    assert missing.status_code == 404
    assert missing.json()['detail']['error_kind'] == 'run_not_found'
    unknown = _file(browser, 'never-recorded-run', None)
    assert unknown.status_code == 404
    assert unknown.json()['detail']['error_kind'] == 'run_not_found'


def test_the_file_refuses_like_the_run_view_once_access_is_lost(parity, client, session_ctx,
                                                                 _auth_db, seeded_identity):
    """Revoked, never a member and a never-recorded run refuse byte-equal with the run view."""
    import uuid
    from datetime import UTC, datetime

    from fastapi.testclient import TestClient
    from sqlalchemy import delete

    import main
    from db.models import OrgMembership, User
    from tests.conftest import attach_session

    run_id, browser = parity['run_id'], parity['browser']
    ctx = session_ctx(client)
    _engine, session_local = _auth_db
    outsider_id = uuid.uuid4()
    with session_local() as db:
        db.add(User(id=outsider_id, email=f'outsider-{outsider_id.hex[:8]}@example.com',
                    password_hash=None, status='active', is_super_admin=False,
                    created_at=datetime.now(tz=UTC)))
        db.flush()
        db.add(OrgMembership(id=uuid.uuid4(), user_id=outsider_id,
                             org_id=seeded_identity['org_id'], role='member'))
        db.commit()
    seen = {}
    try:
        outsider = attach_session(TestClient(main.app), session_local, outsider_id)
        assert outsider.post(f'/api/projects/{ctx.project_uuid}/activate').status_code == 404
        seen['never existed'] = outsider, 'never-recorded-run'
        seen['never a member'] = outsider, run_id
        refusals = {case: (_file(who, rid, 'results_capacity'),
                           _browser_get(who, '/api/results/run-view',
                                        params={'run_id': rid, 'section': 'results_capacity'}))
                    for case, (who, rid) in seen.items()}
        parity['revoke']()
        refusals['revoked'] = (_file(browser, run_id, 'results_capacity'),
                               _browser_get(browser, '/api/results/run-view',
                                            params={'run_id': run_id, 'section': 'results_capacity'}))
    finally:
        with session_local() as db:
            db.execute(delete(OrgMembership).where(OrgMembership.user_id == outsider_id))
            row = db.get(User, outsider_id)
            if row is not None:
                db.delete(row)
            db.commit()
    for case, (file, view) in refusals.items():
        assert file.status_code == 409, (case, file.text)
        assert file.json() == view.json() == {'detail': {
            'error_kind': 'run_project_required', 'message': run_evidence.NO_PROJECT_OPEN}}, case
        assert run_id not in file.text


def test_the_workbook_names_the_run_it_holds(parity):
    browser = parity['browser']
    latest = _latest()
    # A browser request has no acting user of its own: the route reads the
    # runs as its authenticated reader (it named none before, in the real app).
    previous = chat_tools.acting_user_id()
    chat_tools.set_acting_user(None)
    try:
        views = _browser_get(browser, '/api/results/workbook/views')
        book = _browser_get(browser, WORKBOOK)
    finally:
        chat_tools.set_acting_user(previous)
    assert views.status_code == 200, views.text
    run = views.json()['run']
    assert run['run_id'] == latest and run['started_at']
    assert book.status_code == 200, book.text
    assert book.headers['x-workbook-run-id'] == latest
    about = dict(row[:2] for row in _sheets(book.content)['About'] if len(row) >= 2)
    assert about['Run'] == latest


def test_the_workbook_refuses_a_past_run_on_the_route(parity):
    run_id, browser = parity['run_id'], parity['browser']
    refused = _browser_get(browser, WORKBOOK, params={'run_id': run_id})
    assert refused.status_code == 409, refused.text
    detail = refused.json()['detail']
    assert detail['error_kind'] == 'results_workbook_past_run'
    assert detail['run_id'] == run_id and detail['workbook_run_id'] == _latest()
    assert 'export_run_evidence' in detail['message']
    unknown = _browser_get(browser, WORKBOOK, params={'run_id': 'never-recorded-run'})
    assert unknown.status_code == 404
    assert unknown.json()['detail']['error_kind'] == 'run_not_found'


def test_the_workbook_refuses_a_past_run_in_the_chat_tool(parity):
    run_id = parity['run_id']
    with pytest.raises(HTTPException) as refused:
        chat_tools.DISPATCHERS['export_results_workbook'](run_id=run_id)
    assert refused.value.status_code == 409
    assert refused.value.detail['error_kind'] == 'results_workbook_past_run'
    with pytest.raises(HTTPException) as unknown:
        chat_tools.DISPATCHERS['export_results_workbook'](run_id='never-recorded-run')
    assert unknown.value.detail['error_kind'] == 'run_not_found'


def test_the_latest_run_id_changes_nothing_in_the_workbook(parity):
    browser = parity['browser']
    latest = _latest()
    plain = _browser_get(browser, WORKBOOK)
    named = _browser_get(browser, WORKBOOK, params={'run_id': latest})
    assert plain.status_code == named.status_code == 200, (plain.text, named.text)
    assert _sheets(plain.content) == _sheets(named.content)
    assert plain.headers['x-workbook-run-id'] == latest
    meta = chat_tools.DISPATCHERS['export_results_workbook'](run_id=latest)
    assert meta['run_id'] == latest
