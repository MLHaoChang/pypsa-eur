# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT

"""Continuous speech session on a durable thread (LC-07 PR1; LC-C8, LC-C9).

One speech session at a time per durable thread, bound to its owner, project,
generation and pinned worker (every call goes through
``durable_conversation._read``, so the one-worker store lock and replica pin
apply). The media room comes from a ``live_media.MediaTransport``; none is
configured in the shipped app, and the whole surface is off unless
``PYPSA_GUI_LIVE_SPEECH=1``.

Only a *final* recognition segment can become a harness turn: it yields a
one-time ticket (``speech:<segment_id>``) that ``harness.loop.run_turn``
redeems against the exact recognised text before utterance admission.
Partials are acknowledged and dropped. Spoken "yes" never approves a card.

Resources: media, recognition and synthesis are reserved on the parent ledger
before the transport opens, and settled exactly once on whichever exit comes
first (end, error, timeout, rebind, stale generation, restart). A clean end
closes the room and waits for the worker's final usage report, then settles
what was reported; every other exit (or a final report that never comes
within ``USAGE_GRACE_S``) settles the reservation cap flagged
``usage_unverified``, so unknown usage is never under-counted. Usage above the
reservation settles at the cap flagged ``over_reservation``. Transport calls
run outside the durable-thread lock.

Speech output (LC-07 PR2): a turn started by a speech final queues its checked
answer, exactly as captioned, when the turn publishes it (``AnswerCapture``);
the audio worker claims it once (charging its characters to the TTS
reservation) and ``barge_in`` drops queued and playing answers without
touching the running turn.

The browser's media credential is returned by ``start`` alone. It is never
written to the thread file, logged or echoed by a read.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re
import threading
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from harness.providers import live_media
from services import durable_conversation as dc

ENABLE_ENV = 'PYPSA_GUI_LIVE_SPEECH'

MAX_SESSION_SECONDS = 1800
CREDENTIAL_TTL_S = 600
MAX_CREDENTIAL_TTL_S = 900
BRIDGE_TTL_S = MAX_SESSION_SECONDS + 300

# kind -> (reserved amount, provider billing unit)
RESERVATIONS: dict[str, tuple[int, str]] = {
    'media': (MAX_SESSION_SECONDS, 'media_seconds'),
    'stt': (MAX_SESSION_SECONDS, 'audio_seconds'),
    'tts': (60_000, 'characters'),
}

SEEN_SEGMENTS_MAX = 256
SEEN_USAGE_MAX = 512
CLOSED_SESSIONS_MAX = 16

USAGE_GRACE_S = 120
LIVE_STATES = frozenset({'starting', 'open', 'closing'})
# Exits that settle reported usage; every other exit settles the cap.
REPORTED_EXITS = frozenset({'end', 'error'})

_ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,48}$')
_CONTROL_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')
_NUMBER = r'(\d{1,12}(?:[.,]\d{1,12})?)'
# One segment, two numbers joined by a correction word: "7.5, no, 75 MW".
# Matched on whitespace-collapsed text with single optional spaces, so no two
# unbounded runs overlap (CodeQL polynomial-regex on #1058).
_CORRECTION_RE = re.compile(
    _NUMBER + r' ?[A-Za-z%]{0,4} ?[,;.]? ?'
    r'(?:no|not|sorry|i mean|rather|or rather|actually|correction|make that)\b'
    r'[ ,;:]{0,4}' + _NUMBER,
    re.IGNORECASE,
)
_APPROVAL_WORDS = frozenset({
    'yes', 'yeah', 'yep', 'confirm', 'confirmed', 'approve', 'approved',
    'ok', 'okay', 'go ahead', 'do it',
})


def _collapse(text: str) -> str:
    return ' '.join(text.split())


def is_bare_approval(text: str) -> bool:
    """"yes", "Confirm.", "approve please" and the like, nothing more."""
    phrase = _collapse(text).lower().rstrip('.!').strip()
    if phrase.endswith(' please'):
        phrase = phrase[: -len(' please')]
    return phrase in _APPROVAL_WORDS

log = logging.getLogger(__name__)

_TRANSPORT: live_media.MediaTransport | None = None
_CLOSES_LOCK = threading.Lock()
_CLOSES: list[threading.Thread] = []
_REAPERS_LOCK = threading.Lock()
# session_id -> timer that settles the session at its deadline with no other call.
_REAPERS: dict[str, threading.Timer] = {}
SPEECH_ID_PREFIX = 'speech:'
_TICKETS_LOCK = threading.Lock()
# episode_id -> utterance_id -> ticket. Process-local: a restart closes the
# speech session, so a lost ticket can never be redeemed later.
_TICKETS: dict[str, dict[str, dict[str, Any]]] = {}
# LC-07 PR2, also under _TICKETS_LOCK and process-local like the tickets.
# episode_id -> utterance_id -> {session_id, generation} of a redeemed final:
# only a turn started by one may queue its checked answer for speech.
_SPOKEN: dict[str, dict[str, dict[str, Any]]] = {}
# session_id -> outputs in publication order; dropped when the session closes.
_OUTPUTS: dict[str, list[dict[str, Any]]] = {}
OUTPUTS_MAX = 32


def _refuse(kind: str, message: str, status: int = 409) -> None:
    from fastapi import HTTPException

    raise HTTPException(status, {'error_kind': kind, 'message': message})


# ── configuration ──────────────────────────────────────────────────────────

def configure_transport(transport: live_media.MediaTransport | None) -> None:
    """Install the media transport (a deployment step; none ships enabled)."""
    global _TRANSPORT
    if transport is not None and not isinstance(transport, live_media.MediaTransport):
        raise TypeError('transport must implement live_media.MediaTransport')
    _TRANSPORT = transport


def enabled() -> bool:
    return os.environ.get(ENABLE_ENV, '').strip() == '1'


def _require_enabled() -> None:
    if not enabled():
        _refuse(
            'speech_disabled',
            'Continuous speech is not enabled on this server. Type your message instead.',
            503,
        )


def _transport() -> live_media.MediaTransport:
    if _TRANSPORT is None:
        _refuse(
            'speech_not_configured',
            'No speech media service is configured. Type your message instead.',
            503,
        )
    return _TRANSPORT


# ── helpers ────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _touch(thread: dict[str, Any]) -> None:
    thread['checkpoint_rev'] = int(thread.get('checkpoint_rev', 0)) + 1
    thread['updated_at'] = _now_iso()


def _live_session(thread: dict[str, Any]) -> dict[str, Any] | None:
    """The thread's speech session while it still holds reservations."""
    session = thread.get('speech')
    if isinstance(session, dict) and session.get('state') in LIVE_STATES:
        return session
    return None


