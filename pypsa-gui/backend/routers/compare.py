"""
Read-only comparison / results-summary analytics for projects (the
`/api/projects/{name}/compare-state` + `/results-summary` routes and their
`_compute_*_summary` engine).

Carved out of `routers/projects.py` so the project LIFECYCLE (save / load /
import-bundle / template / scenario / rename / delete / activate, the atomic
writers + restricted unpickler) is isolated from this large, pure read-only
analytics engine. The two share only a handful of lifecycle helpers, imported
from projects.py below (`_safe_project_dir`, `_read_meta`,
`_unwrap_results_state`, `_safe_unpickle_results`). No import cycle: projects.py
never imports from here; main.py mounts both under /api/projects, and
results.py / test_weighting lazily import `_build_snapshot_weights` /
`_compute_economics_summary` from here. (compare <-> results mutual imports are
all in-function/lazy, so there is no import-time cycle.)

The import header below is copied wholesale from projects.py and pruned by ruff;
numpy / math are imported LOCALLY inside the moved functions (projects.py never
had them at module level) but are added here defensively.
"""
from __future__ import annotations

import pathlib

import pandas as pd
from fastapi import APIRouter, HTTPException
from services import period_utils
from models.schemas import (
    CompareState,
    ResultsSummary,
)
from services.pypsa_service import PyPSAService
from services.serialization import safe_float as _safe_float


from routers.projects import (
    PROJECTS_DIR,
    _read_meta,
    _safe_unpickle_results,
    _scenario_fields_from_meta,
    _unwrap_results_state,
)
# Shared with the single-network Results endpoints. These were previously
# imported inside four separate function bodies to dodge a suspected import
# cycle; there is none — verified that `compare -> results` and
# `results -> compare` both import cleanly in either order. Hoisted so the
# dependency is visible at the top of the file like every other one.
from routers.deps import AuthorizedProject, ProjectAccessDep
from routers.results import lp_scaled_load_frame

router = APIRouter()


