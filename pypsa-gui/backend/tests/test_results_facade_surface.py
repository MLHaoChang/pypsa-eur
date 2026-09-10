"""
The import-surface tripwire for Phase 2 — lifting `routers/results.py`.

Three contracts on `routers.results` are pinned here, because three different
consumers depend on them and none of them would fail at the seam:

1. **Handler names and parameter lists.** `services/chat_tools.py` resolves a
   results handler by NAME with `getattr(routers.results, name)` and then
   inspects `handler.__code__.co_varnames` for `"source"` to decide how to call
   it. Rename a handler, or a parameter, and the chat tool breaks at runtime
   with no import error anywhere. The table below is a snapshot of `master`.

2. **Names imported by other modules.** `routers/compare.py`,
   `services/asset_results/compute.py` and several tests import
   `_result_df`, `corrected_marginal_prices`, `lp_scaled_load_frame` and
   `get_cost_breakdown` by name from the router.

3. **Where the arithmetic lives now.** `_LIFTED` maps each lifted handler to the
   service function that carries its body. Filled in before the code moved,
   so it went red first.

Plus one layering rule stricter than Phase 1's: nothing under
`services/results/` imports anything under `routers/`.
"""
import importlib
import pathlib
import re

import pytest

import routers.results as R


# Snapshot of every route handler's positional parameter names on `master`.
# `services/chat_tools.py::get_results` reads these reflectively.
_HANDLER_PARAMS: dict[str, list[str]] = {
    "get_cost_breakdown": [],
    "get_objective_decomposition": [],
    "get_economics_by_carrier": [],
    "get_statistics": [],
    "get_generator_results": ["source", "from_", "to_"],
    "get_storage_dispatch_results": ["source", "from_", "to_"],
    "get_store_dispatch_results": ["source", "from_", "to_"],
    "get_store_energy_results": ["source", "from_", "to_"],
    "get_storage_results": ["source", "from_", "to_"],
    "get_line_results": ["source", "from_", "to_"],
    "get_link_results": ["source", "from_", "to_"],
    "get_lcoh": [],
    "get_ac_pf_status": [],
    "get_losses_summary": ["source"],
    "get_carrier_kpis": [],
    "get_emissions": ["source"],
    "get_transformer_results": ["source", "from_", "to_"],
    "get_unit_commitment": ["from_", "to_"],
    "get_line_duals": [],
    "get_voltages": ["source", "from_", "to_"],
    "get_line_reactive": ["source", "from_", "to_"],
    "get_transformer_reactive": ["source", "from_", "to_"],
    "get_prices": ["source", "from_", "to_"],
    "get_price_drivers": ["threshold", "limit"],
    "get_curtailment": ["from_", "to_"],
    "get_lost_load": ["from_", "to_"],
    "get_load_results": ["source", "from_", "to_"],
    "get_asset_economics": [],
}

# Non-route names other modules import from `routers.results`.
_EXPORTED_HELPERS = ("_result_df", "corrected_marginal_prices", "lp_scaled_load_frame")

# handler -> (service module, compute function). The body of the handler lives
# in the compute function; the handler keeps the network lookup, the
# `_dispatch_ready` gate, the `_state` reads and the None -> 204 mapping.
_LIFTED: dict[str, tuple[str, str]] = {
    "get_cost_breakdown": ("services.results.cost_breakdown", "compute_cost_breakdown"),
    "get_asset_economics": ("services.results.asset_economics", "compute_asset_economics"),
    "get_emissions": ("services.results.emissions", "compute_emissions"),
    "get_lcoh": ("services.results.lcoh", "compute_lcoh"),
    "get_carrier_kpis": ("services.results.carrier_kpis", "compute_carrier_kpis"),
    "get_prices": ("services.results.prices", "compute_prices"),
    "get_price_drivers": ("services.results.prices", "compute_price_drivers"),
    "get_line_duals": ("services.results.line_duals", "compute_line_duals"),
    "get_curtailment": ("services.results.curtailment", "compute_curtailment"),
    "get_unit_commitment": ("services.results.unit_commitment", "compute_unit_commitment"),
    "get_statistics": ("services.results.statistics", "compute_statistics"),
    "get_load_results": ("services.results.loads", "compute_load_results"),
}

