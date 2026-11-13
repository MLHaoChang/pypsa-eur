"""Live-voice 01 — hosted LiveKit / Deepgram / OpenAI speech adapters, mocked.

No network and no credentials: httpx runs over `MockTransport`, the Deepgram
socket is an in-memory fake, and the real transports are patched to fail.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import datetime as dt
import hashlib
import hmac
import json
import logging
import time

import httpx
import pytest

from harness.providers import live_media as lm
from harness.providers.deepgram_stt import DeepgramProfile, DeepgramStreamingStt
from harness.providers.livekit_media import (
    MAX_API_RESPONSE_BYTES,
    LiveKitConfig,
    LiveKitTransport,
)
from harness.providers.openai_tts import OpenAiTts, OpenAiTtsProfile

API_KEY = "APIlkTESTKEY"
SECRET = "lk-secret-never-leak-0123456789"
DG_KEY = "dg-key-never-leak"
OA_KEY = "sk-oa-never-leak"
BRIDGE = "bridge-token-never-leak"
WSS = "wss://probe.livekit.invalid"


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    def refuse(self, request):
        raise AssertionError(f"real network attempted: {request.url}")

    async def refuse_async(self, request):
        raise AssertionError(f"real network attempted: {request.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse_async)


def _claims(token: str, secret: str = SECRET) -> dict:
    head, body, sig = token.split(".")
    expected = hmac.new(
        secret.encode(), f"{head}.{body}".encode(), hashlib.sha256
    ).digest()
    assert base64.urlsafe_b64decode(sig + "==") == expected, "bad signature"
    assert json.loads(base64.urlsafe_b64decode(head + "=="))["alg"] == "HS256"
    return json.loads(base64.urlsafe_b64decode(body + "=="))


# ── LiveKit ──────────────────────────────────────────────────────────────────


class LiveKitApi:
    def __init__(self, fail: dict[str, httpx.Response] | None = None):
        self.calls: list[tuple[str, dict, dict]] = []
        self.fail = fail or {}

    def factory(self):
        def respond(request: httpx.Request) -> httpx.Response:
            method = request.url.path.rsplit("/", 1)[-1]
            claims = _claims(request.headers["authorization"].removeprefix("Bearer "))
            self.calls.append((method, json.loads(request.content), claims))
            if method in self.fail:
                return self.fail[method]
            if method == "CreateRoom":
                return httpx.Response(
                    200, json={"name": json.loads(request.content)["name"]}
                )
            return httpx.Response(200, json={})

        return httpx.Client(
            transport=httpx.MockTransport(respond), follow_redirects=False
        )

    def methods(self) -> list[str]:
        return [c[0] for c in self.calls]


def livekit(api: LiveKitApi, agent: str = "pypsa-audio") -> LiveKitTransport:
    config = LiveKitConfig(
        url=WSS, api_key=API_KEY, api_secret=SECRET, agent_name=agent
    )
    return LiveKitTransport(config, client_factory=api.factory)


def _open(transport: LiveKitTransport, **kw):
    args = dict(
        room="pypsa-abc",
        identity="owner-7",
        ttl_s=600,
        bridge_token=BRIDGE,
        language="en",
    )
    args.update(kw)
    return transport.open_session(**args)


def test_livekit_implements_media_transport():
    assert isinstance(livekit(LiveKitApi()), lm.MediaTransport)


def test_livekit_open_creates_bounded_room_dispatches_worker_and_scopes_grant():
    api = LiveKitApi()
    before = time.time()
    grant = _open(livekit(api))
    assert api.methods() == ["CreateRoom", "CreateDispatch"]
    _, room_body, room_claims = api.calls[0]
    assert room_body["name"] == "pypsa-abc"
    assert room_body["max_participants"] == 3
    assert (
        0 < room_body["empty_timeout"] <= 60
        and 0 < room_body["departure_timeout"] <= 60
    )
    assert room_claims["video"] == {"roomCreate": True}
    assert room_claims["exp"] - room_claims["nbf"] <= 65
    _, dispatch, dispatch_claims = api.calls[1]
    assert dispatch["agent_name"] == "pypsa-audio" and dispatch["room"] == "pypsa-abc"
    assert json.loads(dispatch["metadata"]) == {
        "room": "pypsa-abc",
        "language": "en",
        "bridge_token": BRIDGE,
    }
    assert dispatch_claims["video"] == {"roomAdmin": True, "room": "pypsa-abc"}

    assert grant.url == WSS
    claims = _claims(grant.token)
    assert claims["iss"] == API_KEY and claims["sub"] == "owner-7"
    video = claims["video"]
    assert video["room"] == "pypsa-abc" and video["roomJoin"] is True
    assert video["canPublishSources"] == ["microphone"]
    assert video["canPublishData"] is False
    assert not {"roomAdmin", "roomCreate", "roomList", "roomRecord"} & set(video)
    assert before + 590 <= grant.expires_at <= time.time() + 601
    assert SECRET not in grant.token and BRIDGE not in grant.token
    assert SECRET not in repr(grant) and grant.token not in repr(grant)


def test_livekit_grant_lifetime_is_capped():
    grant = _open(livekit(LiveKitApi()), ttl_s=86_400)
    claims = _claims(grant.token)
    assert claims["exp"] - int(time.time()) <= 900


def test_livekit_without_agent_name_does_not_dispatch():
    api = LiveKitApi()
    _open(livekit(api, agent=""))
    assert api.methods() == ["CreateRoom"]


def test_livekit_secret_never_on_the_wire_or_in_repr():
    api = LiveKitApi()
    transport = livekit(api)
    _open(transport)
    for _, body, claims in api.calls:
        assert SECRET not in json.dumps(body) and SECRET not in json.dumps(claims)
    assert SECRET not in repr(transport) and API_KEY not in repr(transport)
    assert SECRET not in repr(transport._config)


def test_livekit_failed_dispatch_deletes_the_room():
    api = LiveKitApi(
        fail={"CreateDispatch": httpx.Response(500, json={"code": "internal"})}
    )
    with pytest.raises(lm.LiveMediaError) as err:
        _open(livekit(api))
    assert err.value.kind == "provider_error"
    assert api.methods() == ["CreateRoom", "CreateDispatch", "DeleteRoom"]
    assert BRIDGE not in str(err.value) and SECRET not in str(err.value)


@pytest.mark.parametrize(
    "response,kind",
    [
        (httpx.Response(401, json={"code": "unauthenticated"}), "provider_auth"),
        (httpx.Response(429, json={"code": "resource_exhausted"}), "provider_limit"),
        (httpx.Response(500, text="boom"), "provider_error"),
        (httpx.Response(200, content=b"x" * (70 * 1024)), "oversize_output"),
        (httpx.Response(200, text="not json"), "malformed_response"),
    ],
)
def test_livekit_create_room_failures_are_typed(response, kind):
    api = LiveKitApi(fail={"CreateRoom": response})
    with pytest.raises(lm.LiveMediaError) as err:
        _open(livekit(api))
    assert err.value.kind == kind
    assert "CreateDispatch" not in api.methods()


def test_livekit_timeout_is_typed():
    def factory():
        def respond(request):
            raise httpx.ReadTimeout("slow", request=request)

        return httpx.Client(transport=httpx.MockTransport(respond))

    transport = LiveKitTransport(
        LiveKitConfig(url=WSS, api_key=API_KEY, api_secret=SECRET),
        client_factory=factory,
    )
    with pytest.raises(lm.LiveMediaError) as err:
        _open(transport)
    assert err.value.kind == "timeout"


def test_livekit_close_is_idempotent_and_raises_real_failures():
    gone = LiveKitApi(
        fail={"DeleteRoom": httpx.Response(404, json={"code": "not_found"})}
    )
    livekit(gone).close_session(room="pypsa-abc")
    livekit(gone).close_session(room="pypsa-abc")
    assert gone.methods() == ["DeleteRoom", "DeleteRoom"]
    broken = LiveKitApi(fail={"DeleteRoom": httpx.Response(503, text="down")})
    with pytest.raises(lm.LiveMediaError):
        livekit(broken).close_session(room="pypsa-abc")


@pytest.mark.parametrize(
    "url",
    [
        "https://x.livekit.cloud",
        "wss://x.livekit.cloud/rtc",
        "wss://x.livekit.cloud?a=1",
        "wss://u:p@x.livekit.cloud",
        "wss://",
        "",
    ],
)
def test_livekit_refuses_unsafe_urls(url):
    with pytest.raises(lm.LiveMediaError) as err:
        LiveKitTransport(LiveKitConfig(url=url, api_key=API_KEY, api_secret=SECRET))
    assert err.value.kind == "not_configured"


def test_livekit_refuses_bad_names_before_any_call():
    api = LiveKitApi()
    for kw in ({"room": "bad room"}, {"identity": "x" * 80}, {"bridge_token": ""}):
        with pytest.raises(lm.LiveMediaError) as err:
            _open(livekit(api), **kw)
        assert err.value.kind == "invalid_request"
    assert api.calls == []


def test_live_speech_accepts_the_livekit_grant():
    """The LC-07 session's own grant check (bounded lifetime) passes."""
    from services import live_speech

    api = LiveKitApi()
    session = {"room": "pypsa-sess1", "language": "de"}
    grant = live_speech._open_room(livekit(api), session, "7", BRIDGE)
    assert isinstance(grant, lm.MediaGrant)
    assert _claims(grant.token)["sub"] == "owner-7"


