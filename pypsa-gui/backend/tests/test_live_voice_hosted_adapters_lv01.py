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
import time

import httpx
import pytest

from harness.providers import live_media as lm
from harness.providers.deepgram_stt import DeepgramProfile, DeepgramStreamingStt
from harness.providers.livekit_media import LiveKitConfig, LiveKitTransport
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
        async with serve(handler, "127.0.0.1", 0, process_request=reject) as server:
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
