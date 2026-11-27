"""
Digest of the chat tool registry, to prove a fragment move changes nothing.

RETRO-FRAG-1: moving a tool family out of `harness/catalogue.py` and
`services/chat_tools.py` into a fragment pair must leave what the providers are
sent and what dispatch does exactly as it was. Run this from
`pypsa-gui/backend` on master and on the branch; every line must be equal::

    python smoke/tool_registry_digest.py

One `<sha256>  <what>` line per surface: the Anthropic and OpenAI-compatible
tool payloads (in order), the OpenAI 128-tool selection for a fixed query, the
TOOLS name order, TOOL_ROUTES, the DISPATCHERS keys with each callable's name
and wrapper depth (the lock / study gates), PRE_DISPATCH_VALIDATORS keys, the
undo-captured, lock-gated and study-gated sets, and the two service-call write
sets.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _sha(value) -> str:
    text = json.dumps(value, sort_keys=False, default=repr, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _depth(fn) -> int:
    n = 0
    while hasattr(fn, "__wrapped__"):
        fn, n = fn.__wrapped__, n + 1
    return n


def digests() -> list[tuple[str, str]]:
    from harness import catalogue as C
    from harness.providers import wiring
    from harness.providers.openai_compat import _to_openai_tools
    from services import chat_tools as T

    tools = wiring._tools_payload(stage_only=True)
    routes = {
        k: [list(r) if isinstance(r, tuple) else r for r in v]
        for k, v in sorted(C.TOOL_ROUTES.items())
    }
    return [
        ("anthropic tools payload", _sha(tools)),
        ("openai tools payload", _sha(_to_openai_tools(tools))),
        (
            "openai bounded selection",
            _sha(
                [
                    t["name"]
                    for t in wiring._bounded_tools(
                        tools, "export the report with my Word template"
                    )
                ]
            ),
        ),
        ("TOOLS name order", _sha([t["name"] for t in C.TOOLS])),
        ("TOOL_ROUTES", _sha(routes)),
        (
            "DISPATCHERS",
            _sha(
                sorted(
                    (k, getattr(v, "__name__", repr(v)), _depth(v))
                    for k, v in T.DISPATCHERS.items()
                )
            ),
        ),
        ("PRE_DISPATCH_VALIDATORS", _sha(sorted(T.PRE_DISPATCH_VALIDATORS))),
        ("UNDO_CAPTURED_TOOLS", _sha(sorted(T.UNDO_CAPTURED_TOOLS))),
        ("lock-gated", _sha(sorted(T._lock_gated_tool_names()))),
        ("study-gated", _sha(sorted(T._study_gated_tool_names()))),
        ("NETWORK_WRITES", _sha(sorted(C._SERVICE_CALL_NETWORK_WRITES))),
        ("NON_NETWORK_WRITES", _sha(sorted(C._SERVICE_CALL_NON_NETWORK_WRITES))),
    ]


def main() -> int:
    for what, digest in digests():
        print(f"{digest}  {what}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
