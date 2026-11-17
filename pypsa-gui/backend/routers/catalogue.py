"""
The Parameter catalogue routes (spec expert-catalogue §3.1).

Both are project-free: the body and the ETag never depend on a project, a
network or the caller. `GET /api/catalogue` answers `If-None-Match` with 304;
the ETag is the sha256 of the exact response bytes for that query (GK B C1).
`GET /api/catalogue/guide/{guide_id}` looks the id up in the catalogue; an id
it does not hold, however spelled, is the same 404 (C3). It is never a path.
"""

from __future__ import annotations

import hashlib

from fastapi import APIRouter, HTTPException, Request, Response

from services.asset_schema import catalogue as C

router = APIRouter()

# The same body as an unmatched route, so no spelling of an id tells a caller more than another.
_NOT_FOUND = "Not Found"


def _etag(body: bytes) -> str:
    return '"' + hashlib.sha256(body).hexdigest() + '"'


@router.get("")
def get_catalogue(request: Request, function: str | None = None, depth: str | None = None) -> Response:
    """Every asset type, group, function and parameter entry; `?function=` and `?depth=` narrow the entries."""
    try:
        body = C.payload_bytes(function=function, depth=depth)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    tag = _etag(body)
    headers = {"ETag": tag, "Cache-Control": "no-cache"}
    match = request.headers.get("if-none-match", "")
    if tag in {t.strip() for t in match.split(",")}:
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)


@router.get("/guide/{guide_id}")
def get_guide(guide_id: str) -> dict:
    """One guide body (markdown, rendered client-side with raw HTML off)."""
    body = C.guide_body(guide_id)
    if body is None:
        raise HTTPException(404, _NOT_FOUND)
    return {"id": guide_id, "markdown": body}


@router.get("/guide/{rest:path}", include_in_schema=False)
def get_guide_unmatched(rest: str) -> dict:
    """Any other shape under /guide/ (empty, `../x`, `a/b`): the same 404 as an unknown id, never the
    app's catch-all page (GK B C3)."""
    raise HTTPException(404, _NOT_FOUND)
