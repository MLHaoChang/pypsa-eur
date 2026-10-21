"""
Apply a mapped equipment table (CSV/XLSX upload) to several assets of one class.

Under `/api/network`, so the middleware takes ONE undo snapshot before the handler (the
loop below calls the native update in-process, never through HTTP, so it adds none),
the foreign-lock gate applies, and results are invalidated like every other network
edit. It goes through the edit card (`harness/edit_card.py`, tool
`apply_equipment_mapping`): the card shows every changed cell old → new with its source
cell. The handler builds the card on a copy (any refused cell refuses the card), refuses
a digest other than the one the user approved (`edit_preview_stale`) or changed table
bytes (`source_changed`), then `equipment_table.apply` writes exactly the card's cells
under the mutation lock and the receipt reads them back. The assistant's
`apply_equipment_mapping` runs the same handler after its own confirmation. See
`services/equipment_table.py`.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from db.models import User
from deps import require_user

router = APIRouter()

TOOL = "apply_equipment_mapping"


class ColumnMap(BaseModel):
    field: str = Field(min_length=1)
    unit: str | None = None


class EquipmentMappingIn(BaseModel):
    upload_id: str = Field(min_length=1)
    sheet: str | None = None
    component_class: str = Field(min_length=1)
    name_column: str = Field(min_length=1)
    columns: dict[str, ColumnMap]
    currency: str | None = None
    money_year: int | None = None
    confirm: list[str] = Field(default_factory=list)
    source_sha256: str = Field(min_length=64, max_length=64)


class EquipmentMappingRun(EquipmentMappingIn):
    # The card the user approved; the HTTP apply always needs it, the assistant's tool
    # passes None because its turn loop already checked the card (`require_current`).
    digest: str | None = None


class EquipmentMappingHttp(EquipmentMappingIn):
    digest: str = Field(min_length=1)


def args_of(body: EquipmentMappingIn) -> dict[str, Any]:
    out = body.model_dump(exclude={"digest"})
    out["columns"] = {k: v.model_dump() for k, v in body.columns.items()}
    return out


@router.post("/equipment_table/apply")
def apply_equipment_mapping_http(body: EquipmentMappingHttp, user: User = Depends(require_user)) -> dict:
    """The UI's apply: the card the user saw must still be current."""
    return apply_equipment_mapping(EquipmentMappingRun(**body.model_dump()), user)


def apply_equipment_mapping(body: EquipmentMappingRun, user: User = Depends(require_user)) -> dict:
    from harness import edit_card
    from services import equipment_table as T

    args = args_of(body)
    card = edit_card.preview(TOOL, args, with_state=False)
    if body.digest is not None and body.digest != card["digest"]:
        T._refuse("edit_preview_stale", "The values changed since the card was shown; nothing was applied. "
                  "Propose the edit again.", 409)
    p = T.plan_active(args)
    T.require_confirmed(p)
    out = T.apply(p, card, approved_by=getattr(user, "email", None))
    return {**out, "receipt": edit_card.receipt(card, args)}
