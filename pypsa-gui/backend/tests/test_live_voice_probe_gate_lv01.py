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


# ── GK B #1483 review: boot safety, evidence identity, grant bound, mutants ──


def test_startup_survives_a_broken_adapter_import(monkeypatch):
    import sys

    import main
    from services import live_speech

    swept = []
    monkeypatch.setattr(live_speech, "start_boot_sweep", lambda: swept.append(1))
    monkeypatch.setattr(live_speech, "_TRANSPORT", None)
    monkeypatch.setenv(live_speech.ENABLE_ENV, "1")
    monkeypatch.setitem(
        sys.modules, "harness.providers.hosted_speech", None
    )  # import fails
    main._speech_boot_sweep()
    assert live_speech._TRANSPORT is None and swept == [1]


def test_evidence_records_the_config_digest_and_code_version():
    gate = hs.probe_gate(env(), today=TODAY)
    assert len(gate.config_digest) == 64
    evidence = asyncio.run(
        probe.run_probe(
            gate,
            language="en",
            transport=FakeTransport(),
            tts=FakeTts(),
            stt=FakeStt("250 and 48"),
        )
    )
    assert evidence["config_digest"] == gate.config_digest
    assert evidence["code_version"] == probe.code_version() != ""


@pytest.mark.parametrize(
    "changes",
    [
        {hs.STT_MODEL: "nova-2"},
        {hs.TTS_MODEL: "tts-1"},
        {hs.TTS_VOICE: "verse"},
        {hs.LANGUAGES: "en"},
        {hs.PROBE_BUDGET: "0.06"},
        {hs.PROBE_APPROVAL: "LV01-PROBE-2"},
        {hs.LIVEKIT_URL: "wss://other.livekit.invalid"},
    ],
)
def test_config_digest_changes_with_what_the_gate_checks(changes):
    assert hs.config_digest(env(**changes)) != hs.config_digest(env())


def test_config_digest_ignores_credentials():
    rotated = env(
        **{hs.DEEPGRAM_API_KEY: "other-dg-key", hs.LIVEKIT_API_SECRET: "other-secret"}
    )
    assert hs.config_digest(rotated) == hs.config_digest(env())
    assert hs.config_digest(env(**{hs.LANGUAGES: "de,en"})) == hs.config_digest(env())


def test_probe_fails_when_the_grant_outlives_900_seconds():
    class LongGrant(FakeTransport):
        def open_session(self, **kw):
            grant = super().open_session(**kw)
            return lm.MediaGrant(
                url=grant.url, token=grant.token, expires_at=time.time() + 901
            )

    heard = "250 and 48"
    assert run(FakeTransport(), FakeTts(), FakeStt(heard))["passed"] is True
    evidence = run(LongGrant(), FakeTts(), FakeStt(heard))
    assert evidence["media"]["grant_ttl_s"] > 900 and evidence["passed"] is False


def test_adapter_builders_read_os_environ_by_default(monkeypatch):
    for name, value in FAKE.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv(hs.STT_MODEL, "nova-env")
    monkeypatch.setenv(hs.TTS_MODEL, "tts-env")
    assert "nova-env" in repr(hs.stt_from_env())
    assert "tts-env" in repr(hs.tts_from_env())


def test_stt_credential_reads_the_key_at_call_time():
    live = env()
    stt = hs.stt_from_env(live)
    assert stt._credential() == FAKE[hs.DEEPGRAM_API_KEY]
    live[hs.DEEPGRAM_API_KEY] = ""
    assert stt._credential() is None


def test_estimate_parts_are_exact():
    e = hs.estimate_probe_usd(60)
    p = hs.PRICES_USD
    assert e["stt"] == pytest.approx(p["deepgram_streaming_minute"])
    assert e["tts"] == pytest.approx(
        p["openai_tts_audio_minute"] + p["openai_tts_input_char"] * hs.PROBE_TTS_CHARS
    )
    assert e["tts"] > p["openai_tts_audio_minute"]


def test_prices_are_fresh_through_day_90_and_stale_from_day_91():
    day = hs.PRICES_CHECKED + dt.timedelta(days=hs.PRICES_MAX_AGE_DAYS)
    assert hs.probe_gate(env(), today=day).ok
    assert hs.probe_gate(env(), today=day + dt.timedelta(days=1)).kinds == [
        "prices_stale"
    ]


def test_sanitize_redacts_secrets_from_four_characters():
    assert hs.sanitize({"n": "x-abcd-x"}, {hs.DEEPGRAM_API_KEY: "abcd"}) == {
        "n": "[redacted]"
    }
    assert hs.sanitize({"n": "x-abc-x"}, {hs.DEEPGRAM_API_KEY: "abc"}) == {
        "n": "x-abc-x"
    }


@pytest.mark.parametrize(
    "value,redacted",
    [
        ("eyJ" + "a" * 20 + "." + "b" * 20 + "." + "c", True),  # 2 dots, 45 chars
        ("eyJ" + "a" * 20 + "." + "b" * 20 + "." + "c" + ".d", False),  # 3 dots
        ("eyJ" + "a" * 16 + "." + "b" * 18 + "." + "c", False),  # exactly 40 chars
        ("xyJ" + "a" * 20 + "." + "b" * 20 + "." + "c", False),  # not a JWT header
    ],
)
def test_sanitize_jwt_rule_needs_every_conjunct(value, redacted):
    assert (hs.sanitize({"n": value}, {}) == {"n": "[redacted]"}) is redacted


def test_transport_from_env_needs_the_url_and_the_key_too():
    assert hs.transport_from_env(env(**{hs.LIVEKIT_URL: None})) is None
    assert hs.transport_from_env(env(**{hs.LIVEKIT_API_KEY: None})) is None


def test_estimate_scales_with_the_window():
    one, two = hs.estimate_probe_usd(60), hs.estimate_probe_usd(120)
    assert two["stt"] == pytest.approx(2 * one["stt"])
    p = hs.PRICES_USD
    audio_two = 2 * p["openai_tts_audio_minute"]
    assert two["tts"] == pytest.approx(
        audio_two + p["openai_tts_input_char"] * hs.PROBE_TTS_CHARS
    )


def test_unset_approval_is_none_not_empty():
    assert hs.probe_gate({}, today=TODAY).approval is None


@pytest.mark.parametrize(
    "budget,kinds",
    [
        ("0", ["budget_invalid"]),
        (str(hs.PLAN_CEILING_USD), []),  # the ceiling itself is allowed
        (
            repr(hs.estimate_probe_usd()["total"]),
            [],
        ),  # a budget equal to the estimate fits
    ],
)
def test_budget_boundaries(budget, kinds):
    assert hs.probe_gate(env(**{hs.PROBE_BUDGET: budget}), today=TODAY).kinds == kinds


def test_sanitize_keeps_non_string_keys():
    assert hs.sanitize({1: "one", "token": "x"}, {}) == {1: "one"}


@pytest.mark.parametrize(
    "name,value",
    [
        ("PRICES_USD", {**hs.PRICES_USD, "deepgram_streaming_minute": 0.02}),
        ("PRICES_CHECKED", dt.date(2026, 11, 1)),
        ("PROBE_SECONDS", 30),
        ("PLAN_CEILING_USD", 0.05),
    ],
)
def test_config_digest_changes_with_prices_and_limits(monkeypatch, name, value):
    before = hs.config_digest(env())
    monkeypatch.setattr(hs, name, value)
    assert hs.config_digest(env()) != before
