"""
Runs and results freshness on the graph (provenance-graph 04a, spec "Runs and results").

One wrapper reads the input fingerprint (`input_fingerprint`); the readers the spec's survey names that kept their
own copy are aliases of it.
`read` takes what the run verdicts compare in one go (`RunsRead`), and `results_verdicts` answers, per run kind,
whether the results still describe the current inputs:

- `results:optimisation` -computed_in-> `run:optimisation`: the fingerprints the solve recorded (non-exact, exact)
  against the current ones; it also `derived_from` the dispatch shape (a dispatch whose columns no longer match the
  network is stale). No solve recorded its inputs: uncompared, never fresh (#1170).
- `results:loadflow` -computed_in-> `run:loadflow`: the edit counter the load flow recorded against the current one;
  and `derived_from` `results:optimisation`, so a stale dispatch makes the load flow stale through the graph.
- `results:study` -computed_in-> `run:study`: whether an edit seam was crossed since the study started
  (`study_state.edited_since`); a record with no captured revision is uncompared.

An unreadable current fingerprint is stale (fail closed, ADR-0001). Reads only: nothing is written.
"""

from __future__ import annotations

import threading
import weakref
from dataclasses import dataclass
from typing import Any

from services.provenance_graph import pins
from services.provenance_graph.types import Edge, Node, Verdict

KINDS = ("optimisation", "study", "loadflow")

_SOLVED = "assistant_solve_input_fingerprint"
_SOLVED_EXACT = "assistant_solve_exact_input_fingerprint"
#: How long `read` waits for the network's mutation lock for its record reads; past it the current fingerprint reads
#: as unreadable (stale), never as an unlocked read (#1384 N1).
LOCK_WAIT_S = 0.5


def results_id(kind: str) -> str:
    return f"results:{kind}"


def run_id(kind: str) -> str:
    return f"run:{kind}"


# ── the one fingerprint wrapper ──────────────────────────────────────────────


def input_fingerprint(ctx, *, exact: bool = False) -> str | None:
    """The context's input fingerprint (solver outputs excluded), or None when it cannot be read: no context, a
    network the hash cannot read, or an empty digest. None is never a fingerprint, so it never matches one."""
    from services import workflow_tools

    try:  # no context raises too: None
        return workflow_tools._network_input_fingerprint(ctx, exact=exact) or None
    except Exception:  # noqa: BLE001 — unreadable inputs are no evidence
        return None


def active_input_fingerprint(*, exact: bool = False) -> str | None:
    """`input_fingerprint` of the active project's context."""
    from services.pypsa_service import PyPSAService

    try:
        ctx = PyPSAService.get_active_context()
    except Exception:  # noqa: BLE001
        return None
    return input_fingerprint(ctx, exact=exact)


# A full-year hourly network takes about a second to hash (several for the exact form), and the header polls the
# project flow: the flow reads through this cache, keyed on the context, its network object and its edit counter
# (every edit seam bumps it: `project_context.network_revision`). The chat and the edit card read live.
_cache: dict[tuple[int, bool], tuple[Any, Any, int, str | None]] = {}
_cache_lock = threading.Lock()


def _cached_fingerprint(ctx, revision: int, *, exact: bool) -> str | None:
    key = (id(ctx), exact)
    network = ctx.network
    with _cache_lock:
        hit = _cache.get(key)
    if hit is not None:
        ctx_ref, net_ref, rev, fp = hit
        if ctx_ref() is ctx and net_ref() is network and rev == revision:
            return fp
    fp = input_fingerprint(ctx, exact=exact)
    try:
        entry = (weakref.ref(ctx), weakref.ref(network), revision, fp)
    except TypeError:  # an object that takes no weak reference is never cached
        return fp
    with _cache_lock:
        if len(_cache) > 16:
            _cache.clear()
        _cache[key] = entry
    return fp


def _clear_cache_for_tests() -> None:
    with _cache_lock:
        _cache.clear()


# ── one read ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RunsRead:
    """What the run verdicts compare, read once. `solved` is the solve's recorded `(fingerprint, exact)`, None when
    no solve recorded one; `current` is the current pair, `(fp, None)` when the non-exact one already differs (the
    exact hash is not needed), None when it cannot be read or the network moved during the read."""

    dispatch: str  # none | fresh | stale (`dispatch_status`)
    solved: tuple[str, str | None] | None
    current: tuple[str, str | None] | None
    loadflow_ran: bool
    loadflow_revision: (
        Any  # the edit counter the load flow recorded (`ac_pf_network_revision`)
    )
    study_done: bool
    study_edited: bool | None  # `study_state.edited_since`; None: no captured revision
    revision: int


def _records(ctx) -> dict:
    """The run records, read under the caller's locks: the solve's recorded fingerprints, the dispatch shape, the
    load flow's and the study's records, and the edit counter."""
    from services import study_state
    from services.dispatch_status import dispatch_status

    n = ctx.network
    meta = getattr(n, "meta", None) or {}
    solved = meta.get(_SOLVED) or None
    exact = meta.get(_SOLVED_EXACT)
    try:
        # A running solve's transient rows are the active context's own: another context (a saved one) has none.
        dispatch = dispatch_status(n, transient=None if _is_active(ctx) else {})
    except Exception:  # noqa: BLE001 — as the flow read it: no results to compare
        dispatch = "none"
    state_lock = getattr(ctx, "solver_state_lock", None)
    if state_lock is not None:
        with state_lock:  # lock order: mutation, then solver_state
            state = dict(getattr(ctx, "solver_state", None) or {})
    else:
        state = dict(getattr(ctx, "solver_state", None) or {})
    record = state.get("eh_study")
    revision = _revision(ctx)
    done = isinstance(record, dict) and record.get("status") == "done"
    return {
        "dispatch": dispatch,
        "solved": (solved, exact) if isinstance(solved, str) else None,
        "loadflow_ran": state.get("ac_pf_results") is not None,
        "loadflow_revision": state.get("ac_pf_network_revision"),
        "study_done": done,
        "study_edited": study_state.edited_since(record, revision=revision)
        if done
        else None,
        "revision": revision,
    }


