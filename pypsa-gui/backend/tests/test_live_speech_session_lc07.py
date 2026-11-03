"""LC-07 PR1 — speech session lifecycle and resources (LC-T13, LC-T15).

Offline only: the media transport is a test double behind the
``live_media.MediaTransport`` seam. Nothing here reaches a vendor.
"""
from __future__ import annotations

import time

import pytest
from fastapi import HTTPException

from services import chat_service
from services import durable_conversation as dc
from services import live_speech as ls
from services import utterance_admission as ua

OWNER = 'owner-aaa'
PROJECT = '11111111-1111-1111-1111-111111111111'
MEDIA_SECRET = 'media-secret-0123456789abcdef'


class FakeTransport:
    name = 'fake'

    def __init__(self, *, fail=False, ttl=None):
        self.fail = fail
        self.ttl = ttl
        self.opened: list[dict] = []
        self.closed: list[str] = []

    def open_session(self, *, room, identity, ttl_s, bridge_token, language):
        if self.fail:
            raise RuntimeError('transport down')
        self.opened.append({'room': room, 'identity': identity,
                            'bridge_token': bridge_token, 'language': language})
        from harness.providers.live_media import MediaGrant
        return MediaGrant(url='wss://media.invalid', token=MEDIA_SECRET,
                          expires_at=time.time() + (self.ttl or ttl_s))

    def close_session(self, *, room):
        self.closed.append(room)


@pytest.fixture(autouse=True)
def _reset(tmp_path, monkeypatch):
    dc._reset_for_tests()
    chat_service._reset_sessions_for_tests()
    ua._reset_for_tests()
    ls._reset_for_tests()
    root = tmp_path / PROJECT
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(dc, '_authority', lambda: (root, PROJECT, OWNER))
    monkeypatch.setenv(ls.ENABLE_ENV, '1')
    yield
    ls._reset_for_tests()
    dc._reset_for_tests()
    chat_service._reset_sessions_for_tests()
    ua._reset_for_tests()


@pytest.fixture
def transport():
    fake = FakeTransport()
    ls.configure_transport(fake)
    return fake


def _thread():
    tid = dc.create_thread()['thread_id']
    thread, _ = dc.resume_episode(tid, generation=1)
    return tid, thread['generation']


def _start(tid, gen):
    out = ls.start(tid, generation=gen, language='en')
    token = ls._TRANSPORT.opened[-1]['bridge_token']
    return out, out['session']['session_id'], token


def test_partial_never_admits_and_final_issues_one_ticket(transport):
    tid, gen = _thread()
    out, sid, token = _start(tid, gen)
    partial = ls.submit_segment(tid, token=token, session_id=sid, segment_id='s1',
                                seq=1, text='set the battery to 7', final=False)
    assert partial['status'] == 'partial_ignored'
    assert ua._DEFAULT.active_utterance(out['session']['episode_id']) is None
    final = ls.submit_segment(tid, token=token, session_id=sid, segment_id='s1',
                              seq=2, text='set the battery to 7.5 MW', final=True)
    assert final['status'] == 'final_accepted'
    assert final['utterance_id'] == 'speech:s1'
    again = ls.submit_segment(tid, token=token, session_id=sid, segment_id='s1',
                              seq=3, text='set the battery to 7.5 MW', final=True)
    assert again['status'] == 'duplicate'
    episode = out['session']['episode_id']
    assert ls.redeem_final(episode, 'speech:s1', 'set the battery to 7.5 MW') is None
    assert ls.redeem_final(episode, 'speech:s1', 'set the battery to 7.5 MW')['error_kind'] == 'speech_final_unknown'


# ── helpers ────────────────────────────────────────────────────────────────

def _file(tid):
    root, _, _ = dc._authority()
    return root / 'durable_conversations' / f'{tid}.json'


def _thread_json(tid):
    import json
    return json.loads(_file(tid).read_text(encoding='utf-8'))


def _final(tid, token, sid, seg, seq, text):
    return ls.submit_segment(tid, token=token, session_id=sid, segment_id=seg,
                             seq=seq, text=text, final=True)


def _refusal(call, *args, **kwargs):
    with pytest.raises(HTTPException) as failure:
        call(*args, **kwargs)
    return failure.value.status_code, failure.value.detail


def _settled_once(tid, sid):
    budgets = _thread_json(tid)['budgets']
    keys = {f'speech-{sid}-{k}' for k in ls.RESERVATIONS}
    charges = [c for c in budgets['final_charges'] if c['key'] in keys]
    assert sorted(c['key'] for c in charges) == sorted(keys)
    assert not [r for r in budgets['reservations'] if r['key'] in keys]
    return {c['key'].rsplit('-', 1)[1]: c for c in charges}


