"""
Filtering the solver's transient rows out of a plain name list.

During a solve the LP scaffolding adds rows that are not part of the user's
network — vintage clones (`parent@<year>`) and VOLL slacks (`__voll_<bus>`).
`PyPSAService` tracks them so reads can hide them; `_get_component` filters
whole DataFrames, and this filters the column- or index-name LISTS that some
endpoints return directly.

It lives here rather than in `routers/network.py` because BOTH that router and
`routers/network_time_axis.py` need it, and the time-axis module must not import
back from the router it was split out of — that is a cycle, and a cycle deferred
into a function body is still a cycle. It is a pure domain helper over
`PyPSAService` with no HTTP in it, so a service module is where it belonged
anyway.
"""
from __future__ import annotations

from services.pypsa_service import PyPSAService


def filter_transient_names(component_class: str, names: list[str]) -> list[str]:
    """
    Drop solver-only transient names (vintage clones, VOLL slacks) from
    a plain name list. Mirror of the row filter in `_get_component`, but
    for endpoints that return column- or index-name LISTS directly (e.g.
    /timeseries, /generators/profiles) instead of full DataFrames.

    Short-circuits when the registry is empty so the healthy path costs
    one dict lookup. Preserves input order, no allocation if nothing
    needs filtering.
    """
    if not names or not PyPSAService.has_any_transient_rows():
        return names
    transient = PyPSAService.get_transient_rows(component_class)
    if not transient:
        return names
    return [n for n in names if n not in transient]
