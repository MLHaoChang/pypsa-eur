# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT

"""Provider-isolated speech adapters for Companion Live (issue 01 / F-U1).

`SttAdapter` and `TtsAdapter` are the only seams the Live bridge may depend on;
a vendor is a concrete class (or `SpeechProfile`) behind them. The HTTP adapters
here speak a generic multipart/JSON contract over an *injected* httpx client
factory so contracts can be proven against `httpx.MockTransport` with no network
and no credentials. No vendor endpoint, model or price is baked in: selection is
conditional on measured gates, so the profile must be supplied by the caller.

Guarantees shared by every adapter: bounded input and output, empty/malformed/
oversize refusal, an explicit pinned language, cooperative cancellation, no
redirects, no retries, no fallback to another model, and credentials that never
reach an error message. The transcript is returned verbatim apart from edge
whitespace; numbers and units are never rewritten.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import httpx

LIVE_MEDIA_ERROR_KINDS: frozenset[str] = frozenset(
    [
        "not_configured",
        "invalid_request",
        "invalid_language",
        "oversize_input",
        "oversize_output",
        "empty_transcript",
        "empty_audio",
        "malformed_response",
        "partial_result",
        "language_mismatch",
        "cancelled",
        "provider_auth",
        "provider_limit",
        "provider_error",
        "timeout",
        "unavailable",
    ]
)

MAX_AUDIO_BYTES = 5 * 1024 * 1024
MAX_TRANSCRIPT_CHARS = 4000
MAX_TTS_TEXT_CHARS = 2000
MAX_TTS_AUDIO_BYTES = 8 * 1024 * 1024
DEFAULT_TIMEOUT_S = 20.0

_LANGUAGE_RE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8}){0,3}$")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class LiveMediaError(Exception):
    def __init__(self, kind: str, message: str, status: int = 502):
        super().__init__(message)
        self.kind = kind if kind in LIVE_MEDIA_ERROR_KINDS else "provider_error"
        self.message, self.status = message, status


@dataclass(frozen=True)
class Utterance:
    text: str
    language: str
    final: bool = True


@dataclass(frozen=True)
class SpeechAudio:
    audio: bytes
    mime: str
    language: str


@dataclass(frozen=True)
class SpeechProfile:
    """Everything vendor-specific; adapters hold no other provider knowledge."""

    name: str
    stt_endpoint: str
    stt_model: str
    tts_endpoint: str
    tts_model: str
    tts_voice: str
    tts_format: str = "wav"


class CancelToken:
    """Single-event-loop cancel signal shared by the bridge and an adapter call."""

    def __init__(self) -> None:
        self._event = asyncio.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    async def wait(self) -> None:
        await self._event.wait()


@runtime_checkable
class SttAdapter(Protocol):
    name: str

    async def transcribe(
        self,
        audio: bytes,
        *,
        mime: str,
        language: str,
        cancel: CancelToken | None = None,
    ) -> Utterance: ...


@runtime_checkable
class TtsAdapter(Protocol):
    name: str

    async def synthesize(
        self,
        text: str,
        *,
        language: str,
        cancel: CancelToken | None = None,
    ) -> SpeechAudio: ...


ClientFactory = Callable[[], httpx.AsyncClient]
Credential = Callable[[], "str | None"]


def default_client_factory() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=DEFAULT_TIMEOUT_S, follow_redirects=False)


def pin_language(language: Any) -> str:
    """Return the normalized pinned tag, or refuse. Auto-detect is not allowed."""
    if not isinstance(language, str) or not _LANGUAGE_RE.match(language):
        raise LiveMediaError(
            "invalid_language",
            "Pin an explicit language tag such as 'en' or 'de-DE'.",
            422,
        )
    head, *rest = language.split("-")
    return "-".join([head.lower(), *rest])


def _primary(tag: str) -> str:
    return tag.split("-")[0].lower()


async def _run(make_call: Callable[[], Any], cancel: CancelToken | None) -> Any:
    """Run one provider call; cancelling aborts the in-flight request."""
    if cancel is not None and cancel.cancelled:
        raise LiveMediaError("cancelled", "The request was cancelled.", 499)
    call = asyncio.ensure_future(make_call())
    if cancel is None:
        return await call
    waiter = asyncio.ensure_future(cancel.wait())
    try:
        await asyncio.wait({call, waiter}, return_when=asyncio.FIRST_COMPLETED)
        if call.done():
            return call.result()
        raise LiveMediaError("cancelled", "The request was cancelled.", 499)
    finally:
        for task in (call, waiter):
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task


def _raise_for_status(response: httpx.Response) -> None:
    status = response.status_code
    if status in (401, 403):
        # Provider authentication is not application/session authentication.
        raise LiveMediaError(
            "provider_auth", "The speech service rejected its credentials."
        )
    if status == 429:
        raise LiveMediaError(
            "provider_limit", "The speech service reached its rate or credit limit.", 429
        )
    if not 200 <= status < 300:
        raise LiveMediaError("provider_error", "The speech service could not process this request.")


async def _post(
    factory: ClientFactory,
    endpoint: str,
    *,
    key: str,
    max_bytes: int,
    **body: Any,
) -> tuple[httpx.Response, bytes]:
    """POST once (no redirects/retries) and read at most ``max_bytes`` of body."""
    try:
        async with factory() as client:
            request = client.build_request(
                "POST", endpoint, headers={"Authorization": f"Bearer {key}"}, **body
            )
            response = await client.send(request, stream=True)
            try:
                _raise_for_status(response)
                declared = response.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > max_bytes:
                    raise LiveMediaError(
                        "oversize_output", "The speech service returned too much data."
                    )
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise LiveMediaError(
                            "oversize_output",
                            "The speech service returned too much data.",
                        )
                    chunks.append(chunk)
            finally:
                await response.aclose()
            return response, b"".join(chunks)
    except httpx.TimeoutException as exc:
        raise LiveMediaError("timeout", "The speech service timed out.", 504) from exc
    except httpx.HTTPError as exc:
        raise LiveMediaError(
            "unavailable", "The speech service could not be reached.", 503
        ) from exc


class HttpSttAdapter:
    """Final-utterance recognition over multipart upload; JSON `{text, language?, final?}`."""

    def __init__(
        self,
        profile: SpeechProfile,
        *,
        credential: Credential,
        client_factory: ClientFactory = default_client_factory,
        max_audio_bytes: int = MAX_AUDIO_BYTES,
        max_transcript_chars: int = MAX_TRANSCRIPT_CHARS,
    ) -> None:
        self.name = profile.name
        self._profile = profile
        self._credential = credential
        self._factory = client_factory
        self._max_audio = max_audio_bytes
        self._max_chars = max_transcript_chars

    async def transcribe(
        self,
        audio: bytes,
        *,
        mime: str,
        language: str,
        cancel: CancelToken | None = None,
    ) -> Utterance:
        pinned = pin_language(language)
        if not isinstance(audio, (bytes, bytearray)) or not audio:
            raise LiveMediaError("invalid_request", "Audio is empty.", 422)
        if len(audio) > self._max_audio:
            raise LiveMediaError("oversize_input", "Audio exceeds the size limit.", 413)
        if not isinstance(mime, str) or not mime.startswith("audio/"):
            raise LiveMediaError("invalid_request", "Audio type is not supported.", 415)
        key = self._credential()
        if not key:
            raise LiveMediaError(
                "not_configured", "Speech recognition is not configured.", 503
            )
        _, payload = await _run(
            lambda: _post(
                self._factory,
                self._profile.stt_endpoint,
                key=key,
                max_bytes=self._max_chars * 8 + 4096,
                data={"model": self._profile.stt_model, "language": pinned},
                files={"file": ("utterance", bytes(audio), mime)},
            ),
            cancel,
        )
        return self._parse(payload, pinned)

    def _parse(self, payload: bytes, pinned: str) -> Utterance:
        try:
            doc = json.loads(payload)
            if not isinstance(doc, dict):
                raise TypeError
            text = doc["text"]
            if not isinstance(text, str):
                raise TypeError
            language = doc.get("language")
            if language is not None and not isinstance(language, str):
                raise TypeError
            final = doc.get("final", True)
            if not isinstance(final, bool):
                raise TypeError
        except (ValueError, KeyError, TypeError) as exc:
            raise LiveMediaError(
                "malformed_response", "The speech service returned an invalid transcript."
            ) from exc
        if len(text) > self._max_chars:
            raise LiveMediaError(
                "oversize_output", "The transcript exceeds the size limit."
            )
        if _CONTROL_RE.search(text):
            raise LiveMediaError(
                "malformed_response", "The transcript contains control characters."
            )
        if not final:
            raise LiveMediaError(
                "partial_result", "Only a final utterance may be accepted."
            )
        if language is not None and _primary(language) != _primary(pinned):
            raise LiveMediaError(
                "language_mismatch",
                "The transcript language differs from the pinned language.",
            )
        text = text.strip()
        if not text:
            raise LiveMediaError("empty_transcript", "No speech was recognised.", 422)
        return Utterance(text=text, language=pinned, final=True)


class HttpTtsAdapter:
    """Synthesis over a JSON request; the response body is audio bytes."""

    def __init__(
        self,
        profile: SpeechProfile,
        *,
        credential: Credential,
        client_factory: ClientFactory = default_client_factory,
        max_text_chars: int = MAX_TTS_TEXT_CHARS,
        max_audio_bytes: int = MAX_TTS_AUDIO_BYTES,
    ) -> None:
        self.name = profile.name
        self._profile = profile
        self._credential = credential
        self._factory = client_factory
        self._max_text = max_text_chars
        self._max_audio = max_audio_bytes

    async def synthesize(
        self,
        text: str,
        *,
        language: str,
        cancel: CancelToken | None = None,
    ) -> SpeechAudio:
        pinned = pin_language(language)
        if not isinstance(text, str) or not text.strip():
            raise LiveMediaError("invalid_request", "Nothing to synthesize.", 422)
        if len(text) > self._max_text:
            raise LiveMediaError("oversize_input", "Text exceeds the size limit.", 413)
        key = self._credential()
        if not key:
            raise LiveMediaError(
                "not_configured", "Speech synthesis is not configured.", 503
            )
        response, audio = await _run(
            lambda: _post(
                self._factory,
                self._profile.tts_endpoint,
                key=key,
                max_bytes=self._max_audio,
                json={
                    "model": self._profile.tts_model,
                    "voice": self._profile.tts_voice,
                    "input": text,
                    "language": pinned,
                    "response_format": self._profile.tts_format,
                },
            ),
            cancel,
        )
        mime = response.headers.get("content-type", "").split(";")[0].strip().lower()
        if not mime.startswith("audio/"):
            raise LiveMediaError(
                "malformed_response", "The speech service did not return audio."
            )
        if not audio:
            raise LiveMediaError("empty_audio", "The speech service returned no audio.")
        return SpeechAudio(audio=audio, mime=mime, language=pinned)
