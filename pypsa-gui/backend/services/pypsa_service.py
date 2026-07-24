import logging
import os
import threading
import time
from contextlib import contextmanager

import pypsa

from services.project_context import ProjectContext

logger = logging.getLogger(__name__)


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

    # ── Active-context lifecycle ──────────────────────────────────────────────
    @classmethod
    def _ensure_active(cls) -> ProjectContext:
        """
        Return the active context, self-healing a fresh UNBOUND one if absent.
        The self-heal matters during uvicorn --reload between the time a module
        is reimported and the lifespan re-runs initialize() — without it the GUI
        shows transient 500s on every save. Two threads both seeing None and
        both creating is harmless (both fresh + empty; last assignment wins) —
        same race profile as the previous unlocked `_network` self-heal.
        """
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
    def reset_network(cls) -> None:
        # Swap in a fresh, UNBOUND context — no on-disk project owns it yet.
        # reset_network runs at the START of every load/import/restore (which
        # then re-bind via set_loaded_project at the end), and on explicit
        # "New"/reset. The new context starts with `loaded_project=None` and an
        # empty transient registry, so a load that fails mid-import leaves
        # identity unbound (rather than dangling on the previous project) and a
        # subsequent autosave can't misdirect.
        prev = cls._active
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
            cls._active = ProjectContext(
                network=n,
                solver_state=prev.solver_state,
                solver_state_lock=prev.solver_state_lock,
                undo=prev.undo,
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
            )
        else:
            cls._active = ProjectContext(network=n)
        cls._clear_swap_caches()

    @classmethod
    def set_network(cls, n: pypsa.Network) -> None:
        """
        Replace the in-memory network with a new one (e.g. the output of a
        spatial clustering operation). Preserves the existing display name AND
        the on-disk identity — the clustered network still belongs to the same
        project — and starts a fresh transient registry (the old transient rows
        pointed at the old network).
        """
        prev = cls._ensure_active()
        n.name = prev.network.name
        # Same project (clustering swaps the network in place): carry identity,
        # solver_state AND undo forward so the user's solver_config / status /
        # undo history survive the swap, matching the pre-split module globals.
        cls._active = ProjectContext(
            network=n,
            loaded_project=prev.loaded_project,
            solver_state=prev.solver_state,
            solver_state_lock=prev.solver_state_lock,
            undo=prev.undo,
            mutation_lock=prev.mutation_lock,  # carry forward (B4 Inc 2) — same project
            # Chatbot v6 Phase 0 — same project, same chat session. Clustering
            # is an explicit user op the chat may have just triggered; killing
            # its session here would lose the very conversation that drove it.
            chat_state=prev.chat_state,
        )
        cls._clear_swap_caches()

    @classmethod
    def build_context(
        cls, *, carry_solver_state: bool = False, carry_undo: bool = False
    ) -> ProjectContext:
        """
        Create a fresh, UNBOUND ProjectContext OFF TO THE SIDE (not activated).

        Build-then-activate (B4.3): the dispatcher populates this ctx fully
        (import network, bind loaded_project, hydrate state) BEFORE publishing it
        via activate_context — so a background solve runs on its own context with
        its OWN fresh mutation_lock (independent of the foreground's), and no
        concurrent reader observes a half-built active ctx. `carry_*` mirror the
        reset/set forward-carry for callers that don't re-hydrate; the new ctx
        ALWAYS gets its own fresh mutation_lock (never carried) so background and
        foreground locks are distinct — that distinctness IS the concurrency.
        """
        n = pypsa.Network()
        n.name = "Untitled Project"
        prev = cls._active
        kwargs: dict = {}
        if carry_solver_state and prev is not None:
            kwargs["solver_state"] = prev.solver_state
            kwargs["solver_state_lock"] = prev.solver_state_lock
        if carry_undo and prev is not None:
            kwargs["undo"] = prev.undo
        # chat_state is INTENTIONALLY NOT carried (chatbot integration v6 M2).
        # A background ctx is for B4.3 dispatcher solves on a DIFFERENT project
        # than the foreground; sharing the foreground's chat session would
        # expose its in-flight conversation to background-solve agents and
        # cross-contaminate the chat.jsonl persist target. The fresh ctx gets
        # a default-factory ChatState (empty session, fresh lock).
        return ProjectContext(network=n, **kwargs)

    @classmethod
    def activate_context(cls, ctx: ProjectContext, *, register: bool = False) -> list[str]:
        """
        Atomically publish `ctx` as the active context (build-then-activate /
        B8 instant switch). Acquires `_registry_lock` so the pointer swap (and
        optional registry insert by the ctx's bound id) is atomic w.r.t. other
        registry ops. Stamps the ctx's LRU recency (it's now MRU) and runs the
        B9 cap check when registering. Clears the rep-period swap cache (keyed
        without network identity, so it must not survive an active-ctx change).

        Returns the list of project_ids evicted by the cap check (empty unless a
        registration pushed the registry over RESIDENT_CAP) so the activate
        endpoint can tell the frontend to drop those projects' caches.
        """
        with cls._registry_lock:
            ctx.last_interacted_at = time.monotonic()
            cls._active = ctx
            if register and ctx.loaded_project is not None:
                cls._contexts[ctx.loaded_project] = ctx
        # Run the cap check AFTER releasing `_registry_lock` so eviction's heavy
        # per-victim save (mutation_lock + io-lock) never nests under it. The
        # just-activated ctx is protected (it's the new active id) so it can
        # never be its own victim. Skip entirely when not registering.
        cls._clear_swap_caches()
        if register and ctx.loaded_project is not None:
            return cls._evict_if_over_cap(protected_ids={ctx.loaded_project})
        return []

    @classmethod
    def _clear_swap_caches(cls) -> None:
        """
        Drop any module-level cache that memoizes results DERIVED from the
        in-memory network, whenever the network is swapped (reset / set).
        Without this, a different project — or the same project after an edit
        that changed its profiles — can read the previous network's cached
        result.

        Currently this is the limited-foresight representative-period cache.
        Its key is now `(id(n), period, cfg_fingerprint)` (network-identity
        namespaced + lock-guarded — see time_aggregation_service), so concurrent
        solves no longer collide; this clear still runs on every swap to bound
        stale `id(n)` entries (and so the same project after a profile edit
        re-clusters). Lazy import keeps this free of import-order/cycle concerns;
        the clear is cheap (frees a dict).
        """
        try:
            from services import time_aggregation_service
            time_aggregation_service.clear_cache()
        except Exception:
            pass

    @classmethod
    def get_lock(cls) -> threading.RLock:
        # B4 Inc 2: the ACTIVE context's per-context mutation_lock (not the old
        # global `_lock`). Because reset/set carry mutation_lock forward, the
        # foreground sees ONE persistent RLock across swaps — byte-identical to
        # the old global lock for all single-active flows. A background solve
        # (B4.3) holds a DIFFERENT ctx's fresh lock, so it doesn't block the
        # foreground. `cls._lock` remains only as the one-time startup guard in
        # initialize() (before any active ctx exists).
        return cls._ensure_active().mutation_lock

    @classmethod
    def get_netcdf_io_lock(cls) -> threading.Lock:
        return cls._netcdf_io_lock

    # ── Active-context solver state ──────────────────────────────────────────
    # The solver lifecycle + result state of the ACTIVE project. The simulation
    # `_state` proxy + the _state_update/_state_snapshot/_solver_in_flight
    # helpers resolve through these so the (lazy) external importers and
    # `sim._state` attribute access in solve_queue always see the active
    # project's state. Per-context (lives on ProjectContext), not a module
    # global — so B4's dispatcher can write a BACKGROUND project's state via its
    # own context handle without touching the foreground.
    @classmethod
    def get_solver_state(cls) -> dict:
        """The active project's solver lifecycle + result-state dict."""
        return cls._ensure_active().solver_state

    @classmethod
    def get_solver_state_lock(cls) -> threading.RLock:
        """RLock guarding the active project's solver_state (old `_state_lock`)."""
        return cls._ensure_active().solver_state_lock

    # ── Resident-context registry methods (B2; dormant until B6/B8) ───────────
    # These manipulate the multi-project registry. Production paths still go
    # through `_active`; B6/B8/B9 progressively wire these in. Locks intentionally
    # stay GLOBAL here — per-context mutation locks need load_project restructured
    # to build-then-activate (the swap happens under get_lock() today), which
    # lands with B4. The netCDF I/O lock stays global forever (process-global HDF5).
    @classmethod
    def register(cls, project_id: str, ctx: ProjectContext) -> list[str]:
        """
        Add/replace a bound resident context, stamp its LRU recency (registering
        counts as an interaction), and run the B9 cap check. Every registration
        path funnels through here — load_project, activate_context(register=True),
        and resolve_project_context (path-scoped reads) — so the cap is enforced
        uniformly.

        Returns the list of project_ids evicted by the cap check (empty when the
        registry is at/below RESIDENT_CAP — i.e. INERT below the cap, the common
        case). The just-registered ctx is freshly stamped (MRU) and protected as
        either the active id or via the explicit protect set, so it is never the
        victim of its own registration.

        LOCK DISCIPLINE: the insert + recency stamp run under `_registry_lock`;
        the cap check is called AFTER releasing the lock so its heavy per-victim
        save (mutation_lock + io-lock) never nests under `_registry_lock`.
        `activate_context` inlines the insert itself (it needs the `_active` swap
        in the same critical section) and then calls `_evict_if_over_cap`
        directly — it does NOT route through this method, so the no-nesting
        invariant holds on every registration path.
        """
        with cls._registry_lock:
            ctx.last_interacted_at = time.monotonic()
            cls._contexts[project_id] = ctx
        return cls._evict_if_over_cap(protected_ids={project_id})

    @classmethod
    def _evict_if_over_cap(cls, protected_ids: set[str] | None = None) -> list[str]:
        """
        Enforce RESIDENT_CAP by evicting least-recently-interacted contexts.

        Policy: while `len(_contexts) > RESIDENT_CAP`, evict the resident ctx with
        the smallest `last_interacted_at` that is NOT (a) the active ctx, (b) in
        `protected_ids`, or (c) carrying a queued/running solve. If no evictable
        victim exists (all over-cap ctxs are protected) → STOP (log it; never
        force-drop a protected ctx). Returns the list of evicted project_ids.

        LOCK ORDERING (critical): the registry membership mutation (picking +
        POPPING victims from `_contexts`) runs under `_registry_lock`; the heavy
        per-victim SAVE (`_save_context`, which takes the victim's mutation_lock +
        the global netCDF I/O lock) runs OUTSIDE it. `_registry_lock` must never
        nest a per-ctx mutation lock (deadlock). We resolve "save-before-data-
        loss" vs "no heavy I/O under registry_lock" by detaching first: under the
        lock we POP each victim from `_contexts` (so it's no longer resolvable)
        while keeping a reference to the detached ctx; then OUTSIDE the lock we
        save each detached ctx to disk. The ctx is unregistered but its in-memory
        edits are still persisted before the reference is dropped and GC'd
        (dropping the ctx GCs its per-project undo stack too — nothing extra).
        If a victim's save FAILS we have already detached it; we log and move on
        (the in-memory copy is lost on GC, but a stranded over-cap registry is
        worse — and the common case below the cap never reaches here at all).

        NOTE on save-then-detach vs detach-then-save: the brief in the v5 design
        prefers detach-then-save for the lock discipline. The only behavioural
        difference from a pure save-first ordering is that a save failure can no
        longer "skip and keep resident" the victim (it's already detached). We
        instead pre-filter to BOUND, non-empty victims (the only ones with disk
        state worth saving) and rely on the protected set to keep anything
        actively in use resident.
        """
        protected = set(protected_ids or set())
        active_id = cls.get_active_id()
        if active_id is not None:
            protected.add(active_id)
        # Projects with a queued/running solve must stay resident — their
        # in-memory ctx is being solved (foreground-resident or background).
        # Lazy import to avoid a services <-> solve_queue import cycle.
        try:
            from services.solve_queue import solve_queue
            for j in solve_queue.list_jobs():
                if j.get("status") in ("queued", "running"):
                    pid = j.get("project_id")
                    if pid is not None:
                        protected.add(pid)
        except Exception:  # noqa: BLE001 — never let a probe failure strand the cap
            logger.exception("eviction: solve_queue protected-set probe failed")

        detached: list[tuple[str, ProjectContext]] = []
        with cls._registry_lock:
            while len(cls._contexts) > cls.RESIDENT_CAP:
                # Candidate victims = resident, not protected. Pick the smallest
                # recency stamp (least-recently-interacted).
                candidates = [
                    (pid, c) for pid, c in cls._contexts.items()
                    if pid not in protected
                ]
                if not candidates:
                    logger.warning(
                        "eviction: registry over cap (%d > %d) but all contexts "
                        "are protected (active or solving) — leaving over-cap",
                        len(cls._contexts), cls.RESIDENT_CAP,
                    )
                    break
                victim_id, victim_ctx = min(
                    candidates, key=lambda kv: kv[1].last_interacted_at
                )
                # Detach NOW (under the lock) so it's no longer resolvable; save
                # it OUTSIDE the lock below. Protect against re-picking on the
                # next loop iteration by adding it to `protected` too.
                cls._contexts.pop(victim_id, None)
                protected.add(victim_id)
                detached.append((victim_id, victim_ctx))

        # Save each detached victim to disk OUTSIDE `_registry_lock` (the save
        # takes the victim's mutation_lock + the global netCDF I/O lock, which
        # must never nest under the registry lock). Only bound, non-empty ctxs
        # have disk state worth persisting.
        evicted: list[str] = []
        for victim_id, victim_ctx in detached:
            cls._save_evicted_ctx(victim_id, victim_ctx)
            evicted.append(victim_id)
        return evicted

    @staticmethod
    def _save_evicted_ctx(victim_id: str, victim_ctx: ProjectContext) -> None:
        """
        Best-effort persist a just-detached eviction victim to disk.

        Skips unbound (`loaded_project is None`) or empty (no buses) contexts —
        nothing on-disk to protect. Wraps `_save_context` in try/except so a save
        failure (e.g. a 409 in-flight gate, an HDF5 hiccup) logs and the eviction
        still completes — the victim is already detached from the registry. Lazy
        import of the router save helper avoids a service -> router import cycle.

        Chatbot v6 Phase 0 (v4-MAJOR-2 placement): the chat_state flush runs
        AFTER `_save_context` succeeds, INSIDE the same try/except umbrella so a
        flush failure logs and the eviction still completes. Placed here (NOT in
        `_evict_if_over_cap`) so the flush inherits the OUTSIDE-`_registry_lock`
        lock-discipline of the save path — `flush_to_disk` may acquire the
        victim's `chat_state.lock` and write `chat.jsonl`, which must never nest
        under `_registry_lock` (deadlock avoidance — see `_evict_if_over_cap`
        docstring). The lazy import of chat_service avoids the
        pypsa_service -> chat_service -> pypsa_service cycle.
        """
        if victim_ctx.loaded_project is None or victim_ctx.network.buses.empty:
            return
        try:
            from routers.projects import _save_context
            _save_context(
                victim_ctx, victim_id, expect=victim_id, persist_user_ts=False,
            )
            # Chat history flush. Phase 0: no-op (append_turn writes
            # synchronously, no in-memory buffer). Phase 1+ may add a buffered
            # flush. Kept here so the call site is stable across phases — and
            # so the flush inherits the eviction's outside-_registry_lock
            # placement (lock-discipline invariant).
            from services import chat_service
            chat_service.flush_to_disk(victim_ctx)
        except Exception:  # noqa: BLE001 — eviction must complete regardless
            logger.exception(
                "eviction: failed to save victim %r before drop; its in-memory "
                "edits are lost but the registry cap is enforced", victim_id,
            )

    @classmethod
    def get_context(cls, project_id: str) -> ProjectContext | None:
        """
        The resident context for a project_id, or None if not resident.
        B6 path-scoped endpoints resolve their target context through this.
        """
        return cls._contexts.get(project_id)

    @classmethod
    def set_active(cls, project_id: str) -> ProjectContext:
        """
        Make a RESIDENT context the active one (instant in-memory switch —
        B8's tab switch, replacing the abort→save→load round-trip). Raises
        KeyError if the project isn't resident. Clears the rep-period swap cache
        (keyed without network identity, so it must not survive a switch).

        The registry lookup + `_active` assignment run under `_registry_lock`
        (the swap-independent global lock `activate_context` also uses) so the
        pointer swap is atomic w.r.t. concurrent registry ops (a background
        solve's activate_context, a B9 eviction). The KeyError on an unknown id
        propagates to the caller (the activate endpoint maps it to a rebuild).
        """
        with cls._registry_lock:
            ctx = cls._contexts[project_id]
            ctx.last_interacted_at = time.monotonic()  # resident switch → MRU (B9)
            cls._active = ctx
        cls._clear_swap_caches()
        return ctx

    @classmethod
    def drop(cls, project_id: str) -> None:
        """
        Evict a resident context from the registry (B9 LRU eviction). Does
        not touch `_active`; B9 guarantees the victim is never the active one.
        """
        cls._contexts.pop(project_id, None)

    @classmethod
    def list_ids(cls) -> list[str]:
        """project_ids of all resident contexts (B9 LRU + the header tray)."""
        return list(cls._contexts.keys())

    @classmethod
    def get_active_id(cls) -> str | None:
        """
        project_id of the active context (derived from its binding), or None
        when the active context is unbound (fresh / New Project). Derived rather
        than stored so it can never drift from `_active`.
        """
        return cls._active.loaded_project if cls._active is not None else None

    # ── Loaded-project identity ──────────────────────────────────────────────
    # Authoritative identity of the project the in-memory network was loaded
    # from / belongs to on disk — the single source of truth that the save path
    # enforces. DECOUPLED from `n.name`: that attribute is a mutable *display*
    # title (settable via PUT /network/meta, shown in the StatusBar), so it
    # can't be trusted as an on-disk binding. Set ONLY by the load-class
    # operations (load/import/template/restore/rename) and the first-save
    # "claim"; `None` for a fresh / unbound network. `save_project(expect=…)`
    # compares the caller's asserted identity against this under `_lock`,
    # atomically with the network swap that loads perform — closing the
    # cross-project-overwrite race that destroyed a project on 2026-05-28.
    @classmethod
    def get_loaded_project(cls) -> str | None:
        """Project the in-memory network belongs to on disk, or None if unbound."""
        # Read-only: don't materialize a context just to answer "unbound?".
        return cls._active.loaded_project if cls._active is not None else None

    @classmethod
    def set_loaded_project(cls, name: str | None) -> None:
        """
        Bind the in-memory network to an on-disk project. Called by every
        load-class op (load/import/template/restore), by rename (the folder
        moved), and by save_project's first-save claim. Must be invoked under
        `get_lock()` by callers that also swap the network, so the binding is
        atomic with respect to the swap.
        """
        cls._ensure_active().loaded_project = name

    # ── Transient-row registry ──────────────────────────────────────────────
    # Rows that the solver's `_apply_modelling_assumptions` adds to a
    # component DataFrame (n.generators / n.links / …) for the duration of
    # one LP solve and reverts in `restore()`. Two known sources:
    #
    #   * Vintage expansion — one row per (parent_asset, investment_period)
    #     named `parent@<year>`. Created in vintage_service.py.
    #   * VOLL slack generators — one per bus, named `__voll_<bus>`. Created
    #     in solver_service._apply_modelling_assumptions step 3.
    #
    # These leak into GET /api/network/{component} responses because reads
    # don't acquire the PyPSA lock (per the project's read-never-locks
    # policy). Surfacing them in the asset tables confuses the user and
    # breaks the design intent (one logical asset == one user-visible row).
    #
    # The registry lives on the active ProjectContext as
    # `{component_class: {transient_name, …}}` (per-network by construction).
    # Callers add via mark_transient when n.add() succeeds, remove via
    # unmark_transient when restore deletes the row. `_get_component` in
    # routers/network.py reads via get_transient_rows and filters. A fresh
    # context (reset/set) starts with an empty registry, so a network swap
    # invalidates entries pointing at rows that no longer exist.
    @classmethod
    @contextmanager
    def solving_context(cls, ctx: ProjectContext):
        """
        Bind the transient-registry target to `ctx` for the current thread.

        The /run + dispatcher worker threads wrap their `run_simulation` /
        `run_ac_pf_stage` call in this so the VOLL-slack / vintage-clone marks
        (and the restore-time clear) land on the SOLVING context — for the
        dispatcher (B4.3) that is a background project, NOT the foreground. The
        binding is per-thread (threading.local) and restored on exit (re-entrant
        via the saved `prev`).
        """
        prev = getattr(cls._solving_ctx, "ctx", None)
        cls._solving_ctx.ctx = ctx
        try:
            yield ctx
        finally:
            cls._solving_ctx.ctx = prev

    @classmethod
    def _transient_target(cls, *, ensure: bool) -> ProjectContext | None:
        """
        The ctx whose transient registry mark/unmark/clear operate on.

        The thread-local SOLVING ctx if a solve bound it on this thread, else the
        active ctx. `ensure=True` (mark path) materialises a fresh active ctx if
        none; `ensure=False` (read/clear path) returns None when there's no ctx.
        """
        ctx = getattr(cls._solving_ctx, "ctx", None)
        if ctx is not None:
            return ctx
        return cls._ensure_active() if ensure else cls._active

    @classmethod
    def mark_transient(cls, component_class: str, name: str) -> None:
        ctx = cls._transient_target(ensure=True)
        with ctx.transient_lock:
            ctx.transient_rows.setdefault(component_class, set()).add(name)

    @classmethod
    def unmark_transient(cls, component_class: str, name: str) -> None:
        ctx = cls._transient_target(ensure=True)
        with ctx.transient_lock:
            bucket = ctx.transient_rows.get(component_class)
            if not bucket:
                return
            bucket.discard(name)
            if not bucket:
                ctx.transient_rows.pop(component_class, None)

    @classmethod
    def get_transient_rows(cls, component_class: str) -> set[str]:
        """
        Return a snapshot copy of the transient row names for the given
        class. Safe to iterate without holding the registry lock.
        """
        ctx = cls._transient_target(ensure=False)
        if ctx is None:
            return set()
        with ctx.transient_lock:
            return set(ctx.transient_rows.get(component_class, set()))

    @staticmethod
    def get_transient_rows_for(
        ctx: ProjectContext, component_class: str
    ) -> set[str]:
        """
        Snapshot copy of the transient row names for an EXPLICIT context's
        class — the per-ctx counterpart of `get_transient_rows`, which
        resolves to the active/solving ctx.

        B6 path-scoped reads serve a SPECIFIC resident project's tables and
        must hide THAT project's solver-internal rows (vintage clones, VOLL
        slacks), not the foreground's. Reads under the ctx's own
        `transient_lock` so it's safe to iterate the result without the lock.
        """
        with ctx.transient_lock:
            return set(ctx.transient_rows.get(component_class, set()))

    @classmethod
    def has_any_transient_rows(cls) -> bool:
        """
        Cheap probe — True if the registry has any entries at all.
        Useful for short-circuiting filter loops on healthy networks.
        """
        ctx = cls._transient_target(ensure=False)
        if ctx is None:
            return False
        with ctx.transient_lock:
            return any(ctx.transient_rows.values())

    @classmethod
    def clear_transient(cls) -> None:
        # Safety net for restore()'s finally block + the vintage cleanup
        # endpoint. A no-op when there's no active context yet.
        ctx = cls._transient_target(ensure=False)
        if ctx is None:
            return
        with ctx.transient_lock:
            ctx.transient_rows.clear()
