"""Study harness issue 22 (HH-01A, HH-05 E02): an envelope-admitted edit
compares its expected input identity and writes inside one project lock.

Real project, real runner and the ordinary dispatch path. The barriers sit
in the dispatch path's own seams: after `tool_running` releases the
runner's locks and before the worker takes the mutation lock
(`_snapshot_for_turn_undo`), inside the worker's write (`edit_card.receipt`),
and after the write's lock is released (`assistant_tasks.finish_step`)."""
# ruff: noqa: F811 -- imported pytest fixtures
import contextvars
import hashlib
import json
import threading

import pytest
from fastapi import HTTPException
from harness import edit_card, loop, runner as harness_runner
from services import assistant_tasks as tasks, dirty_state, run_envelope, run_profiles
from services.project_context import ProjectContext
from services.pypsa_service import PyPSAService
from tests.test_assistant_tasks_study_brief import (  # noqa: F401
    baseline, dispatch, edit, helpers_state, session, wait_until,
)
from tests.test_harness_envelope import envelope_of, granted, profile, refusals, stopped, until  # noqa: F401
from tests.test_harness_run_skeleton import site  # noqa: F401
from tests.test_harness_runner import detached, frames_of, settle, task_path  # noqa: F401

# eh_site@1's edit shapes (`run_profiles.EH_SITE`), on the baseline's G1 and a storage unit S1.
ATOMIC = {
    "edits": [{"tool": "update_component", "target": {"component": "Generator", "name": "G*", "attr": "p_nom_max"},
               "bounds": {"min": 0.0, "rel": 0.5}},
              {"tool": "update_component", "target": {"component": "StorageUnit", "name": "S*", "attr": "p_nom_max"},
               "bounds": {"min": 0.0, "rel": 0.5}},
              {"tool": "update_component", "target": {"component": "StorageUnit", "name": "S*", "attr": "max_hours"},
               "bounds": {"min": 1.0, "max": 8.0}}],
    "runs": [{"tool": "solve_queue_enqueue"}],
    "budget": {"solves": 2, "rounds": 6, "wall_time_h": 8.0},
}
SHAPES = [("Generator", "G1", "p_nom_max", 45.0), ("StorageUnit", "S1", "p_nom_max", 12.0),
          ("StorageUnit", "S1", "max_hours", 6.0)]


def ctx():
    return PyPSAService.get_active_context()


def edit_step(n, component="Generator", name="G1", attr="p_nom_max", value=45.0):
    return {"id": f"inputs.{n}", "tool": "update_component",
            "args": {"component_class": component, "name": name, "attrs": {attr: value}}}


def value(component, name, attr):
    frame = ctx().network.generators if component == "Generator" else ctx().network.storage_units
    return float(frame.at[name, attr])


@pytest.fixture
def eh(granted, monkeypatch):
    """A granted run on eh_site@1's edit shapes."""
    tunable = [{"component": "Generator", "attr": "p_nom_max"}, {"component": "StorageUnit", "attr": "p_nom_max"},
               {"component": "StorageUnit", "attr": "max_hours"}]
    monkeypatch.setitem(run_profiles.PROFILES, "eh_site@1", {**run_profiles.EH_SITE, "envelope": ATOMIC,
                                                              "tunable": tunable})
    network = ctx().network
    network.generators.at["G1", "p_nom_max"] = 40.0
    network.add("StorageUnit", "S1", bus="B1", p_nom_max=10.0, max_hours=4.0, p_nom_extendable=True)

    def go(steps):
        task, path, _ = granted(steps=steps)
        return task, path
    return go


def writer(change):
    """A second, correctly bound writer: the project's mutation lock, the
    change, the edit counter bumped (as the HTTP middleware does). On its own
    thread, so it waits for a lock the step holds."""
    target = ctx()

    def run():
        with target.mutation_lock:
            change(target.network)
            dirty_state.bump_revision(target)
    thread = threading.Thread(target=run)
    thread.start()
    return thread


@pytest.fixture
def gap(monkeypatch):
    """Run `action` once in the gap: after `tool_running` released the
    runner's locks, before the worker takes the mutation lock."""
    def arm(action, tool="update_component"):
        original = loop._snapshot_for_turn_undo
        fired = []

        def barrier(session, tool_name):
            original(session, tool_name)
            if tool_name == tool and not fired:
                fired.append(True)
                action()
        monkeypatch.setattr(loop, "_snapshot_for_turn_undo", barrier)
        return fired
    return arm


