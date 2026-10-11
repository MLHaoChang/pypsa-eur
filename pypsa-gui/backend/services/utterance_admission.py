"""Shared utterance admission coordinator (issue 02 / C-U1).

Typed (and later voice) inputs share one turn lease per chat session, at most
one pending correction, and generation-aware result projection. Media and
``run_turn`` wiring are out of scope for this module.
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from typing import Literal

PENDING_FOLLOWUP_CAPACITY = 1

AdmissionStatus = Literal[
    'admitted',
    'queued',
    'duplicate',
    'stale_generation',
    'rejected_empty',
]
Projection = Literal['apply', 'obsolete']


@dataclass(frozen=True)
class StableUtterance:
    utterance_id: str
    text: str
    generation: int
    session_id: str


@dataclass(frozen=True)
class AdmissionDecision:
    status: AdmissionStatus
    lease_id: str | None
    generation: int
    pending_utterance_id: str | None


@dataclass
class _SessionState:
    generation: int = 1
    lease_id: str | None = None
    active: StableUtterance | None = None
    pending: StableUtterance | None = None
    seen_ids: set[str] | None = None

    def __post_init__(self) -> None:
        if self.seen_ids is None:
            self.seen_ids = set()

    @property
    def active_utterance_id(self) -> str | None:
        return self.active.utterance_id if self.active else None


class TurnCoordinator:
    """In-process per-session admission state (not durable; not ChatSession)."""

    def __init__(self, *, pending_capacity: int = PENDING_FOLLOWUP_CAPACITY) -> None:
        if pending_capacity != 1:
            raise ValueError('Only a single pending follow-up is supported in C-U1.')
        self._pending_capacity = pending_capacity
        self._lock = threading.RLock()
        self._sessions: dict[str, _SessionState] = {}

    def _state(self, session_id: str) -> _SessionState:
        if session_id not in self._sessions:
            self._sessions[session_id] = _SessionState()
        return self._sessions[session_id]

    def active_lease(self, session_id: str) -> str | None:
        with self._lock:
            return self._state(session_id).lease_id

    def active_utterance(self, session_id: str) -> StableUtterance | None:
        with self._lock:
            return self._state(session_id).active

    def pending_utterance_id(self, session_id: str) -> str | None:
        with self._lock:
            pending = self._state(session_id).pending
            return pending.utterance_id if pending else None

    def admit_utterance(self, utterance: StableUtterance) -> AdmissionDecision:
        with self._lock:
            state = self._state(utterance.session_id)
            if not isinstance(utterance.text, str) or not utterance.text.strip():
                return AdmissionDecision(
                    status='rejected_empty',
                    lease_id=None,
                    generation=state.generation,
                    pending_utterance_id=(
                        state.pending.utterance_id if state.pending else None
                    ),
                )
            if utterance.generation != state.generation:
                return AdmissionDecision(
                    status='stale_generation',
                    lease_id=None,
                    generation=state.generation,
                    pending_utterance_id=(
                        state.pending.utterance_id if state.pending else None
                    ),
                )
            if utterance.utterance_id in state.seen_ids:
                return AdmissionDecision(
                    status='duplicate',
                    lease_id=None,
                    generation=state.generation,
                    pending_utterance_id=(
                        state.pending.utterance_id if state.pending else None
                    ),
                )
            if state.lease_id is None:
                lease_id = uuid.uuid4().hex
                state.lease_id = lease_id
                state.active = utterance
                state.seen_ids.add(utterance.utterance_id)
                return AdmissionDecision(
                    status='admitted',
                    lease_id=lease_id,
                    generation=state.generation,
                    pending_utterance_id=None,
                )
            # Lease held: queue or replace the single pending correction.
            state.pending = utterance
            state.seen_ids.add(utterance.utterance_id)
            return AdmissionDecision(
                status='queued',
                lease_id=None,
                generation=state.generation,
                pending_utterance_id=utterance.utterance_id,
            )

    def release_lease(
        self,
        *,
        session_id: str,
        lease_id: str,
        generation: int,
        promote: bool = True,
    ) -> AdmissionDecision | None:
        """Clear the active lease. When ``promote`` is True, admit the pending
        correction as the next lease (caller must start that turn). When False,
        clear the lease only and leave pending in place for a later drain.
        """
        with self._lock:
            state = self._state(session_id)
            if state.lease_id != lease_id or state.generation != generation:
                return None
            state.lease_id = None
            state.active = None
            if not promote:
                return None
            pending = state.pending
            state.pending = None
            if pending is None:
                return None
            if pending.generation != state.generation:
                return None
            # Pending id was already recorded in seen_ids at queue time; promote
            # without treating it as a duplicate.
            new_lease = uuid.uuid4().hex
            state.lease_id = new_lease
            state.active = pending
            return AdmissionDecision(
                status='admitted',
                lease_id=new_lease,
                generation=state.generation,
                pending_utterance_id=None,
            )

    def bump_generation(self, *, session_id: str) -> int:
        with self._lock:
            state = self._state(session_id)
            state.generation += 1
            state.lease_id = None
            state.active = None
            state.pending = None
            return state.generation

    def project_result(
        self,
        *,
        session_id: str,
        lease_id: str,
        generation: int,
    ) -> Projection:
        with self._lock:
            state = self._state(session_id)
            if state.lease_id == lease_id and state.generation == generation:
                return 'apply'
            return 'obsolete'


_DEFAULT = TurnCoordinator()


def admit_utterance(utterance: StableUtterance) -> AdmissionDecision:
    return _DEFAULT.admit_utterance(utterance)


def _reset_for_tests() -> None:
    global _DEFAULT
    _DEFAULT = TurnCoordinator()
