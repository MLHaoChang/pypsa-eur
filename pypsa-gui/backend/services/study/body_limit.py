r"""
A request-body size limit on the decision-study routes, enforced before the
body is parsed as JSON (F1 B2, gate S8 [S-v2-1]).

The draft load travels in the intake as ``load.csv_text``, capped at
``packs.MAX_LOAD_BYTES`` (25 MB). That cap is checked by the handler, so
FastAPI had already read and parsed the whole body: an 80 MB body cost about
200 MB of RSS before the refusal. This ASGI middleware refuses a body over
``MAX_STUDY_BODY_BYTES`` with 413 and ``error_kind`` ``study_request_too_large``:

* from ``Content-Length`` when the client sends one, before reading a byte;
* otherwise (or when the declared length is wrong) by counting the streamed
  bytes, buffering at most the limit.

Only ``/api/projects/{name}/studies`` paths are limited (the same anchored
pattern as ``main._SOLVER_BLOCKING_EXEMPT_PATTERNS``); every other route
keeps its own limits.

The headroom: JSON escaping at most doubles a CSV's bytes (``\n``, ``\"``
and ``\\`` take two each, and a browser's ``JSON.stringify`` leaves
non-ASCII text alone), so twice the cap plus 1 MB for the rest of the intake
admits every file the handler's cap admits. A body just over the cap still
reaches the handler and gets its typed ``load_upload_invalid``.
"""
from __future__ import annotations

import json
import re

from services.study.packs import MAX_LOAD_BYTES

MAX_STUDY_BODY_BYTES = 2 * MAX_LOAD_BYTES + 1024 * 1024

ERROR_KIND = "study_request_too_large"

_STUDY_PATH = re.compile(r"^/api/projects/[^/]+/studies(/|$)")


def _declared_length(scope) -> int | None:
    for key, value in scope.get("headers") or ():
        if key.lower() == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


async def _refuse(send, limit: int) -> None:
    body = json.dumps({"detail": {
        "error_kind": "study_request_too_large",
        "message": (
            f"The request body is larger than {limit // (1024 * 1024)} MB, the "
            f"most a study request may carry (a load file is capped at "
            f"{MAX_LOAD_BYTES // (1024 * 1024)} MB). Use a smaller file."
        ),
    }}).encode()
    await send({"type": "http.response.start", "status": 413, "headers": [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
        (b"connection", b"close"),
    ]})
    await send({"type": "http.response.body", "body": body})


class StudyBodyLimitMiddleware:
    """Pure ASGI, so the refusal happens before Starlette builds a Request."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not _STUDY_PATH.match(scope.get("path", "")):
            await self.app(scope, receive, send)
            return
        limit = MAX_STUDY_BODY_BYTES
        declared = _declared_length(scope)
        if declared is not None and declared > limit:
            await _refuse(send, limit)
            return
        chunks: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                # A disconnect: hand it to the app as it would have seen it.
                pending = [message]
                break
            body = message.get("body", b"")
            total += len(body)
            if total > limit:
                await _refuse(send, limit)
                return
            chunks.append(body)
            if not message.get("more_body", False):
                pending = [{"type": "http.request", "body": b"".join(chunks),
                            "more_body": False}]
                break

        async def replay():
            if pending:
                return pending.pop(0)
            return await receive()

        await self.app(scope, replay, send)
