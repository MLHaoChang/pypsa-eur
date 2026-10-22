"""
The shipped (`common`) cost references over HTTP: read-only.

Thin handlers over `services/library/reference_catalogue.py`. The references are
pinned, attributed benchmarks shipped in-tree, the same for every organization, so
no org is resolved; the caller only has to be signed in. `pack_source` is an asset's
`overnight_cost` provenance source (`GET /api/network/asset_schema/{component}/
provenance/{name}`), so the comparison stands beside the pack that asset was priced
from; without it, the latest generic pack. Applying a reference is a separate,
previewed edit.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from db.models import User
from deps import require_user
from services.library import reference_catalogue as C
from services.library.defaults_pack.loader import DefaultsPackError

router = APIRouter()


@router.get("", response_model=list[C.ReferenceSummary])
def list_references(user: User = Depends(require_user)):
    return C.list_references()


@router.get("/{technology}/{year}", response_model=C.ReferenceDetail)
def get_reference(technology: str, year: int, pack_source: str | None = None,
                  user: User = Depends(require_user)):
    provenance = {"overnight_cost": {"source": pack_source}} if pack_source else None
    try:
        return C.reference_detail(technology, year, provenance)
    except DefaultsPackError as exc:
        raise HTTPException(404, {"code": "library_reference_unknown", "message": str(exc)}) from exc
