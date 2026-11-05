"""Thin HTTP surface for durable parent conversation threads (issue 09 / B-I1)."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from db.models import User
from deps import require_user
from services import chat_tools, durable_conversation as dc, live_speech

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


class ReconcileResolution(BaseModel):
    model_config = ConfigDict(extra='forbid')
    key: str = Field(..., min_length=1, max_length=512)
    resolution: Literal['done', 'not_done', 'dismissed']


class ReconcileBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    generation: int = Field(..., ge=1)
    resolutions: list[ReconcileResolution] = Field(
        default_factory=list, max_length=dc.RECONCILE_MAX,
    )


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


@router.post('/{thread_id}/reconcile')
def reconcile_thread(
    thread_id: str,
    body: ReconcileBody,
    user: User = Depends(require_user),
):
    """Resolve unverified turns/tool calls and re-read jobs; never replays."""
    _bind_actor(user)
    return dc.reconcile_effects(
        thread_id,
        generation=body.generation,
        resolutions=[item.model_dump() for item in body.resolutions],
    )


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


# ── LC-07: continuous speech session (off unless PYPSA_GUI_LIVE_SPEECH=1) ──

class SpeechStartBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    generation: int = Field(..., ge=1)
    language: str = Field(..., min_length=2, max_length=35)


class SpeechEndBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    generation: int = Field(..., ge=1)
    session_id: str = Field(..., min_length=1, max_length=48)


class SpeechSegmentBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    session_id: str = Field(..., min_length=1, max_length=48)
    segment_id: str = Field(..., min_length=1, max_length=48)
    seq: int = Field(..., ge=0)
    text: str = Field(..., max_length=4000)
    final: bool


class SpeechUsageBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    session_id: str = Field(..., min_length=1, max_length=48)
    usage_id: str = Field(..., min_length=1, max_length=48)
    kind: str = Field(..., min_length=1, max_length=16)
    amount: float
    unit: str = Field(..., min_length=1, max_length=32)
    final: bool = False


@router.post('/{thread_id}/speech/start')
def start_speech(thread_id: str, body: SpeechStartBody, user: User = Depends(require_user)):
    """Open a speech session; the media grant is returned here and nowhere else."""
    _bind_actor(user)
    return live_speech.start(thread_id, generation=body.generation, language=body.language)


@router.get('/{thread_id}/speech')
def get_speech(thread_id: str, user: User = Depends(require_user)):
    _bind_actor(user)
    return live_speech.state(thread_id)


@router.post('/{thread_id}/speech/end')
def end_speech(thread_id: str, body: SpeechEndBody, user: User = Depends(require_user)):
    _bind_actor(user)
    return live_speech.end(thread_id, generation=body.generation, session_id=body.session_id)


@router.post('/{thread_id}/speech/segment')
def speech_segment(
    thread_id: str,
    body: SpeechSegmentBody,
    user: User = Depends(require_user),
    x_bridge_token: str = Header(default=''),
):
    """The audio bridge reports one recognition segment; only finals count."""
    _bind_actor(user)
    return live_speech.submit_segment(
        thread_id, token=x_bridge_token, session_id=body.session_id,
        segment_id=body.segment_id, seq=body.seq, text=body.text, final=body.final,
    )


@router.post('/{thread_id}/speech/usage')
def speech_usage(
    thread_id: str,
    body: SpeechUsageBody,
    user: User = Depends(require_user),
    x_bridge_token: str = Header(default=''),
):
    """The audio bridge reports provider usage, once per usage_id; ``final`` last."""
    _bind_actor(user)
    return live_speech.report_usage(
        thread_id, token=x_bridge_token, session_id=body.session_id,
        usage_id=body.usage_id, kind=body.kind, amount=body.amount, unit=body.unit,
        final=body.final,
    )


class SpeechOutputBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    session_id: str = Field(..., min_length=1, max_length=48)


class SpeechBargeInBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    session_id: str = Field(..., min_length=1, max_length=48)
    generation: int = Field(..., ge=1)


@router.post('/{thread_id}/speech/output/claim')
def speech_output_claim(
    thread_id: str,
    body: SpeechOutputBody,
    user: User = Depends(require_user),
    x_bridge_token: str = Header(default=''),
):
    """The audio bridge takes the next checked answer to speak, byte-equal to its caption."""
    _bind_actor(user)
    return live_speech.claim_output(thread_id, token=x_bridge_token, session_id=body.session_id)


@router.post('/{thread_id}/speech/barge-in')
def speech_barge_in(
    thread_id: str,
    body: SpeechBargeInBody,
    user: User = Depends(require_user),
    x_bridge_token: str = Header(default=''),
):
    """The user spoke over playback: drop queued and claimed answers; the turn runs on."""
    _bind_actor(user)
    return live_speech.barge_in(
        thread_id, token=x_bridge_token, session_id=body.session_id, generation=body.generation,
    )
