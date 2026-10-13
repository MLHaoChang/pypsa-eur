"""
U2 WP10 acceptance — one engine: the guided study reaches the Investment Case
engine through its two seams only, and GS's own engine is gone.

Plan: docs/superpowers/plans/2026-10-05-guided-study-u2-engine-rewire.md,
WP10 "Acceptance" (and §2: "the adapter is the only GS module that imports IC
services; `compile.py` imports IC models plus the defaults-pack loader and the
export-series helper").

* Every import of an engine module (`services.commercial`, `services.finance`,
  `services.library`, `services.asset_schema`, and the results modules
  `finance_case`, `billing`, `value_flows`) by a study module or the study
  routes sits in `services/study/engine_adapter.py` or `compile.py`.
* Every engine name those two use is a frozen facade name
  (`tests/test_engine_facade_frozen.py`) or a WORKAROUND listed below with the
  engine ask that would retire it.
* Nothing imports GS's deleted engine: `BillCalculator`, the pro forma
  (`services/study/proforma.py`, `proforma_xlsx.py`), `services/study/
  tariff.py`, `_wrap_with_demand_charge`, or `SolverConfig.demand_charge`.

The scans read the source with `ast` (lazy imports inside functions
included), so a module that would fail to import is still caught.
"""
from __future__ import annotations

import ast
import dataclasses
import importlib
import pathlib

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
STUDY = BACKEND / "services" / "study"
SEAMS = {"services/study/engine_adapter.py", "services/study/compile.py"}
ENGINE = ("services.commercial", "services.finance", "services.library",
          "services.asset_schema", "services.results.finance_case",
          "services.results.billing", "services.results.value_flows")

#: Engine names outside the frozen facade the seams use, each a WORKAROUND
#: with the engine ask (to IC through the owner) that would retire it.
ALLOWED_EXCEPTIONS: dict[str, str] = {
    # Q15: the pure binder and the Library series resolver are not frozen
    # (only `bind_commercial_on_context`, which binds a LOADED context).
    "services.commercial.binding.bind_commercial": "Q15",
    "services.commercial.binding.BindingRefusal": "Q15",
    "services.library.series_store.resolve": "Q15",
    "services.library.series_store.LibraryRefNotFound": "Q15",
    "services.library.series_store.LibraryRefStale": "Q15",
    # The study-owned export series' delete (owner decision 2026-10-05, WP6
    # gate W1): `series_store.delete_series` / `pinned_by` asked for.
    "services.library.series_store.list_series": "delete_series/pinned_by",
    "services.library.bundle_pins.read_pins": "delete_series/pinned_by",
    "services.library.bundle_pins.collect_refs": "delete_series/pinned_by",
    # WP7 freeze ask: names `engine_adapter` reads outside the facade.
    "services.finance.engine.wacc_gate": "WP7 freeze (wacc_gate)",
    "services.commercial.lp_bindings.META_LINKS": "WP7 freeze (META_LINKS)",
    # WP7 N15: the asset schema's writer and reader (S0's, not frozen).
    "services.asset_schema.derive.apply_parts": "N15 (derive.apply_parts)",
    "services.asset_schema.derive.has_parts": "N15 (derive.has_parts)",
}


def _study_sources() -> list[tuple[str, pathlib.Path]]:
    out = [(str(p.relative_to(BACKEND)), p) for p in sorted(STUDY.glob("*.py"))]
    out.append(("routers/studies.py", BACKEND / "routers" / "studies.py"))
    return out


def _is_module(dotted: str) -> bool:
    try:
        importlib.import_module(dotted)
    except ImportError:
        return False
    return True


def _engine_names(path: pathlib.Path) -> list[tuple[int, str]]:
    """
    `(line, dotted name)` of every engine name a file uses: `from M import x`
    is `M.x`; when `M.x` is itself a module, each `x.attr` the file reads is
    `M.x.attr` instead; `import M` is `M` with its attributes the same way.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: dict[str, str] = {}          # local name -> dotted module
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for a in node.names:
                full = f"{node.module}.{a.name}"
                if not full.startswith(ENGINE):
                    continue
                if _is_module(full):
                    modules[a.asname or a.name] = full
                else:
                    out.append((node.lineno, full))
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith(ENGINE):
                    modules[a.asname or a.name.split(".")[0]] = a.name
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                and node.value.id in modules:
            out.append((node.lineno, f"{modules[node.value.id]}.{node.attr}"))
    return sorted(set(out))


def _facade() -> set[str]:
    from tests import test_engine_facade_frozen as F

    return set(F.SIGNATURES) | set(F.DATACLASS_FIELDS)


def test_the_study_reaches_the_engine_only_through_its_two_seams():
    outside = {}
    for rel, path in _study_sources():
        if rel in SEAMS:
            continue
        names = _engine_names(path)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        mods = [f"{n.lineno}:{n.module}" for n in ast.walk(tree)
                if isinstance(n, ast.ImportFrom) and n.module
                and (n.module.startswith(ENGINE) or any(
                    f"{n.module}.{a.name}".startswith(ENGINE) for a in n.names))]
        if names or mods:
            outside[rel] = sorted({f"{ln}:{x}" for ln, x in names} | set(mods))
    assert outside == {}


def test_the_seams_use_only_facade_names_or_listed_workarounds():
    facade = _facade()
    unlisted = {}
    used: set[str] = set()
    for rel in sorted(SEAMS):
        for line, name in _engine_names(BACKEND / rel):
            used.add(name)
            if name not in facade and name not in ALLOWED_EXCEPTIONS:
                unlisted.setdefault(rel, []).append(f"{line}:{name}")
    assert unlisted == {}
    # Every listed exception is still used (a retired WORKAROUND leaves the list).
    assert sorted(set(ALLOWED_EXCEPTIONS) - used) == []
    assert all(ALLOWED_EXCEPTIONS.values())


def test_the_scan_reads_the_import_forms_the_seams_use(tmp_path):
    """The scan's own guard: module imports with attributes, names, aliases."""
    probe = tmp_path / "probe.py"
    probe.write_text(
        "from services.commercial import binding\n"
        "from services.finance.engine import run_case, wacc_gate as wg\n"
        "import services.library.series_store as ss\n"
        "def f(n):\n"
        "    binding.bind_commercial(n)\n"
        "    return ss.resolve(n), run_case, wg\n", encoding="utf-8")
    assert {x for _l, x in _engine_names(probe)} == {
        "services.commercial.binding.bind_commercial", "services.finance.engine.run_case",
        "services.finance.engine.wacc_gate", "services.library.series_store.resolve"}


