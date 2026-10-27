"""Tier B owner journeys: a real model plays the journey (LC-13).

Opt-in only (`PYPSA_GUI_OJ_LIVE=1`, set by tools/run_owner_journeys.py --live):
never part of the default suite, never a merge gate. Every provider request is
reserved against one shared dollar cap before it is sent; the report lists the
spend. Assertions are the Tier A oracle's, on the model's own trace.
"""
import json
import os
from pathlib import Path

import pandas as pd
import pytest

from services import chat_service, chat_tools, llm_config
from services.pypsa_service import PyPSAService
from tests.live_api_support import MeteredProvider, TokenBudget
from tests.oj_grid import ORACLE
from tests.test_oj01_latest_loadflow_journey import MESSAGE as OJ01_MESSAGE, oj_grid  # noqa: F401

LIVE = os.environ.get("PYPSA_GUI_OJ_LIVE") == "1"
MODEL = os.environ.get("PYPSA_GUI_OJ_LIVE_MODEL", "gpt-6.1-sol")
CAP_USD = float(os.environ.get("PYPSA_GUI_OJ_LIVE_CAP_USD", "0") or 0)
TRIALS = int(os.environ.get("PYPSA_GUI_OJ_LIVE_TRIALS", "3"))
SPEND = Path(os.environ.get("PYPSA_GUI_OJ_LIVE_SPEND", "/tmp/oj-live-spend.json"))

pytestmark = pytest.mark.skipif(not LIVE, reason="Tier B: set PYPSA_GUI_OJ_LIVE=1 (tools/run_owner_journeys.py --live)")


class _UpperBoundEncoder:
    """tiktoken is not a declared dependency: one token per two characters
    over-counts, which only makes the spending cap stricter."""

    def encode(self, text):
        return [0] * (len(text) // 2 + 1)


@pytest.fixture(scope="module")
def meter():
    assert CAP_USD > 0, "Tier B needs a positive PYPSA_GUI_OJ_LIVE_CAP_USD"
    meter = TokenBudget(None, request_limit=200, dollar_limit=CAP_USD)
    meter.on_change = lambda: SPEND.write_text(json.dumps(
        {"cap_usd": CAP_USD, "upper_cost_usd": meter.cost_nanodollars / 1e9,
         "requests": [{k: r.get(k) for k in ("case", "model", "usage")} for r in meter.records]}))
    meter.on_change()
    return meter


def _live(meter, case):
    key = os.environ.get("PYPSA_GUI_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    assert key, "Live credential unavailable"
    profile = llm_config.LLMProfile(id="oj-live", label="Owner journey live", preset="openai", wire="openai",
                                    base_url=None, model=MODEL, tools=True, vision=False, auth="bearer",
                                    fallback_model=None, max_output_tokens=1024)
    llm_config.save_profiles([profile], profile.id)
    session = chat_service.ChatSession(model=MODEL)
    session.profile_id, session.bound_wire = profile.id, "openai"
    chat_tools.set_chat_session(session)
    return session, MeteredProvider(meter, _UpperBoundEncoder(), case, max_calls=8, api_key=key)


@pytest.mark.parametrize("trial", range(TRIALS))
def test_oj01_live(oj_grid, meter, trial, monkeypatch):  # noqa: F811
    session, provider = _live(meter, f"OJ-01#{trial}")
    calls, frames = [], []

    def recording(name, real):
        def run(**args):
            calls.append(name)
            return real(**args)
        return run

    for name, real in list(chat_tools.DISPATCHERS.items()):
        monkeypatch.setitem(chat_tools.DISPATCHERS, name, recording(name, real))
    for name, data in chat_service.run_turn(session, OJ01_MESSAGE, provider=provider):
        frames.append((name, data))
        if name == "tool_pending_confirmation":
            session.record_decision(data["confirmation_token"], "approve")
    errors = [d for e, d in frames if e in {"error", "tool_error"}]
    assert not errors, errors
    assert "get_loadflow_summary" in calls, calls
    opened = [d for e, d in frames if e == "ui_event"]
    assert opened and opened[0].get("results_tab") == "loadflow", opened
    text = "".join(d.get("delta", "") for e, d in frames if e == "token")
    top = ORACLE["ranking"][0]
    assert top in text and f"{ORACLE['util_pct'][top]:.0f}" in text, text


OJ21_MESSAGE = "Set L1 to 500 MVA, L2 to 450 MVA and the battery's overnight power cost to 150 €/kW."
OJ23_MESSAGE = "Set L1 and L2 to 500 MVA and L3 to 400."


def _values():
    n = PyPSAService.get_network()
    cost = n.storage_units.at["Battery", "inv_power_overnight"] if "inv_power_overnight" in n.storage_units else None
    return n.lines.at["L1", "s_nom"], n.lines.at["L2", "s_nom"], None if pd.isna(cost) else cost


def _play(session, provider, message):
    frames = []
    for name, data in chat_service.run_turn(session, message, provider=provider):
        frames.append((name, data))
        if name == "tool_pending_confirmation":
            session.record_decision(data["confirmation_token"], "approve")
    return frames


@pytest.mark.parametrize("trial", range(TRIALS))
def test_oj21_live(oj_grid, meter, trial):  # noqa: F811
    # LC-16: one card for three assets of two classes, one approval, native read-back.
    session, provider = _live(meter, f"OJ-21#{trial}")
    before = _values()
    frames = _play(session, provider, OJ21_MESSAGE)
    cards = [d["edit_card"] for e, d in frames if e == "tool_pending_confirmation" and d.get("edit_card")]
    assert len(cards) == 1, [c.get("tool") for c in cards]
    typed = {(r["name"], r["field"]) for r in cards[0]["rows"] if r["role"] == "typed"}
    assert typed == {("L1", "s_nom"), ("L2", "s_nom"), ("Battery", "inv_power_overnight")}, typed
    assert before[:2] == (300, 300)
    assert _values() == (500, 450, 150000)


@pytest.mark.parametrize("trial", range(TRIALS))
def test_oj23_live(oj_grid, meter, trial):  # noqa: F811
    # LC-16: one missing member refuses the batch; the words name it and the lines that exist.
    session, provider = _live(meter, f"OJ-23#{trial}")
    before = _values()
    frames = _play(session, provider, OJ23_MESSAGE)
    assert not [d for e, d in frames if e == "tool_pending_confirmation" and d.get("edit_card")]
    assert _values() == before
    text = "".join(d.get("delta", "") for e, d in frames if e == "token")
    assert "L3" in text and "L1" in text and "L2" in text, text
