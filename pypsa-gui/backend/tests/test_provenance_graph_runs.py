"""
provenance-graph 04a: runs and results freshness on the graph (`services/provenance_graph/runs.py`).

The scenario table names, per scenario, what each consumer answers now and what it answered before this slice
(GK B V3): the project flow's `freshness_by_kind` (the header's input), `solve_freshness` (the chat stamp,
`chat_tools._with_freshness`, and `run_gates._fresh`) and `edit_card._results_stale`. No row moves toward fresh.
"""

from __future__ import annotations

import pandas as pd
import pypsa
import pytest

from services import workflow_tools
from services.provenance_graph import runs
from services.pypsa_service import PyPSAService

SOLVED, EXACT = (
    "assistant_solve_input_fingerprint",
    "assistant_solve_exact_input_fingerprint",
)


@pytest.fixture(autouse=True)
def _no_cache():
    runs._clear_cache_for_tests()
    yield
    runs._clear_cache_for_tests()


def _net():
    n = pypsa.Network()
    n.set_snapshots(range(3))
    n.add("Bus", "b")
    n.add("Generator", "g", bus="b", p_nom=10, marginal_cost=50)
    n.add("Load", "l", bus="b", p_set=5)
    return n


def _solve(n, ctx):
    """What a solve leaves: a dispatch that matches the network, both recorded fingerprints (as
    `workflow_tools._record_solve_inputs` writes them) and an optimal, completed state."""
    n.generators_t.p = pd.DataFrame({"g": [5.0, 5.0, 5.0]}, index=n.snapshots)
    n.meta[SOLVED] = workflow_tools._network_input_fingerprint(ctx)
    n.meta[EXACT] = workflow_tools._network_input_fingerprint(ctx, exact=True)
    ctx.solver_state.update(
        status="completed", condition="optimal", lopf_results=None, ac_pf_results=None
    )


@pytest.fixture
def solved(install_network):
    n = install_network(_net())
    ctx = PyPSAService.get_active_context()
    _solve(n, ctx)
    return n, ctx


def _answers(*, solving=False):
    from harness import edit_card
    from services import flow_service

    by_kind = flow_service._freshness_by_kind(
        flow_service._run_states(solving=solving), has_study=True
    )
    fresh = workflow_tools.solve_freshness()
    return {
        "optimisation": by_kind["optimisation"],
        "loadflow": by_kind["loadflow"],
        "study": by_kind["study"],
        "changed": fresh["inputs_changed_since_solve"],
        "current": fresh["results_current"],
        "edit_card": edit_card._results_stale(),
    }


def _value_edit(n, ctx):
    n.generators.at["g", "marginal_cost"] = 60


def _shape_edit(n, ctx):
    n.add("Generator", "g2", bus="b", p_nom=1)


