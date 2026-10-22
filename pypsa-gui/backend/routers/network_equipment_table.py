"""
Map an uploaded equipment table (CSV/XLSX) onto several assets of one class: the
manual-mapping form's preview, and the apply both the form and the assistant use.

`apply` is under `/api/network`, so the middleware takes ONE undo snapshot before the
handler (the loop below calls the native update in-process, never through HTTP, so it
adds none), the foreign-lock gate applies, and results are invalidated like every other
network edit. It goes through the edit card (`harness/edit_card.py`, tool
`apply_equipment_mapping`): the card shows every changed cell old → new with its source
cell. The handler builds the card on a copy (any refused cell refuses the card), refuses
a digest other than the one the user approved (`edit_preview_stale`) or changed table
bytes (`source_changed`), then `equipment_table.apply` writes exactly the card's cells
under the mutation lock and the receipt reads them back. The assistant's
`apply_equipment_mapping` runs the same handler after its own confirmation, with the
digest of the card the user approved there (`edit_card.approved_digest`).

`preview` (`preview_router`, mounted at `/api/equipment_table`) is a read: it sits
outside `/api/network` and `/api/io`, so it takes no undo snapshot, leaves the edit
counter and the results alone, and needs no `_UNDO_EXCLUDE` entry. See
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


class EquipmentMappingHttp(EquipmentMappingIn):
    # The card the user approved: the form's from its preview, the assistant's from
    # the confirmation its turn loop showed. Any other refuses (edit_preview_stale).
    digest: str = Field(min_length=1)


class EquipmentPreviewIn(BaseModel):
    """A table to read, and optionally a mapping to plan. Without `component_class` the
    preview is the table alone; without `name_column` and `columns`, the table and the
    class's mappable fields."""

    upload_id: str = Field(min_length=1)
    sheet: str | None = None
    component_class: str | None = None
    name_column: str | None = None
    columns: dict[str, ColumnMap] = Field(default_factory=dict)
    currency: str | None = None
    money_year: int | None = None
    confirm: list[str] = Field(default_factory=list)


# Data rows the preview shows of the table itself; the plan reads every row.
SAMPLE_ROWS = 5


def args_of(body: BaseModel) -> dict[str, Any]:
    out = body.model_dump(exclude={"digest"})
    out["columns"] = {k: v.model_dump() for k, v in body.columns.items()}
    return out


preview_router = APIRouter()


@preview_router.post("/preview")
def preview_equipment_mapping_http(body: EquipmentPreviewIn, user: User = Depends(require_user)) -> dict:
    """`{table, fields?, plan?, card?}`. `table` = `{source, headers, rows, row_count}` with
    the first `SAMPLE_ROWS` rows' raw values; `fields` the class's mappable fields
    (`equipment_table.mappable_fields`); `plan` what `preview_equipment_mapping`
    reports; `card` the edit card the apply will check, only while no confirmation is
    missing and some cell changes (else `plan` says why). Writes nothing."""
    from harness import edit_card
    from services import chat_tools
    from services import equipment_table as T
    from services.pypsa_service import PyPSAService

    table = T.read_table(chat_tools._require_active_project(), body.upload_id, body.sheet)
    out: dict[str, Any] = {"table": {
        "source": table["source"], "headers": table["headers"], "row_count": len(table["rows"]),
        "rows": [{"row": r["row"], "cells": {h: c["raw"] for h, c in r["cells"].items()}}
                 for r in table["rows"][:SAMPLE_ROWS]]}}
    if not body.component_class:
        return out
    out["fields"] = T.mappable_fields(edit_card._fields(body.component_class))
    if not body.name_column or not body.columns:
        return out
    args = {**args_of(body), "source_sha256": table["source"]["sha256"]}
    p = T.plan_active(args)
    out["plan"] = T.summary(PyPSAService.get_network(), p)
    out["card"] = (edit_card.preview(TOOL, args)
                   if not p["missing"] and T.changes(PyPSAService.get_network(), p) else None)
    return out


@router.post("/equipment_table/apply")
def apply_equipment_mapping(body: EquipmentMappingHttp, user: User = Depends(require_user)) -> dict:
    """The apply: the card the user approved must still be current."""
    from harness import edit_card
    from services import equipment_table as T

    args = args_of(body)
    card = edit_card.preview(TOOL, args, with_state=False)
    if body.digest != card["digest"]:
        T._refuse("edit_preview_stale", "The values changed since the card was shown; nothing was applied. "
                  "Propose the edit again.", 409)
    p = T.plan_active(args)
    T.require_confirmed(p)
    out = T.apply(p, card, approved_by=getattr(user, "email", None))
    return {**out, "receipt": edit_card.receipt(card, args)}