@pytest.fixture
def during(monkeypatch):
    """Run `action` once inside the worker's write, after the handler."""
    def arm(action):
        original = edit_card.receipt
        fired = []

        def barrier(card, args):
            if not fired:
                fired.append(True)
                action()
            return original(card, args)
        monkeypatch.setattr(edit_card, "receipt", barrier)
        return fired
    return arm


def head(path):
    envelope = envelope_of(path)
    return envelope["chain"][-1]["fingerprint"] if envelope["chain"] else envelope["fingerprint"]


# ── E02 / A01: an edit in the gap is drift, not the chain's next link ────────


def test_an_edit_in_the_gap_before_the_write_pauses_the_run_with_no_envelope_write(eh, gap):
    task, path = eh([edit_step(2)])
    before = head(path)
    fired = gap(lambda: writer(lambda n: n.storage_units.__setitem__("p_nom_max", 11.0)).join())
    record = until(harness_runner.Runner("a"), path, task, stopped)
    assert fired
    assert value("StorageUnit", "S1", "p_nom_max") == 11.0  # the external edit stands
    assert value("Generator", "G1", "p_nom_max") == 40.0  # the step wrote nothing
    assert record["run"]["status"] == "paused" and record["run"]["reason"] == "envelope_drift", record["run"]
    assert envelope_of(path)["chain"] == [] and head(path) == before
    assert refusals(path) == ["drift"]


@pytest.mark.parametrize("component,name,attr,new", SHAPES)
def test_with_no_edit_in_the_gap_each_shape_writes_once_and_adds_one_link(eh, gap, component, name, attr, new):
    task, path = eh([edit_step(2, component, name, attr, new)])
    fired = gap(lambda: None)
    record = until(harness_runner.Runner("a"), path, task, lambda r: r["steps"][1]["status"] != "pending")
    assert fired and record["steps"][1]["status"] == "completed", record["steps"][1]
    assert value(component, name, attr) == new
    chain = envelope_of(path)["chain"]
    assert len(chain) == 1 and chain[0]["fingerprint"] == run_envelope._fingerprint(ctx())
    assert chain[0]["network_revision"] == dirty_state.revision(ctx())
    frames = frames_of(task["task_id"], record["owner_id"])
    assert not [f for f in frames if f["frame"] == "tool_pending_confirmation"]


# ── A04: an edit after the write's lock is released is not in the link ──────


def test_an_edit_after_the_write_is_not_in_the_link_and_the_next_step_pauses(eh, during, monkeypatch):
    task, path = eh([edit_step(2), edit_step(3, "StorageUnit", "S1", "max_hours", 5.0)])
    original = tasks.finish_step
    wrote = during(lambda: None)  # armed once the step's write has run
    fired = []

    def barrier(*args, **kwargs):
        out = original(*args, **kwargs)
        if wrote and not fired:
            fired.append(True)
            writer(lambda n: n.loads.__setitem__("p_set", 6.0)).join()
        return out
    monkeypatch.setattr(tasks, "finish_step", barrier)
    record = until(harness_runner.Runner("a"), path, task, stopped)
    assert fired
    chain = envelope_of(path)["chain"]
    assert len(chain) == 1 and chain[0]["fingerprint"] != run_envelope._fingerprint(ctx())
    # The plan's own brief check reads the same change first (issue 08), as in test_harness_envelope.
    assert record["run"]["status"] == "paused", record["run"]
    assert record["run"]["reason"] in ("envelope_drift", "envelope_brief_stale"), record["run"]
    assert value("Generator", "G1", "p_nom_max") == 45.0 and value("StorageUnit", "S1", "max_hours") == 4.0


# ── A18: a solver-config write cannot interleave; before pickup it refuses ──


def solver_config_write(results):
    """The solver-config route's own write (a LC-40 counter bump at its sink)."""
    from routers import simulation
    try:
        results.append(simulation.update_solver_config(simulation.SolverConfigSchema(voll=54321.0),
                                                       db=None, user=None))
    except Exception as exc:  # noqa: BLE001 - shown on failure
        results.append(exc)


def in_context(target, *args):
    """A thread that runs `target` in a copy of this thread's context (the run's project)."""
    context = contextvars.copy_context()
    return threading.Thread(target=lambda: context.run(target, *args))