@router.get("/{name}/compare-state")
def get_compare_state(
    project: AuthorizedProject = ProjectAccessDep,
) -> CompareState:
    """
    Compact, read-only summary of a non-active project's state.

    Loads the project's ``network.nc`` into a transient ``pypsa.Network``,
    computes aggregates, then discards the instance — ``PyPSAService``'s
    active singleton is NOT touched. Designed for the scenario-tree compare
    view: the user can A/B inspect two scenarios without losing whatever
    they're editing in memory.

    Cost: roughly one ``import_from_netcdf`` per call. For typical PyPSA
    networks (≤1000 buses, ≤8760 snapshots) this is ~50-500 ms. Frontend
    should cache the result per (project, last_saved) key; the data is
    immutable until the underlying project is saved again.

    Errors:
      404 — project directory or network.nc missing
      500 — netcdf failed to import (corrupted file)
    """
    import pandas as pd
    import pypsa as _pypsa
    from services.dispatch_status import dispatch_status as _classify_dispatch

    # Authorized, org-scoped directory from `ProjectAccessDep`. Never
    # `_safe_project_dir(name)` — that is a path-traversal guard, and the
    # projects root is shared across orgs, so it resolved any tenant's project.
    name = project.name
    src = project.directory
    nc_path = src / "network.nc"
    if not nc_path.exists():
        raise HTTPException(404, f"Project '{name}' not found")

    temp_n = _pypsa.Network()
    try:
        with PyPSAService.get_netcdf_io_lock():
            temp_n.import_from_netcdf(str(nc_path))
    except Exception as exc:  # noqa: BLE001 — surface PyPSA-side errors
        # Strip the absolute path prefix from the error message so the 500
        # body doesn't leak server FS layout to the client. Localhost-only
        # GUI today, but cheap defence for any future remote deployment.
        msg = str(exc).replace(str(PROJECTS_DIR.resolve()), "<projects>")
        raise HTTPException(500, f"Failed to load project '{name}': {msg}")

    meta = _read_meta(src)
    status_str = _classify_dispatch(temp_n)

    def _carrier_sum(df: pd.DataFrame, col: str) -> dict[str, float]:
        """
        Sum `col` per `carrier` on `df`, returning a JSON-safe dict.

        Empty DataFrame / missing column → empty dict. NaN/inf cells are
        dropped (groupby+sum can return them on all-NaN groups).
        """
        if df.empty or "carrier" not in df.columns or col not in df.columns:
            return {}
        try:
            grouped = df.groupby("carrier")[col].sum()
        except Exception:
            return {}
        out: dict[str, float] = {}
        for k, v in grouped.items():
            if v is None:
                continue
            fv = float(v)
            if fv != fv or fv in (float("inf"), float("-inf")):  # NaN/inf guard
                continue
            out[str(k)] = fv
        return out

    installed_cap = _carrier_sum(temp_n.generators, "p_nom")
    # Optimised capacity: only meaningful when there's a solve to read from.
    # We surface it as None on unsolved/stale networks so the frontend can
    # distinguish "no opt result" from "opt result happens to be 0".
    optimised_cap: dict[str, float] | None = None
    if status_str != "none":
        opt = _carrier_sum(temp_n.generators, "p_nom_opt")
        # PyPSA leaves p_nom_opt at the static default for non-extendable
        # generators — treat as missing only if the entire dict is empty.
        optimised_cap = opt if opt else None
    storage_cap = _carrier_sum(temp_n.storage_units, "p_nom")
    store_cap = _carrier_sum(temp_n.stores, "e_nom")

    peak_demand_mw = 0.0
    total_energy_mwh = 0.0
    if not temp_n.loads.empty:
        # Demand as the LP saw it — prefer loads_t.p (already LP-scaled solver
        # output) and apply cfg.load_scalers to p_set otherwise, via the SAME
        # `lp_scaled_load_frame` helper the Results `/results/loads` endpoint
        # uses. Previously Compare read raw `loads_t.p_set`, ignoring
        # `load_scalers` (2027×1.1, 2028×1.2…) → it under-reported demand vs the
        # Results tab. `from_state=False` so the helper reads temp_n's OWN frame,
        # not the live network's cached result snapshot.
        try:
            from routers.simulation import _state as _sim_state2
            _cfg2 = _sim_state2.get("solver_config")
            loads_t_p = lp_scaled_load_frame(temp_n, _cfg2, from_state=False)
            if loads_t_p is None:
                loads_t_p = temp_n.loads_t.p_set
        except Exception:
            loads_t_p = temp_n.loads_t.p_set
        if not loads_t_p.empty:
            per_snap_total = loads_t_p.abs().sum(axis=1)
            if not per_snap_total.empty:
                peak_demand_mw = float(per_snap_total.max())
                # Energy basis via period_utils (generators column × years) so
                # compare "Total energy" matches the Results-tab demand KPI.
                try:
                    w = period_utils.snapshot_weights(
                        temp_n, "generators", sns=per_snap_total.index
                    ).reindex(per_snap_total.index).fillna(1.0)
                    total_energy_mwh = float((per_snap_total * w).sum())
                except (KeyError, AttributeError):
                    total_energy_mwh = float(per_snap_total.sum())
        elif "p_set" in temp_n.loads.columns:
            # Fallback: static-only loads. Treat the static p_set as a flat
            # per-snapshot demand; not great, but better than reporting 0.
            static_total = float(temp_n.loads["p_set"].abs().sum())
            peak_demand_mw = static_total
            total_energy_mwh = static_total * max(len(temp_n.snapshots), 1)

    # NaN/inf guard on the demand scalars. A malformed all-NaN `loads_t.p_set`
    # yields `float(nan)` from `.max()`/`.sum()` above — Starlette's JSONResponse
    # renders with `allow_nan=False` and would 500 on a non-finite float. The
    # `_carrier_sum` dicts are already filtered; these two scalars weren't.
    peak_demand_mw = _safe_float(peak_demand_mw, 0.0)
    total_energy_mwh = _safe_float(total_energy_mwh, 0.0)

    snap_start: str | None = None
    snap_end: str | None = None
    if len(temp_n.snapshots) > 0:
        sn = temp_n.snapshots
        if isinstance(sn, pd.MultiIndex):
            # Multi-investment-period network: project to the timestep level
            # so the compare view shows a real ISO datetime, not the Python
            # tuple repr `(np.int64(2025), Timestamp('…'))`.
            level1 = sn.get_level_values(1)
            ts0, ts1 = level1[0], level1[-1]
        else:
            ts0, ts1 = sn[0], sn[-1]
        snap_start = ts0.isoformat() if hasattr(ts0, "isoformat") else str(ts0)
        snap_end = ts1.isoformat() if hasattr(ts1, "isoformat") else str(ts1)

    # Objective resolution — see CLAUDE.md "objective_constant must be
    # baseline-anchored" + "myopic n.objective only reflects last period".
    # Old metadata.json snapshots wrote `n._objective` ONLY (variable part)
    # without adding the constant offset; on networks with large existing-
    # capacity CAPEX the saved value can be NEGATIVE (e.g. Project B
    # showed -€115M while real total system cost is +€2.4B). Compute the
    # display total from the loaded `temp_n` itself so projects saved
    # before the worker fix still surface a sane number. For myopic mode
    # this is the last-period contribution only (the in-memory accumulator
    # doesn't persist via netcdf); falls back to meta when temp_n has no
    # objective attrs (e.g. unsolved bundle).
    try:
        _obj_var   = float(getattr(temp_n, "_objective", None) or 0.0)
        _obj_const = float(getattr(temp_n, "_objective_constant", None) or 0.0)
        if _obj_var or _obj_const:
            lp_total: float | None = _obj_var + _obj_const
        else:
            lp_total = None
    except Exception:
        lp_total = None
    # Objective resolution. Two sources, each incomplete in a different mode:
    #   • `lp_total` (= temp_n._objective + _objective_constant) recomputed from
    #     the netcdf. For OVERNIGHT/PERFECT foresight this is the full objective;
    #     for MYOPIC it's the LAST PERIOD ONLY (the horizon accumulator isn't
    #     persisted in the netcdf), so it understates the true total.
    #   • `meta["objective"]` saved at solve time from the live status — the
    #     full-horizon value for myopic, but OLD bundles sometimes stored a
    #     partial (variable-only, occasionally negative) value.
    # Pick the LARGER of the two positive/finite candidates: for myopic this
    # recovers the full horizon (meta 12.57 B vs lp_total 2.97 B); for the
    # legacy partial-meta case the full lp_total wins. Falls back gracefully
    # when only one is available.
    import math as _math_obj
    _meta_obj = meta.get("objective")
    _obj_candidates = [
        v for v in (lp_total, _meta_obj)
        if isinstance(v, (int, float)) and _math_obj.isfinite(v) and v > 0
    ]
    if _obj_candidates:
        display_objective = max(_obj_candidates)
    else:
        display_objective = lp_total if lp_total is not None else _meta_obj

    return CompareState(
        name=name,
        bus_count=len(temp_n.buses),
        generator_count=len(temp_n.generators),
        line_count=len(temp_n.lines),
        load_count=len(temp_n.loads),
        link_count=len(temp_n.links),
        storage_unit_count=len(temp_n.storage_units),
        store_count=len(temp_n.stores),
        snapshot_count=len(temp_n.snapshots),
        snapshot_start=snap_start,
        snapshot_end=snap_end,
        installed_capacity_by_carrier=installed_cap,
        optimised_capacity_by_carrier=optimised_cap,
        storage_capacity_by_carrier=storage_cap,
        store_capacity_by_carrier=store_cap,
        peak_demand_mw=peak_demand_mw,
        total_energy_mwh=total_energy_mwh,
        objective=display_objective,
        solve_time=meta.get("solve_time"),
        dispatch_status=status_str,
        parent_project=meta.get("parent_project"),
        # Decoded, not raw: a bundle written before migration 0004 carries the
        # category as a `[type]` prefix inside the description, and the
        # compare payload would otherwise ship the marker as prose.
        **_scenario_fields_from_meta(meta),
        created_at=meta.get("created_at"),
        last_saved=meta.get("last_saved"),
    )


