"""
Tripwire for the planning-loop router lift (2026-09-13).

Pins three contracts the lift must not silently undo:

1. Handler names + request models stay importable from ``routers.results``
   (``services/chat_tools.py`` and the endpoint suites import them there).
2. The runners never import ``routers.*`` (layering).
3. The POST handlers stay thin — mesh refuse + runner call — so the FMEA
   controllers cannot quietly grow back into the results router.
"""
from __future__ import annotations

import ast
import importlib
import pathlib

import pytest


_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_RESULTS = _BACKEND / "routers" / "results.py"
_COUPLING_RUNNER = _BACKEND / "services" / "adequacy" / "coupling_loop_runner.py"
_MARGIN_RUNNER = _BACKEND / "services" / "adequacy" / "margin_loop_runner.py"

# Hard ceiling: a thin handler is mesh refuse + imports + the runner call.
# Today's measured sizes are 31 and 36; leave headroom for comment edits.
_MAX_HANDLER_LOC = 80


def test_handlers_and_models_stay_on_the_router_surface():
    R = importlib.import_module("routers.results")
    for name in (
        "post_coupling_loop",
        "post_margin_loop",
        "CouplingLoopRequest",
        "MarginLoopRequest",
        "MARGIN_LOOP_PANEL_LABEL",
        "LOOP_WARNING_V1",
        "MARGIN_LOOP_WARNING_V1",
        "UNREACHABLE_COPY_V1",
        "NEVER_BOUND_COPY_V1",
        "PROBE_MARGIN",
    ):
        assert hasattr(R, name), name


def test_runners_never_import_routers():
    for path in (_COUPLING_RUNNER, _MARGIN_RUNNER):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("routers"), (
                    f"{path.name} imports {node.module}"
                )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("routers"), (
                        f"{path.name} imports {alias.name}"
                    )


def _handler_loc(name: str) -> int:
    tree = ast.parse(_RESULTS.read_text())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node.end_lineno - node.lineno + 1
    raise AssertionError(f"{name} not found in results.py")


@pytest.mark.parametrize("name", ["post_coupling_loop", "post_margin_loop"])
def test_post_handlers_stay_thin(name: str):
    loc = _handler_loc(name)
    assert loc <= _MAX_HANDLER_LOC, f"{name} is {loc} lines (cap {_MAX_HANDLER_LOC})"


def test_runners_export_start_functions():
    coupling = importlib.import_module("services.adequacy.coupling_loop_runner")
    margin = importlib.import_module("services.adequacy.margin_loop_runner")
    assert callable(coupling.start_coupling_loop)
    assert callable(margin.start_margin_loop)