def _is_active(ctx) -> bool:
    from services.pypsa_service import PyPSAService

    try:
        return PyPSAService.get_active_context() is ctx
    except Exception:  # noqa: BLE001
        return False


def _locked_records(ctx) -> tuple[dict, bool]:
    """The run records under the network's mutation lock, and whether the lock was held. A context with no lock
    has nothing to wait for. When the wait times out the records are still read (the caller then reads the current
    fingerprint as unreadable, so nothing compares fresh against a network mid-edit)."""
    lock = getattr(ctx, "mutation_lock", None)
    if lock is None:
        return _records(ctx), True
    if not lock.acquire(timeout=LOCK_WAIT_S):
        return _records(ctx), False
    try:
        return _records(ctx), True
    finally:
        lock.release()


def read(ctx=None, *, cached: bool = False) -> RunsRead:
    """One read of the run records and the current inputs (#1384 N1): the records under the network's mutation
    lock (and `solver_state` under its own lock inside it), the fingerprint between two reads of the edit counter
    and network object. An edit that lands meanwhile is read again once; a second move, or a mutation-lock wait past
    `LOCK_WAIT_S`, reads the current fingerprint as unreadable (stale)."""
    from services.pypsa_service import PyPSAService

    if ctx is None:
        try:
            ctx = PyPSAService.get_active_context()
        except Exception:  # noqa: BLE001
            ctx = None
    if ctx is None or getattr(ctx, "network", None) is None:
        return RunsRead("none", None, None, False, None, False, None, 0)
    for _attempt in range(2):
        network = ctx.network
        records, locked = _locked_records(ctx)
        if not locked:
            return RunsRead(current=None, **records)  # the lock wait timed out: unreadable, stale
        current = _current(ctx, records, cached=cached)
        if ctx.network is network and _revision(ctx) == records["revision"]:
            return RunsRead(current=current, **records)
    return RunsRead(current=None, **records)


def _revision(ctx) -> int:
    return int(getattr(ctx, "network_revision", 0) or 0)


def _current(ctx, records: dict, *, cached: bool) -> tuple[str, str | None] | None:
    solved = records["solved"]
    if solved is None:
        return None  # nothing recorded to compare with: the hash is not read

    def fp(exact):
        if cached:
            return _cached_fingerprint(ctx, records["revision"], exact=exact)
        return input_fingerprint(ctx, exact=exact)

    plain = fp(False)
    if plain is None:
        return None
    if plain != solved[0]:
        return (plain, None)  # already differs: the exact hash cannot make it match
    exact = fp(True)
    return None if exact is None else (plain, exact)


# ── the run graph ────────────────────────────────────────────────────────────


def _unreadable() -> Any:
    raise ValueError("the current inputs cannot be read")


def add_runs(b, r: RunsRead) -> None:
    """The run nodes and their `computed_in` edges for the read `r` (spec "Node kinds": `run`)."""
    opt, lf, st = (results_id(k) for k in ("optimisation", "loadflow", "study"))
    b.node(Node("run", run_id("optimisation"), r.solved))
    b.node(Node("derived", opt))
    b.node(Node("input", "input:dispatch_shape", r.dispatch))
    if r.solved is None:
        # No solve recorded its inputs (a loaded network): compared nothing, never fresh.
        b.edge(Edge("computed_in", opt, run_id("optimisation")))
    else:
        current = r.current
        b.edge(
            Edge(
                "computed_in",
                opt,
                run_id("optimisation"),
                pins.Pin(
                    "input fingerprint",
                    r.solved,
                    (lambda: current) if current is not None else _unreadable,
                ),
            )
        )
    dispatch = r.dispatch
    b.edge(
        Edge(
            "derived_from",
            opt,
            "input:dispatch_shape",
            pins.Pin(
                "dispatch matches the network",
                "matches",
                lambda: "differs" if dispatch == "stale" else "matches",
            ),
        )
    )

    b.node(Node("run", run_id("loadflow"), r.loadflow_revision))
    b.node(Node("derived", lf))
    captured = r.loadflow_revision
    valid = isinstance(captured, int) and not isinstance(captured, bool)
    revision = r.revision
    b.edge(
        Edge(
            "computed_in",
            lf,
            run_id("loadflow"),
            # A counter that is missing or not an int (an older save) reads stale, the safe direction.
            pins.Pin("network revision", captured if valid else None, lambda: revision),
        )
    )
    b.edge(Edge("derived_from", lf, opt))  # the dispatch the load flow read

    b.node(Node("run", run_id("study"), r.study_done))
    b.node(Node("derived", st))
    edited = r.study_edited
    b.edge(
        Edge(
            "computed_in",
            st,
            run_id("study"),
            None
            if edited is None
            else pins.Pin("edited since the study ran", False, lambda: edited),
        )
    )


def runs_graph(r: RunsRead, memory=None):
    from services.provenance_graph.queries import Builder

    b = Builder(r, memory)
    add_runs(b, r)
    return b.build()


def results_verdicts(r: RunsRead) -> dict[str, Verdict]:
    """Each run kind's verdict on the read `r` (`queries.staleness` through the run graph)."""
    from services.provenance_graph.queries import staleness

    g = runs_graph(r)
    return {k: staleness(g, results_id(k)) for k in KINDS}
