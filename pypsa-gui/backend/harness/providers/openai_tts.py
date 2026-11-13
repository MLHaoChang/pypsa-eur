# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT

"""Hosted OpenAI speech synthesis behind `live_media.TtsAdapter` (live-voice 01).

The model and voice are pinned by configuration; there is no default model and
no automatic migration. A model on OpenAI's published retirement schedule is
refused (``model_retired``) from its shutdown date on, never swapped for its
recommended replacement: changing the model is an owner decision recorded in
the issue. The request shape is the `/v1/audio/speech` contract (model, voice,
input, response_format); it sends no language field, so the language support
of a profile is the list it was validated for.

Transport behaviour (bounded body, no redirects or retries, cooperative
cancel, credentials never in errors) is `live_media`'s shared POST.
"""

from __future__ import annotations

import datetime as _dt
import re
from collections.abc import Callable
from dataclasses import dataclass

from harness.providers.live_media import (
    MAX_TTS_AUDIO_BYTES,
    MAX_TTS_TEXT_CHARS,
    CancelToken,
    ClientFactory,
    Credential,
    LiveMediaError,
    SpeechAudio,
    _post,
    _run,
    default_client_factory,
    pin_language,
)

ENDPOINT = "https://api.openai.com/v1/audio/speech"

# Shutdown dates from OpenAI's deprecation schedule as recorded in
# docs/superpowers/assessments/2026-10-10-single-user-voice-cost.md (legacy
# standalone TTS, January 6, 2027). Refresh before production; a model missing
# here is not thereby supported, only not known to be retiring.
RETIREMENTS: dict[str, _dt.date] = {
    "tts-1": _dt.date(2027, 1, 6),
    "tts-1-hd": _dt.date(2027, 1, 6),
}

FORMATS = {
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "opus": "audio/opus",
    "pcm": "audio/pcm",
}
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


@dataclass(frozen=True)
class OpenAiTtsProfile:
    model: str
    voice: str
    languages: frozenset[str]  # pinned primary tags this profile was validated for
    response_format: str = "wav"


def retires_on(model: str) -> _dt.date | None:
    return RETIREMENTS.get(model)


class OpenAiTts:
    name = "openai-tts"

    def __init__(
        self,
        profile: OpenAiTtsProfile,
        *,
        credential: Credential,
        client_factory: ClientFactory = default_client_factory,
        endpoint: str = ENDPOINT,
        today: Callable[[], _dt.date] = _dt.date.today,
        max_text_chars: int = MAX_TTS_TEXT_CHARS,
        max_audio_bytes: int = MAX_TTS_AUDIO_BYTES,
    ) -> None:
        if not _ID_RE.match(profile.model or "") or not _ID_RE.match(
            profile.voice or ""
        ):
            raise LiveMediaError(
                "not_configured", "Pin a synthesis model and voice.", 503
            )
        if profile.response_format not in FORMATS:
            raise LiveMediaError(
                "not_configured", "The audio format is not supported.", 503
            )
        if not profile.languages:
            raise LiveMediaError("not_configured", "Pin the synthesis languages.", 503)
        self._profile = profile
        self._credential = credential
        self._factory = client_factory
        self._endpoint = endpoint
        self._today = today
        self._max_text = max_text_chars
        self._max_audio = max_audio_bytes

    def __repr__(self) -> str:
        return f"OpenAiTts(model={self._profile.model!r}, voice={self._profile.voice!r})"  # repr-ok: debug repr that hides the key

    def _check_model(self) -> None:
        shutdown = retires_on(self._profile.model)
        if shutdown is not None and self._today() >= shutdown:
            raise LiveMediaError(
                "provider_error",
                f"The synthesis model was retired on {shutdown.isoformat()}; "
                "choose a supported model.",
                503,
            )

    async def synthesize(
        self,
        text: str,
        *,
        language: str,
        cancel: CancelToken | None = None,
    ) -> SpeechAudio:
        pinned = pin_language(language)
        if pinned.split("-")[0] not in self._profile.languages:
            raise LiveMediaError(
                "invalid_language",
                "This synthesis profile was not validated for that language.",
                422,
            )
        if not isinstance(text, str) or not text.strip():
            raise LiveMediaError("invalid_request", "Nothing to synthesize.", 422)
        if len(text) > self._max_text:
            raise LiveMediaError("oversize_input", "Text exceeds the size limit.", 413)
        self._check_model()
        key = self._credential()
        if not key:
            raise LiveMediaError(
                "not_configured", "Speech synthesis is not configured.", 503
            )
        response, audio = await _run(
            lambda: _post(
                self._factory,
                self._endpoint,
                key=key,
                max_bytes=self._max_audio,
                json={
                    "model": self._profile.model,
                    "voice": self._profile.voice,
                    "input": text,
                    "response_format": self._profile.response_format,
                },
            ),
            cancel,
        )
        mime = response.headers.get("content-type", "").split(";")[0].strip().lower()
        expected = FORMATS[self._profile.response_format]
        if not mime.startswith("audio/") and mime != "application/octet-stream":
            raise LiveMediaError(
                "malformed_response", "The speech service did not return audio."
            )
        if not audio:
            raise LiveMediaError("empty_audio", "The speech service returned no audio.")
        return SpeechAudio(audio=audio, mime=expected, language=pinned)
