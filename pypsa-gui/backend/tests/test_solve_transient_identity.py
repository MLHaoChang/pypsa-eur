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


def test_a_solve_that_raises_before_the_lp_releases_the_snapshot(baseline, monkeypatch):
    """An exception after the hold and before the LP's own restore: only the
    outer `finally` releases."""
    from services.adequacy import energization

    cfg = short_horizon_layers(ctx().network)
    ctx().solver_state["solver_config"] = cfg
    held = []

    def boom(network):
        held.append(wt._solve_snapshot(network) is not None)
        raise RuntimeError("energization failed")

    monkeypatch.setattr(energization, "apply_energization", boom)
    status, _ = run_simulation(
        cfg,
        ctx().network,
        ctx().mutation_lock,
        threading.Event(),
        queue.SimpleQueue(),
        state_update=lambda **kw: None,
    )
    assert held == [True] and status == "error"
    assert not wt._SOLVE_INPUTS


def test_a_lasting_normalisation_is_in_the_snapshot(baseline):
    """B1: a change the solve keeps (here a stale dispatch fix it clears) is
    taken before the snapshot, so a mid-solve read equals the post-solve one."""
    import pandas as pd

    cfg = short_horizon_layers(ctx().network)
    n = ctx().network
    n.generators_t.p_set = pd.DataFrame({"G1": [5.0, 5.0]}, index=n.snapshots)
    ctx().solver_state["solver_config"] = cfg
    before = identity(ctx())
    seen = solve_reading_mid_lp(cfg, lambda: identity(ctx()))
    after = identity(ctx())
    assert after != before  # the layer was on and lasted
    assert seen["mid"] == after


def test_the_snapshot_outlives_the_restore(baseline, monkeypatch):
    """B1 ordering: the release follows the modelling restore, never precedes it."""
    cfg = short_horizon_layers(ctx().network)
    ctx().solver_state["solver_config"] = cfg
    before = identity(ctx())
    original = solver_service._apply_modelling_assumptions
    at_restore = []

    def wrapped(network, config, phase):
        restore, captured = original(network, config, phase)

        def restore_reading():
            at_restore.append(identity(ctx()))  # every transient still applied
            restore()

        return restore_reading, captured

    monkeypatch.setattr(solver_service, "_apply_modelling_assumptions", wrapped)
    solve_reading_mid_lp(cfg, lambda: None)
    assert at_restore and at_restore[0] == before


# ── SP-1: the lasting normalisations are invisible to unlocked readers ──────


def lasting_normalisation_layers(n):
    """Two lasting normalisations far apart in `run_simulation`: a GUI
    transformer preset that is no PyPSA type (cleared early) and a stale
    dispatch fix (cleared late)."""
    import pandas as pd

    n.add("Bus", "B2", v_nom=20)
    n.add(
        "Transformer", "T1", bus0="B1", bus1="B2", s_nom=50.0, x=0.1, type="380/220 kV"
    )
    n.add("Load", "L2", bus="B2", p_set=1.0)
    n.generators_t.p_set = pd.DataFrame({"G1": [5.0, 5.0]}, index=n.snapshots)


def test_a_read_between_two_normalisations_sees_the_pre_solve_inputs(
    baseline, monkeypatch
):
    """SP-1 (2): an unlocked reader inside the normalisation window, after the
    transformer type is cleared but before the dispatch fix is, hashes the
    inputs the solve started from: never a mix the network never had."""
    cfg = short_horizon_layers(ctx().network)
    lasting_normalisation_layers(ctx().network)
    ctx().solver_state["solver_config"] = cfg
    before = identity(ctx())
    original = solver_service._normalise_dynamic_indexes
    between = []

    def reading(network, *args, **kwargs):
        if not between:
            assert ctx().network.transformers.at["T1", "type"] == ""  # first one done
            assert "G1" in ctx().network.generators_t.p_set  # second one pending
            between.append(identity(ctx()))
        return original(network, *args, **kwargs)

    monkeypatch.setattr(solver_service, "_normalise_dynamic_indexes", reading)
    seen = solve_reading_mid_lp(cfg, lambda: identity(ctx()))
    after = identity(ctx())
    assert after != before  # both normalisations lasted
    assert between == [before]
    assert seen["mid"] == after  # past the window: the normalised inputs
    assert not wt._SOLVE_INPUTS


