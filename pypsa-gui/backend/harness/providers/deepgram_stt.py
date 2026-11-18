# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT

"""Deepgram streaming recognition behind `live_media.StreamingSttAdapter` (live-voice 01).

One websocket per utterance stream: audio frames go up, `Results` messages come
down and become `TranscriptEvent`s (interim ones for captions, ``final`` ones
the bridge may submit). The model and language are pinned by the caller; the
adapter never picks, upgrades or falls back to another model, and never lets
the service auto-detect the language. Numbers are requested as digits
(``numerals``) and otherwise passed through verbatim.

The socket is opened through an injected ``connect`` factory so contracts run
against an in-memory fake with no network and no credentials; the default
factory imports ``websockets`` lazily. The key travels in a header only and
never reaches an error, a log line or ``repr``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlencode

from harness.providers.live_media import (
    CancelToken,
    Credential,
    LiveMediaError,
    TranscriptEvent,
    pin_language,
)

ENDPOINT = "wss://api.deepgram.com/v1/listen"


class _NoAuthorization(logging.Filter):
    """Drop any record that would print the Authorization header."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            return "authorization" not in record.getMessage().lower()
        except Exception:  # noqa: BLE001 — an unformattable record is dropped
            return False


# websockets logs every handshake header at DEBUG, the key included. Its own
# logger is pinned to INFO and filtered, so no level setting reaches the key.
WS_LOGGER = logging.getLogger(__name__ + ".websocket")
WS_LOGGER.setLevel(logging.INFO)
WS_LOGGER.addFilter(_NoAuthorization())
MAX_STREAM_BYTES = 1800 * 48_000 * 2  # a full 30 min session of 48 kHz mono PCM
MAX_FRAME_BYTES = 64 * 1024
MAX_MESSAGE_CHARS = 64 * 1024
MAX_EVENT_CHARS = 4000
MAX_EVENTS = 20_000
OPEN_TIMEOUT_S = 10.0
IDLE_TIMEOUT_S = 30.0

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MODEL_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class Socket(Protocol):
    """The three calls the adapter makes; ``recv`` returns None once closed."""

    async def send(self, data: bytes | str) -> None: ...

    async def recv(self) -> bytes | str | None: ...

    async def close(self) -> None: ...


Connect = Callable[
    [str, dict[str, str]], "contextlib.AbstractAsyncContextManager[Socket]"
]


@contextlib.asynccontextmanager
async def default_connect(url: str, headers: dict[str, str]) -> AsyncIterator[Socket]:
    try:
        from websockets.asyncio.client import connect
        from websockets.exceptions import ConnectionClosed, InvalidStatus
    except ImportError as exc:  # pragma: no cover - deployment without websockets
        raise LiveMediaError(
            "not_configured", "Streaming recognition is not installed.", 503
        ) from exc

    class _Socket:
        def __init__(self, ws: Any) -> None:
            self._ws = ws

        async def send(self, data: bytes | str) -> None:
            try:
                await self._ws.send(data)
            except ConnectionClosed as exc:
                raise LiveMediaError(
                    "unavailable", "The recognition stream closed early.", 503
                ) from exc

        async def recv(self) -> bytes | str | None:
            try:
                return await self._ws.recv()
            except ConnectionClosed as exc:
                if exc.rcvd is not None and exc.rcvd.code not in (1000, 1001):
                    raise LiveMediaError(
                        "provider_error", "The recognition stream ended with an error."
                    ) from exc
                return None

        async def close(self) -> None:
            await self._ws.close()

    try:
        ws = await connect(
            url,
            additional_headers=headers,
            open_timeout=OPEN_TIMEOUT_S,
            max_size=MAX_MESSAGE_CHARS,
            logger=WS_LOGGER,
        )
    except InvalidStatus as exc:
        status = exc.response.status_code
        if status in (401, 403):
            raise LiveMediaError(
                "provider_auth", "The recognition service rejected its credentials."
            ) from exc
        if status == 429:
            raise LiveMediaError(
                "provider_limit", "The recognition service reached its limit.", 429
            ) from exc
        raise LiveMediaError(
            "provider_error", "The recognition service refused the stream."
        ) from exc
    except (TimeoutError, OSError) as exc:
        raise LiveMediaError(
            "unavailable", "The recognition service could not be reached.", 503
        ) from exc
    try:
        yield _Socket(ws)
    finally:
        with contextlib.suppress(Exception):
            await ws.close()


