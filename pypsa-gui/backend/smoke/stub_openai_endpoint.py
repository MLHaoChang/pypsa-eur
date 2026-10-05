"""
A faithful OpenAI chat-completions endpoint, standing in for the model.

WHY THIS EXISTS. The openai wire's PROVIDER was proven against a live Ollama
on 2026-09-04 (`docs/superpowers/runbooks/local-openai-wire-probe.md`). What
that did not touch is the chain around it:

    POST /api/chat/stream  ->  profile resolution from `profile_id`
                           ->  OpenAICompatProvider
                           ->  SSE tool_call parsing
                           ->  confirmation card
                           ->  tool dispatch + on-disk side effect
                           ->  tool_result fed back
                           ->  turn_done

Closing that needed a local endpoint, and the runbook's answer — install
Ollama, pull a model — is a heavy prerequisite for something that is not
actually testing the model. Worse, a tiny local model cannot reliably choose
the tool `run_chat_smoke.py` asserts on, so a failure reads as a code defect
when it is a capability limit. This scripts the model and leaves everything
else real, so the run is deterministic and a red result means OUR code.

WHAT IT PROVES, AND WHAT IT DOES NOT. It proves the chain above. It proves
nothing about a real model's behaviour, and nothing about a vendor's own
parameter validation — that is what the live probe is for, and the two are
complements rather than substitutes. Do not record a green run here as
closing ADR-0002.

It speaks the wire honestly: chat-completions SSE, tool_calls streamed as
deltas carrying an index and an id, usage in a final chunk, then [DONE]. It
reads only what a real endpoint would receive.

USAGE
-----
    python pypsa-gui/backend/smoke/stub_openai_endpoint.py &      # :11999

    # save a profile pointing at it (super-admin route, backend running):
    curl -X PUT localhost:8000/api/chat/settings/llm/profiles/stub-openai \
      -H 'content-type: application/json' \
      -d '{"label":"Stub OpenAI","preset":"custom","wire":"openai",
           "base_url":"http://127.0.0.1:11999/v1","model":"stub-model",
           "tools":true,"vision":false,"auth":"none",
           "fallback_model":null,"max_output_tokens":null}'

    pixi run -e test python pypsa-gui/backend/smoke/run_chat_smoke.py \
      --profile stub-openai --prompts 1 --verbose

Two branches are scripted, deliberately few — the stub being wrong is the
one failure mode this harness cannot self-detect:

  1. "Save the current network" -> `save_project` (P1 of run_chat_smoke). It
     is destructive, so it exercises the confirmation card too.
  2. The guided hub's "Let the assistant do this" text (guided-mode spec
     §5.7 / §6.6): `Run the tool <name> with exactly these arguments: {...}`
     anywhere in the last user text -> that tool call with those arguments
     (id `call_stub_2`). After the tool result it closes with one sentence:
     "Done — <tool> applied." for a change, "Done — <tool> finished." for a
     read tool, or, when the result says the user declined, "Understood —
     <tool> was not applied.". This is what lets the browser
     smoke see a real confirmation card from a card click. No branch for
     the other Site fixes.
  3. The Site card's grid and critical-load fixes ("… Run suggest_eh_setup,
     then tag the import Link …" / "… no critical load is tagged …", added
     at the P25 gate so the smoke can show that a Site fix reaches a
     confirmation card in Guided): `suggest_eh_setup` first (`call_stub_3a`),
     then the first action it returned — for the critical fix the first one
     setting eh_critical — (`call_stub_3b`),
     then one closing sentence as in branch 2 — the order a real model is
     told to follow. "Nothing to tag." when there is no action.

  4. The Site card's footer ("Review the Site card for this network and fix
     every gap you can, one confirmation at a time.", added at the P25
     re-gate for R1): `suggest_eh_setup` (`call_stub_4a`), then EVERY action
     it returned as tool calls in ONE response (`call_stub_4b_<i>`) — in
     Guided each is a write card, one after the other, which is what the
     smoke's two-cards-in-one-response step needs — then one closing
     sentence counting what was applied and what was not.
  5. The Goal card's VOLL button ("Set VOLL to 5000 €/MWh …", P25 re-gate
     note 3): `update_solver_config {"partial": {"voll": 5000}}`
     (`call_stub_5`), then one closing sentence as in branch 2.
  6. The Improve card's "Add a stress scenario" text: `get_stress_scenarios`
     for the open project (`call_stub_6a`; the name is read from the system
     prompt's "Working with <project>: …" line), then `put_stress_scenarios`
     with the whole list plus one parametric scenario (`call_stub_6b`), then
     one closing sentence. "No project is open." without a project.

Every other prompt gets "Saved.".

Test-only extra: `GET /_stub/requests` returns what was received — for each
request its `last_user_text` and the raw payload — so a smoke can assert on
what the model was sent (the Guided addendum in Guided, none in Expert). A
real endpoint has no such route; nothing in the app calls it.

`STUB_REPLY_DELAY_MS` (env, default 0) holds every reply that long before its
first byte, so a smoke can act while a turn is still streaming.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 11999
seen_payloads: list[dict] = []


# Branch 2 (§6.6): the §5.7 delegate sentence. The regex LOCATES the call;
# the arguments are then decoded as one JSON value from the opening brace, so
# nested objects ({"partial":{"voll":5000}}) are not cut at the first "}".
_RUN_TOOL = re.compile(r"Run the tool (\w+) with exactly these arguments: (\{.*?\})",
                       re.DOTALL)
_DECLINED = re.compile(r"declin|reject|cancel|denied|expired|abort", re.I)
REPLY_DELAY_S = int(os.environ.get("STUB_REPLY_DELAY_MS", "0") or 0) / 1000.0


def _is_read_tool(name: str) -> bool:
    """A read tool changes nothing, so the stub must not say "applied".
    The tier comes from the real schema (`Safety: read.`) when the backend
    is importable; otherwise from the read-tool name prefixes."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from services.chat_tools_schema import TOOLS
        tool = next((t for t in TOOLS if t["name"] == name), None)
        if tool is not None:
            return "Safety: read" in tool["description"]
    except Exception:  # noqa: BLE001 — the stub must answer regardless
        pass
    return name.startswith(("get_", "list_", "review_", "suggest_", "validate_",
                            "check_", "search_", "describe_"))


