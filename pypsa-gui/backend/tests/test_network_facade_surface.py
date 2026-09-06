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
}

# Names other modules import from `routers.network` that must stay put: the
# CRUD factory, the HTTP-response helpers, and every route.
_STAYS = [
    "_serialize_component", "_get_component", "_meta_payload",
    "_filter_transient_names", "_create_component", "_merge_partial_update",
    "_update_component", "_delete_component", "_xlsx_response",
    "_push_undo_snapshot", "_apply_profile_upload",
]


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
    The ~80 CRUD routes and their factory are deliberately NOT extracted, and
    `_xlsx_response` returns a `StreamingResponse` — an HTTP concern. If one of
    these moves, this phase's scope grew without the plan being updated.
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