def _session_view(session: dict[str, Any] | None) -> dict[str, Any] | None:
    if session is None:
        return None
    keep = (
        'session_id', 'state', 'generation', 'episode_id', 'language',
        'started_at', 'expires_at', 'closed_reason', 'closed_at', 'usage',
        'settlement',
    )
    return {k: session[k] for k in keep if k in session}


def _require_id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        _refuse('speech_request_invalid', f'{field} must be 1–48 letters, digits, _ or -.', 422)
    return value


def _bounded_append(items: list, value: Any, limit: int) -> None:
    items.append(value)
    del items[:-limit]


def _number(value: float) -> int | float:
    return int(value) if float(value).is_integer() else value


def _drop_tickets(episode_id: str | None, session_id: str | None = None) -> None:
    with _TICKETS_LOCK:
        if episode_id:
            _TICKETS.pop(episode_id, None)
            _SPOKEN.pop(episode_id, None)
        if session_id:
            _OUTPUTS.pop(session_id, None)


def ambiguous_quantity(text: str) -> bool:
    """True when one segment corrects one number with a different one."""
    for match in _CORRECTION_RE.finditer(_collapse(text)):
        first, second = (m.replace(',', '.') for m in match.groups())
        try:
            if float(first) != float(second):
                return True
        except ValueError:
            return True
    return False


def _pending_card(episode_id: str | None) -> bool:
    from services import chat_service

    session = chat_service.get_session(episode_id) if episode_id else None
    return bool(session is not None and getattr(session, 'pending_confirmations', None))


def _ensure_bridge_credentials(thread: dict[str, Any]) -> dict[str, Any]:
    creds = thread.get('bridge_credentials') or {}
    if (
        not creds.get('token')
        or int(creds.get('generation', -1)) != int(thread.get('generation', 1))
        or float(creds.get('expires_at') or 0) <= time.time() + MAX_SESSION_SECONDS
    ):
        creds = dc._mint_bridge_credentials(thread)
        creds['expires_at'] = time.time() + BRIDGE_TTL_S
        thread['bridge_credentials'] = creds
    return creds


def _save_if(path: Any, thread: dict[str, Any], changed: bool) -> None:
    if changed:
        _touch(thread)
        dc._save(path, thread)


def _owner_thread(thread_id: str, *, renew: bool) -> tuple[Any, dict[str, Any]]:
    """Read under the caller's lock, check the pin, reap an outgrown session."""
    path, thread = dc._read(thread_id)
    dc._require_authoritative_replica(thread, renew=renew)
    _save_if(path, thread, _reap(thread))
    return path, thread