# ── GS's own engine is gone ───────────────────────────────────────────────

DELETED_MODULES = ("services.study.tariff", "services.study.proforma",
                   "services.study.proforma_xlsx")
DELETED_NAMES = ("BillCalculator", "_wrap_with_demand_charge", "build_investment_case",
                 "write_tariff_prices", "demand_charge_config", "record_solved_demand_charge",
                 "DemandChargeRefused")
#: The one file that names the deleted code ON PURPOSE: the record of how the
#: stored pre-U2 fork fixture was written (it ran once, before WP10).
RECORDS = {"tests/fixtures/pre_u2_fork/make_pre_u2_fork.py"}


def _python_sources():
    for top in ("services", "routers", "models", "tests", "smoke"):
        for p in sorted((BACKEND / top).rglob("*.py")):
            rel = str(p.relative_to(BACKEND))
            if "__pycache__" in rel or rel in RECORDS or rel == "tests/test_study_one_engine.py":
                continue
            yield rel, p
    yield "main.py", BACKEND / "main.py"


def _gs_engine_uses(path: pathlib.Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            mods = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            hits += [f"{node.lineno}:import {m}" for m in mods if m in DELETED_MODULES]
            hits += [f"{node.lineno}:import {a.name}" for a in node.names
                     if a.name in DELETED_NAMES]
        elif isinstance(node, ast.Import):
            hits += [f"{node.lineno}:import {a.name}" for a in node.names
                     if a.name in DELETED_MODULES]
        elif isinstance(node, ast.Name) and node.id in DELETED_NAMES:
            hits.append(f"{node.lineno}:{node.id}")
        elif isinstance(node, ast.Attribute) and node.attr in DELETED_NAMES:
            hits.append(f"{node.lineno}:.{node.attr}")
        elif isinstance(node, ast.Attribute) and node.attr == "demand_charge" \
                and isinstance(node.value, ast.Name) and node.value.id in (
                    "cfg", "config", "solver_config", "gcfg", "SolverConfig"):
            hits.append(f"{node.lineno}:{node.value.id}.demand_charge")
        elif isinstance(node, ast.Call) and _callee(node) in CONFIG_CALLS:
            # `SolverConfig(demand_charge=...)`; a guided TARIFF form's own
            # `demand_charge` (`Tariff.demand_charge`) is another field.
            hits += [f"{node.lineno}:{_callee(node)}(demand_charge=)" for k in node.keywords
                     if k.arg == "demand_charge"]
    return hits


CONFIG_CALLS = {"SolverConfig", "SolverConfigSchema", "solver_config", "option_solver_config"}


def _callee(call: ast.Call) -> str | None:
    f = call.func
    return f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else None)


def test_nothing_imports_the_deleted_gs_engine():
    for mod in DELETED_MODULES:
        assert not (BACKEND / (mod.replace(".", "/") + ".py")).exists(), mod
    found = {rel: hits for rel, p in _python_sources() if (hits := _gs_engine_uses(p))}
    assert found == {}


def test_solver_config_has_no_demand_charge_field():
    from models.schemas import SolverConfigSchema
    from services.solver_service import SolverConfig

    assert "demand_charge" not in {f.name for f in dataclasses.fields(SolverConfig)}
    assert "demand_charge" not in SolverConfigSchema.model_fields


@pytest.mark.parametrize("snippet", [
    "from services.study.tariff import BillCalculator\n",
    "from services.study import proforma\n",
    "def f():\n    from services.solver.objective import _wrap_with_demand_charge\n",
    "x = cfg.demand_charge\n",
    "SolverConfig(demand_charge={})\n",
])
def test_the_gs_engine_scan_sees_each_form(tmp_path, snippet):
    probe = tmp_path / "probe.py"
    probe.write_text(snippet, encoding="utf-8")
    assert _gs_engine_uses(probe)
