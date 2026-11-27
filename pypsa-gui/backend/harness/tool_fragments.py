"""
Per-tool registry fragments for the chat catalogue (RETRO-FRAG-1, issue 02).

A chat tool is a PAIR of modules with the same stem, so two PRs that each add
a tool touch no common file:

- ``harness/catalogue_tools/<stem>.py``: data only. ``TOOLS`` (entries built
  with ``harness.catalogue_base._t``), ``ROUTES`` (name -> the TOOL_ROUTES
  value), optionally ``NETWORK_WRITES`` / ``NON_NETWORK_WRITES`` (service-call
  tools, see ``catalogue.tool_edits_network``) and ``ORDER``.
- ``services/chat_dispatchers/<stem>.py``: ``DISPATCHERS`` (name -> callable)
  and optionally ``PRE_DISPATCH_VALIDATORS``.

Both packages are discovered at import (``pkgutil.iter_modules``). The schema
fragments are merged by ``harness/catalogue.py``; the dispatcher fragments by
``services/chat_tools.py`` BEFORE its lock gate, study gate and undo capture
wrap ``DISPATCHERS``, so a fragment's tool is gated like any other.

Order (``wiring._bounded_tools`` breaks selection ties by TOOLS index, and a
stable order keeps provider prefix caches warm): a family moved out of the
monolith is spliced back where it was with ``*_fragment("<stem>")`` and
declares no ORDER. Every other fragment declares ``ORDER = YYYYMMDDNN`` (the
day the tool was added, then a counter) and is appended after the monolith in
ORDER. A clash, a duplicate name or key, or an unpaired module refuses the
import with both module names.
"""

from __future__ import annotations

import importlib
import pkgutil
import sys
from types import ModuleType
from typing import Any

SCHEMA_PACKAGE = "harness.catalogue_tools"
DISPATCH_PACKAGE = "services.chat_dispatchers"
LEGACY_SCHEMA = "harness.catalogue"
LEGACY_DISPATCH = "services.chat_tools"


class FragmentError(ImportError):
    """A fragment that cannot be merged; the message names the modules."""


def discover(package: str) -> dict[str, ModuleType]:
    """Import every non-underscore module of ``package``; stem -> module, sorted."""
    pkg = importlib.import_module(package)
    stems = sorted(
        m.name for m in pkgutil.iter_modules(pkg.__path__) if not m.name.startswith("_")
    )
    if not stems and getattr(sys, "frozen", False):
        raise FragmentError(
            f"{package}: no fragment modules found in the frozen app; "
            "pypsa-gui.spec must collect the package (collect_submodules)"
        )
    return {s: importlib.import_module(f"{package}.{s}") for s in stems}


class SchemaFragments:
    """The catalogue side: splice, append in ORDER, merge the side tables."""

    def __init__(self, modules: dict[str, ModuleType]):
        self.modules = modules
        self.spliced: set[str] = set()
        # The fragments' NETWORK_WRITES, which chat_tools adds to the service-call
        # network mutators its lock and study gates cover (set by ``merge``).
        self.network_writes: frozenset[str] = frozenset()

    def splice(self, stem: str) -> list[dict[str, Any]]:
        """The TOOLS of a family moved out of the monolith, for its old place."""
        mod = self.modules.get(stem)
        if mod is None:
            raise FragmentError(
                f"{LEGACY_SCHEMA} splices {SCHEMA_PACKAGE}.{stem}, which was not found"
            )
        if hasattr(mod, "ORDER"):
            raise FragmentError(
                f"{mod.__name__} is spliced by {LEGACY_SCHEMA} and also declares ORDER; "
                "a spliced fragment keeps its place, so drop one"
            )
        if stem in self.spliced:
            raise FragmentError(f"{LEGACY_SCHEMA} splices {mod.__name__} twice")
        self.spliced.add(stem)
        return list(mod.TOOLS)

    def appended(self) -> list[ModuleType]:
        """The fragments not spliced, in ORDER; a missing or shared ORDER refuses."""
        seen: dict[int, str] = {}
        rest = [m for s, m in self.modules.items() if s not in self.spliced]
        for mod in rest:
            order = getattr(mod, "ORDER", None)
            if not isinstance(order, int) or isinstance(order, bool):
                raise FragmentError(
                    f"{mod.__name__} is not spliced by {LEGACY_SCHEMA}, so it must "
                    "declare ORDER = YYYYMMDDNN (an int)"
                )
            if order in seen:
                raise FragmentError(
                    f"ORDER {order} is declared by both {seen[order]} and {mod.__name__}; "
                    "take the next free counter"
                )
            seen[order] = mod.__name__
        return sorted(rest, key=lambda m: m.ORDER)

    def extend(self, tools: list[dict[str, Any]]) -> None:
        """Append the fragments not spliced to ``tools``, in ORDER."""
        for mod in self.appended():
            tools.extend(mod.TOOLS)

    def merge(
        self,
        tools: list[dict[str, Any]],
        routes: dict[str, Any],
        network_writes: frozenset[str],
        non_network_writes: frozenset[str],
    ) -> tuple[frozenset[str], frozenset[str]]:
        """Check names are unique and merge the fragments' tables (``routes`` in place).

        Returns the two service-call write sets with the fragments' members added.
        """
        origin = self._origins()
        seen: dict[str, str] = {}
        for t in tools:
            name, where = t["name"], origin.get(id(t), LEGACY_SCHEMA)
            if name in seen:
                raise FragmentError(
                    f"tool {name!r} is defined by both {seen[name]} and {where}"  # repr-ok: import-time error for the developer
                )
            seen[name] = where
        for mod in self.modules.values():
            names = {t["name"] for t in mod.TOOLS}
            route_keys = set(getattr(mod, "ROUTES", {}))
            if route_keys != names:
                raise FragmentError(
                    f"{mod.__name__}: ROUTES keys {sorted(route_keys)} must equal its "
                    f"TOOLS names {sorted(names)}"
                )
            _merge(routes, mod.ROUTES, LEGACY_SCHEMA, mod.__name__, "route key")
        self.network_writes = frozenset(
            n for m in self.modules.values() for n in getattr(m, "NETWORK_WRITES", ())
        )
        network_writes = _merge_set(network_writes, "NETWORK_WRITES", self.modules)
        non_network_writes = _merge_set(
            non_network_writes, "NON_NETWORK_WRITES", self.modules
        )
        return network_writes, non_network_writes

    def _origins(self) -> dict[int, str]:
        return {id(t): m.__name__ for m in self.modules.values() for t in m.TOOLS}


