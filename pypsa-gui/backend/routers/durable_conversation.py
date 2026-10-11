"""Thin HTTP surface for durable parent conversation threads (issue 09 / B-I1)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from db.models import User
from deps import require_user
from services import chat_tools, durable_conversation as dc

router = APIRouter()


class CheckpointBody(BaseModel):
    model_config = ConfigDict(extra='forbid')

    generation: int
    goals: list[str] | None = None
    constraints: list[str] | None = None
    units: dict[str, str] | None = None
    refs: dict[str, Any] | None = None
    task_ids: list[str] | None = None
    job_ids: list[str] | None = None
    experiment_ids: list[str] | None = None
    evidence_revisions: list[dict[str, Any]] | None = None
    profile_lineage: list[dict[str, Any]] | None = None
    episode_id: str | None = None
    budgets: dict[str, Any] | None = None


class ResumeBody(BaseModel):
    model_config = ConfigDict(extra='forbid')

    generation: int = Field(..., ge=1)


def _bind_actor(user: User) -> None:
    chat_tools.set_acting_user(str(user.id))


@router.post('')
def create_thread(user: User = Depends(require_user)):
    _bind_actor(user)
    return dc.create_thread()


@router.get('/{thread_id}')
def get_thread(thread_id: str, user: User = Depends(require_user)):
    _bind_actor(user)
    return dc.authorize_thread(thread_id)


@router.post('/{thread_id}/checkpoint')
def checkpoint_thread(
    thread_id: str,
    body: CheckpointBody,
    user: User = Depends(require_user),
):
    _bind_actor(user)
    fields = body.model_dump(exclude_none=True, exclude={'generation'})
    return dc.checkpoint_thread(thread_id, generation=body.generation, **fields)


@router.post('/{thread_id}/resume')
def resume_thread(
    thread_id: str,
    body: ResumeBody,
    user: User = Depends(require_user),
):
    _bind_actor(user)
    thread, _session = dc.resume_episode(thread_id, generation=body.generation)
    return thread
