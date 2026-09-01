"""
Per-project in-memory state container.

Today the backend holds exactly ONE active project's network (the single
foreground project). `ProjectContext` encapsulates everything that is logically
*per project* so that the multi-project work (see
`~/.claude/plans/multi-project-architecture-v5.md`) can later hold a registry of
these keyed by `project_id` without touching the ~110
`PyPSAService.get_network()` call sites — those keep calling the same classmethod
API, which delegates to the active context.

Phase 0 scope: introduce this container and have `PyPSAService` delegate to a
single `_active` instance. Behaviour is byte-identical to the previous loose
class attributes — only the internal layout changes. The multi-entry registry
and per-project locks are deferred to Phase A/B (where retained concurrent
contexts make them earn their keep); per-project locking without concurrent
contexts would be premature.

What is per-context vs global:
  * Per-context (here): the network, its on-disk identity, and its transient-row
    registry — all meaningless across projects.
  * Global (stays on PyPSAService): the mutation lock and the netCDF I/O lock.
    The latter guards process-global, thread-unsafe HDF5 library state and MUST
    remain a single shared instance even once multiple contexts are resident.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from dataclasses import fields as _dc_fields
from typing import Any

import pypsa

# Module-level import is cycle-safe: undo_service imports nothing from services
# at module load (its PyPSAService reach is lazy, inside _active()).
from services.undo_service import _UndoState


@dataclass
class ChatState:
    """
    Per-project chatbot conversation state (Phase 0 stub).

    The chatbot integration v6 plan keeps conversation history per project,
    stored on disk at ``projects/<loaded_project>/chat.jsonl`` and held in
    memory on the project's ProjectContext. Phase 0 lands the SHAPE only:
    the `session` field carries a `chat_service.ChatSession` at runtime
    (typed `Any` to avoid a `project_context -> chat_service` import cycle),
    `persist_path` caches the resolved chat.jsonl absolute path, and `lock`
    guards append + rotation under one critical section.

    Lifecycle invariants (Phase 0 QA Gate A verifies):
      * Carried forward by `reset_network` / `set_network` (same project,
        network swap) — same shape as `solver_state` / `undo` / `mutation_lock`
        so single-project autosave + chat history surviving every swap is
        byte-identical to those globals' carry semantics.
      * NOT carried by `build_context` (background work for B4.3 dispatcher
        gets its own fresh chat state — the foreground's session must not
        be visible to background-solve agents).
      * Flushed via `chat_service.flush_to_disk(ctx)` by `_save_evicted_ctx`
        AFTER `_save_context` succeeds (inside the same try/except so a flush
        failure is logged and the eviction still completes).

    Locking:
      * `lock` is acquired by `chat_service.append_turn` for the entire
        write + rotation cycle (M9 + v4-MINOR-2 — rotation cannot race a
        concurrent append). Distinct from `ChatSession._lock` (which guards
        in-memory session mutations like usage accumulators, pending
        confirmations) so a long-running disk write does not block a
        memory-only session update.
    """

    session: Any = None
    persist_path: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


def _default_solver_state() -> dict[str, Any]:
    """
    Fresh solver-state dict with a default SolverConfig — the per-context
    equivalent of the old module-init
    ``_state = ProjectSolverState(solver_config=SolverConfig()).as_dict()``.

    SolverConfig is imported lazily (at instance-creation time, never at module
    load) so this module stays free of a
    project_context -> solver_service -> pypsa_service -> project_context import
    cycle. ``ProjectSolverState`` is defined later in this module; the forward
    reference resolves at call time.
    """
    from services.solver_service import SolverConfig  # noqa: PLC0415

    return ProjectSolverState(solver_config=SolverConfig()).as_dict()


@dataclass
class ProjectContext:
    """All per-project in-memory state for ONE project's network."""

    # The in-memory PyPSA network this context owns.
    network: pypsa.Network

    # Authoritative on-disk identity of the project this network belongs to, or
    # None when fresh/unbound. DECOUPLED from the mutable `network.name` display
    # title — the save path's `expect`/`rebind` identity guard trusts this, not
    # the title. Set only by load-class ops + the first-save claim.
    #
    # This is the human-readable NAME and is what the client sends and sees. It
    # is deliberately NOT the registry key any more — see `registry_key` below.
    loaded_project: str | None = None

    # ── Tenant identity (Step 0a) ───────────────────────────────────────────
    # Which org's project this is, and its immutable row id. Set together by
    # `PyPSAService.bind_project`; both None on an unbound context.
    org_id: str | None = None
    project_uuid: str | None = None
    # The project's RESOLVED storage directory — `project_registry.project_dir`,
    # never the raw `Project.storage_path`, which is relative from phase 1b on
    # and no longer org-scoped in local mode. Held on
    # the context so an eviction save writes to the row's real location instead
    # of re-deriving a path from the display name — which was wrong the moment
    # two orgs owned the same name.
    storage_dir: str | None = None

    # Transient LP-scaffolding rows the solver adds for the duration of one solve
    # (vintage clones `parent@<year>`, VOLL slacks `__voll_<bus>` — convention
    # owned by services/adequacy/slack.py) and reverts in
    # restore(). Keyed `{component_class: {name, …}}`. Hidden from GET reads so a
    # user never sees solver internals as asset rows. Per-network by construction
    # — each context's expansion is its own.
    transient_rows: dict[str, set[str]] = field(default_factory=dict)
    transient_lock: threading.Lock = field(default_factory=threading.Lock)

    # Solver lifecycle + result state for THIS project — the per-context form of
    # the old module-global `routers.simulation._state`. The `_state` proxy in
    # simulation.py forwards every access to the ACTIVE context's dict here.
    # reset_network/set_network carry this (and its lock) forward, so single-
    # project behaviour is byte-identical to the pre-split module global (which
    # survived every network swap). B2's registry gives each resident project
    # its own; B4's dispatcher writes a background project's state directly.
    solver_state: dict[str, Any] = field(default_factory=_default_solver_state)
    # Guards multi-key reads/writes of `solver_state` (the old `_state_lock`).
    # RLock: the /run claim holds it while calling helpers that re-acquire it.
    solver_state_lock: threading.RLock = field(default_factory=threading.RLock)

    # Per-project undo stack + byte accounting + coalesce timestamp (B3). The
    # old module-global undo deque, now per-context; undo_service operates on the
    # ACTIVE context's instance. Carried forward across reset_network/set_network
    # (byte-identical to the old global, which survived every swap); B8 stops
    # carrying it so each resident project keeps its own undo history.
    undo: _UndoState = field(default_factory=_UndoState)

    # LRU recency stamp (B9). `time.monotonic()` of the last time this context
    # became active, was activated, was registered, or was returned as a
    # resident path-scoped read. The eviction policy (PyPSAService._evict_if_
    # over_cap) treats the SMALLEST stamp as least-recently-interacted. Monotonic
    # (not wall-clock) because it's purely an ordering key — never displayed and
    # immune to clock adjustments. Defaults 0.0 so a never-touched ctx sorts as
    # oldest. The frontend `touchTab` is the UI mirror; THIS stamp is the
    # authority for eviction.
    last_interacted_at: float = 0.0

    # Per-context network-mutation lock (B4). What PyPSAService.get_lock() returns
    # (= the active ctx's) once Increment 2 wires it. Guards this ctx's in-memory
    # network mutations + its loaded_project bind + dispatch writes. NOT carried
    # forward across reset/set — each resident ctx owns its lock so a background
    # solve (B4.3) holds ITS lock while the foreground edits a DIFFERENT ctx under
    # that ctx's lock. RLock: save_project / the /run claim nest helpers that
    # re-acquire it. Unused until B4 Increment 2.
    mutation_lock: threading.RLock = field(default_factory=threading.RLock)

    # Per-project chatbot conversation state (chatbot integration v6 Phase 0).
    # Lifecycle mirrors `solver_state` / `undo` — carried forward by
    # reset_network / set_network so a same-project network swap preserves the
    # active chat session, NOT carried by build_context so background-solve
    # contexts (B4.3) get their own clean chat. Flushed to disk via
    # `chat_service.flush_to_disk` from `_save_evicted_ctx` (B9 eviction)
    # before the context is dropped. See ChatState above for field details.
    chat_state: ChatState = field(default_factory=ChatState)

    @property
    def registry_key(self) -> str | None:
        """
        Key under which this context is resident in `PyPSAService._contexts`.

        Until Step 0a the registry was keyed by the project NAME. Names are
        unique *within an org*, but the registry is *per process*, so two orgs
        that both had a project called `Baseline` shared one slot: whichever
        org activated second either got the other's resident network back or
        evicted it. Keying on `(org_id, project_uuid)` removes the collision at
        the source — a uuid belongs to exactly one org, and the org prefix
        keeps the key legible in logs and eviction messages.

        Falls back to the name for an UNBOUND context (`New Project` before its
        first save, which has no row to point at yet). That fallback is what
        Step 0b replaces with a per-session scratch identity; on one process
        with one active project it is still correct today.
        """
        if self.org_id and self.project_uuid:
            return f"{self.org_id}:{self.project_uuid}"
        return self.loaded_project