def test_a_solver_config_write_waits_for_the_compare_write_and_capture(eh, during):
    task, path = eh([edit_step(2)])
    results, blocked = [], []

    def inside():
        thread = in_context(solver_config_write, results)
        thread.start()
        thread.join(timeout=0.5)
        blocked.append(thread.is_alive())  # still waiting on the solver-state lock the write holds
        inside.thread = thread
    fired = during(inside)
    record = until(harness_runner.Runner("a"), path, task, lambda r: r["steps"][1]["status"] != "pending")
    inside.thread.join(timeout=10)
    assert fired and blocked == [True] and results and not isinstance(results[0], Exception), results
    assert record["steps"][1]["status"] == "completed"
    chain = envelope_of(path)["chain"]
    assert len(chain) == 1 and chain[0]["fingerprint"] != run_envelope._fingerprint(ctx())  # the config is not in it


def test_a_solver_config_write_before_pickup_refuses(eh, gap):
    task, path = eh([edit_step(2)])
    results = []
    fired = gap(lambda: solver_config_write(results))  # on the run's thread, in the gap
    record = until(harness_runner.Runner("a"), path, task, stopped)
    assert fired and results and not isinstance(results[0], Exception), results
    assert record["run"]["status"] == "paused" and record["run"]["reason"] == "envelope_drift", record["run"]
    assert value("Generator", "G1", "p_nom_max") == 40.0 and envelope_of(path)["chain"] == []


# ── A08: same input, bumped revision; a sub-precision change ────────────────


def test_an_edit_then_undo_in_the_gap_refuses_on_the_revision(eh, gap):
    task, path = eh([edit_step(2)])

    def edit_then_undo():
        writer(lambda n: n.storage_units.__setitem__("p_nom_max", 11.0)).join()
        writer(lambda n: n.storage_units.__setitem__("p_nom_max", 10.0)).join()
    fired = gap(edit_then_undo)
    record = until(harness_runner.Runner("a"), path, task, stopped)
    assert fired and value("StorageUnit", "S1", "p_nom_max") == 10.0
    assert record["run"]["status"] == "paused" and record["run"]["reason"] == "envelope_drift", record["run"]
    assert value("Generator", "G1", "p_nom_max") == 40.0 and envelope_of(path)["chain"] == []


def test_a_sub_precision_change_in_the_gap_refuses_on_the_exact_fingerprint(eh, gap):
    task, path = eh([edit_step(2)])

    def nudge():
        before = run_envelope._fingerprint(ctx())
        ctx().network.storage_units.at["S1", "p_nom_max"] = 10.0 + 1e-12  # no counter bump
        assert run_envelope._fingerprint(ctx()) == before  # below the chain fingerprint's precision
    fired = gap(nudge)
    record = until(harness_runner.Runner("a"), path, task, stopped)
    assert fired
    assert record["run"]["status"] == "paused" and record["run"]["reason"] == "envelope_drift", record["run"]
    assert value("Generator", "G1", "p_nom_max") == 40.0 and envelope_of(path)["chain"] == []


# ── A05: the lock is the project's own, never process-global ────────────────


def test_another_projects_lock_is_free_while_this_write_holds_its_own(eh, during):
    import pypsa
    task, path = eh([edit_step(2)])
    other = ProjectContext(network=pypsa.Network(), project_uuid=None)
    seen = []

    def inside():
        held = ctx()  # the run's project, as the worker resolves it

        def other_writer():
            with other.mutation_lock, other.solver_state_lock:
                other.network.add("Bus", "X")
            seen.append("other")

        def same_writer():
            got = held.mutation_lock.acquire(timeout=0.3)
            if got:
                held.mutation_lock.release()
            seen.append(("same", got))
        for target in (other_writer, same_writer):
            thread = threading.Thread(target=target)
            thread.start()
            thread.join(timeout=5)
    fired = during(inside)
    record = until(harness_runner.Runner("a"), path, task, lambda r: r["steps"][1]["status"] != "pending")
    assert fired and seen == ["other", ("same", False)]
    assert record["steps"][1]["status"] == "completed" and "X" in other.network.buses.index


# ── A09: what the envelope must still be, read again at pickup ──────────────


def _revoke(task, path, session):
    run_envelope.decide(session, task["task_id"], "revoke")


def _expire(task, path, session):
    envelope = envelope_of(path)
    envelope["expires_at"] = "2000-01-01T00:00:00+00:00"
    run_envelope.path_for(path).write_text(json.dumps(envelope))


