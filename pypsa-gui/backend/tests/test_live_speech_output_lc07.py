"""LC-07 PR2 — speech output (LC-T14).

Spoken text is the caption byte for byte; only a completed checked answer of a
turn started by a speech final is spoken; barge-in cancels playback only; TTS
characters settle once through the speech session's ledger. Offline: the
transport and the TTS endpoint are test doubles.
"""
from __future__ import annotations

import asyncio
import hashlib
import json

import httpx
import pytest

from harness.providers import live_media
from services import chat_service
from services import live_speech as ls
from services import utterance_admission as ua
from tests.test_live_speech_session_lc07 import (  # noqa: F401 — _reset is autouse
    FakeTransport,
    _final,
    _refusal,
    _reset,
    _settled_once,
    _start,
    _thread,
    _thread_json,
)

# Engineering numbers and units, non-ASCII included: spoken exactly as shown.
ANSWER = 'Battery p_nom is 7.5 MW; line loading 98.4 % at 13:00 (≈ 1 200 €/kW).'


@pytest.fixture
def transport():
    fake = FakeTransport()
    ls.configure_transport(fake)
    return fake


class FakeTurn:
    def __init__(self, text: str, answer_id: str = 'ans-1') -> None:
        self.approved = {'answer_id': answer_id, 'text': text}

    def project_frame(self, event, payload):
        return payload


def _checked_frames(text=ANSWER, answer_id='ans-1'):
    return [
        ('answer_approved', {'answer_id': answer_id}),
        ('token', {'delta': text}),
        ('turn_done', {'usage': {}, 'answer_id': answer_id}),
    ]


def _run(monkeypatch, episode, message, *, utterance_id, generation, frames,
         approved=ANSWER, before=None):
    """Drive run_turn with a scripted body; returns the client's frames."""
    from harness import attention, loop

    def body(session, _message, **_kwargs):
        session.assurance_turn = FakeTurn(approved) if approved is not None else None
        if before is not None:
            before(session)
        yield from frames

    monkeypatch.setattr(loop, '_run_turn_body', body)
    monkeypatch.setattr(attention, 'checked_answer', lambda *a, **k: None)
    return list(loop.run_turn(episode, message, client=object(),
                              utterance_id=utterance_id, generation=generation))


def _spoken_turn(monkeypatch, *, frames=None, approved=ANSWER, before=None,
                 text='what is the battery size'):
    tid, gen = _thread()
    out, sid, token = _start(tid, gen)
    episode = chat_service.get_session(out['session']['episode_id'])
    accepted = _final(tid, token, sid, 's1', 1, text)
    got = _run(monkeypatch, episode, text, utterance_id='speech:s1',
               generation=accepted['generation'],
               frames=_checked_frames() if frames is None else frames,
               approved=approved, before=before)
    return tid, sid, token, episode, got


def _caption(frames):
    return ''.join(p['delta'] for e, p in frames if e == 'token')


# ── spoken text is the caption ─────────────────────────────────────────────

def test_checked_answer_is_spoken_byte_equal_to_its_caption(transport, monkeypatch):
    tid, sid, token, _, frames = _spoken_turn(monkeypatch)
    claimed = ls.claim_output(tid, token=token, session_id=sid)
    assert claimed['status'] == 'speak'
    spoken = claimed['output']['text']
    assert spoken.encode('utf-8') == _caption(frames).encode('utf-8') == ANSWER.encode('utf-8')
    assert claimed['output']['sha256'] == hashlib.sha256(ANSWER.encode('utf-8')).hexdigest()
    assert claimed['output']['answer_id'] == 'ans-1'
    assert claimed['language'] == 'en'
    # Each answer is spoken at most once.
    assert ls.claim_output(tid, token=token, session_id=sid) == {'status': 'empty'}


