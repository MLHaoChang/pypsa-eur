"""
Apply one shipped (`common`) cost reference to one asset of the active network.

Under `/api/network`, so the write takes the undo snapshot and invalidates results
like every other network edit. Both routes go through the edit card
(`harness/edit_card.py`, tool `apply_library_reference`): `preview` returns the
old → new card with the reference and the pack value beside each row; `apply`
re-previews, refuses a card whose digest moved (`edit_preview_stale`), writes,
and returns the card's receipt. The assistant's `apply_library_reference` runs
the same handler after its own confirmation. Nothing is written to a pack or a
Library item. See `services/library/reference_apply.py`.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from db.models import User
from deps import require_user

router = APIRouter()

TOOL = "apply_library_reference"


class ReferenceApplyIn(BaseModel):
    component_class: str
    name: str = Field(min_length=1)
    technology: str = Field(min_length=1)
    projection_year: int
    confirm: list[str] = Field(default_factory=list)


class ReferenceApplyRun(ReferenceApplyIn):
    # The card the user approved; a moved digest refuses (edit_preview_stale).
    digest: str | None = None


def _args(body: ReferenceApplyIn) -> dict:
    return body.model_dump(include={"component_class", "name", "technology", "projection_year", "confirm"})


@router.post("/library_reference/preview")
def preview_library_reference(body: ReferenceApplyIn, user: User = Depends(require_user)) -> dict:
    """`{card, needs, missing}`. While a confirmation is missing there is no card yet:
    `needs` says what each confirmation means, and the caller previews again with it."""
    from harness import edit_card
    from services.library import reference_apply as A

    p = A.plan_active(_args(body))
    card = None if p["missing"] else edit_card.preview(TOOL, _args(body))
    return {"card": card, "needs": p["needs"], "missing": p["missing"], "notes": p["notes"],
            "rows": p["rows"], "pack": p["pack"], "licence": p["licence"], "attribution": p["attribution"]}


def apply_reference(args: dict, approved_by: str | None) -> dict:
    """Plan, refuse an unconfirmed plan, write through the native component update, stamp."""
    from services import chat_tools
    from services.library import reference_apply as A
    from services.pypsa_service import PyPSAService

    p = A.plan_active(args)
    A.require_confirmed(p)
    chat_tools.update_component(p["component_class"], p["name"], p["attrs"])
    A.stamp(PyPSAService.get_network(), p, approved_by=approved_by)
    return {"component_class": p["component_class"], "name": p["name"], "written": p["attrs"],
            "source": p["source"], "currency": p["currency"], "currency_year": p["currency_year"],
            "licence": p["licence"], "attribution": p["attribution"], "notes": p["notes"]}


@router.post("/library_reference/apply")
def apply_library_reference(body: ReferenceApplyRun, user: User = Depends(require_user)) -> dict:
    from harness import edit_card

    args = _args(body)
    card = edit_card.preview(TOOL, args, with_state=False)
    if body.digest is not None:
        edit_card.require_current(TOOL, args, {"digest": body.digest})
    out = apply_reference(args, getattr(user, "email", None))
    return {**out, "receipt": edit_card.receipt(card, args)}