def merge_dispatchers(
    dispatchers: dict[str, Any],
    validators: dict[str, Any],
    modules: dict[str, ModuleType],
    schema_modules: dict[str, ModuleType],
) -> None:
    """Merge the dispatcher fragments in place, after checking each pairs its schema."""
    for stem in sorted(set(modules) | set(schema_modules)):
        mod, schema = modules.get(stem), schema_modules.get(stem)
        if mod is None or schema is None:
            have = (mod or schema).__name__
            want = f"{DISPATCH_PACKAGE if mod is None else SCHEMA_PACKAGE}.{stem}"
            raise FragmentError(f"{have} has no pair: {want} is missing")
        names = {t["name"] for t in schema.TOOLS}
        if set(mod.DISPATCHERS) != names:
            raise FragmentError(
                f"{mod.__name__} DISPATCHERS {sorted(mod.DISPATCHERS)} must equal "
                f"{schema.__name__} TOOLS {sorted(names)}"
            )
        _merge(
            dispatchers, mod.DISPATCHERS, LEGACY_DISPATCH, mod.__name__, "dispatcher"
        )
        _merge(
            validators,
            getattr(mod, "PRE_DISPATCH_VALIDATORS", {}),
            LEGACY_DISPATCH,
            mod.__name__,
            "pre-dispatch validator",
        )


def _merge(
    into: dict[str, Any], new: dict[str, Any], legacy: str, where: str, what: str
) -> None:
    for key, value in new.items():
        if key in into:
            raise FragmentError(
                f"{what} {key!r} is defined by both {legacy} and {where}"  # repr-ok: import-time error for the developer
            )
        into[key] = value


def _merge_set(
    base: frozenset[str], attr: str, modules: dict[str, ModuleType]
) -> frozenset[str]:
    out = set(base)
    for mod in modules.values():
        extra = set(getattr(mod, attr, ()))
        if not extra <= {t["name"] for t in mod.TOOLS}:
            raise FragmentError(
                f"{mod.__name__}: {attr} names a tool it does not define"
            )
        clash = out & extra
        if clash:
            raise FragmentError(
                f"{attr} {sorted(clash)} is declared by both {LEGACY_SCHEMA} and {mod.__name__}"
            )
        out |= extra
    return frozenset(out)


def dispatcher_source_tree():
    """One AST of `services/chat_tools.py` plus every dispatcher fragment.

    For the tests that read the dispatchers' source (argshape, imports, handler
    dependencies): a dispatcher moved into a fragment stays under their scan.
    """
    import ast
    from pathlib import Path

    services = Path(__file__).resolve().parents[1] / "services"
    files = [
        services / "chat_tools.py",
        *sorted((services / "chat_dispatchers").glob("[!_]*.py")),
    ]
    body: list = []
    for path in files:
        body.extend(
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path)).body
        )
    return ast.Module(body=body, type_ignores=[])
