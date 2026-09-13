"""
Tripwire for the study-runner lifts (MC + frontier + FMEA sweep).

Pins three contracts the lifts must not silently undo:

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
_MC_RUNNER = _BACKEND / "services" / "adequacy" / "mc_loop_runner.py"
_FRONTIER_RUNNER = _BACKEND / "services" / "adequacy" / "frontier_loop_runner.py"
_FMEA_SWEEP_RUNNER = _BACKEND / "services" / "adequacy" / "fmea_sweep_runner.py"

_RUNNERS = (_MC_RUNNER, _FRONTIER_RUNNER, _FMEA_SWEEP_RUNNER)

# Hard ceiling: a thin handler is mesh refuse + imports + the runner call.
# Today's measured sizes are ~19–31; leave headroom for comment edits.
_MAX_HANDLER_LOC = 80


def test_handlers_and_models_stay_on_the_router_surface():
    R = importlib.import_module("routers.results")
    for name in (
        "post_mc",
        "post_frontier",
        "post_fmea_sweep",
        "McRequest",
        "McElccAsset",
        "FrontierRequest",
        "FmeaSweepRequest",
    ):
        assert hasattr(R, name), name


def test_runners_never_import_routers():
    for path in _RUNNERS:
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


@pytest.mark.parametrize(
    "name", ["post_mc", "post_frontier", "post_fmea_sweep"],
)
def test_post_handlers_stay_thin(name: str):
    loc = _handler_loc(name)
    assert loc <= _MAX_HANDLER_LOC, (
        f"{name} is {loc} lines (cap {_MAX_HANDLER_LOC})"
    )


def test_runners_export_start_functions():
    mc = importlib.import_module("services.adequacy.mc_loop_runner")
    frontier = importlib.import_module("services.adequacy.frontier_loop_runner")
    sweep = importlib.import_module("services.adequacy.fmea_sweep_runner")
    assert callable(mc.start_mc)
    assert callable(frontier.start_frontier)
    assert callable(sweep.start_fmea_sweep)