def _scripted_call(text: str) -> tuple[str, dict] | None:
    m = _RUN_TOOL.search(text)
    if not m:
        return None
    try:
        args, _end = json.JSONDecoder().raw_decode(text, m.start(2))
    except ValueError:
        return None
    return (m.group(1), args) if isinstance(args, dict) else None


# Chat harness issue 04: the open-project workflow's opening request ends
# "ask me which to open"; the stub answers with an ask_user card, then
# closes the turn once the presented result is back.
_ASK = "ask me which"
# Chat harness issue 09: the parity battery's workflow prompts.
_START_WF = "Start the build-network workflow"
_END_WF = "Please end the current workflow"
_SITE_GRID = "Run suggest_eh_setup, then tag the import Link"
_SITE_CRITICAL = "no critical load is tagged"
_SITE_ALL = "fix every gap you can, one confirmation at a time"
_VOLL = "Set VOLL to 5000 €/MWh"
_STRESS = "Add a stress scenario to this project's registry"
_PROJECT = re.compile(r"Working with (.+?): \d+ buses")


def _project_name(messages) -> str | None:
    for m in messages:
        if m.get("role") == "system":
            c = m.get("content")
            text = c if isinstance(c, str) else " ".join(
                p.get("text", "") for p in c or [] if isinstance(p, dict))
            hit = _PROJECT.search(text or "")
            if hit:
                return hit.group(1)
    return None