# The two shared helpers move to services with a `result_df` keyword; the
# router keeps same-signature wrappers so compare.py and asset_results are
# untouched.
_LIFTED_HELPERS: dict[str, tuple[str, str]] = {
    "lp_scaled_load_frame": ("services.results.load_frames", "lp_scaled_load_frame"),
    "corrected_marginal_prices": ("services.results.load_frames", "corrected_marginal_prices"),
}


@pytest.mark.parametrize("name,params", sorted(_HANDLER_PARAMS.items()))
def test_every_handler_keeps_its_name_and_parameters(name, params):
    """
    `chat_tools.get_results` does `getattr(routers.results, name)` and then
    `"source" in handler.__code__.co_varnames`. Both the name and the parameter
    names are therefore API, even though nothing imports them.
    """
    handler = getattr(R, name, None)
    assert callable(handler), f"routers.results.{name} is gone or not callable"
    code = handler.__code__
    actual = list(code.co_varnames[: code.co_argcount])
    assert actual == params, (
        f"{name} parameters changed: {actual} != {params}. chat_tools inspects "
        f"these reflectively; FastAPI reads them for the query contract."
    )


@pytest.mark.parametrize("name", _EXPORTED_HELPERS)
def test_helpers_other_modules_import_are_still_exported(name):
    assert callable(getattr(R, name, None)), (
        f"routers.results.{name} is imported by compare.py / asset_results / tests"
    )


@pytest.mark.parametrize("handler,target", sorted(_LIFTED.items()))
def test_each_lifted_handler_has_its_compute_function(handler, target):
    module_name, fn_name = target
    module = importlib.import_module(module_name)
    fn = getattr(module, fn_name, None)
    assert callable(fn), f"{module_name}.{fn_name} missing for {handler}"
    assert fn.__module__ == module_name, (
        f"{fn_name} is re-exported into {module_name} rather than defined there"
    )


@pytest.mark.parametrize("name,target", sorted(_LIFTED_HELPERS.items()))
def test_lifted_helpers_take_result_df_and_the_router_wraps_them(name, target):
    """
    The service version must accept `result_df` as a keyword-only parameter —
    that is the whole point of moving it — and the router must still expose
    the old signature so its importers do not change.
    """
    import inspect

    module_name, fn_name = target
    svc = getattr(importlib.import_module(module_name), fn_name)
    kw = inspect.signature(svc).parameters.get("result_df")
    assert kw is not None and kw.kind is inspect.Parameter.KEYWORD_ONLY, (
        f"{module_name}.{fn_name} must take `result_df` keyword-only"
    )
    wrapper = getattr(R, name)
    assert "result_df" not in inspect.signature(wrapper).parameters, (
        f"routers.results.{name} must keep the pre-lift signature"
    )


def test_services_results_never_imports_routers():
    """
    Stricter than Phase 1's rule. A service that imports a router has the
    dependency arrow backwards, and `services/asset_results/compute.py`
    importing `_result_df` lazily from `routers.results` is already one such
    arrow. This package does not add another.
    """
    pkg = pathlib.Path(__file__).resolve().parent.parent / "services" / "results"
    assert pkg.is_dir(), "services/results/ does not exist"
    pat = re.compile(r"^\s*(from\s+routers[\s.]|import\s+routers\b)")
    offenders = [
        f"{p.name}:{i}: {line.strip()}"
        for p in sorted(pkg.glob("*.py"))
        for i, line in enumerate(p.read_text().splitlines(), 1)
        if pat.match(line)
    ]
    assert not offenders, "services/results imports a router:\n  " + "\n  ".join(offenders)


def test_the_package_reexports_nothing():
    """
    Same rule as `services/solver/`: the façade is the router, not the package.

    Checked on the `__init__.py` SOURCE, not on `vars(package)` — Python
    attaches every imported submodule to the package object as an attribute,
    so a runtime check would flag `cost_breakdown`, `prices`, ... the moment
    anything imported them.
    """
    import ast

    init = pathlib.Path(__file__).resolve().parent.parent / "services" / "results" / "__init__.py"
    tree = ast.parse(init.read_text())
    stmts = [type(s).__name__ for s in tree.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
    assert stmts == [], f"services/results/__init__.py contains {stmts}; it must hold only a docstring"
