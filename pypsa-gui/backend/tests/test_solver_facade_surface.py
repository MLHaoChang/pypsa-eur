"""
The import-surface tripwire for the `services/solver/` decomposition.

`services/solver_service.py` is being carved into a `services/solver/` package
(see `docs/superpowers/specs/2026-09-04-backend-god-file-decomposition-design.md`).
Forty-plus call sites — 39 test modules among them — import names, most of them
private, straight out of `services.solver_service`. The decomposition keeps that
module as the single import surface and re-exports the carved names, so no call
site changes.

This file is what makes that guarantee enforceable. Without it, a forgotten
re-export surfaces as an ImportError in whichever router happens to import the
name next, at whatever later date someone runs that path — instead of here, at
the seam, in the task that dropped it.

`_FACADE_ORIGINS` maps every externally-imported name to the module it is
expected to LIVE in. `None` means "still defined in solver_service itself".
Each extraction task flips one group of entries from `None` to its new module
BEFORE moving the code — so the test goes red, the move makes it green, and the
map doubles as the running record of where the decomposition has got to.

Names here were enumerated by AST-walking every `from services.solver_service
import ...` in the backend, not by reading imports by eye.
"""
import importlib

import pytest

import services.solver_service as solver_service


# name -> module it is expected to be DEFINED in; None = still in solver_service.
_FACADE_ORIGINS: dict[str, str | None] = {
    # ── Stays in solver_service permanently ──────────────────────────────────
    # SolverConfig is imported by 41 call sites and is annotated as the *string*
    # "SolverConfig" everywhere it is consumed, so the carved modules type
    # against it without importing it. Moving it would buy nothing and put the
    # most widely imported name in the backend into every task's blast radius.
    "SolverConfig": None,
    "run_simulation": None,               # the orchestrator
    "user_code_enabled": None,
    "_canonical_load_carrier_key": None,  # routers/results.py

    # ── Task 1 → services/solver/periodized_costs.py ─────────────────────────
    "_annuity": None,
    "_reference_build_year": None,
    "_pv_factor_series": None,
    "fill_periodized_cost_defaults": None,
    "with_periodized_cost_defaults": None,
    "periodized_capital_costs": None,

    # ── Task 2 → services/solver/diagnostics.py ──────────────────────────────
    "_diagnose_infeasibility": None,
    "_log_global_constraint_shadow_prices": None,

    # ── Task 3 → services/solver/objective.py ────────────────────────────────
    "_objective_conditioning": None,

    # ── Task 4 → services/solver/assumptions.py ──────────────────────────────
    "resolve_branch_outages": None,
    "_apply_modelling_assumptions": None,
    "_normalise_dynamic_indexes": None,
    "_DISPATCH_FIX_ACCESSORS": None,

    # ── Task 5 → services/solver/myopic.py ───────────────────────────────────
    "_run_myopic_foresight": None,
    "_freeze_period_capacities": None,
    "_defer_future_vintage_builds": None,
    "_outages_active_in_period": None,
    "_frozen_vintage_store": None,

    # ── Task 6 → services/solver/runtime.py ──────────────────────────────────
    "check_solver_availability": None,
    "_AbortWatcher": None,
    "_SolveHeartbeat": None,
    "_RollingWindowFailureCatcher": None,
    "_ThreadScopedQueueHandler": None,
}


@pytest.mark.parametrize("name", sorted(_FACADE_ORIGINS))
def test_the_facade_still_exports(name):
    """
    Every name any call site imports out of `services.solver_service` is still
    reachable from it, wherever the decomposition has since moved the body.
    """
    assert hasattr(solver_service, name), (
        f"services.solver_service no longer exports {name!r}. The decomposition "
        f"keeps solver_service as the single import surface — re-export it from "
        f"its new module rather than repointing the call sites."
    )


@pytest.mark.parametrize(
    "name,origin",
    sorted((n, o) for n, o in _FACADE_ORIGINS.items() if o is not None),
)
def test_a_reexported_name_is_the_same_object_as_its_definition(name, origin):
    """
    The façade must re-export the *identical* object, not a copy or a wrapper.

    Identity is not pedantry here. `SolveAborted` is caught by an
    `except SolveAborted` inside `run_simulation`; a second class object with the
    same name would sail straight through that handler. The same applies to any
    default argument or registry that closes over a moved function.
    """
    module = importlib.import_module(origin)
    assert getattr(solver_service, name) is getattr(module, name), (
        f"{name!r} on services.solver_service is a different object from "
        f"{origin}.{name}. Re-export the name itself, don't redefine or wrap it."
    )


def test_carved_modules_never_import_back_from_solver_service():
    """
    Dependencies run one way, down the DAG in the design doc. `ac_pf_service.py`
    — the earlier carve-out — imports three names back from solver_service and
    is itself imported lazily from a function body to avert the resulting
    cycle. That is a cycle deferred, not removed, and this decomposition does
    not repeat it.

    If this fails, the cluster was cut in the wrong place. Re-cut it; do not
    add a function-body import to paper over the cycle.
    """
    import pathlib

    pkg = pathlib.Path(__file__).resolve().parent.parent / "services" / "solver"
    if not pkg.is_dir():
        pytest.skip("services/solver/ does not exist yet (pre-Task-1)")

    offenders = []
    for path in sorted(pkg.glob("*.py")):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"'):
                continue  # docstring prose naming the seam is expected
            if "solver_service" in stripped and (
                stripped.startswith("import ") or stripped.startswith("from ")
            ):
                offenders.append(f"{path.name}:{lineno}: {stripped}")

    assert not offenders, (
        "carved modules must not import from solver_service:\n  "
        + "\n  ".join(offenders)
    )


def test_run_simulation_stays_a_module_attribute_of_solver_service():
    """
    `tests/test_solve_queue.py:258` does `import services.solver_service as ss`
    then `monkeypatch.setattr(ss, "run_simulation", ...)`, and
    `services/solve_queue.py:324` resolves `run_simulation` through a
    function-body import — which is what lets that patch take effect.

    So `run_simulation` has to stay a real module-level attribute of
    solver_service. Were it ever carved out and merely re-exported, the patch
    would rebind the façade while solve_queue kept calling the original, and
    the superseded-job test would fail in a way that points nowhere near here.
    """
    assert _FACADE_ORIGINS["run_simulation"] is None
    assert callable(solver_service.run_simulation)
    assert solver_service.run_simulation.__module__ == "services.solver_service"