def _bridge_thread(thread_id: str, token: Any) -> tuple[Any, dict[str, Any]]:
    """The audio worker's read: a current, unexpired bridge token first."""
    if not isinstance(token, str):
        _refuse('bridge_credentials_invalid', 'Bridge credentials are not valid.', 401)
    creds = dc.authorize_bridge_credentials(thread_id, token)
    if float(creds.get('expires_at') or 0) <= time.time():
        _refuse('bridge_credentials_stale', 'Bridge credentials have expired.', 401)
    return _owner_thread(thread_id, renew=False)


def _find_session(thread: dict[str, Any], session_id: str) -> dict[str, Any]:
    session = thread.get('speech')
    if isinstance(session, dict) and session.get('session_id') == session_id:
        return session
    if session_id in (thread.get('speech_closed') or []):
        return {'session_id': session_id, 'state': 'closed'}
    _refuse('speech_session_not_found', 'No such speech session on this thread.', 404)
    return {}


# ── room teardown (outside the thread lock) ────────────────────────────────

def _close_room(room: str | None) -> None:
    """Tear the room down on a worker thread; callers may hold the lock."""
    transport = _TRANSPORT
    if transport is None or not room:
        return

    def run() -> None:
        try:
            transport.close_session(room=room)
        except Exception as exc:  # noqa: BLE001 — logged; the charge is already capped
            log.warning('speech room %s did not close: %s', room, type(exc).__name__)

    worker = threading.Thread(target=run, name=f'speech-close-{room}', daemon=True)
    with _CLOSES_LOCK:
        _CLOSES[:] = [t for t in _CLOSES if t.is_alive()]
        _CLOSES.append(worker)
    worker.start()


def wait_for_closes(timeout: float = 5.0) -> None:
    """Join pending room closes (shutdown hook and tests)."""
    with _CLOSES_LOCK:
        pending = list(_CLOSES)
    for worker in pending:
        worker.join(timeout)


# ── settle (every exit) ────────────────────────────────────────────────────

def close_in(thread: dict[str, Any], reason: str) -> bool:
    """Close the live session on an in-memory thread and settle it once.

    The caller holds ``durable_conversation._LOCK`` and saves. Returns False
    when no session holds reservations. ``end`` and ``error`` settle reported
    usage; any other reason settles the cap flagged ``usage_unverified``.
    """
    session = _live_session(thread)
    if session is None:
        return False
    if session.get('state') != 'closing':
        _close_room(session.get('room'))
    settlement: dict[str, Any] = {}
    for kind, key in (session.get('keys') or {}).items():
        cap, unit = RESERVATIONS[kind]
        used = float((session.get('usage') or {}).get(kind, 0))
        flags: list[str] = []
        if reason not in REPORTED_EXITS:
            amount, flags = cap, ['usage_unverified']
        elif used > cap:
            amount, flags = cap, ['over_reservation']
        else:
            amount = _number(used)
        dc._settle_in(thread, key=key, amount=amount, unit=unit, flags=flags)
        settlement[kind] = {'amount': amount, 'unit': unit, 'flags': flags}
    session['state'] = 'closed'
    session['closed_reason'] = reason
    session['closed_at'] = _now_iso()
    session['settlement'] = settlement
    _bounded_append(
        thread.setdefault('speech_closed', []), session['session_id'], CLOSED_SESSIONS_MAX,
    )
    _drop_tickets(session.get('episode_id'), session['session_id'])
    _cancel_reap(session['session_id'])
    return True


def _reap(thread: dict[str, Any]) -> bool:
    """Settle a session the thread has outgrown, that expired, or whose final
    usage report never came."""
    session = _live_session(thread)
    if session is None:
        return False
    now = time.time()
    if int(session.get('generation', -1)) != int(thread.get('generation', 1)):
        return close_in(thread, 'stale_generation')
    if session.get('state') == 'closing':
        if float(session.get('closing_deadline') or 0) <= now:
            return close_in(thread, 'usage_unreported')
        return False
    if session.get('state') == 'open' and float(session.get('expires_ts') or 0) <= now:
        return close_in(thread, 'timeout')
    return False


# ── public API ─────────────────────────────────────────────────────────────

def _reserve_session(thread: dict[str, Any], language: str) -> dict[str, Any]:
    session_id = uuid.uuid4().hex
    keys = {kind: f'speech-{session_id}-{kind}' for kind in RESERVATIONS}
    for kind, key in keys.items():
        amount, unit = RESERVATIONS[kind]
        dc._reserve_in(thread, key=key, kind=f'speech_{kind}', amount=amount, unit=unit)
    session = {
        'session_id': session_id,
        'state': 'starting',
        'generation': int(thread.get('generation', 1)),
        'episode_id': thread.get('episode_id'),
        'language': language,
        'room': f'pypsa-{session_id}',
        'keys': keys,
        'usage': {kind: 0 for kind in RESERVATIONS},
        'usage_seen': [],
        'usage_final': False,
        'segments_seen': [],
        'last_seq': -1,
        'started_at': _now_iso(),
    }
    thread['speech'] = session
    return session