# ── Deepgram streaming ───────────────────────────────────────────────────────


class FakeSocket:
    def __init__(self, messages, *, block=False):
        self.messages = list(messages)
        self.block = block
        self.sent: list = []

    async def send(self, data):
        self.sent.append(data)

    async def recv(self):
        await asyncio.sleep(0)
        if self.messages:
            return self.messages.pop(0)
        if self.block:
            await asyncio.Event().wait()
        return None

    async def close(self):
        pass


class Connector:
    def __init__(self, socket):
        self.socket = socket
        self.url = self.headers = None

    @contextlib.asynccontextmanager
    async def __call__(self, url, headers):
        self.url, self.headers = url, headers
        yield self.socket


def result(text, *, final, start=0.0):
    return json.dumps(
        {
            "type": "Results",
            "start": start,
            "duration": 1.0,
            "is_final": final,
            "channel": {"alternatives": [{"transcript": text, "confidence": 0.9}]},
        }
    )


PROFILE = DeepgramProfile(model="nova-3", languages=frozenset({"en", "de"}))


def deepgram(socket, *, key=DG_KEY, **kw):
    connector = Connector(socket)
    adapter = DeepgramStreamingStt(
        PROFILE, credential=lambda: key, connect=connector, **kw
    )
    return adapter, connector


async def collect(adapter, frames, language="en", cancel=None):
    return [e async for e in adapter.stream(frames, language=language, cancel=cancel)]


def test_deepgram_implements_streaming_protocol():
    assert isinstance(deepgram(FakeSocket([]))[0], lm.StreamingSttAdapter)


