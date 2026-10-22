"""
Values from an uploaded PDF datasheet onto assets, each beside its page and quote (data-
to-model slice 2): the dialog's preview, and the apply both the dialog and the assistant use.

As `routers/network_equipment_table.py`: `apply` is under `/api/network`, so the middleware
takes ONE undo snapshot before the handler, the foreign-lock gate applies and results are
invalidated like every other network edit. It goes through the edit card (tool
`apply_document_values`): the card shows each changed value old → new with its page and
quote. The handler rebuilds the card, refuses a digest other than the one the user
approved (`edit_preview_stale`) or changed bytes (`source_changed`), then slice 1's
`equipment_table.apply` writes exactly the card's values under the mutation lock and the
receipt reads them back.

`preview` (`preview_router`, mounted at `/api/document_values`) is a read outside
`/api/network` and `/api/io`: no undo snapshot, no edit-counter bump, no results cleared.
See `services/document_values.py`.

The import tariff and PPA contracts (PR-B, `services/document_values_commercial.py`):
`commercial_router`, mounted at `/api/simulation/document_values`, so the foreign-lock gate
applies and no network undo snapshot is taken (the solver config is not on that stack; the
receipt says how to revert). Its preview is `POST /api/document_values/commercial/preview`.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from db.models import User
from deps import require_user

router = APIRouter()

TOOL = "apply_document_values"


class Citation(BaseModel):
    names: list[str] = Field(min_length=1)
    field: str = Field(min_length=1)
    page: int
    quote: str = Field(min_length=1)
    unit: str | None = None
    value_text: str | None = None


class DocumentValuesIn(BaseModel):
    upload_id: str = Field(min_length=1)
    component_class: str = Field(min_length=1)
    citations: list[Citation] = Field(min_length=1)
    decimal_mark: Literal[".", ","] | None = None
    currency: str | None = None
    money_year: int | None = None
    confirm: list[str] = Field(default_factory=list)
    source_sha256: str = Field(min_length=64, max_length=64)


class DocumentValuesHttp(DocumentValuesIn):
    # The card the user approved: the dialog's from its preview, the assistant's from
    # the confirmation its turn loop showed. Any other refuses (edit_preview_stale).
    digest: str = Field(min_length=1)


class DocumentPreviewIn(BaseModel):
    """A document to read, and optionally citations to check. Without `component_class`
    the preview is the document alone; without `citations`, it and the class's fields."""

    upload_id: str = Field(min_length=1)
    component_class: str | None = None
    citations: list[Citation] = Field(default_factory=list)
    decimal_mark: Literal[".", ","] | None = None
    currency: str | None = None
    money_year: int | None = None
    confirm: list[str] = Field(default_factory=list)
    # The dialog's quote picker: list candidate lines for each of the class's fields.
    find: bool = False


def args_of(body: DocumentPreviewIn | DocumentValuesIn) -> dict[str, Any]:
    return body.model_dump(exclude={"digest"})


preview_router = APIRouter()


@preview_router.post("/preview")
def preview_document_values_http(body: DocumentPreviewIn, user: User = Depends(require_user)) -> dict:
    """The dialog's read: the document, the class's fields, the plan and the card
    (`document_values.preview`). Writes nothing."""
    from services import document_values as D

    return D.preview(args_of(body))


@router.post("/document_values/apply")
def apply_document_values(body: DocumentValuesHttp, user: User = Depends(require_user)) -> dict:
    """The apply: the card the user approved must still be current."""
    from harness import edit_card
    from services import document_values as D
    from services import equipment_table as T

    args = args_of(body)
    card = edit_card.preview(TOOL, args, with_state=False)
    if body.digest != card["digest"]:
        T._refuse("edit_preview_stale", "The values changed since the card was shown; nothing was applied. "
                  "Propose the edit again.", 409)
    p = D.plan_active(args)
    T.require_confirmed(p)
    out = T.apply(p, card, approved_by=getattr(user, "email", None))
    return {**out, "receipt": edit_card.receipt(card, args)}


# ── The import tariff and PPA contracts (section 7) ────────────────────────

COMMERCIAL_TOOL = "apply_commercial_document_values"


class CommercialCitation(BaseModel):
    target: Literal["tariff", "ppa"]
    entity: str = Field(min_length=1)
    field: str = Field(min_length=1)
    page: int
    quote: str = Field(min_length=1)
    unit: str | None = None
    value_text: str | None = None


class CommercialPreviewIn(BaseModel):
    upload_id: str = Field(min_length=1)
    citations: list[CommercialCitation] = Field(default_factory=list)
    decimal_mark: Literal[".", ","] | None = None
    currency: str | None = None
    money_year: int | None = None
    confirm: list[str] = Field(default_factory=list)


class CommercialValuesIn(CommercialPreviewIn):
    citations: list[CommercialCitation] = Field(min_length=1)
    source_sha256: str = Field(min_length=64, max_length=64)


class CommercialValuesHttp(CommercialValuesIn):
    digest: str = Field(min_length=1)


class CommercialPreviewHttp(CommercialPreviewIn):
    # The dialog's quote picker: list candidate lines for each tariff item and PPA field.
    find: bool = False


@preview_router.post("/commercial/preview")
def preview_commercial_document_values_http(body: CommercialPreviewHttp, user: User = Depends(require_user)) -> dict:
    """The dialog's read in commercial mode: the document, and with citations the plan and
    the card (`document_values_commercial.preview`). Writes nothing."""
    from services import document_values_commercial as DC

    return DC.preview(body.model_dump())


commercial_router = APIRouter()


@commercial_router.post("/apply")
def apply_commercial_document_values(body: CommercialValuesHttp, user: User = Depends(require_user)) -> dict:
    """The apply: the card the user approved must still be current, and the stored
    commercial config still the one it was read from (compare-and-set)."""
    from harness import edit_card
    from services import chat_tools
    from services import document_values_commercial as DC
    from services import equipment_table as T

    args = body.model_dump(exclude={"digest"})
    card = edit_card.preview(COMMERCIAL_TOOL, args, with_state=False)
    if body.digest != card["digest"]:
        T._refuse("edit_preview_stale", "The values changed since the card was shown; nothing was applied. "
                  "Propose the edit again.", 409)
    p = DC.plan_active(args)
    T.require_confirmed(p)
    # The save goes through the solver-config handler as the signed-in user: outside a
    # chat turn nothing else has bound one (`no_acting_user`).
    previous = chat_tools.acting_user_id()
    chat_tools.set_acting_user(str(user.id))
    try:
        out = DC.apply(p, card, approved_by=getattr(user, "email", None))
    finally:
        chat_tools.set_acting_user(previous)
    return {**out, "receipt": edit_card.receipt(card, args)}
