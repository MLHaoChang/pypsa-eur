"""Issue 01 / F-U1 — mocked speech transport contracts (no network, no credentials)."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from harness.providers import live_media as lm


KEY = "sk-test-never-leak"
PROFILE = lm.SpeechProfile(
    name="mock-speech",
    stt_endpoint="https://speech.invalid/v1/stt",
    stt_model="stt-mock-1",
    tts_endpoint="https://speech.invalid/v1/tts",
    tts_model="tts-mock-1",
    tts_voice="voice-a",
)
AUDIO = b"RIFF" + b"\x00" * 64


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    async def refuse(self, request):
        raise AssertionError(f"real network attempted: {request.url}")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse)


class Recorder:
    def __init__(self, handler):
        self.handler = handler
        self.requests: list[httpx.Request] = []

    def factory(self):
        def respond(request):
            self.requests.append(request)
            return self.handler(request)

        return httpx.AsyncClient(
            transport=httpx.MockTransport(respond), follow_redirects=False
        )


def stt(handler, **kwargs):
    rec = Recorder(handler)
    adapter = lm.HttpSttAdapter(
        PROFILE, credential=lambda: KEY, client_factory=rec.factory, **kwargs
    )
    return adapter, rec


def tts(handler, **kwargs):
    rec = Recorder(handler)
    adapter = lm.HttpTtsAdapter(
        PROFILE, credential=lambda: KEY, client_factory=rec.factory, **kwargs
    )
    return adapter, rec


def ok_text(text="  Limit battery to 7.5 MW  ", **extra):
    return lambda request: httpx.Response(200, json={"text": text, **extra})


def transcribe(adapter, **kwargs):
    kwargs.setdefault("mime", "audio/wav")
    kwargs.setdefault("language", "en")
    return asyncio.run(adapter.transcribe(AUDIO, **kwargs))


def refusal(call) -> lm.LiveMediaError:
    with pytest.raises(lm.LiveMediaError) as failure:
        call()
    assert failure.value.kind in lm.LIVE_MEDIA_ERROR_KINDS
    assert KEY not in failure.value.message
    return failure.value


def test_adapters_satisfy_provider_isolated_protocols():
    adapter_s, _ = stt(ok_text())
    adapter_t, _ = tts(lambda r: httpx.Response(200))
    assert isinstance(adapter_s, lm.SttAdapter)
    assert isinstance(adapter_t, lm.TtsAdapter)
    assert adapter_s.name == adapter_t.name == "mock-speech"


def test_final_utterance_preserves_numbers_and_units():
    adapter, rec = stt(ok_text("  Limit battery to 7.5 MW, 1,200 MWh  ", language="en-US", final=True))
    out = transcribe(adapter)
    assert out == lm.Utterance(
        text="Limit battery to 7.5 MW, 1,200 MWh", language="en", final=True
    )
    request = rec.requests[0]
    assert request.method == "POST"
    assert str(request.url) == PROFILE.stt_endpoint
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert b"stt-mock-1" in request.content


def test_single_attempt_no_retry_or_redirect_follow():
    adapter, rec = stt(lambda r: httpx.Response(302, headers={"location": "https://elsewhere.invalid/"}))
    assert refusal(lambda: transcribe(adapter)).kind == "provider_error"
    assert len(rec.requests) == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={"text": ""}),
        httpx.Response(200, json={"text": "   \n\t "}),
    ],
)
def test_empty_transcript_refused(response):
    adapter, _ = stt(lambda r: response)
    assert refusal(lambda: transcribe(adapter)).kind == "empty_transcript"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"not json"),
        httpx.Response(200, content=b"\xff\xfe\x00"),
        httpx.Response(200, json=["text"]),
        httpx.Response(200, json={"words": "hi"}),
        httpx.Response(200, json={"text": 7.5}),
        httpx.Response(200, json={"text": "ok", "language": 3}),
        httpx.Response(200, json={"text": "ok", "final": "yes"}),
        httpx.Response(200, json={"text": "bad\x00byte"}),
    ],
)
def test_malformed_transcript_refused(response):
    adapter, _ = stt(lambda r: response)
    assert refusal(lambda: transcribe(adapter)).kind == "malformed_response"


def test_partial_result_is_not_a_final_utterance():
    adapter, _ = stt(ok_text("Limit bat", final=False))
    assert refusal(lambda: transcribe(adapter)).kind == "partial_result"


def test_oversize_transcript_and_body_refused():
    adapter, _ = stt(ok_text("x" * 51), max_transcript_chars=50)
    assert refusal(lambda: transcribe(adapter)).kind == "oversize_output"

    adapter, _ = stt(lambda r: httpx.Response(200, content=b" " * 10_000), max_transcript_chars=50)
    assert refusal(lambda: transcribe(adapter)).kind == "oversize_output"

    adapter, _ = stt(
        lambda r: httpx.Response(200, headers={"content-length": "999999"}, content=b"{}"),
        max_transcript_chars=50,
    )
    assert refusal(lambda: transcribe(adapter)).kind == "oversize_output"


def test_invalid_or_oversize_input_never_reaches_provider():
    adapter, rec = stt(ok_text(), max_audio_bytes=32)
    assert refusal(lambda: transcribe(adapter)).kind == "oversize_input"
    assert refusal(
        lambda: asyncio.run(adapter.transcribe(b"", mime="audio/wav", language="en"))
    ).kind == "invalid_request"
    small, rec2 = stt(ok_text())
    assert refusal(lambda: transcribe(small, mime="text/plain")).kind == "invalid_request"
    assert rec.requests == [] and rec2.requests == []


def test_missing_credential_never_calls_provider():
    rec = Recorder(ok_text())
    adapter = lm.HttpSttAdapter(PROFILE, credential=lambda: None, client_factory=rec.factory)
    assert refusal(lambda: transcribe(adapter)).kind == "not_configured"
    assert rec.requests == []


@pytest.mark.parametrize(
    "status,kind",
    [(401, "provider_auth"), (403, "provider_auth"), (429, "provider_limit"), (500, "provider_error")],
)
def test_provider_status_mapping_never_leaks_key(status, kind):
    adapter, _ = stt(lambda r: httpx.Response(status, text=f"echo {KEY}"))
    assert refusal(lambda: transcribe(adapter)).kind == kind


def test_transport_failures_map_to_timeout_and_unavailable():
    def timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    def down(request):
        raise httpx.ConnectError("down", request=request)

    assert refusal(lambda: transcribe(stt(timeout)[0])).kind == "timeout"
    assert refusal(lambda: transcribe(stt(down)[0])).kind == "unavailable"


def test_language_pin_is_sent_normalized_and_enforced():
    adapter, rec = stt(ok_text("Grenze 7,5 MW", language="de"))
    out = transcribe(adapter, language="DE-de")
    assert out.language == "de-de"
    assert b'name="language"' in rec.requests[0].content
    assert b"de-de" in rec.requests[0].content

    mismatch, _ = stt(ok_text("limit 7.5 MW", language="en"))
    assert refusal(lambda: transcribe(mismatch, language="de")).kind == "language_mismatch"


@pytest.mark.parametrize("language", [None, "", "auto", "english please", "e", "en_US", 5])
def test_language_must_be_pinned_never_auto(language):
    adapter, rec = stt(ok_text())
    assert refusal(lambda: transcribe(adapter, language=language)).kind == "invalid_language"
    assert rec.requests == []


def test_cancel_before_call_sends_nothing():
    adapter, rec = stt(ok_text())
    token = lm.CancelToken()
    token.cancel()
    assert refusal(lambda: transcribe(adapter, cancel=token)).kind == "cancelled"
    assert rec.requests == []


def test_cancel_during_inflight_request_aborts_promptly():
    state = {}

    async def scenario():
        gate = asyncio.Event()
        state["started"] = asyncio.Event()

        async def handler(request):
            state["started"].set()
            await gate.wait()
            return httpx.Response(200, json={"text": "late"})

        rec = Recorder(handler)
        adapter = lm.HttpSttAdapter(
            PROFILE, credential=lambda: KEY, client_factory=rec.factory
        )
        token = lm.CancelToken()
        task = asyncio.ensure_future(
            adapter.transcribe(AUDIO, mime="audio/wav", language="en", cancel=token)
        )
        await asyncio.wait_for(state["started"].wait(), 2)
        token.cancel()
        with pytest.raises(lm.LiveMediaError) as failure:
            await asyncio.wait_for(task, 2)
        gate.set()
        return failure.value

    assert asyncio.run(scenario()).kind == "cancelled"


def test_tts_happy_path_and_language_pin():
    def respond(request):
        body = json.loads(request.content)
        assert body == {
            "model": "tts-mock-1",
            "voice": "voice-a",
            "input": "Battery limit is 7.5 MW.",
            "language": "en-GB",
            "response_format": "wav",
        }
        assert request.headers["authorization"] == f"Bearer {KEY}"
        return httpx.Response(200, content=AUDIO, headers={"content-type": "audio/wav; charset=x"})

    adapter, rec = tts(respond)
    out = asyncio.run(adapter.synthesize("Battery limit is 7.5 MW.", language="en-GB"))
    assert out == lm.SpeechAudio(audio=AUDIO, mime="audio/wav", language="en-GB")
    assert len(rec.requests) == 1


def test_tts_refusals():
    audio = lambda r: httpx.Response(200, content=AUDIO, headers={"content-type": "audio/wav"})

    def synth(adapter, text="hello", language="en", cancel=None):
        return lambda: asyncio.run(adapter.synthesize(text, language=language, cancel=cancel))

    adapter, rec = tts(audio, max_text_chars=10)
    assert refusal(synth(adapter, text="  ")).kind == "invalid_request"
    assert refusal(synth(adapter, text="x" * 11)).kind == "oversize_input"
    assert refusal(synth(adapter, language="auto")).kind == "invalid_language"
    token = lm.CancelToken()
    token.cancel()
    assert refusal(synth(adapter, cancel=token)).kind == "cancelled"
    assert rec.requests == []

    empty, _ = tts(lambda r: httpx.Response(200, content=b"", headers={"content-type": "audio/wav"}))
    assert refusal(synth(empty)).kind == "empty_audio"
    html, _ = tts(lambda r: httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"}))
    assert refusal(synth(html)).kind == "malformed_response"
    big, _ = tts(
        lambda r: httpx.Response(200, content=AUDIO, headers={"content-type": "audio/wav"}),
        max_audio_bytes=16,
    )
    assert refusal(synth(big)).kind == "oversize_output"
    denied, _ = tts(lambda r: httpx.Response(401, text=KEY))
    assert refusal(synth(denied)).kind == "provider_auth"
    nokey = lm.HttpTtsAdapter(PROFILE, credential=lambda: "", client_factory=Recorder(audio).factory)
    assert refusal(synth(nokey)).kind == "not_configured"


def test_default_factory_forbids_redirects():
    client = lm.default_client_factory()
    try:
        assert client.follow_redirects is False
    finally:
        asyncio.run(client.aclose())
