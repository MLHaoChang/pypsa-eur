# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT

"""Hosted speech configuration and the live-probe gate (live-voice 01).

Everything here reads the environment at call time and returns typed results;
no value read from a credential variable is ever returned, logged or put in
evidence. The names of missing variables are reported, never their values.

`transport_from_env` builds the LiveKit transport the server installs with
`live_speech.configure_transport` when continuous speech is enabled.
`probe_gate` decides whether the paid live probe may run: it refuses with
typed reasons until every key is present, the models are pinned, and an owner
approved budget that fits both the plan ceiling and the price estimate is set.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from harness.providers import openai_tts
from harness.providers.deepgram_stt import DeepgramProfile, DeepgramStreamingStt
from harness.providers.livekit_media import LiveKitConfig, LiveKitTransport
from harness.providers.openai_tts import OpenAiTts, OpenAiTtsProfile

LIVEKIT_URL = "LIVEKIT_URL"
LIVEKIT_API_KEY = "LIVEKIT_API_KEY"
LIVEKIT_API_SECRET = "LIVEKIT_API_SECRET"
AGENT_NAME = "PYPSA_GUI_LIVE_AGENT_NAME"
DEEPGRAM_API_KEY = "DEEPGRAM_API_KEY"
OPENAI_KEYS = ("PYPSA_GUI_OPENAI_API_KEY", "OPENAI_API_KEY")
STT_MODEL = "PYPSA_GUI_LIVE_STT_MODEL"
TTS_MODEL = "PYPSA_GUI_LIVE_TTS_MODEL"
TTS_VOICE = "PYPSA_GUI_LIVE_TTS_VOICE"
LANGUAGES = "PYPSA_GUI_LIVE_LANGUAGES"
PROBE_BUDGET = "PYPSA_GUI_LIVE_PROBE_BUDGET_USD"
PROBE_APPROVAL = "PYPSA_GUI_LIVE_PROBE_APPROVAL"

SECRET_ENVS = (LIVEKIT_API_KEY, LIVEKIT_API_SECRET, DEEPGRAM_API_KEY, *OPENAI_KEYS)

# Plan step 3: "Paid probe ceiling $0.10/60 s includes transport and all API
# components" (docs/superpowers/plans/2026-10-10-live-voice-conversation.md).
PROBE_SECONDS = 60
PLAN_CEILING_USD = 0.10

# Prices from docs/superpowers/assessments/2026-10-10-single-user-voice-cost.md,
# each taken at its conservative end. Refresh with the assessment.
PRICES_CHECKED = _dt.date(2026, 10, 10)
PRICES_MAX_AGE_DAYS = 90
PRICES_USD: dict[str, float] = {
    # LiveKit Build: $0/month with 5,000 WebRTC minutes; no automatic paid upgrade.
    "livekit_webrtc_participant_minute": 0.0,
    # Deepgram Nova-3 Multilingual streaming, regular (not promotional) price.
    "deepgram_streaming_minute": 0.0092,
    # OpenAI speech synthesis: output audio $12/M tokens; budgeted per minute of
    # generated audio at OpenAI's own ~$0.015/min estimate for gpt-4o-mini-tts.
    "openai_tts_audio_minute": 0.015,
    # OpenAI speech synthesis text input $0.60/M tokens; one token ~ 4 chars.
    "openai_tts_input_char": 0.60 / 1_000_000 / 4,
}
PROBE_PARTICIPANTS = 2
PROBE_TTS_CHARS = 200

GATE_REFUSALS: frozenset[str] = frozenset(
    {
        "keys_missing",
        "model_not_pinned",
        "language_not_pinned",
        "budget_not_approved",
        "budget_invalid",
        "budget_above_ceiling",
        "estimate_exceeds_budget",
        "prices_stale",
        "tts_model_retired",
    }
)

_APPROVAL_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+$")
_LANG_RE = re.compile(r"^[a-z]{2,3}$")


def _get(env: Mapping[str, str], name: str) -> str:
    return (env.get(name) or "").strip()


def openai_key_present(env: Mapping[str, str]) -> bool:
    return any(_get(env, name) for name in OPENAI_KEYS)


def languages(env: Mapping[str, str]) -> frozenset[str]:
    tags = {t.strip().lower() for t in _get(env, LANGUAGES).split(",") if t.strip()}
    return frozenset(t for t in tags if _LANG_RE.match(t))


def transport_from_env(env: Mapping[str, str] | None = None) -> LiveKitTransport | None:
    """The configured LiveKit transport, or None while any part is missing."""
    env = os.environ if env is None else env
    url, key, secret = (
        _get(env, n) for n in (LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
    )
    if not (url and key and secret):
        return None
    config = LiveKitConfig(
        url=url, key_id=key, api_secret=secret, agent_name=_get(env, AGENT_NAME)
    )
    return LiveKitTransport(config)


def stt_from_env(
    env: Mapping[str, str] | None = None, **kwargs: Any
) -> DeepgramStreamingStt:
    env = os.environ if env is None else env
    profile = DeepgramProfile(
        model=_get(env, STT_MODEL), languages=languages(env), **kwargs
    )
    return DeepgramStreamingStt(
        profile, credential=lambda: _get(env, DEEPGRAM_API_KEY) or None
    )


def tts_from_env(env: Mapping[str, str] | None = None, **kwargs: Any) -> OpenAiTts:
    env = os.environ if env is None else env
    profile = OpenAiTtsProfile(
        model=_get(env, TTS_MODEL),
        voice=_get(env, TTS_VOICE),
        languages=languages(env),
        **kwargs,
    )

    def credential() -> str | None:
        return next((_get(env, n) for n in OPENAI_KEYS if _get(env, n)), None)

    return OpenAiTts(profile, credential=credential)


def estimate_probe_usd(seconds: int = PROBE_SECONDS) -> dict[str, float]:
    """A conservative cost of one probe: every component billed for the whole window."""
    minutes = math.ceil(seconds) / 60
    parts = {
        "transport": PRICES_USD["livekit_webrtc_participant_minute"]
        * PROBE_PARTICIPANTS
        * minutes,
        "stt": PRICES_USD["deepgram_streaming_minute"] * minutes,
        "tts": PRICES_USD["openai_tts_audio_minute"] * minutes
        + PRICES_USD["openai_tts_input_char"] * PROBE_TTS_CHARS,
    }
    parts["total"] = sum(parts.values())
    return {k: round(v, 6) for k, v in parts.items()}


@dataclass(frozen=True)
class Refusal:
    kind: str
    detail: str  # names variables or limits, never values read from a secret

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "detail": self.detail}


@dataclass(frozen=True)
class GateResult:
    refusals: tuple[Refusal, ...] = ()
    budget_usd: float | None = None
    approval: str | None = None
    estimate: dict[str, float] = field(default_factory=dict)
    config_digest: str = ""

    @property
    def ok(self) -> bool:
        return not self.refusals

    @property
    def kinds(self) -> list[str]:
        return [r.kind for r in self.refusals]


def probe_gate(
    env: Mapping[str, str] | None = None, *, today: _dt.date | None = None
) -> GateResult:
    """Every reason the paid probe may not run now; empty when it may."""
    env = os.environ if env is None else env
    today = today or _dt.date.today()
    refusals: list[Refusal] = []

    missing = [
        n
        for n in (LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET, DEEPGRAM_API_KEY)
        if not _get(env, n)
    ]
    if not openai_key_present(env):
        missing.append(" or ".join(OPENAI_KEYS))
    if missing:
        refusals.append(Refusal("keys_missing", ", ".join(missing)))

    unpinned = [n for n in (STT_MODEL, TTS_MODEL, TTS_VOICE) if not _get(env, n)]
    if unpinned:
        refusals.append(Refusal("model_not_pinned", ", ".join(unpinned)))
    if not languages(env):
        refusals.append(Refusal("language_not_pinned", LANGUAGES))

    tts_model = _get(env, TTS_MODEL)
    shutdown = openai_tts.retires_on(tts_model) if tts_model else None
    if shutdown is not None and today >= shutdown:
        refusals.append(
            Refusal("tts_model_retired", f"{TTS_MODEL} retired {shutdown.isoformat()}")
        )

    if (today - PRICES_CHECKED).days > PRICES_MAX_AGE_DAYS:
        refusals.append(
            Refusal(
                "prices_stale",
                f"prices checked {PRICES_CHECKED.isoformat()}; refresh the assessment",
            )
        )

    estimate = estimate_probe_usd()
    approval = _get(env, PROBE_APPROVAL)
    raw_budget = _get(env, PROBE_BUDGET)
    budget: float | None = None
    if not approval or not raw_budget:
        refusals.append(
            Refusal("budget_not_approved", f"{PROBE_BUDGET} and {PROBE_APPROVAL}")
        )
    elif not _APPROVAL_RE.match(approval):
        refusals.append(
            Refusal("budget_invalid", f"{PROBE_APPROVAL} must be an owner decision id")
        )
    else:
        try:
            budget = float(raw_budget)
        except ValueError:
            budget = None
        if budget is None or not math.isfinite(budget) or budget <= 0:
            refusals.append(
                Refusal("budget_invalid", f"{PROBE_BUDGET} must be a positive number")
            )
            budget = None
        elif budget > PLAN_CEILING_USD:
            refusals.append(
                Refusal(
                    "budget_above_ceiling",
                    f"{budget:.4f} > plan ceiling {PLAN_CEILING_USD:.2f} per {PROBE_SECONDS} s",
                )
            )
        elif estimate["total"] > budget:
            refusals.append(
                Refusal(
                    "estimate_exceeds_budget",
                    f"estimate {estimate['total']:.4f} > budget {budget:.4f}",
                )
            )
    return GateResult(
        refusals=tuple(refusals),
        budget_usd=budget,
        approval=approval or None,
        estimate=estimate,
        config_digest=config_digest(env),
    )


CONFIG_ENVS = (
    LIVEKIT_URL,
    AGENT_NAME,
    STT_MODEL,
    TTS_MODEL,
    TTS_VOICE,
    PROBE_BUDGET,
    PROBE_APPROVAL,
)


def config_digest(env: Mapping[str, str]) -> str:
    """SHA-256 of the non-secret configuration `probe_gate` checks.

    Evidence carries it so a result is tied to the exact models, voice,
    languages, budget and prices it ran with. No credential is an input.
    """
    doc = {
        "env": {name: _get(env, name) for name in CONFIG_ENVS},
        "languages": sorted(languages(env)),
        "prices": PRICES_USD,
        "prices_checked": PRICES_CHECKED.isoformat(),
        "probe_seconds": PROBE_SECONDS,
        "plan_ceiling_usd": PLAN_CEILING_USD,
    }
    blob = json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


_SECRET_FIELD_RE = re.compile(
    r"token|secret|key|authorization|password|credential", re.I
)


def sanitize(evidence: Any, env: Mapping[str, str] | None = None) -> Any:
    """Drop secret-named fields and redact any string holding a secret value."""
    env = os.environ if env is None else env
    secrets = [v for v in (_get(env, n) for n in SECRET_ENVS) if len(v) >= 4]

    def clean(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                k: clean(v)
                for k, v in value.items()
                if not (isinstance(k, str) and _SECRET_FIELD_RE.search(k))
            }
        if isinstance(value, (list, tuple)):
            return [clean(v) for v in value]
        if isinstance(value, str):
            for secret in secrets:
                if secret in value:
                    return "[redacted]"
            if value.count(".") == 2 and len(value) > 40 and value.startswith("eyJ"):
                return "[redacted]"  # a JWT, whoever minted it
        return value

    return clean(evidence)
