"""Issue 30 (SH-06 (b), owner decision FILL-11-6): a solve's own in-place
input changes never reach the run identity.

`run_simulation` changes the live network's inputs while it solves (the
horizon scaling of `capital_cost` / `fom_cost`, the discount_rate / lifetime
fill, the CO2 adder, the VOLL slack generators, ...) and reverts them after.
A reader that does not hold the solve's mutation lock (`run_envelope.settle`
right after an enqueue) hashed those transient values, so the chain's next
link was a fingerprint the project never had and the next step was refused
`brief_stale`. A one-year horizon hid it (factor 1). Real project, real
solve, real queue and runner; only the timing is pinned (the solve waits
mid-LP until the reader has read)."""

# ruff: noqa: F811 -- imported pytest fixtures
import copy
import queue
import threading


from harness import runner as harness_runner
from services import run_envelope, solver_service, workflow_tools as wt
from services.pypsa_service import PyPSAService
from services.solver_service import SolverConfig, run_simulation
from tests.test_assistant_tasks_study_brief import (  # noqa: F401
    baseline,
    dispatch,
    edit,
    helpers_state,
    session,
    wait_until,
)
from tests.test_harness_envelope import BLOCK, granted, profile, refusals, step, until  # noqa: F401
from tests.test_harness_run_skeleton import site, start  # noqa: F401
from tests.test_harness_runner import detached, frames_of, settle, task_path  # noqa: F401


def ctx():
    return PyPSAService.get_active_context()


def short_horizon_layers(n):
    """Switch on every reverting layer a two-snapshot network can carry: an
    extendable asset priced by `capital_cost` + `fom_cost` (horizon scaling),
    one priced by `overnight_cost` with no rate or lifetime (the fill), a
    fossil carrier (CO2 adder); VOLL comes from the config."""
    n.add("Carrier", "gas", co2_emissions=0.2)
    n.generators.at["G1", "carrier"] = "gas"
    n.add(
        "Generator",
        "G2",
        bus="B1",
        p_nom_extendable=True,
        p_nom_max=50.0,
        capital_cost=1000.0,
        fom_cost=50.0,
        marginal_cost=1.0,
        p_max_pu=0.5,
    )
    n.add(
        "Generator",
        "G3",
        bus="B1",
        p_nom_extendable=True,
        p_nom_max=50.0,
        overnight_cost=5000.0,
        marginal_cost=2.0,
    )
    return SolverConfig(co2_price=80.0, voll=3000.0)


def active_layers(n):
    """Which layers are on the live network now (asserted mid-solve, so the
    test fails loudly if the fixture stops exercising them)."""
    g = n.generators
    return {
        "capital_cost": g.at["G2", "capital_cost"] != 1000.0,
        "fom_cost": g.at["G2", "fom_cost"] != 50.0,
        "discount_rate": g.at["G3", "discount_rate"]
        == g.at["G3", "discount_rate"],  # filled: not NaN
        "co2": g.at["G1", "marginal_cost"] != 10.0,
        "voll": len(g) > 3,
    }


def identity(c):
    return wt._network_input_fingerprint(c), wt._network_input_fingerprint(
        c, exact=True
    )


def solve_reading_mid_lp(cfg, reader, lock=None):
    """Run the project solve; `reader()` runs between the LP and the
    modelling restore, where every transform is still applied."""
    seen = {}
    original = solver_service._no_solution_is_a_failure

    def mid(network, *args, **kwargs):
        if "mid" not in seen:
            seen["layers"] = active_layers(network)
            seen["mid"] = reader()
        return original(network, *args, **kwargs)

    c = ctx()
    c.solver_state["solver_config"] = cfg
    solver_service._no_solution_is_a_failure = mid
    try:
        log = queue.SimpleQueue()
        status, condition = run_simulation(
            cfg,
            c.network,
            lock or c.mutation_lock,
            threading.Event(),
            log,
            state_update=lambda **kw: None,
        )
    finally:
        solver_service._no_solution_is_a_failure = original
    lines = []
    while not log.empty():
        lines.append(log.get())
    assert status in ("ok", "optimal"), (
        status,
        condition,
        [m for m in lines if m and "ERROR" in str(m).upper()][-5:],
    )
    return seen


# ── T1 / T2: mid-solve, pre-solve and post-solve identities agree ───────────


def test_a_short_horizon_solves_transient_inputs_never_reach_the_identity(baseline):
    cfg = short_horizon_layers(ctx().network)
    ctx().solver_state["solver_config"] = cfg
    before = identity(ctx())
    seen = solve_reading_mid_lp(cfg, lambda: identity(ctx()))
    assert seen["layers"] == dict.fromkeys(seen["layers"], True), seen[
        "layers"
    ]  # every layer was on
    assert (
        seen["mid"] == before
    )  # default and exact both hash the inputs, not the transients
    assert identity(ctx()) == before  # every layer reverted: nothing hidden that lasts
    assert not wt._SOLVE_INPUTS  # released


def test_a_mid_solve_reader_keeps_its_own_config(baseline):
    """The snapshot is of the network only: a reader's config still counts."""
    cfg = short_horizon_layers(ctx().network)
    ctx().solver_state["solver_config"] = cfg
    before = identity(ctx())

    def other_config():
        reader = copy.copy(ctx())
        reader.solver_state = {
            **ctx().solver_state,
            "solver_config": SolverConfig(co2_price=81.0, voll=3000.0),
        }
        return identity(reader)

    seen = solve_reading_mid_lp(cfg, other_config)
    assert seen["mid"][0] != before[0] and seen["mid"][1] != before[1]


