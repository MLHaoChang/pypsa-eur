"""RP-18 / RP-20: confirm that series with no provenance record are the
network's own.

One body for the route (`POST /api/projects/{name}/profiles/unrecorded/confirm`)
and the chat tool `confirm_unrecorded_series`. It writes ONLY the provenance
meta, never the values, under one undo step. All or nothing: a batch with a
duplicate, a series that is not unrecorded now, or one whose values changed
since the card showed its digest is refused whole, so nothing is stamped
unseen.

"Unrecorded" is decided by `readiness.profile_kind` alone (GK B C1), and the
re-read, the undo snapshot and the write happen in one critical section under
the lock the other profile writers hold (`PyPSAService.get_lock()` then
`user_timeseries._user_ts_lock`, as `apply.apply_profile` nests them), so no
edit can land between the check and the stamp (C2).
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

KEY = ("generators", "p_max_pu")
UNDO_NOTE = "one undo restores the series to having no record"


class UnrecordedRefused(Exception):
    """The whole batch is refused; nothing was written. ``detail`` is the
    route's 409 body; each kind is a literal at its raise site, so the
    error-kind manifest sees every one."""

    def __init__(self, detail: dict[str, Any]):
        super().__init__(detail["message"])
        self.kind = detail["error_kind"]
        self.components = detail["components"]
        self._detail = {**detail, "component": detail["components"][0] if detail["components"] else None}

    def detail(self) -> dict[str, Any]:
        return dict(self._detail)


def _pairs(items: Iterable[Any]) -> list[tuple[str, str]]:
    out = []
    for it in items:
        if isinstance(it, Mapping):
            out.append((str(it.get("component") or ""), str(it.get("digest") or "")))
        else:
            out.append((str(it.component), str(it.digest)))
    return out


def check(ctx, items: Iterable[Any]) -> list[tuple[str, str]]:
    """The batch as (component, digest) pairs, refused whole unless every
    series is unrecorded now and still has the digest the caller saw.
    Callers that write hold the lock (`confirm`); the chat precheck and card
    call it unlocked only to refuse early."""
    from services.profile_gen.apply import current_digest
    from services.profile_gen.readiness import profile_kind

    pairs = _pairs(items)
    if not pairs:
        raise UnrecordedRefused({"error_kind": "unrecorded_empty", "components": [],
                                 "message": "name at least one series to confirm"})
    names = [c for c, _ in pairs]
    dupes = sorted({c for c in names if names.count(c) > 1})
    if dupes:
        raise UnrecordedRefused({
            "error_kind": "unrecorded_duplicate", "components": dupes,
            "message": f"each series may be listed once; listed more than once: {', '.join(dupes)}"})
    gone = [c for c in names if profile_kind(ctx, c) != "unrecorded"]
    if gone:
        raise UnrecordedRefused({
            "error_kind": "not_unrecorded", "components": gone,
            "message": f"{', '.join(gone)} no longer {'has' if len(gone) == 1 else 'have'} "
                       "a series with no record; reload the list"})
    stale = [c for c, d in pairs if current_digest(ctx.user_ts[(*KEY, c)]) != d]
    if stale:
        raise UnrecordedRefused({
            "error_kind": "unrecorded_stale", "components": stale,
            "message": f"{', '.join(stale)} changed since the list was shown; "
                       "reload it and confirm again"})
    return pairs


def confirm(ctx, items: Iterable[Any]) -> dict[str, Any]:
    """Stamp every series in ``items`` as the network's own, or refuse the
    whole batch (`UnrecordedRefused`). One undo snapshot per call."""
    from services.network_undo import push_undo_snapshot
    from services.pypsa_service import PyPSAService
    from services.user_timeseries import NETWORK_NATIVE_META, _user_ts_lock

    with PyPSAService.get_lock(), _user_ts_lock:
        pairs = check(ctx, items)
        push_undo_snapshot()
        for component, _ in pairs:
            ctx.user_ts_meta[(*KEY, component)] = dict(NETWORK_NATIVE_META)
    return {"confirmed": [c for c, _ in pairs], "count": len(pairs), "undo": UNDO_NOTE}