def _open_room(
    transport: live_media.MediaTransport, session: dict[str, Any], owner_id: str, bridge: str,
) -> live_media.MediaGrant | None:
    """Ask the transport for the room; None on any failure or unbounded grant."""
    try:
        grant = transport.open_session(
            room=session['room'],
            identity=f'owner-{owner_id}',
            ttl_s=CREDENTIAL_TTL_S,
            bridge_token=bridge,
            language=session['language'],
        )
        ttl = float(grant.expires_at) - time.time()
        if not isinstance(grant, live_media.MediaGrant) or not 0 < ttl <= MAX_CREDENTIAL_TTL_S:
            return None  # close_in('error') tears the room down
        return grant
    except Exception as exc:  # noqa: BLE001 — any transport failure is one typed refusal
        log.warning('speech room %s did not open: %s', session['room'], type(exc).__name__)
        return None


def start(thread_id: str, *, generation: int, language: str) -> dict[str, Any]:
    """Reserve, open the media room and return the browser's grant once.

    The reservation is saved before the transport is called, and the
    transport is called without holding the thread lock. The response carries
    the thread's unresolved ``reconcile`` items; they are cleared only in the
    save that records the opened session.
    """
    from services import chat_service

    _require_enabled()
    transport = _transport()
    try:
        language = live_media.pin_language(language)
    except live_media.LiveMediaError:
        _refuse('speech_request_invalid', 'Choose a supported language tag.', 422)
    with dc._LOCK:
        path, thread = _owner_thread(thread_id, renew=True)
        dc._require_generation(thread, generation)
        if _live_session(thread) is not None:
            _refuse('speech_session_open', 'A speech session is already open on this thread.')
        episode_id = thread.get('episode_id')
        if not episode_id or chat_service.get_session(episode_id) is None:
            _refuse('speech_episode_required', 'Resume the conversation before starting speech.')
        session = _reserve_session(thread, language)
        bridge = _ensure_bridge_credentials(thread)['token']
        owner_id = thread['owner_id']
        _save_if(path, thread, True)

    grant = _open_room(transport, session, owner_id, bridge)

    with dc._LOCK:
        path, thread = dc._read(thread_id)
        current = thread.get('speech') or {}
        if current.get('session_id') != session['session_id'] or current.get('state') != 'starting':
            # Rebind, resume or restart settled it while the room was opening.
            _close_room(session['room'])
            _refuse('speech_transport_failed', 'The speech session was superseded. Start again.', 502)
        if grant is None:
            close_in(thread, 'error')
            _save_if(path, thread, True)
            _refuse(
                'speech_transport_failed',
                'The speech media service did not start. Type your message instead.',
                502,
            )
        reconcile = list(thread.get('reconcile') or [])
        expires_ts = time.time() + MAX_SESSION_SECONDS
        current.update({
            'state': 'open',
            'expires_ts': expires_ts,
            'expires_at': datetime.fromtimestamp(expires_ts, UTC).isoformat(),
        })
        thread['reconcile'] = []
        _save_if(path, thread, True)
        _schedule_reap(path, current['session_id'], expires_ts)
        return {
            'session': _session_view(current),
            'generation': int(thread.get('generation', 1)),
            'media': {'url': grant.url, 'token': grant.token, 'expires_at': grant.expires_at},
            'reconcile': reconcile,
        }