# ── LC-T13: finalized speech and corrections ───────────────────────────────

def test_out_of_order_final_is_refused_and_writes_nothing(transport):
    tid, gen = _thread()
    _, sid, token = _start(tid, gen)
    assert _final(tid, token, sid, 's2', 5, 'show line loading')['status'] == 'final_accepted'
    before = _file(tid).read_bytes()
    status, detail = _refusal(_final, tid, token, sid, 's1', 4, 'show bus voltages')
    assert (status, detail['error_kind']) == (409, 'speech_final_out_of_order')
    assert _file(tid).read_bytes() == before


def test_final_after_rebind_uses_a_revoked_token_and_gets_no_ticket(transport):
    tid, gen = _thread()
    out, sid, token = _start(tid, gen)
    dc.rebind_scope(tid, generation=gen)
    status, detail = _refusal(_final, tid, token, sid, 's1', 1, 'run the load flow')
    assert status == 401
    assert detail['error_kind'] in {'bridge_credentials_invalid', 'bridge_credentials_stale'}
    episode = out['session']['episode_id']
    assert ls.redeem_final(episode, 'speech:s1', 'run the load flow') is not None


def test_spoken_yes_never_approves_a_pending_card(transport):
    tid, gen = _thread()
    out, sid, token = _start(tid, gen)
    episode = chat_service.get_session(out['session']['episode_id'])
    episode.pending_confirmations['card-1'] = object()
    for seq, word in enumerate(['yes', 'Confirm.', 'approve', 'yes please'], start=1):
        result = _final(tid, token, sid, f'y{seq}', seq, word)
        assert result['status'] == 'not_an_approval'
        assert ls.redeem_final(episode.session_id, f'speech:y{seq}', word) is not None
    assert list(episode.pending_confirmations) == ['card-1']


def test_yes_without_a_pending_card_is_an_ordinary_final(transport):
    tid, gen = _thread()
    _, sid, token = _start(tid, gen)
    assert _final(tid, token, sid, 'y1', 1, 'yes')['status'] == 'final_accepted'


@pytest.mark.parametrize('text', [
    'set the battery to 7.5, no, 75 MW',
    'limit it to 7.5 MW not 75 MW',
    'make it 110 kV, I mean 220 kV',
])
def test_one_segment_correcting_a_number_asks_which_value(transport, text):
    tid, gen = _thread()
    out, sid, token = _start(tid, gen)
    result = _final(tid, token, sid, 'q1', 1, text)
    assert result['status'] == 'clarify'
    assert result['text'] == text
    assert ls.redeem_final(out['session']['episode_id'], 'speech:q1', text) is not None


@pytest.mark.parametrize('text', [
    'set the battery to 7.5 MW',
    'between 5 and 10 MW',
    'compare line 5 not line 6',
    'set 7.5 MW, no, 7.5 MW',
    '0,5 pu',
])
def test_plain_quantities_are_not_flagged(transport, text):
    tid, gen = _thread()
    out, sid, token = _start(tid, gen)
    result = _final(tid, token, sid, 'p1', 1, text)
    assert result['status'] == 'final_accepted'
    assert result['text'] == text  # verbatim, numbers and units untouched


def test_run_turn_admits_only_the_redeemed_final_text(transport):
    from harness import loop

    tid, gen = _thread()
    out, sid, token = _start(tid, gen)
    episode = chat_service.get_session(out['session']['episode_id'])
    accepted = _final(tid, token, sid, 's1', 1, 'set the battery to 7.5 MW')

    # A partial or unknown id never reaches admission.
    frames = list(loop.run_turn(episode, 'set the battery to 7', client=object(),
                                utterance_id='speech:s9', generation=accepted['generation']))
    assert frames[0] == ('error', {
        'error_kind': 'speech_final_unknown',
        'message': 'This speech was not a finalized utterance for this conversation; '
                   'nothing was sent.',
    })
    assert ua._DEFAULT.active_utterance(episode.session_id) is None

    # Altered text spends the ticket and admits nothing.
    frames = list(loop.run_turn(episode, 'set the battery to 75 MW', client=object(),
                                utterance_id='speech:s1', generation=accepted['generation']))
    assert frames[0][1]['error_kind'] == 'speech_final_mismatch'
    assert ua._DEFAULT.active_utterance(episode.session_id) is None


