"""
The import-surface tripwire for Phase 5 — lifting the network's TIME AXIS
routes out of `routers/network.py`.

Phase 4 took the pure helper clusters out and deliberately left the ~80 CRUD
routes alone, on the grounds that they are individually short. Measuring the
remainder showed where the depth actually is:

    snapshots + timeseries + investment periods
        = 1,028 lines across 15 routes
        = 45% of the file's function content, in 19% of its routes

    storage_units / stores / shunt_impedances
        = 4 routes each, in 8 lines each

So this phase moves the fifteen, not the eighty. Splitting two-line factory
calls into per-component modules would add files without removing complexity.

Why it is a clean cut: those fifteen routes reference exactly THREE
module-level names from `routers/network.py` — `router`, `_filter_transient_names`
(shared, so it moved to `services/transient_rows.py` where both can import it
without a cycle), and `_ATTR_TO_CLASS` (used by nothing else, so it moved with
them). Everything else they touch already lives in `services/`.

The constraint this file pins is the import surface. Fourteen of the fifteen
handlers are imported BY NAME from `services/chat_tools.py`, which calls them
in-process as plain functions rather than over HTTP — so `routers.network` stays
the import surface and re-exports them, and chat_tools does not change. (A bare
name grep suggests ~45 files import `set_snapshots`; almost all of those are
`n.set_snapshots(...)`, the pypsa Network method. The real count, by AST, is
one.)
"""
from __future__ import annotations

import ast
import importlib
import inspect
import pathlib
import re

import pytest

import routers.network as NET

_ORIGIN = "routers.network_time_axis"

# Every name the move relocates. All fifteen are routes; `_ATTR_TO_CLASS` is the
# lookup table they share.
_MOVED = [
    "get_snapshots",
    "download_snapshot_weightings_csv",
    "upload_snapshot_weightings_csv",
    "update_snapshot_weightings",
    "set_snapshots",
    "set_multi_period_snapshots",
    "sample_representative_weeks",
    "get_investment_periods",
    "set_investment_periods",
    "update_investment_period_weightings",
    "list_timeseries",
    "get_timeseries",
    "set_timeseries",
    "upload_timeseries",
    "delete_timeseries",
    "_ATTR_TO_CLASS",
]

# Imported by name from services/chat_tools.py, which calls them in-process.
# Breaking any of these breaks the chat tool layer, not just an HTTP route.
_CHAT_TOOL_IMPORTS = [n for n in _MOVED if n not in ("set_timeseries", "_ATTR_TO_CLASS")]


@pytest.mark.parametrize("name", _MOVED)
def test_the_router_still_exports_every_moved_name(name):
    assert hasattr(NET, name), (
        f"routers.network.{name} is gone. `services/chat_tools.py` imports it "
        f"by name — re-export it from {_ORIGIN} rather than repointing that."
    )


@pytest.mark.parametrize("name", _MOVED)
def test_a_moved_name_is_the_identical_object(name):
    """
    Re-export the object, don't redefine it. For a route handler a copy would
    be worse than a broken import: FastAPI would serve the original while
    `chat_tools` called the duplicate, and the two would drift silently.
    """
    svc = getattr(importlib.import_module(_ORIGIN), name)
    assert getattr(NET, name) is svc, f"routers.network.{name} is not {_ORIGIN}.{name}"


@pytest.mark.parametrize("name", _CHAT_TOOL_IMPORTS)
def test_chat_tools_can_still_import_each_handler(name):
    """
    The actual contract, exercised the way `chat_tools` exercises it. It does
    `from routers.network import <handler>` inside a function body, so a
    missing re-export surfaces only when that tool is invoked.
    """
    module = importlib.import_module("routers.network")
    fn = getattr(module, name, None)
    assert callable(fn), f"chat_tools imports {name} from routers.network; it is not callable"


