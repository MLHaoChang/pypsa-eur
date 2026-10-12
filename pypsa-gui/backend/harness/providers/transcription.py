# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT

"""Recorded-audio adapter, independent of chat profiles and tool dispatch."""

from __future__ import annotations

import os

import httpx

MODEL = "gpt-4o-mini-transcribe"
ENDPOINT = "https://api.openai.com/v1/audio/transcriptions"


class TranscriptionError(Exception):
    def __init__(self, kind: str, message: str, status: int = 502):
        super().__init__(message)
        self.kind, self.message, self.status = kind, message, status


def credential() -> str | None:
    # Read at call time so existing Settings key updates need no restart.
    return os.environ.get("PYPSA_GUI_OPENAI_API_KEY") or os.environ.get(
        "OPENAI_API_KEY"
    )


async def transcribe_audio(
    audio: bytes,
    filename: str,
    mime: str,
    language: str | None,
    glossary: str,
) -> str:
    key = credential()
    if not key:
        raise TranscriptionError(
            "not_configured",
            "AI dictation needs an OpenAI API key. Use browser dictation or configure the key in Assistant settings.",
            503,
        )
    data = {"model": MODEL, "response_format": "json"}
    if language:
        data["language"] = language
    if glossary:
        data["prompt"] = "Vocabulary: " + glossary
    try:
        # No redirects, SDK retries, automatic fallback, or logging of audio/key.
        async with httpx.AsyncClient(timeout=45, follow_redirects=False) as client:
            response = await client.post(
                ENDPOINT,
                headers={"Authorization": f"Bearer {key}"},
                data=data,
                files={"file": (filename, audio, mime)},
            )
    except httpx.TimeoutException as exc:
        raise TranscriptionError(
            "timeout", "Transcription timed out. Try a shorter recording.", 504
        ) from exc
    except httpx.HTTPError as exc:
        raise TranscriptionError(
            "unavailable", "The transcription service could not be reached.", 503
        ) from exc
    if response.status_code in (401, 403):
        # Provider authentication is NOT application/session authentication.
        raise TranscriptionError(
            "provider_auth",
            "The transcription service rejected its API key or access. Check the OpenAI key and permissions.",
        )
    if response.status_code == 429:
        raise TranscriptionError(
            "provider_limit",
            "The transcription service reached its rate or credit limit. Try later.",
            429,
        )
    if (
        response.status_code >= 400
        or response.status_code < 200
        or response.status_code >= 300
    ):
        raise TranscriptionError(
            "provider_error",
            "The transcription service could not process this recording. Try a shorter recording or browser dictation.",
        )
    try:
        text = response.json()["text"]
        if not isinstance(text, str) or len(text) > 20000:
            raise ValueError("Invalid transcript")
    except (ValueError, KeyError, TypeError) as exc:
        raise TranscriptionError(
            "invalid_response",
            "The transcription service returned an invalid transcript.",
        ) from exc
    # Never rewrite numbers, units or words through a chat model.
    return text.strip()
