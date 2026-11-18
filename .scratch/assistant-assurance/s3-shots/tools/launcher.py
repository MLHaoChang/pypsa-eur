"""Shot launcher for S3 (objective-bridge) screenshots. Throwaway; not part of the repo.

Runs the real FastAPI app from the wt-s3-shots checkout with two seams patched:

* the chat model provider is a scripted provider (harness.providers.fake.FakeProvider
  subclass) that drafts each checked ledger by echoing the server's own computed
  ledger (the values the server offered in <untrusted_data>), so no key is needed;
* when the flag file INJECT10 exists, services.results.cost_breakdown.compute_cost_breakdown
  over-reports "total" by +10 EUR (the G4-style injection the unit test uses).

Usage: python launcher.py  (cwd = pypsa-gui/backend; env set by start-backend.sh)
"""
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.getcwd())

FLAG = Path(__file__).with_name("INJECT10")

from harness.protocol import LLMEvent  # noqa: E402
from harness.providers.fake import FakeProvider  # noqa: E402


def _text_turn(text):
    third = max(1, len(text) // 3)
    return {
        "events": [
            LLMEvent(type="text_delta", text=text[i : i + third])
            for i in range(0, len(text), third)
        ],
        "blocks": [{"type": "text", "text": text}],
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


class ScriptedLedgerProvider(FakeProvider):
    """Echo the server-computed ledger back as the draft (server's own fact values)."""

    name = "fake"

    def __init__(self):
        super().__init__([])

    def stream(self, request):
        content = request.messages[-1]["content"] if request.messages else ""
        text = (
            content
            if isinstance(content, str)
            else "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
        )
        found = re.findall(
            r'<untrusted_data>(\{"schema_version".*?\})</untrusted_data>', text
        )
        if found:
            ledger = json.loads(found[-1])
            self._turns.append(_text_turn(json.dumps(ledger)))
        else:
            self._turns.append(_text_turn("(scripted shot provider: no ledger offered)"))
        yield from super().stream(request)


def _provider(profile, client=None):
    return ScriptedLedgerProvider(), None


import harness.loop as loop  # noqa: E402
import harness.providers.wiring as wiring  # noqa: E402
from services import chat_service  # noqa: E402

for mod in (loop, wiring, chat_service):
    setattr(mod, "_provider_for_profile", _provider)

from services.results import cost_breakdown  # noqa: E402

_real = cost_breakdown.compute_cost_breakdown


def _maybe_inflated(*args, **kwargs):
    cb = _real(*args, **kwargs)
    if FLAG.exists() and isinstance(cb, dict) and cb.get("total") is not None:
        return {**cb, "total": cb["total"] + 10.0}
    return cb


cost_breakdown.compute_cost_breakdown = _maybe_inflated

import uvicorn  # noqa: E402

if __name__ == "__main__":
    import main  # noqa: E402

    uvicorn.run(main.app, host="127.0.0.1", port=int(os.environ.get("SHOT_API_PORT", "8000")))