def submit_segment(
    thread_id: str,
    *,
    token: str,
    session_id: str,
    segment_id: str,
    seq: int,
    text: str,
    final: bool,
) -> dict[str, Any]:
    """The audio worker reports one recognition segment.

    Partials are dropped. A final becomes at most one admission ticket per
    segment id, in order; an ambiguous correction asks for clarification, and
    a bare approval word never approves a pending card.
    """
    _require_enabled()
    _require_id(session_id, 'session_id')
    _require_id(segment_id, 'segment_id')
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
        _refuse('speech_request_invalid', 'seq must be a non-negative integer.', 422)
    if (
        not isinstance(text, str)
        or len(text) > live_media.MAX_TRANSCRIPT_CHARS
        or _CONTROL_RE.search(text)
    ):
        _refuse('speech_request_invalid', 'The transcript text is not usable.', 422)
    with dc._LOCK:
        path, thread = _bridge_thread(thread_id, token)
        session = _find_session(thread, session_id)
        if session.get('state') != 'open':
            _refuse('speech_session_closed', 'This speech session has ended.')
        if not final:
            return {'status': 'partial_ignored', 'segment_id': segment_id}
        if segment_id in session['segments_seen']:
            return {'status': 'duplicate', 'segment_id': segment_id}
        if seq <= int(session['last_seq']):
            _refuse(
                'speech_final_out_of_order',
                'This final arrived after a later one and was not used.',
            )
        text = text.strip()
        if not text:
            _refuse('speech_request_invalid', 'The transcript text is empty.', 422)
        _bounded_append(session['segments_seen'], segment_id, SEEN_SEGMENTS_MAX)
        session['last_seq'] = seq
        _save_if(path, thread, True)
        episode_id = session['episode_id']
        if ambiguous_quantity(text):
            return {
                'status': 'clarify',
                'segment_id': segment_id,
                'text': text,
                'message': 'I heard two different numbers. Which value did you mean?',
            }
        if is_bare_approval(text) and _pending_card(episode_id):
            return {
                'status': 'not_an_approval',
                'segment_id': segment_id,
                'text': text,
                'message': 'Speech does not approve changes. Use Confirm on the card.',
            }
        from services import utterance_admission as ua

        utterance_id = f'{SPEECH_ID_PREFIX}{segment_id}'
        admission_generation = ua._DEFAULT.generation(episode_id)
        with _TICKETS_LOCK:
            _TICKETS.setdefault(episode_id, {})[utterance_id] = {
                'text': text,
                'generation': admission_generation,
                'session_id': session_id,
            }
        return {
            'status': 'final_accepted',
            'segment_id': segment_id,
            'utterance_id': utterance_id,
            'generation': admission_generation,
            'text': text,
        }


def is_speech_utterance(utterance_id: str) -> bool:
    return utterance_id.startswith(SPEECH_ID_PREFIX)


def redeem_final(
    episode_id: str, utterance_id: str, text: str, generation: int,
) -> dict[str, str] | None:
    """Consume a speech ticket; None when ``text`` and ``generation`` are the
    recognised final's own.

    Returns the refusal detail otherwise. Called by ``run_turn`` before
    admission for every speech utterance id, so only finalized speech is
    admitted, once.
    """
    with _TICKETS_LOCK:
        ticket = (_TICKETS.get(episode_id) or {}).pop(utterance_id, None)
    if ticket is None:
        return {
            'error_kind': 'speech_final_unknown',
            'message': 'This speech was not a finalized utterance for this conversation; '
            'nothing was sent.',
        }
    if ticket['text'] != text or ticket['generation'] != generation:
        return {
            'error_kind': 'speech_final_mismatch',
            'message': 'The text or generation differs from what was recognised; nothing was sent.',
        }
    with _TICKETS_LOCK:
        _remember_spoken(episode_id, utterance_id, {
            'session_id': ticket['session_id'],
            'generation': ticket['generation'],
        })
    return None


def report_usage(
    thread_id: str,
    *,
    token: str,
    session_id: str,
    usage_id: str,
    kind: str,
    amount: Any,
    unit: str,
    final: bool = False,
) -> dict[str, Any]:
    """Add provider usage to the live session, once per ``usage_id``.

    ``final`` marks the worker's last report; once the owner has ended the
    session it settles reported usage. Usage for a settled session is
    acknowledged and ignored.
    """
    _require_enabled()
    _require_id(session_id, 'session_id')
    _require_id(usage_id, 'usage_id')
    if kind not in RESERVATIONS:
        _refuse('speech_usage_invalid', 'Unknown usage kind.', 422)
    if (
        isinstance(amount, bool)
        or not isinstance(amount, (int, float))
        or not math.isfinite(amount)
        or amount < 0
    ):
        _refuse('speech_usage_invalid', 'Usage must be a finite, non-negative number.', 422)
    if unit != RESERVATIONS[kind][1]:
        _refuse('speech_usage_invalid', 'Usage is not in the reserved unit.', 422)
    with dc._LOCK:
        path, thread = _bridge_thread(thread_id, token)
        session = _find_session(thread, session_id)
        if session.get('state') not in {'open', 'closing'}:
            return {'status': 'ignored', 'reason': 'settled'}
        status = _record_usage(thread, session, usage_id, kind, amount, final)
        _save_if(path, thread, status != 'duplicate')
        return {'status': status, 'usage_id': usage_id}


def _record_usage(
    thread: dict[str, Any],
    session: dict[str, Any],
    usage_id: str,
    kind: str,
    amount: float,
    final: bool,
) -> str:
    """Add usage to a live session once per ``usage_id``; the caller saves."""
    if usage_id in session['usage_seen']:
        return 'duplicate'
    _bounded_append(session['usage_seen'], usage_id, SEEN_USAGE_MAX)
    session['usage'][kind] = _number(float(session['usage'].get(kind, 0)) + float(amount))
    session['usage_final'] = session.get('usage_final') or bool(final)
    settled = session['state'] == 'closing' and session['usage_final'] and close_in(thread, 'end')
    return 'settled' if settled else 'recorded'