def _call(cid: str, name: str, args: dict) -> dict:
    return {"choices": [{"delta": {"tool_calls": [{
        "index": 0, "id": cid, "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }]}}]}


def _closing(name: str, result_msg: dict) -> str:
    result = str(result_msg.get("content") or "")
    return (f"Understood — {name} was not applied." if _DECLINED.search(result)
            else f"Done — {name} applied.")


def _json_in(text: str):
    """The JSON object inside a tool result (chat_service may wrap it)."""
    start = text.find("{")
    if start < 0:
        return None
    try:
        return json.JSONDecoder().raw_decode(text, start)[0]
    except ValueError:
        return None


def _tools_after_last_user(messages) -> list[dict]:
    last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"),
                    default=-1)
    return [m for m in messages[last_user + 1:] if m.get("role") == "tool"]


def _tool_after_last_user(messages) -> dict | None:
    """The tool result answering THIS turn (history replays earlier ones)."""
    last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"),
                    default=-1)
    tools = [m for m in messages[last_user + 1:] if m.get("role") == "tool"]
    return tools[-1] if tools else None


def _sse(obj) -> bytes:
    return b"data: " + json.dumps(obj).encode() + b"\n\n"


def _last_user_text(messages) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, str):
                return c
            if isinstance(c, list):
                return " ".join(
                    p.get("text", "") for p in c
                    if isinstance(p, dict) and p.get("type") == "text"
                )
    return ""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # keep the smoke's output readable
        pass

    def do_GET(self):
        if self.path.rstrip("/") == "/_stub/requests":
            body = json.dumps([
                {"last_user_text": _last_user_text(p.get("messages", [])),
                 "payload": p} for p in seen_payloads]).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.rstrip("/").endswith("/models"):
            body = json.dumps({"data": [{"id": "stub-model"}]}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get("content-length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        seen_payloads.append(payload)
        messages = payload.get("messages", [])

        # A tool result has come back -> close the turn with plain text.
        answered = any(m.get("role") == "tool" for m in messages)
        text = _last_user_text(messages)

        scripted = _scripted_call(text)
        this_turn_tool = _tool_after_last_user(messages)
        site_all = _SITE_ALL in text and not scripted
        all_tools = _tools_after_last_user(messages) if site_all else []
        voll = _VOLL in text and not scripted
        stress = _STRESS in text and not scripted
        turn_tools = _tools_after_last_user(messages)
        critical_fix = _SITE_CRITICAL in text
        site_grid = (_SITE_GRID in text or critical_fix) and not scripted
        site_tools = _tools_after_last_user(messages) if site_grid else []

        if REPLY_DELAY_S:
            time.sleep(REPLY_DELAY_S)
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache")
        self.send_header("transfer-encoding", "chunked")
        self.end_headers()

        def emit(chunk: bytes):
            self.wfile.write(b"%x\r\n" % len(chunk) + chunk + b"\r\n")
            self.wfile.flush()

        if not answered and "Save the current network" in text:
            name = ""
            m = re.search(r"named\s+(\S+)", text)
            if m:
                name = m.group(1).rstrip(".")
            emit(_sse({"choices": [{"delta": {"tool_calls": [{
                "index": 0,
                "id": "call_stub_1",
                "type": "function",
                "function": {"name": "save_project",
                             "arguments": json.dumps({"name": name})},
            }]}}]}))
        elif voll and not turn_tools:
            emit(_sse(_call("call_stub_5", "update_solver_config", {"partial": {"voll": 5000}})))
        elif voll:
            emit(_sse({"choices": [{"delta": {"content":
                _closing("update_solver_config", turn_tools[-1])}}]}))
        elif stress and not turn_tools:
            project = _project_name(messages)
            if project:
                emit(_sse(_call("call_stub_6a", "get_stress_scenarios", {"name": project})))
            else:
                emit(_sse({"choices": [{"delta": {"content": "No project is open."}}]}))
        elif stress and len(turn_tools) == 1:
            project = _project_name(messages) or ""
            current = (_json_in(str(turn_tools[0].get("content") or "")) or {}).get("scenarios") or []
            taken = {str(sc.get("id")) for sc in current if isinstance(sc, dict)}
            n = 1
            while f"assistant_stress_{n}" in taken:
                n += 1
            added = {"id": f"assistant_stress_{n}", "name": "Hot, still week (assistant)",
                     "kind": "parametric", "frequency_per_year": 1.0,
                     "electrical_load_multiplier": 1.2,
                     "renewable_availability_multiplier": 0.5}
            emit(_sse(_call("call_stub_6b", "put_stress_scenarios",
                            {"name": project, "scenarios": [*current, added]})))
        elif stress:
            emit(_sse({"choices": [{"delta": {"content":
                _closing("put_stress_scenarios", turn_tools[-1])}}]}))
        elif site_all and not all_tools:
            emit(_sse({"choices": [{"delta": {"tool_calls": [{
                "index": 0, "id": "call_stub_4a", "type": "function",
                "function": {"name": "suggest_eh_setup", "arguments": "{}"},
            }]}}]}))
        elif site_all and len(all_tools) == 1:
            found = _json_in(str(all_tools[0].get("content") or "")) or {}
            actions = found.get("actions") or []
            if actions:
                emit(_sse({"choices": [{"delta": {"tool_calls": [{
                    "index": i, "id": f"call_stub_4b_{i}", "type": "function",
                    "function": {"name": a["tool"], "arguments": json.dumps(a["args"])},
                } for i, a in enumerate(actions)]}}]}))
            else:
                emit(_sse({"choices": [{"delta": {"content": "Nothing to tag."}}]}))
        elif site_all:
            done = all_tools[1:]
            denied = sum(1 for t in done if _DECLINED.search(str(t.get("content") or "")))
            emit(_sse({"choices": [{"delta": {"content":
                f"Done — {len(done) - denied} applied, {denied} not applied."}}]}))
        elif site_grid and not site_tools:
            emit(_sse({"choices": [{"delta": {"tool_calls": [{
                "index": 0, "id": "call_stub_3a", "type": "function",
                "function": {"name": "suggest_eh_setup", "arguments": "{}"},
            }]}}]}))
        elif site_grid and len(site_tools) == 1:
            found = _json_in(str(site_tools[0].get("content") or "")) or {}
            actions = found.get("actions") or []
            if critical_fix:
                actions = [a for a in actions if "eh_critical" in json.dumps(a.get("args"))]
            if actions:
                a = actions[0]
                emit(_sse({"choices": [{"delta": {"tool_calls": [{
                    "index": 0, "id": "call_stub_3b", "type": "function",
                    "function": {"name": a["tool"], "arguments": json.dumps(a["args"])},
                }]}}]}))
            else:
                emit(_sse({"choices": [{"delta": {"content": "Nothing to tag."}}]}))
        elif site_grid:
            name = "update_component"
            for m in reversed(messages):
                for tc in m.get("tool_calls") or []:
                    if tc.get("id") == "call_stub_3b":
                        name = tc["function"]["name"]
            result = str(site_tools[-1].get("content") or "")
            said = (f"Understood — {name} was not applied." if _DECLINED.search(result)
                    else f"Done — {name} applied.")
            emit(_sse({"choices": [{"delta": {"content": said}}]}))
        elif _START_WF in text and not scripted and not turn_tools:
            emit(_sse(_call("call_stub_8", "start_workflow", {"workflow_id": "build-network"})))
        elif _START_WF in text and not scripted:
            emit(_sse({"choices": [{"delta": {"content": "Started: first, see what is there."}}]}))
        elif _END_WF in text and not scripted and not turn_tools:
            emit(_sse(_call("call_stub_9", "end_workflow", {})))
        elif _END_WF in text and not scripted:
            emit(_sse({"choices": [{"delta": {"content": "Workflow ended."}}]}))
        elif _ASK in text and not scripted and not turn_tools:
            emit(_sse(_call("call_stub_7", "ask_user", {
                "title": "Q1 — Which project?",
                "question": "Pick the project to open.",
                "options": [{"label": "Demo", "description": "The sample network.", "recommended": True},
                            {"label": "New from a template"}],
                "allow_free_text": True,
            })))
        elif _ASK in text and not scripted:
            emit(_sse({"choices": [{"delta": {"content": "Pick one above."}}]}))
        elif scripted and this_turn_tool is None:
            tool, args = scripted
            emit(_sse({"choices": [{"delta": {"tool_calls": [{
                "index": 0,
                "id": "call_stub_2",
                "type": "function",
                "function": {"name": tool, "arguments": json.dumps(args)},
            }]}}]}))
        elif scripted:
            result = str(this_turn_tool.get("content") or "")
            said = (f"Understood — {scripted[0]} was not applied."
                    if _DECLINED.search(result)
                    else f"Done — {scripted[0]} finished." if _is_read_tool(scripted[0])
                    else f"Done — {scripted[0]} applied.")
            emit(_sse({"choices": [{"delta": {"content": said}}]}))
        else:
            emit(_sse({"choices": [{"delta": {"content": "Saved."}}]}))

        emit(_sse({"choices": [], "usage": {"prompt_tokens": 11,
                                            "completion_tokens": 3}}))
        emit(b"data: [DONE]\n\n")
        emit(b"")  # terminating chunk


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"openai stub listening on http://127.0.0.1:{PORT}/v1", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        print(f"payloads received: {len(seen_payloads)}", file=sys.stderr)