def test_deepgram_streams_interim_and_final_events_verbatim_with_usage():
    socket = FakeSocket(
        [
            result("store 250", final=False, start=1.5),
            result("Store 250 MWh at 48 €/MWh.", final=True, start=1.5),
            json.dumps({"type": "UtteranceEnd"}),
            result("   ", final=True, start=3.0),
            json.dumps({"type": "Metadata", "duration": 2.0, "request_id": "req-1"}),
        ]
    )
    adapter, connector = deepgram(socket)
    frames = [b"\x00\x01" * 4800, b"\x00\x01" * 4800]
    events = asyncio.run(collect(adapter, frames))
    assert [(e.segment_id, e.seq, e.text, e.final) for e in events] == [
        ("seg-1500", 1, "store 250", False),
        ("seg-1500", 2, "Store 250 MWh at 48 €/MWh.", True),
    ]
    assert socket.sent[:2] == frames and json.loads(socket.sent[-1]) == {
        "type": "CloseStream"
    }
    usage = adapter.usage
    assert usage.audio_bytes == 19_200 and usage.audio_seconds_sent == pytest.approx(
        0.2
    )
    assert (
        usage.provider_seconds == 2.0
        and usage.finals == 1
        and usage.request_ids == ["req-1"]
    )
    assert "model=nova-3" in connector.url and "language=en" in connector.url
    assert "numerals=true" in connector.url and "detect_language" not in connector.url
    assert connector.headers == {"Authorization": f"Token {DG_KEY}"}
    assert DG_KEY not in connector.url and DG_KEY not in repr(adapter)


@pytest.mark.parametrize("language", ["multi", "auto", "fr", "", None])
def test_deepgram_refuses_unpinned_or_unvalidated_language_before_connecting(language):
    adapter, connector = deepgram(FakeSocket([]))
    with pytest.raises(lm.LiveMediaError) as err:
        asyncio.run(collect(adapter, [b"\x00"], language=language))
    assert err.value.kind == "invalid_language" and connector.url is None


def test_deepgram_without_key_or_model_is_not_configured():
    adapter, connector = deepgram(FakeSocket([]), key=None)
    with pytest.raises(lm.LiveMediaError) as err:
        asyncio.run(collect(adapter, [b"\x00"]))
    assert err.value.kind == "not_configured" and connector.url is None
    with pytest.raises(lm.LiveMediaError):
        DeepgramStreamingStt(
            DeepgramProfile(model="", languages=frozenset({"en"})),
            credential=lambda: DG_KEY,
        )


@pytest.mark.parametrize(
    "frames,kw,kind",
    [
        ([b"\x00" * (65 * 1024)], {}, "oversize_input"),
        ([b"\x00" * 1000] * 5, {"max_stream_bytes": 3000}, "oversize_input"),
        (["text"], {}, "invalid_request"),
    ],
)
def test_deepgram_refuses_bad_audio_even_while_waiting(frames, kw, kind):
    adapter, _ = deepgram(FakeSocket([], block=True), **kw)
    with pytest.raises(lm.LiveMediaError) as err:
        asyncio.run(asyncio.wait_for(collect(adapter, frames), 2))
    assert err.value.kind == kind


@pytest.mark.parametrize(
    "message,kind",
    [
        ("{not json", "malformed_response"),
        (b"\x00\x01", "malformed_response"),
        (json.dumps({"type": "Error", "description": "x"}), "provider_error"),
        (result("bad\x07text", final=True), "malformed_response"),
        (result("x" * 4001, final=True), "oversize_output"),
        (json.dumps({"type": "Results", "channel": {}}), "malformed_response"),
        (
            json.dumps(
                {
                    "type": "Results",
                    "is_final": "yes",
                    "channel": {"alternatives": [{"transcript": "a"}]},
                }
            ),
            "malformed_response",
        ),
    ],
)
def test_deepgram_refuses_malformed_messages(message, kind):
    adapter, _ = deepgram(FakeSocket([message]))
    with pytest.raises(lm.LiveMediaError) as err:
        asyncio.run(collect(adapter, [b"\x00\x00"]))
    assert err.value.kind == kind
    assert DG_KEY not in str(err.value)


def test_deepgram_cancel_interrupts_a_blocked_stream():
    async def go():
        cancel = lm.CancelToken()
        adapter, _ = deepgram(FakeSocket([result("hi", final=False)], block=True))
        seen = []

        async def consume():
            async for event in adapter.stream(
                [b"\x00\x00"], language="en", cancel=cancel
            ):
                seen.append(event)
                cancel.cancel()

        with pytest.raises(lm.LiveMediaError) as err:
            await asyncio.wait_for(consume(), 2)
        return err.value.kind, seen

    kind, seen = asyncio.run(go())
    assert kind == "cancelled" and len(seen) == 1


def test_deepgram_cancelled_before_start_never_connects():
    cancel = lm.CancelToken()
    cancel.cancel()
    adapter, connector = deepgram(FakeSocket([]))
    with pytest.raises(lm.LiveMediaError) as err:
        asyncio.run(collect(adapter, [b"\x00"], cancel=cancel))
    assert err.value.kind == "cancelled" and connector.url is None


def test_deepgram_idle_stream_times_out():
    adapter, _ = deepgram(FakeSocket([], block=True), idle_timeout_s=0.05)
    with pytest.raises(lm.LiveMediaError) as err:
        asyncio.run(asyncio.wait_for(collect(adapter, [b"\x00\x00"]), 2))
    assert err.value.kind == "timeout"


# ── OpenAI speech synthesis ──────────────────────────────────────────────────


TTS_PROFILE = OpenAiTtsProfile(
    model="gpt-4o-mini-tts", voice="alloy", languages=frozenset({"en"})
)


def openai(handler, *, profile=TTS_PROFILE, key=OA_KEY, today=dt.date(2026, 11, 13)):
    requests: list[httpx.Request] = []

    def factory():
        def respond(request):
            requests.append(request)
            return handler(request)

        return httpx.AsyncClient(
            transport=httpx.MockTransport(respond), follow_redirects=False
        )

    adapter = OpenAiTts(
        profile, credential=lambda: key, client_factory=factory, today=lambda: today
    )
    return adapter, requests


