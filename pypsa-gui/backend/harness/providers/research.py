"""
The research capability: a provider's own web search and fetch, for research
turns only (study harness issue 09, design §8, owner decision SH-RESEARCH-1).

This is the one place that names the server tools. Above the provider layer a
research turn is this wrapper plus the `FetchedSource` records it reads out
of the turn's blocks; no vendor
word leaves `harness/providers`.

- `web_research(profile)` says whether a profile's provider can search: only
  the Anthropic wire at its own endpoint. Any other wire, or a custom
  endpoint, is `None`, and the research step is `not_available`.
- `ResearchProvider(inner, spec)` wraps the turn's provider (GK B: around the
  reservation wrapper, `Research(Reserving(base))`, so a reservation bounds
  the final request): it appends the server tools to every request of the
  turn, as provider-run tools (`type`, no input schema, a `max_uses`), and
  keeps every page fetched in this turn as a `FetchedSource`. A send the
  reservation refuses for the research cap sets `cap_reached`.
- Search results carry no readable page text, so only a fetched page can
  verify a quote (`services.source_ledger.verify`).
"""
from __future__ import annotations

import dataclasses
import hashlib
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterator

from harness.protocol import LLMEvent, LLMRequest, ProviderError

# Per provider call: how many searches and fetches the server may run. Bounds
# what one call can cost (search and fetch fees are per request).
SPEC = {"search_max_uses": 3, "fetch_max_uses": 3, "max_content_tokens": 20000}
RETAIN_CHARS = 200_000
# The reservation's refusals (SH-24, `SendRefused.reason`) and what each makes
# of the research step. Any other reason ends the turn as an error. None stops the run.
RESEARCH_BUDGET = "research_budget"  # the research cap: the step reads cap_reached
NOT_AVAILABLE = frozenset({"research_unavailable", "research_not_allowed"})

# Models with the dynamic-filtering variants; older ones keep the basic ones.
_CURRENT = ("claude-opus-5", "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6",
            "claude-sonnet-5", "claude-sonnet-4-6")
_SEARCH = {"current": "web_search_20260209", "basic": "web_search_20250305"}
_FETCH = {"current": "web_fetch_20260209", "basic": "web_fetch_20250910"}


def web_research(profile: Any) -> dict[str, Any] | None:
    """The research spec for this provider profile, or None when it has no
    web search (research then reads `not_available`)."""
    if profile is None or getattr(profile, "wire", None) != "anthropic":
        return None
    if getattr(profile, "base_url", None):  # a compatible endpoint, not the vendor's own
        return None
    return dict(SPEC)


def server_tools(model: str, spec: dict[str, Any]) -> list[dict[str, Any]]:
    """The wire's server tool entries for one research request."""
    tier = "current" if isinstance(model, str) and model.startswith(_CURRENT) else "basic"
    return [
        {"type": _SEARCH[tier], "name": "web_search", "max_uses": int(spec["search_max_uses"])},
        {"type": _FETCH[tier], "name": "web_fetch", "max_uses": int(spec["fetch_max_uses"]),
         "max_content_tokens": int(spec["max_content_tokens"])},
    ]


@dataclass(frozen=True)
class FetchedSource:
    """One page fetched in a research turn (#970 fold, item 1). `text` is the
    retained page text (NFC, capped); `sha256` is of the whole text read."""
    url: str
    fetched_at: str
    title: str
    sha256: str
    text: str
    truncated: bool

    def public(self) -> dict[str, Any]:
        out = dataclasses.asdict(self)
        out.pop("text")
        return out


def _page_text(document: Any) -> str | None:
    if not isinstance(document, dict):
        return None
    source = document.get("source")
    if not isinstance(source, dict) or source.get("type") != "text":
        return None  # a PDF or other binary source has no text to verify against
    data = source.get("data")
    return data if isinstance(data, str) else None


def fetched_sources(blocks: list[dict[str, Any]]) -> list[FetchedSource]:
    """Every successfully fetched text page in one response's blocks. An
    error result, a non-text page and a search result are not sources."""
    out = []
    for block in blocks or []:
        if not isinstance(block, dict) or block.get("type") != "web_fetch_tool_result":
            continue
        result = block.get("content")
        if not isinstance(result, dict) or result.get("type") != "web_fetch_result":
            continue
        url = result.get("url")
        document = result.get("content")
        text = _page_text(document)
        if not isinstance(url, str) or not url or text is None:
            continue
        text = unicodedata.normalize("NFC", text)
        out.append(FetchedSource(
            url=url,
            fetched_at=str(result.get("retrieved_at") or ""),
            title=str((document or {}).get("title") or ""),
            sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            text=text[:RETAIN_CHARS],
            truncated=len(text) > RETAIN_CHARS,
        ))
    return out


class ResearchProvider:
    """The turn's provider with web search and fetch switched on. Keeps this
    turn's fetched pages (`fetched`) and the usage of each call (`usage`)."""

    def __init__(self, inner: Any, spec: dict[str, Any]) -> None:
        self._inner = inner
        self._spec = dict(spec)
        self.name = getattr(inner, "name", "research")
        self.fetched: list[FetchedSource] = []
        self.usage: list[dict[str, int]] = []
        self.cap_reached = False
        self.refused: str | None = None  # the reservation's refusal reason, if any

    def stream(self, request: LLMRequest) -> Iterator[LLMEvent]:
        from harness import send_reservation
        request = dataclasses.replace(request, tools=list(request.tools) + server_tools(request.model, self._spec))
        try:
            # The reservation inside holds this send under scope "research" (SH-24).
            with send_reservation.research():
                for event in self._inner.stream(request):
                    if event.type == "message_done":
                        self.fetched.extend(fetched_sources(event.blocks))
                        self.usage.append(dict(event.usage or {}))
                    yield event
        except ProviderError as exc:
            reason = getattr(exc, "reason", None)
            if reason is not None:
                self.refused = reason
                self.cap_reached = reason == RESEARCH_BUDGET  # refused before sending: research stops
            raise

    def __getattr__(self, name: str) -> Any:  # probe and the like reach the inner provider
        return getattr(self._inner, name)
