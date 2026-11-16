"""Progress-stream event ids: ``<epoch>:<seq>`` (live-voice 11c).

An epoch names one in-memory life of a chat session's progress log. It is a
random nonce signed with the server key and the session id, so every epoch a
session ever had verifies for that session after a restart (no stored set,
nothing to evict), and an epoch minted for any other session does not.
A known past epoch is answered with a ``job_snapshot``; an epoch that does
not verify is foreign and refused. The epoch is separate from 11b's
``monitor_generation``: Stop monitoring never moves it.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets

_NONCE_HEX = 12
_MAC_HEX = 16
_EPOCH_RE = re.compile(rf"^([0-9a-f]{{{_NONCE_HEX}}})\.([0-9a-f]{{{_MAC_HEX}}})$")
# A seq longer than this is not one this server minted (it would take 10^18 frames).
_MAX_SEQ_DIGITS = 18


def _key() -> bytes:
    from settings import get_settings

    return get_settings().secret_key.encode()


def _mac(session_id: str, nonce: str) -> str:
    material = f"progress-epoch:{session_id}:{nonce}".encode()
    return hmac.new(_key(), material, hashlib.sha256).hexdigest()[:_MAC_HEX]


def mint_epoch(session_id: str) -> str:
    nonce = secrets.token_hex(_NONCE_HEX // 2)
    return f"{nonce}.{_mac(session_id, nonce)}"


def epoch_belongs_to(session_id: str, epoch: str) -> bool:
    """True when ``epoch`` was minted for ``session_id`` (now or before a restart)."""
    match = _EPOCH_RE.match(epoch)
    if match is None:
        return False
    return hmac.compare_digest(match.group(2), _mac(session_id, match.group(1)))


def format_event_id(epoch: str, seq: int) -> str:
    return f"{epoch}:{seq}"


def parse_event_id(raw: str) -> tuple[str, int]:
    """Split ``<epoch>:<seq>``; ValueError on anything else."""
    epoch, _sep, seq = raw.strip().partition(":")
    if (
        _EPOCH_RE.match(epoch) is None
        or not seq.isdigit()
        or not seq.isascii()
        or len(seq) > _MAX_SEQ_DIGITS
    ):
        raise ValueError("malformed progress event id")
    return epoch, int(seq)
