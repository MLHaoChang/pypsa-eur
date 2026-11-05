"""LC-07 PR4 — echo and overlap of finalized speech (LC-T13).

Our own answer heard back through the mic must not become a new utterance;
finals that overlap a running turn queue one correction, never a second turn.
Offline: the transport is a test double.
"""
from __future__ import annotations

import pytest

from services import live_speech as ls
from services import utterance_admission as ua
from tests.test_live_speech_output_lc07 import (  # noqa: F401 — _reset is autouse
    ANSWER,
    _reset,
    _spoken_turn,
)
from tests.test_live_speech_session_lc07 import (
    FakeTransport,
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


def _speaking(monkeypatch):
    """A spoken turn whose checked answer the worker has claimed (playing)."""
    tid, sid, token, episode, _ = _spoken_turn(monkeypatch)
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
