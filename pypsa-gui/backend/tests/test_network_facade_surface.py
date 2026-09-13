"""
The import-surface tripwire for Phase 4 — extracting the pure helper clusters
out of `routers/network.py`.

`routers/network.py` is the most entangled module in the backend: fifty-plus
import sites across `services/chat_tools.py`, `routers/projects.py`,
`routers/snapshots.py`, `routers/io.py`, `routers/project_network.py`,
`main.py`, `services/solver_service.py` and the tests, and most of them import
PRIVATE helpers by name. The ~80 CRUD routes are deliberately left alone — they
are individually short, and the file is long but not deep there.

Four clusters move, and every one of them is a **pure move**: none of these
functions touches `PyPSAService`-plus-router state the way the results and
compare bodies did, so `routers.network` re-exports the identical objects and
not one call site changes.

The one hazard this file exists to pin is `_user_ts`. It is a module-level
mutable dict that `services/chat_tools.py` imports BY VALUE inside a function
body — and that module even carries the comment "only fails if routers/network
refactor breaks paths". Re-exporting a dict is safe only while nothing rebinds
it; `test_the_user_ts_store_is_never_rebound` checks that statically, in both
the router and the service, so a future `_user_ts = {}` fails here rather than
silently splitting the store in two.
"""
import ast
import importlib
import inspect
import pathlib
import re

import pytest

import routers.network as NET


# name -> module it is expected to be DEFINED in.
_MOVED: dict[str, str] = {
    # ── geometry: haversine, bus coordinates, impedance preview ──────────────
    "_EARTH_KM": "services.network_geometry",
    "_haversine_km": "services.network_geometry",
    "_bus_coord": "services.network_geometry",
    "_line_haversine_km": "services.network_geometry",
    "_IMPEDANCE_FIELDS": "services.network_geometry",
    "_impedance_preview": "services.network_geometry",
    "_RecomputeResult": "services.network_geometry",
    "_recompute_lengths_for_bus": "services.network_geometry",

    # ── transformer voltage / type rules ─────────────────────────────────────
    "_VNOM_TOL_KV": "services.transformer_rules",
    "_validate_transformer_voltage": "services.transformer_rules",
    "_enrich_transformer_voltage": "services.transformer_rules",
    "_sanitise_transformer_type": "services.transformer_rules",

    # ── synthetic profile shapes + carrier classification ────────────────────
    "_ELEC_CARRIERS": "services.profile_shapes",
    "_H2_CARRIERS_LOAD": "services.profile_shapes",
    "_HEAT_CARRIERS": "services.profile_shapes",
    "_load_section": "services.profile_shapes",
    "_h2_load_profile": "services.profile_shapes",
    "_heat_load_profile": "services.profile_shapes",
    "_double_peak_profile": "services.profile_shapes",
    "_shape_for_section": "services.profile_shapes",
    "_template_snapshots": "services.profile_shapes",
    "_RENEWABLE_KW": "services.profile_shapes",
    "_CONVENTIONAL_KW": "services.profile_shapes",
    "_DR_KW": "services.profile_shapes",
    "_gen_category": "services.profile_shapes",
    "_profile_meta_for": "services.profile_shapes",
    "_solar_cf_profile": "services.profile_shapes",
    "_wind_cf_profile": "services.profile_shapes",
    "_flat_cf_profile": "services.profile_shapes",
    "_H2_CARRIERS": "services.profile_shapes",
    "_link_category": "services.profile_shapes",

    # ── the snapshot MultiIndex builder ──────────────────────────────────────
    # Its own module: the routes use it AND `_ensure_snapshots_cover_user_ts`
    # does, so it belongs to neither and sits below both — the same reasoning
    # that gave `services/solver/vintage_store.py` its own file in Phase 1.
    "_build_period_multiindex": "services.snapshot_index",

    # ── the user time-series store ───────────────────────────────────────────
    "_user_ts": "services.user_timeseries",
    "_user_ts_lock": "services.user_timeseries",
    "_TS_COMPONENTS": "services.user_timeseries",
    "_user_ts_rename_asset": "services.user_timeseries",
    "_user_ts_delete_asset": "services.user_timeseries",
    "_user_ts_extent": "services.user_timeseries",
    "_annual_hourly_reference": "services.user_timeseries",
    "_serialize_user_ts": "services.user_timeseries",
    "_restore_user_ts": "services.user_timeseries",
    "_backup_network_ts_to_user_ts": "services.user_timeseries",
    "_rebase_flat_user_ts": "services.user_timeseries",
    "_ensure_snapshots_cover_user_ts": "services.user_timeseries",
    "_reapply_user_ts_to_network": "services.user_timeseries",
    "_capture_snapshot_weights_per_timestep": "services.user_timeseries",
    "_reapply_snapshot_weights": "services.user_timeseries",
    "_flatten_snapshot_state": "services.user_timeseries",
    "_parse_upload": "services.user_timeseries",

    # ── bulk edit (`PATCH /_bulk`) ───────────────────────────────────────────
    # Body + coerce / finite-default rules. The FastAPI handler stays thin in
    # the router; see `_LIFTED_HANDLERS` below.
    "_COMPONENT_ATTRS": "services.network_bulk",
    "_FINITE_DEFAULT_BOUNDS": "services.network_bulk",
    "_finite_input_meta": "services.network_bulk",
    "_finite_bound_default": "services.network_bulk",
    "_bool_input_default": "services.network_bulk",
    "_coerce_bulk_value": "services.network_bulk",

    # ── undo capture (middleware + /undo routes) ──────────────────────────────
    "_push_undo_snapshot": "services.network_undo",

    # ── generic CRUD factory ──────────────────────────────────────────────────
    "_serialize_component": "services.network_crud",
    "_get_component": "services.network_crud",
    "_meta_payload": "services.network_crud",
    "_drop_unknown_extras": "services.network_crud",
    "_normalise_flag_column": "services.network_crud",
    "_create_component": "services.network_crud",
    "_merge_partial_update": "services.network_crud",
    "_detach_component_series": "services.network_crud",
    "_reattach_component_series": "services.network_crud",
    "_rename_component_safely": "services.network_crud",
    "_update_component": "services.network_crud",
    "_delete_component": "services.network_crud",
}

