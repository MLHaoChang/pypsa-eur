"""Which members a batch apply writes (RP-09).

The ONE rule behind both the route (`apply_profile_batch`) and the
confirmation card (`card.preview_members`): the card the user approves is
exactly what is written, so neither keeps its own copy.
"""
from __future__ import annotations

from typing import Any, Sequence

from fastapi import HTTPException

from services.profile_gen.readiness import technology_of


def batch_members(points: Sequence[str], generators, members: Sequence[tuple[str, str | None]],
                  all_from_job: bool) -> tuple[list[tuple[str, str | None]], list[dict[str, str]]]:
    """(members as (component, point_id), skipped). `points` are the job's
    point ids (`meta["points"]`). Naming members together with `all_from_job`
    is ambiguous and refused; `all_from_job` takes every point named after a
    wind/solar generator and lists the rest under `skipped`."""
    if all_from_job and members:
        raise HTTPException(status_code=422, detail={
            "error_kind": "ambiguous_members",
            "message": "give either `members` or `all_from_job`, not both"})
    if not all_from_job:
        return list(members), []
    chosen: list[tuple[str, str | None]] = []
    skipped: list[dict[str, str]] = []
    for pid in (str(p) for p in points):
        if pid in generators.index and technology_of(generators.at[pid, "carrier"]) is not None:
            chosen.append((pid, None))
        else:
            skipped.append({"point_id": pid,
                            "reason": "not a wind/solar generator on the network"})
    return chosen, skipped
