import threading

import pypsa


class PyPSAService:
    _network: pypsa.Network | None = None
    # RLock (not Lock) — reentrant. Endpoints that internally call other
    # endpoints (e.g. POST /scenarios → save_project) hold the lock at the
    # outer level and need save_project's own `with get_lock():` to no-op
    # rather than deadlock. All call sites are single-threaded same-request
    # flows; the lock's purpose is cross-request serialization, which RLock
    # preserves identically to Lock.
    _lock: threading.RLock = threading.RLock()
    # Separate Lock guarding all netCDF file I/O (import_from_netcdf /
    # export_to_netcdf). netCDF4 / h5py share global HDF5 state and are NOT
    # thread-safe — two concurrent reads on different files race and surface
    # as "NetCDF: HDF error" or "NetCDF: Not a valid ID". The CompareView fires
    # 4 parallel reads (compare-state + results-summary × 2 projects) which
    # reliably triggers this. Lock ordering: pypsa_lock OUTER, netcdf_io_lock
    # INNER. The transient compare/results endpoints only take this lock; the
    # active-network load/save paths take pypsa_lock first, then this one.
    _netcdf_io_lock: threading.Lock = threading.Lock()

    @classmethod
    def initialize(cls) -> None:
        with cls._lock:
            if cls._network is None:
                cls._network = pypsa.Network()
                cls._network.name = "Untitled Project"

    @classmethod
    def get_network(cls) -> pypsa.Network:
        # Self-heal if the singleton is None — happens during uvicorn --reload
        # between the time a module is reimported and the lifespan re-runs
        # initialize(). Without this the GUI shows transient 500s on every save.
        if cls._network is None:
            cls._network = pypsa.Network()
            cls._network.name = "Untitled Project"
        return cls._network

    @classmethod
    def reset_network(cls) -> None:
        cls._network = pypsa.Network()
        cls._network.name = "Untitled Project"
        # Any transient-row entries pointed at the old network — drop them.
        cls.clear_transient()

    @classmethod
    def set_network(cls, n: pypsa.Network) -> None:
        """
        Replace the in-memory network with a new one (e.g. the output of a
        spatial clustering operation). Preserves the existing name so the
        project tab/title don't flicker.
        """
        prev_name = cls._network.name if cls._network is not None else "Untitled Project"
        cls._network = n
        cls._network.name = prev_name
        # Any transient-row entries pointed at the old network — drop them.
        cls.clear_transient()

    @classmethod
    def get_lock(cls) -> threading.RLock:
        return cls._lock

    @classmethod
    def get_netcdf_io_lock(cls) -> threading.Lock:
        return cls._netcdf_io_lock

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
    # The registry tracks `{component_class: {transient_name, …}}`. Callers
    # add via mark_transient when n.add() succeeds, remove via
    # unmark_transient when restore deletes the row. `_get_component` in
    # routers/network.py reads via get_transient_rows and filters. A
    # `clear_transient` exists as a safety net for restore()'s finally
    # block and is called on every network swap (reset/set) to invalidate
    # entries pointing at rows that no longer exist.
    _transient_rows: dict[str, set[str]] = {}
    _transient_lock: threading.Lock = threading.Lock()

    @classmethod
    def mark_transient(cls, component_class: str, name: str) -> None:
        with cls._transient_lock:
            cls._transient_rows.setdefault(component_class, set()).add(name)

    @classmethod
    def unmark_transient(cls, component_class: str, name: str) -> None:
        with cls._transient_lock:
            bucket = cls._transient_rows.get(component_class)
            if not bucket:
                return
            bucket.discard(name)
            if not bucket:
                cls._transient_rows.pop(component_class, None)

    @classmethod
    def get_transient_rows(cls, component_class: str) -> set[str]:
        """
        Return a snapshot copy of the transient row names for the given
        class. Safe to iterate without holding the registry lock.
        """
        with cls._transient_lock:
            return set(cls._transient_rows.get(component_class, set()))

    @classmethod
    def has_any_transient_rows(cls) -> bool:
        """
        Cheap probe — True if the registry has any entries at all.
        Useful for short-circuiting filter loops on healthy networks.
        """
        with cls._transient_lock:
            return any(cls._transient_rows.values())

    @classmethod
    def clear_transient(cls) -> None:
        with cls._transient_lock:
            cls._transient_rows.clear()