# Names other modules import from `routers.network` that must remain exported.
# The CRUD factory now lives in services.network_crud (see `_MOVED`); this list
# is empty — kept so the parametrized stay tests still collect.
_STAYS = [
    ]

# Thin FastAPI handlers whose bodies live in services. The handler name stays
# on `routers.network` (chat_tools imports them by name); the compute function
# is defined in the service module. Optional third tuple element = extra
# `co_names` allowed on the thin wrapper (e.g. an injected CRUD factory).
_LIFTED_HANDLERS: dict[str, tuple] = {
    "bulk_update": ("services.network_bulk", "apply_bulk_update"),
    "undo_info": ("services.network_undo", "get_undo_info"),
    "undo_last": ("services.network_undo", "apply_undo"),
    "update_bus": ("services.network_buses", "apply_update_bus", ("_update_component",)),
    "delete_bus_cascade": ("services.network_buses", "apply_delete_bus_cascade"),
    "rename_bus": ("services.network_buses", "apply_rename_bus"),
    "recalculate_line_lengths": ("services.network_lines", "apply_recalculate_line_lengths"),
    "rescale_impedances": ("services.network_lines", "apply_rescale_impedances"),
    "create_global_constraint": ("services.network_global_constraints", "apply_create_global_constraint"),
    "update_global_constraint": (
        "services.network_global_constraints",
        "apply_update_global_constraint",
        ("_merge_partial_update",),
    ),
    "delete_global_constraint": ("services.network_global_constraints", "apply_delete_global_constraint"),
}

# `_filter_transient_names` was in the list above until Phase 5, and came out
# deliberately rather than quietly. It is a pure domain helper over
# `PyPSAService` with no HTTP in it, and Phase 5 gave it a SECOND caller in
# `routers/network_time_axis.py` — which must not import back from the router it
# was split out of, because that is a cycle. It now lives in
# `services/transient_rows.py`; `routers.network` keeps the private name as an
# alias, so nothing that referenced it changed. The router-scoped assertion
# below no longer applies to it, which is why it is named here instead of
# silently dropped.
_MOVED_OUT_IN_PHASE_5 = {"_filter_transient_names": "services.transient_rows"}


@pytest.mark.parametrize("name,origin", sorted(_MOVED.items()))
def test_the_router_still_exports_every_moved_name(name, origin):
    assert hasattr(NET, name), (
        f"routers.network.{name} is gone. Fifty-plus call sites import from this "
        f"module — re-export it from {origin} rather than repointing them."
    )


@pytest.mark.parametrize("name,origin", sorted(_MOVED.items()))
def test_a_moved_name_is_the_identical_object(name, origin):
    """
    Every cluster here is a pure move, so the router name must BE the service
    object, not a copy. For `_user_ts` and `_user_ts_lock` this is not a style
    point: they are shared mutable state, and two objects would mean two
    stores — the router writing to one and `chat_tools` to the other.
    """
    svc = getattr(importlib.import_module(origin), name)
    assert getattr(NET, name) is svc, f"routers.network.{name} is not {origin}.{name}"


