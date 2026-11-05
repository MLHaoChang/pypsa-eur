"""LC-07 PR4 — echo and overlap of finalized speech (LC-T13).

Our own answer heard back through the mic must not become a new utterance;
finals that overlap a running turn queue one correction, never a second turn.
Offline: the transport is a test double.
"""
from __future__ import annotations

import pytest

from services import durable_conversation as dc
from services import live_speech as ls
from services import utterance_admission as ua
from tests.test_live_speech_output_lc07 import (  # noqa: F401 — _reset is autouse
    ANSWER,
    _checked_frames,
    _reset,
    _spoken_turn,
)
from tests.test_live_speech_session_lc07 import (
    PROJECT,
    FakeTransport,
    _refusal,
    _final,
    _start,
    _thread,
    _thread_json,
)

ECHO = 'Battery p_nom is 7.5 MW and stores'


@pytest.fixture
def transport():
    fake = FakeTransport()
    ls.configure_transport(fake)
    return fake


def _speaking(monkeypatch, answer=ANSWER):
    """A spoken turn whose checked answer the worker has claimed (playing)."""
    tid, sid, token, episode, _ = _spoken_turn(
        monkeypatch, frames=_checked_frames(text=answer), approved=answer,
    )
    assert ls.claim_output(tid, token=token, session_id=sid)['status'] == 'speak'
    return tid, sid, token, episode


def _age(sid, seconds):
    for spoken in ls._RECENT_SPOKEN[sid]:
        spoken['claimed_at'] -= seconds


def test_echo_of_the_playing_answer_gets_no_ticket(transport, monkeypatch):
    tid, sid, token, episode = _speaking(monkeypatch)
    echo = _final(tid, token, sid, 's2', 2, ECHO)
    assert echo['status'] == 'echo_ignored'
    assert 'utterance_id' not in echo
    assert 'speech:s2' not in ls._TICKETS.get(episode.session_id, {})
    refused = ls.redeem_final(episode.session_id, 'speech:s2', ECHO,
                              ua._DEFAULT.generation(episode.session_id))
    assert refused['error_kind'] == 'speech_final_unknown'
    # Marked seen: a replay of the same segment is a duplicate, not a ticket.
    assert _final(tid, token, sid, 's2', 3, ECHO)['status'] == 'duplicate'


def test_same_words_after_playback_are_an_ordinary_final(transport, monkeypatch):
    tid, sid, token, _ = _speaking(monkeypatch)
    _age(sid, ls.ECHO_BASE_S + len(ANSWER) / ls.ECHO_CHARS_PER_S + 1)
    assert _final(tid, token, sid, 's2', 2, ECHO)['status'] == 'final_accepted'


def test_user_speech_over_playback_is_accepted(transport, monkeypatch):
    tid, sid, token, _ = _speaking(monkeypatch)
    accepted = _final(tid, token, sid, 's2', 2, 'stop and show me the line loading')
    assert accepted['status'] == 'final_accepted'


def test_echo_after_barge_in_is_still_ignored(transport, monkeypatch):
    tid, sid, token, _ = _speaking(monkeypatch)
    gen = _thread_json(tid)['generation']
    assert ls.barge_in(tid, token=token, session_id=sid, generation=gen)['status'] == 'cancelled'
    assert _final(tid, token, sid, 's2', 2, 'stores 1 200 kWh')['status'] == 'echo_ignored'


@pytest.mark.parametrize('text', ['7.5 MW', 'yes 7.5 MW', 'so it is 7.5 MW', 'MW and'])
def test_short_overlap_with_the_answer_is_not_echo(transport, monkeypatch, text):
    tid, sid, token, _ = _speaking(monkeypatch)
    assert _final(tid, token, sid, 's2', 2, text)['status'] in {'final_accepted', 'clarify'}


def test_words_out_of_order_are_not_echo(transport, monkeypatch):
    tid, sid, token, _ = _speaking(monkeypatch)
    text = 'MW 7.5 is battery'
    assert _final(tid, token, sid, 's2', 2, text)['status'] == 'final_accepted'


