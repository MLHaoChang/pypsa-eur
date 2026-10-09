"""Long conversations through unchanged handlers, with real state/UI oracles."""
import json
import os

import pandas as pd
import pypsa
import pytest
from jsonschema import Draft202012Validator

from harness.providers.fake import FakeProvider
from tests.live_api_support import MeteredProvider
from tests.openai_comprehensive_cases import TOOL_BY_NAME
from tests.openai_conversation_cases import SCENARIOS
from tests.test_openai_comprehensive_live import comprehensive_run, paid, setup_profile, turn


def network(install_network):
    net = pypsa.Network()
    net.set_snapshots(pd.date_range("2026-01-01", periods=4, freq="h"))
    net.add("Carrier", "AC")
    net.add("Carrier", "gas")
    net.add("Bus", "B1", v_nom=110)
    net.add("Bus", "B2", v_nom=20)
    net.add("Line", "E0", bus0="B1", bus1="B2", x=0.1, r=0.01, s_nom=100)
    net.add("Generator", "G0", bus="B1", carrier="gas", p_nom=100, marginal_cost=10)
    net.add("Load", "L0", bus="B2", p_set=5)
    install_network(net)
    return net


def assert_oracle(case, session, net, frames, *, check_text=True):
    text = "".join(d["delta"] for e, d in frames if e == "token")
    outputs = [d for e, d in frames if e == "tool_result"]
    failures = [d for e, d in frames if e == "tool_error"]
    errors = [d for e, d in frames if e == "error"]
    assert not errors, errors
    assert frames[-1][0] == "turn_done"
    if case["deny"]:
        assert failures and any(e == "tool_pending_confirmation" for e, _ in frames)
        assert not outputs and "DeclinedBus" not in net.buses.index
    elif case["error"]:
        assert failures and not outputs
    else:
        assert not failures, failures
        if case["tool"]:
            assert outputs, "no real tool result"
    if check_text:
        assert text.strip(), "empty final response"
    oracle = case["oracle"]
    if oracle == "two_buses":
        assert set(net.buses.index) == {"B1", "B2"}
    elif oracle == "bus_33":
        assert net.buses.at["ConversationBus", "v_nom"] == 33
    elif oracle == "bus_66":
        assert net.buses.at["ConversationBus", "v_nom"] == 66
    elif oracle == "renamed":
        assert "ConversationBus" not in net.buses.index and "RenamedBus" in net.buses.index
    elif oracle == "load_3":
        assert net.loads.at["ConversationLoad", "p_set"] == 3
        assert net.loads.at["ConversationLoad", "bus"] == "RenamedBus"
    elif oracle == "three_generators":
        assert {"G1", "G2", "G3"} <= set(net.generators.index)
    elif oracle == "generator_total_110":
        assert net.generators.p_nom.sum() == 110
    elif oracle == "bulk_cost":
        assert net.generators.at["G1", "marginal_cost"] == net.generators.at["G2", "marginal_cost"] == 12
    elif oracle == "load_deleted":
        assert "ConversationLoad" not in net.loads.index
    elif oracle == "generators_deleted":
        assert set(net.generators.index) == {"G0"}
    elif oracle == "bus_deleted":
        assert "RenamedBus" not in net.buses.index
    elif oracle in ("carrier", "carrier_list"):
        assert "probe_solar" in net.carriers.index
    elif oracle == "four_snapshots":
        assert len(net.snapshots) == 4
    elif oracle == "weight_two":
        assert net.snapshot_weightings.iloc[0]["objective"] == 2
    elif oracle == "download":
        result = outputs[0]["result"]
        assert isinstance(result, dict) and result["download_url"].startswith("/api/network/")
        assert result["filename"] and result["media_type"]
    elif oracle in ("constant_seven", "timeseries_seven"):
        assert list(net.loads_t.p_set["L0"]) == [7, 7, 7, 7]
    elif oracle in ("uploaded", "timeseries_four"):
        assert list(net.loads_t.p_set["L0"]) == [1, 2, 3, 4]
    elif oracle == "series_deleted":
        assert "L0" not in net.loads_t.p_set.columns
    elif oracle == "skill":
        assert outputs[0]["result"]["name"] == "grill"
    elif oracle == "workflow_started":
        assert session.workflow["id"] == "build-network"
    elif oracle in ("workflow_buses", "workflow_branches"):
        assert session.workflow["step"] == oracle.split("_", 1)[1]
    elif oracle == "workflow_ended":
        assert session.workflow is None
    elif oracle == "choice":
        card = next(d for e, d in frames if e == "choice_request")
        assert [o["label"] for o in card["options"]] == ["B1", "B2"]
        assert card["options"][0]["recommended"] and card["allow_free_text"] is False
    elif oracle.startswith("ui_"):
        event = next(d for e, d in frames if e == "ui_event")
        assert event["kind"] == {"ui_select": "select_component", "ui_panel": "navigate", "ui_snapshot": "set_snapshot"}[oracle]
    elif oracle in ("unknown_skill", "unknown_workflow"):
        assert failures[0]["error_kind"] == oracle
    elif oracle in ("solved", "solved_status"):
        from services.pypsa_service import PyPSAService
        solved = PyPSAService.get_network()
        assert solved.objective is not None and abs(float(solved.objective) - 200) < 1e-5
    if check_text:
        required = {"voltage_110": "110", "voltage_66": "66", "voltage_20": "20", "cost_12": "12",
                    "generator_total_110": "110", "memory_denied": "DeclinedBus", "memory_choice": "B2"}
        if oracle in required:
            assert required[oracle].lower() in text.lower(), text
        if oracle == "memory_choice":
            assert "20" in text
    return {"text": text, "tools": [d for e, d in frames if e == "tool_request"],
            "tool_errors": failures, "result_count": len(outputs)}


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_conversation_fixture_without_api(scenario, install_network, monkeypatch):
    net = network(install_network)
    cases = SCENARIOS[scenario]
    session = setup_profile(monkeypatch, {c["tool"] for c in cases if c["tool"]})
    for i, case in enumerate(cases):
        blocks = [{"type": "tool_use", "id": f"offline_{i}", "name": case["tool"], "input": case["args"]}] if case["tool"] else [{"type": "text", "text": "fixture"}]
        provider = FakeProvider([{"blocks": blocks}, {"blocks": [{"type": "text", "text": "fixture"}]}])
        frames = turn(session, "Fixture operation", provider, decision="deny" if case["deny"] else "approve",
                      ui_context={"ui_mode": "guided"} if case["guided"] else None)
        assert_oracle(case, session, net, frames, check_text=False)


