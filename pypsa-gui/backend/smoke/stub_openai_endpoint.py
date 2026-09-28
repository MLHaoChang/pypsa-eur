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
     "Done — <tool> applied." (or, when the result says the user declined,
     "Understood — <tool> was not applied."). This is what lets the browser
     smoke see a real confirmation card from a card click. No branch for
     the Site fixes in v1.

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


def _scripted_call(text: str) -> tuple[str, dict] | None:
    m = _RUN_TOOL.search(text)
    if not m:
        return None
    try:
        args, _end = json.JSONDecoder().raw_decode(text, m.start(2))
    except ValueError:
        return None
    return (m.group(1), args) if isinstance(args, dict) else None


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
                    if _DECLINED.search(result) else f"Done — {scripted[0]} applied.")
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
