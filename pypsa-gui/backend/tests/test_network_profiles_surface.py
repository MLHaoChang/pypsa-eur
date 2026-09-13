"""
Import-surface tripwire for the network PROFILES lift — load / generator /
link profile routes out of `routers/network.py`.

After the bulk lift (#32), measuring what remained showed the next deep
cluster is the three profile blocks (~500 LOC) plus the two helpers only
they use (`_xlsx_response`, `_apply_profile_upload`). The ~80 CRUD factory
calls stay put.

Same contract as Phase 5's time-axis sibling: `services/chat_tools.py`
imports the handlers BY NAME and calls them in-process, so `routers.network`
stays the import surface and re-exports them. `routers.network_profiles`
never imports `routers.network` (would be a cycle).
"""
from __future__ import annotations

import ast
import importlib
import inspect
import pathlib
import re

import pytest

import routers.network as NET

_ORIGIN = "routers.network_profiles"

_MOVED = [
    "_xlsx_response",
    "_LOAD_SHAPES",
    "_apply_profile_upload",
    "get_load_profiles",
    "download_load_profile_template",
    "aggregate_load_profile",
    "upload_load_profile",
    "get_generator_profiles",
    "download_generator_profile_template",
    "upload_generator_profile",
    "get_link_profiles",
    "download_link_profile_template",
    "upload_link_profile",
]

# chat_tools imports these by name (list + template download + upload).
_CHAT_TOOL_IMPORTS = [
    "get_load_profiles",
    "get_generator_profiles",
    "get_link_profiles",
    "download_load_profile_template",
    "download_generator_profile_template",
    "download_link_profile_template",
    "upload_load_profile",
    "upload_generator_profile",
    "upload_link_profile",
]


@pytest.mark.parametrize("name", _MOVED)
def test_the_router_still_exports_every_moved_name(name):
    assert hasattr(NET, name), (
        f"routers.network.{name} is gone. `services/chat_tools.py` imports "
        f"profile handlers by name — re-export it from {_ORIGIN}."
    )


@pytest.mark.parametrize("name", _MOVED)
def test_a_moved_name_is_the_identical_object(name):
    svc = getattr(importlib.import_module(_ORIGIN), name)
    assert getattr(NET, name) is svc, (
        f"routers.network.{name} is not {_ORIGIN}.{name}"
    )


@pytest.mark.parametrize("name", _CHAT_TOOL_IMPORTS)
def test_chat_tools_can_still_import_each_handler(name):
    module = importlib.import_module("routers.network")
    fn = getattr(module, name, None)
    assert callable(fn), f"routers.network.{name} missing for chat_tools"


def test_the_profiles_module_never_imports_routers_network():
    backend = pathlib.Path(__file__).resolve().parent.parent
    path = backend / "routers" / "network_profiles.py"
    pat = re.compile(r"^\s*(from\s+routers\.network\b|import\s+routers\.network\b)")
    offenders = [
        f"{i}: {line.strip()}"
        for i, line in enumerate(path.read_text().splitlines(), 1)
        if pat.match(line)
    ]
    assert not offenders, (
        f"{_ORIGIN} imports routers.network — that is a cycle:\n  "
        + "\n  ".join(offenders)
    )


def test_the_crud_factory_still_exported_from_the_router():
    """Factory helpers may live in services.network_crud but must remain
    importable from routers.network (chat_tools / project_network)."""
    stays = [
        "_serialize_component",
        "_get_component",
        "_create_component",
        "_update_component",
        "_delete_component",
        "_merge_partial_update",
    ]
    import services.network_crud as CRUD
    for name in stays:
        fn = getattr(NET, name, None)
        assert fn is not None, f"routers.network.{name} disappeared"
        assert fn is getattr(CRUD, name), (
            f"routers.network.{name} is not services.network_crud.{name}"
        )


def test_the_moved_routes_are_gone_from_the_router_source():
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


def test_push_undo_snapshot_moved_to_network_undo_after_this_phase():
    """Later undo lift; still reachable from the router façade."""
    assert NET._push_undo_snapshot.__module__ == "services.network_undo"


def test_the_profiles_router_is_included():
    """FastAPI only serves routes on an included router."""
    src = pathlib.Path(__file__).resolve().parent.parent.joinpath(
        "routers", "network.py"
    ).read_text()
    assert "include_router(_profiles_router)" in src or "include_router( _profiles_router)" in src, (
        "routers.network must include the profiles router"
    )