def _raising(n, ctx, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("unreadable")

    monkeypatch.setattr(workflow_tools, "_network_input_fingerprint", boom)


def _unrecorded(n, ctx):
    n.meta.pop(SOLVED)
    n.meta.pop(EXACT)


def _plain_only(n, ctx):
    n.meta.pop(EXACT)  # an older save: the exact fingerprint was never recorded


def _loadflow(n, ctx, *, captured=None):
    ctx.solver_state["ac_pf_results"] = {}
    ctx.solver_state["ac_pf_network_revision"] = (
        ctx.network_revision if captured is None else captured
    )


def _study(n, ctx, *, revision="now"):
    record = {"status": "done"}
    if revision == "now":
        record["network_revision"] = int(ctx.network_revision or 0)
    elif revision is not None:
        record["network_revision"] = revision
    ctx.solver_state["eh_study"] = record


#: scenario → (now, before this slice) for each consumer. Keys: optimisation, loadflow, study (the flow), changed
#: (`inputs_changed_since_solve`), edit_card (`_results_stale`). "raise" was an exception out of `solve_freshness`.
SCENARIOS = {
    "solved, untouched": (
        [],
        dict(optimisation="fresh", changed=False, edit_card=False),
        dict(optimisation="fresh", changed=False, edit_card=False),
    ),
    "value edit, same dispatch shape": (
        [_value_edit],
        dict(optimisation="stale", changed=True, edit_card=True),
        dict(optimisation="fresh", changed=True, edit_card=True),
    ),
    "shape edit": (
        [_shape_edit],
        dict(optimisation="stale", changed=True, edit_card=True),
        dict(optimisation="stale", changed=True, edit_card=True),
    ),
    "current fingerprint unreadable": (
        [_raising],
        dict(optimisation="stale", changed=True, edit_card=True),
        dict(optimisation="fresh", changed="raise", edit_card=True),
    ),
    "no solve recorded its inputs (a loaded network)": (
        [_unrecorded],
        dict(optimisation="uncompared", changed=None, edit_card=False),
        dict(optimisation="fresh", changed=None, edit_card=False),
    ),
    "exact fingerprint never recorded": (
        [_plain_only],
        dict(optimisation="stale", changed=True, edit_card=True),
        dict(optimisation="fresh", changed=True, edit_card=False),
    ),
}

#: Worse is larger; a consumer may move up the order, never down (V3).
RANK = {"fresh": 0, False: 0, None: 1, "uncompared": 1, "stale": 2, True: 2, "raise": 2}


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_every_consumer_agrees_with_the_optimisation_verdict(solved, monkeypatch, name):
    n, ctx = solved
    steps, now, before = SCENARIOS[name]
    for step in steps:
        step(n, ctx, monkeypatch) if step is _raising else step(n, ctx)
    got = _answers()
    assert {k: got[k] for k in now} == now, name
    verdict = runs.results_verdicts(runs.read())["optimisation"].state
    assert got["optimisation"] == (
        verdict if verdict in ("fresh", "stale") else "uncompared"
    )
    # The chat stamp and the header agree: stale exactly when the verdict is.
    assert (
        (got["changed"] is True) == (got["optimisation"] == "stale") == got["edit_card"]
    )
    # `run_gates._fresh` reads `results_current`: never current unless the verdict is fresh (V3: None is not fresh).
    assert got["current"] is (got["optimisation"] == "fresh")
    for key in now:
        assert RANK[now[key]] >= RANK[before[key]], (name, key)


def test_run_gates_fresh_treats_no_recorded_inputs_as_not_fresh(solved):
    from services import run_gates

    n, ctx = solved
    env = run_gates.Env.__new__(run_gates.Env)
    object.__setattr__(env, "ctx", ctx)
    assert run_gates._fresh(env) is True
    _unrecorded(n, ctx)
    assert workflow_tools.solve_freshness()["inputs_changed_since_solve"] is None
    assert run_gates._fresh(env) is False


# ── the load flow and the study ─────────────────────────────────────────────


def test_the_load_flow_reads_its_own_counter(solved):
    n, ctx = solved
    _loadflow(n, ctx)
    assert _answers()["loadflow"] == "fresh"
    ctx.network_revision = int(ctx.network_revision or 0) + 1
    assert _answers()["loadflow"] == "stale"


@pytest.mark.parametrize("captured", ["7", True, 1.0])
def test_a_load_flow_counter_that_is_not_an_int_reads_stale(solved, captured):
    n, ctx = solved
    ctx.network_revision = 1
    _loadflow(n, ctx, captured=captured)
    assert _answers()["loadflow"] == "stale"


def test_the_load_flow_on_a_stale_dispatch_is_stale_through_the_graph(solved):
    """No edit seam was crossed (the counter still matches), but the dispatch it read no longer describes the
    inputs: the load flow is stale through `derived_from` to the optimisation, not by its own counter."""
    n, ctx = solved
    _loadflow(n, ctx)
    _value_edit(n, ctx)
    got = runs.results_verdicts(runs.read())["loadflow"]
    assert got.state == "stale"
    assert [b.edge for b in got.broken] == ["computed_in"]
    assert got.broken[0].src == runs.results_id("optimisation")
    assert _answers()["loadflow"] == "stale"


def test_a_load_flow_on_a_loaded_network_is_uncompared(solved):
    n, ctx = solved
    _loadflow(n, ctx)
    _unrecorded(n, ctx)
    assert _answers()["loadflow"] == "uncompared"


@pytest.mark.parametrize(
    "revision, want", [("now", "fresh"), (-1, "stale"), (None, "uncompared")]
)
def test_the_study_reads_edited_since(solved, revision, want):
    n, ctx = solved
    _study(n, ctx, revision=revision)
    assert _answers()["study"] == want


def test_nothing_ran_reads_not_run_and_hashes_nothing(install_network, monkeypatch):
    install_network(_net())
    calls = []
    real = workflow_tools._network_input_fingerprint
    monkeypatch.setattr(
        workflow_tools,
        "_network_input_fingerprint",
        lambda *a, **k: calls.append(1) or real(*a, **k),
    )
    got = _answers()
    assert (got["optimisation"], got["loadflow"], got["study"]) == ("not_run",) * 3
    assert got["changed"] is None and got["edit_card"] is False
    assert calls == []


def test_the_one_word_takes_the_worst_kind():
    from services import flow_service as F

    word = F._freshness
    assert word({"optimisation": "fresh", "study": "stale"}, running=False) == "stale"
    assert (
        word({"optimisation": "uncompared", "study": "fresh"}, running=False)
        == "uncompared"
    )
    assert word({"optimisation": "fresh", "study": "not_run"}, running=False) == "fresh"
    assert (
        word({"optimisation": "not_run", "study": "not_run"}, running=False)
        == "not_run"
    )
    assert word({"optimisation": "stale", "study": "stale"}, running=True) == "running"


# ── V1, V2: one wrapper; None never matches ─────────────────────────────────


def test_every_alias_returns_the_one_wrapper_s_bytes(solved, monkeypatch):
    from harness import edit_card
    from services import assistant_tasks, run_envelope, run_evidence, run_gates

    _, ctx = solved
    plain = runs.input_fingerprint(ctx)
    exact = runs.input_fingerprint(ctx, exact=True)
    assert plain == workflow_tools._network_input_fingerprint(ctx)
    assert exact == workflow_tools._network_input_fingerprint(ctx, exact=True)
    assert run_gates.fingerprint(ctx) == run_envelope._fingerprint(ctx) == plain
    assert run_evidence._input_fingerprint(ctx) == plain
    assert assistant_tasks._fingerprint() == edit_card._fingerprint() == plain
    assert run_envelope._exact(ctx) == exact
    # An empty digest and no context are no fingerprint (V1).
    monkeypatch.setattr(
        workflow_tools, "_network_input_fingerprint", lambda *a, **k: ""
    )
    for got in (
        run_gates.fingerprint(ctx),
        run_envelope._fingerprint(ctx),
        run_envelope._exact(ctx),
        run_evidence._input_fingerprint(ctx),
        assistant_tasks._fingerprint(),
        edit_card._fingerprint(),
        runs.input_fingerprint(None),
    ):
        assert got is None


def test_the_fingerprint_bytes_are_pinned(install_network):
    """P4: the digest every stored record holds is unchanged by this slice."""
    install_network(_net())
    ctx = PyPSAService.get_active_context()
    assert runs.input_fingerprint(ctx) == PINNED
    assert runs.input_fingerprint(ctx, exact=True) == PINNED_EXACT


#: `_net()`'s fingerprints on master `e264eecbf`, before this slice.
PINNED = "c561840daf49d3d23c8883389725934c1e5250d805a01222accb9e725f20304b"
PINNED_EXACT = "1d5934ea55ff7a5f1e0ad35f45fa7404f5e84fad7601bc0c8991cfc114b541b5"


def test_a_recorded_none_against_a_current_none_is_never_fresh(solved, monkeypatch):
    """V2: no recorded fingerprint and no readable current one compare nothing: the verdict is uncompared, the
    chat says None (no claim), and nothing reads fresh or current."""
    n, ctx = solved
    _unrecorded(n, ctx)
    _raising(n, ctx, monkeypatch)
    r = runs.read()
    assert r.solved is None and r.current is None
    assert runs.results_verdicts(r)["optimisation"].state == "uncompared"
    got = _answers()
    assert got["optimisation"] == "uncompared" and got["changed"] is None
    assert got["current"] is False


def test_an_empty_recorded_fingerprint_is_no_record(solved, monkeypatch):
    n, ctx = solved
    n.meta[SOLVED] = ""
    monkeypatch.setattr(
        workflow_tools, "_network_input_fingerprint", lambda *a, **k: ""
    )
    assert runs.read().solved is None
    assert _answers()["optimisation"] == "uncompared"


# ── V4: one read ────────────────────────────────────────────────────────────


def test_an_edit_right_after_the_read_is_never_read_as_fresh(solved, monkeypatch):
    """#1384 N1: an edit (with its seam's counter bump) that lands right after the fingerprint was read: the read
    is taken again, and the verdict describes the edited network."""
    n, ctx = solved
    real = runs._current
    landed = []

    def then_edit(*a, **k):
        out = real(*a, **k)
        if not landed:
            landed.append(1)
            _value_edit(n, ctx)
            ctx.network_revision = int(ctx.network_revision or 0) + 1
        return out

    monkeypatch.setattr(runs, "_current", then_edit)
    assert runs.results_verdicts(runs.read())["optimisation"].state == "stale"


def test_a_network_that_keeps_moving_reads_unreadable(solved, monkeypatch):
    n, ctx = solved
    real = runs._current

    def always(*a, **k):
        out = real(*a, **k)
        ctx.network_revision = int(ctx.network_revision or 0) + 1
        return out

    monkeypatch.setattr(runs, "_current", always)
    r = runs.read()
    assert r.current is None
    assert runs.results_verdicts(r)["optimisation"].state == "stale"


# ── V5: the flow's cache ────────────────────────────────────────────────────


def test_the_flow_hashes_once_per_edit(solved, monkeypatch):
    n, ctx = solved
    calls = []
    real = workflow_tools._network_input_fingerprint
    monkeypatch.setattr(
        workflow_tools,
        "_network_input_fingerprint",
        lambda *a, **k: calls.append(k.get("exact", False)) or real(*a, **k),
    )
    for _ in range(3):
        assert (
            runs.results_verdicts(runs.read(cached=True))["optimisation"].state
            == "fresh"
        )
    assert calls == [False, True]
    # An edit through a seam bumps the counter: the next read hashes again and sees it.
    _value_edit(n, ctx)
    ctx.network_revision = int(ctx.network_revision or 0) + 1
    assert (
        runs.results_verdicts(runs.read(cached=True))["optimisation"].state == "stale"
    )
    assert calls == [False, True, False]
    # A new network object on the same context is never served the old one's hash.
    other = n.copy()
    other.meta = dict(n.meta)
    ctx.network = other
    runs.read(cached=True)
    assert calls == [False, True, False, False]


# ── what the mutants asked for (V9) ─────────────────────────────────────────


def test_an_unreadable_current_fingerprint_is_named_as_such(solved, monkeypatch):
    n, ctx = solved
    _raising(n, ctx, monkeypatch)
    (broke,) = runs.results_verdicts(runs.read())["optimisation"].broken
    assert broke.edge == "computed_in" and broke.reason.startswith("cannot be read now")


def test_no_context_or_no_network_reads_nothing(monkeypatch):
    import types

    def boom():
        raise RuntimeError("no active project")

    monkeypatch.setattr(PyPSAService, "get_active_context", boom)
    for r in (runs.read(), runs.read(types.SimpleNamespace(network=None))):
        assert (r.dispatch, r.solved, r.current) == ("none", None, None)
        assert runs.results_verdicts(r)["optimisation"].state == "uncompared"
    assert runs.active_input_fingerprint() is None


def test_a_bare_context_is_read_with_its_defaults():
    """A context with no solver state, edit counter or lock: read, never raised."""
    import types

    r = runs.read(
        types.SimpleNamespace(network=_net(), solver_state=None, network_revision=None)
    )
    assert r.solved is None and r.revision == 0 and r.study_done is False
    assert r.loadflow_ran is False


def test_the_lock_is_released_after_the_read(solved):
    import threading

    _, ctx = solved
    runs.read()
    got = []

    def other():
        ok = ctx.mutation_lock.acquire(timeout=2)
        got.append(ok)
        if ok:
            ctx.mutation_lock.release()

    t = threading.Thread(target=other)
    t.start()
    t.join()
    assert got == [True]


def test_only_the_active_context_reads_the_transient_rows(solved, monkeypatch):
    import copy

    from services import dispatch_status as D

    _, ctx = solved
    seen = []
    real = D.dispatch_status
    monkeypatch.setattr(
        D,
        "dispatch_status",
        lambda n, *, transient=None: (
            seen.append(transient) or real(n, transient=transient)
        ),
    )
    runs.read(ctx)
    runs.read(copy.copy(ctx))
    assert seen == [None, {}]


def test_a_network_swapped_during_the_read_is_unreadable(solved, monkeypatch):
    n, ctx = solved
    real = runs._current

    def swap(*a, **k):
        out = real(*a, **k)
        other = n.copy()
        other.meta = dict(n.meta)
        ctx.network = other  # no counter bump: the object identity alone moved
        return out

    monkeypatch.setattr(runs, "_current", swap)
    assert runs.read().current is None


@pytest.mark.parametrize("status", ["running", "failed"])
def test_a_study_that_is_not_done_reads_not_run(solved, status):
    n, ctx = solved
    ctx.solver_state["eh_study"] = {"status": status, "network_revision": 0}
    assert _answers()["study"] in ("not_run", "running")
    assert runs.read().study_done is False


def test_the_flow_s_study_is_null_without_a_study_and_the_load_flow_needs_results(
    solved,
):
    from services import flow_service as F

    n, ctx = solved
    states = F._run_states(solving=False)
    assert F._freshness_by_kind(states, has_study=False)["study"] is None
    assert F._freshness_by_kind(states, has_study=True)["study"] == "not_run"
    assert states["loadflow"] == "not_run"  # results, but no load flow ran
    _loadflow(n, ctx)
    n.generators_t.p = n.generators_t.p.iloc[:, :0]  # an edit cleared the dispatch
    assert F._run_states(solving=False)["loadflow"] == "not_run"


def test_results_current_still_needs_check_answers_guard(solved):
    """A fresh verdict is not enough: a solve that did not finish optimal is never current."""
    _, ctx = solved
    ctx.solver_state["condition"] = "infeasible"
    got = workflow_tools.solve_freshness()
    assert got == {"inputs_changed_since_solve": False, "results_current": False}


def test_loadflow_current_is_the_flow_s_load_flow_word(solved):
    """`run_lifecycle.loadflow_current` (the chat's re-run check, `project_readiness`) says what the flow's
    `freshness_by_kind.loadflow` says."""
    from harness import run_lifecycle

    n, ctx = solved
    assert run_lifecycle.loadflow_current(ctx) is False  # no load flow ran
    _loadflow(n, ctx)
    assert run_lifecycle.loadflow_current(ctx) is True
    assert _answers()["loadflow"] == "fresh"
    kept = n.generators_t.p
    n.generators_t.p = kept.iloc[:, :0]  # the dispatch cleared, nothing else moved
    assert run_lifecycle.loadflow_current(ctx) is False
    n.generators_t.p = kept
    _value_edit(n, ctx)  # the dispatch it read no longer describes the inputs
    assert run_lifecycle.loadflow_current(ctx) is False
    # The seam's counter bump, which the flow's cache keys on.
    ctx.network_revision = int(ctx.network_revision or 0) + 1
    assert _answers()["loadflow"] == "stale"


# ── locks (#1384 N1) ─────────────────────────────────────────────────────────


def test_a_mutation_lock_wait_that_times_out_reads_unreadable(solved, monkeypatch):
    """A network held mid-edit past `LOCK_WAIT_S` is never compared unlocked: the current fingerprint reads as
    unreadable, so the solved results are stale, and the hash is not read at all."""
    import threading

    _, ctx = solved
    assert runs.results_verdicts(runs.read())["optimisation"].state == "fresh"
    monkeypatch.setattr(runs, "LOCK_WAIT_S", 0.01)
    hashed = []
    real = workflow_tools._network_input_fingerprint
    monkeypatch.setattr(
        workflow_tools,
        "_network_input_fingerprint",
        lambda *a, **k: hashed.append(1) or real(*a, **k),
    )
    held, done = threading.Event(), threading.Event()

    def editor():
        with ctx.mutation_lock:
            held.set()
            done.wait(5)

    t = threading.Thread(target=editor)
    t.start()
    try:
        assert held.wait(5)
        r = runs.read()
    finally:
        done.set()
        t.join()
    assert r.current is None and r.solved is not None and hashed == []
    assert runs.results_verdicts(r)["optimisation"].state == "stale"
    # The lock free again: fresh, as before.
    assert runs.results_verdicts(runs.read())["optimisation"].state == "fresh"


def test_solver_state_is_read_under_its_lock_inside_the_mutation_lock(solved):
    """Lock order: the mutation lock, then `solver_state_lock` (as `loadflow_current` took it)."""
    _, ctx = solved
    seen = []
    real_state = ctx.solver_state_lock

    class Spy:
        def __enter__(self):
            # An RLock held by this thread can be taken again; another thread's cannot.
            seen.append(ctx.mutation_lock._is_owned())
            return real_state.__enter__()

        def __exit__(self, *exc):
            return real_state.__exit__(*exc)

    ctx.solver_state_lock = Spy()
    try:
        runs.read()
    finally:
        ctx.solver_state_lock = real_state
    assert seen == [True]


# ── V2: a recorded None against a current None (the touched comparers) ───────


def test_the_edit_card_never_reads_none_against_none_as_unchanged(solved, monkeypatch):
    """`edit_card._results_stale`: no recorded fingerprint compares nothing (uncompared, not claimed stale, as
    before); a recorded one against an unreadable current one is stale."""
    from harness import edit_card

    n, ctx = solved
    _raising(n, ctx, monkeypatch)
    assert edit_card._fingerprint() is None
    assert edit_card._results_stale() is True  # recorded vs None
    _unrecorded(n, ctx)
    assert edit_card._results_stale() is False  # None vs None: no claim either way
    assert runs.results_verdicts(runs.read())["optimisation"].state == "uncompared"


def test_the_envelope_chain_never_reads_none_against_none_as_no_drift(solved, monkeypatch):
    """`run_envelope.chain_head`: a chain whose last link holds None and a current None is unreadable, not 'no
    drift'."""
    from services import run_envelope

    n, ctx = solved
    _raising(n, ctx, monkeypatch)
    monkeypatch.setattr(run_envelope, "live", lambda _p: True)
    monkeypatch.setattr(
        run_envelope,
        "_load",
        lambda _p: {"id": "e", "state": "granted", "chain": [], "fingerprint": None},
    )
    assert run_envelope.chain_head("unused", ctx) == (None, "fingerprint_unreadable")


def test_a_brief_never_reads_none_against_none_as_current(solved, monkeypatch):
    """`assistant_tasks._stale`: a brief recorded on None against a current None is stale (fail closed)."""
    from services import assistant_tasks

    n, ctx = solved
    _raising(n, ctx, monkeypatch)
    task = {"id": "t", "brief": {"x": 1}, "status": "ready", "fingerprint": None}
    assert assistant_tasks._stale(task) is True
