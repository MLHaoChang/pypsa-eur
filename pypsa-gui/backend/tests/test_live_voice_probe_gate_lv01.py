"""Live-voice 01 — the closed live-probe gate, evidence sanitizing and wiring.

LV01-PROBE-LATER-1: the probe is built but never runs here. No network, no
credentials: every value below is a fake, and the real transports fail.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import time

import httpx
import pytest

from harness.providers import hosted_speech as hs
from harness.providers import live_media as lm
from harness.providers.livekit_media import LiveKitTransport
from smoke import live_voice_probe as probe

TODAY = dt.date(2026, 11, 13)
FAKE = {
    hs.LIVEKIT_URL: "wss://probe.livekit.invalid",
    hs.LIVEKIT_API_KEY: "APIfakekey01",
    hs.LIVEKIT_API_SECRET: "fake-livekit-secret-0001",
    hs.DEEPGRAM_API_KEY: "fake-deepgram-key-0001",
    "OPENAI_API_KEY": "sk-fake-openai-0001",
    hs.STT_MODEL: "nova-3",
    hs.TTS_MODEL: "gpt-4o-mini-tts",
    hs.TTS_VOICE: "alloy",
    hs.LANGUAGES: "en,de",
    hs.PROBE_BUDGET: "0.05",
    hs.PROBE_APPROVAL: "LV01-PROBE-1",
}
SECRETS = [
    FAKE[n]
    for n in (
        hs.LIVEKIT_API_KEY,
        hs.LIVEKIT_API_SECRET,
        hs.DEEPGRAM_API_KEY,
        "OPENAI_API_KEY",
    )
]


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    def refuse(self, request):
        raise AssertionError(f"real network attempted: {request.url}")

    async def refuse_async(self, request):
        raise AssertionError(f"real network attempted: {request.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse_async)


def env(**changes):
    out = dict(FAKE)
    for name, value in changes.items():
        if value is None:
            out.pop(name, None)
        else:
            out[name] = value
    return out


def test_gate_is_closed_with_typed_reasons_on_an_empty_environment():
    gate = hs.probe_gate({}, today=TODAY)
    assert not gate.ok
    assert gate.kinds == [
        "keys_missing",
        "model_not_pinned",
        "language_not_pinned",
        "budget_not_approved",
    ]
    keys = gate.refusals[0].detail
    for name in (
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
        "DEEPGRAM_API_KEY",
        "OPENAI_API_KEY",
    ):
        assert name in keys
    assert set(gate.kinds) <= hs.GATE_REFUSALS


def test_gate_opens_only_with_keys_pinned_models_and_an_approved_fitting_budget():
    gate = hs.probe_gate(env(), today=TODAY)
    assert gate.ok, gate.refusals
    assert gate.budget_usd == 0.05 and gate.approval == "LV01-PROBE-1"
    assert gate.estimate["total"] <= gate.budget_usd <= hs.PLAN_CEILING_USD


@pytest.mark.parametrize(
    "changes,kind",
    [
        ({hs.DEEPGRAM_API_KEY: None}, "keys_missing"),
        ({"OPENAI_API_KEY": None}, "keys_missing"),
        ({hs.LIVEKIT_API_SECRET: "  "}, "keys_missing"),
        ({hs.TTS_MODEL: None}, "model_not_pinned"),
        ({hs.STT_MODEL: ""}, "model_not_pinned"),
        ({hs.LANGUAGES: "multi-lingual, *"}, "language_not_pinned"),
        ({hs.PROBE_BUDGET: None}, "budget_not_approved"),
        ({hs.PROBE_APPROVAL: None}, "budget_not_approved"),
        ({hs.PROBE_APPROVAL: "yes"}, "budget_invalid"),
        ({hs.PROBE_BUDGET: "abc"}, "budget_invalid"),
        ({hs.PROBE_BUDGET: "nan"}, "budget_invalid"),
        ({hs.PROBE_BUDGET: "-1"}, "budget_invalid"),
        ({hs.PROBE_BUDGET: "0.5"}, "budget_above_ceiling"),
        ({hs.PROBE_BUDGET: "0.01"}, "estimate_exceeds_budget"),
    ],
)
def test_gate_refuses_each_missing_condition(changes, kind):
    gate = hs.probe_gate(env(**changes), today=TODAY)
    assert gate.kinds == [kind]


def test_gate_accepts_the_alternate_openai_key_name():
    gate = hs.probe_gate(
        env(OPENAI_API_KEY=None, PYPSA_GUI_OPENAI_API_KEY="sk-alt-0001"), today=TODAY
    )
    assert gate.ok


def test_gate_refuses_a_retired_tts_model_and_stale_prices():
    retired = hs.probe_gate(env(**{hs.TTS_MODEL: "tts-1"}), today=dt.date(2027, 1, 6))
    assert "tts_model_retired" in retired.kinds
    assert hs.probe_gate(env(**{hs.TTS_MODEL: "tts-1"}), today=dt.date(2027, 1, 5)).ok
    stale = hs.probe_gate(env(), today=hs.PRICES_CHECKED + dt.timedelta(days=91))
    assert stale.kinds == ["prices_stale"]


def test_gate_refusals_never_carry_secret_values():
    for changes in ({}, {hs.PROBE_BUDGET: "0.5"}, {hs.DEEPGRAM_API_KEY: None}):
        text = json.dumps(
            [r.as_dict() for r in hs.probe_gate(env(**changes), today=TODAY).refusals]
        )
        assert not any(secret in text for secret in SECRETS)


def test_estimate_fits_the_plan_ceiling_with_every_component():
    estimate = hs.estimate_probe_usd()
    assert set(estimate) == {"transport", "stt", "tts", "total"}
    assert estimate["stt"] > 0 and estimate["tts"] > 0
    assert estimate["total"] == pytest.approx(
        estimate["transport"] + estimate["stt"] + estimate["tts"]
    )
    assert estimate["total"] <= hs.PLAN_CEILING_USD
    assert hs.estimate_probe_usd(120)["total"] > estimate["total"]


def test_transport_from_env_needs_all_three_livekit_values():
    assert hs.transport_from_env({}) is None
    assert hs.transport_from_env(env(**{hs.LIVEKIT_API_SECRET: None})) is None
    transport = hs.transport_from_env(env())
    assert isinstance(transport, LiveKitTransport) and isinstance(
        transport, lm.MediaTransport
    )
    assert not any(secret in repr(transport) for secret in SECRETS)


def test_sanitize_drops_secret_fields_and_redacts_secret_values_and_jwts():
    jwt = "eyJhbGciOiJIUzI1NiJ9." + "a" * 40 + "." + "b" * 43
    evidence = {
        "media": {"token": "abc", "bridge_token": "x", "url": "wss://ok"},
        "note": f"auth was {FAKE[hs.DEEPGRAM_API_KEY]}",
        "list": [FAKE[hs.LIVEKIT_API_SECRET], jwt, "fine"],
        "Authorization": "Bearer x",
        "transcript": "250 MWh",
    }
    clean = hs.sanitize(evidence, env())
    assert clean == {
        "media": {"url": "wss://ok"},
        "note": "[redacted]",
        "list": ["[redacted]", "[redacted]", "fine"],
        "transcript": "250 MWh",
    }


def test_probe_script_without_keys_refuses_typed_and_spends_nothing(
    monkeypatch, capsys
):
    for name in (*FAKE, "PYPSA_GUI_OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert probe.main(["--run"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["gate"] == "closed"
    assert "keys_missing" in [r["kind"] for r in report["refusals"]]
    assert "budget_not_approved" in [r["kind"] for r in report["refusals"]]


def test_probe_script_with_an_open_gate_but_no_run_flag_does_not_spend(
    monkeypatch, capsys
):
    monkeypatch.setattr(
        hs,
        "probe_gate",
        lambda *a, **k: hs.GateResult(estimate=hs.estimate_probe_usd()),
    )
    monkeypatch.setattr(
        hs, "transport_from_env", lambda *a, **k: pytest.fail("built a transport")
    )
    assert probe.main([]) == 0
    assert json.loads(capsys.readouterr().out)["gate"] == "open"


# ── the probe pipeline over fakes ────────────────────────────────────────────


class FakeTransport:
    def __init__(self, fail_open=False):
        self.fail_open = fail_open
        self.opened: list[str] = []
        self.closed: list[str] = []

    def open_session(self, *, room, identity, ttl_s, bridge_token, language):
        self.opened.append(room)
        if self.fail_open:
            raise lm.LiveMediaError("provider_auth", "rejected")
        return lm.MediaGrant(
            url="wss://x", token="eyJ.secret.token", expires_at=time.time() + ttl_s
        )

    def close_session(self, *, room):
        self.closed.append(room)


class FakeTts:
    def __init__(self, fail=False):
        self.fail = fail

    async def synthesize(self, text, *, language, cancel=None):
        if self.fail:
            raise lm.LiveMediaError("provider_limit", "quota")
        return lm.SpeechAudio(
            audio=b"RIFF" + b"\x00" * 20_000, mime="audio/wav", language=language
        )


class FakeStt:
    def __init__(self, heard):
        self.heard = heard
        self.usage = type(
            "U", (), {"audio_bytes": 0, "provider_seconds": 4.2, "finals": 0}
        )()

    async def stream(self, frames, *, language, cancel=None):
        for frame in frames:
            self.usage.audio_bytes += len(frame)
        yield lm.TranscriptEvent("seg-0", 1, "the battery", False)
        self.usage.finals = 1
        yield lm.TranscriptEvent("seg-0", 2, self.heard, True)


GATE = hs.GateResult(
    budget_usd=0.05, approval="LV01-PROBE-1", estimate=hs.estimate_probe_usd()
)


def run(transport, tts, stt, **kw):
    return asyncio.run(
        probe.run_probe(
            GATE, language="en", transport=transport, tts=tts, stt=stt, **kw
        )
    )


def test_probe_passes_when_room_closes_and_every_number_comes_back():
    transport = FakeTransport()
    heard = (
        "The battery stores 250 megawatt hours and costs 48 euros per megawatt hour."
    )
    evidence = run(transport, FakeTts(), FakeStt(heard))
    assert evidence["passed"] is True and evidence["errors"] == {}
    assert transport.opened == transport.closed and len(transport.opened) == 1
    assert (
        evidence["stt"]["numbers_expected"]
        == ["250", "48"]
        == evidence["stt"]["numbers_heard"]
    )
    assert evidence["stt"]["audio_bytes"] == 20_004
    assert 0 < evidence["media"]["grant_ttl_s"] <= 120
    assert (
        evidence["webrtc_browser_join"] == "not_run" and evidence["project_writes"] == 0
    )
    clean = json.dumps(hs.sanitize(evidence, env()))
    assert "eyJ.secret.token" not in clean


def test_probe_fails_on_a_changed_number():
    evidence = run(
        FakeTransport(), FakeTts(), FakeStt("stores 25 megawatt hours, 48 euros")
    )
    assert evidence["passed"] is False and evidence["stt"]["numbers_exact"] is False


def test_probe_closes_the_room_and_records_typed_kinds_when_steps_fail():
    transport = FakeTransport(fail_open=True)
    evidence = run(transport, FakeTts(fail=True), FakeStt(""))
    assert evidence["errors"] == {"media": "provider_auth", "speech": "provider_limit"}
    assert transport.closed == transport.opened and evidence["passed"] is False


def test_probe_is_cut_at_its_window():
    class SlowTts(FakeTts):
        async def synthesize(self, text, *, language, cancel=None):
            await asyncio.sleep(5)

    started = time.monotonic()
    evidence = run(FakeTransport(), SlowTts(), FakeStt(""), window_s=0.3)
    assert time.monotonic() - started < 2
    assert evidence["errors"] == {"speech": "timeout"}


# ── server wiring ────────────────────────────────────────────────────────────


def test_startup_installs_the_livekit_transport_only_when_speech_is_enabled(
    monkeypatch,
):
    import main
    from services import live_speech

    monkeypatch.setattr(live_speech, "start_boot_sweep", lambda: None)
    monkeypatch.setattr(live_speech, "_TRANSPORT", None)
    for name, value in FAKE.items():
        monkeypatch.setenv(name, value)

    monkeypatch.delenv(live_speech.ENABLE_ENV, raising=False)
    main._speech_boot_sweep()
    assert live_speech._TRANSPORT is None

    monkeypatch.setenv(live_speech.ENABLE_ENV, "1")
    main._speech_boot_sweep()
    assert isinstance(live_speech._TRANSPORT, LiveKitTransport)

    monkeypatch.setenv(hs.LIVEKIT_URL, "https://not-wss.invalid")
    monkeypatch.setattr(live_speech, "_TRANSPORT", None)
    main._speech_boot_sweep()
    assert live_speech._TRANSPORT is None  # bad config: speech stays off, the app boots
