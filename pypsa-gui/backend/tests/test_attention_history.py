"""Decision history bounds, process recovery, privacy and unavailable storage."""
# ruff: noqa: F811 -- imported shared pytest fixture.
import concurrent.futures
import json
import os
import subprocess
import sys
import time
import uuid
from types import SimpleNamespace

import pytest

from harness import attention, session as sessions
from tests.test_assistant_decisions import origin, ask, inspect  # noqa: F401


def test_history_survives_a_new_process_without_recreating_sessions(client, origin, monkeypatch):
    ctx, owner, sid, _app = origin
    ask(monkeypatch, client, sid)
    sessions.drop_session(sid)
    code = """
import json, sys
from harness import attention, session
rows, warnings = attention.project_records(sys.argv[1], sys.argv[2], [])
assert session.get_session(sys.argv[3]) is None
print(json.dumps([rows, warnings]))
"""
    result = subprocess.run([sys.executable, '-c', code, owner, str(ctx.project_uuid), sid],
                            env=os.environ.copy(), text=True, capture_output=True, timeout=30, check=True)
    rows, warnings = json.loads(result.stdout)
    assert rows[0]['session_id'] == sid and rows[0]['question']['options'][0]['label'] == 'Battery'
    assert rows[0]['inspection_only'] is True and not warnings


@pytest.mark.parametrize('corruption', ['owner', 'version', 'shape', 'duplicate', 'status', 'question', 'nested', 'link'])
def test_corruption_is_visible_and_never_imported_or_repaired(client, origin, monkeypatch, corruption):
    _ctx, _owner, sid, app = origin
    ask(monkeypatch, client, sid)
    row = client.get('/api/chat/activity').json()['items'][0]
    path = list(app.rglob('decisions.json'))[0]
    data = json.loads(path.read_text())
    if corruption == 'owner':
        data['owner_id'] = str(uuid.uuid4())
    elif corruption == 'version':
        data['version'] = True
    elif corruption == 'shape':
        data['records'][0]['confirmation_token'] = 'must-not-be-imported'
    elif corruption == 'duplicate':
        data['records'] *= 2
    elif corruption == 'status':
        data['records'][0]['native_status'] = 'approved'
    elif corruption == 'question':
        data['records'][0]['question'] = None
    if corruption == 'link':
        other = app / 'outside.json'
        other.write_text(json.dumps(data))
        path.unlink()
        path.symlink_to(other)
    else:
        path.write_text('[' * 1200 if corruption == 'nested' else json.dumps(data))
    before = path.read_bytes()
    response = client.get('/api/chat/activity')
    assert response.status_code == 200
    assert response.json()['items'] == []
    assert 'decision_history_unavailable' in response.json()['warnings']
    assert inspect(client, row).status_code == 404
    ask(monkeypatch, client, sid)  # best-effort journal cannot break native question
    assert path.read_bytes() == before


def test_failed_history_write_cannot_block_native_choice_and_is_disclosed(client, origin, monkeypatch):
    from harness import owned_snapshots
    _ctx, _owner, sid, _app = origin
    def fail(*args, **kwargs):
        raise OSError('private-disk-error')
    monkeypatch.setattr(owned_snapshots, 'write', fail)
    response = ask(monkeypatch, client, sid)
    assert 'private-disk-error' not in response.text
    activity = client.get('/api/chat/activity')
    assert 'decision_history_unavailable' in activity.json()['warnings']
    assert 'private-disk-error' not in activity.text


def test_concurrent_questions_have_unique_origins_and_do_not_resolve_other_sessions(client, origin):
    ctx, owner, _sid, _app = origin
    def present(i):
        native = SimpleNamespace(session_id=f'concurrent-{i}', owner_user_id=owner)
        attention.question_presented(native, ctx.project_uuid,
            {'tool_use_id': 'reused-tool-id', 'question': 'Which basis?', 'options': [{'label': 'A'}]})
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(present, range(16)))
    rows, warnings = attention.project_records(owner, str(ctx.project_uuid), [])
    assert len(rows) == 16 and len({r['source_id'] for r in rows}) == 16 and not warnings
    attention.followed_up(SimpleNamespace(session_id='unrelated', owner_user_id=owner), ctx.project_uuid)
    assert all(r['native_status'] == 'waiting_for_input' for r in attention.project_records(owner, str(ctx.project_uuid), [])[0])
    attention.followed_up(SimpleNamespace(session_id='concurrent-0', owner_user_id=owner), ctx.project_uuid)
    rows, _ = attention.project_records(owner, str(ctx.project_uuid), [])
    assert sum(r['native_status'] == 'followed_up' for r in rows) == 1


def test_bounds_disclose_truncation_stable_reads_and_lazy_expiry(client, origin, monkeypatch):
    ctx, owner, sid, app = origin
    monkeypatch.setattr(attention, 'MAX_RECORDS', 2)
    for _ in range(3):
        ask(monkeypatch, client, sid)
    rows, warnings = attention.project_records(owner, str(ctx.project_uuid), [])
    assert len(rows) == 2 and 'decision_history_truncated' in warnings
    before = list(app.rglob('decisions.json'))[0].read_bytes()
    assert attention.project_records(owner, str(ctx.project_uuid), []) == (rows, warnings)
    assert list(app.rglob('decisions.json'))[0].read_bytes() == before
    now = time.time()
    monkeypatch.setattr(attention, 'time', SimpleNamespace(time=lambda: now + attention.RETENTION_SECONDS + 1))
    assert attention.project_records(owner, str(ctx.project_uuid), []) == ([], [])


def test_byte_bound_retains_newest_questions_and_warns(client, origin, monkeypatch):
    from harness import owned_snapshots
    ctx, owner, sid, app = origin
    monkeypatch.setattr(owned_snapshots, 'MAX_BYTES', 2400)
    for _ in range(4):
        ask(monkeypatch, client, sid)
    rows, warnings = attention.project_records(owner, str(ctx.project_uuid), [])
    assert rows and len(rows) < 4 and 'decision_history_truncated' in warnings
    assert list(app.rglob('decisions.json'))[0].stat().st_size <= 2400