def end(thread_id: str, *, generation: int, session_id: str) -> dict[str, Any]:
    """The owner ends the session. Idempotent.

    The room closes now; reported usage settles once the worker's final
    report is in (at once if it already is), else the cap after
    ``USAGE_GRACE_S``.
    """
    _require_enabled()
    _require_id(session_id, 'session_id')
    with dc._LOCK:
        path, thread = _owner_thread(thread_id, renew=True)
        dc._require_generation(thread, generation)
        session = _find_session(thread, session_id)
        if session.get('state') != 'open':
            return {'status': session.get('state'), 'session': _session_view(session)}
        if session.get('usage_final'):
            close_in(thread, 'end')
        else:
            _close_room(session.get('room'))
            session['state'] = 'closing'
            session['closing_deadline'] = time.time() + USAGE_GRACE_S
        _save_if(path, thread, True)
        if session['state'] == 'closing':
            _schedule_reap(path, session_id, session['closing_deadline'])
        return {'status': session['state'], 'session': _session_view(session)}


# ── speech output (LC-07 PR2, LC-T14) ─────────────────────────────────────

SPOKEN_PER_EPISODE_MAX = 16


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


class AnswerCapture:
    """Follows one speech turn's client frames and queues its checked answer.

    ``run_turn`` feeds this every frame it yields to the client, after the
    assurance projection. The answer is queued for speech only when the turn
    reaches ``turn_done`` naming the answer that ``answer_approved``
    announced, every ``token`` frame of the turn came after that approval and
    together they equal the assurance-approved text byte for byte (the caption
    is the spoken text), the turn was not aborted and the utterance's admission
    generation is still current. Legacy unvalidated text, a refused, errored or
    aborted turn and an approval whose turn never completed are never spoken.
    """

    def __init__(self, session: Any, utterance_id: str) -> None:
        self._session = session
        self._utterance_id = utterance_id
        self._answer_id: str | None = None
        self._caption: list[str] = []
        self._unchecked = False
        self.result: str | None = None

    def note(self, event: str, payload: Any) -> None:
        if self.result is not None:
            return
        payload = payload if isinstance(payload, dict) else {}
        if event == 'answer_approved':
            if self._answer_id is not None:
                self._unchecked = True
            self._answer_id = payload.get('answer_id')
        elif event == 'token':
            delta = payload.get('delta')
            if self._answer_id is None or not isinstance(delta, str):
                self._unchecked = True  # caption text that no approval covers
            else:
                self._caption.append(delta)
        elif event == 'error':
            self._unchecked = True
        elif event == 'turn_done':
            self.result = self._publish(payload)

    def _publish(self, payload: dict[str, Any]) -> str:
        turn = getattr(self._session, 'assurance_turn', None)
        approved = getattr(turn, 'approved', None)
        if self._unchecked or turn is None or not isinstance(approved, dict):
            return 'not_checked'
        answer_id = payload.get('answer_id')
        text = approved.get('text')
        if not answer_id or answer_id != self._answer_id or approved.get('answer_id') != answer_id:
            return 'not_checked'
        if not isinstance(text, str) or ''.join(self._caption).encode('utf-8') != text.encode('utf-8'):
            return 'caption_mismatch'
        if self._session.abort_event.is_set():
            return 'aborted'
        if not text.strip():
            return 'empty'
        if len(text) > live_media.MAX_TTS_TEXT_CHARS:
            return 'too_long'
        return queue_answer(self._session.session_id, self._utterance_id, answer_id, text)


def capture_for(session: Any, utterance_id: str | None) -> AnswerCapture | None:
    """A capture for a turn started by a speech final; None for any other turn."""
    if utterance_id is None or not is_speech_utterance(utterance_id):
        return None
    return AnswerCapture(session, utterance_id)


def queue_answer(episode_id: str, utterance_id: str, answer_id: str, text: str) -> str:
    """Queue a checked answer for the session whose final started the turn."""
    from services import utterance_admission as ua

    with _TICKETS_LOCK:
        spoken = (_SPOKEN.get(episode_id) or {}).pop(utterance_id, None)
        if spoken is None:
            return 'not_speech'  # never redeemed here, or the session closed
        if ua._DEFAULT.generation(episode_id) != spoken['generation']:
            return 'stale_generation'
        _bounded_append(_OUTPUTS.setdefault(spoken['session_id'], []), {
            'output_id': uuid.uuid4().hex,
            'answer_id': answer_id,
            'utterance_id': utterance_id,
            'text': text,
            'sha256': _sha256(text),
            'chars': len(text),
            'generation': spoken['generation'],
            'state': 'queued',
        }, OUTPUTS_MAX)
    return 'queued'