# ── Solver lifecycle + result state ──────────────────────────────────────────
# Canonical key groups for the simulation state (today's module-global
# `routers.simulation._state`). Defined here as the single source of truth so
# the dispatcher (Phase A) can instantiate one solver-state per project and the
# persistence layer (`routers.projects._RESULTS_STATE_KEYS`) derives from the
# same list rather than maintaining a parallel copy that can drift.
#
# LIFECYCLE — reset on every solve (status + the worker handles).
LIFECYCLE_KEYS = (
    "status", "condition", "objective", "solve_time",
    "thread", "stop_event", "log_queue", "last_failure",
)
# RESULT_STATE — the side-results persisted to `results_state.pkl`. Order matches
# the historical `_RESULTS_STATE_KEYS` tuple (the save/restore paths iterate it).
RESULT_STATE_KEYS = (
    "lopf_results", "ac_pf_results",
    "last_lost_load",
    "adequacy_report",
    "last_reserve_margin",
    "ac_pf_convergence", "ac_pf_convergence_list",
    "ac_pf_slack_bus_used", "ac_pf_stripped_voll_slacks",
    "ac_pf_converged_count", "ac_pf_total_snapshots",
)


# The keys under a project's solver state that hold a long-running STUDY
# record — the class-B/C sweep, the frontier, the sequential MC and the two
# planning loops.
#
# ★ Here rather than in `services/study_state.py` (which re-exports it) for two
# reasons. It is the same KIND of datum as RESULT_STATE_KEYS above — the
# enumerated contents of a solver_state — and keeping the two side by side is
# what makes it visible that the study keys are NOT in the result keys, which
# is precisely the bug this list was moved for: `reset_network` reset one list
# and not the other, so a finished study outlived the network it measured.
# And it keeps the import graph acyclic — `study_state` imports PyPSAService,
# so `pypsa_service` cannot import `study_state`, but it already imports this
# module.
STUDY_KEYS = ("fmea_sweep", "frontier", "mc", "coupling_loop", "margin_loop")