def test_synthesis_sends_the_caption_verbatim(transport, monkeypatch):
    tid, sid, token, _, _ = _spoken_turn(monkeypatch)
    output = ls.claim_output(tid, token=token, session_id=sid)['output']
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, content=b'RIFF', headers={'content-type': 'audio/wav'})

    profile = live_media.SpeechProfile(
        name='fake', stt_endpoint='https://stt.invalid', stt_model='m',
        tts_endpoint='https://tts.invalid', tts_model='m', tts_voice='v',
    )
    tts = live_media.HttpTtsAdapter(
        profile, credential=lambda: 'k',
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    audio = asyncio.run(ls.synthesize_output(tts, output, language='en'))
    assert audio.mime == 'audio/wav'
    assert sent[0]['input'].encode('utf-8') == ANSWER.encode('utf-8')

    tampered = {**output, 'text': ANSWER.replace('7.5', '75')}
    with pytest.raises(live_media.LiveMediaError):
        asyncio.run(ls.synthesize_output(tts, tampered, language='en'))
    assert len(sent) == 1


# ── never spoken: unapproved, incomplete, checked-but-pending ──────────────

@pytest.mark.parametrize('case', [
    'legacy_unvalidated', 'approved_never_completed', 'error_after_approval',
    'prose_before_approval', 'caption_differs', 'turn_done_other_answer',
])
def test_text_that_is_not_a_completed_checked_answer_is_never_spoken(
    transport, monkeypatch, case,
):
    approved = ANSWER
    if case == 'legacy_unvalidated':
        approved = None
        frames = [('token', {'delta': ANSWER}), ('turn_done', {'usage': {}})]
    elif case == 'approved_never_completed':  # checked, publication still pending
        frames = _checked_frames()[:2] + [('session_done', {'reason': 'aborted'})]
    elif case == 'error_after_approval':
        frames = _checked_frames()[:2] + [
            ('error', {'error_kind': 'assurance_publication_failed', 'message': 'x'}),
            ('turn_done', {'usage': {}, 'answer_id': 'ans-1'}),
        ]
    elif case == 'prose_before_approval':
        frames = [('token', {'delta': 'Thinking… '})] + _checked_frames()
    elif case == 'caption_differs':
        frames = _checked_frames(text=ANSWER.replace('7.5 MW', '75 MW'))
    else:
        frames = _checked_frames()[:2] + [('turn_done', {'usage': {}, 'answer_id': 'ans-2'})]
    tid, sid, token, _, _ = _spoken_turn(monkeypatch, frames=frames, approved=approved)
    assert ls.claim_output(tid, token=token, session_id=sid) == {'status': 'empty'}
    assert _thread_json(tid)['speech']['usage']['tts'] == 0


def test_aborted_turn_is_not_spoken(transport, monkeypatch):
    tid, sid, token, _, _ = _spoken_turn(
        monkeypatch, before=lambda session: session.abort_event.set(),
    )
    assert ls.claim_output(tid, token=token, session_id=sid) == {'status': 'empty'}


def test_typed_turn_is_not_spoken(transport, monkeypatch):
    tid, gen = _thread()
    out, sid, token = _start(tid, gen)
    episode = chat_service.get_session(out['session']['episode_id'])
    _run(monkeypatch, episode, 'what is the battery size', utterance_id='typed-1',
         generation=ua._DEFAULT.generation(episode.session_id), frames=_checked_frames())
    assert ls.claim_output(tid, token=token, session_id=sid) == {'status': 'empty'}


def test_stale_generation_answer_is_never_spoken(transport, monkeypatch):
    # Superseded while the turn ran: never queued.
    tid, sid, token, _, _ = _spoken_turn(
        monkeypatch,
        before=lambda session: ua._DEFAULT.bump_generation(session_id=session.session_id),
    )
    assert not ls._OUTPUTS.get(sid)
    assert ls.claim_output(tid, token=token, session_id=sid) == {'status': 'empty'}


def test_queued_answer_superseded_before_playback_is_dropped(transport, monkeypatch):
    tid, sid, token, episode, _ = _spoken_turn(monkeypatch)
    ua._DEFAULT.bump_generation(session_id=episode.session_id)
    assert ls.claim_output(tid, token=token, session_id=sid) == {'status': 'empty'}
    assert _thread_json(tid)['speech']['usage']['tts'] == 0


def test_closing_the_session_drops_unspoken_answers(transport, monkeypatch):
    from services import durable_conversation as dc

    tid, sid, token, _, _ = _spoken_turn(monkeypatch)
    dc.rebind_scope(tid, generation=_thread_json(tid)['generation'])
    assert sid not in ls._OUTPUTS
    status, detail = _refusal(ls.claim_output, tid, token=token, session_id=sid)
    assert status in {401, 409}


# ── barge-in cancels playback only ─────────────────────────────────────────

def test_barge_in_cancels_playback_but_not_the_running_turn(transport, monkeypatch):
    from services import chat_service as cs

    tid, gen = _thread()
    out, sid, token = _start(tid, gen)
    episode = cs.get_session(out['session']['episode_id'])
    # Two answers: one claimed (playing), one queued.
    for seg, seq in (('s1', 1), ('s2', 2)):
        accepted = _final(tid, token, sid, seg, seq, f'question {seg}')
        _run(monkeypatch, episode, f'question {seg}', utterance_id=f'speech:{seg}',
             generation=accepted['generation'], frames=_checked_frames(answer_id='ans-1'))
    playing = ls.claim_output(tid, token=token, session_id=sid)['output']

    # A turn is running and holds an admission lease while the user barges in.
    accepted = _final(tid, token, sid, 's3', 3, 'question s3')
    assert ls.redeem_final(episode.session_id, 'speech:s3', 'question s3',
                           accepted['generation']) is None
    decision = ua.admit_utterance(ua.StableUtterance(
        utterance_id='speech:s3', text='question s3',
        generation=accepted['generation'], session_id=episode.session_id,
    ))
    lease = ua._DEFAULT.active_lease(episode.session_id)
    assert decision.status == 'admitted' and lease
    episode._turn_in_flight = True

    result = ls.barge_in(tid, token=token, session_id=sid)

    assert result['status'] == 'cancelled'
    assert playing['output_id'] in result['cancelled'] and len(result['cancelled']) == 2
    assert not episode.abort_event.is_set()
    assert episode._turn_in_flight is True
    assert ua._DEFAULT.active_lease(episode.session_id) == lease
    assert ua._DEFAULT.generation(episode.session_id) == accepted['generation']
    assert _thread_json(tid)['speech']['state'] == 'open'
    # Late audio is flushed: nothing cancelled can be claimed again.
    assert ls.claim_output(tid, token=token, session_id=sid) == {'status': 'empty'}
    # The running turn still publishes and is spoken.
    episode._turn_in_flight = False
    ls.queue_answer(episode.session_id, 'speech:s3', 'ans-3', 'Loading is 98 %.')
    assert ls.claim_output(tid, token=token, session_id=sid)['output']['text'] == 'Loading is 98 %.'
    # Charged characters are not refunded by a barge-in.
    assert _thread_json(tid)['speech']['usage']['tts'] == len(ANSWER) + len('Loading is 98 %.')


# ── TTS characters settle once through the session ledger ──────────────────

def test_tts_characters_settle_once_through_the_ledger(transport, monkeypatch):
    tid, sid, token, _, _ = _spoken_turn(monkeypatch)
    out = ls.claim_output(tid, token=token, session_id=sid)['output']
    assert _thread_json(tid)['speech']['usage']['tts'] == len(ANSWER)
    # The worker's own report under the same id is a duplicate.
    dup = ls.report_usage(tid, token=token, session_id=sid, usage_id=f"tts-{out['output_id']}",
                          kind='tts', amount=len(ANSWER), unit='characters')
    assert dup['status'] == 'duplicate'
    gen = _thread_json(tid)['generation']
    assert ls.end(tid, generation=gen, session_id=sid)['status'] == 'closing'
    ls.report_usage(tid, token=token, session_id=sid, usage_id='fin', kind='media',
                    amount=3, unit='media_seconds', final=True)
    charges = _settled_once(tid, sid)
    assert charges['tts']['amount'] == len(ANSWER)
    assert charges['tts']['unit'] == 'characters'
    late = ls.report_usage(tid, token=token, session_id=sid, usage_id='late', kind='tts',
                           amount=5, unit='characters')
    assert late == {'status': 'ignored', 'reason': 'settled'}
    assert _settled_once(tid, sid)['tts']['amount'] == len(ANSWER)


def test_answer_beyond_the_tts_reservation_is_not_spoken(transport, monkeypatch):
    monkeypatch.setitem(ls.RESERVATIONS, 'tts', (10, 'characters'))
    tid, sid, token, _, _ = _spoken_turn(monkeypatch)
    claimed = ls.claim_output(tid, token=token, session_id=sid)
    assert claimed['status'] == 'over_budget'
    assert _thread_json(tid)['speech']['usage']['tts'] == 0
    assert ls.claim_output(tid, token=token, session_id=sid) == {'status': 'empty'}


# ── routes ─────────────────────────────────────────────────────────────────

def test_output_routes_need_the_bridge_token_and_the_flag(client, transport, monkeypatch):
    tid, sid, token, _, _ = _spoken_turn(monkeypatch)
    base = f'/api/durable-conversations/{tid}/speech'
    for path in ('/output/claim', '/barge-in'):
        bare = client.post(base + path, json={'session_id': sid})
        assert bare.status_code == 401, path
    claimed = client.post(base + '/output/claim', headers={'X-Bridge-Token': token},
                          json={'session_id': sid})
    assert claimed.status_code == 200 and claimed.json()['output']['text'] == ANSWER
    barged = client.post(base + '/barge-in', headers={'X-Bridge-Token': token},
                         json={'session_id': sid})
    assert barged.json()['cancelled'] == [claimed.json()['output']['output_id']]
    monkeypatch.delenv(ls.ENABLE_ENV)
    for path in ('/output/claim', '/barge-in'):
        off = client.post(base + path, headers={'X-Bridge-Token': token},
                          json={'session_id': sid})
        assert (off.status_code, off.json()['detail']['error_kind']) == (503, 'speech_disabled')


def test_bridge_calls_refuse_on_a_closed_session(transport, monkeypatch):
    tid, sid, token, _, _ = _spoken_turn(monkeypatch)
    gen = _thread_json(tid)['generation']
    ls.report_usage(tid, token=token, session_id=sid, usage_id='fin', kind='media',
                    amount=1, unit='media_seconds', final=True)
    ls.end(tid, generation=gen, session_id=sid)
    status, detail = _refusal(ls.claim_output, tid, token=token, session_id=sid)
    assert (status, detail['error_kind']) == (409, 'speech_session_closed')
    assert ls.barge_in(tid, token=token, session_id=sid) == {'status': 'ignored', 'cancelled': []}