# ── Results-Summary helpers (Compare Scenarios v2) ────────────────────────────
# These compute per-category result summaries for a transient network. They
# deliberately avoid reading anything from `_state` so the same code path can
# serve an active OR a saved-to-disk project — `_state` is in-memory-only and
# only holds the active project's _sources_; the disk network always carries
# the LP result columns (PyPSA's export_to_netcdf serialises _t tables).




def _read_lost_load_capture(project_dir: pathlib.Path) -> dict | None:
    """
    Read the VOLL slack capture (``last_lost_load``) out of a project's
    ``results_state.pkl``.

    The capture cannot live on the network: solver_service strips the slack
    generators immediately after capturing them, so the DataFrame never
    survives a netcdf round-trip. It is persisted alongside the rest of the
    solver state instead. Shape:
      ``{lost_load_t: DataFrame(snapshot x bus), lost_load_total_mwh: float,
         lost_load_cost_eur: float}``
    Returns ``None`` for every "no capture" state (no pickle, unreadable
    pickle, key absent) — all of which mean "this project shed nothing".
    """
    results_path = project_dir / "results_state.pkl"
    if not results_path.exists():
        return None
    try:
        raw = _safe_unpickle_results(results_path.read_bytes())
    except Exception:
        return None
    data = _unwrap_results_state(raw)
    cap = data.get("last_lost_load") if isinstance(data, dict) else None
    return cap if isinstance(cap, dict) else None


