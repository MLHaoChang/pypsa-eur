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
    # _canonical_load_carrier_key moved WITH assumptions (Task 3): its only
    # in-module caller is _apply_modelling_assumptions, and leaving it behind
    # would have made assumptions.py import back from the façade.

    # ── Task 1 → services/solver/periodized_costs.py ─────────────────────────
    "_annuity": "services.solver.periodized_costs",
    "_reference_build_year": "services.solver.periodized_costs",
    "_pv_factor_series": "services.solver.periodized_costs",
    "fill_periodized_cost_defaults": "services.solver.periodized_costs",
    "with_periodized_cost_defaults": "services.solver.periodized_costs",
    "periodized_capital_costs": "services.solver.periodized_costs",

    # ── Task 4 → services/solver/diagnostics.py ──────────────────────────────
    "_diagnose_infeasibility": "services.solver.diagnostics",
    "_log_global_constraint_shadow_prices": "services.solver.diagnostics",

    # ── Task 6 → services/solver/objective.py ────────────────────────────────
    "_objective_conditioning": "services.solver.objective",

    # ── Task 3 → services/solver/assumptions.py ──────────────────────────────
    "resolve_branch_outages": "services.solver.assumptions",
    "_apply_modelling_assumptions": "services.solver.assumptions",
    "_normalise_dynamic_indexes": "services.solver.assumptions",
    "_DISPATCH_FIX_ACCESSORS": "services.solver.assumptions",
    "_canonical_load_carrier_key": "services.solver.assumptions",  # routers/results.py

    # ── Task 2 → services/solver/vintage_store.py ────────────────────────────
    # Its own module because BOTH assumptions and myopic read this store, so it
    # belongs to neither; that is what makes the rest of the cut a DAG.
    "_frozen_vintage_store": "services.solver.vintage_store",

    # ── Task 7 → services/solver/myopic.py ───────────────────────────────────
    "_run_myopic_foresight": "services.solver.myopic",
    "_freeze_period_capacities": "services.solver.myopic",
    "_defer_future_vintage_builds": "services.solver.myopic",
    "_outages_active_in_period": "services.solver.myopic",

    # ── Task 5 → services/solver/runtime.py ──────────────────────────────────
    "check_solver_availability": "services.solver.runtime",
    "_AbortWatcher": "services.solver.runtime",
    "_SolveHeartbeat": "services.solver.runtime",
    "_RollingWindowFailureCatcher": "services.solver.runtime",
    "_ThreadScopedQueueHandler": "services.solver.runtime",
    "SolveAborted": "services.solver.runtime",
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


def test_no_call_site_was_left_behind_by_a_move():
    """
    Static undefined-name sweep (ruff F821) over `services/` and `routers/`.

    This is the guard for the decomposition's characteristic defect: a helper
    moves to a carved module and a caller is left behind referring to a name
    that no longer exists in its module. Python binds globals at call time, so
    the import still succeeds, the app still boots, and the break only surfaces
    when that particular code path runs — for the solver, that means inside a
    real solve, minutes into a suite, in a traceback that points at the caller
    rather than at the move.

    It caught exactly that during Task 3: the line range for the load-carrier
    block silently swallowed `_safe_log`, which belongs with the log plumbing,
    leaving five callers in `solver_service` referring to a name that had gone
    to `assumptions.py`.

    The expected findings are the deliberate `cfg: "SolverConfig"` forward
    references. `SolverConfig` stays in `solver_service` and the carved modules
    type against it as a string precisely so they need no import for it —
    string annotations are never evaluated at runtime. ruff resolves them
    anyway and reports them; they are listed here rather than suppressed, so
    that any finding naming a DIFFERENT symbol fails this test.

    ruff comes from the `dev` feature, which the `test` environment includes,
    so the canonical `pixi run gui-tests` has it. If it is missing this fails
    rather than skips — a skipped test reads as a green suite, which is how a
    hole this size stays open.
    """
    import pathlib
    import subprocess
    import sys

    backend = pathlib.Path(__file__).resolve().parent.parent
    proc = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--isolated",
         "--select", "F821", "--output-format", "concise",
         "services", "routers"],
        cwd=backend, capture_output=True, text=True,
    )
    assert "No module named" not in proc.stderr, (
        "ruff is not importable here. It ships with the `dev` feature, which "
        "the `test` environment includes — run the suite via `pixi run gui-tests`."
    )

    findings = [
        line for line in proc.stdout.splitlines()
        if line.strip() and "F821" in line
    ]
    unexpected = [f for f in findings if "Undefined name `SolverConfig`" not in f]

    assert not unexpected, (
        "undefined name(s) — a move almost certainly left a caller behind:\n  "
        + "\n  ".join(unexpected)
    )