# ── LC-T15: media ownership and resource bounds ────────────────────────────

def test_flag_off_refuses_every_speech_route(transport, monkeypatch):
    tid, gen = _thread()
    monkeypatch.delenv(ls.ENABLE_ENV)
    calls = [
        lambda: ls.start(tid, generation=gen, language='en'),
        lambda: ls.state(tid),
        lambda: ls.end(tid, generation=gen, session_id='abc'),
        lambda: ls.submit_segment(tid, token='t', session_id='abc', segment_id='s',
                                  seq=1, text='x', final=True),
        lambda: ls.report_usage(tid, token='t', session_id='abc', usage_id='u',
                                kind='stt', amount=1, unit='audio_seconds'),
    ]
    for call in calls:
        status, detail = _refusal(call)
        assert (status, detail['error_kind']) == (503, 'speech_disabled')
    assert 'speech' not in _thread_json(tid)
    assert transport.opened == []


def test_no_transport_configured_refuses_start():
    tid, gen = _thread()
    status, detail = _refusal(ls.start, tid, generation=gen, language='en')
    assert (status, detail['error_kind']) == (503, 'speech_not_configured')


def test_second_process_holding_the_store_refuses_start(transport, tmp_path):
    from services import app_workers
    from tests.test_durable_conversation_one_worker import _spawn_holder

    tid, gen = _thread()
    before = _file(tid).read_bytes()
    app_workers._reset_for_tests()  # this process lets go of the store lock
    holder = _spawn_holder(_file(tid).parent)
    try:
        status, detail = _refusal(ls.start, tid, generation=gen, language='en')
        assert (status, detail['error_kind']) == (503, 'multi_worker_unsupported')
        assert _file(tid).read_bytes() == before
        assert transport.opened == []
    finally:
        holder.kill()
        holder.wait()


def test_unknown_other_owner_and_other_project_refuse_alike(transport, monkeypatch, tmp_path):
    tid, gen = _thread()
    _start(tid, gen)
    unknown = _refusal(ls.state, '22222222-2222-2222-2222-222222222222')
    malformed = _refusal(ls.state, 'not-a-uuid')
    monkeypatch.setattr(dc, '_authority', lambda: (tmp_path / PROJECT, PROJECT, 'owner-bbb'))
    other_owner = _refusal(ls.state, tid)
    other_owner_start = _refusal(ls.start, tid, generation=gen, language='en')
    other_root = tmp_path / 'other'
    (other_root / 'durable_conversations').mkdir(parents=True)
    _file_text = (tmp_path / PROJECT / 'durable_conversations' / f'{tid}.json').read_bytes()
    (other_root / 'durable_conversations' / f'{tid}.json').write_bytes(_file_text)
    monkeypatch.setattr(dc, '_authority', lambda: (other_root, 'project-other', OWNER))
    other_project = _refusal(ls.state, tid)
    assert unknown == malformed == other_owner == other_owner_start == other_project
    assert unknown[0] == 404 and unknown[1]['error_kind'] == 'thread_not_found'


def test_stale_generation_start_is_409_and_writes_nothing(transport):
    tid, gen = _thread()
    before = _file(tid).read_bytes()
    status, detail = _refusal(ls.start, tid, generation=gen - 1, language='en')
    assert (status, detail['error_kind']) == (409, 'stale_generation')
    assert _file(tid).read_bytes() == before
    assert transport.opened == []


