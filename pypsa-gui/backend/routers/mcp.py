"""
`/api/mcp`: local MCP exposure (chat harness issue 11; `harness/mcp_server.py`
holds the protocol). Streamable HTTP with JSON responses; no server-initiated
stream (GET answers 405).

Refused unless local mode is on (404, as if absent), without the per-launch
bearer token (401), or from a foreign `Origin` (403: a browser page on
another site, DNS rebinding). The identity binding mirrors `chat_stream`:
bound here on the event loop, so the threadpool copies that run the call
inherit it.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

import local_mode
import security
from harness import mcp_server

router = APIRouter()

MAX_BODY_BYTES = 1_000_000


def _guard(request: Request) -> str | None:
    """The caller's user id, after the local-mode, token and origin checks."""
    if not local_mode.is_local_mode():
        raise HTTPException(status_code=404, detail="Not Found")
    if not mcp_server.token_matches(request.headers.get("Authorization")):
        raise HTTPException(status_code=401, detail="a valid MCP bearer token is required")
    origin = request.headers.get("Origin")
    if origin and origin not in security.allowed_origins():
        raise HTTPException(status_code=403, detail="requests from this origin are refused")
    user = getattr(request.state, "auth_user", None)
    return str(user.id) if getattr(user, "id", None) is not None else None


def _bind(user_id: str | None) -> None:
    """The acting identity and the project cell, as `chat_stream` binds them."""
    from services import chat_tools
    from services.pypsa_service import PyPSAService
    chat_tools.set_acting_user(user_id)
    chat_tools.set_acting_session(None)
    PyPSAService.bind_turn_cell()


@router.post("/mcp")
async def mcp_post(request: Request) -> Response:
    user_id = _guard(request)
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="request too large")
    try:
        message: Any = json.loads(raw)
    except ValueError:
        return JSONResponse(mcp_server._error(None, -32700, "parse error"), status_code=400)
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or "method" not in message:
        return JSONResponse(mcp_server._error(None, -32600, "invalid request"), status_code=400)
    if "id" not in message:                                   # a notification
        return Response(status_code=202)
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    if message["method"] == "initialize":
        client = params.get("clientInfo") if isinstance(params.get("clientInfo"), dict) else {}
        session = mcp_server.open_session(user_id, client.get("name"))
        return JSONResponse(mcp_server.initialize(message["id"], params),
                            headers={"Mcp-Session-Id": session.session_id})
    sid = request.headers.get("Mcp-Session-Id")
    if not sid:
        return JSONResponse(mcp_server._error(message["id"], -32600, "Mcp-Session-Id header required"),
                            status_code=400)
    session = mcp_server.find_session(sid, user_id)
    if session is None:
        return JSONResponse(mcp_server._error(message["id"], -32001, "session not found"),
                            status_code=404)
    if message["method"] == "tools/call":
        from services import chat_service
        allowed, retry_after = chat_service.check_rate_limit(sid)
        if not allowed:
            raise HTTPException(status_code=429, detail={"error_kind": "rate_limited_local",
                                                         "message": "too many MCP calls"},
                                headers={"Retry-After": str(int(retry_after) + 1)})
    _bind(user_id)
    toolset = request.query_params.get("toolset")
    reply = await run_in_threadpool(mcp_server.handle, session, message, toolset=toolset)
    return JSONResponse(reply)


@router.delete("/mcp")
async def mcp_delete(request: Request) -> Response:
    user_id = _guard(request)
    session = mcp_server.find_session(request.headers.get("Mcp-Session-Id"), user_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    await run_in_threadpool(mcp_server.close_session, session)
    return Response(status_code=204)


@router.get("/mcp")
async def mcp_get(request: Request) -> Response:
    _guard(request)
    return Response(status_code=405, headers={"Allow": "POST, DELETE"})
