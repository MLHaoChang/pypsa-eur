# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT

"""Provider-neutral validation and cost/concurrency bounds for dictation."""

from __future__ import annotations

from collections import deque
from contextlib import contextmanager
import threading
import time

from fastapi import HTTPException

MAX_AUDIO_BYTES = 10 * 1024 * 1024
MAX_RECORDING_SECONDS = 120  # Client segment limit; bytes are enforced server-side.
LANGUAGES = (
    "en",
    "de",
    "zh",
    "fr",
    "es",
    "it",
    "pt",
    "ja",
    "ko",
    "ar",
    "hi",
    "nl",
    "pl",
    "uk",
)
_lock = threading.Lock()
_active: set[str] = set()
_recent: dict[str, deque[float]] = {}


def validate_options(language: str, glossary: str) -> tuple[str | None, str]:
    if language != "auto" and language not in LANGUAGES:
        raise HTTPException(
            422,
            detail={
                "code": "invalid_language",
                "message": "Choose Auto or a supported dictation language.",
            },
        )
    if len(glossary) > 500 or any(ord(c) < 32 and c not in "\n\t" for c in glossary):
        raise HTTPException(
            422,
            detail={
                "code": "invalid_glossary",
                "message": "Terminology must be at most 500 characters and contain no control characters.",
            },
        )
    return (None if language == "auto" else language), glossary.strip()


def audio_format(audio: bytes, mime: str | None) -> tuple[str, str]:
    """Ignore user filenames. Check recording container signatures before upload."""
    declared = (mime or "").split(";", 1)[0].strip().lower()
    if not audio:
        raise HTTPException(
            400, detail={"code": "empty_audio", "message": "The recording is empty."}
        )
    formats = [
        (
            len(audio) >= 12 and audio[:4] == b"RIFF" and audio[8:12] == b"WAVE",
            {"audio/wav", "audio/wave", "audio/x-wav"},
            "recording.wav",
            "audio/wav",
        ),
        (
            len(audio) >= 16 and audio[:4] == b"\x1aE\xdf\xa3",
            {"audio/webm", "video/webm"},
            "recording.webm",
            "audio/webm",
        ),
        (
            len(audio) >= 16 and audio[4:8] == b"ftyp",
            {"audio/mp4", "video/mp4", "audio/m4a", "audio/x-m4a"},
            "recording.m4a",
            "audio/mp4",
        ),
        (
            len(audio) >= 16
            and (audio[:3] == b"ID3" or (audio[0] == 255 and audio[1] & 224 == 224)),
            {"audio/mpeg", "audio/mp3"},
            "recording.mp3",
            "audio/mpeg",
        ),
    ]
    for matches, allowed, filename, canonical in formats:
        if matches and declared in allowed:
            return filename, canonical
    raise HTTPException(
        415,
        detail={
            "code": "unsupported_audio",
            "message": "Use a WAV, WebM, MP4/M4A or MP3 audio recording.",
        },
    )


@contextmanager
def reserve_transcription(owner: str):
    """One in-flight call per user, four globally, eight attempts/minute/user."""
    now = time.monotonic()
    with _lock:
        for key in list(_recent):
            if not _recent[key] or _recent[key][-1] <= now - 60:
                del _recent[key]
        if owner in _active or len(_active) >= 4:
            raise HTTPException(
                429,
                detail={
                    "code": "dictation_busy",
                    "message": "Transcription is busy. Wait for the current recording to finish.",
                },
            )
        history = _recent.get(owner, deque())
        while history and history[0] <= now - 60:
            history.popleft()
        if len(history) >= 8 or (owner not in _recent and len(_recent) >= 2048):
            raise HTTPException(
                429,
                detail={
                    "code": "dictation_rate_limit",
                    "message": "Too many recordings. Wait a minute before trying again.",
                },
            )
        history.append(now)
        _recent[owner] = history
        _active.add(owner)
    try:
        yield
    finally:
        with _lock:
            _active.discard(owner)
