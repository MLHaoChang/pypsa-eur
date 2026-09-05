"""
OpenAI-compatible chat-completions provider over raw httpx.

Covers Ollama, LM Studio, vLLM, and the OpenAI/Moonshot/DashScope clouds —
they all speak this wire format. Exists in THIS plan primarily to prove the
seam is not secretly Anthropic-shaped (seam spec): tool-call framing differs
structurally, and there is no cache_control at all — `stable` is silently
dropped, which is exactly what the annotation design absorbs.

httpx is imported function-locally: it is a dev dependency until plan 2 adds
it to gui-requirements.txt, and this module must import cleanly in the
frozen app regardless.

History translation notes (request.messages arrive Anthropic-block-shaped):
  * assistant tool_use blocks   → assistant message with tool_calls[]
  * user tool_result blocks     → one {"role": "tool"} message each
  * thinking / redacted_thinking→ dropped (no wire equivalent; never replayed)
  * user image blocks (base64)  → translated to chat-completions
                                  `image_url` (a `data:` URL); the message
                                  `content` becomes a parts LIST whenever any
                                  non-text part survives translation, and
                                  stays a plain string otherwise (fix round 1,
                                  Task 8 review finding 1 — the original
                                  "dropped here" behaviour was the bug: a
                                  vision:true openai-wire profile could get
                                  an image attachment past the capability
                                  gate and then silently lose it in this
                                  function, so the model answered about a
                                  picture it never saw)
  * document/PDF blocks, and any image whose `source.type` isn't `base64`
                                  → still not translated (this adapter has no
                                  way to carry them); chat_service's
                                  capability gate refuses those turns before
                                  any provider call, so a well-formed request
                                  never reaches this function carrying one
"""
from __future__ import annotations

import json
import logging
from typing import Any, Iterator

from services import redaction
from services.llm_provider import LLMEvent, LLMRequest, ProviderError

logger = logging.getLogger("pypsa_gui.chat")

_REASONING_KEYS = ("reasoning_content", "reasoning")  # passive passthrough


def _to_openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"type": "function",
             "function": {"name": t["name"],
                          "description": t.get("description", ""),
                          "parameters": t.get("input_schema", {})}}
            for t in tools]