def test_a_nested_refresh_never_replaces_the_outer_snapshot(baseline):
    c = ctx()
    with c.mutation_lock:
        outer = wt.hold_solve_inputs(c.network, c.mutation_lock)
        snapshot = wt._solve_snapshot(c.network)
        inner = wt.hold_solve_inputs(c.network, c.mutation_lock)
        inner.refresh()
        assert wt._solve_snapshot(c.network) is snapshot
        inner()
        outer.refresh()
        assert wt._solve_snapshot(c.network) is not snapshot  # the outermost may
        outer()
        outer.refresh()  # released: a late refresh registers nothing
    assert not wt._SOLVE_INPUTS


def test_the_outer_refresh_works_after_a_nested_hold_came_and_went(baseline):
    c = ctx()
    with c.mutation_lock:
        outer = wt.hold_solve_inputs(c.network, c.mutation_lock)
        snapshot = wt._solve_snapshot(c.network)
        inner = wt.hold_solve_inputs(c.network, c.mutation_lock)
        inner()
        assert wt._solve_snapshot(c.network) is snapshot  # the outer one stays
        outer.refresh()
        refreshed = wt._solve_snapshot(c.network)
        assert refreshed is not None and refreshed is not snapshot
        outer()
    assert not wt._SOLVE_INPUTS


def test_a_refresh_after_release_or_replacement_publishes_nothing(baseline):
    """S3: refresh publishes only while its own hold is the registered one."""
    c = ctx()
    with c.mutation_lock:
        stale = wt.hold_solve_inputs(c.network, c.mutation_lock)
        stale()
        stale.refresh()  # released
        assert not wt._SOLVE_INPUTS
        newer = wt.hold_solve_inputs(c.network, c.mutation_lock)
        snapshot = wt._solve_snapshot(c.network)
        stale.refresh()  # a newer hold replaced it
        assert wt._solve_snapshot(c.network) is snapshot
        newer()
        outer = wt.hold_solve_inputs(c.network, c.mutation_lock)
        inner = wt.hold_solve_inputs(c.network, c.mutation_lock)
        snapshot = wt._solve_snapshot(c.network)
        outer()  # released while a nested holder keeps the entry registered
        outer.refresh()
        assert wt._solve_snapshot(c.network) is snapshot
        inner()
    assert not wt._SOLVE_INPUTS


class _Slot(list):
    """An entry that records every write to its snapshot slot (the publish
    point itself) and whether the copy behind it had finished by then."""

    def __init__(self, entry, published, copied):
        super().__init__(entry)
        self.published = published
        self.copied = copied

    def __setitem__(self, i, value):
        if i == 1:
            self.published.append((value, self.copied.is_set()))
        super().__setitem__(i, value)