def wav(request):
    return httpx.Response(
        200, content=b"RIFF" + b"\x00" * 40, headers={"content-type": "audio/wav"}
    )


def test_openai_tts_sends_the_speech_contract_and_returns_audio():
    adapter, requests = openai(wav)
    audio = asyncio.run(adapter.synthesize("It costs 48 €/MWh.", language="en-GB"))
    assert (
        audio.audio.startswith(b"RIFF")
        and audio.mime == "audio/wav"
        and audio.language == "en-GB"
    )
    (request,) = requests
    assert json.loads(request.content) == {
        "model": "gpt-4o-mini-tts",
        "voice": "alloy",
        "input": "It costs 48 €/MWh.",
        "response_format": "wav",
    }
    assert request.headers["authorization"] == f"Bearer {OA_KEY}"
    assert OA_KEY not in repr(adapter)


def test_openai_tts_refuses_a_retired_model_from_its_shutdown_date_without_swapping():
    legacy = OpenAiTtsProfile(model="tts-1", voice="alloy", languages=frozenset({"en"}))
    adapter, requests = openai(wav, profile=legacy, today=dt.date(2027, 1, 5))
    asyncio.run(adapter.synthesize("hello", language="en"))
    assert json.loads(requests[0].content)["model"] == "tts-1"
    adapter, requests = openai(wav, profile=legacy, today=dt.date(2027, 1, 6))
    with pytest.raises(lm.LiveMediaError) as err:
        asyncio.run(adapter.synthesize("hello", language="en"))
    assert "retired" in err.value.message and requests == []


@pytest.mark.parametrize(
    "handler,kind",
    [
        (lambda r: httpx.Response(200, json={"error": "x"}), "malformed_response"),
        (
            lambda r: httpx.Response(
                200, content=b"", headers={"content-type": "audio/wav"}
            ),
            "empty_audio",
        ),
        (lambda r: httpx.Response(401, json={}), "provider_auth"),
        (lambda r: httpx.Response(429, json={}), "provider_limit"),
    ],
)
def test_openai_tts_failures_are_typed(handler, kind):
    adapter, _ = openai(handler)
    with pytest.raises(lm.LiveMediaError) as err:
        asyncio.run(adapter.synthesize("hello", language="en"))
    assert err.value.kind == kind and OA_KEY not in err.value.message


def test_openai_tts_refusals_before_any_call():
    adapter, requests = openai(wav)
    for text, language, kind in (
        ("hi", "de", "invalid_language"),
        (" ", "en", "invalid_request"),
        ("x" * 2001, "en", "oversize_input"),
    ):
        with pytest.raises(lm.LiveMediaError) as err:
            asyncio.run(adapter.synthesize(text, language=language))
        assert err.value.kind == kind
    keyless, _ = openai(wav, key=None)
    with pytest.raises(lm.LiveMediaError) as err:
        asyncio.run(keyless.synthesize("hi", language="en"))
    assert err.value.kind == "not_configured" and requests == []
    with pytest.raises(lm.LiveMediaError):
        OpenAiTts(
            OpenAiTtsProfile(model="", voice="alloy", languages=frozenset({"en"})),
            credential=lambda: OA_KEY,
        )


def test_deepgram_default_connect_over_a_loopback_websocket():
    """The real `websockets` wrapper: header auth, frames, CloseStream, clean close, 401."""
    from http import HTTPStatus

    from websockets.asyncio.server import serve

    async def handler(ws):
        assert ws.request.headers["Authorization"] == f"Token {DG_KEY}"
        received = 0
        async for message in ws:
            if isinstance(message, bytes):
                received += len(message)
            elif json.loads(message) == {"type": "CloseStream"}:
                break
        await ws.send(result(f"heard {received} bytes", final=True))
        await ws.send(json.dumps({"type": "Metadata", "duration": 0.5}))
        await ws.close()

    def reject(connection, request):
        if request.headers.get("Authorization") != f"Token {DG_KEY}":
            return connection.respond(HTTPStatus.UNAUTHORIZED, "no\n")
        return None

    async def go():
        # The test server would log the headers it received; it is not ours.
        quiet = logging.getLogger("lv01.test.server")
        quiet.setLevel(logging.WARNING)
        async with serve(
            handler, "127.0.0.1", 0, process_request=reject, logger=quiet
        ) as server:
            port = server.sockets[0].getsockname()[1]
            endpoint = f"ws://127.0.0.1:{port}/v1/listen"
            good = DeepgramStreamingStt(
                PROFILE, credential=lambda: DG_KEY, endpoint=endpoint
            )
            events = [
                e
                async for e in good.stream([b"\x00" * 100, b"\x00" * 60], language="en")
            ]
            bad = DeepgramStreamingStt(
                PROFILE, credential=lambda: "wrong", endpoint=endpoint
            )
            with pytest.raises(lm.LiveMediaError) as err:
                [e async for e in bad.stream([b"\x00"], language="en")]
            return events, good.usage, err.value

    events, usage, error = asyncio.run(asyncio.wait_for(go(), 10))
    assert [e.text for e in events] == ["heard 160 bytes"]
    assert usage.provider_seconds == 0.5 and usage.audio_bytes == 160
    assert error.kind == "provider_auth" and "wrong" not in error.message


# ── GK B #1482 review: F1–F7 and killing tests for the guard survivors ───────