def test_an_edit_after_the_solve_moves_the_identity(baseline):
    cfg = short_horizon_layers(ctx().network)
    ctx().solver_state["solver_config"] = cfg
    solve_reading_mid_lp(cfg, lambda: None)
    after = identity(ctx())
    ctx().network.generators.at["G2", "capital_cost"] = 900.0
    moved = identity(ctx())
    assert moved[0] != after[0] and moved[1] != after[1]


def test_an_aborted_solve_releases_the_snapshot(baseline):
    cfg = short_horizon_layers(ctx().network)
    ctx().solver_state["solver_config"] = cfg
    before = identity(ctx())

    def boom():
        assert wt._SOLVE_INPUTS
        raise RuntimeError("the LP failed")

    original = solver_service._no_solution_is_a_failure
    solver_service._no_solution_is_a_failure = lambda *a, **k: boom()
    try:
        status, _ = run_simulation(
            cfg,
            ctx().network,
            ctx().mutation_lock,
            threading.Event(),
            queue.SimpleQueue(),
            state_update=lambda **kw: None,
        )
    finally:
        solver_service._no_solution_is_a_failure = original
    assert status == "error"
    assert not wt._SOLVE_INPUTS
    assert identity(ctx()) == before


# ── B2: re-entrant; B3: only under the owning context's lock ────────────────


def test_a_nested_solve_never_clears_the_outer_snapshot(baseline):
    cfg = short_horizon_layers(ctx().network)
    ctx().solver_state["solver_config"] = cfg
    before = identity(ctx())
    c = ctx()
    with c.mutation_lock:
        release = wt.hold_solve_inputs(c.network, c.mutation_lock)
        try:
            c.network.generators.at["G2", "capital_cost"] = (
                0.228  # the outer solve's transient
            )
            seen = solve_reading_mid_lp(
                cfg, lambda: identity(ctx())
            )  # the inner, nested solve
            assert seen["mid"] == before
            assert (
                identity(ctx()) == before
            )  # the inner finally left the outer snapshot registered
        finally:
            c.network.generators.at["G2", "capital_cost"] = 1000.0
            release()
            release()  # idempotent: a second release never frees another holder's snapshot
    assert not wt._SOLVE_INPUTS


def test_no_snapshot_without_the_owning_contexts_lock(baseline):
    c = ctx()
    foreign = threading.RLock()
    with foreign:
        release = wt.hold_solve_inputs(
            c.network, foreign
        )  # not the project's mutation lock
        assert wt._solve_snapshot(c.network) is None
    release()
    release = wt.hold_solve_inputs(
        c.network, c.mutation_lock
    )  # the right lock, but not held
    assert wt._solve_snapshot(c.network) is None
    release()
    with c.mutation_lock:  # control: held, and the project's own
        release = wt.hold_solve_inputs(c.network, c.mutation_lock)
        assert wt._solve_snapshot(c.network) is not None
        release()
    assert not wt._SOLVE_INPUTS


def test_a_solve_refused_before_the_lp_releases_the_snapshot(baseline):
    """An early return under the lock (validation) never reaches the restore:
    only the outer `finally` releases."""
    c = ctx()
    c.network.add(
        "Generator", "G2", bus="B1", p_nom_extendable=True, capital_cost=1000.0
    )  # no p_nom_max
    cfg = SolverConfig()
    c.solver_state["solver_config"] = cfg
    status, condition = run_simulation(
        cfg,
        c.network,
        c.mutation_lock,
        threading.Event(),
        queue.SimpleQueue(),
        state_update=lambda **kw: None,
    )
    assert (status, condition) == ("error", "validation_failed")
    assert not wt._SOLVE_INPUTS


# ── T4: the owner's Done, at the envelope ───────────────────────────────────


def test_a_short_horizon_base_solve_then_an_effectful_step_is_admitted(
    granted, profile, monkeypatch
):
    """A two-hour base solve, the settle right after its enqueue (while the
    solve is mid-LP), then an envelope-admitted edit: admitted, not
    `brief_stale`."""
    cfg = short_horizon_layers(ctx().network)
    ctx().solver_state["solver_config"] = cfg
    settled = threading.Event()
    original_settle = run_envelope.settle

    def settle_then_release(*args, **kwargs):
        try:
            return original_settle(*args, **kwargs)
        finally:
            settled.set()

    monkeypatch.setattr(run_envelope, "settle", settle_then_release)
    original_mid = solver_service._no_solution_is_a_failure
    mid_layers = {}

    def hold_mid_lp(network, *args, **kwargs):
        mid_layers.update(active_layers(network))
        settled.wait(30)  # the settle reads while this solve's transients are applied
        return original_mid(network, *args, **kwargs)

    monkeypatch.setattr(solver_service, "_no_solution_is_a_failure", hold_mid_lp)
    # `run_simulation` starts the solve and returns: outside ATOMIC_TOOLS, its
    # step settles on an identity read now, while the solve runs.
    profile({**BLOCK, "runs": [{"tool": "run_simulation"}]})
    task, path, _ = granted(
        steps=[{"id": "inputs.2", "tool": "run_simulation", "args": {}}, step(3, 25.0)]
    )

    def edited(r):
        return next(x for x in r["steps"] if x["id"] == "inputs.3")

    record = until(
        harness_runner.Runner("a"),
        path,
        task,
        lambda r: (
            r["run"]["status"] in ("completed", "needs_user", "paused", "failed")
            or edited(r)["status"] not in ("pending", "waiting")
        ),
    )
    assert mid_layers.get("capital_cost"), (
        mid_layers
    )  # the settle really overlapped the transient
    assert "brief_stale" not in refusals(path) and "drift" not in refusals(path), (
        refusals(path)
    )
    assert edited(record)["status"] == "completed", [
        (s["tool"], s["status"]) for s in record["steps"]
    ]
    assert ctx().network.generators.at["G1", "p_nom"] == 25.0
