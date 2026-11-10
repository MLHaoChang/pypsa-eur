"""Apply one saved parameter (assistant memory slice 03) to one asset of the active network.

The preview is `POST /api/memory/suggestions/{id}/preview` (a read). The apply stays
under `/api/network`, like `library_reference/apply`, so it gets the same foreign-lock
gate, undo snapshot, edit-counter bump, results invalidation and change-log entry.
See `services/memory_parameters.py`.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session as DBSession

from db.models import User
from db.session import get_db
from deps import require_user

router = APIRouter()


class MemoryParameterApply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    project_id: str = Field(min_length=1)
    component_class: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1)
    digest: str = Field(min_length=1)


@router.post("/memory_parameter/apply")
def apply_memory_parameter(
    body: MemoryParameterApply,
    db: DBSession = Depends(get_db),
    user: User = Depends(require_user),
) -> dict:
    """The user's Apply on the card they saw: the digest must still be current."""
    from routers.memory import _raise
    from services import memory_parameters as P
    from services import memory_store as M

    try:
        project = P.bound_project(db, user, body.project_id)
        return P.apply(db, user, project, body.item_id, body.version, body.component_class,
                       body.name, body.digest)
    except M.MemoryError as exc:
        _raise(exc)