@pytest.mark.parametrize("name", _STAYS)
def test_the_crud_and_http_helpers_stay_in_the_router(name):
    """
    The ~80 CRUD routes and their factory are deliberately NOT extracted. If
    one of these moves, this phase's scope grew without the plan being updated.
    (Profile helpers left with `routers.network_profiles` — see that surface.)
    """
    fn = getattr(NET, name, None)
    assert fn is not None, f"routers.network.{name} disappeared"
    if inspect.isfunction(fn):
        assert fn.__module__ == "routers.network", f"{name} was moved out of the router"


def test_the_user_ts_store_is_never_rebound():
    """
    `services/chat_tools.py` does `from routers.network import _user_ts,
    _user_ts_lock` inside a function and then mutates the dict. That works
    across the re-export only because the store is MUTATED in place and never
    reassigned — a single `_user_ts = {}` anywhere would leave the router
    holding one dict and every by-value importer holding another.

    chat_tools even guards its import with "only fails if routers/network
    refactor breaks paths". This is that check, made mechanical.
    """
    backend = pathlib.Path(__file__).resolve().parent.parent
    targets = {"_user_ts", "_user_ts_lock"}
    offenders = []
    for rel, allow_definition in (("routers/network.py", False),
                                  ("services/user_timeseries.py", True)):
        path = backend / rel
        tree = ast.parse(path.read_text())
        defined_at = set()
        for node in ast.walk(tree):
            binds = []
            if isinstance(node, ast.Assign):
                binds = [t for t in node.targets if isinstance(t, ast.Name)]
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and isinstance(node.target, ast.Name):
                binds = [node.target]
            for t in binds:
                if t.id not in targets:
                    continue
                # The single module-level definition in the service is the store itself.
                if allow_definition and node.col_offset == 0 and t.id not in defined_at:
                    defined_at.add(t.id)
                    continue
                offenders.append(f"{rel}:{node.lineno}: {ast.unparse(node)[:70]}")
    assert not offenders, (
        "the user-ts store is rebound; by-value importers would split off their "
        "own copy:\n  " + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("module", sorted(set(_MOVED.values())))
def test_the_extracted_services_never_import_routers(module):
    backend = pathlib.Path(__file__).resolve().parent.parent
    path = backend / (module.replace(".", "/") + ".py")
    assert path.is_file(), f"{module} does not exist"
    pat = re.compile(r"^\s*(from\s+routers[\s.]|import\s+routers\b)")
    offenders = [
        f"{path.name}:{i}: {line.strip()}"
        for i, line in enumerate(path.read_text().splitlines(), 1)
        if pat.match(line)
    ]
    assert not offenders, f"{module} imports a router:\n  " + "\n  ".join(offenders)


@pytest.mark.parametrize("name,origin", sorted(_MOVED_OUT_IN_PHASE_5.items()))
def test_a_name_phase_5_moved_out_is_still_reachable_from_the_router(name, origin):
    """
    Phase 5 relocated it, but `routers.network` still has to answer to the name:
    the router's own call sites use it, and dropping the alias would be a silent
    behaviour change rather than a move.
    """
    import importlib

    svc = getattr(importlib.import_module(origin), name.lstrip("_"))
    assert getattr(NET, name) is svc, (
        f"routers.network.{name} is not {origin}.{name.lstrip('_')} — the alias "
        f"must re-export the moved function, not redefine it"
    )


@pytest.mark.parametrize("handler,target", sorted(_LIFTED_HANDLERS.items()))
def test_each_lifted_handler_stays_thin_and_delegates(handler, target):
    """
    The FastAPI route stays on `routers.network` (name + decorator are API);
    the body is a one-line call into the service. A fat body growing back here
    undoes the lift without failing the pure-move `_MOVED` checks.
    """
    module_name, fn_name, *rest = target
    allowed_extra = set(rest[0]) if rest else set()
    module = importlib.import_module(module_name)
    fn = getattr(module, fn_name, None)
    assert callable(fn), f"{module_name}.{fn_name} missing for {handler}"
    assert fn.__module__ == module_name, (
        f"{fn_name} is re-exported into {module_name} rather than defined there"
    )
    wrapper = getattr(NET, handler, None)
    assert callable(wrapper), f"routers.network.{handler} is gone"
    assert wrapper.__module__ == "routers.network", (
        f"{handler} must remain the FastAPI handler on routers.network"
    )
    src = inspect.getsource(wrapper)
    assert fn_name in src, (
        f"routers.network.{handler} no longer calls {fn_name}; the body drifted "
        f"back into the router"
    )
    code = wrapper.__code__
    expected = (fn_name, *sorted(allowed_extra)) if allowed_extra else (fn_name,)
    # Order in co_names follows bytecode; compare as sets + require fn first-ish.
    assert set(code.co_names) == {fn_name, *allowed_extra}, (
        f"routers.network.{handler} co_names={code.co_names!r}; expected "
        f"{sorted({fn_name, *allowed_extra})!r} — the lock / write loop belongs "
        f"in {module_name}"
    )
    assert fn_name in code.co_names

