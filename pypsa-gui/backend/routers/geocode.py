"""
`/api/geocode` — the address box of the map's click-to-place mode (plan 2, M3).

Thin over `services/geocode.py`: `GET /status` tells the frontend whether
to show the box at all, `GET ?q=` forwards one query. Read-only, never
lock-gated, nothing written.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from services import geocode

router = APIRouter()


@router.get("/status")
def geocode_status() -> dict:
    return {"configured": geocode.configured(), "setting": geocode.GEOCODER_URL_ENV}


@router.get("")
def geocode_search(q: str = Query(..., min_length=1, max_length=geocode.MAX_QUERY_CHARS)) -> list[dict]:
    try:
        return geocode.search(q)
    except geocode.GeocoderUnset as exc:
        raise HTTPException(503, str(exc)) from exc
    except geocode.GeocoderUnavailable as exc:
        raise HTTPException(502, str(exc)) from exc