def _remember_spoken(episode_id: str, utterance_id: str, record: dict[str, Any]) -> None:
    spoken = _SPOKEN.setdefault(episode_id, {})
    spoken[utterance_id] = record
    for stale in list(spoken)[:-SPOKEN_PER_EPISODE_MAX]:
        spoken.pop(stale, None)


def _output_view(output: dict[str, Any]) -> dict[str, Any]:
    return {k: output[k] for k in ('output_id', 'answer_id', 'text', 'sha256', 'chars')}


def claim_output(thread_id: str, *, token: str, session_id: str) -> dict[str, Any]:
    """The audio worker takes the next answer to speak, at most once.

    The text is the caption, byte for byte. Its characters are charged to the
    session's TTS reservation once, through the same usage record as
    ``report_usage`` (usage id ``tts-<output_id>``; a worker report under that
    id is a duplicate). An answer that would exceed the reservation is not
    spoken; it stays in the caption.
    """
    from services import utterance_admission as ua

    _require_enabled()
    _require_id(session_id, 'session_id')
    with dc._LOCK:
        path, thread = _bridge_thread(thread_id, token)
        session = _find_session(thread, session_id)
        if session.get('state') != 'open':
            _refuse('speech_session_closed', 'This speech session has ended.')
        current = ua._DEFAULT.generation(session['episode_id'])
        with _TICKETS_LOCK:
            output = None
            for item in _OUTPUTS.get(session_id) or []:
                if item['state'] != 'queued':
                    continue
                if item['generation'] != current:
                    item['state'] = 'stale'
                    continue
                output = item
                break
            if output is None:
                return {'status': 'empty'}
            cap = RESERVATIONS['tts'][0]
            if float(session['usage'].get('tts', 0)) + output['chars'] > cap:
                output['state'] = 'over_budget'
                return {'status': 'over_budget', 'output_id': output['output_id']}
            output['state'] = 'claimed'
            view = _output_view(output)
        _record_usage(thread, session, f"tts-{view['output_id']}", 'tts', view['chars'], False)
        _save_if(path, thread, True)
        return {'status': 'speak', 'language': session['language'], 'output': view}


def barge_in(thread_id: str, *, token: str, session_id: str) -> dict[str, Any]:
    """The user spoke over playback: drop every queued or claimed answer.

    Playback only. The running turn, its admission lease and any solver job
    are untouched, and nothing already charged is refunded.
    """
    _require_enabled()
    _require_id(session_id, 'session_id')
    with dc._LOCK:
        _, thread = _bridge_thread(thread_id, token)
        session = _find_session(thread, session_id)
        if session.get('state') != 'open':
            return {'status': 'ignored', 'cancelled': []}
        with _TICKETS_LOCK:
            cancelled = []
            for item in _OUTPUTS.get(session_id) or []:
                if item['state'] in {'queued', 'claimed'}:
                    item['state'] = 'cancelled'
                    cancelled.append(item['output_id'])
        return {'status': 'cancelled', 'cancelled': cancelled}


async def synthesize_output(
    tts: live_media.TtsAdapter,
    output: dict[str, Any],
    *,
    language: str,
    cancel: live_media.CancelToken | None = None,
) -> live_media.SpeechAudio:
    """Synthesize a claimed answer exactly as captioned; numbers and units are
    passed verbatim, never rewritten."""
    text = output.get('text')
    if not isinstance(text, str) or _sha256(text) != output.get('sha256'):
        raise live_media.LiveMediaError(
            'invalid_request', 'The text to speak differs from the caption.', 422,
        )
    return await tts.synthesize(text, language=language, cancel=cancel)


# ── deadline reaper and boot sweep (GK B NOT OK on #1058, condition (d)) ───

def _deadline(session: dict[str, Any]) -> float | None:
    if session.get('state') == 'closing':
        return float(session.get('closing_deadline') or 0)
    if session.get('state') == 'open':
        return float(session.get('expires_ts') or 0)
    return None


def _schedule_reap(path: Any, session_id: str, at_ts: float) -> None:
    """Settle ``session_id`` at ``at_ts`` even if no request ever comes.

    ``path`` is the thread file resolved by an ACL'd read at start or end; the
    timer re-reads it under the thread lock and the store lock.
    """
    delay = max(0.0, at_ts - time.time()) + 0.05
    timer = threading.Timer(delay, _reap_path, args=(path, session_id))
    timer.daemon = True
    timer.name = f'speech-reap-{session_id}'
    with _REAPERS_LOCK:
        previous = _REAPERS.pop(session_id, None)
        _REAPERS[session_id] = timer
    if previous is not None:
        previous.cancel()
    timer.start()


