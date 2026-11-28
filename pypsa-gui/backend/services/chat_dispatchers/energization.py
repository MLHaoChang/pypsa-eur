"""
Energization (template-e2e 02): the dispatcher for set_energization_period.

Schema and route: `harness/catalogue_tools/energization.py`.

A thin wrapper over `routers.flow.put_flow_energization`, called in-process
through chat_tools' `_route` with the acting user, so a chat edit takes the
route's whole path: the edit lock, the open-project and live-study checks, the
typed 422 refusals, one undo step, the edit counter, the unsaved mark, cleared
dispatch and the change log. A viewer is refused before dispatch by the
write tier (`restrictions.require_allowed`, `project_view_only`).

The chat_tools helpers are imported inside the function: chat_tools imports
this module at its end, so a top-level import would be circular when this
module is imported first.
"""

from __future__ import annotations

from typing import Any

_UNSET = object()


def set_energization_period(
    name: str, link: Any = _UNSET, mw: Any = _UNSET, period: Any = None
) -> dict:
    """`PUT .../flow/energization`: one period of one Link set to one MW value."""
    from routers.flow import put_flow_energization as _h
    from services.chat_tools import _authorized_project, _route

    # Sent as the HTTP body would be: a `link` or `mw` the model left out stays
    # out, so the route's own shape check refuses it (energization_invalid_request).
    # A missing `period` is null, as the route reads an absent one: "all" on a
    # single-period network, energization_unknown_period on a multi-period one.
    body = {
        k: v
        for k, v in (("link", link), ("period", period), ("mw", mw))
        if v is not _UNSET
    }
    return _route(_h, body, project=_authorized_project(name))


DISPATCHERS = {
    "set_energization_period": set_energization_period,
}
