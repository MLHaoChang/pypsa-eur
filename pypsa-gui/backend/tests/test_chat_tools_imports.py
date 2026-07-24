"""
Regression guard: every chat-tool delegation must import a handler that
actually EXISTS.

Each tool in `services/chat_tools.py` is a thin wrapper that lazily does
`from routers.<x> import <handler> as _h` (or `from services.<x> import ...`)
and calls it. When a router refactor RENAMES a handler (e.g.
`get_capabilities` -> `capabilities`, `list_all_timeseries` -> `list_timeseries`)
without updating the wrapper, the tool raises `ImportError` on the first line
of every call — the chatbot silently loses that capability and falls back to
worse data (this is exactly how the time-series tools started reporting loads
as "flat": the fetch threw, so the agent used the static scalar `p_set`).

This test walks chat_tools.py's AST, finds every `from routers.*/services.*/
models.* import NAME`, and asserts NAME resolves on the target module. (models.*
is in scope because wrappers also import Pydantic request models from there —
e.g. set_multi_period_snapshots imported a `MultiPeriodSnapshotConfig` that does
not exist.) It is a PURE
import-resolution check — no network, no PyPSA, no disk — so it is safe to run
concurrently with the disk-writing chat suites.

NOTE: this catches import-NAME drift, not argument-SHAPE drift. A handler that
was renamed AND had its signature changed (e.g. now takes a Pydantic body
instead of positional kwargs) will pass this test once the name is fixed but
still fail at call time with a TypeError. Those cases live in KNOWN_BROKEN
below until fixed properly.
"""
from __future__ import annotations

import ast
import importlib
import pathlib

import pytest

CHAT_TOOLS = pathlib.Path(__file__).resolve().parents[1] / "services" / "chat_tools.py"

# Delegations that are KNOWN to be broken by more than a simple rename — each
# needs an argument-shape or dependency fix, not just a corrected import name,
# so they are tracked here rather than silently "fixed" into subtly-wrong
# mutating tools. Keyed by (module, imported_name) -> what the real fix is.
# When you fix one, REMOVE it here — `test_known_broken_still_broken` will fail
# until you do, keeping this list honest.
# Empty: every previously-broken delegation has been fixed (the 7 entries that
# lived here — BulkUpdateRequest, ClusteringRequest, put_vintage_bounds, enqueue,
# MultiPeriodSnapshotConfig, get_project_network_meta, get_project_network_component
# — now import the correct handler/model, with arg-shapes/DI corrected). The main
# test below now guards the WHOLE delegation surface. If a future refactor breaks a
# delegation, add a (module, name) entry here with the real fix, and
# `test_known_broken_still_broken` will remind you to remove it once repaired.
KNOWN_BROKEN: dict[tuple[str, str], str] = {}


def _delegated_imports() -> list[tuple[int, str, str]]:
    """(lineno, module, name) for every routers.*/services.* from-import."""
    tree = ast.parse(CHAT_TOOLS.read_text(encoding="utf-8"))
    out: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and (
            node.module.startswith("routers.")
            or node.module.startswith("services.")
            or node.module.startswith("models.")
        ):
            for alias in node.names:
                out.append((node.lineno, node.module, alias.name))
    return out


def _resolves(module: str, name: str) -> bool:
    try:
        mod = importlib.import_module(module)
    except Exception:  # noqa: BLE001 — a module that won't import is also "broken"
        return False
    if hasattr(mod, name):
        return True
    # `from package import submodule` — the name is a submodule that may not yet
    # be bound as an attribute of the package. Importing it resolves the binding.
    try:
        importlib.import_module(f"{module}.{name}")
        return True
    except Exception:  # noqa: BLE001
        return False


def test_all_chat_tool_delegations_resolve() -> None:
    """Every delegated handler import resolves (except the KNOWN_BROKEN allow-list)."""
    broken = [
        (lineno, module, name)
        for lineno, module, name in _delegated_imports()
        if (module, name) not in KNOWN_BROKEN and not _resolves(module, name)
    ]
    assert not broken, (
        "chat_tools.py delegates to handler(s) that no longer exist (a router "
        "refactor likely renamed them). The chatbot raises ImportError on every "
        "call to these tools. Fix the import name or add to KNOWN_BROKEN:\n"
        + "\n".join(f"  chat_tools.py:{ln}  from {m} import {n}" for ln, m, n in broken)
    )


@pytest.mark.parametrize(
    "module,name", sorted(KNOWN_BROKEN) or [(None, None)], ids=lambda v: str(v),
)
def test_known_broken_still_broken(module, name) -> None:
    """Self-cleaning: once a KNOWN_BROKEN import resolves, remove it from the list."""
    if module is None:
        pytest.skip("KNOWN_BROKEN is empty — all chat-tool delegations resolve")
    if _resolves(module, name):
        pytest.fail(
            f"`from {module} import {name}` now RESOLVES — the underlying tool was "
            f"fixed. Remove ({module!r}, {name!r}) from KNOWN_BROKEN in this file "
            "(and confirm the call-site arguments match the handler signature)."
        )