def test_the_moved_module_never_imports_back_from_the_router():
    """
    Dependencies run one way. `filter_transient_names` is shared by both
    halves, so the temptation was to import it back from the router — which is a
    cycle, and a cycle deferred into a function body is still a cycle. It moved
    to `services/transient_rows.py` instead, which both import from.
    """
    path = pathlib.Path(__file__).resolve().parent.parent / (_ORIGIN.replace(".", "/") + ".py")
    assert path.is_file(), f"{_ORIGIN} does not exist yet"
    pat = re.compile(r"^\s*(from\s+routers\.network\b|import\s+routers\.network\b)")
    offenders = [
        f"{path.name}:{i}: {line.strip()}"
        for i, line in enumerate(path.read_text().splitlines(), 1)
        if pat.match(line)
    ]
    assert not offenders, (
        f"{_ORIGIN} imports from routers.network — that is a cycle:\n  "
        + "\n  ".join(offenders)
    )


def test_the_crud_routes_did_not_come_along():
    """
    Scope guard. This phase moves the time axis; the ~80 CRUD routes stay put
    deliberately, because they are two-line factory calls and splitting them
    would add files without removing complexity. If one of these moved, the
    phase grew without the reasoning being revisited.
    """
    stays = ["_serialize_component", "_get_component", "_create_component",
             "_update_component", "_delete_component", "_merge_partial_update",
             "_xlsx_response", "_push_undo_snapshot"]
    for name in stays:
        fn = getattr(NET, name, None)
        assert fn is not None, f"routers.network.{name} disappeared"
        if inspect.isfunction(fn):
            assert fn.__module__ == "routers.network", (
                f"{name} left routers.network — this phase was meant to move the "
                f"time-axis routes only"
            )


def test_the_moved_routes_are_gone_from_the_router_source():
    """
    The other half of the move: re-exporting is not enough, the bodies have to
    have LEFT. A `def get_snapshots` still sitting in `routers/network.py`
    would mean the file never got smaller and two definitions now race for the
    same URL.
    """
    backend = pathlib.Path(__file__).resolve().parent.parent
    tree = ast.parse((backend / "routers" / "network.py").read_text())
    still_here = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in _MOVED
    }
    assert not still_here, (
        f"these are still DEFINED in routers/network.py: {sorted(still_here)}"
    )


def test_no_network_route_wildcards_its_first_segment():
    """
    Why the move is order-neutral, asserted rather than argued.

    `router.include_router()` appends, so the fifteen routes no longer sit where
    they were defined: they used to be at positions 62-71, 82-85 and 96 of the
    network router and are now the first fifteen. The OpenAPI document is
    byte-identical either way — FastAPI dispatches on first match, and position
    only decides anything when two routes can match ONE request.

    Under `/api/network` none can, because every path's first segment is a
    literal: `/snapshots`, `/timeseries`, `/buses/{name}`, never
    `/api/network/{thing}`. A wildcard there would swallow `/snapshots` and
    `/timeseries` from wherever it was registered first, and the failure would
    be a 404 on a route that plainly exists.

    So this is the condition under which the reorder is safe. Add
    `/api/network/{x}` and it stops holding — which is what this test is for.
    """
    import main

    prefix = "/api/network/"
    offenders = []
    for route in main.app.routes:
        for path in _iter_route_paths(route):
            if not path.startswith(prefix):
                continue
            first = path[len(prefix):].split("/", 1)[0]
            if "{" in first:
                offenders.append(path)
    assert not offenders, (
        "these /api/network routes wildcard their first path segment, so route "
        f"ORDER now decides which handler serves a request: {sorted(set(offenders))}. "
        "The time-axis routes are attached by include_router and are no longer "
        "in their original positions; a first-segment wildcard would shadow them."
    )


def _iter_route_paths(route):
    """
    Every concrete path under `route`, including ones behind an included router.

    `app.routes` hands back `_IncludedRouter` wrappers rather than the routes
    themselves. Their `effective_candidates` PROPERTY builds the flattened list
    on first access; the `_effective_candidates` attribute behind it is empty
    until then, and reading that instead is how the first version of this
    walker found zero paths and passed vacuously.
    """
    path = getattr(route, "path_format", None) or getattr(route, "path", None)
    if isinstance(path, str):
        yield path
    children = list(getattr(route, "routes", None) or [])
    candidates = getattr(route, "effective_candidates", None)
    if candidates is not None:
        children += list(candidates() if callable(candidates) else candidates)
    for child in children:
        yield from _iter_route_paths(child)