@router.get("/{name}/results-summary")
def get_results_summary(
    project: AuthorizedProject = ProjectAccessDep,
) -> ResultsSummary:
    """
    Per-tab results summary for the Compare-Scenarios v2 view.

    Returns capacity + dispatch in Phase 1. Future phases (loading, prices,
    emissions, economics, curtailment, lost-load, storage-cycling) will fill
    in additional optional fields on the same payload without breaking
    consumers.

    Loads the project's ``network.nc`` into a transient ``pypsa.Network``,
    aggregates result-side metrics, then discards the instance — same
    pattern as ``compare-state``, no impact on the active singleton.
    """
    import pandas as pd
    import pypsa as _pypsa
    from services.dispatch_status import dispatch_status as _classify_dispatch

    # Authorized, org-scoped directory from `ProjectAccessDep`. Never
    # `_safe_project_dir(name)` — that is a path-traversal guard, and the
    # projects root is shared across orgs, so it resolved any tenant's project.
    name = project.name
    src = project.directory
    nc_path = src / "network.nc"
    if not nc_path.exists():
        raise HTTPException(404, f"Project '{name}' not found")

    temp_n = _pypsa.Network()
    try:
        with PyPSAService.get_netcdf_io_lock():
            temp_n.import_from_netcdf(str(nc_path))
    except Exception as exc:  # noqa: BLE001
        msg = str(exc).replace(str(PROJECTS_DIR.resolve()), "<projects>")
        raise HTTPException(500, f"Failed to load project '{name}': {msg}")

    is_multi = isinstance(temp_n.snapshots, pd.MultiIndex)
    periods: list[int] = []
    if is_multi:
        try:
            periods = sorted({int(p) for p in temp_n.snapshots.get_level_values(0)})
        except Exception:
            periods = []
    has_solve = _classify_dispatch(temp_n) == "fresh"

    capacity = _compute_capacity_summary(temp_n, periods, is_multi, has_solve)
    dispatch = _compute_dispatch_summary(temp_n, periods, is_multi, has_solve)
    loading = _compute_loading_summary(temp_n, periods, is_multi, has_solve)
    prices = _compute_prices_summary(temp_n, periods, is_multi, has_solve)
    emissions = _compute_emissions_summary(temp_n, periods, is_multi, has_solve)
    economics = _compute_economics_summary(
        temp_n, periods, is_multi, has_solve, prices_from_state=False,
        lost_load_cap=_read_lost_load_capture(src),
    )
    curtailment = _compute_curtailment_summary(temp_n, periods, is_multi, has_solve)
    lost_load = _compute_lost_load_summary(src, temp_n, periods, is_multi, has_solve)
    storage_cycling = _compute_storage_cycling_summary(temp_n, periods, is_multi, has_solve)

    return ResultsSummary(
        project=name,
        is_multi_period=is_multi,
        periods=periods,
        has_solve=has_solve,
        capacity=capacity,
        dispatch=dispatch,
        loading=loading,
        prices=prices,
        emissions=emissions,
        economics=economics,
        curtailment=curtailment,
        lost_load=lost_load,
        storage_cycling=storage_cycling,
    )


