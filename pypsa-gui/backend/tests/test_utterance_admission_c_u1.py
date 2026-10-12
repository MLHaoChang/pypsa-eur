"""Issue 02 / C-U1 — stable utterance admission, one lease, bounded pending."""
from __future__ import annotations

import pytest

from services import utterance_admission as ua


@pytest.fixture(autouse=True)
def _reset():
    ua._reset_for_tests()
    yield
    ua._reset_for_tests()


def _utt(
    utterance_id: str,
    text: str = 'hello',
    *,
    generation: int = 1,
    session_id: str = 'sess-1',
) -> ua.StableUtterance:
    return ua.StableUtterance(
        utterance_id=utterance_id,
        text=text,
        generation=generation,
        session_id=session_id,
    )


def test_stable_utterance_admitted_once():
    coord = ua.TurnCoordinator()
    first = coord.admit_utterance(_utt('u1', 'Limit battery to 7.5 MW'))
    assert first.status == 'admitted'
    assert first.lease_id
    assert first.generation == 1
    second = coord.admit_utterance(_utt('u1', 'different text ignored'))
    assert second.status == 'duplicate'
    assert second.lease_id is None
    assert coord.active_lease('sess-1') == first.lease_id


def test_one_turn_lease_second_utterance_queued():
    coord = ua.TurnCoordinator()
    first = coord.admit_utterance(_utt('u1'))
    assert first.status == 'admitted'
    queued = coord.admit_utterance(_utt('u2', 'correction'))
    assert queued.status == 'queued'
    assert queued.lease_id is None
    assert queued.pending_utterance_id == 'u2'
    assert coord.active_lease('sess-1') == first.lease_id


def test_bounded_pending_correction_keeps_latest():
    coord = ua.TurnCoordinator()
    coord.admit_utterance(_utt('u1'))
    coord.admit_utterance(_utt('u2', 'first correction'))
    latest = coord.admit_utterance(_utt('u3', 'latest correction'))
    assert latest.status == 'queued'
    assert latest.pending_utterance_id == 'u3'
    assert coord.pending_utterance_id('sess-1') == 'u3'


def test_release_promotes_pending_to_lease():
    coord = ua.TurnCoordinator()
    first = coord.admit_utterance(_utt('u1'))
    coord.admit_utterance(_utt('u2', 'follow-up'))
    promoted = coord.release_lease(
        session_id='sess-1', lease_id=first.lease_id, generation=1,
    )
    assert promoted is not None
    assert promoted.status == 'admitted'
    assert promoted.pending_utterance_id is None
    assert promoted.lease_id != first.lease_id
    assert coord.active_lease('sess-1') == promoted.lease_id
    assert coord.pending_utterance_id('sess-1') is None
    active = coord.active_utterance('sess-1')
    assert active is not None
    assert active.utterance_id == 'u2'
    assert active.text == 'follow-up'


def test_stale_generation_cannot_admit():
    coord = ua.TurnCoordinator()
    coord.admit_utterance(_utt('u1'))
    new_gen = coord.bump_generation(session_id='sess-1')
    assert new_gen == 2
    stale = coord.admit_utterance(_utt('u2', generation=1))
    assert stale.status == 'stale_generation'
    assert stale.lease_id is None
    assert coord.pending_utterance_id('sess-1') is None


def test_obsolete_generation_cannot_project_effects():
    coord = ua.TurnCoordinator()
    admitted = coord.admit_utterance(_utt('u1'))
    assert coord.project_result(
        session_id='sess-1',
        lease_id=admitted.lease_id,
        generation=1,
    ) == 'apply'
    coord.bump_generation(session_id='sess-1')
    assert coord.project_result(
        session_id='sess-1',
        lease_id=admitted.lease_id,
        generation=1,
    ) == 'obsolete'


def test_duplicate_id_cannot_repeat_effects_after_release():
    coord = ua.TurnCoordinator()
    first = coord.admit_utterance(_utt('u1'))
    released = coord.release_lease(
        session_id='sess-1', lease_id=first.lease_id, generation=1,
    )
    assert released is None  # no pending to promote
    again = coord.admit_utterance(_utt('u1', 'replay'))
    assert again.status == 'duplicate'
    assert again.lease_id is None
    assert coord.active_lease('sess-1') is None


def test_blank_text_is_rejected():
    coord = ua.TurnCoordinator()
    decision = coord.admit_utterance(_utt('u-empty', '   '))
    assert decision.status == 'rejected_empty'
    assert decision.lease_id is None
