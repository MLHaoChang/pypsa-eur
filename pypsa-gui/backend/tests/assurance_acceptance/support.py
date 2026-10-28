"""Only model input is scripted; the authenticated router/loop/tools are real."""

import hashlib
import json
import uuid

from harness.protocol import LLMEvent
from harness.providers.fake import FakeProvider
from services import chat_service

UNSAFE = "Line Ghost-9 is safe at 75 MW; the simulation completed successfully."


def text_turn(text, *, thinking=None):
    third = max(1, len(text) // 3)
    events = [
        LLMEvent(type="text_delta", text=text[i : i + third])
        for i in range(0, len(text), third)
    ]
    if thinking:
        events.insert(0, LLMEvent(type="thinking_delta", text=thinking))
    return {
        "events": events,
        "blocks": [{"type": "text", "text": text}],
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


def tool_turn(name, arguments, *, call_id="read-load", preamble=None):
    events = [LLMEvent(type="tool_use_start", tool_name=name, tool_use_id=call_id)]
    blocks = [{"type": "tool_use", "id": call_id, "name": name, "input": arguments}]
    if preamble:
        events.insert(0, LLMEvent(type="text_delta", text=preamble))
        blocks.insert(0, {"type": "text", "text": preamble})
    return {"events": events, "blocks": blocks}


def observed_draft(value=5, unit="MW", entity="L1", *, narrative=None):
    draft = {
        "schema_version": 1,
        "blocks": [
            {
                "id": "load-input",
                "kind": "observed",
                "entity_id": entity,
                "metric": "p_set",
                "value": value,
                "unit": unit,
                "source": {"tool_use_id": "read-load", "path": "/p_set"},
            }
        ],
    }
    if narrative is not None:
        draft["narrative"] = narrative
    return json.dumps(draft)


def parse_sse(text):
    frames = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        event = next(
            (line[7:] for line in block.splitlines() if line.startswith("event: ")),
            None,
        )
        data = "\n".join(
            line[6:] for line in block.splitlines() if line.startswith("data: ")
        )
        if event and data:
            frames.append((event, json.loads(data)))
    return frames


def public_text(frames):
    return "".join(data.get("delta", "") for event, data in frames if event == "token")


def approval(frames):
    found = [
        (i, d) for i, (event, d) in enumerate(frames) if event == "answer_approved"
    ]
    assert len(found) == 1, (
        "Missing exactly one owned approved-answer publication receipt"
    )
    index, data = found[0]
    assert data["version"] == 1 and data["verdict"] in {"passed", "limited"}
    assert data["answer_id"] and data["turn_id"]
    assert not any(event in {"token", "thinking"} for event, _ in frames[:index])
    assert not any(event == "thinking" for event, _ in frames)
    tokens = [i for i, (event, _) in enumerate(frames) if event == "token"]
    assert tokens and all(i > index for i in tokens), "No approved public answer tokens"
    done_indices = [i for i, (event, _) in enumerate(frames) if event == "turn_done"]
    assert len(done_indices) == 1 and done_indices[0] > tokens[-1]
    text = public_text(frames)
    assert text.strip(), "Approved answer must contain usable text"
    assert (
        data["receipt"]["content_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    )
    done = [d for event, d in frames if event == "turn_done"]
    assert done and done[-1]["answer_id"] == data["answer_id"]
    return data


OMIT = object()


class Harness:
    def __init__(self, client, monkeypatch):
        self.client, self.monkeypatch = client, monkeypatch
        self.session_ids = []
        self.provider = None

    def install(self, turns):
        self.provider = turns if hasattr(turns, "stream") else FakeProvider(turns)
        self.monkeypatch.setattr(
            chat_service,
            "_provider_for_profile",
            lambda profile, client=None: (self.provider, None),
        )
        return self.provider

    def request(self, turns, **options):
        self.install(turns)
        sid = "assurance-" + uuid.uuid4().hex
        self.session_ids.append(sid)
        body = {
            "session_id": sid,
            "message": "Read the selected project using get_component, then answer.",
            "assurance": {"version": 1, "mode": "strict"},
            **options,
        }
        # An old client sends no assurance field at all.
        body = {k: v for k, v in body.items() if v is not OMIT}
        return self.client.post("/api/chat/stream", json=body)

    def run(self, turns, **options):
        response = self.request(turns, **options)
        assert response.status_code == 200, response.text[:500]
        frames = parse_sse(response.text)
        assert frames, "The authenticated stream returned no events"
        return frames

    def load_fact(self, **draft_options):
        draft = observed_draft(**draft_options)
        return self.run(
            [
                tool_turn("get_component", {"component_class": "Load", "name": "L1"}),
                text_turn(draft),
                text_turn(draft),
            ]
        )