# What each study is called in a refusal. A user who is told "a study is
# running" cannot act; one who is told WHICH can go and deal with it.
STUDY_LABELS = {
    "fmea_sweep": "an FMEA sweep",
    "frontier": "a frontier study",
    "mc": "a sequential-MC study",
    "coupling_loop": "a coupling-loop study",
    "margin_loop": "a margin-loop study",
}

# The studies a user can actually STOP.
#
# ★ Only these two have an `/abort` route and a `stop_event`; `mc`, `frontier`
# and `fmea_sweep` have neither. This is load-bearing for the copy: the
# Phase-7 refusal sentence said "Wait for it to finish, or abort it" for EVERY
# study, which names a control that does not exist for three of the five. A
# refusal must never invent an action the user cannot take.
#
# Pinned by a test against the routes that actually exist, so this cannot
# drift the day someone gives the MC an abort.
ABORTABLE_STUDIES = ("coupling_loop", "margin_loop")


def record_is_running(record) -> bool:
    """True while a study record's worker thread is genuinely alive.

    ONE definition, called by `study_state.study_running` (the 409 mesh) and by
    `PyPSAService.reset_network` (which must not clear a live study out from
    under that mesh). study_state's own docstring makes the argument: a guard
    that differs between callers is not a guard.

    Testing `thread.is_alive()` and not just the status string matters: a
    crashed worker that never got to write its terminal status would otherwise
    wedge the surface permanently, and the user's only recovery would be a
    process restart.
    """
    if not record:
        return False
    try:
        thread = record.get("thread")
        if thread is None or record.get("status") != "running":
            return False
        # ★ A record can be PUBLISHED before its thread is STARTED. `post_mc`,
        # `post_frontier` and `post_fmea_sweep` do
        #     record["thread"] = t; _state[key] = record; t.start()
        # with no lock, so in that gap the record is visible while
        # `is_alive()` is still False. Reading that as "not running" was a
        # real hole: a swap was allowed AND Phase 10's clear nulled the live
        # study's record, after which the 409 mesh could no longer see it and
        # would admit a second study on the same network.
        #
        # `Thread.ident` is None until `start()` and set forever after, so it
        # distinguishes NEVER-STARTED from ran-and-finished exactly. That
        # distinction is what keeps this from re-introducing the wedge Phase
        # 11 avoided: a worker that died without writing a terminal status has
        # `ident` set, so it still reads as NOT running and cannot block every
        # network-replacing route for the rest of the session.
        return bool(thread.is_alive() or thread.ident is None)
    except Exception:                                         # noqa: BLE001
        return False


