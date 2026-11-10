"""`/api/memory/*`: the Memory page's API (assistant-memory plan §4, §5; §9 M2, M8).

Not a project route: memory spans projects and belongs to a user or an org.
Every route checks owner scope server-side in `services/memory_store.py`;
an item the caller may not read or change is a 404, never a 403, so ids
cannot be probed. Evidence labels are re-checked for project access on every
read (§1.4, §9 M1).
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session as DBSession

from db.models import User
from db.session import get_db
from deps import require_user
from services import memory_store as M

router = APIRouter()

ACTIONS = ("confirm", "dismiss", "retire", "restore", "nominate", "promote", "decline")


def _raise(exc: M.MemoryError):
    raise HTTPException(exc.status, {"error_kind": exc.kind, "message": str(exc)})


def _out(db: DBSession, user: User, v: M.ItemView) -> dict[str, Any]:
    return {
        "id": v.public_id,
        "kind": v.kind,
        "scope": v.owner_scope,
        "applies_to": v.project_kind,
        "concept_keys": list(v.concept_keys),
        "status": v.status,
        "origin": v.origin,
        "version": v.version,
        "summary": v.summary,
        "body": v.body,
        "evidence": M.evidence_for(db, user, v.evidence),
        "nomination": v.nomination if v.owner_scope == "user" else None,
        "promoted_from": M.public_id(v.promoted_from) if v.promoted_from else None,
        "recall_count": v.recall_count,
        "apply_count": v.apply_count,
        "last_used_at": v.last_used_at.isoformat() if v.last_used_at else None,
        "created_at": v.created_at.isoformat(),
        "updated_at": v.updated_at.isoformat(),
    }


class NewItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["preference", "chart_spec", "lesson"]
    body: dict[str, Any]
    concept_keys: list[str] = Field(min_length=1, max_length=M.MAX_KEYS)
    project_kind: (
        Literal["capacity_expansion", "planning_dynamics", "connection"] | None
    ) = None
    summary: str | None = Field(default=None, max_length=M.MAX_SUMMARY)
    evidence: list[dict[str, Any]] | None = Field(
        default=None, max_length=M.MAX_EVIDENCE
    )


class ItemEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: dict[str, Any] | None = None
    summary: str | None = Field(default=None, max_length=M.MAX_SUMMARY)
    concept_keys: list[str] | None = Field(
        default=None, min_length=1, max_length=M.MAX_KEYS
    )


class Promote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: dict[str, Any] | None = None
    summary: str | None = Field(default=None, max_length=M.MAX_SUMMARY)


@router.get("/items")
def list_items(
    kind: Literal["preference", "chart_spec", "parameter", "lesson"] | None = None,
    status: Literal[
        "proposed", "confirmed", "retired", "dismissed", "all"
    ] = "confirmed",
    scope: Literal["user", "org"] | None = None,
    nominated: bool = False,
    db: DBSession = Depends(get_db),
    user: User = Depends(require_user),
) -> dict:
    """The caller's own items and their org's; with `nominated`, an org
    admin's queue of members' suggestions (empty for anyone else)."""
    rows = M.list_items(
        db,
        user,
        kind=kind,
        status=None if status == "all" else status,
        scope=scope,
        nominated=nominated,
    )
    org = M.org_of(db, user)
    return {
        "items": [_out(db, user, v) for v in rows],
        "can_promote": org is not None and M.is_admin_of(db, user, org),
    }


@router.post("/items", status_code=201)
def create_item(
    payload: NewItem,
    db: DBSession = Depends(get_db),
    user: User = Depends(require_user),
) -> dict:
    """Save as default: the click is the confirmation (§2.1). A second save
    on the same owner, kind, concept keys and project kind is a new version."""
    try:
        v = M.create_item(
            db,
            user,
            kind=payload.kind,
            body=payload.body,
            concept_keys=payload.concept_keys,
            project_kind=payload.project_kind,
            summary=payload.summary,
            evidence=payload.evidence,
            status="confirmed",
            origin="button",
        )
    except M.MemoryError as exc:
        _raise(exc)
    return _out(db, user, v)


class Dismissal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat_id: str = Field(min_length=1, max_length=128)
    tool_use_id: str = Field(min_length=1, max_length=128)


def _denied_proposal(chat_id: str, tool_use_id: str, user: User) -> dict:
    """The `propose_memory` input of `tool_use_id` in the caller's own live
    chat. The client names the card; the server reads what it proposed, so
    the client never supplies the content. Anything else is a 404."""
    from harness.session import get_session, session_owner_allows

    session = get_session(chat_id)
    if session is None or not session_owner_allows(session, getattr(user, "id", None)):
        _raise(M.MemoryError("memory proposal not found", "memory_not_found", 404))
    messages = list(session.messages)
    # A card that ran (Remember) has a successful tool_result: it was not denied.
    for message in messages:
        content = message.get("content") if isinstance(message, dict) else None
        if message.get("role") == "user" and isinstance(content, list):
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_result"
                    and block.get("tool_use_id") == tool_use_id
                    and not block.get("is_error")
                ):
                    _raise(
                        M.MemoryError(
                            "this memory card was not declined", "memory_status", 409
                        )
                    )
    for message in messages:
        content = message.get("content") if isinstance(message, dict) else None
        if message.get("role") != "assistant" or not isinstance(content, list):
            continue
        for block in content:
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_use"
                and block.get("id") == tool_use_id
                and block.get("name") == "propose_memory"
                and isinstance(block.get("input"), dict)
            ):
                return block["input"]
    _raise(M.MemoryError("memory proposal not found", "memory_not_found", 404))


@router.post("/dismissals", status_code=201)
def dismiss_proposal(
    payload: Dismissal,
    response: Response,
    db: DBSession = Depends(get_db),
    user: User = Depends(require_user),
) -> dict:
    """Record "Not now" on a memory card (§2.2): the card's deny writes nothing,
    so the UI names the denied card here and the server reads its proposal from
    the caller's chat. It is stored dismissed in one commit (its body is dropped
    after 30 days by `sweep_dismissed`; its hash stays), so `propose_memory`
    does not offer the same proposal again for 30 days. A repeat is a 200 on
    the same row."""
    from services import memory_tools

    args = _denied_proposal(payload.chat_id, payload.tool_use_id, user)
    kind, body, keys, summary, pk = memory_tools._checked(args)
    evidence = [{"kind": "chat", "id": payload.chat_id, "label": "from this chat"}]
    try:
        v, created = M.dismiss_proposal(
            db,
            user,
            kind=kind,
            body=body,
            concept_keys=keys,
            project_kind=pk,
            summary=summary,
            evidence=evidence,
        )
    except M.MemoryError as exc:
        _raise(exc)
    if not created:
        response.status_code = 200
    return _out(db, user, v)


@router.patch("/items/{item_id}")
def edit_item(
    item_id: str,
    payload: ItemEdit,
    db: DBSession = Depends(get_db),
    user: User = Depends(require_user),
) -> dict:
    """A new version; the old ones stay readable."""
    try:
        v = M.edit_item(
            db,
            user,
            item_id,
            body=payload.body,
            summary=payload.summary,
            concept_keys=payload.concept_keys,
        )
    except M.MemoryError as exc:
        _raise(exc)
    return _out(db, user, v)


@router.post("/items/{item_id}/{action}")
def item_action(
    item_id: str,
    action: str,
    payload: Promote | None = None,
    db: DBSession = Depends(get_db),
    user: User = Depends(require_user),
) -> dict:
    """Confirm | dismiss | retire | restore (the status machine, §1.3);
    nominate (a member's "Suggest to org"); promote | decline (an admin of the
    item's own org, §5, §9 M2)."""
    if action not in ACTIONS:
        raise HTTPException(
            404, {"error_kind": "memory_not_found", "message": "unknown action"}
        )
    try:
        if action == "nominate":
            v = M.nominate(db, user, item_id)
        elif action == "promote":
            p = payload or Promote()
            v = M.promote(db, user, item_id, body=p.body, summary=p.summary)
        elif action == "decline":
            v = M.decline(db, user, item_id)
        else:
            v = M.transition(db, user, item_id, action)
    except M.MemoryError as exc:
        _raise(exc)
    return _out(db, user, v)


@router.get("/items/{item_id}/versions")
def item_versions(
    item_id: str, db: DBSession = Depends(get_db), user: User = Depends(require_user)
) -> dict:
    try:
        rows = M.versions(db, user, item_id)
    except M.MemoryError as exc:
        _raise(exc)
    for r in rows:
        r["evidence"] = M.evidence_for(db, user, r["evidence"])
    return {"id": M.public_id(M.parse_id(item_id)), "versions": rows}