def test_openai_tts_retired_model_has_its_own_kind():
    legacy = OpenAiTtsProfile(model="tts-1", voice="alloy", languages=frozenset({"en"}))
    adapter, _ = openai(wav, profile=legacy, today=dt.date(2027, 1, 6))
    with pytest.raises(lm.LiveMediaError) as err:
        asyncio.run(adapter.synthesize("hello", language="en"))
    assert (
        err.value.kind == "model_retired"
        and "model_retired" in lm.LIVE_MEDIA_ERROR_KINDS
    )


@pytest.mark.parametrize("cancel_first", [True, False])
def test_deepgram_sync_frame_source_cannot_starve_cancel_or_idle_timeout(cancel_first):
    import itertools

    async def go():
        cancel = lm.CancelToken()
        adapter, _ = deepgram(FakeSocket([], block=True), idle_timeout_s=0.2)
        if cancel_first:
            asyncio.get_running_loop().call_later(0.05, cancel.cancel)
        frames = itertools.repeat(b"")
        with pytest.raises(lm.LiveMediaError) as err:
            await asyncio.wait_for(collect(adapter, frames, cancel=cancel), 2)
        return err.value.kind

    assert asyncio.run(go()) == ("cancelled" if cancel_first else "timeout")


def test_deepgram_sync_nonempty_source_is_cancelled_before_the_stream_cap():
    import itertools

    async def go():
        cancel = lm.CancelToken()
        socket = FakeSocket([], block=True)
        adapter, _ = deepgram(socket)
        asyncio.get_running_loop().call_later(0.05, cancel.cancel)
        with pytest.raises(lm.LiveMediaError) as err:
            await asyncio.wait_for(
                collect(adapter, itertools.repeat(b"\x00" * 1024), cancel=cancel), 2
            )
        return err.value.kind, adapter.usage.audio_bytes

    kind, sent = asyncio.run(go())
    assert kind == "cancelled" and sent < 172 * 1024 * 1024


def test_deepgram_key_reaches_no_log_record_at_debug(caplog):
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="websockets")
    test_deepgram_default_connect_over_a_loopback_websocket()
    assert all(DG_KEY not in r.getMessage() for r in caplog.records)


def test_livekit_create_room_timeout_deletes_the_room_best_effort():
    calls = []

    def factory():
        def respond(request):
            method = request.url.path.rsplit("/", 1)[-1]
            calls.append(method)
            if method == "CreateRoom":
                raise httpx.ReadTimeout("slow", request=request)
            return httpx.Response(200, json={})

        return httpx.Client(transport=httpx.MockTransport(respond))

    transport = LiveKitTransport(
        LiveKitConfig(url=WSS, api_key=API_KEY, api_secret=SECRET),
        client_factory=factory,
    )
    with pytest.raises(lm.LiveMediaError) as err:
        _open(transport)
    assert err.value.kind == "timeout" and calls == ["CreateRoom", "DeleteRoom"]
    refused = LiveKitApi(fail={"CreateRoom": httpx.Response(401, json={})})
    with pytest.raises(lm.LiveMediaError):
        _open(livekit(refused))
    assert refused.methods() == ["CreateRoom"]


def test_livekit_connect_error_is_typed():
    def factory():
        def respond(request):
            raise httpx.ConnectError("refused", request=request)

        return httpx.Client(transport=httpx.MockTransport(respond))

    transport = LiveKitTransport(
        LiveKitConfig(url=WSS, api_key=API_KEY, api_secret=SECRET),
        client_factory=factory,
    )
    with pytest.raises(lm.LiveMediaError) as err:
        transport.close_session(room="pypsa-abc")
    assert err.value.kind == "unavailable"


def test_livekit_refuses_a_different_room_and_deletes_the_requested_one():
    class OtherRoom(LiveKitApi):
        def factory(self):
            def respond(request):
                method = request.url.path.rsplit("/", 1)[-1]
                self.calls.append((method, json.loads(request.content), {}))
                if method == "CreateRoom":
                    return httpx.Response(200, json={"name": "someone-else"})
                return httpx.Response(200, json={})

            return httpx.Client(transport=httpx.MockTransport(respond))

    api = OtherRoom()
    with pytest.raises(lm.LiveMediaError) as err:
        _open(livekit(api))
    assert err.value.kind == "malformed_response"
    assert api.methods() == ["CreateRoom", "DeleteRoom"]
    assert api.calls[1][1] == {"room": "pypsa-abc"}


def test_livekit_close_and_open_check_names_and_types_before_calling():
    api = LiveKitApi()
    with pytest.raises(lm.LiveMediaError) as err:
        livekit(api).close_session(room="bad room")
    assert err.value.kind == "invalid_request"
    with pytest.raises(lm.LiveMediaError) as err:
        _open(livekit(api), bridge_token=123)
    assert err.value.kind == "invalid_request" and api.calls == []


@pytest.mark.parametrize(
    "url",
    ["wss://u@x.livekit.cloud", "wss://:p@x.livekit.cloud", "wss://x.livekit.cloud#f"],
)
def test_livekit_refuses_credentials_or_fragments_in_the_url(url):
    with pytest.raises(lm.LiveMediaError) as err:
        LiveKitTransport(LiveKitConfig(url=url, api_key=API_KEY, api_secret=SECRET))
    assert err.value.kind == "not_configured"


@pytest.mark.parametrize("key,secret", [("", SECRET), (API_KEY, "")])
def test_livekit_needs_key_and_secret(key, secret):
    with pytest.raises(lm.LiveMediaError) as err:
        LiveKitTransport(LiveKitConfig(url=WSS, api_key=key, api_secret=secret))
    assert err.value.kind == "not_configured"