def _spend(task, path, session):
    record = json.loads(path.read_text())
    budget = harness_runner._budget(record)
    budget["used"]["cost_eur"] = budget["limit"]["cost_eur"]
    path.write_text(json.dumps(record))


def _regrant(task, path, session):
    envelope = envelope_of(path)
    envelope["id"] = "another-grant"
    run_envelope.path_for(path).write_text(json.dumps(envelope))


@pytest.mark.parametrize("change", [_revoke, _expire, _spend, _regrant])
def test_an_envelope_change_between_preflight_and_pickup_refuses_with_no_write(eh, gap, session, change):
    task, path = eh([edit_step(2)])
    fired = gap(lambda: change(task, path, session))
    record = until(harness_runner.Runner("a"), path, task, stopped)
    assert fired and record["run"]["status"] in ("paused", "needs_user"), record["run"]
    assert value("Generator", "G1", "p_nom_max") == 40.0
    assert envelope_of(path)["chain"] == []


def test_an_acl_loss_between_preflight_and_pickup_refuses_with_no_write(eh, gap, monkeypatch):
    from services import project_registry
    task, path = eh([edit_step(2)])

    def lose():
        monkeypatch.setattr(project_registry, "resolve_project",
                            lambda *a, **k: (_ for _ in ()).throw(HTTPException(404, "gone")))
    fired = gap(lose)
    record = until(harness_runner.Runner("a"), path, task, stopped)
    assert fired and record["run"]["status"] in ("paused", "needs_user"), record["run"]
    assert value("Generator", "G1", "p_nom_max") == 40.0 and envelope_of(path)["chain"] == []


# ── SH-ENVELOPE-1: nothing narrowed ─────────────────────────────────────────


def test_eh_site_envelope_and_tunables_are_unchanged():
    """The envelope SH-ENVELOPE-1 granted, pinned: this issue narrows nothing."""
    block = json.dumps({"envelope": run_profiles.EH_SITE["envelope"], "tunable": run_profiles.EH_SITE["tunable"]},
                       sort_keys=True)
    assert hashlib.sha256(block.encode()).hexdigest() == PINNED_EH_SITE


PINNED_EH_SITE = "76dad7270f8355292b03bca8e592e18fbcd613702f6412c714b35c50e66323f5"  # master 07cc07a9b


# ── GK B (18) conditions ────────────────────────────────────────────────────


def test_a_late_workers_receipt_never_settles_a_later_step(eh, during, monkeypatch):
    """(2) A write that outlives the per-tool deadline leaves its receipt in
    its own step's list: no link from it, and the next step reads drift."""
    from harness import budget as harness_budget
    import time
    monkeypatch.setattr(harness_budget, "PER_TOOL_TIMEOUT_SECONDS", 0.5)
    done = threading.Event()

    def slow():
        time.sleep(1.5)  # past the deadline, still inside the write
        done.set()
    during(slow)
    task, path = eh([edit_step(2), edit_step(3, "StorageUnit", "S1", "max_hours", 5.0)])
    record = until(harness_runner.Runner("a"), path, task, stopped)
    assert done.wait(10)
    time.sleep(0.5)
    assert envelope_of(path)["chain"] == []  # the late receipt settled nothing
    assert record["run"]["status"] in ("paused", "needs_user"), record["run"]
    assert value("StorageUnit", "S1", "max_hours") == 4.0  # the next step did not run


def test_a_grant_or_revoke_from_another_thread_completes_while_the_write_holds_its_locks(eh, during, session):
    """(3) Lock order: the guard takes no `assistant_tasks._LOCK` under the
    project's locks, and no envelope path takes a project lock under it."""
    task, path = eh([edit_step(2)])
    finished = []

    def inside():
        context = contextvars.copy_context()
        thread = threading.Thread(target=lambda: context.run(run_envelope.decide, session, task["task_id"], "revoke"))
        thread.start()
        thread.join(timeout=5)
        finished.append(not thread.is_alive())
    fired = during(inside)
    until(harness_runner.Runner("a"), path, task, lambda r: r["steps"][1]["status"] != "pending")
    assert fired and finished == [True]
    assert envelope_of(path)["state"] == "revoked"


def test_the_handlers_lock_is_the_rlock_the_write_holds(eh, during):
    """(4) Under the run's context `PyPSAService.get_lock()` is the lock the
    guard holds, re-entered by the handler on the same thread."""
    task, path = eh([edit_step(2)])
    seen = []

    def inside():
        lock = PyPSAService.get_lock()
        seen.append((lock is ctx().mutation_lock, lock._is_owned()))
    fired = during(inside)
    record = until(harness_runner.Runner("a"), path, task, lambda r: r["steps"][1]["status"] != "pending")
    assert fired and seen == [(True, True)] and record["steps"][1]["status"] == "completed"


