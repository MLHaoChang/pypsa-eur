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

import contextlib
import threading
import weakref
from dataclasses import dataclass
from typing import Any

from services.provenance_graph import pins
from services.provenance_graph.types import Edge, Node, Verdict

KINDS = ("optimisation", "study", "loadflow")

_SOLVED = "assistant_solve_input_fingerprint"
_SOLVED_EXACT = "assistant_solve_exact_input_fingerprint"


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
# project flow: the flow reads through this cache. An entry is served only for the exact state it was computed
# from (`_stamp`): the context and network objects, the edit counter (`network_revision`, bumped by every edit
# seam), the input generation (`dirty_state.bump_inputs`: the chat seam after its handler, and every writer of a
# hashed input that crosses no edit seam), and a digest of the hashed inputs kept outside the network (the
# goal-free config, the DC spec and island sidecars). One hash per state at a time (single flight): a concurrent
# poll waits for it instead of hashing again. The chat and the edit card read live.
_cache: dict[tuple[int, bool], tuple[Any, Any, tuple, str | None]] = {}
_flights: dict[tuple[int, bool], threading.Lock] = {}
_cache_lock = threading.Lock()
_MISS = object()


def _stamp(ctx) -> tuple:
    """The state a cached fingerprint stands for, besides the context and network objects (see above)."""
    import hashlib
    import json
    import pathlib

    from services.reliability_goal import goal_free_config

    h = hashlib.sha256()
    try:
        config = (getattr(ctx, "solver_state", None) or {}).get("solver_config")
        h.update(
            json.dumps(goal_free_config(config), sort_keys=True, default=str).encode()
        )
    except Exception:  # noqa: BLE001 — an unreadable config is its own state
        h.update(b"config unreadable")
    d = getattr(ctx, "storage_dir", None)
    for name in ("eh_template.json", "island_config.json"):
        h.update(name.encode())
        try:
            h.update(pathlib.Path(d, name).read_bytes() if d else b"no project dir")
        except OSError:
            h.update(b"absent")
    return (
        _revision(ctx),
        int(getattr(ctx, "input_generation", 0) or 0),
        h.hexdigest(),
    )


def _lookup(key, ctx, network, stamp):
    with _cache_lock:
        hit = _cache.get(key)
    if hit is None:
        return _MISS
    ctx_ref, net_ref, at, fp = hit
    return fp if ctx_ref() is ctx and net_ref() is network and at == stamp else _MISS


def _cached_fingerprint(ctx, stamp: tuple, *, exact: bool) -> str | None:
    key = (id(ctx), exact)
    network = ctx.network
    fp = _lookup(key, ctx, network, stamp)
    if fp is not _MISS:
        return fp
    with _cache_lock:
        flight = _flights.setdefault(key, threading.Lock())
    with flight:  # single flight: a concurrent poll of the same state waits for this hash, never the mutation lock
        fp = _lookup(key, ctx, network, stamp)
        if fp is not _MISS:
            return fp
        fp = input_fingerprint(ctx, exact=exact)
        # Stored only when nothing moved during the hash: the entry stands for the state it was read from.
        if ctx.network is not network or _stamp(ctx) != stamp:
            return fp
        try:
            entry = (weakref.ref(ctx), weakref.ref(network), stamp, fp)
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
        _flights.clear()


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
    # Lock order: mutation, then solver_state. A context with no solver_state lock has nothing to wait for.
    with getattr(ctx, "solver_state_lock", None) or contextlib.nullcontext():
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


def _try_locked_records(ctx) -> dict:
    """The run records, under the network's mutation lock when it is free. A freshness read never waits for it
    (as `preflight_validate`): a solve holds it for the whole solve, and other holders (a save, a hand-over) change
    no input. When it is held the records are read as they stand; the edit-counter bracket in `read` still catches an
    edit that lands."""
    lock = getattr(ctx, "mutation_lock", None)
    if lock is None or not lock.acquire(blocking=False):
        return _records(ctx)
    try:
        return _records(ctx)
    finally:
        lock.release()


def read(ctx=None, *, cached: bool = False) -> RunsRead:
    """One read of the run records and the current inputs (#1384 N1): the records (under the mutation lock when it
    is free, never waiting for it; `solver_state` under its own lock), the fingerprint between two reads of the edit
    counter and network object (and, `cached`, the cache stamp). An edit that lands meanwhile is read again once; a
    second move reads the current fingerprint as unreadable (stale)."""
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
        stamp = _stamp(ctx) if cached else None
        records = _try_locked_records(ctx)
        current = _current(ctx, records, stamp=stamp)
        moved = ctx.network is not network or _revision(ctx) != records["revision"]
        if not moved and (stamp is None or _stamp(ctx) == stamp):
            return RunsRead(current=current, **records)
    return RunsRead(current=None, **records)


def _revision(ctx) -> int:
    return int(getattr(ctx, "network_revision", 0) or 0)


def _current(
    ctx, records: dict, *, stamp: tuple | None
) -> tuple[str, str | None] | None:
    solved = records["solved"]
    if solved is None:
        return None  # nothing recorded to compare with: the hash is not read

    def fp(exact):
        if stamp is not None:
            return _cached_fingerprint(ctx, stamp, exact=exact)
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
