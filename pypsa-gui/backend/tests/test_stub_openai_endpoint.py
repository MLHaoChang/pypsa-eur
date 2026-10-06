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


def test_a_read_tool_is_reported_as_done_not_applied(stub):
    _mod, base = stub
    text = 'Run the tool suggest_eh_setup with exactly these arguments: {"archetype":"off_grid"}'
    calls, said = _chat(base, [
        {"role": "user", "content": text},
        {"role": "assistant", "content": None, "tool_calls": [{
            "id": "call_stub_2", "type": "function",
            "function": {"name": "suggest_eh_setup", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call_stub_2", "content": '{"status": "ok"}'},
    ])
    assert calls == []
    assert said == "Done — suggest_eh_setup finished."
    assert "applied" not in said


# ── branch 3 (P25 gate B1 smoke): the Site grid fix ─────────────────────────
SITE_GRID = ("On the Site card, the grid connection is missing. Run suggest_eh_setup, "
             "then tag the import Link with eh_role = grid_import and the grid-side bus "
             "eh_poc = true, explaining each choice before the confirmation.")
SUGGESTED = {"status": "ok", "actions": [
    {"tool": "update_component", "args": {"component_class": "Link", "name": "grid_import",
                                           "attrs": {"eh_role": "grid_import"}},
     "effect": "tag"},
    {"tool": "update_component", "args": {"component_class": "Bus", "name": "grid",
                                           "attrs": {"eh_poc": True}}, "effect": "mark"}]}


def _assistant_call(cid, name, args):
    return {"role": "assistant", "content": None, "tool_calls": [{
        "id": cid, "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)}}]}


def test_branch_3_first_reads_the_suggestions(stub):
    _mod, base = stub
    calls, said = _chat(base, [{"role": "user", "content": GUIDED_BLOCK + SITE_GRID}])
    assert said == ""
    assert [(c["id"], c["name"], json.loads(c["arguments"])) for c in calls] == [
        ("call_stub_3a", "suggest_eh_setup", {})]


def test_branch_3_then_runs_the_first_suggested_action(stub):
    _mod, base = stub
    # the tool result arrives wrapped, as chat_service sends it
    wrapped = f"<untrusted_data>\n{json.dumps(SUGGESTED)}\n</untrusted_data>"
    calls, said = _chat(base, [
        {"role": "user", "content": SITE_GRID},
        _assistant_call("call_stub_3a", "suggest_eh_setup", {}),
        {"role": "tool", "tool_call_id": "call_stub_3a", "content": wrapped},
    ])
    assert said == ""
    assert [(c["id"], c["name"], json.loads(c["arguments"])) for c in calls] == [
        ("call_stub_3b", "update_component", SUGGESTED["actions"][0]["args"])]


@pytest.mark.parametrize("result,want", [
    ('{"error_kind": "confirmation_denied"}', "Understood — update_component was not applied."),
    ('{"ok": true}', "Done — update_component applied."),
])
def test_branch_3_closes_after_the_action(stub, result, want):
    _mod, base = stub
    calls, said = _chat(base, [
        {"role": "user", "content": SITE_GRID},
        _assistant_call("call_stub_3a", "suggest_eh_setup", {}),
        {"role": "tool", "tool_call_id": "call_stub_3a", "content": json.dumps(SUGGESTED)},
        _assistant_call("call_stub_3b", "update_component", SUGGESTED["actions"][0]["args"]),
        {"role": "tool", "tool_call_id": "call_stub_3b", "content": result},
    ])
    assert calls == [] and said == want


def test_branch_3_with_nothing_to_suggest_says_so(stub):
    _mod, base = stub
    calls, said = _chat(base, [
        {"role": "user", "content": SITE_GRID},
        _assistant_call("call_stub_3a", "suggest_eh_setup", {}),
        {"role": "tool", "tool_call_id": "call_stub_3a",
         "content": json.dumps({"status": "ok", "actions": []})},
    ])
    assert calls == [] and said == "Nothing to tag."


SITE_CRITICAL = ("On the Site card, no critical load is tagged. Propose which buses must "
                 "stay on (eh_critical = true) and tag them after I confirm.")


def test_branch_3_critical_fix_reads_then_runs_the_critical_action(stub):
    _mod, base = stub
    calls, _ = _chat(base, [{"role": "user", "content": SITE_CRITICAL}])
    assert [c["name"] for c in calls] == ["suggest_eh_setup"]
    crit = {"tool": "update_component", "args": {"component_class": "Bus", "name": "it_bus",
                                                 "attrs": {"eh_critical": True}}, "effect": "x"}
    found = {"status": "ok", "actions": SUGGESTED["actions"] + [crit]}
    calls, said = _chat(base, [
        {"role": "user", "content": SITE_CRITICAL},
        _assistant_call("call_stub_3a", "suggest_eh_setup", {}),
        {"role": "tool", "tool_call_id": "call_stub_3a", "content": json.dumps(found)},
    ])
    assert [(c["id"], json.loads(c["arguments"])) for c in calls] == [("call_stub_3b", crit["args"])]
# ── branch 4 (P25 re-gate R1 smoke): the Site footer, all actions at once ──
SITE_ALL = ("Review the Site card for this network and fix every gap you can, one "
            "confirmation at a time.")


def test_branch_4_reads_then_emits_every_action_in_one_response(stub):
    _mod, base = stub
    calls, _ = _chat(base, [{"role": "user", "content": GUIDED_BLOCK + SITE_ALL}])
    assert [(c["id"], c["name"]) for c in calls] == [("call_stub_4a", "suggest_eh_setup")]
    calls, said = _chat(base, [
        {"role": "user", "content": SITE_ALL},
        _assistant_call("call_stub_4a", "suggest_eh_setup", {}),
        {"role": "tool", "tool_call_id": "call_stub_4a",
         "content": f"<untrusted_data>\n{json.dumps(SUGGESTED)}\n</untrusted_data>"},
    ])
    assert said == ""
    assert [(c["id"], c["name"], json.loads(c["arguments"])) for c in calls] == [
        (f"call_stub_4b_{i}", a["tool"], a["args"]) for i, a in enumerate(SUGGESTED["actions"])]


def test_branch_4_closes_with_the_count(stub):
    _mod, base = stub
    msgs = [{"role": "user", "content": SITE_ALL},
            _assistant_call("call_stub_4a", "suggest_eh_setup", {}),
            {"role": "tool", "tool_call_id": "call_stub_4a", "content": json.dumps(SUGGESTED)},
            {"role": "tool", "tool_call_id": "call_stub_4b_0",
             "content": '{"error_kind": "confirmation_denied"}'},
            {"role": "tool", "tool_call_id": "call_stub_4b_1", "content": '{"ok": true}'}]
    calls, said = _chat(base, msgs)
    assert calls == [] and said == "Done — 1 applied, 1 not applied."


# ── branches 5 and 6 (P25 re-gate note 3): the Goal VOLL and stress buttons ─
VOLL_TEXT = "Set VOLL to 5000 €/MWh so the study can price shortfall."   # delegate.ts VOLL_TEXT
STRESS_TEXT = ("Add a stress scenario to this project's registry for a weak-grid site like "
               "the Data Center Energy Hub example; read the current registry first and send "
               "the whole list back with put_stress_scenarios.")         # stressScenarioText()
SYSTEM = {"role": "system",
          "content": "You are … Working with Data Center Energy Hub: 3 buses, 0 lines, "
                     "168 snapshots, solved=True."}


def test_branch_5_voll_button_sets_voll(stub):
    _mod, base = stub
    calls, said = _chat(base, [SYSTEM, {"role": "user", "content": GUIDED_BLOCK + VOLL_TEXT}])
    assert said == ""
    assert [(c["id"], c["name"], json.loads(c["arguments"])) for c in calls] == [
        ("call_stub_5", "update_solver_config", {"partial": {"voll": 5000}})]
    calls, said = _chat(base, [
        {"role": "user", "content": VOLL_TEXT},
        _assistant_call("call_stub_5", "update_solver_config", {"partial": {"voll": 5000}}),
        {"role": "tool", "tool_call_id": "call_stub_5", "content": '{"voll": 5000}'},
    ])
    assert calls == [] and said == "Done — update_solver_config applied."


def test_branch_6_stress_button_reads_then_writes_the_whole_list(stub):
    _mod, base = stub
    calls, _ = _chat(base, [SYSTEM, {"role": "user", "content": STRESS_TEXT}])
    assert [(c["id"], c["name"], json.loads(c["arguments"])) for c in calls] == [
        ("call_stub_6a", "get_stress_scenarios", {"name": "Data Center Energy Hub"})]
    existing = {"id": "heatwave", "kind": "parametric", "frequency_per_year": 0.5,
                "electrical_load_multiplier": 1.3, "renewable_availability_multiplier": 0.8}
    calls, said = _chat(base, [
        SYSTEM, {"role": "user", "content": STRESS_TEXT},
        _assistant_call("call_stub_6a", "get_stress_scenarios", {"name": "Data Center Energy Hub"}),
        {"role": "tool", "tool_call_id": "call_stub_6a",
         "content": f"<untrusted_data>\n{json.dumps({'scenarios': [existing], 'error': None})}\n</untrusted_data>"},
    ])
    assert said == "" and [c["id"] for c in calls] == ["call_stub_6b"]
    assert calls[0]["name"] == "put_stress_scenarios"
    args = json.loads(calls[0]["arguments"])
    assert args["name"] == "Data Center Energy Hub"
    assert args["scenarios"][0] == existing            # the whole list, kept
    added = args["scenarios"][1]
    assert added["id"] != existing["id"] and added["kind"] == "parametric"
    # the one the stub adds passes the registry's own validation
    from services.adequacy.stress import _validate
    _validate(args["scenarios"])


def test_branch_6_without_an_open_project_says_so(stub):
    _mod, base = stub
    calls, said = _chat(base, [{"role": "user", "content": STRESS_TEXT}])
    assert calls == [] and said == "No project is open."


# ── chat harness (issues 04, 09): the parity battery's phrases ──────────────

def test_ask_me_which_yields_an_ask_user_card_then_closes(stub):
    _mod, base = stub
    text = ("I want to open or create a project. List my projects and the "
            "available templates, then ask me which to open.")
    calls, said = _chat(base, [{"role": "user", "content": text}])
    assert [c["name"] for c in calls] == ["ask_user"]
    args = json.loads(calls[0]["arguments"])
    assert sum(1 for o in args["options"] if o.get("recommended")) == 1
    calls, said = _chat(base, [
        {"role": "user", "content": text},
        {"role": "assistant", "content": None, "tool_calls": [{
            "id": "call_stub_7", "type": "function",
            "function": {"name": "ask_user", "arguments": json.dumps(args)}}]},
        {"role": "tool", "tool_call_id": "call_stub_7", "content": '{"status": "presented"}'},
    ])
    assert calls == [] and said == "Pick one above."


@pytest.mark.parametrize("text,tool,args,closing", [
    ("Start the build-network workflow and tell me the first step.",
     "start_workflow", {"workflow_id": "build-network"}, "Started: first, see what is there."),
    ("Please end the current workflow.", "end_workflow", {}, "Workflow ended."),
])
def test_workflow_phrases_call_the_tool_then_close(stub, text, tool, args, closing):
    _mod, base = stub
    calls, _ = _chat(base, [{"role": "user", "content": text}])
    assert [c["name"] for c in calls] == [tool]
    assert json.loads(calls[0]["arguments"]) == args
    calls, said = _chat(base, [
        {"role": "user", "content": text},
        {"role": "assistant", "content": None, "tool_calls": [{
            "id": "call_x", "type": "function",
            "function": {"name": tool, "arguments": json.dumps(args)}}]},
        {"role": "tool", "tool_call_id": "call_x", "content": '{"ok": true}'},
    ])
    assert calls == [] and said == closing


# ── Branch 7 (P27a, deferred spec §1.3): a template create from chat, then a
# second tool in the SAME response — what shows the same-turn dispatch works.
TEMPLATE_TEXT = "Create a project from the eh_datacenter template called probe-a8"


def test_branch_7_pins_the_exact_regex(stub):
    mod, _base = stub
    assert mod._TEMPLATE_CREATE.pattern == (
        r"Create a project from the ([\w-]+) template called ([\w.-]+?)\.?(?=\s|$)")


def test_branch_7_emits_both_calls_in_one_response_in_order(stub):
    _mod, base = stub
    calls, said = _chat(base, [SYSTEM, {"role": "user", "content": TEMPLATE_TEXT + "."}])
    assert said == ""
    assert [(c["id"], c["name"], json.loads(c["arguments"])) for c in calls] == [
        ("call_stub_7a", "create_project_from_template",
         {"template_id": "eh_datacenter", "new_name": "probe-a8"}),
        # `list_components` requires `component_class` (the spec's `{}` would
        # come back as a tool_error, not the tool_result the smoke asserts).
        ("call_stub_7b", "list_components", {"component_class": "Bus"}),
    ]


def test_branch_7_waits_for_both_results_then_closes(stub):
    _mod, base = stub
    first = [
        {"role": "user", "content": TEMPLATE_TEXT},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_stub_7a", "type": "function", "function": {
                "name": "create_project_from_template",
                "arguments": '{"template_id":"eh_datacenter","new_name":"probe-a8"}'}},
            {"id": "call_stub_7b", "type": "function", "function": {
                "name": "list_components", "arguments": '{"component_class":"Bus"}'}}]},
        {"role": "tool", "tool_call_id": "call_stub_7a", "content": '{"name": "probe-a8"}'},
    ]
    # One result in: no new call, no closing sentence yet.
    calls, said = _chat(base, first)
    assert calls == [] and said == ""
    calls, said = _chat(base, first + [
        {"role": "tool", "tool_call_id": "call_stub_7b", "content": "[]"}])
    assert calls == []
    assert said == "Done — create_project_from_template applied."


# ── chat harness issue 20: reasoning and cached usage on the OpenAI wire ──

def _chunks(base: str, text: str) -> list[dict]:
    """POST one user message; return every decoded SSE chunk."""
    body = json.dumps({"model": "stub-model", "stream": True,
                       "messages": [{"role": "user", "content": text}]}).encode()
    req = urllib.request.Request(f"{base}/v1/chat/completions", data=body,
                                 headers={"content-type": "application/json"})
    out = []
    with urllib.request.urlopen(req, timeout=10) as r:
        for raw in r.read().decode().split("\n\n"):
            raw = raw.strip()
            if raw.startswith("data: ") and raw != "data: [DONE]":
                out.append(json.loads(raw[len("data: "):]))
    return out


def test_the_think_branch_streams_reasoning_before_the_answer(stub):
    mod, base = stub
    chunks = _chunks(base, f"{mod._THINK} before you answer.")
    deltas = [c["choices"][0]["delta"] for c in chunks if c.get("choices")]
    reasoning = "".join(d.get("reasoning_content") or "" for d in deltas)
    content = "".join(d.get("content") or "" for d in deltas)
    assert reasoning and content
    first_content = next(i for i, d in enumerate(deltas) if d.get("content"))
    assert all(d.get("reasoning_content") for d in deltas[:first_content])


def test_every_reply_reports_a_cache_read_in_its_usage(stub):
    mod, base = stub
    usage = [c["usage"] for c in _chunks(base, "hello") if c.get("usage")][-1]
    cached = usage["prompt_tokens_details"]["cached_tokens"]
    assert 0 < cached < usage["prompt_tokens"]