def _flatten_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content
                       if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _to_openai_content_parts(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Anthropic-shaped `text` / `image` content blocks -> chat-completions
    `content` parts list, order preserved.

    Fix round 1 (Task 8 review, finding 1): the ONLY non-text block this
    adapter knows how to carry is a base64-sourced `image`, translated to
    `{"type": "image_url", "image_url": {"url": "data:<media_type>;base64,
    <data>"}}` — the chat-completions vision shape. Anything else (a
    `document`/PDF block, or an image whose `source.type` isn't `base64` —
    a url source, an unrecognised shape) is skipped here, not translated.
    That is safe silence, not the bug this function used to have: by the
    time a request reaches this adapter, chat_service's capability gate
    (`_outbound_vision_block_kinds` + the `capability_unsupported` checks in
    `_run_turn_body`) has already refused any turn carrying one of those —
    a well-formed request never contains a block this function can't
    translate.
    """
    parts: list[dict[str, Any]] = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        btype = b.get("type")
        if btype == "text":
            text = b.get("text", "")
            if text:
                parts.append({"type": "text", "text": text})
        elif btype == "image":
            source = b.get("source")
            if isinstance(source, dict) and source.get("type") == "base64":
                media_type = source.get("media_type", "")
                data = source.get("data", "")
                parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{media_type};base64,{data}"},
                })
            # else: unsupported source shape — skipped defensively; the
            # capability gate is responsible for never letting this happen.
    return parts


def _to_openai_messages(request: LLMRequest) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{
        "role": "system",
        "content": "\n\n".join(b.get("text", "")
                               for b in request.system_blocks),
    }]
    for msg in request.messages:
        role, content = msg.get("role"), msg.get("content")
        if role == "assistant" and isinstance(content, list):
            tool_calls = [
                {"id": b.get("id", ""), "type": "function",
                 "function": {"name": b.get("name", ""),
                              "arguments": json.dumps(b.get("input", {}))}}
                for b in content
                if isinstance(b, dict) and b.get("type") == "tool_use"
            ]
            entry: dict[str, Any] = {"role": "assistant",
                                     "content": _flatten_text(content) or None}
            if tool_calls:
                entry["tool_calls"] = tool_calls
            out.append(entry)
        elif role == "user" and isinstance(content, list):
            results = [b for b in content if isinstance(b, dict)
                       and b.get("type") == "tool_result"]
            for b in results:
                out.append({"role": "tool",
                            "tool_call_id": b.get("tool_use_id", ""),
                            "content": _flatten_text(b.get("content", ""))
                            or json.dumps(b.get("content", ""))})
            # Everything else this turn carried — text plus any image /
            # document blocks — minus the tool_result blocks peeled off
            # above.
            remainder = [b for b in content if isinstance(b, dict)
                        and b.get("type") != "tool_result"]
            has_non_text = any(b.get("type") != "text" for b in remainder)
            if has_non_text:
                # Fix round 1 (Task 8 review, finding 1) — a non-text part
                # (e.g. an `image` block) survived; emit a chat-completions
                # multi-part `content` LIST so it actually reaches the wire
                # instead of being flattened away by _flatten_text. Pure-text
                # turns (the common case) do NOT take this path — see the
                # `else` below, which keeps the original plain-string shape
                # unchanged.
                parts = _to_openai_content_parts(remainder)
                if parts:
                    out.append({"role": "user", "content": parts})
            else:
                plain = _flatten_text(content)
                if plain:
                    # T6 — a turn that mixes plain text with tool_result
                    # blocks (e.g. the user typed something alongside a tool
                    # reply) must emit BOTH: the tool messages above, then
                    # this user message. The old `and not results` guard
                    # dropped the text entirely whenever tool_result blocks
                    # were also present.
                    out.append({"role": "user", "content": plain})
        else:
            out.append({"role": role or "user",
                        "content": _flatten_text(content)})
    return out


class _TokenParamRefused(Exception):
    """
    Internal: the endpoint refused the completion-length parameter BY NAME.

    Never escapes this module — `stream` catches it and retries once under
    the other spelling. Raised only from the pre-body status check, so no
    event has been yielded yet when it fires.
    """


class OpenAICompatProvider:
    name = "openai-compat"

    # C-2 — the two spellings of the completion-length parameter. EXACTLY ONE
    # is ever sent. The previous code sent both and a source comment claimed
    # that was safe; it is not. OpenAI's refusal is PRESENCE-based — the error
    # is `unsupported_parameter` naming `max_tokens` itself, because the
    # model's supported-parameter set does not contain it — so adding the new
    # name alongside does not help while the old one is still in the body.
    _TOKEN_PARAM_LEGACY = "max_tokens"
    _TOKEN_PARAM_COMPLETION = "max_completion_tokens"

    def __init__(self, base_url: str, api_key: str | None = None,
                 http_client: Any | None = None,
                 token_param: str = "max_tokens") -> None:
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._http = http_client  # tests inject MockTransport clients
        # Server-derived from the profile's preset (`llm_config
        # .derive_token_param`), never client-set. `max_tokens` is the default
        # because it is what every local and OpenAI-compatible server accepts;
        # only endpoints declared otherwise get the newer name.
        self._token_param = (
            token_param if token_param in
            (self._TOKEN_PARAM_LEGACY, self._TOKEN_PARAM_COMPLETION)
            else self._TOKEN_PARAM_LEGACY
        )

    def _other_token_param(self, used: str) -> str:
        return (
            self._TOKEN_PARAM_COMPLETION
            if used == self._TOKEN_PARAM_LEGACY
            else self._TOKEN_PARAM_LEGACY
        )

    @staticmethod
    def _refused_the_token_param(body: bytes, param: str) -> bool:
        """
        True when a 400 body is specifically "that token parameter is not
        supported here" — the ONE case worth retrying under the other name.

        Deliberately narrow. A generic 400 (bad model, malformed request)
        must surface immediately: silently re-sending it would double every
        failed request and hide the real cause. Matched on the parameter name
        appearing together with an unsupported-parameter signal, so it works
        against vendors that word the message differently but keep the shape.
        """
        try:
            text = body.decode("utf-8", "replace").lower()
        except Exception:  # noqa: BLE001 — a body we cannot read is not a match
            return False
        if param.lower() not in text:
            return False
        return any(
            marker in text for marker in
            ("unsupported_parameter", "unsupported parameter",
             "unrecognized_keys", "unknown parameter", "not supported")
        )

    def _client(self):
        import httpx  # function-local: dev-only dep until plan 2 ships it
        return self._http or httpx.Client(timeout=httpx.Timeout(120.0,
                                                                connect=10.0))

    def _stream_payload(self, request: LLMRequest, token_param: str) -> dict:
        payload: dict[str, Any] = {
            "model": request.model,
            # C-2 — EXACTLY ONE completion-length parameter. See the class
            # constants for why sending both is not a compatible superset.
            token_param: request.max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
            "messages": _to_openai_messages(request),
        }
        if request.tools:
            payload["tools"] = _to_openai_tools(request.tools)
        return payload

    def stream(self, request: LLMRequest) -> Iterator[LLMEvent]:
        """
        One turn on the OpenAI wire, with a single adaptive retry (C-2).

        `_stream_once` raises `_TokenParamRefused` ONLY from the pre-body
        status check, before it has yielded anything — so retrying it cannot
        replay events a consumer has already seen. Any other failure, and any
        failure after the first yield, propagates untouched.
        """
        token_param = self._token_param
        try:
            yield from self._stream_once(request, token_param)
        except _TokenParamRefused:
            try:
                yield from self._stream_once(
                    request, self._other_token_param(token_param))
            except _TokenParamRefused as exc:
                # F3 — an endpoint that refuses BOTH spellings must not let a
                # private exception reach `chat_service`, where the broad
                # `except Exception` renders it as `internal_error`. One retry
                # is the whole budget: two sends, then a typed error.
                raise ProviderError(
                    "invalid_request",
                    "endpoint rejected both completion-length parameters",
                ) from exc

    def _stream_once(
        self, request: LLMRequest, token_param: str
    ) -> Iterator[LLMEvent]:
        import httpx
        payload = self._stream_payload(request, token_param)
        headers = {"content-type": "application/json"}
        if self._key:
            headers["authorization"] = f"Bearer {self._key}"

        text_parts: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        # M6 fix round 1/2 — `started_indices`: at most one tool_use_start
        # per index (finding 3). `seen_ids`: every id assigned so far, real
        # or synthetic, so a NEW id never collides with one already handed
        # out (finding 2). Synthetic ids are assigned only after the whole
        # stream is read (see the finalisation loop below, fix round 2) —
        # that is what makes the collision defence symmetric: a real id
        # cannot "steal" an id already given to a synthetic tool_use_start,
        # because no synthetic id — and no synthetic tool_use_start — exists
        # yet while deltas are still arriving.
        started_indices: set[int] = set()
        seen_ids: set[str] = set()
        usage: dict[str, int] = {}
        client = self._client()
        try:
            with client.stream("POST", f"{self._base}/chat/completions",
                               json=payload, headers=headers) as resp:
                if resp.status_code == 400:
                    # C-2 — the endpoint may want the OTHER spelling. Only a
                    # refusal that NAMES this parameter is retried, and only
                    # once; anything else surfaces unchanged. `custom`
                    # profiles have no preset declaration to read, so this is
                    # what keeps one aimed at OpenAI from being dead on
                    # arrival.
                    # F5 — bounded. `resp.read()` has no size cap, so a
                    # hostile endpoint could answer 400 with a multi-GB body;
                    # the marker match only ever needs the first few KB.
                    body = b""
                    for chunk in resp.iter_bytes():
                        body += chunk
                        if len(body) >= 8192:
                            break
                    if self._refused_the_token_param(body, token_param):
                        raise _TokenParamRefused(token_param)
                    raise ProviderError(
                        _kind_for_status(resp.status_code),
                        f"HTTP {resp.status_code} from chat/completions")
                if resp.status_code != 200:
                    raise ProviderError(
                        _kind_for_status(resp.status_code),
                        f"HTTP {resp.status_code} from chat/completions")
                for line in resp.iter_lines():
                    if not line.startswith("data:"):
                        yield LLMEvent(type="ping")
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except ValueError:
                        yield LLMEvent(type="ping")
                        continue
                    if isinstance(chunk.get("usage"), dict):
                        u = chunk["usage"]
                        usage = {
                            "input_tokens": int(u.get("prompt_tokens", 0) or 0),
                            "output_tokens": int(
                                u.get("completion_tokens", 0) or 0),
                        }
                    for choice in chunk.get("choices", []):
                        delta = choice.get("delta") or {}
                        for rk in _REASONING_KEYS:
                            if delta.get(rk):
                                yield LLMEvent(type="thinking_delta",
                                               text=str(delta[rk]))
                        if delta.get("content"):
                            text_parts.append(delta["content"])
                            yield LLMEvent(type="text_delta",
                                           text=delta["content"])
                        for tc in delta.get("tool_calls") or []:
                            idx = int(tc.get("index", 0))
                            slot = calls.setdefault(
                                idx, {"id": "", "name": "", "args": ""})
                            # Capture name/arguments BEFORE deciding whether
                            # to fire a start, so an id arriving on a later
                            # delta than the name still gets the right
                            # `tool_name` in its tool_use_start (fix round 2).
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                slot["name"] = fn["name"]
                            if fn.get("arguments"):
                                slot["args"] += fn["arguments"]
                            real_id = tc.get("id")
                            if real_id:
                                slot["id"] = real_id
                                seen_ids.add(real_id)
                                if idx not in started_indices:
                                    started_indices.add(idx)
                                    yield LLMEvent(
                                        type="tool_use_start",
                                        tool_use_id=real_id,
                                        tool_name=slot["name"])
                                # else: a second real id for an
                                # already-started index — extremely unlikely,
                                # but `slot["id"]` above still adopts the
                                # latest one without a second start (finding
                                # 3's "at most one start per index").
                            # An id-less delta for an index that hasn't
                            # started does NOT synthesize or start here
                            # anymore (fix round 2) — see the finalisation
                            # loop below for why.
        except ProviderError:
            raise
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise ProviderError(
                "unreachable",
                redaction.redact_secrets_in_str(str(exc))) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                "upstream_error",
                redaction.redact_secrets_in_str(str(exc))) from exc
        finally:
            if self._http is None:
                client.close()

        blocks: list[dict[str, Any]] = []
        if text_parts:
            blocks.append({"type": "text", "text": "".join(text_parts)})
        for idx in sorted(calls):
            slot = calls[idx]
            if not slot["id"]:
                # M6 fix round 2 — deferred synthesis. An index that reaches
                # end-of-stream having NEVER received a real id gets its
                # synthetic id (and its single tool_use_start) assigned HERE,
                # only now that `seen_ids` holds every real id the whole
                # stream ever produced. This is what closes the asymmetry a
                # round-1 review found: previously a synthetic id was handed
                # out — and its tool_use_start already emitted — the moment
                # the first id-less delta arrived, so a real id minted LATER
                # on a different index that happened to equal it could still
                # collide (the `seen_ids` check only guarded against ids
                # seen so far, and there is no event that can retroactively
                # correct an already-emitted tool_use_start's id). Assigning
                # synthetic ids only after the stream is fully drained means
                # no synthetic id — and no synthetic tool_use_start — ever
                # exists for a real id to collide with while deltas are
                # still arriving; the two are collision-checked against the
                # SAME final `seen_ids`, so the defence is symmetric.
                candidate = f"__synth_{idx}"
                bump = 0
                while candidate in seen_ids:
                    bump += 1
                    candidate = f"__synth_{idx}_{bump}"
                slot["id"] = candidate
                seen_ids.add(candidate)
                yield LLMEvent(type="tool_use_start", tool_use_id=candidate,
                               tool_name=slot["name"])
            try:
                args = json.loads(slot["args"]) if slot["args"] else {}
            except ValueError:
                args = {"_raw_arguments": slot["args"]}
            blocks.append({"type": "tool_use", "id": slot["id"],
                           "name": slot["name"], "input": args})
        yield LLMEvent(type="message_done", blocks=blocks, usage=usage)

    def probe(self, model: str) -> tuple[str, float | None]:
        """
        `(verdict, latency_ms)` for the Task 9 connection test — a real
        `max_tokens=1` NON-streaming completion, deliberately NOT routed
        through `stream()`.

        The verdict vocabulary here is FIXED and different on purpose from
        `_kind_for_status` (which `stream()` uses): that mapping collapses
        every non-401/429 4xx into `invalid_request` because a mid-turn
        retry loop only needs to know "deterministic failure, don't retry",
        never WHICH 4xx it was. A connection test's entire job is telling
        the operator which thing is wrong, so a 404 here gets its own
        `model_not_found` verdict — actionable ("this model name doesn't
        exist on that endpoint") in a way `invalid_request` is not.

        SECURITY (non-negotiable, matches `routers/local_settings.py`'s own
        probe): the return value is two fixed strings and a float — no
        upstream exception text, no base_url, no host, ever reaches it. A
        network failure of any kind (connect refused, DNS, timeout, TLS)
        collapses to `unreachable`; only the exception's CLASS NAME is
        logged, which cannot contain a credential or a hostname fragment
        the way `str(exc)` can.
        """
        import time
        import httpx
        # C-2 — one token parameter, same rule as `stream`. The Test
        # connection button must not 400 for a reason a real turn would not.
        payload: dict[str, Any] = {
            "model": model,
            self._token_param: 1,
            "stream": False,
            "messages": [{"role": "user", "content": "ping"}],
        }
        headers = {"content-type": "application/json"}
        if self._key:
            headers["authorization"] = f"Bearer {self._key}"
        client = self._client()
        start = time.monotonic()
        try:
            resp = client.post(f"{self._base}/chat/completions",
                               json=payload, headers=headers)
        except httpx.HTTPError as exc:
            logger.warning(
                "chat: connection test could not reach the endpoint (%s)",
                type(exc).__name__,
            )
            return "unreachable", None
        finally:
            if self._http is None:
                client.close()
        elapsed_ms = (time.monotonic() - start) * 1000.0
        if resp.status_code == 200:
            return "ok", elapsed_ms
        if resp.status_code == 401:
            return "unauthorized", None
        if resp.status_code == 404:
            return "model_not_found", None
        if resp.status_code == 400 and self._refused_the_token_param(
            resp.content, self._token_param
        ):
            # C-2 — same adaptive retry as `stream`, so a `custom` profile
            # aimed at OpenAI does not report a bogus `invalid_request` from
            # the one button an operator uses to check their configuration.
            payload[self._other_token_param(self._token_param)] = payload.pop(
                self._token_param
            )
            try:
                retry = client.post(f"{self._base}/chat/completions",
                                    json=payload, headers=headers)
            except httpx.HTTPError:
                return "unreachable", None
            if retry.status_code == 200:
                return "ok", (time.monotonic() - start) * 1000.0
            if retry.status_code == 401:
                return "unauthorized", None
            if retry.status_code == 404:
                return "model_not_found", None
        return "invalid_request", None

    def probe_models(self) -> list[str] | None:
        """
        Best-effort `GET {base}/models` -> sorted model ids, or `None` on
        ANY failure — network error, non-200, unparseable body, or a body
        that isn't the expected `{"data": [{"id": ...}, ...]}` shape. This
        is cosmetic information displayed ALONGSIDE the verdict, never a
        second source of truth for it, so it must never raise: a vendor
        that serves chat/completions but not /models (Ollama predates it on
        some versions; a lot of proxies never add it) is not a connection
        failure.
        """
        import httpx
        headers: dict[str, str] = {}
        if self._key:
            headers["authorization"] = f"Bearer {self._key}"
        client = self._client()
        try:
            resp = client.get(f"{self._base}/models", headers=headers)
            if resp.status_code != 200:
                return None
            data = resp.json()
            items = data.get("data") if isinstance(data, dict) else None
            if not isinstance(items, list):
                return None
            ids = sorted({
                item["id"] for item in items
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            })
            return ids or None
        except Exception:  # noqa: BLE001 — best-effort, must never raise
            return None
        finally:
            if self._http is None:
                client.close()


def _kind_for_status(status: int) -> str:
    if status == 401:
        return "unauthorized"
    if status == 429:
        return "rate_limited"
    if 400 <= status < 500:
        # whole-range on purpose — same rationale as map_sdk_exception:
        # a deterministic 4xx retried is guaranteed waste.
        return "invalid_request"
    return "upstream_error"
