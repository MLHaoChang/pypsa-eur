"""
The import-surface tripwire for Phase 3 — moving the `routers/compare.py`
engine into `services/compare/`.

Who depends on `routers.compare` and how:

- `services/chat_tools.py` imports `get_compare_state` / `get_results_summary`.
- `routers/results.py` lazily imports `_compute_economics_summary` and
  `_build_snapshot_weights`.
- Tests import `_compute_capacity_summary`, `_compute_total_annuitised_capex`,
  `_periodized_lookup`, `_safe_capital_cost`, `_build_snapshot_weights` by
  name, and call all nine `_compute_*_summary` functions POSITIONALLY as
  `CMP._compute_x(n, periods, is_multi, has_solve)`.

So every one of those names stays on the router with its positional parameter
list unchanged (a snapshot of `master` below). Pure functions are re-exported
— the router name IS the service object. The four that used to reach for
router state are wrapped instead — the router name is a thin function that
resolves the state and calls the service, whose signature is a superset.
"""
import importlib
import inspect
import pathlib
import re

import pytest

import routers.compare as CMP


# name -> positional parameter names on `master`.
_SURFACE: dict[str, list[str]] = {
    "get_compare_state": ["project"],
    "get_results_summary": ["project"],
    "_read_lost_load_capture": ["project_dir"],
    "_bucket_add": ["d", "key", "value", "period"],
    "_bucket_replicate_per_period": ["d", "key", "value", "periods"],
    "_to_pv_dict": ["d"],
    "_to_pv": ["d"],
    "_periodized_lookup": ["n"],
    "_safe_capital_cost": ["row", "pcc", "comp_attr"],
    "_classify_build_year": ["value"],
    "_build_snapshot_weights": ["n", "column"],
    "_per_period_groupby": ["series", "sns", "is_multi"],
    "_co2_intensity_map": ["n"],
    "_compute_capacity_summary": ["n", "periods", "is_multi", "has_solve"],
    "_compute_total_annuitised_capex": ["n", "periods", "is_multi", "years_map", "pcc"],
    "_compute_dispatch_summary": ["n", "periods", "is_multi", "has_solve"],
    "_compute_loading_summary": ["n", "periods", "is_multi", "has_solve"],
    "_compute_prices_summary": ["n", "periods", "is_multi", "has_solve"],
    "_compute_emissions_summary": ["n", "periods", "is_multi", "has_solve"],
    "_compute_economics_summary": ["n", "periods", "is_multi", "has_solve", "prices_from_state", "lost_load_cap"],
    "_compute_curtailment_summary": ["n", "periods", "is_multi", "has_solve"],
    "_compute_lost_load_summary": ["project_dir", "n", "periods", "is_multi", "has_solve"],
    "_compute_storage_cycling_summary": ["n", "periods", "is_multi", "has_solve"],
}

# Pure moves: the router re-exports the very same object.
_REEXPORTED: dict[str, str] = {
    "_bucket_add": "services.compare.support",
    "_bucket_replicate_per_period": "services.compare.support",
    "_to_pv_dict": "services.compare.support",
    "_to_pv": "services.compare.support",
    "_safe_capital_cost": "services.compare.support",
    "_classify_build_year": "services.compare.support",
    "_build_snapshot_weights": "services.compare.support",
    "_per_period_groupby": "services.compare.support",
    "_co2_intensity_map": "services.compare.support",
    "_compute_total_annuitised_capex": "services.compare.capacity",
    "_compute_loading_summary": "services.compare.loading",
    "_compute_prices_summary": "services.compare.prices",
    "_compute_emissions_summary": "services.compare.emissions",
    "_compute_curtailment_summary": "services.compare.curtailment",
    "_compute_storage_cycling_summary": "services.compare.storage_cycling",
}

# Wrapped: router name resolves state, service name takes it as keyword-only.
# router name -> (service module, service name, extra keyword-only params)
_WRAPPED: dict[str, tuple[str, str, list[str]]] = {
    "_periodized_lookup": ("services.compare.support", "_periodized_lookup", ["cfg"]),
    "_compute_capacity_summary": ("services.compare.capacity", "_compute_capacity_summary", ["cfg"]),
    "_compute_dispatch_summary": ("services.compare.dispatch", "_compute_dispatch_summary", ["cfg"]),
    "_compute_economics_summary": ("services.compare.economics", "_compute_economics_summary", ["cfg", "result_df"]),
    "_compute_lost_load_summary": ("services.compare.lost_load", "compute_lost_load_summary", []),
}


def _positional(fn) -> list[str]:
    return [
        p.name for p in inspect.signature(fn).parameters.values()
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]


@pytest.mark.parametrize("name,params", sorted(_SURFACE.items()))
def test_every_router_name_keeps_its_positional_parameters(name, params):
    fn = getattr(CMP, name, None)
    assert callable(fn), f"routers.compare.{name} is gone"
    assert _positional(fn) == params, f"{name}: {_positional(fn)} != {params}"


@pytest.mark.parametrize("name,origin", sorted(_REEXPORTED.items()))
def test_pure_moves_are_reexported_as_the_same_object(name, origin):
    svc = getattr(importlib.import_module(origin), name)
    assert getattr(CMP, name) is svc, f"routers.compare.{name} is not {origin}.{name}"
    assert svc.__module__ == origin


@pytest.mark.parametrize("name,target", sorted(_WRAPPED.items()))
def test_wrapped_names_delegate_to_a_service_with_the_state_as_keywords(name, target):
    module_name, svc_name, extra = target
    svc = getattr(importlib.import_module(module_name), svc_name)
    router_fn = getattr(CMP, name)
    assert router_fn is not svc, f"{name} should be a wrapper, not a re-export"
    assert router_fn.__module__ == "routers.compare"
    sig = inspect.signature(svc)
    for kw in extra:
        p = sig.parameters.get(kw)
        assert p is not None and p.kind is p.KEYWORD_ONLY, (
            f"{module_name}.{svc_name} must take `{kw}` keyword-only"
        )
        assert kw not in inspect.signature(router_fn).parameters, (
            f"routers.compare.{name} must keep the pre-move signature (no `{kw}`)"
        )


def test_the_lost_load_service_takes_the_capture_not_a_directory():
    """
    The router reads the capture through the projects router's restricted
    unpickler and hands the DICT to the service. A same-named service function
    whose first positional argument silently meant something else would be a
    trap, hence the different name.
    """
    from services.compare.lost_load import compute_lost_load_summary

    assert _positional(compute_lost_load_summary) == ["cap", "n", "periods", "is_multi", "has_solve"]
    assert not hasattr(importlib.import_module("services.compare.lost_load"), "_compute_lost_load_summary")


def test_services_compare_never_imports_routers():
    pkg = pathlib.Path(__file__).resolve().parent.parent / "services" / "compare"
    assert pkg.is_dir()
    pat = re.compile(r"^\s*(from\s+routers[\s.]|import\s+routers\b)")
    offenders = [
        f"{p.name}:{i}: {line.strip()}"
        for p in sorted(pkg.glob("*.py"))
        for i, line in enumerate(p.read_text().splitlines(), 1)
        if pat.match(line)
    ]
    assert not offenders, "services/compare imports a router:\n  " + "\n  ".join(offenders)


def test_the_package_reexports_nothing():
    import ast

    init = pathlib.Path(__file__).resolve().parent.parent / "services" / "compare" / "__init__.py"
    stmts = [type(s).__name__ for s in ast.parse(init.read_text()).body
             if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
    assert stmts == [], f"services/compare/__init__.py contains {stmts}"