def test_conversation_arguments_match_catalogue():
    for cases in SCENARIOS.values():
        for case in cases:
            if case["tool"]:
                Draft202012Validator(TOOL_BY_NAME[case["tool"]]["input_schema"]).validate(case["args"])


@paid
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_live_long_conversation(scenario, install_network, monkeypatch, comprehensive_run):
    meter, encoder, report, checkpoint = comprehensive_run
    key = "conversation:" + scenario
    if report["cases"].get(key, {}).get("status") == "passed":
        pytest.skip("cached successful conversation; no duplicate API spend")
    net = network(install_network)
    cases = SCENARIOS[scenario]
    session = setup_profile(monkeypatch, {c["tool"] for c in cases if c["tool"]})
    cached = report["cases"].get(key, {})
    result = {"level": "unchanged_handlers_and_network_state", "status": "failed", "turns": list(cached.get("turns", []))}
    for field in ("session_messages", "workflow"):
        if field in cached:
            result[field] = cached[field]
    # Rebuild a failed conversation's disposable state from recorded calls,
    # then restore its exact API history. Successful API turns cost nothing
    # to resume; only the pending turn is sent again.
    for i, outcome in enumerate(result["turns"]):
        prior = cases[i]
        replay = [{"blocks": [{"type": "tool_use", "id": t["tool_use_id"],
                              "name": t["tool_name"], "input": t["args"]}]}
                  for t in outcome["tools"]]
        replay.append({"blocks": [{"type": "text", "text": outcome["text"]}]})
        frames = turn(session, outcome["prompt"], FakeProvider(replay),
                      decision="deny" if prior["deny"] else "approve",
                      ui_context={"ui_mode": "guided"} if prior["guided"] else None)
        assert_oracle(prior, session, net, frames, check_text=False)
    if result["turns"] and cached.get("session_messages"):
        session.messages.clear()
        session.messages.extend(cached["session_messages"])
        session.workflow = cached.get("workflow")
    try:
        for i, case in enumerate(cases):
            if i < len(result["turns"]):
                continue
            tool = case["tool"]
            choices = (None,) if case["prompt"] and tool else (tool,) if tool else ("none",)
            # Natural prompts allow a read before a write. Explicit operations
            # cap after one tool round; each next user message is a new turn.
            choices = choices + ((None, None, "none") if case["prompt"] and tool else ("none",))
            provider = MeteredProvider(meter, encoder, f"{key}:{i}", choices=choices, max_calls=4,
                                      api_key=os.environ["OPENAI_API_KEY"])
            prompt = case["prompt"] or f"Call {tool} once with " + json.dumps(case["args"], ensure_ascii=False) + ". Report the actual outcome briefly."
            frames = turn(session, prompt, provider, decision="deny" if case["deny"] else "approve",
                          ui_context={"ui_mode": "guided"} if case["guided"] else None)
            outcome = assert_oracle(case, session, net, frames)
            outcome.update(index=i, prompt=prompt, calls=provider.calls)
            result["turns"].append(outcome)
            result["session_messages"] = list(session.messages)
            result["workflow"] = session.workflow
            report["cases"][key] = result
            checkpoint()
            print("LIVE_CONVERSATION " + json.dumps({"scenario": scenario, "turn": i + 1,
                  "tokens": meter.charged, "upper_dollars": meter.cost_nanodollars / 1e9}), flush=True)
        result["status"] = "passed"
    except Exception as exc:
        result["reason"] = str(exc)[:1500]
        raise
    finally:
        report["cases"][key] = result
        checkpoint()