@dataclass(frozen=True)
class DeepgramProfile:
    """The pinned recognition profile; nothing here has a default model."""

    model: str
    languages: frozenset[str]  # pinned primary tags this profile was validated for
    encoding: str | None = "linear16"  # None: containerized audio (wav), detected
    sample_rate: int | None = 48_000
    endpointing_ms: int = 300


@dataclass
class StreamUsage:
    """Actual usage of one stream: what was sent and what the service billed."""

    audio_bytes: int = 0
    audio_seconds_sent: float = 0.0
    provider_seconds: float | None = None
    events: int = 0
    finals: int = 0
    request_ids: list[str] = field(default_factory=list)


class DeepgramStreamingStt:
    name = "deepgram"

    def __init__(
        self,
        profile: DeepgramProfile,
        *,
        credential: Credential,
        connect: Connect = default_connect,
        endpoint: str = ENDPOINT,
        max_stream_bytes: int = MAX_STREAM_BYTES,
        idle_timeout_s: float = IDLE_TIMEOUT_S,
    ) -> None:
        if not _MODEL_RE.match(profile.model or ""):
            raise LiveMediaError("not_configured", "Pin a recognition model.", 503)
        if not profile.languages:
            raise LiveMediaError(
                "not_configured", "Pin the recognition languages.", 503
            )
        self._profile = profile
        self._credential = credential
        self._connect = connect
        self._endpoint = endpoint
        self._max_bytes = max_stream_bytes
        self._idle = idle_timeout_s
        self.usage = StreamUsage()

    def __repr__(self) -> str:
        return f"DeepgramStreamingStt(model={self._profile.model!r})"  # repr-ok: debug repr that hides the key

    def _url(self, language: str) -> str:
        params: dict[str, Any] = {
            "model": self._profile.model,
            "language": language,
            "interim_results": "true",
            "punctuate": "true",
            "numerals": "true",
            "endpointing": self._profile.endpointing_ms,
        }
        if self._profile.encoding:
            params.update(
                encoding=self._profile.encoding,
                sample_rate=self._profile.sample_rate,
                channels=1,
            )
        return f"{self._endpoint}?{urlencode(params)}"

    def _pin(self, language: Any) -> str:
        pinned = pin_language(language)
        if pinned.split("-")[0] not in self._profile.languages:
            raise LiveMediaError(
                "invalid_language",
                "This recognition profile was not validated for that language.",
                422,
            )
        return pinned

    def _bytes_per_second(self) -> float | None:
        if self._profile.encoding == "linear16" and self._profile.sample_rate:
            return float(self._profile.sample_rate * 2)
        return None

    async def _send_all(
        self, ws: Socket, frames: Any, cancel: CancelToken | None = None
    ) -> None:
        rate = self._bytes_per_second()
        async for frame in _aiter(frames):
            if cancel is not None and cancel.cancelled:
                raise LiveMediaError("cancelled", "The recognition was cancelled.", 499)
            if not isinstance(frame, (bytes, bytearray)):
                raise LiveMediaError(
                    "invalid_request", "Audio frames must be bytes.", 422
                )
            if not frame:
                continue
            if len(frame) > MAX_FRAME_BYTES:
                raise LiveMediaError(
                    "oversize_input", "An audio frame is too large.", 413
                )
            if self.usage.audio_bytes + len(frame) > self._max_bytes:
                raise LiveMediaError(
                    "oversize_input", "The audio stream is too long.", 413
                )
            await ws.send(bytes(frame))
            self.usage.audio_bytes += len(frame)
            if rate:
                self.usage.audio_seconds_sent = self.usage.audio_bytes / rate
        await ws.send(json.dumps({"type": "CloseStream"}))

    async def _recv(
        self, ws: Socket, cancel: CancelToken | None, sender: asyncio.Future
    ) -> bytes | str | None:
        """The next message, or raise: cancel, idle timeout, or a failed send."""
        if sender.done() and not sender.cancelled() and sender.exception():
            raise sender.exception()
        receive = asyncio.ensure_future(asyncio.wait_for(ws.recv(), self._idle))
        owned = {receive}
        if cancel is not None:
            owned.add(asyncio.ensure_future(cancel.wait()))
        watched = owned | ({sender} if not sender.done() else set())
        try:
            while True:
                await asyncio.wait(watched, return_when=asyncio.FIRST_COMPLETED)
                if sender.done() and not sender.cancelled() and sender.exception():
                    raise sender.exception()
                if receive.done():
                    try:
                        return receive.result()
                    except TimeoutError as exc:
                        raise LiveMediaError(
                            "timeout",
                            "The recognition service stopped responding.",
                            504,
                        ) from exc
                if cancel is not None and cancel.cancelled:
                    raise LiveMediaError(
                        "cancelled", "The recognition was cancelled.", 499
                    )
                watched = owned  # the sender finished cleanly; keep waiting
        finally:
            for task in owned:
                if not task.done():
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task

    def _parse(self, raw: bytes | str) -> TranscriptEvent | None:
        if isinstance(raw, bytes):
            raise LiveMediaError(
                "malformed_response", "The recognition service sent binary data."
            )
        if len(raw) > MAX_MESSAGE_CHARS:
            raise LiveMediaError(
                "oversize_output", "A recognition message is too large."
            )
        try:
            doc = json.loads(raw)
            if not isinstance(doc, dict):
                raise TypeError
        except (ValueError, TypeError) as exc:
            raise LiveMediaError(
                "malformed_response", "The recognition service sent invalid data."
            ) from exc
        kind = doc.get("type")
        if kind == "Metadata":
            duration = doc.get("duration")
            if isinstance(duration, (int, float)) and duration >= 0:
                self.usage.provider_seconds = float(duration)
            request_id = doc.get("request_id")
            if isinstance(request_id, str) and len(request_id) <= 64:
                self.usage.request_ids.append(request_id)
            return None
        if kind == "Error":
            raise LiveMediaError(
                "provider_error", "The recognition service reported an error."
            )
        if kind != "Results":
            return None  # SpeechStarted, UtteranceEnd and future events carry no text
        try:
            text = doc["channel"]["alternatives"][0]["transcript"]
            start = doc.get("start", 0.0)
            final = doc.get("is_final", False)
            if (
                not isinstance(text, str)
                or not isinstance(final, bool)
                or not isinstance(start, (int, float))
                or isinstance(start, bool)
                or start < 0
            ):
                raise TypeError
        except (KeyError, IndexError, TypeError) as exc:
            raise LiveMediaError(
                "malformed_response", "The recognition service sent an invalid result."
            ) from exc
        if len(text) > MAX_EVENT_CHARS:
            raise LiveMediaError("oversize_output", "A transcript segment is too long.")
        if _CONTROL_RE.search(text):
            raise LiveMediaError(
                "malformed_response", "The transcript contains control characters."
            )
        text = text.strip()
        if not text:
            return None
        self.usage.events += 1
        if self.usage.events > MAX_EVENTS:
            raise LiveMediaError("oversize_output", "Too many recognition events.")
        if final:
            self.usage.finals += 1
        return TranscriptEvent(
            segment_id=f"seg-{int(round(start * 1000))}",
            seq=self.usage.events,
            text=text,
            final=final,
        )

    async def stream(
        self,
        frames: Any,
        *,
        language: str,
        cancel: CancelToken | None = None,
    ) -> AsyncIterator[TranscriptEvent]:
        pinned = self._pin(language)
        if cancel is not None and cancel.cancelled:
            raise LiveMediaError("cancelled", "The recognition was cancelled.", 499)
        key = self._credential()
        if not key:
            raise LiveMediaError(
                "not_configured", "Speech recognition is not configured.", 503
            )
        self.usage = StreamUsage()
        headers = {"Authorization": f"Token {key}"}
        async with self._connect(self._url(pinned), headers) as ws:
            sender = asyncio.ensure_future(self._send_all(ws, frames, cancel))
            try:
                while True:
                    raw = await self._recv(ws, cancel, sender)
                    if raw is None:
                        break
                    event = self._parse(raw)
                    if event is not None:
                        yield event
                await sender
            finally:
                if not sender.done():
                    sender.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await sender


async def _aiter(frames: Any) -> AsyncIterator[Any]:
    """Frames as an async stream that always yields to the event loop.

    A synchronous source (a list, ``itertools.repeat``) never suspends on its
    own, so without the ``sleep(0)`` it would starve cancel and idle timeout.
    """
    if hasattr(frames, "__aiter__"):
        async for frame in frames:
            yield frame
            await asyncio.sleep(0)
    else:
        for frame in frames:
            yield frame
            await asyncio.sleep(0)
