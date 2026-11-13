# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT

"""The live-voice 01 hosted-speech probe, closed until the owner opens its gate.

    pixi run python pypsa-gui/backend/smoke/live_voice_probe.py          # gate only
    pixi run python pypsa-gui/backend/smoke/live_voice_probe.py --run    # spend

Without ``--run`` it only prints the gate (no network). With ``--run`` it first
re-checks the gate and refuses with the typed reasons while keys, pinned models
or the owner-approved budget (``PYPSA_GUI_LIVE_PROBE_BUDGET_USD`` with the
decision id in ``PYPSA_GUI_LIVE_PROBE_APPROVAL``) are absent. When the gate is
open it runs one bounded connectivity probe, no project writes:

1. opens and closes one LiveKit room (worker dispatch only when
   ``PYPSA_GUI_LIVE_AGENT_NAME`` is set) and checks the grant lives at most 900 s;
2. synthesizes one short sentence with exact numbers through the pinned TTS;
3. streams that audio through the pinned Deepgram model and checks that every
   number comes back exactly.

The whole run is cut at the probe window. Sanitized evidence (sizes, timings,
models, usage, typed error kinds; never a key, token or signed URL) is written
under ``.scratch/live-voice/evidence/``. A browser WebRTC join is a separate,
manual step and is recorded as not run.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.providers import hosted_speech  # noqa: E402
from harness.providers.live_media import LiveMediaError  # noqa: E402
from harness.providers.livekit_media import MAX_GRANT_TTL_S  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
EVIDENCE_DIR = REPO / ".scratch" / "live-voice" / "evidence"
SENTENCES = {
    "en": "The battery stores 250 megawatt hours and costs 48 euros per megawatt hour.",
    "de": "Die Batterie speichert 250 Megawattstunden und kostet 48 Euro pro Megawattstunde.",
}
FRAME_BYTES = 8192
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")


def code_version() -> str:
    """The checked-out commit, with a marker when the tree has local changes."""
    try:
        head = subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(REPO), "status", "--porcelain", "--untracked-files=no"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return f"{head}+dirty" if dirty else head


def numbers(text: str) -> list[str]:
    return [n.replace(",", ".") for n in _NUMBER_RE.findall(text.replace(" ", ""))]


def _kind(exc: BaseException) -> str:
    if isinstance(exc, LiveMediaError):
        return exc.kind
    if isinstance(exc, asyncio.TimeoutError):
        return "timeout"
    return type(exc).__name__


async def run_probe(
    gate: hosted_speech.GateResult,
    *,
    language: str,
    transport: Any,
    tts: Any,
    stt: Any,
    window_s: float = hosted_speech.PROBE_SECONDS,
) -> dict[str, Any]:
    """The probe itself; callers pass adapters so it runs against fakes too."""
    sentence = SENTENCES[language]
    evidence: dict[str, Any] = {
        "date": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
        "approval": gate.approval,
        "config_digest": gate.config_digest,
        "code_version": code_version(),
        "budget_usd": gate.budget_usd,
        "estimate_usd": gate.estimate,
        "language": language,
        "stt_adapter": repr(stt),
        "tts_adapter": repr(tts),
        "webrtc_browser_join": "not_run",
        "project_writes": 0,
    }
    started = time.monotonic()

    async def media() -> None:
        room = f"pypsa-probe-{uuid.uuid4().hex[:12]}"
        step: dict[str, Any] = {"room_opened": False, "room_closed": False}
        evidence["media"] = step
        t0 = time.monotonic()
        try:
            grant = await asyncio.to_thread(
                transport.open_session,
                room=room,
                identity="probe-owner",
                ttl_s=120,
                bridge_token=uuid.uuid4().hex,  # throwaway: the probe has no thread
                language=language,
            )
            step["room_opened"] = True
            step["grant_ttl_s"] = round(grant.expires_at - time.time())
            step["open_ms"] = round((time.monotonic() - t0) * 1000)
        finally:
            try:
                await asyncio.to_thread(transport.close_session, room=room)
                step["room_closed"] = True
            except Exception as exc:  # noqa: BLE001 — recorded as a typed kind
                step["close_error"] = _kind(exc)

    async def speech() -> None:
        step: dict[str, Any] = {"chars": len(sentence)}
        evidence["tts"] = step
        t0 = time.monotonic()
        audio = await tts.synthesize(sentence, language=language)
        step.update(
            audio_bytes=len(audio.audio),
            mime=audio.mime,
            ms=round((time.monotonic() - t0) * 1000),
        )
        heard: list[str] = []
        t1 = time.monotonic()
        frames = [
            audio.audio[i : i + FRAME_BYTES]
            for i in range(0, len(audio.audio), FRAME_BYTES)
        ]
        async for event in stt.stream(frames, language=language):
            if event.final:
                heard.append(event.text)
        usage = stt.usage
        transcript = " ".join(heard)
        evidence["stt"] = {
            "ms": round((time.monotonic() - t1) * 1000),
            "audio_bytes": usage.audio_bytes,
            "provider_seconds": usage.provider_seconds,
            "finals": usage.finals,
            "transcript": transcript,
            "numbers_expected": numbers(sentence),
            "numbers_heard": numbers(transcript),
            "numbers_exact": numbers(transcript) == numbers(sentence),
        }

    errors: dict[str, str] = {}
    for name, step in (("media", media), ("speech", speech)):
        remaining = window_s - (time.monotonic() - started)
        try:
            await asyncio.wait_for(step(), max(remaining, 0.001))
        except Exception as exc:  # noqa: BLE001 — every failure is one typed kind
            errors[name] = _kind(exc)
    evidence["errors"] = errors
    evidence["elapsed_s"] = round(time.monotonic() - started, 2)
    evidence["passed"] = (
        not errors
        and evidence.get("media", {}).get("room_closed") is True
        and 0 < evidence.get("media", {}).get("grant_ttl_s", 0) <= MAX_GRANT_TTL_S
        and evidence.get("stt", {}).get("numbers_exact") is True
    )
    return evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--run", action="store_true", help="spend: run the probe if the gate is open"
    )
    parser.add_argument(
        "--out", type=Path, default=None, help="evidence file (default under .scratch)"
    )
    args = parser.parse_args(argv)

    gate = hosted_speech.probe_gate()
    report = {
        "gate": "open" if gate.ok else "closed",
        "refusals": [r.as_dict() for r in gate.refusals],
        "estimate_usd": gate.estimate,
        "plan_ceiling_usd": hosted_speech.PLAN_CEILING_USD,
    }
    print(json.dumps(hosted_speech.sanitize(report), indent=2))
    if not gate.ok:
        return 2
    if not args.run:
        return 0

    pinned = sorted(hosted_speech.languages(os.environ))
    language = next((tag for tag in pinned if tag in SENTENCES), None)
    if language is None:
        print(
            json.dumps(
                {"refusal": "language_not_probed", "supported": sorted(SENTENCES)}
            )
        )
        return 2
    transport = hosted_speech.transport_from_env()
    tts = hosted_speech.tts_from_env(response_format="wav")
    stt = hosted_speech.stt_from_env(encoding=None, sample_rate=None)
    evidence = hosted_speech.sanitize(
        asyncio.run(
            run_probe(gate, language=language, transport=transport, tts=tts, stt=stt)
        )
    )
    out = (
        args.out
        or EVIDENCE_DIR / f"probe-{_dt.datetime.now(_dt.UTC):%Y%m%dT%H%M%SZ}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps({"passed": evidence["passed"], "evidence": str(out)}))
    return 0 if evidence["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
