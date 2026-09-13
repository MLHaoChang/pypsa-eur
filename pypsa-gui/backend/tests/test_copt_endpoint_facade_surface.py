"""Tripwire for the COPT / FMEA-modes router lift (2026-09-13).

Pins three contracts the lift must not silently undo:

1. Handler names stay importable from ``routers.results`` (``chat_tools``
   resolves ``get_copt`` / ``get_fmea_modes`` by name).
2. ``copt_endpoint`` never imports ``routers.*`` (layering).
3. The GET handlers stay thin — network/state + payload builder + HTTP map —
   so the COPT controller cannot quietly grow back into the results router.
"""
from __future__ import annotations

import ast
import importlib
import pathlib

import pytest

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_RESULTS = _BACKEND / "routers" / "results.py"
_ENDPOINT = _BACKEND / "services" / "adequacy" / "copt_endpoint.py"

_MAX_HANDLER_LOC = 40


def test_handlers_stay_on_the_router_surface():
    R = importlib.import_module("routers.results")
    for name in ("get_copt", "get_fmea_modes"):
        assert hasattr(R, name) and callable(getattr(R, name)), name


def test_endpoint_module_never_imports_routers():
    tree = ast.parse(_ENDPOINT.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("routers"), (
                f"{_ENDPOINT.name} imports {node.module}"
            )
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("routers"), (
                    f"{_ENDPOINT.name} imports {alias.name}"
                )


def _handler_loc(name: str) -> int:
    tree = ast.parse(_RESULTS.read_text())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node.end_lineno - node.lineno + 1
    raise AssertionError(f"{name} not found in results.py")


@pytest.mark.parametrize("name", ["get_copt", "get_fmea_modes"])
def test_get_handlers_stay_thin(name: str):
    loc = _handler_loc(name)
    assert loc <= _MAX_HANDLER_LOC, (
        f"{name} is {loc} lines (cap {_MAX_HANDLER_LOC})"
    )


def test_builders_are_defined_in_the_endpoint_module():
    mod = importlib.import_module("services.adequacy.copt_endpoint")
    for name in ("build_copt_payload", "build_fmea_modes_payload"):
        fn = getattr(mod, name, None)
        assert callable(fn), name
        assert fn.__module__ == "services.adequacy.copt_endpoint"
