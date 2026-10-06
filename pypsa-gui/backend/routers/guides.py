"""
In-app guides (P21): the tour/field catalogue the GUI's guided tours and
hover tips read. Static, read-only content — no project or network state.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from services.guides import GuideError, guide_topics, load_guide

router = APIRouter()


@router.get("")
def list_guides() -> dict:
    return {"topics": guide_topics()}


@router.get("/{topic}")
def get_guide(topic: str) -> dict:
    try:
        return load_guide(topic)
    except GuideError as exc:
        raise HTTPException(404, str(exc)) from exc