@pytest.mark.parametrize(
    "profile",
    [
        OpenAiTtsProfile(
            model="gpt-4o-mini-tts",
            voice="alloy",
            languages=frozenset({"en"}),
            response_format="flac",
        ),
        OpenAiTtsProfile(model="gpt-4o-mini-tts", voice="alloy", languages=frozenset()),
        OpenAiTtsProfile(
            model="gpt-4o-mini-tts", voice="Alloy Voice", languages=frozenset({"en"})
        ),
    ],
)
def test_openai_tts_profile_is_validated(profile):
    with pytest.raises(lm.LiveMediaError) as err:
        OpenAiTts(profile, credential=lambda: OA_KEY)
    assert err.value.kind == "not_configured"


def test_openai_tts_text_type_and_length_boundary():
    adapter, requests = openai(wav)
    with pytest.raises(lm.LiveMediaError) as err:
        asyncio.run(adapter.synthesize(123, language="en"))
    assert err.value.kind == "invalid_request"
    asyncio.run(adapter.synthesize("x" * 2000, language="en"))
    assert len(requests) == 1


def test_openai_tts_accepts_octet_stream_and_reports_the_profile_format():
    def octet(request):
        return httpx.Response(
            200,
            content=b"\x00\x01" * 10,
            headers={"content-type": "application/octet-stream"},
        )

    adapter, _ = openai(octet)
    assert asyncio.run(adapter.synthesize("hi", language="en")).mime == "audio/wav"


def test_openai_tts_audio_is_bounded():
    def big(request):
        return httpx.Response(
            200, content=b"\x00" * 5000, headers={"content-type": "audio/wav"}
        )

    requests: list = []

    def factory():
        return httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: (requests.append(r), big(r))[1])
        )

    adapter = OpenAiTts(
        TTS_PROFILE,
        credential=lambda: OA_KEY,
        client_factory=factory,
        max_audio_bytes=4096,
    )
    with pytest.raises(lm.LiveMediaError) as err:
        asyncio.run(adapter.synthesize("hi", language="en"))
    assert err.value.kind == "oversize_output"


def test_deepgram_audio_format_params():
    _, connector = deepgram(FakeSocket([]))
    adapter = DeepgramStreamingStt(
        PROFILE, credential=lambda: DG_KEY, connect=connector
    )
    asyncio.run(collect(adapter, [b"\x00\x00"]))
    assert "encoding=linear16" in connector.url
    assert "sample_rate=48000" in connector.url and "channels=1" in connector.url
    wav_profile = DeepgramProfile(
        model="nova-3", languages=frozenset({"en"}), encoding=None, sample_rate=None
    )
    wav_adapter = DeepgramStreamingStt(
        wav_profile, credential=lambda: DG_KEY, connect=connector
    )
    asyncio.run(collect(wav_adapter, [b"\x00\x00"]))
    assert "encoding" not in connector.url and "sample_rate" not in connector.url
    assert "channels" not in connector.url and wav_adapter.usage.audio_seconds_sent == 0


def test_deepgram_message_cap_and_event_cap(monkeypatch):
    adapter, _ = deepgram(FakeSocket(["x" * (64 * 1024 + 1)]))
    with pytest.raises(lm.LiveMediaError) as err:
        asyncio.run(collect(adapter, [b"\x00\x00"]))
    assert err.value.kind == "oversize_output"
    from harness.providers import deepgram_stt

    monkeypatch.setattr(deepgram_stt, "MAX_EVENTS", 2)
    adapter, _ = deepgram(FakeSocket([result(f"w{i}", final=False) for i in range(3)]))
    with pytest.raises(lm.LiveMediaError) as err:
        asyncio.run(collect(adapter, [b"\x00\x00"]))
    assert err.value.kind == "oversize_output"


def test_deepgram_counts_finals_and_skips_empty_frames():
    socket = FakeSocket(
        [
            result("a", final=True),
            result("b", final=False),
            result("c", final=True, start=2),
        ]
    )
    adapter, _ = deepgram(socket)
    asyncio.run(collect(adapter, [b"", b"\x00\x00", b""]))
    assert adapter.usage.finals == 2 and adapter.usage.events == 3
    assert [f for f in socket.sent if isinstance(f, bytes)] == [b"\x00\x00"]


@pytest.mark.parametrize(
    "meta,seconds,ids",
    [
        ({"duration": "2"}, None, []),
        ({"duration": -1}, None, []),
        ({"duration": 0, "request_id": "r" * 65}, 0.0, []),
        ({"duration": 1.5, "request_id": "r" * 64}, 1.5, ["r" * 64]),
    ],
)
def test_deepgram_metadata_is_validated(meta, seconds, ids):
    adapter, _ = deepgram(FakeSocket([json.dumps({"type": "Metadata", **meta})]))
    asyncio.run(collect(adapter, [b"\x00\x00"]))
    assert (
        adapter.usage.provider_seconds == seconds and adapter.usage.request_ids == ids
    )


def test_deepgram_needs_pinned_languages():
    with pytest.raises(lm.LiveMediaError) as err:
        DeepgramStreamingStt(
            DeepgramProfile(model="nova-3", languages=frozenset()),
            credential=lambda: DG_KEY,
        )
    assert err.value.kind == "not_configured"


def test_deepgram_failed_sender_wins_over_a_chatty_socket():
    class Chatty(FakeSocket):
        async def recv(self):
            await asyncio.sleep(0)
            return result("more", final=False)

    adapter, _ = deepgram(Chatty([]))
    with pytest.raises(lm.LiveMediaError) as err:
        asyncio.run(asyncio.wait_for(collect(adapter, ["not bytes"]), 2))
    assert err.value.kind == "invalid_request"


def test_deepgram_cancel_after_the_sender_finished():
    async def go():
        cancel = lm.CancelToken()
        adapter, _ = deepgram(FakeSocket([], block=True))
        asyncio.get_running_loop().call_later(0.1, cancel.cancel)
        with pytest.raises(lm.LiveMediaError) as err:
            await asyncio.wait_for(collect(adapter, [b"\x00\x00"], cancel=cancel), 2)
        return err.value.kind

    assert asyncio.run(go()) == "cancelled"