def test_a_reader_racing_refresh_sees_the_old_or_the_new_snapshot(
    baseline, monkeypatch
):
    """S2: refresh builds its copy completely, then publishes it with one swap;
    a reader racing it hashes the old snapshot or the new, nothing else."""
    cfg = short_horizon_layers(ctx().network)
    lasting_normalisation_layers(ctx().network)
    c = ctx()
    c.solver_state["solver_config"] = cfg
    old = identity(c)
    reads, published, stop = [], [], threading.Event()
    copying, copied = threading.Event(), threading.Event()
    real_copy = wt._network_copy

    def slow_copy(network):
        if network is not c.network:  # a reader copying the snapshot
            return real_copy(network)
        copying.set()
        stop.wait(0.3)  # readers run while the new snapshot is being built
        out = real_copy(network)
        copied.set()
        return out

    def reader():  # one fingerprint per read: a swap may land between two
        exact = False
        while not stop.is_set():
            reads.append((exact, wt._network_input_fingerprint(c, exact=exact)))
            exact = not exact

    with c.mutation_lock:
        hold = wt.hold_solve_inputs(c.network, c.mutation_lock)
        key = id(c.network)
        wt._SOLVE_INPUTS[key] = _Slot(wt._SOLVE_INPUTS[key], published, copied)
        hold._entry = wt._SOLVE_INPUTS[key]
        c.network.transformers.at["T1", "type"] = ""  # the lasting normalisations
        c.network.generators_t.p_set = c.network.generators_t.p_set.iloc[:, :0]
        new = identity(_live(c))
        t = threading.Thread(target=reader)
        t.start()
        monkeypatch.setattr(wt, "_network_copy", slow_copy)
        hold.refresh()
        monkeypatch.setattr(wt, "_network_copy", real_copy)
        before_swap = len(reads)
        while len(reads) < before_swap + 2:
            stop.wait(0.01)
        stop.set()
        t.join()
        assert copying.is_set()
        assert [done for _, done in published] == [True]  # one swap, of a finished copy
        assert published[0][0] is wt._solve_snapshot(c.network)
        hold()
    assert old != new
    allowed = {(False, old[0]), (True, old[1]), (False, new[0]), (True, new[1])}
    assert reads and set(reads) <= allowed, set(reads) - allowed
    assert {fp for _, fp in reads} & set(old) and {fp for _, fp in reads} & set(new)


def _live(c):
    """A probe context that hashes the live network, not the snapshot."""
    probe = copy.copy(c)
    probe.network = wt._network_copy(c.network)  # not registered: read as it is
    return probe


def test_a_normalisation_that_raises_leaves_no_snapshot(baseline, monkeypatch):
    """S4: a raise between the hold and the refresh releases the hold."""
    cfg = short_horizon_layers(ctx().network)
    ctx().solver_state["solver_config"] = cfg

    def boom(*args, **kwargs):
        assert wt._solve_snapshot(ctx().network) is not None  # held by now
        raise RuntimeError("injected")

    monkeypatch.setattr(solver_service, "_sanitise_transformer_types", boom)
    c = ctx()
    status, _ = run_simulation(
        cfg, c.network, c.mutation_lock, threading.Event(), queue.SimpleQueue()
    )
    assert status == "error"
    assert not wt._SOLVE_INPUTS


def test_the_ac_pf_stage_transients_never_reach_the_identity(baseline, monkeypatch):
    """S6: the AC-PF stage after a LOPF fixes dispatch into `p_set` and sets
    generator controls in place, and reverts them; a reader mid-stage hashes
    the inputs, as after the solve."""
    from services import ac_pf_service

    cfg = short_horizon_layers(ctx().network)
    cfg.run_ac_pf_after_lopf = True
    ctx().solver_state["solver_config"] = cfg
    original = ac_pf_service._fix_dispatch_for_ac_pf
    mid = []

    def reading(network):
        original(network)
        assert "G1" in network.generators_t.p_set  # the transient is on
        mid.append(identity(ctx()))

    monkeypatch.setattr(ac_pf_service, "_fix_dispatch_for_ac_pf", reading)
    solve_reading_mid_lp(cfg, lambda: None)
    after = identity(ctx())
    assert mid == [after]
    assert not wt._SOLVE_INPUTS


# Recorded on master 4e637711f (before SP-1): the short-horizon fixture's
# solve, which SP-1 must leave unchanged.
SOLVE_ON_MASTER = {
    "objective": 14.876712,
    "p_nom_opt": {"G1": 20.0, "G2": 12.0, "G3": 0.0},
    "p": {"G1": 0.0, "G2": 12.0, "G3": 0.0},
}


def test_solve_results_equal_master(baseline):
    cfg = short_horizon_layers(ctx().network)
    lasting_normalisation_layers(ctx().network)
    ctx().solver_state["solver_config"] = cfg
    solve_reading_mid_lp(cfg, lambda: None)
    n = ctx().network
    got = {
        "objective": round(float(n.objective), 6),
        "p_nom_opt": {g: round(float(v), 6) for g, v in n.generators.p_nom_opt.items()},
        "p": {g: round(float(v), 6) for g, v in n.generators_t.p.sum().items()},
    }
    assert got == SOLVE_ON_MASTER


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
