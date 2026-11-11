"""RP-20: the card of `confirm_unrecorded_series`.

One row per series the call would stamp, every one (no cap, RP-18 R4), each
showing the digest of the values the user is vouching for. The rows are built
by `services.profile_gen.unrecorded.check`, the same reader the write uses
(GK B C1), so a batch the write would refuse gets no card at all. The digest
of the card binds the exact (component, digest) set and the project
(`require_current` re-previews at approval, C3); the write re-checks under
its lock (C2).
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from harness.edit_card import Adapter, register

OLD = "no record"
NEW = "the network's own"
_VERB = "confirm unrecorded series"


def _scope(args: dict):
    """The project the call names, refused unless it is the open one and
    nobody else holds its edit lock: the route's own gates, before any card."""
    from routers.deps import require_project_access
    from routers.profiles import _check_lock, _foreground_ctx
    from services import chat_tools

    with chat_tools._acting() as (db, user):
        proj = require_project_access(str(args.get("project_id") or ""), db=db, user=user)
        _check_lock(proj, db, user)
    return proj, _foreground_ctx(proj, _VERB)


def _preview(args: dict) -> list[dict]:
    from services.profile_gen import unrecorded

    _, ctx = _scope(args)
    try:
        pairs = unrecorded.check(ctx, args.get("items") or [])
    except unrecorded.UnrecordedRefused as exc:
        raise HTTPException(409, exc.detail()) from None
    return [{"component_class": "Generator", "name": component, "field": "provenance",
             "label": "Series record", "old": f"{OLD} (series {digest})", "new": NEW,
             "unit": "", "role": "typed", "digest": digest}
            for component, digest in pairs]


def _binding(args: dict) -> dict:
    proj, _ = _scope(args)
    return {"project": proj.uuid or proj.name}


def _readback(args: dict, rows: list[dict]) -> list[Any]:
    """Each series' record as stored now (`readiness.profile_kind`)."""
    from services.profile_gen.readiness import profile_kind
    from services.pypsa_service import PyPSAService

    ctx = PyPSAService.get_active_context()
    words = {"network_native": NEW, "unrecorded": OLD}
    return [words.get(profile_kind(ctx, row["name"]), "another record") for row in rows]


def _says(args: dict, rows: list[dict]) -> str:
    n = len(rows)
    return (f"Records {n} series as {NEW}; their values are unchanged")


register("confirm_unrecorded_series", Adapter("series_edit", _preview, _readback, section_id="profiles",
                                              binding=_binding, says=_says))