def test_media_credential_is_returned_once_and_never_stored_or_logged(transport, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    tid, gen = _thread()
    out, sid, token = _start(tid, gen)
    assert out['media']['token'] == MEDIA_SECRET
    assert 0 < out['media']['expires_at'] - time.time() <= ls.MAX_CREDENTIAL_TTL_S
    _final(tid, token, sid, 's1', 1, 'show results')
    ls.end(tid, generation=gen, session_id=sid)
    assert MEDIA_SECRET not in _file(tid).read_text(encoding='utf-8')
    assert MEDIA_SECRET not in caplog.text
    assert MEDIA_SECRET not in repr(ls.state(tid))
    assert MEDIA_SECRET not in repr(dc.get_thread(tid))


def test_grant_beyond_the_ttl_bound_is_refused_and_settled(monkeypatch):
    long_lived = FakeTransport(ttl=3600)
    ls.configure_transport(long_lived)
    tid, gen = _thread()
    status, detail = _refusal(ls.start, tid, generation=gen, language='en')
    assert (status, detail['error_kind']) == (502, 'speech_transport_failed')
    sid = _thread_json(tid)['speech']['session_id']
    charges = _settled_once(tid, sid)
    assert all(c['amount'] == 0 for c in charges.values())
    assert long_lived.closed == [f'pypsa-{sid}']


def test_reservation_is_durable_before_the_transport_opens(monkeypatch):
    seen = {}

    class Recording(FakeTransport):
        def open_session(self, **kwargs):
            tid = seen['tid']
            seen['reservations'] = [r['key'] for r in _thread_json(tid)['budgets']['reservations']]
            return super().open_session(**kwargs)

    ls.configure_transport(Recording())
    tid, gen = _thread()
    seen['tid'] = tid
    out, sid, _ = _start(tid, gen)
    assert sorted(seen['reservations']) == sorted(f'speech-{sid}-{k}' for k in ls.RESERVATIONS)


def _usage(tid, token, sid, uid, kind, amount, unit):
    return ls.report_usage(tid, token=token, session_id=sid, usage_id=uid,
                           kind=kind, amount=amount, unit=unit)


def test_end_settles_reported_usage_once_and_late_usage_is_a_no_op(transport):
    tid, gen = _thread()
    _, sid, token = _start(tid, gen)
    assert _usage(tid, token, sid, 'u1', 'stt', 12.5, 'audio_seconds')['status'] == 'recorded'
    assert _usage(tid, token, sid, 'u1', 'stt', 12.5, 'audio_seconds')['status'] == 'duplicate'
    _usage(tid, token, sid, 'u2', 'media', 30, 'media_seconds')
    closed = ls.end(tid, generation=gen, session_id=sid)
    assert closed['session']['closed_reason'] == 'end'
    charges = _settled_once(tid, sid)
    assert charges['stt']['amount'] == 12.5 and charges['media']['amount'] == 30
    assert charges['tts']['amount'] == 0
    before = _file(tid).read_bytes()
    late = _usage(tid, token, sid, 'u3', 'stt', 4, 'audio_seconds')
    assert late == {'status': 'ignored', 'reason': 'settled'}
    assert ls.end(tid, generation=gen, session_id=sid)['status'] == 'closed'
    assert _file(tid).read_bytes() == before
    assert transport.closed == [f'pypsa-{sid}']


@pytest.mark.parametrize('kind, amount, unit', [
    ('stt', float('nan'), 'audio_seconds'),
    ('stt', float('inf'), 'audio_seconds'),
    ('stt', -1, 'audio_seconds'),
    ('stt', True, 'audio_seconds'),
    ('stt', 3, 'media_seconds'),
    ('llm', 3, 'tokens'),
])
def test_invalid_usage_is_refused(transport, kind, amount, unit):
    tid, gen = _thread()
    _, sid, token = _start(tid, gen)
    before = _file(tid).read_bytes()
    status, detail = _refusal(_usage, tid, token, sid, 'u1', kind, amount, unit)
    assert (status, detail['error_kind']) == (422, 'speech_usage_invalid')
    assert _file(tid).read_bytes() == before


def test_usage_over_the_reservation_settles_at_the_cap_flagged(transport):
    tid, gen = _thread()
    _, sid, token = _start(tid, gen)
    cap = ls.RESERVATIONS['tts'][0]
    _usage(tid, token, sid, 'u1', 'tts', cap + 500, 'characters')
    ls.end(tid, generation=gen, session_id=sid)
    tts = _settled_once(tid, sid)['tts']
    assert tts['amount'] == cap and tts['flags'] == ['over_reservation']


def test_timeout_settles_once(transport, monkeypatch):
    tid, gen = _thread()
    _, sid, token = _start(tid, gen)
    later = time.time() + ls.MAX_SESSION_SECONDS + 1
    monkeypatch.setattr(ls.time, 'time', lambda: later)
    view = ls.state(tid)
    assert view['session']['closed_reason'] == 'timeout'
    charges = _settled_once(tid, sid)
    assert all(c['flags'] == ['usage_unverified'] for c in charges.values())
    ls.state(tid)
    _settled_once(tid, sid)


def test_rebind_closes_the_session_and_settles_once(transport):
    tid, gen = _thread()
    _, sid, token = _start(tid, gen)
    dc.rebind_scope(tid, generation=gen)
    assert _thread_json(tid)['speech']['closed_reason'] == 'rebind'
    _settled_once(tid, sid)
    assert transport.closed == [f'pypsa-{sid}']
    # A new start mints a new grant and a new bridge token at the new generation.
    out = ls.start(tid, generation=gen + 1, language='en')
    assert transport.opened[-1]['bridge_token'] != token
    assert out['session']['session_id'] != sid


def test_resume_closes_the_session_as_stale_generation(transport):
    tid, gen = _thread()
    _, sid, _ = _start(tid, gen)
    dc.resume_episode(tid, generation=gen)
    assert _thread_json(tid)['speech']['closed_reason'] == 'stale_generation'
    _settled_once(tid, sid)


def test_restart_takeover_settles_once(transport, monkeypatch):
    from tests.test_durable_conversation_restart_rotation import _die_and_restart

    tid, gen = _thread()
    _, sid, _ = _start(tid, gen)
    _die_and_restart(monkeypatch)
    dc.resume_episode(tid, generation=gen)
    assert _thread_json(tid)['speech']['closed_reason'] == 'restart'
    charges = _settled_once(tid, sid)
    assert all(c['flags'] == ['usage_unverified'] for c in charges.values())


def test_start_returns_and_clears_reconcile_items(transport):
    tid, gen = _thread()
    data = _thread_json(tid)
    data['reconcile'] = [{'tool': 'set_component', 'status': 'unverified'}]
    _file(tid).write_text(__import__('json').dumps(data), encoding='utf-8')
    out, _, _ = _start(tid, gen)
    assert out['reconcile'] == [{'tool': 'set_component', 'status': 'unverified'}]
    assert _thread_json(tid)['reconcile'] == []


def test_failed_start_leaves_reconcile_items(monkeypatch):
    ls.configure_transport(FakeTransport(fail=True))
    tid, gen = _thread()
    data = _thread_json(tid)
    data['reconcile'] = [{'tool': 'set_component', 'status': 'unverified'}]
    _file(tid).write_text(__import__('json').dumps(data), encoding='utf-8')
    status, detail = _refusal(ls.start, tid, generation=gen, language='en')
    assert detail['error_kind'] == 'speech_transport_failed'
    assert _thread_json(tid)['reconcile'] == [{'tool': 'set_component', 'status': 'unverified'}]
    # A refused start (session already open) leaves them too.
    ls.configure_transport(FakeTransport())
    ls.start(tid, generation=gen, language='en')
    data = _thread_json(tid)
    data['reconcile'] = [{'tool': 'x'}]
    _file(tid).write_text(__import__('json').dumps(data), encoding='utf-8')
    status, detail = _refusal(ls.start, tid, generation=gen, language='en')
    assert detail['error_kind'] == 'speech_session_open'
    assert _thread_json(tid)['reconcile'] == [{'tool': 'x'}]


def test_seams_are_protocols_without_vendor_imports():
    import sys

    from harness.providers import live_media

    assert isinstance(FakeTransport(), live_media.MediaTransport)
    assert not [m for m in sys.modules if m.split('.')[0] in {'livekit', 'deepgram'}]
    assert 'MEDIA' not in repr(live_media.MediaGrant(url='u', token='MEDIA', expires_at=1.0))


def test_speech_routes_over_http(client, transport, monkeypatch):
    tid, gen = _thread()
    started = client.post(f'/api/durable-conversations/{tid}/speech/start',
                          json={'generation': gen, 'language': 'en'})
    assert started.status_code == 200, started.text
    sid = started.json()['session']['session_id']
    token = transport.opened[-1]['bridge_token']
    seg = client.post(f'/api/durable-conversations/{tid}/speech/segment',
                      headers={'X-Bridge-Token': token},
                      json={'session_id': sid, 'segment_id': 's1', 'seq': 1,
                            'text': 'show line loading', 'final': True})
    assert seg.json()['status'] == 'final_accepted'
    no_token = client.post(f'/api/durable-conversations/{tid}/speech/usage',
                           json={'session_id': sid, 'usage_id': 'u1', 'kind': 'stt',
                                 'amount': 1, 'unit': 'audio_seconds'})
    assert no_token.status_code == 401
    got = client.get(f'/api/durable-conversations/{tid}/speech')
    assert got.json()['session']['state'] == 'open'
    assert MEDIA_SECRET not in got.text
    ended = client.post(f'/api/durable-conversations/{tid}/speech/end',
                        json={'generation': gen, 'session_id': sid})
    assert ended.json()['session']['closed_reason'] == 'end'
    monkeypatch.delenv(ls.ENABLE_ENV)
    off = client.get(f'/api/durable-conversations/{tid}/speech')
    assert (off.status_code, off.json()['detail']['error_kind']) == (503, 'speech_disabled')