# ── Façade over services/compare/ ────────────────────────────────────────────
# The engine lives in `services/compare/` (see the decomposition spec, Phase 3
# addendum). Pure functions are re-exported under their old names so the
# tests, `routers/results.py` and `services/chat_tools.py` that import them
# from here are untouched. The four that used to resolve solver state inline
# — `_periodized_lookup`, the capacity / dispatch / economics summaries — are
# WRAPPED: the router resolves the state exactly as the engine did and passes
# it in as keyword arguments. `_read_lost_load_capture` stays here because it
# goes through the projects router's restricted unpickler.
from services.compare import capacity as _svc_capacity
from services.compare import dispatch as _svc_dispatch
from services.compare import economics as _svc_economics
from services.compare import support as _svc_support
from services.compare.capacity import _compute_total_annuitised_capex  # noqa: F401
from services.compare.curtailment import _compute_curtailment_summary  # noqa: F401
from services.compare.emissions import _compute_emissions_summary  # noqa: F401
from services.compare.loading import _compute_loading_summary  # noqa: F401
from services.compare.lost_load import compute_lost_load_summary
from services.compare.prices import _compute_prices_summary  # noqa: F401
from services.compare.storage_cycling import _compute_storage_cycling_summary  # noqa: F401
from services.compare.support import (  # noqa: F401
    _CLS_TO_ATTR,
    _bucket_add,
    _bucket_replicate_per_period,
    _build_snapshot_weights,
    _classify_build_year,
    _co2_intensity_map,
    _per_period_groupby,
    _safe_capital_cost,
    _to_pv,
    _to_pv_dict,
)
from services.solver_service import SolverConfig
from routers.results import _result_df


def _live_solver_config():
    """
    The solver config the engine used to resolve inline: the LIVE
    ``routers.simulation._state["solver_config"]``, falling back to a default
    ``SolverConfig()`` if the state has none or the lookup raises. Kept with
    the same try/except so the wrappers below degrade exactly as the inline
    code did.

    This is the active project's config even when ``get_results_summary`` is
    summarising a different project loaded from disk — as it always was; the
    engine's old docstring called that deliberate. It is now a visible
    argument instead of a lazy import three calls deep.
    """
    try:
        from routers.simulation import _state as _sim_state
        return _sim_state.get("solver_config") or SolverConfig()
    except Exception:
        return SolverConfig()


def _periodized_lookup(n) -> dict:
    return _svc_support._periodized_lookup(n, cfg=_live_solver_config())


def _compute_capacity_summary(n, periods, is_multi, has_solve):
    return _svc_capacity._compute_capacity_summary(
        n, periods, is_multi, has_solve, cfg=_live_solver_config(),
    )


def _compute_dispatch_summary(n, periods, is_multi, has_solve):
    # The engine read `_state.get("solver_config")` — possibly None, no
    # default — for the load scalers, inside a try that also covered the
    # import. The import cannot fail once the app is up; the None is passed
    # through unchanged.
    try:
        from routers.simulation import _state as _sim_state
        cfg = _sim_state.get("solver_config")
    except Exception:
        cfg = None
    return _svc_dispatch._compute_dispatch_summary(n, periods, is_multi, has_solve, cfg=cfg)


def _compute_economics_summary(n, periods, is_multi, has_solve, prices_from_state: bool = True,
                               lost_load_cap: dict | None = None):
    return _svc_economics._compute_economics_summary(
        n, periods, is_multi, has_solve, prices_from_state, lost_load_cap,
        cfg=_live_solver_config(), result_df=_result_df,
    )


def _compute_lost_load_summary(project_dir: pathlib.Path, n, periods, is_multi, has_solve):
    return compute_lost_load_summary(_read_lost_load_capture(project_dir), n, periods, is_multi, has_solve)
