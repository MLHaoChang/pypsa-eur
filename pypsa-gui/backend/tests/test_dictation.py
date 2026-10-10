# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT

"""Dictation route, provider isolation, bounded uploads and safe errors."""

import asyncio
import io
import wave

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import main
from harness.providers import transcription
from services import dictation, llm_config


def wav_bytes():
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\0\0" * 160)
    return out.getvalue()


@pytest.fixture(autouse=True)
def isolation(monkeypatch):
    monkeypatch.delenv("PYPSA_GUI_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    dictation._active.clear()
    dictation._recent.clear()
    yield
    dictation._active.clear()
    dictation._recent.clear()


def post(client, *, audio=None, mime="audio/wav", **data):
    return client.post(
        "/api/dictation/transcribe",
        files={
            "file": (
                "untrusted-name.wav",
                wav_bytes() if audio is None else audio,
                mime,
            )
        },
        data=data,
    )


def test_config_is_safe_and_profile_independent(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret-never-returned")
    monkeypatch.setattr(
        llm_config,
        "resolve_profile",
        lambda *_: pytest.fail("Dictation used a chat profile"),
    )
    response = client.get("/api/dictation/config")
    assert response.status_code == 200
    assert response.json()["available"] is True
    assert response.json()["max_bytes"] == 10 * 1024 * 1024
    assert "secret-never-returned" not in response.text


def test_no_credentials_is_explicit(client):
    assert client.get("/api/dictation/config").json()["available"] is False
    response = post(client)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "not_configured"


@pytest.mark.parametrize("path", ["/api/dictation/config", "/api/dictation/transcribe"])
def test_authentication_required(path):
    with TestClient(main.app) as unauthenticated:
        response = (
            unauthenticated.get(path)
            if path.endswith("config")
            else post(unauthenticated)
        )
        assert response.status_code == 401


def test_csrf_protects_paid_post(client):
    from settings import get_settings

    response = client.post(
        "/api/dictation/transcribe",
        files={"file": ("audio.wav", wav_bytes(), "audio/wav")},
        headers={get_settings().csrf_header_name: "invalid"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "csrf_token_invalid"


@pytest.mark.parametrize(
    "language,hint", [("auto", None), ("de", "de"), ("zh", "zh"), ("en", "en")]
)
def test_language_glossary_and_exact_values(client, monkeypatch, language, hint):
    calls = []

    async def fake(*args):
        calls.append(args)
        return "Bus A-12: 7.5 MW; not 75 MWh."

    monkeypatch.setattr(transcription, "transcribe_audio", fake)
    response = post(client, language=language, glossary="PyPSA, GridSpine, MW, MWh")
    assert response.status_code == 200
    assert response.json()["text"] == "Bus A-12: 7.5 MW; not 75 MWh."
    assert calls[0][1:] == (
        "recording.wav",
        "audio/wav",
        hint,
        "PyPSA, GridSpine, MW, MWh",
    )


@pytest.mark.parametrize(
    "options",
    [
        {"language": "xx"},
        {"language": "de\n"},
        {"glossary": "a" * 501},
        {"glossary": "bad\0word"},
    ],
)
def test_invalid_options_never_call_provider(client, monkeypatch, options):
    async def fail(*_):
        pytest.fail("Paid request for invalid input")

    monkeypatch.setattr(transcription, "transcribe_audio", fail)
    assert post(client, **options).status_code == 422


@pytest.mark.parametrize(
    "audio,mime,status",
    [
        (b"", "audio/wav", 400),
        (b"not audio", "audio/wav", 415),
        (wav_bytes(), "text/plain", 415),
        (b"MZ" + b"\0" * 30, "audio/webm", 415),
    ],
)
def test_invalid_audio_never_call_provider(client, monkeypatch, audio, mime, status):
    async def fail(*_):
        pytest.fail("Paid request for invalid recording")

    monkeypatch.setattr(transcription, "transcribe_audio", fail)
    assert post(client, audio=audio, mime=mime).status_code == status


def test_oversize_is_bounded(client, monkeypatch):
    monkeypatch.setattr(dictation, "MAX_AUDIO_BYTES", 20)
    assert post(client).status_code == 413


@pytest.mark.parametrize(
    "audio,mime,extension",
    [
        (wav_bytes(), "audio/wav", ".wav"),
        (b"\x1aE\xdf\xa3" + b"\0" * 20, "audio/webm;codecs=opus", ".webm"),
        (b"\0\0\0\x20ftyp" + b"\0" * 20, "audio/mp4", ".m4a"),
        (b"ID3" + b"\0" * 20, "audio/mpeg", ".mp3"),
    ],
)
def test_container_formats(audio, mime, extension):
    filename, canonical = dictation.audio_format(audio, mime)
    assert filename.endswith(extension)
    assert ";" not in canonical


def test_concurrency_limit_releases_on_failure():
    with dictation.reserve_transcription("one"):
        with pytest.raises(HTTPException) as error:
            with dictation.reserve_transcription("one"):
                pytest.fail("Concurrent duplicate accepted")
        assert error.value.status_code == 429
    with pytest.raises(ValueError):
        with dictation.reserve_transcription("one"):
            raise ValueError("failure")
    assert not dictation._active


def test_global_concurrency_limit():
    from contextlib import ExitStack

    with ExitStack() as stack:
        for owner in ["1", "2", "3", "4"]:
            stack.enter_context(dictation.reserve_transcription(owner))
        with pytest.raises(HTTPException):
            stack.enter_context(dictation.reserve_transcription("5"))
    assert not dictation._active


def test_rate_limit_and_expiry(monkeypatch):
    monkeypatch.setattr(dictation.time, "monotonic", lambda: 100)
    for _ in range(8):
        with dictation.reserve_transcription("user"):
            pass
    with pytest.raises(HTTPException):
        with dictation.reserve_transcription("user"):
            pass
    monkeypatch.setattr(dictation.time, "monotonic", lambda: 161)
    with dictation.reserve_transcription("user"):
        pass


def mock_http(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(
        transcription.httpx,
        "AsyncClient",
        lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handler)),
    )


@pytest.mark.parametrize("language", [None, "de"])
def test_adapter_multipart_auto_and_manual(monkeypatch, language):
    def respond(request):
        assert str(request.url) == transcription.ENDPOINT
        assert request.headers["authorization"] == "Bearer test-key"
        body = request.content
        assert b"gpt-4o-mini-transcribe" in body
        assert b"Vocabulary: GridSpine, MWh" in body
        assert (b'name="language"' in body) == (language is not None)
        return httpx.Response(200, json={"text": "  7.5 MW  "})

    mock_http(monkeypatch, respond)
    result = asyncio.run(
        transcription.transcribe_audio(
            wav_bytes(), "recording.wav", "audio/wav", language, "GridSpine, MWh"
        )
    )
    assert result == "7.5 MW"


@pytest.mark.parametrize(
    "status,kind",
    [
        (401, "provider_auth"),
        (403, "provider_auth"),
        (429, "provider_limit"),
        (400, "provider_error"),
        (500, "provider_error"),
        (302, "provider_error"),
    ],
)
def test_provider_errors_are_sanitized_without_retries(
    monkeypatch, client, status, kind
):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(
            status,
            json={"error": {"message": "leaked-test-key private audio"}},
            headers={"Location": "https://untrusted.example"},
        )

    mock_http(monkeypatch, respond)
    response = post(client)
    assert response.json()["detail"]["code"] == kind
    assert response.status_code != 401
    assert "leaked-test-key" not in response.text
    assert len(calls) == 1
    assert not dictation._active


@pytest.mark.parametrize(
    "payload", [{}, {"text": None}, {"text": []}, {"text": "x" * 20001}]
)
def test_malformed_transcript(monkeypatch, payload):
    mock_http(monkeypatch, lambda _: httpx.Response(200, json=payload))
    with pytest.raises(transcription.TranscriptionError, match="invalid transcript"):
        asyncio.run(
            transcription.transcribe_audio(
                wav_bytes(), "recording.wav", "audio/wav", None, ""
            )
        )


def test_timeout_is_safe(client, monkeypatch):
    def timeout(request):
        raise httpx.ReadTimeout("private error key", request=request)

    mock_http(monkeypatch, timeout)
    response = post(client)
    assert response.status_code == 504
    assert response.json()["detail"]["code"] == "timeout"
    assert "private error" not in response.text


def test_silence_is_empty_not_fabricated(client, monkeypatch):
    mock_http(monkeypatch, lambda _: httpx.Response(200, json={"text": "  "}))
    assert post(client).json()["text"] == ""
