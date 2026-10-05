"""
F1 B2 (gate S8 [S-v2-1]): the study routes refuse an oversized request body
BEFORE it is parsed as JSON.

The 25 MB ``csv_text`` cap (``packs.MAX_LOAD_BYTES``) used to be checked
only after FastAPI had parsed the whole body; an 80 MB body cost about
200 MB of RSS before the refusal. ``services.study.body_limit`` refuses it
413 with a typed ``error_kind`` from ``Content-Length`` and, when there is
none, by counting the streamed bytes. Only ``/api/projects/{name}/studies``
routes are limited.

The limit is patched small here so the tests do not move 50 MB around; the
real limit's headroom is asserted against the cap separately, and the
existing ``test_a_draft_load_larger_than_an_upload_is_refused`` (a body just
above 25 MB, refused 422 by the handler) proves it end to end.
"""
from __future__ import annotations

import json

import pytest

from services.study import body_limit
from services.study import packs
from tests.study_s4_support import enable_studies

_KIND = "study_request_too_large"


@pytest.fixture
def studies_on(monkeypatch):
    yield from enable_studies(monkeypatch)


@pytest.fixture
def small_limit(monkeypatch):
    monkeypatch.setattr(body_limit, "MAX_STUDY_BODY_BYTES", 4096)
    return 4096


def _big_body(n: int) -> bytes:
    return json.dumps({"intake": {"load": {"source": "upload", "csv_text": "x" * n}}}).encode()


def test_the_limit_leaves_headroom_above_the_csv_text_cap():
    # JSON escaping can at most double a CSV's plain-text bytes (`\n`, `\"`,
    # `\\` each take two), so a file that passes the cap always fits.
    assert body_limit.MAX_STUDY_BODY_BYTES >= 2 * packs.MAX_LOAD_BYTES + 1024 * 1024
    assert body_limit.MAX_STUDY_BODY_BYTES < 80 * 1024 * 1024


def test_a_body_over_the_limit_by_content_length_is_refused_413_typed(
        client, api_project, studies_on, small_limit):
    name = api_project("f1-b2-cl")
    body = _big_body(small_limit + 10)
    r = client.post(f"/api/projects/{name}/studies/preview", content=body,
                    headers={"content-type": "application/json"})
    assert r.status_code == 413, r.text
    assert r.json()["detail"]["error_kind"] == _KIND


def test_the_refusal_comes_before_json_parsing(client, api_project, studies_on, small_limit):
    # Not JSON at all: a parser that ran first would answer 422.
    name = api_project("f1-b2-notjson")
    r = client.post(f"/api/projects/{name}/studies/preview", content=b"{" * (small_limit + 1),
                    headers={"content-type": "application/json"})
    assert r.status_code == 413, r.text
    assert r.json()["detail"]["error_kind"] == _KIND


def test_a_streamed_body_without_content_length_is_counted(
        client, api_project, studies_on, small_limit):
    name = api_project("f1-b2-stream")
    body = _big_body(small_limit * 3)

    def chunks():
        for i in range(0, len(body), 1000):
            yield body[i:i + 1000]

    r = client.post(f"/api/projects/{name}/studies/preview", content=chunks(),
                    headers={"content-type": "application/json"})
    assert r.status_code == 413, r.text
    assert r.json()["detail"]["error_kind"] == _KIND


def test_a_lying_content_length_is_still_counted(studies_on, small_limit):
    """A declared length under the limit does not let a longer stream through."""
    import asyncio

    seen: list[bytes] = []

    async def app(scope, receive, send):  # pragma: no cover - must not run
        seen.append((await receive())["body"])

    mw = body_limit.StudyBodyLimitMiddleware(app)
    body = b"x" * (small_limit * 2)
    msgs = [{"type": "http.request", "body": body[:small_limit], "more_body": True},
            {"type": "http.request", "body": body[small_limit:], "more_body": False}]
    sent: list[dict] = []

    async def receive():
        return msgs.pop(0)

    async def send(msg):
        sent.append(msg)

    scope = {"type": "http", "method": "POST", "path": "/api/projects/p/studies/preview",
             "headers": [(b"content-length", b"10")]}
    asyncio.run(mw(scope, receive, send))
    assert sent[0]["status"] == 413 and not seen


def test_a_declared_length_over_the_limit_is_refused_before_reading(studies_on, small_limit):
    """An 80 MB body announced by Content-Length is never read at all."""
    import asyncio

    reads: list[int] = []

    async def app(scope, receive, send):  # pragma: no cover - must not run
        raise AssertionError("the app ran")

    async def receive():
        reads.append(1)
        return {"type": "http.request", "body": b"", "more_body": False}

    sent: list[dict] = []

    async def send(msg):
        sent.append(msg)

    scope = {"type": "http", "method": "POST", "path": "/api/projects/p/studies/preview",
             "headers": [(b"content-length", str(80 * 1024 * 1024).encode())]}
    asyncio.run(body_limit.StudyBodyLimitMiddleware(app)(scope, receive, send))
    assert sent[0]["status"] == 413 and reads == []
    assert json.loads(sent[1]["body"])["detail"]["error_kind"] == _KIND


def test_a_body_under_the_limit_reaches_the_handler(client, api_project, studies_on, small_limit):
    name = api_project("f1-b2-small")
    r = client.post(f"/api/projects/{name}/studies/preview",
                    json={"intake": {"load": {"source": "upload", "csv_text": "x" * 100}}})
    assert r.status_code != 413, r.text


def test_other_routes_are_not_limited(client, api_project, studies_on, small_limit):
    name = api_project("f1-b2-other")
    body = _big_body(small_limit + 10)
    for path in (f"/api/projects/{name}/studiesX", f"/api/projects/{name}/uploads"):
        r = client.post(path, content=body, headers={"content-type": "application/json"})
        assert r.status_code != 413, (path, r.status_code, r.text)
