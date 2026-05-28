from __future__ import annotations

from fastapi import APIRouter
from services import change_log_service

router = APIRouter()


@router.get("/")
def get_changelog() -> list[dict]:
    return change_log_service.get_all()


@router.delete("/", status_code=204)
def clear_changelog() -> None:
    change_log_service.clear()
