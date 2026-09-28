"""
The smoke's stub model (guided-mode spec §6.6, review B7): branch 2 turns the
§5.7 "Let the assistant do this" text into the tool call it names, so the
browser smoke sees a real confirmation card. Fast and in-process: the stub's
own HTTP server on a free port, spoken to over the OpenAI wire.

The delegate text below is the frontend's `actionText` (pages/hubDesign/
delegate.ts) built for a real finding shape — the two change together
(spec §9: "the delegate text and the regex are changed together").
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

STUB = pathlib.Path(__file__).resolve().parents[1] / "smoke" / "stub_openai_endpoint.py"


@pytest.fixture(scope="module")
def stub():
    spec = importlib.util.spec_from_file_location("stub_openai_endpoint", STUB)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield mod, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def _chat(base: str, messages: list[dict]) -> tuple[list[dict], str]:
    """POST one chat-completions request; return (tool_calls, text)."""
    body = json.dumps({"model": "stub-model", "stream": True,
                       "messages": messages}).encode()
    req = urllib.request.Request(f"{base}/v1/chat/completions", data=body,
                                 headers={"content-type": "application/json"})
    calls: dict[int, dict] = {}
    text = ""
    with urllib.request.urlopen(req, timeout=10) as r:
        for raw in r.read().decode().split("\n\n"):
            raw = raw.strip()
            if not raw.startswith("data: ") or raw == "data: [DONE]":
                continue
            chunk = json.loads(raw[len("data: "):])
            for ch in chunk.get("choices", []):
                d = ch.get("delta", {})
                text += d.get("content") or ""
                for tc in d.get("tool_calls") or []:
                    c = calls.setdefault(tc["index"], {"id": tc.get("id"), "name": "",
                                                       "arguments": ""})
                    fn = tc.get("function", {})
                    c["name"] += fn.get("name") or ""
                    c["arguments"] += fn.get("arguments") or ""
    return [calls[i] for i in sorted(calls)], text


def _delegate_text(title: str, tool: str, args: dict) -> str:
    # pages/hubDesign/delegate.ts actionTexts(), verbatim (JSON.stringify has
    # no spaces: separators=(",", ":")).
    return (f'Apply this recommendation from the study review: "{title}". '
            f"Run the tool {tool} with exactly these arguments: "
            f'{json.dumps(args, separators=(",", ":"))}. Say in one sentence what '
            "will change, then proceed to the confirmation.")


GUIDED_BLOCK = (
    "<untrusted_data>\nThe user is currently looking at:\n  open panel: hubDesign\n"
    "  mode: guided\n  guided step: improve\n</untrusted_data>\n\n"
    "Guided mode is on. Rules for this turn: answer in plain language.\n\n")


@pytest.mark.parametrize("tool,args", [
    ("run_eh_study", {"archetype": "weak_flexible",
                      "stages": ["apply_pack", "dtc_stress", "dtc_planning"],
                      "pack_overrides": {"import_p_nom_mw": 40, "target_lole_h": 3}}),
    ("update_solver_config", {"partial": {"voll": 5000}}),
    ("update_component", {"component_class": "Bus", "name": "grid",
                          "attrs": {"eh_poc": True}}),
])
def test_branch_2_parses_the_delegate_text_into_the_tool_call(stub, tool, args):
    _mod, base = stub
    text = GUIDED_BLOCK + _delegate_text("Not certified: 12.4 h/yr exceeds 3 h/yr", tool, args)
    calls, said = _chat(base, [{"role": "system", "content": "sys"},
                               {"role": "user", "content": text}])
    assert said == ""
    assert len(calls) == 1
    assert calls[0]["id"] == "call_stub_2"
    assert calls[0]["name"] == tool
    # nested objects parse whole (a bare non-greedy \{.*?\} would cut
    # {"partial":{"voll":5000}} at the first closing brace)
    assert json.loads(calls[0]["arguments"]) == args


def test_after_the_tool_result_it_closes_with_one_sentence(stub):
    _mod, base = stub
    text = _delegate_text("t", "update_solver_config", {"partial": {"voll": 5000}})
    calls, said = _chat(base, [
        {"role": "user", "content": text},
        {"role": "assistant", "content": None, "tool_calls": [{
            "id": "call_stub_2", "type": "function",
            "function": {"name": "update_solver_config",
                         "arguments": '{"partial":{"voll":5000}}'}}]},
        {"role": "tool", "tool_call_id": "call_stub_2", "content": '{"ok": true}'},
    ])
    assert calls == []
    assert said == "Done — update_solver_config applied."


@pytest.mark.parametrize("result", [
    '{"error": "user declined the action"}',
    # what chat_service sends on a denied card (error_kind confirmation_denied)
    '{"error_kind": "confirmation_denied", "message": "user denied confirmation for \'x\'"}',
])
def test_a_declined_card_is_not_reported_as_applied(stub, result):
    _mod, base = stub
    text = _delegate_text("t", "update_solver_config", {"partial": {"voll": 5000}})
    calls, said = _chat(base, [
        {"role": "user", "content": text},
        {"role": "assistant", "content": None, "tool_calls": [{
            "id": "call_stub_2", "type": "function",
            "function": {"name": "update_solver_config", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call_stub_2", "content": result},
    ])
    assert calls == []
    assert "not applied" in said and "update_solver_config" in said


def test_a_tool_result_from_an_earlier_turn_does_not_stop_a_new_request(stub):
    # The session replays history: a new delegate request after an answered
    # one must still be scripted.
    _mod, base = stub
    first = _delegate_text("a", "update_solver_config", {"partial": {"voll": 1}})
    second = _delegate_text("b", "update_component",
                            {"component_class": "Bus", "name": "x", "attrs": {"eh_poc": True}})
    calls, _ = _chat(base, [
        {"role": "user", "content": first},
        {"role": "assistant", "content": None, "tool_calls": [{
            "id": "call_stub_2", "type": "function",
            "function": {"name": "update_solver_config", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call_stub_2", "content": "{}"},
        {"role": "assistant", "content": "Done."},
        {"role": "user", "content": second},
    ])
    assert [c["name"] for c in calls] == ["update_component"]


@pytest.mark.parametrize("text", [
    "hello",
    "Explain in plain language: \"x\". Evidence: {\"a\": 1}. What are my options?",
    "Run the tool with exactly these arguments: {}",          # no tool name
    "Run the tool update_component with exactly these arguments: {not json}",
])
def test_text_without_the_marker_falls_through_to_the_default_reply(stub, text):
    _mod, base = stub
    calls, said = _chat(base, [{"role": "user", "content": text}])
    assert calls == []
    assert said == "Saved."


def test_branch_1_is_unchanged(stub):
    _mod, base = stub
    calls, _ = _chat(base, [{"role": "user",
                             "content": "Save the current network as a project named demo."}])
    assert [(c["id"], c["name"], json.loads(c["arguments"])) for c in calls] == [
        ("call_stub_1", "save_project", {"name": "demo"})]


def test_the_recorded_requests_expose_the_last_user_text(stub):
    mod, base = stub
    _chat(base, [{"role": "user", "content": GUIDED_BLOCK + "what now?"}])
    with urllib.request.urlopen(f"{base}/_stub/requests", timeout=5) as r:
        rec = json.loads(r.read())
    assert rec[-1]["last_user_text"].startswith("<untrusted_data>")
    assert "Guided mode is on" in rec[-1]["last_user_text"]
    assert rec[-1]["last_user_text"].endswith("what now?")