def test_session_exit_forgets_what_was_spoken(transport, monkeypatch):
    tid, sid, token, _ = _speaking(monkeypatch)
    assert ls._RECENT_SPOKEN.get(sid)
    monkeypatch.setattr(ls, 'USAGE_GRACE_S', 0.05)
    ls.end(tid, generation=_thread_json(tid)['generation'], session_id=sid)
    ls.wait_for_reaps(timeout=5)
    assert _thread_json(tid)['speech']['state'] == 'closed'
    assert sid not in ls._RECENT_SPOKEN


def test_overlapping_finals_queue_one_correction_never_a_second_turn(transport):
    tid, gen = _thread()
    out, sid, token = _start(tid, gen)
    episode_id = out['session']['episode_id']

    def admit(seg, seq, text):
        accepted = _final(tid, token, sid, seg, seq, text)
        uid, generation = accepted['utterance_id'], accepted['generation']
        assert ls.redeem_final(episode_id, uid, text, generation) is None
        return ua._DEFAULT.admit_utterance(
            ua.StableUtterance(uid, text, generation, episode_id))

    assert admit('s1', 1, 'run the load flow').status == 'admitted'
    lease = ua._DEFAULT.active_lease(episode_id)
    assert admit('s2', 2, 'for the winter week').status == 'queued'
    assert admit('s3', 3, 'no, the summer week').status == 'queued'
    # One running turn, one pending correction (the latest), same lease.
    assert ua._DEFAULT.active_lease(episode_id) == lease
    assert ua._DEFAULT.active_utterance(episode_id).utterance_id == 'speech:s1'
    assert ua._DEFAULT.pending_utterance_id(episode_id) == 'speech:s3'
    # A replayed final never reaches admission again.
    assert _final(tid, token, sid, 's2', 4, 'for the winter week')['status'] == 'duplicate'


# ── GK B (25) GO conditions E1–E6 ──────────────────────────────────────────

ASKS = 'I can raise p_nom to 9 MW. Shall I apply it? Say yes apply it, or press Confirm.'


def _window(chars):
    return ls.ECHO_BASE_S + chars / ls.ECHO_CHARS_PER_S


def test_echoed_approval_never_approves_a_pending_card(transport, monkeypatch):
    # E1: under 3 words the bare-approval rule guards (as in
    # test_spoken_yes_never_approves_a_pending_card); 3+ words are echo.
    tid, sid, token, episode = _speaking(monkeypatch, ASKS)
    episode.pending_confirmations['card-1'] = object()
    short = _final(tid, token, sid, 's2', 2, 'yes')
    assert short['status'] == 'not_an_approval'
    echo = _final(tid, token, sid, 's3', 3, 'yes apply it')
    assert echo['status'] == 'echo_ignored'
    assert 'speech:s3' not in ls._TICKETS.get(episode.session_id, {})
    assert list(episode.pending_confirmations) == ['card-1']
    # E6: the ignored echo leaves no state that blocks the user's own repeat.
    _age(sid, _window(len(ASKS)) + 1)
    again = _final(tid, token, sid, 's4', 4, 'yes apply it')
    assert again['status'] == 'final_accepted'
    assert list(episode.pending_confirmations) == ['card-1']


def test_a_one_word_final_is_never_echo(transport, monkeypatch):
    # E2: "yes" heard during an answer containing "yes" goes through the
    # ordinary rules (no card pending here: an ordinary final).
    tid, sid, token, _ = _speaking(monkeypatch, ASKS)
    assert _final(tid, token, sid, 's2', 2, 'yes')['status'] == 'final_accepted'
    assert _final(tid, token, sid, 's3', 3, 'apply it')['status'] == 'final_accepted'


def test_only_the_last_words_of_a_long_answer_are_kept(transport, monkeypatch):
    # E3: bounded per claim.
    monkeypatch.setattr(ls, 'ECHO_WORDS_MAX', 8)
    answer = 'one two three four five six seven eight nine ten eleven twelve'
    tid, sid, token, _ = _speaking(monkeypatch, answer)
    kept = ls._RECENT_SPOKEN[sid][-1]['words']
    assert kept == answer.split()[-8:]
    assert _final(tid, token, sid, 's2', 2, 'one two three')['status'] == 'final_accepted'
    assert _final(tid, token, sid, 's3', 3, 'ten eleven twelve')['status'] == 'echo_ignored'