# default_connect error paths, over a stand-in for websockets' connect.


class _ClosedSocket:
    def __init__(self, recv_exc=None, send_exc=None):
        self.recv_exc, self.send_exc = recv_exc, send_exc

    async def send(self, data):
        if self.send_exc:
            raise self.send_exc

    async def recv(self):
        await asyncio.sleep(0.01)
        raise self.recv_exc

    async def close(self):
        pass


def _with_connect(monkeypatch, behaviour):
    import websockets.asyncio.client as client

    async def fake_connect(url, **kwargs):
        assert kwargs["logger"].name.endswith(".websocket")
        return behaviour()

    monkeypatch.setattr(client, "connect", fake_connect)


def _stream_error(adapter):
    with pytest.raises(lm.LiveMediaError) as err:
        asyncio.run(asyncio.wait_for(collect(adapter, [b"\x00\x00"]), 2))
    return err.value


def _real_adapter():
    return DeepgramStreamingStt(PROFILE, credential=lambda: DG_KEY)


@pytest.mark.parametrize(
    "status,kind",
    [(401, "provider_auth"), (429, "provider_limit"), (500, "provider_error")],
)
def test_default_connect_maps_handshake_status(monkeypatch, status, kind):
    from websockets.datastructures import Headers
    from websockets.exceptions import InvalidStatus
    from websockets.http11 import Response

    def refuse():
        raise InvalidStatus(Response(status, "", Headers()))

    _with_connect(monkeypatch, refuse)
    assert _stream_error(_real_adapter()).kind == kind


def test_default_connect_maps_network_errors_and_abnormal_close(monkeypatch):
    from websockets.exceptions import ConnectionClosedError
    from websockets.frames import Close

    def unreachable():
        raise OSError("refused")

    _with_connect(monkeypatch, unreachable)
    assert _stream_error(_real_adapter()).kind == "unavailable"

    abnormal = ConnectionClosedError(Close(1011, "boom"), None)
    _with_connect(monkeypatch, lambda: _ClosedSocket(recv_exc=abnormal))
    assert _stream_error(_real_adapter()).kind == "provider_error"

    closed = ConnectionClosedError(None, None)
    _with_connect(
        monkeypatch, lambda: _ClosedSocket(recv_exc=abnormal, send_exc=closed)
    )
    assert _stream_error(_real_adapter()).kind == "unavailable"


def test_default_connect_without_websockets_is_not_configured(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "websockets.asyncio.client", None)
    assert _stream_error(_real_adapter()).kind == "not_configured"


# ── GK B #1482: killing tests for the livekit guard survivors ────────────────


def test_livekit_response_at_the_size_limit_is_accepted():
    body = json.dumps({"name": "pypsa-abc", "pad": "x" * (60 * 1024)})
    assert len(body) < MAX_API_RESPONSE_BYTES
    api = LiveKitApi(fail={"CreateRoom": httpx.Response(200, content=body)})
    _open(livekit(api))  # under the cap: no oversize_output


def test_livekit_response_over_the_size_limit_is_refused():
    big = LiveKitApi(
        fail={
            "CreateRoom": httpx.Response(
                200, content=b"x" * (MAX_API_RESPONSE_BYTES + 1)
            )
        }
    )
    with pytest.raises(lm.LiveMediaError) as err:
        _open(livekit(big))
    assert err.value.kind == "oversize_output"


def test_livekit_rejects_an_invalid_agent_name():
    with pytest.raises(lm.LiveMediaError) as err:
        LiveKitTransport(
            LiveKitConfig(
                url=WSS, api_key=API_KEY, api_secret=SECRET, agent_name="bad name"
            )
        )
    assert err.value.kind == "not_configured"


def test_livekit_treats_300_as_an_error():
    api = LiveKitApi(fail={"CreateRoom": httpx.Response(300, json={})})
    with pytest.raises(lm.LiveMediaError) as err:
        _open(livekit(api))
    assert err.value.kind == "provider_error"


@pytest.mark.parametrize(
    "response",
    [httpx.Response(404, json={}), httpx.Response(500, json={"code": "not_found"})],
)
def test_livekit_close_swallows_either_not_found_signal(response):
    api = LiveKitApi(fail={"DeleteRoom": response})
    livekit(api).close_session(room="pypsa-abc")  # no raise


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(429, json={}),
        httpx.Response(500, json={"code": "resource_exhausted"}),
    ],
)
def test_livekit_maps_either_quota_signal_to_provider_limit(response):
    api = LiveKitApi(fail={"CreateRoom": response})
    with pytest.raises(lm.LiveMediaError) as err:
        _open(livekit(api))
    assert err.value.kind == "provider_limit"


def test_livekit_none_room_or_identity_is_a_typed_refusal():
    api = LiveKitApi()
    for kw in ({"room": None}, {"identity": None}):
        with pytest.raises(lm.LiveMediaError) as err:
            _open(livekit(api), **kw)
        assert err.value.kind == "invalid_request"
    with pytest.raises(lm.LiveMediaError) as err:
        livekit(api).close_session(room=None)
    assert err.value.kind == "invalid_request"
    assert api.calls == []


@pytest.mark.parametrize("model,voice", [(None, "alloy"), ("gpt-4o-mini-tts", None)])
def test_openai_tts_unset_model_or_voice_is_a_typed_refusal(model, voice):
    profile = OpenAiTtsProfile(model=model, voice=voice, languages=frozenset({"en"}))
    with pytest.raises(lm.LiveMediaError) as err:
        OpenAiTts(profile, credential=lambda: OA_KEY)
    assert err.value.kind == "not_configured"


