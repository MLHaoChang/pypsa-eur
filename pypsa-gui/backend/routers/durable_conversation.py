"""Thin HTTP surface for durable parent conversation threads (issue 09 / B-I1)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
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


class RebindBody(BaseModel):
    model_config = ConfigDict(extra='forbid')

    generation: int = Field(..., ge=1)


class ProfileBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    generation: int = Field(..., ge=1)
    profile_id: str = Field(..., min_length=1, max_length=64)


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


@router.post('/{thread_id}/rebind')
def rebind_thread(
    thread_id: str,
    body: RebindBody,
    user: User = Depends(require_user),
):
    _bind_actor(user)
    return dc.rebind_scope(thread_id, generation=body.generation)


@router.post('/{thread_id}/profile')
def switch_thread_profile(
    thread_id: str,
    body: ProfileBody,
    user: User = Depends(require_user),
):
    """Switch provider profile; unknown and keyless profiles refuse alike.

    One refusal for both, naming neither the profile nor any key or env
    variable, so the answer never says whether a credential is set.
    """
    from routers.chat import _profile_chat_ready
    from services import llm_config

    _bind_actor(user)
    try:
        profile = llm_config.resolve_profile(body.profile_id)
    except llm_config.ProfileNotConfiguredError:
        profile = None
    if profile is None or not _profile_chat_ready(profile):
        raise HTTPException(status_code=422, detail={
            'error_kind': 'profile_unavailable',
            'message': 'That model profile is not available. Pick one from the model menu.',
        })
    thread, _session = dc.switch_profile(
        thread_id, generation=body.generation, profile=profile,
    )
    return thread