def running_study_key(state) -> str | None:
    """The key of the first live study in ``state``, or None.

    Pure: takes the state dict rather than reaching for the active project, so
    `pypsa_service` can call it without importing `study_state` (which imports
    PyPSAService — the cycle Phase 10 already had to route around).
    """
    if not state:
        return None
    for key in STUDY_KEYS:
        try:
            if record_is_running(state.get(key)):
                return key
        except Exception:                                     # noqa: BLE001
            continue
    return None


def study_swap_refusal(state, action: str) -> str | None:
    """The 409 detail for an action that would REPLACE the network, or None.

    ``action`` is what the user was trying to do ("load a project", "undo",
    "restore a snapshot"), because a refusal that does not say what it refused
    is a worse error than the bug it prevents.

    Why the swap is refused at all: a study's worker closes over the
    `pypsa.Network` object captured before it started, so replacing the
    network does not STOP the study — it DETACHES it. The study keeps solving
    the old object and keeps publishing into the solver state the swap carries
    forward, so the new project's Adequacy tab fills in live with the old
    project's study, and a `restore="final"` loop writes its certified value
    into the new project's solver config.
    """
    key = running_study_key(state)
    if key is None:
        return None
    label = STUDY_LABELS.get(key, key)
    remedy = ("Wait for it to finish, or abort it."
              if key in ABORTABLE_STUDIES else
              "It cannot be aborted, so wait for it to finish.")
    return (f"{label} is running on this network. Trying to {action} now "
            "would not stop it — the study holds the network it started on, "
            "so it would keep running against a network you are no longer "
            "looking at, and would publish its result over the new one. "
            + remedy)