def test_a_missing_own_bump_refuses(eh, monkeypatch):
    """(5) The pickup expects exactly the dispatch seam's one bump; +0 is drift too."""
    task, path = eh([edit_step(2)])
    monkeypatch.setattr(dirty_state, "bump_revision", lambda ctx=None: dirty_state.revision(ctx))
    record = until(harness_runner.Runner("a"), path, task, stopped)
    assert record["run"]["status"] == "paused" and record["run"]["reason"] == "envelope_drift", record["run"]
    assert value("Generator", "G1", "p_nom_max") == 40.0 and envelope_of(path)["chain"] == []


def test_a_held_lock_at_pickup_refuses_with_no_write(eh, gap, monkeypatch):
    """(7) A lock timeout fails closed."""
    task, path = eh([edit_step(2)])
    monkeypatch.setattr(harness_runner, "LOCK_TIMEOUT_SECONDS", 0.2)
    target, held, release = ctx(), threading.Event(), threading.Event()

    def hold():
        with target.mutation_lock:
            held.set()
            release.wait(10)
    holder = threading.Thread(target=hold)

    def start_holding():
        holder.start()
        assert held.wait(5)  # the other holder has the lock before the worker asks for it
    fired = gap(start_holding)
    record = until(harness_runner.Runner("a"), path, task, stopped)
    release.set()
    holder.join(5)
    assert fired and record["run"]["status"] in ("paused", "needs_user"), record["run"]
    assert value("Generator", "G1", "p_nom_max") == 40.0 and envelope_of(path)["chain"] == []


def test_an_unreadable_identity_at_pickup_refuses_with_no_write(eh, gap, monkeypatch):
    """(7) An unreadable exact fingerprint fails closed."""
    task, path = eh([edit_step(2)])
    fired = gap(lambda: monkeypatch.setattr(run_envelope, "_exact", lambda ctx: None))
    record = until(harness_runner.Runner("a"), path, task, stopped)
    assert fired and record["run"]["status"] == "paused", record["run"]
    assert record["run"]["reason"] == "envelope_fingerprint_unreadable"
    assert value("Generator", "G1", "p_nom_max") == 40.0 and envelope_of(path)["chain"] == []


def test_a_receipt_settles_only_its_own_call(eh):
    """(2) Another call's receipt (a late worker's) never settles this step."""
    task, path = eh([])
    target = ctx()
    with target.mutation_lock:
        before, reason = run_envelope.chain_head(path, target)
        assert reason is None
        target.network.generators.at["G1", "p_nom_max"] = 45.0
        dirty_state.bump_revision(target)
        late = {"call_id": "an-earlier-call", **run_envelope.identity(target)}
    run_envelope.settle_own(path, "this-call", target, before, [late])
    assert envelope_of(path)["chain"] == []
    run_envelope.settle_own(path, "an-earlier-call", target, before, [late])
    assert [link["after_change"] for link in envelope_of(path)["chain"]] == ["an-earlier-call"]


def test_the_queued_solve_snapshots_its_config_under_the_held_locks(eh, monkeypatch):
    """The enqueue's solver-config snapshot is taken inside the guarded write:
    the identity compared is the one the job snapshots (issue 22 item 4)."""
    from routers import solve_queue as solve_queue_router
    original, seen = solve_queue_router._config_snapshot_for, []

    def snapshot(*args, **kwargs):
        seen.append((ctx().mutation_lock._is_owned(), ctx().solver_state_lock._is_owned()))
        return original(*args, **kwargs)
    monkeypatch.setattr(solve_queue_router, "_config_snapshot_for", snapshot)
    project_id = PyPSAService.get_active_context().project_uuid
    task, path = eh([{"id": "inputs.2", "tool": "solve_queue_enqueue", "args": {"project_id": str(project_id)}}])
    until(harness_runner.Runner("a"), path, task, lambda r: bool(seen) or r["steps"][1]["status"] != "pending")
    record = json.loads(path.read_text())
    assert seen and seen[0] == (True, True), (seen, record["run"].get("status"), record["run"].get("reason"), [(s["tool"], s["status"]) for s in record["steps"]])

