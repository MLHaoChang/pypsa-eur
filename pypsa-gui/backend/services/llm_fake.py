"""
Deterministic scripted provider. No network, no key.

This is the provider-level fake the seam spec calls for: it runs the REAL
agent loop (unlike StreamRequest.script, which fakes output frames and
bypasses dispatch/confirmation). It also records every LLMRequest so tests
can assert the `stable` annotations actually arrive — the guard against the
silent tenfold cache-cost regression.
"""
from __future__ import annotations

import copy
from typing import Any, Iterator

from services.llm_provider import LLMEvent, LLMRequest, ProviderError


class FakeProvider:
    name = "fake"

    def __init__(self, turns: list[dict[str, Any] | ProviderError]) -> None:
        self._turns = list(turns)
        self.requests: list[LLMRequest] = []

    def stream(self, request: LLMRequest) -> Iterator[LLMEvent]:
        self.requests.append(copy.deepcopy(request))
        if not self._turns:
            raise AssertionError("FakeProvider: script exhausted")
        turn = self._turns.pop(0)
        if isinstance(turn, ProviderError):
            raise turn
        yield from turn.get("events", [])
        yield LLMEvent(
            type="message_done",
            blocks=copy.deepcopy(turn.get("blocks", [])),
            usage=dict(turn.get("usage", {})),
        )
