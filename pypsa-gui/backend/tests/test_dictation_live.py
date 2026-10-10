# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT

"""
Opt-in, single-call transcription smoke through the authenticated route.

PYPSA_GUI_LIVE_DICTATION=1 PYPSA_GUI_DICTATION_AUDIO=/path/to/fixture.wav
PYPSA_GUI_DICTATION_LEDGER=/path/to/existing-ledger.json pytest tests/test_dictation_live.py

Use the documented synthetic English fixture; never personal audio in this smoke.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import wave

import pytest

from harness.providers import transcription


@pytest.mark.skipif(
    os.environ.get("PYPSA_GUI_LIVE_DICTATION") != "1",
    reason="Paid dictation smoke is opt-in",
)
def test_live_dictation_short_audio(client):
    if not transcription.credential():
        pytest.skip("Transcription credentials unavailable")
    audio_path = Path(os.environ["PYPSA_GUI_DICTATION_AUDIO"])
    ledger_path = Path(os.environ["PYPSA_GUI_DICTATION_LEDGER"])
    audio = audio_path.read_bytes()
    with wave.open(str(audio_path), "rb") as recording:
        seconds = recording.getnframes() / recording.getframerate()
    assert 0 < seconds <= 10, "Smoke fixture must be <=10 seconds"
    from routers import dictation as route
    from services import dictation as service

    checkpoint = hashlib.sha256(
        audio
        + b"".join(
            Path(module.__file__).read_bytes()
            for module in (transcription, route, service)
        )
        + Path(__file__).read_bytes()
    ).hexdigest()
    ledger = json.loads(ledger_path.read_text())
    if ledger.get("dictation_completed", {}).get(checkpoint):
        pytest.skip("Matching live dictation smoke already completed; no paid repeat")
    # Pricing confirmed 2026-10-10 at developers.openai.com/api/docs/pricing:
    # gpt-4o-mini-transcribe $0.003/minute estimated. Reserve $0.01, retaining
    # the conservative reservation since this route intentionally returns no
    # accounting metadata. Preserve the preceding chat ledger and dollar cap.
    reserve_cost = 10_000_000
    reserve_tokens = 1024
    cost = ledger["cost_nanodollars"]
    limit = ledger.get("token_limit")
    assert cost + reserve_cost <= int(ledger["dollar_limit"] * 1e9)
    assert limit is None or ledger["charged_tokens"] + reserve_tokens <= limit
    record = {
        "case": "dictation:short-english",
        "model": transcription.MODEL,
        "reserved": reserve_tokens,
        "charged": reserve_tokens,
        "reserved_cost_nanodollars": reserve_cost,
        "charged_cost_nanodollars": reserve_cost,
        "usage_reported": False,
        "audio_seconds": seconds,
    }
    ledger["requests"].append(record)
    ledger["charged_tokens"] += reserve_tokens
    ledger["cost_nanodollars"] += reserve_cost
    ledger["upper_cost_dollars"] = ledger["cost_nanodollars"] / 1e9
    ledger_path.write_text(json.dumps(ledger, indent=2))
    response = client.post(
        "/api/dictation/transcribe",
        files={"file": ("fixture.wav", audio, "audio/wav")},
        data={"language": "auto", "glossary": "PyPSA, GridSpine, MW, MWh"},
    )
    record["http_status"] = response.status_code
    if response.status_code == 200:
        text = response.json()["text"]
        record["fixture_transcript"] = text
        ledger_path.write_text(json.dumps(ledger, indent=2))
        normalized = re.sub(r"\s+", " ", text.lower())
        assert "7.5" in normalized or "seven point five" in normalized
        assert "not" in normalized
        assert "megawatt" in normalized or "mw" in normalized
        ledger.setdefault("dictation_completed", {})[checkpoint] = True
    else:
        record["error_code"] = response.json().get("detail", {}).get("code")
    ledger_path.write_text(json.dumps(ledger, indent=2))
    if response.status_code in (502, 503) and record.get("error_code") in (
        "provider_auth",
        "unavailable",
        "not_configured",
    ):
        pytest.skip(
            f"Live audio endpoint blocked: {record['error_code']} (HTTP {response.status_code})"
        )
    assert response.status_code == 200, response.text
