"""
Edge Investment Case — dependency tripwires (Phase 0, WP0.6).

Plan: docs/superpowers/plans/2026-09-26-edge-investment-case-p0-p1.md WP0.6

The investment-case packages sit BELOW the routers and BESIDE the solver:
  * `services/commercial/**`, `services/finance/**`, `services/library/**`
    never import `routers.*` and never import `services.solver_service`
    (they take solved networks and plain arguments; `solver_service` is the
    façade the ROUTERS use, and importing it from a leaf would pull the whole
    solve stack into a pure cashflow module);
  * each package `__init__.py` is a docstring and nothing else, so there is no
    second import surface to drift from the modules themselves;
  * results-side seams added by this programme live in `services/results/` and
    obey the same no-router rule as the other `compute_*` modules.

AST-based, not regex-based, so an import inside a function body or a
`from x import (a,\n b)` block cannot slip past, and prose that merely names
a module in a docstring is not an offender.

Packages that do not exist yet are skipped individually; `services/finance`
exists from WP0.2 on, so the test can never pass vacuously.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

_SERVICES = pathlib.Path(__file__).resolve().parent.parent / "services"
IC_PACKAGES = ("commercial", "finance", "library")
# Results-side modules this programme adds (P0: the physical-quantity seam).
IC_RESULTS_MODULES = ("physical_quantities",)
# Leaf packages must not reach the solve stack. `services.solver` (the carved
# modules) is equally off-limits: finance never runs a solve.
FORBIDDEN_PREFIXES = ("routers", "services.solver_service", "services.solver.")


def _imports(path: pathlib.Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(), filename=str(path))
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend((node.lineno, a.name) for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.append((node.lineno, node.module))
            # `from services import solver_service` names the module as an alias
            out.extend((node.lineno, f"{node.module}.{a.name}") for a in node.names)
    return out


def _forbidden(mod: str) -> bool:
    return any(mod == p.rstrip(".") or mod.startswith(p if p.endswith(".") else p + ".")
               or mod == p for p in FORBIDDEN_PREFIXES)


def _package_files(name: str) -> list[pathlib.Path]:
    pkg = _SERVICES / name
    if not pkg.is_dir():
        return []
    return sorted(pkg.rglob("*.py"))


def test_finance_package_exists_so_the_tripwire_is_not_vacuous():
    assert (_SERVICES / "finance" / "__init__.py").is_file()
    assert len(_package_files("finance")) >= 4


@pytest.mark.parametrize("name", IC_PACKAGES)
def test_ic_packages_never_import_routers_or_the_solver(name):
    files = _package_files(name)
    if not files:
        pytest.skip(f"services/{name}/ does not exist yet")
    offenders = [
        f"{p.relative_to(_SERVICES.parent)}:{ln}: {mod}"
        for p in files for ln, mod in _imports(p) if _forbidden(mod)
    ]
    assert not offenders, "forbidden imports:\n  " + "\n  ".join(offenders)


@pytest.mark.parametrize("name", IC_PACKAGES)
def test_ic_package_inits_are_docstring_only(name):
    inits = [p for p in _package_files(name) if p.name == "__init__.py"]
    if not inits:
        pytest.skip(f"services/{name}/ does not exist yet")
    for init in inits:
        body = ast.parse(init.read_text()).body
        assert len(body) == 1 and isinstance(body[0], ast.Expr) \
            and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str), (
                f"{init.relative_to(_SERVICES.parent)} must contain a docstring only")


@pytest.mark.parametrize("mod", IC_RESULTS_MODULES)
def test_ic_results_modules_never_import_routers(mod):
    path = _SERVICES / "results" / f"{mod}.py"
    assert path.is_file(), path
    offenders = [f"{ln}: {m}" for ln, m in _imports(path)
                 if m == "routers" or m.startswith("routers.")]
    assert not offenders, offenders


@pytest.mark.parametrize("mod", IC_RESULTS_MODULES)
def test_ic_results_public_functions_are_defined_in_their_module(mod):
    import importlib
    import inspect

    m = importlib.import_module(f"services.results.{mod}")
    public = [f for n, f in inspect.getmembers(m, inspect.isfunction)
              if not n.startswith("_") and f.__module__ == m.__name__]
    assert public, f"{mod} exposes no public function"
    for n, f in inspect.getmembers(m, inspect.isfunction):
        if n.startswith("compute_"):
            assert f.__module__ == m.__name__, f"{n} is re-exported, not defined, in {mod}"


def test_the_detector_catches_what_it_claims_to(tmp_path):
    """The AST walk must flag function-body imports, aliased module imports,
    and multi-line from-imports — and must not flag prose."""
    src = tmp_path / "probe.py"
    src.write_text(
        '"""Mentions routers.results and services.solver_service in prose."""\n'
        "def f():\n"
        "    import routers.results as R\n"
        "    return R\n"
        "from services import solver_service\n"
        "from services.solver_service import (\n"
        "    SolverConfig,\n"
        ")\n"
        "from services.period_utils import snapshot_weights\n"
    )
    hits = [m for _, m in _imports(src) if _forbidden(m)]
    assert "routers.results" in hits
    assert "services.solver_service" in hits
    assert not any(m.startswith("services.period_utils") for m in hits)