@dataclass
class ProjectSolverState:
    """
    Per-project solver lifecycle + result state — the typed shape of today's
    module-global `routers.simulation._state`.

    Phase A1 scope: this is the canonical SHAPE + factory only. `simulation._state`
    is still a plain dict, built via `ProjectSolverState(...).as_dict()`, so the
    ~120 existing `_state[...]` access sites are unchanged and behaviour is
    byte-identical. Later A-steps thread a per-project INSTANCE through the solver
    (so a background solve writes its own state, not the foreground's) and migrate
    the dict accesses to attribute access. Keeping the field list here now means
    the dispatcher can `ProjectSolverState(...)` per project without re-deriving it.

    Non-network types are annotated `Any` so this module stays free of imports
    from `solver_service` (SolverConfig) / `simulation` (BufferedLogQueue) and
    cannot create an import cycle.
    """

    # Lifecycle (reset each solve)
    status: str = "idle"          # idle | running | completed | failed | aborted
    condition: Any = None
    objective: Any = None
    solve_time: Any = None
    thread: Any = None            # the worker threading.Thread
    stop_event: Any = None        # threading.Event for abort
    log_queue: Any = None         # BufferedLogQueue (SSE source)
    # Actionable failure card from the last finished solve (None on success /
    # abort). Declared here so it's part of the canonical state shape — written
    # via `_state_update(last_failure=…)` in run_simulation's finally, reset
    # each solve, and cleared per-project on switch (not an orphan key).
    last_failure: Any = None
    # Config (project-scoped; injected by the caller — defaults None to avoid a
    # SolverConfig import here. simulation.py passes a real SolverConfig()).
    solver_config: Any = None
    # Result-state (persisted to results_state.pkl)
    last_lost_load: Any = None
    adequacy_report: Any = None   # minimal AdequacyReport dict (target solves)
    # The firm-capacity (reserve-margin) result of the last solve that
    # enforced one — the PERSISTED solve-time stash `/results/reserve_margin`
    # serves. Reset with the rest each solve, so a margin can never outlive
    # the plan that met it.
    last_reserve_margin: Any = None
    lopf_results: Any = None
    ac_pf_results: Any = None
    ac_pf_convergence: Any = None        # dict[snapshot_iso, bool] (legacy)
    ac_pf_convergence_list: Any = None   # list[{snapshot, period?, ok}]
    ac_pf_slack_bus_used: Any = None
    ac_pf_stripped_voll_slacks: Any = None
    ac_pf_converged_count: Any = None
    ac_pf_total_snapshots: Any = None
    # ── Study records (STUDY_KEYS) — NOT persisted, NOT lifecycle ────────────
    # Each holds the record of one long-running adequacy study: status, its
    # result, the worker thread the 409 mesh tests for liveness.
    #
    # ★ Declared here for the same reason `last_failure` and
    # `last_reserve_margin` above are: so they are part of the canonical state
    # shape and NOT orphan keys. They were undeclared until Phase 10, and that
    # is precisely why nothing ever reset them — every reset path iterates
    # declared fields (LIFECYCLE_KEYS, RESULT_STATE_KEYS), so a key in neither
    # list is a key that survives forever. A finished study therefore outlived
    # the network it measured: project A's MC study was served for project B,
    # and a study of a discarded network survived "New". Both reproduced over
    # HTTP in `tests/test_adequacy_study_scoping.py`.
    #
    # They are deliberately in NO persistence group: a study measures a
    # network in memory and must not be restored from disk beside a network it
    # may no longer describe.
    fmea_sweep: Any = None
    frontier: Any = None
    mc: Any = None
    coupling_loop: Any = None
    margin_loop: Any = None

    def as_dict(self) -> dict[str, Any]:
        """A plain dict with the same keys/values — the legacy `_state` shape."""
        return {f.name: getattr(self, f.name) for f in _dc_fields(self)}
