import logging
import os
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar

import pypsa

# Installs the PyPSA load-warning filter for the `ic:` reference frames at
# app start (P2 WP2.2-0), before any loader runs.
from services.commercial import settlement_inputs as _ic_settlement_inputs  # noqa: F401

from services.project_context import (
    RESULT_STATE_KEYS,
    STUDY_KEYS,
    ProjectContext,
    record_is_running,
    running_study_key,
    study_swap_refusal,
)
from services.undo_service import _UndoState

logger = logging.getLogger(__name__)


def _copy_undo_state(src: _UndoState) -> _UndoState:
    """An independent copy of an undo stack (same entries, own deque and locks),
    so the two contexts can never push onto or pop from each other's history."""
    from collections import deque

    with src.lock:
        out = _UndoState()
        out.stack = deque(src.stack, maxlen=src.stack.maxlen)
        out.total_bytes = src.total_bytes
        out.last_push = src.last_push
    return out


class PyPSAService:
    # The single ACTIVE project context — the one foreground project whose
    # network every `get_network()` call resolves to. Phase 0 of the
    # multi-project work encapsulated the previously-loose per-project class
    # attributes (network / loaded_project / transient rows + lock) into
    # ProjectContext; the classmethod API below is byte-for-byte unchanged, so
    # the ~110 call sites are untouched. Phase A/B replaces this single slot
    # with a registry keyed by project_id + an active pointer — see
    # services/project_context.py and the v5 design doc.
    _active: ProjectContext | None = None

    # ── Resident-context registry (B2) ───────────────────────────────────────
    # Multi-project registry of BOUND resident contexts keyed by on-disk
    # project_id (== ProjectContext.loaded_project). B2 adds the data structure +
    # the register/get_context/set_active/drop/list_ids methods; production code
    # still resolves everything through `_active` (single-resident), so the
    # registry is DORMANT until B6 (get_context → path-scoped endpoints) and B8
    # (set_active → instant tab switch + retained load) wire it in. `_active`
    # stays the authoritative pointer for get_network() and uniquely handles the
    # UNBOUND (New Project) case the registry can't key (no project_id yet).
    _contexts: dict[str, ProjectContext] = {}
    # Registry keys registered ON A DECISION STUDY'S BEHALF (gate S4 BC-S4-1):
    # the study's base context and each option fork the queue solves. They
    # neither count toward RESIDENT_CAP nor may be chosen as its victims, and
    # registering one never runs the cap check — so a study can never evict,
    # and so write back, a user's context. Bounded: the runner drops each fork
    # after reading it and the base it registered when the run ends.
    _study_owned: set[str] = set()

    # ── LRU eviction cap (B9) ─────────────────────────────────────────────────
    # Max number of RESIDENT bound contexts held in `_contexts`. Every
    # registration path (register / activate_context(register=True) /
    # resolve_project_context) folds the cap check into `register`, which evicts
    # the least-recently-interacted EVICTABLE context once the registry exceeds
    # the cap. NEVER evicts the active project or one with a queued/running solve
    # (the protected set). Each victim is SAVED to disk before drop so unsaved
    # background residency isn't lost; a save failure skips that victim.
    #
    # RAM ceiling: roughly RESIDENT_CAP × per-project RAM, where per-project RAM
    # is the network + the solver_state result deep-copies (LP/PF `_t` DataFrame
    # snapshots) + up to the undo MAX_BYTES (env-configurable in B3). Raise the
    # cap only if the host has headroom for that many concurrent networks.
    #
    # Overridable via env at import time: PYPSA_GUI_RESIDENT_CAP (default 5).
    RESIDENT_CAP: int = int(os.environ.get("PYPSA_GUI_RESIDENT_CAP", "5"))

    # RLock (not Lock) — reentrant. Endpoints that internally call other
    # endpoints (e.g. POST /scenarios → save_project) hold the lock at the
    # outer level and need save_project's own `with get_lock():` to no-op
    # rather than deadlock. All call sites are single-threaded same-request
    # flows; the lock's purpose is cross-request serialization, which RLock
    # preserves identically to Lock. STAYS GLOBAL through B2 (the registry
    # landed dormant; one-solve/one-mutation-at-a-time is unchanged). The
    # per-context mutation lock is deferred to B4: making get_lock() per-context
    # needs load_project / import_bundle / scenario / restore / resample
    # restructured to build-then-activate, because today they reset_network()
    # (which reassigns _active) INSIDE `with get_lock():` then mutate the new
    # context under that same lock — a per-context lock would change mid-block
    # and break the atomic swap+bind the save-time expect/rebind guard relies on.
    _lock: threading.RLock = threading.RLock()
    # Separate Lock guarding all netCDF file I/O (import_from_netcdf /
    # export_to_netcdf and bare `pypsa.Network(path)` reads). netCDF4 / h5py
    # share global HDF5 state and are NOT thread-safe — two concurrent reads on
    # different files race and surface as "NetCDF: HDF error" or "NetCDF: Not a
    # valid ID". The CompareView fires 4 parallel reads (compare-state +
    # results-summary × 2 projects) which reliably triggers this. Lock ordering:
    # pypsa_lock OUTER, netcdf_io_lock INNER. The transient compare/results
    # endpoints only take this lock; the active-network load/save paths take
    # pypsa_lock first, then this one. STAYS A SINGLE SHARED INSTANCE even once
    # multiple project contexts are resident — it guards process-global HDF5
    # library state, not per-project data.
    _netcdf_io_lock: threading.Lock = threading.Lock()

    # Thread-local "solving context" (B4). When a solver worker thread binds it
    # (via `solving_context()`), the transient-registry mark/unmark/clear target
    # THAT ctx instead of the active one — so a background solve (B4.3) marks its
    # OWN `__voll_*`/`@year` rows, not the foreground's, and `clear_transient()`
    # in restore wipes the solving ctx's marks, not the foreground's. Unset on
    # request threads (GET reads via get_transient_rows) → active, so single-
    # resident behaviour is byte-identical. threading.local: each worker thread
    # sees only its own binding.
    _solving_ctx = threading.local()

    # Global registry/swap lock (B4). Guards `_active`-pointer reassignment +
    # `_contexts` membership so build-then-activate (activate_context) and the
    # B8 instant switch publish atomically. SWAP-INDEPENDENT (unlike a per-ctx
    # lock, it doesn't change when `_active` moves). INVARIANT: never acquired
    # while holding a per-ctx mutation_lock (only activate_context/set_active
    # take it, and they take it FIRST) — prevents the two-lock deadlock.
    _registry_lock: threading.RLock = threading.RLock()

    # ── Hydrate-or-adopt lock (one ProjectContext per project, always) ────────
    # Per-registry-key lock, taken ONLY on a registry miss. FOUR cold paths
    # build-and-register a context — `activate_project`, `resolve_project_context`,
    # `active_project.resolve_for_session` (both branches, twice per authenticated
    # request on every route) and the solve-queue dispatcher — and every one of
    # them ran an UNSYNCHRONISED get_context -> build -> hydrate -> register.
    # `get_context` is an unlocked dict read and `register` takes `_registry_lock`
    # only for the insert without re-checking for a concurrent winner, so two
    # cold paths racing on the same non-resident project each built a context and
    # the second overwrote the first in `_contexts` while the first was already
    # bound into a live request.
    #
    # A FIFTH path publishes without building: `routers.projects.load_project`
    # resets the CALLER'S OWN context, imports the project into it, and
    # republishes it under the project's key — a deliberate REPLACEMENT of the
    # resident entry (that is what re-opening a project from disk means), not a
    # fork. It takes this lock for the insert like everyone else, and it
    # REFUSES with 409 while the solve queue is running a job for the key,
    # because replacing the entry there would leave the dispatcher solving —
    # and saving — a context nothing can resolve any more (defect D-1).
    #
    # LOCK ORDER — hydrate -> `_registry_lock` -> `solve_queue._lock`. A hydrate
    # lock is NEVER acquired while `_registry_lock` is held. `_hydrate_guard` is a
    # leaf lock held only for the dict lookup that hands out the per-key lock, and
    # nothing is called while it is held. The pre-existing rule that
    # `_registry_lock` never nests a per-ctx `mutation_lock` (see
    # `_evict_if_over_cap`) is unchanged.
    #
    # The three named locks are not the whole order, and the tail is where a
    # future contributor is most likely to invert it. Nested UNDER a held
    # hydrate lock, on the slow path, are also:
    #   * the ctx being built: its `mutation_lock` and then the process-global
    #     `_netcdf_io_lock` — `_hydrate_context_from_disk` takes both to
    #     `import_from_netcdf`;
    #   * an ARBITRARY VICTIM context's `mutation_lock` and the same
    #     `_netcdf_io_lock`, because `register` -> `_evict_if_over_cap` ->
    #     `_save_evicted_ctx` -> `_save_context` saves whatever the cap
    #     evicts, and the victim is chosen by LRU — it is not the caller's
    #     context and not knowable from the call site.
    # So the full order is: hydrate -> `_registry_lock` -> `solve_queue._lock`,
    # and hydrate -> (any ctx) `mutation_lock` -> `_netcdf_io_lock`. Nothing
    # today acquires a hydrate lock while holding a `mutation_lock` or the I/O
    # lock, and nothing may start: that is the cycle this note exists to
    # prevent. (`_evict_if_over_cap` deliberately runs its saves OUTSIDE
    # `_registry_lock` for the same reason, one level down.)
    #
    # Refcounted: `hydrate_or_adopt` increments the waiter count on entry and
    # decrements (deleting the entry once it hits zero) in a `finally`, so the
    # dict holds an entry only while at least one thread is actually inside
    # the slow path for that key — bounded by CONCURRENT misses in flight, not
    # by how many distinct keys were EVER missed. Without this the dict would
    # be a genuine leak on a long-lived server process: `active_project.
    # resolve_for_session` (Task 2) routes the per-session `scratch:<id>` key
    # through this same helper, and every session ever created would else
    # pin a permanent entry that nothing removes.
    _hydrate_locks: dict[str, tuple[threading.Lock, int]] = {}
    _hydrate_guard: threading.Lock = threading.Lock()

    # ── Request-scoped active context (Step 0b) ──────────────────────────────
    # The "active project" is per SESSION, not per process. `_request_ctx` holds
    # the context resolved for the request currently on this thread/task; when
    # it is set, EVERY `get_network()` call site (133 of them) resolves to that
    # caller's project without any of them changing.
    #
    # Falls back to `_active` when unset, which is the correct answer for the
    # callers that genuinely have no session: the solve-queue dispatcher thread,
    # the eviction save, and direct in-process use. Those either carry their own
    # ctx explicitly or operate on the process foreground by design.
    #
    # ContextVar rather than threading.local because it survives BOTH threads
    # and asyncio tasks — the same reason `_ThreadScopedQueueHandler` will have
    # to switch to one if Step 2's worker runs jobs on a single event loop.
    _request_ctx: ContextVar[ProjectContext | None] = ContextVar(
        "pypsa_request_ctx", default=None
    )

    @classmethod
    def bind_request_context(cls, ctx: ProjectContext | None):
        """Bind (or clear) the active context for the current request."""
        return cls._request_ctx.set(ctx)

    @classmethod
    def reset_request_context(cls, token) -> None:
        cls._request_ctx.reset(token)

    # Registry key the request's context occupies. Held alongside the ctx so a
    # mid-request swap (New Project, load, import) replaces the SAME registry
    # slot instead of leaving the old object resident under a stale key.
    _request_slot: ContextVar[str | None] = ContextVar(
        "pypsa_request_slot", default=None
    )

    # The session's SCRATCH slot key, held for the whole request so an UNBOUND
    # context created mid-request (New Project, or the reset that starts every
    # load) lands in the session's own draft slot instead of overwriting the
    # registry entry of the project the user was just on.
    _request_scratch: ContextVar[str | None] = ContextVar(
        "pypsa_request_scratch", default=None
    )

    # P33b: a one-element list the request middleware binds beside the ctx.
    # A handler's `_request_ctx.set(...)` (a mid-request swap: undo, "New",
    # import) is made in a COPY of the context (threadpool / call_next task),
    # so the middleware cannot see it after `call_next` — but it can see a
    # mutation of a list it shares. `_note_request_ctx` writes the swapped-in
    # ctx into it, so the middleware's edit-counter bump lands on the ctx the
    # request ENDED on, not on the project the user just left.
    _request_ctx_box: ContextVar[list | None] = ContextVar(
        "pypsa_request_ctx_box", default=None
    )

    @classmethod
    def bind_request_box(cls, box: list | None):
        return cls._request_ctx_box.set(box)

    @classmethod
    def _note_request_ctx(cls, ctx: ProjectContext) -> None:
        box = cls._request_ctx_box.get()
        if box is not None:
            box[0] = ctx

    @classmethod
    def bind_request_slot(cls, key: str | None):
        return cls._request_slot.set(key)

    @classmethod
    def bind_request_scratch(cls, key: str | None):
        return cls._request_scratch.set(key)

    # A chat turn's binding CELL — a one-element list shared by reference.
    #
    # `_request_ctx` is a ContextVar, and a `set()` only reaches code running
    # in the same Context. The chat SSE generator never runs in one: Starlette's
    # `iterate_in_threadpool` drives every `next()` in a FRESH copy of the
    # request task's context, so a project switch a tool makes — published by
    # `_publish_active`, adopted by the dispatcher — lands on a per-item copy
    # and is gone at the next `yield`. The rest of the turn then edits the
    # PREVIOUS project while the model believes it moved. (The first fix for
    # this, `adopt_active_from`, was tested by driving the generator in one
    # context, which is exactly the case where it works.)
    #
    # A cell fixes it without any reader caring: the ContextVar holds the same
    # LIST object in every copy that descends from the task that bound it, so a
    # write into the list is seen by all of them — including the `asyncio.run`
    # task an async import handler runs in, two copies further down.
    # `routers/chat.py` binds it from the event-loop task (the only place a
    # binding survives the per-item copies, as its `set_acting_user` note
    # explains); nothing else binds one, so outside a chat turn every read
    # below is exactly `_request_ctx.get()`.
    _turn_cell: ContextVar[list | None] = ContextVar("pypsa_turn_cell", default=None)

    @classmethod
    def bind_turn_cell(cls):
        """Share this request's binding with every context copied from here.

        No-op (returns None) outside a request: with no request context the
        process foreground `_active` is used, which is shared already.
        """
        current = cls._request_ctx.get()
        if current is None:
            return None
        return cls._turn_cell.set([current])

    @classmethod
    def _scoped(cls) -> ProjectContext | None:
        """The request-scoped context: the turn cell's if one is bound."""
        cell = cls._turn_cell.get()
        if cell is not None and cell[0] is not None:
            return cell[0]
        return cls._request_ctx.get()

    @classmethod
    def _set_scoped(cls, ctx: ProjectContext) -> None:
        """Publish `ctx` as the request-scoped context, cell included."""
        cls._request_ctx.set(ctx)
        cell = cls._turn_cell.get()
        if cell is not None:
            cell[0] = ctx

    @classmethod
    def get_request_context(cls) -> ProjectContext | None:
        return cls._scoped()

    @classmethod
    def adopt_process_foreground(cls) -> ProjectContext | None:
        """
        Hand the process foreground context to the first session that asks, ONCE.

        `_active` is a BOOTSTRAP slot, not a shared workspace. Before Step 0b it
        was the one place an unbound network could live; now each session owns
        its own scratch context, and `_active` exists only for callers with no
        session at all (the solve dispatcher, eviction, direct in-process use).

        Adopt-once resolves the handover without reintroducing sharing: the
        first session to need a scratch context takes `_active` over and CLEARS
        it under the registry lock, so a second session cannot adopt the same
        object. Returns None when there is nothing to adopt.

        In production this is nearly always a no-op on an empty network — the
        lifespan creates one at startup and no request-path code writes `_active`
        any more. It matters for two real cases: a process that was serving
        before this change and still holds a bound foreground, and any
        in-process caller (tests, scripts) that installs a network directly and
        then drives the app over HTTP.
        """
        with cls._registry_lock:
            ctx = cls._active
            if ctx is None:
                return None
            cls._active = None
            return ctx

    @classmethod
    def rekey_context(cls, ctx: ProjectContext) -> None:
        """
        Move `ctx` to the registry slot its CURRENT identity implies.

        Called after a context's binding changes — the first save of a draft, a
        Save-As rebind, a rename. A session's unbound draft lives under
        `scratch:<session>`; the moment it becomes a real project it has to
        become findable by `org:uuid`, or everything that looks projects up by
        key (the solve dispatcher, path-scoped reads, activate's resident fast
        path) misses it and hydrates a SECOND copy from disk — leaving the user
        editing one context while a background solve writes another.
        """
        key = ctx.registry_key
        if key is None:
            return
        with cls._registry_lock:
            for existing_key, existing_ctx in list(cls._contexts.items()):
                if existing_ctx is ctx and existing_key != key:
                    cls._contexts.pop(existing_key, None)
            cls._contexts[key] = ctx
        if cls._scoped() is ctx:
            cls._request_slot.set(key)

    @classmethod
    def adopt_active_from(cls, snapshot) -> bool:
        """Carry a project switch made inside `snapshot` into THIS context.

        `snapshot` is a `contextvars.Context` that some other thread ran work
        in. A `ContextVar.set()` performed inside `Context.run()` mutates that
        Context and is invisible to the caller once `run()` returns — which is
        exactly what happens to a chat tool: `chat_service` dispatches every
        tool as `copy_context().run(...)` on an executor (it has to, because a
        pool worker inherits no contextvars), so `_publish_active`'s
        `_request_ctx.set(ctx)` landed on the copy and died with it. In server
        mode the rest of the turn therefore kept operating on the PREVIOUS
        project while the model believed it had switched.

        Only the active-project var is carried, deliberately: the acting user
        and session are bound per request and a tool has no business changing
        them, so a blanket "copy every var back" would turn this into a
        privilege-transfer primitive.

        Returns True when a switch was actually adopted. No-op outside a
        request, where `_publish_active` writes the process foreground
        (`cls._active`) instead and nothing was ever lost.

        NOT sufficient on its own, and that is why `_turn_cell` exists: this
        writes `_request_ctx` in the CALLER's context, and the chat generator's
        caller is itself a per-item copy that is discarded at the next yield.
        It also cannot see a publish made one copy further down, inside the
        `asyncio.run` task an async handler (every import) runs in. With a cell
        bound the publish has already reached the turn and this is a no-op.
        """
        if cls._scoped() is None:
            return False
        try:
            switched = snapshot[cls._request_ctx]
        except (KeyError, TypeError):
            return False
        # Compared against the caller's RAW var — what the copy started as —
        # not against `_scoped()`. Unchanged means the tool published nothing
        # in this copy, which is not the same as "no switch": an async handler
        # publishes one copy further down, straight into the cell, and the
        # snapshot still holds the OLD context. Comparing with the cell would
        # read that stale value as a switch and adopt it, quietly undoing the
        # import that just happened.
        if switched is None or switched is cls._request_ctx.get():
            return False
        if switched is cls._scoped():
            return False
        cls._set_scoped(switched)
        return True

    @classmethod
    def _publish_active(cls, ctx: ProjectContext) -> None:
        """
        Make `ctx` the active one for whoever is asking.

        Inside a request (Step 0b) that means the caller's session slot, so one
        user pressing "New" cannot reset another user's network. Outside a
        request — the solve dispatcher, eviction, direct in-process callers —
        it means the process foreground, exactly as before.
        """
        if cls._scoped() is not None:
            cls._set_scoped(ctx)
            cls._note_request_ctx(ctx)
            # Route by the NEW context's own identity, not by the slot the
            # request arrived on. A `reset_network()` mid-request produces an
            # UNBOUND context; writing it into the previous project's registry
            # slot would replace that project's resident network with an empty
            # one, so "New Project" silently wiped the copy of the project the
            # user had open. An unbound context belongs in the session's scratch
            # slot; a bound one belongs under its own `org:uuid` key.
            slot = ctx.registry_key or cls._request_scratch.get()
            cls._request_slot.set(slot)
            if slot is not None:
                with cls._registry_lock:
                    cls._contexts[slot] = ctx
            return
        cls._active = ctx

    # ── Active-context lifecycle ──────────────────────────────────────────────
    @classmethod
    def _ensure_active(cls) -> ProjectContext:
        """
        Return the active context, self-healing a fresh UNBOUND one if absent.

        Prefers the REQUEST-scoped context (Step 0b) so two users on one process
        each read their own project. The self-heal below matters during uvicorn
        --reload between the time a module is reimported and the lifespan
        re-runs initialize() — without it the GUI shows transient 500s on every
        save. Two threads both seeing None and both creating is harmless (both
        fresh + empty; last assignment wins) — same race profile as the previous
        unlocked `_network` self-heal.
        """
        scoped = cls._scoped()
        if scoped is not None:
            return scoped
        if cls._active is None:
            n = pypsa.Network()
            n.name = "Untitled Project"
            cls._active = ProjectContext(network=n)
        return cls._active

    @classmethod
    def initialize(cls) -> None:
        with cls._lock:
            cls._ensure_active()

    @classmethod
    def get_network(cls) -> pypsa.Network:
        return cls._ensure_active().network

    @classmethod
    def get_active_context(cls) -> ProjectContext:
        """
        The active ProjectContext (the one get_network() resolves to).

        Used by undo_service (per-project undo, B3) and B6 path-scoping.
        """
        return cls._ensure_active()

    @classmethod
    def refuse_if_study_running(cls, action: str = "replace the network",
                                *, ctx=None) -> None:
        """Raise 409 if a study is live — WITHOUT swapping anything.

        ★ Phase 11 put this check inside `reset_network` and asserted the 409
        was "raised BEFORE any mutation". That is true INSIDE `reset_network`
        and false at four of its seven call sites, which do destructive work
        first: `undo_last` pops the undo entry (destroying the step it then
        refuses to apply), `restore_snapshot` overwrites the project's files
        on disk, and `import_bundle` / `create_from_template` commit the
        project row — so the retry the refusal advises fails forever with
        "already exists".

        A route that mutates anything before swapping must call THIS first.
        The check is pure: it reads the state and raises, and touches nothing.
        """
        prev = ctx if ctx is not None else (cls._scoped() or cls._active)
        if prev is None:
            return
        detail = study_swap_refusal(prev.solver_state, action)
        if detail:
            from fastapi import HTTPException

            raise HTTPException(409, detail)

    @classmethod
    def reset_network(cls, *, allow_during_study: bool = False,
                      action: str = "replace the network",
                      carry: str = "fresh") -> None:
        # Swap in a fresh, UNBOUND context — no on-disk project owns it yet.
        # reset_network runs at the START of every load/import/restore (which
        # then re-bind via set_loaded_project at the end), and on explicit
        # "New"/reset. The new context starts with `loaded_project=None` and an
        # empty transient registry, so a load that fails mid-import leaves
        # identity unbound (rather than dangling on the previous project) and a
        # subsequent autosave can't misdirect.
        # ★ `carry` — what of the outgoing context's PER-PROJECT state the new
        # one inherits (P33b gate B-1). It used to hand over the outgoing
        # `_UndoState` OBJECT on every swap; with both contexts resident
        # (P33b step 4b) and an undo that lands (step 0), project B's edits
        # then sat on project A's stack, and Undo in A replaced A's network
        # with B's — which a save wrote into A's files.
        #   "same"  — an in-place replace of the SAME project (undo): share
        #             the undo stack, keep the edit counter and result state.
        #   "copy"  — the same workspace gets a new network (io import into
        #             the open draft): a COPY of the undo stack (the capture the
        #             middleware pushed before the import stays undoable, and
        #             the outgoing context's own stack is never touched) and
        #             the counter.
        #   "fresh" — (default) a DIFFERENT project or a blank draft (load,
        #             template, bundle import, Saved-snapshot restore, New):
        #             its own empty undo stack, counter 0 (the routes restore a
        #             saved project's counter from metadata.json) and no result
        #             state of the project being left.
        if carry not in ("same", "copy", "fresh"):
            raise ValueError(f"reset_network carry={carry!r}")
        prev = cls._scoped() or cls._active
        # ★ REFUSE BY DEFAULT while a study is live (Phase 11).
        #
        # A study's worker closes over the `pypsa.Network` object captured
        # before it started, so replacing the network does not STOP it — it
        # DETACHES it. The study keeps solving the old object and keeps
        # publishing into the solver_state carried forward below, so the new
        # project's Adequacy tab fills in LIVE with the old project's study,
        # and a `restore="final"` loop writes its certified cap or margin into
        # the NEW project's solver config and re-solves there.
        #
        # The guard is here, at the one choke point every network-replacing
        # route already goes through, and not repeated at those seven call
        # sites — a guard repeated seven times is a guard the eighth route
        # forgets. `allow_during_study=True` is the explicit opt-out for a
        # caller that genuinely must proceed.
        #
        # Raised BEFORE any mutation, so a refusal leaves the swap
        # `action` DEFAULTS to a correct-if-generic phrase rather than being
        # required: the guard then works with no call-site changes at all, and
        # a route that forgets to describe itself still gets a true sentence
        # instead of no protection. Sharpening the wording per route is a copy
        # improvement, never a correctness dependency.
        #
        # not-started rather than half-done. HTTPException from a service is
        # the established pattern here (project_registry, project_acl,
        # upload_service, storage_paths, chat_service, …), so FastAPI returns
        # the 409 with no handler to register.
        if not allow_during_study:
            cls.refuse_if_study_running(action, ctx=prev)
        n = pypsa.Network()
        n.name = "Untitled Project"
        # Carry the solver-state object (+ its lock) AND the undo stack forward so
        # the simulation `_state` proxy and undo_service are byte-identical to the
        # pre-split module globals, which survived every network swap. load_project
        # re-hydrates state explicitly right after (solver_config from disk-or-
        # default, results from the pkl) and calls undo_service.clear(); "New"/reset
        # intentionally keeps the prior lifecycle + undo, as before. B2's registry
        # replaces this carry-forward with per-project selection.
        if prev is not None:
            # ★ A finished STUDY must not outlive the network it measured.
            #
            # The carried-forward solver_state below is the SAME dict object,
            # and load_project re-hydrates only `solver_config` and
            # RESULT_STATE_KEYS — the five study keys are in neither list. So
            # without this, project A's completed MC study is served for
            # project B, and a study of a discarded network survives "New".
            # Both were reproduced over HTTP before this line existed
            # (`tests/test_adequacy_study_scoping.py`), and it is the unfixed
            # half of the QA-round-7 defect that put a 3.5x wrong reliability
            # standard on the wire.
            #
            # This is the ONE choke point — every path that replaces the
            # foreground network comes through here — so the fix cannot be
            # bypassed by a caller that forgets.
            #
            # A RUNNING record is left ALONE, deliberately: clearing it would
            # make `study_running()` False while the worker thread is still
            # alive and still mutating a network, breaking the 409 mesh and
            # admitting a SECOND study — the exact corruption the mesh exists
            # to prevent. A study running ACROSS a network swap is a real
            # defect and a separate fix (a 409 at the eight routes that
            # replace the network); leaking its record keeps the mutex honest
            # and the panel reads "running" rather than showing a fabricated
            # result. A test pins this choice.
            #
            # ★ Cleared on the COPY, not on `prev` (P33b 10a). `prev` is the
            # outgoing project's RESIDENT, registered context: clearing its own
            # dict erased that project's finished study, so creating B from a
            # template, loading B or pressing "New" and then re-activating A (a
            # pointer swap) found no study while A's stored report was still
            # there. The fresh context must not inherit A's study — that is the
            # whole purpose of the clear, and the copy serves it. `prev`'s
            # record still describes `prev`'s own network object, which the swap
            # does not touch. (`set_network` keeps its in-place clear: there the
            # measured network IS replaced.)
            state = dict(prev.solver_state)
            for key in STUDY_KEYS:
                if not record_is_running(state.get(key)):
                    state[key] = None
            if carry == "fresh":
                # The project being left keeps its results; the new one starts
                # without them (load / import / restore re-read their own from
                # results_state.pkl; a template project has none).
                for key in RESULT_STATE_KEYS:
                    state[key] = None
            if carry == "same":
                undo_state = prev.undo
            elif carry == "copy":
                undo_state = _copy_undo_state(prev.undo)
            else:
                undo_state = _UndoState()
            cls._publish_active(ProjectContext(
                network=n,
                # ★ A COPY, not the same dict (Phase 11 review, BLOCKER 5).
                #
                # Carrying the object itself left the outgoing project's
                # context and the fresh one pointing at ONE dict, so the clear
                # above — which runs once, at swap time — cleared what was
                # there THEN, and any study written AFTER the swap landed in a
                # dict the old project still read. Reproduced over HTTP: load
                # A, press "New", run a study, re-activate A, and A is served
                # the scratch network's study. That is Phase 10's headline
                # symptom with the order reversed.
                #
                # Copying is safe precisely because nothing depends on the
                # dict's IDENTITY surviving a swap: `simulation._state` is
                # `_ActiveStateProxy`, which resolves through
                # `PyPSAService.get_solver_state()` on every access. The
                # carry-forward's actual purpose — the user's solver_config
                # and lifecycle surviving "New" — is preserved by the copy's
                # VALUES. The lock stays shared: it guards both dicts, which
                # is coarser than necessary and never wrong.
                solver_state=state,
                solver_state_lock=prev.solver_state_lock,
                undo=undo_state,
                # Carry the mutation_lock forward too (B4 Inc 2): the foreground
                # `get_lock()` then returns ONE persistent RLock across every
                # reset/set swap — byte-identical to the old global `_lock`, and
                # load_project's `with get_lock(): reset_network(); import; bind`
                # holds the SAME lock across the swap (no mid-block change). A
                # background ctx built via build_context() gets a FRESH lock, so
                # it runs concurrently with the foreground.
                mutation_lock=prev.mutation_lock,
                # Carry chat_state forward (chatbot integration v6 Phase 0).
                # Same-project network swap (reset is a no-op on identity for
                # New/Reset; load_project rebinds at the end) must preserve the
                # active chat session — otherwise an autosave-triggered reset
                # mid-conversation would silently zero out user history.
                # build_context EXCLUDED from this carry (background-solve
                # contexts get clean chat).
                chat_state=prev.chat_state,
                # P33b: the edit counter belongs to the project, like `undo`;
                # a load / restore re-hydrates it from metadata.json.
                network_revision=(prev.network_revision
                                  if carry in ("same", "copy") else 0),
                # Carry the user-uploaded time-series store forward, as a COPY
                # for the same reason `solver_state` is copied above: the old ctx
                # must not observe writes made after the swap.
                #
                # Carried at all because the store used to be a module global and
                # so survived every swap. Nothing depends on the VALUES arriving
                # here: every caller that resets in order to LOAD something sets
                # the store explicitly a few lines later (load_project,
                # import_bundle, create-from-template, restore-snapshot,
                # `POST /network/reset`, `POST /io/new`, undo), so the copy is
                # replaced before anyone reads it. `set_network` (clustering) is
                # the case that genuinely needs the values — see there.
                #
                # Per `carry` (P33b master merge): "same" / "copy" keep the
                # workspace, so they keep a copy of its profiles; "fresh" is a
                # DIFFERENT project or a blank draft, so it starts with its own
                # empty store, like its undo stack, counter and result state.
                # load_project's late queue-solve refusal resets "fresh" on
                # purpose (an empty context neither an eviction write-back nor
                # a first save can misuse), so it gets no profiles either.
                user_ts=(dict(prev.user_ts)
                         if carry in ("same", "copy") else {}),
                # Same rule as `user_ts`: the profile-provenance channel
                # (profile_gen Task 5) belongs to the workspace.
                user_ts_meta=(dict(prev.user_ts_meta)
                              if carry in ("same", "copy") else {}),
            ))
        else:
            cls._publish_active(ProjectContext(network=n))
        cls._clear_swap_caches()

    @classmethod
    def set_network(cls, n: pypsa.Network, *,
                    allow_during_study: bool = False) -> None:
        """
        Replace the in-memory network with a new one (e.g. the output of a
        spatial clustering operation). Preserves the existing display name AND
        the on-disk identity — the clustered network still belongs to the same
        project — and starts a fresh transient registry (the old transient rows
        pointed at the old network).
        """
        prev = cls._ensure_active()
        # ★ The EIGHTH network-replacing path (Phase 11 review, BLOCKER 4).
        # Phase 11's spec asserted `reset_network` was the only one; clustering
        # replaces the network through HERE, and had neither the refusal nor
        # Phase 10's study-record clear. So clustering during a study returned
        # 200 and DETACHED it — verbatim the defect the guard exists to
        # prevent — and a finished study of the pre-clustering network
        # survived onto the clustered one.
        if not allow_during_study:
            cls.refuse_if_study_running("re-cluster the network", ctx=prev)
        for key in STUDY_KEYS:
            if not record_is_running(prev.solver_state.get(key)):
                prev.solver_state[key] = None
        n.name = prev.network.name
        # Phase 12h: the replacement network was built by a clustering pass
        # that aggregates rows and never carries a custom bool column's dtype
        # — so put `p_max_pu_includes_outages` back to `bool` here, at the one
        # network-replacing path that does not go through the netCDF import
        # helper. Without it the flag is silently dropped by the swap.
        try:
            from services.adequacy.eh_columns import normalise_eh_columns
            from services.adequacy.occurrence import normalise_flag_column
            normalise_flag_column(n)
            normalise_eh_columns(n)
        except Exception:                                     # noqa: BLE001
            pass
        # Same project (clustering swaps the network in place): carry identity,
        # solver_state AND undo forward so the user's solver_config / status /
        # undo history survive the swap, matching the pre-split module globals.
        cls._publish_active(ProjectContext(
            network=n,
            loaded_project=prev.loaded_project,
            org_id=prev.org_id,
            project_uuid=prev.project_uuid,
            storage_dir=prev.storage_dir,
            solver_state=prev.solver_state,
            solver_state_lock=prev.solver_state_lock,
            undo=prev.undo,
            mutation_lock=prev.mutation_lock,  # carry forward (B4 Inc 2) — same project
            # Chatbot v6 Phase 0 — same project, same chat session. Clustering
            # is an explicit user op the chat may have just triggered; killing
            # its session here would lose the very conversation that drove it.
            chat_state=prev.chat_state,
            # P33b B3: replacing the network object is an edit. Its one
            # production caller (POST /api/network/cluster) is also bumped by
            # the HTTP or chat seam, so one clustering is +2 — harmless, the
            # sentence ("edited since") stays true.
            network_revision=prev.network_revision + 1,
            # Same project, same uploaded profiles: clustering swaps the network
            # object and nothing repopulates the store afterwards, so NOT carrying
            # this would drop every uploaded profile the moment a network is
            # re-clustered. A copy, not the object — see `reset_network`.
            user_ts=dict(prev.user_ts),
            # The provenance channel follows its series (profile_gen Task 5).
            user_ts_meta=dict(prev.user_ts_meta),
        ))
        cls._clear_swap_caches()