def test_rebind_forgets_what_was_spoken(transport, monkeypatch):
    # E3: a rebind (generation change) closes the session and clears it.
    tid, sid, token, _ = _speaking(monkeypatch)
    dc.rebind_scope(tid, generation=_thread_json(tid)['generation'])
    assert sid not in ls._RECENT_SPOKEN


@pytest.mark.parametrize('offset, status', [(-0.5, 'echo_ignored'), (0.5, 'final_accepted')])
def test_echo_window_boundary(transport, monkeypatch, offset, status):
    # E5: just inside and just outside ECHO_BASE_S + chars / ECHO_CHARS_PER_S.
    tid, sid, token, _ = _speaking(monkeypatch)
    _age(sid, _window(len(ANSWER)) + offset)
    assert _final(tid, token, sid, 's2', 2, ECHO)['status'] == status


def test_echo_reply_and_log_hold_no_transcript(transport, monkeypatch, caplog):
    # E4 (backend half): the reply carries a fixed message only; nothing logs it.
    tid, sid, token, _ = _speaking(monkeypatch)
    with caplog.at_level('DEBUG'):
        echo = _final(tid, token, sid, 's2', 2, ECHO)
    assert echo['status'] == 'echo_ignored'
    assert 'text' not in echo and 'p_nom' not in echo['message']
    assert 'p_nom' not in caplog.text


# ── E4: transcript-free notices on the owner's GET /speech ─────────────────

def _notice_for(monkeypatch, status):
    tid, sid, token, episode = _speaking(monkeypatch)
    if status == 'echo_ignored':
        text = ECHO
    elif status == 'clarify':
        text = 'set the line to 300, no 400 MW'
    else:
        episode.pending_confirmations['card-1'] = object()
        text = 'yes please'
    assert _final(tid, token, sid, 's2', 2, text)['status'] == status
    return tid, sid, text


@pytest.mark.parametrize('status', ['echo_ignored', 'clarify', 'not_an_approval'])
def test_each_unadmitted_final_leaves_a_notice_without_transcript(
    transport, monkeypatch, status,
):
    tid, sid, text = _notice_for(monkeypatch, status)
    notices = ls.state(tid)['notices']
    assert [(n['status'], n['seq']) for n in notices] == [(status, 2)]
    assert set(notices[0]) == {'status', 'message', 'seq', 'generation'}
    assert notices[0]['generation'] == _thread_json(tid)['generation']
    blob = repr(notices).lower()
    assert text.lower() not in blob
    for word in _words_of(text):
        assert word not in blob


def _words_of(text):
    return [w for w in ls._words(text) if len(w) > 3]


def test_notices_are_bounded_oldest_first(transport, monkeypatch):
    tid, sid, token, _ = _speaking(monkeypatch)
    for seq in range(2, 2 + ls.NOTICES_MAX + 2):
        assert _final(tid, token, sid, f's{seq}', seq, ECHO)['status'] == 'echo_ignored'
    seqs = [n['seq'] for n in ls.state(tid)['notices']]
    assert seqs == list(range(4, 4 + ls.NOTICES_MAX))


def test_rebind_and_exit_clear_the_notices(transport, monkeypatch):
    tid, sid, _ = _notice_for(monkeypatch, 'echo_ignored')
    assert ls.state(tid)['notices']
    dc.rebind_scope(tid, generation=_thread_json(tid)['generation'])
    assert sid not in ls._NOTICES
    assert ls.state(tid)['notices'] == []


def test_older_generation_notices_are_not_shown(transport, monkeypatch):
    tid, sid, _ = _notice_for(monkeypatch, 'echo_ignored')
    for notice in ls._NOTICES[sid]:
        notice['generation'] -= 1
    assert ls.state(tid)['notices'] == []


def test_a_foreign_owner_gets_todays_refusal_and_no_notice(transport, monkeypatch, tmp_path):
    tid, _, _ = _notice_for(monkeypatch, 'echo_ignored')
    unknown = _refusal(ls.state, '22222222-2222-2222-2222-222222222222')
    root = dc._authority()[0]
    monkeypatch.setattr(dc, '_authority', lambda: (root, PROJECT, 'owner-bbb'))
    other_owner = _refusal(ls.state, tid)
    assert unknown == other_owner
    assert other_owner[0] == 404 and other_owner[1]['error_kind'] == 'thread_not_found'
