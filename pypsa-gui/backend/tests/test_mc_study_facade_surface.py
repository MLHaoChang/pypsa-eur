"""
Tripwire for the MC study router lift (2026-09-13).

Pins three contracts the lift must not silently undo:

1. Handler name + request models stay importable from ``routers.results``
   (``services/chat_tools.py`` and the endpoint suites import them there).
2. The runner never imports ``routers.*`` (layering).
3. The POST handler stays thin — mesh refuse + runner call — so the FMEA
   controller cannot quietly grow back into the results router.
"""
from __future__ import annotations

import ast
import importlib
import pathlib


_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_RESULTS = _BACKEND / "routers" / "results.py"
_MC_RUNNER = _BACKEND / "services" / "adequacy" / "mc_loop_runner.py"

# Hard ceiling: a thin handler is mesh refuse + imports + the runner call.
# Today's measured size is ~35; leave headroom for comment edits.
_MAX_HANDLER_LOC = 80


def test_handlers_and_models_stay_on_the_router_surface():
    R = importlib.import_module("routers.results")
    for name in ("post_mc", "McRequest", "McElccAsset"):
        assert hasattr(R, name), name


def test_runner_never_imports_routers():
    tree = ast.parse(_MC_RUNNER.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("routers"), (
                f"{_MC_RUNNER.name} imports {node.module}"
            )
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("routers"), (
                    f"{_MC_RUNNER.name} imports {alias.name}"
                )


def _handler_loc(name: str) -> int:
    tree = ast.parse(_RESULTS.read_text())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node.end_lineno - node.lineno + 1
    raise AssertionError(f"{name} not found in results.py")


def test_post_mc_stays_thin():
    loc = _handler_loc("post_mc")
    assert loc <= _MAX_HANDLER_LOC, (
        f"post_mc is {loc} lines (cap {_MAX_HANDLER_LOC})"
    )


def test_runner_exports_start_mc():
    mc = importlib.import_module("services.adequacy.mc_loop_runner")
    assert callable(mc.start_mc)