# ── GK B #1482: killing tests for the deepgram guard survivors ───────────────

from harness.providers import deepgram_stt as dg  # noqa: E402


def _no_other_tasks():
    return [
        t
        for t in asyncio.all_tasks()
        if t is not asyncio.current_task() and not t.done()
    ]


def test_deepgram_frame_and_stream_limits_are_inclusive():
    frame = b"\x00" * dg.MAX_FRAME_BYTES
    adapter, socket = deepgram(FakeSocket([]), max_stream_bytes=2 * dg.MAX_FRAME_BYTES)
    asyncio.run(collect(adapter, [frame, frame]))
    assert adapter.usage.audio_bytes == 2 * dg.MAX_FRAME_BYTES


def test_deepgram_message_and_text_limits_are_inclusive(monkeypatch):
    text = "x" * dg.MAX_EVENT_CHARS
    message = result(text, final=True)
    monkeypatch.setattr(dg, "MAX_MESSAGE_CHARS", len(message))
    monkeypatch.setattr(dg, "MAX_EVENTS", 1)
    adapter, _ = deepgram(FakeSocket([message]))
    events = asyncio.run(collect(adapter, [b"\x00\x00"]))
    assert events[0].text == text


def test_deepgram_rejects_valid_json_sent_as_binary():
    adapter, _ = deepgram(FakeSocket([result("hi", final=True).encode()]))
    with pytest.raises(lm.LiveMediaError) as err:
        asyncio.run(collect(adapter, [b"\x00\x00"]))
    assert err.value.kind == "malformed_response"


@pytest.mark.parametrize("message", ["[1]", "3", '"Results"'])
def test_deepgram_rejects_non_object_json(message):
    adapter, _ = deepgram(FakeSocket([message]))
    with pytest.raises(lm.LiveMediaError) as err:
        asyncio.run(collect(adapter, [b"\x00\x00"]))
    assert err.value.kind == "malformed_response"


@pytest.mark.parametrize(
    "fields",
    [
        {"start": "1"},
        {"start": True},
        {"start": -1},
        {"channel": {"alternatives": [{"transcript": 5}]}},
    ],
)
def test_deepgram_result_field_types(fields):
    doc = json.loads(result("hi", final=True))
    doc.update(fields)
    adapter, _ = deepgram(FakeSocket([json.dumps(doc)]))
    with pytest.raises(lm.LiveMediaError) as err:
        asyncio.run(collect(adapter, [b"\x00\x00"]))
    assert err.value.kind == "malformed_response"


@pytest.mark.parametrize(
    "encoding,rate",
    [("linear16", None), ("mulaw", 8000)],
)
def test_deepgram_counts_seconds_only_for_linear16_with_a_rate(encoding, rate):
    profile = DeepgramProfile(
        model="nova-3", languages=frozenset({"en"}), encoding=encoding, sample_rate=rate
    )
    connector = Connector(FakeSocket([]))
    adapter = DeepgramStreamingStt(
        profile, credential=lambda: DG_KEY, connect=connector
    )
    asyncio.run(collect(adapter, [b"\x00" * 100]))
    assert adapter.usage.audio_bytes == 100 and adapter.usage.audio_seconds_sent == 0


def test_deepgram_unset_model_is_a_typed_refusal():
    with pytest.raises(lm.LiveMediaError) as err:
        DeepgramStreamingStt(
            DeepgramProfile(model=None, languages=frozenset({"en"})),
            credential=lambda: DG_KEY,
        )
    assert err.value.kind == "not_configured"


def test_deepgram_log_filter_drops_authorization_and_unformattable_records():
    import logging

    keep = dg._NoAuthorization()

    def record(msg, args=()):
        return logging.LogRecord("x", logging.DEBUG, __file__, 1, msg, args, None)

    assert keep.filter(record("> Authorization: Token %s", (DG_KEY,))) is False
    assert keep.filter(record("> Host: api.deepgram.com")) is True
    assert keep.filter(record("%s %s", (1,))) is False


def test_deepgram_sender_honours_cancel_while_the_socket_keeps_talking():
    import itertools

    class Chatty(FakeSocket):
        async def recv(self):
            await asyncio.sleep(0)
            return result("more", final=False)

    async def go():
        cancel = lm.CancelToken()
        adapter, _ = deepgram(Chatty([]))
        asyncio.get_running_loop().call_later(0.05, cancel.cancel)
        with pytest.raises(lm.LiveMediaError) as err:
            await asyncio.wait_for(
                collect(adapter, itertools.repeat(b"\x00\x00"), cancel=cancel), 2
            )
        return err.value.kind

    assert asyncio.run(go()) == "cancelled"


def test_deepgram_an_unset_cancel_token_does_not_cancel():
    class Late(FakeSocket):
        async def recv(self):
            await asyncio.sleep(0.05)  # the sender finishes first
            return self.messages.pop(0) if self.messages else None

    adapter, _ = deepgram(Late([result("hi", final=True)]))
    events = asyncio.run(collect(adapter, [b"\x00\x00"], cancel=lm.CancelToken()))
    assert [e.text for e in events] == ["hi"]


def test_deepgram_leaves_no_task_behind():
    async def go():
        cancel = lm.CancelToken()
        adapter, _ = deepgram(FakeSocket([result("a", final=False)], block=True))
        stream = adapter.stream(_slow_frames(), language="en", cancel=cancel)
        first = await stream.__anext__()
        await stream.aclose()  # the consumer walks away mid-stream
        await asyncio.sleep(0)
        return first.text, _no_other_tasks()

    text, left = asyncio.run(go())
    assert text == "a" and left == []


async def _slow_frames():
    while True:
        yield b"\x00\x00"
        await asyncio.sleep(0.01)