def _cancel_reap(session_id: str) -> None:
    with _REAPERS_LOCK:
        timer = _REAPERS.pop(session_id, None)
    if timer is not None and timer is not threading.current_thread():
        timer.cancel()


def _reap_path(path: Any, session_id: str) -> None:
    import security
    from services import app_workers

    with _REAPERS_LOCK:
        if _REAPERS.get(session_id) is threading.current_thread():
            _REAPERS.pop(session_id, None)
    try:
        with dc._LOCK:
            app_workers.require_single_worker()
            thread = dc._load(path)
            session = _live_session(thread)
            if session is None or session.get('session_id') != session_id:
                return
            if thread.get('owner_replica_id') != security.replica_id():
                return  # pinned to another worker; its own reaper or takeover settles it
            if _reap(thread):
                _touch(thread)
                dc._save(path, thread)
                return
            deadline = _deadline(session)
        if deadline is not None:
            _schedule_reap(path, session_id, deadline)
    except Exception as exc:  # noqa: BLE001 — a reaper never takes the process down
        log.warning('speech reap of %s failed: %s', session_id, type(exc).__name__)


def wait_for_reaps(timeout: float = 5.0) -> None:
    """Join reapers due within ``timeout`` (tests)."""
    end_at = time.monotonic() + timeout
    while time.monotonic() < end_at:
        with _REAPERS_LOCK:
            pending = list(_REAPERS.values())
        if not pending:
            return
        for timer in pending:
            timer.join(max(0.0, end_at - time.monotonic()))
    wait_for_closes()


def _project_dirs() -> list[Any]:
    from db.models import Project
    from db.session import SessionLocal
    from services import project_registry

    db = SessionLocal()
    try:
        return [project_registry.project_dir(p) for p in db.query(Project).all()]
    finally:
        db.close()


def _owned_elsewhere(thread: dict[str, Any]) -> bool:
    """Pinned to another replica whose owner lease has not lapsed."""
    import security

    pinned = thread.get('owner_replica_id')
    if not pinned or pinned == security.replica_id():
        return False
    return float(thread.get('owner_lease_until') or 0) > dc._wall_clock()


def sweep_at_boot(project_dirs: list[Any] | None = None) -> int:
    """Settle every speech session a dead process left holding reservations.

    Sessions are process-bound (room, worker, tickets), so at boot none can
    belong to this process; a thread another replica still holds (its owner
    lease has not lapsed) is left alone. Only stores that hold a live session are locked,
    so the store lock stays lazy for everything else. Returns the count.
    """
    import json
    from pathlib import Path

    from fastapi import HTTPException

    from services import app_workers

    try:
        app_workers.require_single_worker()
        dirs = _project_dirs() if project_dirs is None else project_dirs
    except Exception as exc:  # noqa: BLE001
        log.warning('speech boot sweep skipped: %s', type(exc).__name__)
        return 0
    settled = 0
    for project_dir in dirs:
        store = Path(project_dir) / 'durable_conversations'
        if not store.is_dir():
            continue
        for path in sorted(store.glob('*.json')):
            try:
                peek = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            if _live_session(peek) is None:
                continue
            try:
                with dc._LOCK:
                    thread = dc._load(path)
                    if _owned_elsewhere(thread):
                        log.info('speech boot sweep left %s: another replica holds it', path.name)
                        continue
                    if close_in(thread, 'restart'):
                        _touch(thread)
                        dc._save(path, thread)
                        settled += 1
            except HTTPException as exc:
                log.warning('speech boot sweep left %s: %s', path.name,
                            (exc.detail or {}).get('error_kind'))
    return settled


def start_boot_sweep() -> None:
    """Run ``sweep_at_boot`` off the startup path."""
    threading.Thread(target=sweep_at_boot, name='speech-boot-sweep', daemon=True).start()


def state(thread_id: str) -> dict[str, Any]:
    """The owner's view of the current speech session; carries no credential."""
    _require_enabled()
    with dc._LOCK:
        _, thread = _owner_thread(thread_id, renew=False)
        return {
            'generation': int(thread.get('generation', 1)),
            'session': _session_view(thread.get('speech')),
        }


def _reset_for_tests() -> None:
    global _TRANSPORT
    with _REAPERS_LOCK:
        timers = list(_REAPERS.values())
        _REAPERS.clear()
    for timer in timers:
        timer.cancel()
    wait_for_closes()
    _TRANSPORT = None
    with _TICKETS_LOCK:
        _TICKETS.clear()
        _SPOKEN.clear()
        _OUTPUTS.clear()
