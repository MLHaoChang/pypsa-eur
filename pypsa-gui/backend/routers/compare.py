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
from services import economics, period_utils
from models.schemas import (
    AssetLCOHEntry,
    CapacityComparison,
    CarrierEconomics,
    CarrierPeriodValue,
    CarrierPriceStats,
    CompareState,
    CurtailmentComparison,
    DispatchComparison,
    EconomicsComparison,
    EmissionsComparison,
    LineLoadingEntry,
    LoadingComparison,
    LostLoadBus,
    LostLoadComparison,
    PricesComparison,
    ResultsSummary,
    StorageCyclingComparison,
    StorageUnitCycles,
)
from services.pypsa_service import PyPSAService
from services.serialization import safe_float as _safe_float

import numpy as np

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
from routers.results import corrected_marginal_prices, lp_scaled_load_frame

import logging

logger = logging.getLogger("pypsa_gui.compare")

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

def _bucket_add(d: dict, key: str, value: float, period: int | None) -> None:
    """
    Accumulate ``value`` into ``d[key]['total']`` (and ``by_period[period]``
    when ``period`` is set). The bucket shape mirrors ``CarrierPeriodValue`` so
    the dicts can be cast straight into the schema without a second pass.
    """
    if key not in d:
        d[key] = {"total": 0.0, "by_period": {}}
    d[key]["total"] += value
    if period is not None:
        ps = str(period)
        d[key]["by_period"][ps] = d[key]["by_period"].get(ps, 0.0) + value


def _bucket_replicate_per_period(d: dict, key: str, value: float, periods: list) -> None:
    """
    Replicate ``value`` across EVERY period's by_period bucket WITHOUT
    re-adding to ``total``. Used for brownfield (pre-build) capacity that
    operates in every investment period — without replicating, the Compare
    View period filter shows the per-period bar dropping to incremental-only
    (e.g. gas total=484 MW but by_period[2026]=84 MW → switching the period
    selector from "All" to "2026" makes the bar shrink by 400 MW even though
    those 400 MW of brownfield are still in service).
    """
    if not periods:
        return
    if key not in d:
        d[key] = {"total": 0.0, "by_period": {}}
    for p in periods:
        ps = str(int(p))
        d[key]["by_period"][ps] = d[key]["by_period"].get(ps, 0.0) + value


def _to_pv_dict(d: dict) -> dict:
    """
    Materialise the loose accumulator dicts into ``CarrierPeriodValue``
    instances ready for the Pydantic schema.
    """
    from models.schemas import CarrierPeriodValue as _CPV
    return {k: _CPV(total=v["total"], by_period=v["by_period"]) for k, v in d.items()}


def _to_pv(d: dict) -> CarrierPeriodValue:
    """Single-bucket variant for OPEX / total-load totals (no carrier split)."""
    from models.schemas import CarrierPeriodValue as _CPV
    return _CPV(total=d["total"], by_period=d["by_period"])


def _periodized_lookup(n) -> dict:
    """
    Build ``services.solver_service.periodized_capital_costs``'s per-asset
    dict ONCE for this network, using the same live ``SolverConfig`` every
    other economics surface reads (``routers.simulation._state["solver_config"]``
    -- the same object ``asset_economics`` / ``cost_breakdown`` / ``asset_costs``
    resolve their ``cfg`` from). Callers that walk many asset rows (the
    ``_walk_*`` closures below) must call this ONCE per network and pass the
    result to ``_safe_capital_cost`` -- ``periodized_capital_costs`` walks
    every cost-bearing component class in one pass, so calling it per-row
    would be O(rows) times more expensive for the exact same answer.

    Returns ``{}`` if ``periodized_capital_costs`` itself raises. Every
    ``_safe_capital_cost`` lookup against an empty dict already resolves to
    0.0 (see that function's docstring), so this degrades to "no CAPEX
    contribution from any asset" rather than a 500 -- matching
    ``get_economics_by_carrier`` (routers/results.py), which wraps its
    whole ``_compute_economics_summary`` call in a try/except for the same
    reason. Before this fix, a ``periodized_capital_costs`` raise propagated
    uncaught through ``_compute_capacity_summary`` / ``_compute_economics_
    summary`` into ``get_results_summary`` (routers/compare.py), which has
    no such guard -- turning what ``get_economics_by_carrier`` treats as
    graceful degradation into a plain 500 for the Compare-Scenarios view.
    """
    from services.solver_service import SolverConfig, periodized_capital_costs

    try:
        from routers.simulation import _state as _sim_state
        cfg = _sim_state.get("solver_config") or SolverConfig()
    except Exception:
        cfg = SolverConfig()
    try:
        return periodized_capital_costs(n, cfg)
    except Exception:
        # Return contract is deliberately unchanged (``{}``): every caller
        # already reads through ``_safe_capital_cost``, and reshaping this
        # without auditing all of them is how the last round of bugs happened.
        # But the degradation is silent and total — every asset's CAPEX reads
        # 0.0 — so it must at least be diagnosable from pypsa-gui.log. Matches
        # `services/asset_results/compute.py`, which names the consequence in
        # the log line rather than just recording that something failed.
        logger.exception(
            "periodized_capital_costs failed while building the Compare view; "
            "every asset's capital cost will read 0.00 in this comparison",
        )
        return {}


def _safe_capital_cost(row, pcc: dict, comp_attr: str) -> float:
    """
    LP-effective annuitised EUR/MW/yr for one asset row.

    Delegates entirely to ``services.solver_service.periodized_capital_costs``
    -- the SAME resolution ``asset_economics``, ``cost_breakdown``,
    ``asset_costs`` and (since Task 5) Asset Detail all use. ``pcc`` is that
    function's per-network output (build it once via ``_periodized_lookup(n)``
    and reuse it across every row -- see that helper's docstring for why).
    ``comp_attr`` selects which top-level bucket (``"generators"``,
    ``"storage_units"``, ``"stores"``, ``"links"``, ``"lines"``,
    ``"transformers"``) this row belongs to.

    This function used to hand-roll
    ``overnight_cost * annuity(discount_rate, lifetime)`` for the
    overnight_cost path, which omitted the ``nyears`` (horizon-fraction)
    scaling PyPSA's own ``capital_cost`` accessor applies via
    ``periodized_cost(..., nyears=n.nyears)`` -- 365x too high on the golden
    fixture's unit-weighted 24-snapshot periods, 52.14x on a unit-weighted
    168-hour week. See
    docs/superpowers/findings/2026-08-01-economic-surface-disagreements.md
    Sections 4 and 9 for the measured defect this replaced. Reimplementing a
    resolution that already exists elsewhere (``periodized_capital_costs``)
    is what caused that bug; this function now defers to it instead of
    carrying a second, independent annuity implementation that can drift out
    of sync.

    ``periodized_capital_costs`` itself PREFERS ``overnight_cost`` over a
    non-zero ``capital_cost`` column (see that function's docstring) -- the
    same preference this function used to hand-implement -- so there is no
    behavioural difference there; only the missing ``nyears`` factor is
    fixed.

    Returns 0.0 when the asset has no entry in ``pcc`` (e.g. not a
    cost-bearing class, or the row's name isn't in the network) -- caller
    skips the contribution, matching the old function's contract.

    CONTRACT: ``row`` must be a pandas Series obtained via ``df.loc[name]``
    (its ``.name`` is how this function knows which asset to look up in
    ``pcc``). A plain dict -- or anything else without a ``.name`` -- has no
    identity to look up and silently resolves to 0.0 rather than raising.
    Every call site in this module binds ``row`` this way today, but a
    future caller passing a dict would hit this silently, not loudly.
    """
    name = getattr(row, "name", None)
    if name is None:
        return 0.0
    entry = (pcc.get(comp_attr) or {}).get(name)
    if not entry:
        return 0.0
    return _safe_float(entry.get("capital_cost"), 0.0)


def _classify_build_year(value) -> int | None:
    """
    Coerce ``value`` to an integer year in the [1900, 2200] range or
    return None. Used to attribute new CAPEX / vintage-expanded capacity to a
    specific investment period; values outside the range (e.g. PyPSA's
    default 0 for pre-existing assets) collapse to None and the caller
    treats them as "no period attribution".
    """
    import math as _math
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    if not _math.isfinite(x):
        return None
    y = int(x)
    if not (1900 <= y <= 2200):
        return None
    return y


# PyPSA component class name -> the `n.<attr>` DataFrame / `periodized_capital_costs`
# top-level bucket it corresponds to. Every `_safe_capital_cost` call site in this
# module already knows the component class it's walking (it's either an explicit
# `cls_name` parameter or the DataFrame being iterated is unambiguous); this maps
# that to the key `periodized_capital_costs` uses.
_CLS_TO_ATTR: dict[str, str] = {
    "Generator": "generators",
    "StorageUnit": "storage_units",
    "Store": "stores",
    "Link": "links",
    "Line": "lines",
    "Transformer": "transformers",
}


def _build_snapshot_weights(n, column: str = "objective") -> pd.Series:
    """
    Return Σ-weight per snapshot = ``snapshot_weightings[column]`` ×
    ``investment_period_weightings.years``.

    ``column`` selects the PyPSA weighting basis:
      * ``"objective"`` — COST quantities (OPEX, revenue, CAPEX commitment).
      * ``"generators"`` — ENERGY quantities (dispatch GWh, served load,
        emissions). This is the column PyPSA's ``n.statistics()`` and the
        Results-tab energy KPIs use; summing energy with the ``objective``
        column diverges from the Results tab whenever the two columns differ
        (e.g. representative-week runs).

    Falls back to the ``objective`` column, then to 1.0 per snapshot, if the
    requested column is missing or misshapen — so an older netcdf without a
    ``generators`` column behaves exactly as before, and a malformed netcdf
    still can't take the comparison view down.

    Thin wrapper kept for its many call sites in this module; the
    implementation lives in `services.period_utils.snapshot_weights` so
    results.py shares one weighting basis with the comparison view.
    """
    return period_utils.snapshot_weights(n, column)


def _per_period_groupby(series, sns, is_multi):
    """
    Group ``series`` by snapshot-level-0 period, returning a dict
    ``{period_str: float}``. Empty for flat networks — the caller relies on
    ``total`` alone in that case.
    """
    if not is_multi:
        return {}
    try:
        grouped = series.groupby(sns.get_level_values(0)).sum()
    except Exception:
        return {}
    out: dict[str, float] = {}
    import math as _math
    for p, v in grouped.items():
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if not _math.isfinite(fv):
            continue
        out[str(int(p))] = fv
    return out


def _compute_capacity_summary(n, periods, is_multi, has_solve) -> CapacityComparison:
    """
    Capacity-expansion side of the comparison payload.

    Returns two CAPEX views:
      • ``capex_meur_by_carrier`` — TOTAL annuitised CAPEX per carrier
        (existing + new) per period, matching ``/results/cost_breakdown``.
        This is the headline number that users can reconcile with the live
        Results panel.
      • ``new_capex_meur_by_carrier`` — increment-only via the vintage walk
        (``vintage_p_nom_opt × annuitised_capital_cost``) and non-vintage
        ``max(0, p_nom_opt − p_nom) × annuitised_capital_cost`` for plain
        assets. Useful for "what additional investment did this scenario
        commit?" but can be misleading when the existing fleet dominates.
    """
    import math as _math
    # Per-asset annuitised capital_cost, resolved once via the SAME
    # `periodized_capital_costs` path `asset_economics` / `cost_breakdown` /
    # `asset_costs` use — see `_periodized_lookup`'s docstring. This reads
    # `_state["solver_config"]` (the currently-active singleton's config, not
    # necessarily the project this network came from — a pre-existing
    # limitation, not addressed here).
    pcc = _periodized_lookup(n)

    # ipw.years per period — multiplier for annuitised €/yr → period total.
    # Empty on a flat network; `years_for_period` then answers 1.0 for every
    # lookup, which is the flat-network semantic.
    years_map = period_utils.period_years_map(n)

    # Generator total capacity (brownfield + built), keyed by carrier. Links
    # used to land here too, but that conflated link MW with generator MW in
    # the Compare View's generator table — they now have their own
    # `link_cap_by_carrier` below.
    cap_by_carrier: dict = {}
    capex_by_carrier: dict = {}
    storage_mw_by_carrier: dict = {}
    storage_mwh_by_carrier: dict = {}
    # New (built) capacity in MW per carrier — GENERATORS ONLY. Storage and
    # link builds have their own maps so each Compare View table (generator /
    # storage / links) pairs a total and a built column over the SAME set of
    # components. Previously this was a shared gen+storage+link bucket, which
    # produced impossible rows (battery total=0 MW / built=427 MW) because the
    # generator table's total column is generators-only.
    new_cap_by_carrier: dict = {}
    # New (built) storage increments — MW (storage_units) and MWh (storage_units
    # × max_hours + stores' e_nom delta).
    new_storage_mw_by_carrier: dict = {}
    new_storage_mwh_by_carrier: dict = {}
    # Link capacity (MW) — total (brownfield + built) and built-only. Feeds a
    # dedicated Links table; heat-pumps / electrolyzers / datacenters / P2X.
    link_cap_by_carrier: dict = {}
    new_link_cap_by_carrier: dict = {}

    # Vintage results live in n.meta after solve; they survive the netcdf
    # round-trip and carry the per-period (build_year) breakdown that the
    # collapsed-onto-parent dataframe rows lose. Structure:
    #   n.meta["vintage_results"][cls][parent_name] = {
    #     "initial_capacity": float,
    #     "capacity_field": "p_nom" | "e_nom" | "s_nom",
    #     "periods": [{"build_year": int, "p_nom_opt": float, ...}],
    #   }
    # Walked BEFORE the plain df pass so we can mark parent_name as "handled
    # via vintages" and avoid double-counting from the parent row.
    vintage_root = (n.meta or {}).get("vintage_results", {})
    if not isinstance(vintage_root, dict):
        vintage_root = {}

    def _vintage_handled(cls_name: str, parent: str) -> bool:
        block = vintage_root.get(cls_name)
        return isinstance(block, dict) and parent in block

    def _walk_vintages(cls_name: str, df, target_cap: dict, target_capex: dict,
                      also_mwh: dict | None = None,
                      target_new_cap: dict | None = None,
                      target_new_mwh: dict | None = None) -> None:
        block = vintage_root.get(cls_name)
        if not isinstance(block, dict) or df is None or df.empty:
            return
        for parent_name, payload in block.items():
            if parent_name not in df.index:
                continue
            row = df.loc[parent_name]
            carrier = str(row.get("carrier", "unknown") or "unknown").lower()
            # Initial (pre-build) capacity contributes to the carrier total
            # under no specific period — it's "already there".
            try:
                ini = float(payload.get("initial_capacity") or 0)
            except (TypeError, ValueError):
                ini = 0.0
            if _math.isfinite(ini) and ini > 0:
                # Brownfield contributes to total ONCE (None branch) and is
                # also replicated into every period's by_period bucket so the
                # Compare View period-filter shows operational fleet, not just
                # incremental builds. Otherwise a 400 MW pre-existing asset
                # vanishes when the user picks a specific period.
                _bucket_add(target_cap, carrier, ini, None)
                _bucket_replicate_per_period(target_cap, carrier, ini, periods)
                if also_mwh is not None:
                    try:
                        mh = float(row["max_hours"]) if "max_hours" in df.columns else 0.0
                    except (TypeError, ValueError):
                        mh = 0.0
                    if _math.isfinite(mh) and mh > 0:
                        _bucket_add(also_mwh, carrier, ini * mh, None)
                        _bucket_replicate_per_period(also_mwh, carrier, ini * mh, periods)
            periods_list = payload.get("periods") or []
            if not isinstance(periods_list, list):
                continue
            # Per-period capacity = vintage p_nom_opt under that build_year.
            # CAPEX = vintage p_nom_opt × annuitised capital_cost from parent.
            cc_per_mw = _safe_capital_cost(row, pcc, _CLS_TO_ATTR[cls_name])
            for entry in periods_list:
                if not isinstance(entry, dict):
                    continue
                by_int = _classify_build_year(entry.get("build_year"))
                try:
                    opt = float(entry.get("p_nom_opt") or 0)
                except (TypeError, ValueError):
                    continue
                if not _math.isfinite(opt) or opt <= 1e-9:
                    continue
                _bucket_add(target_cap, carrier, opt, by_int)
                # Mirror the per-period contribution into the "new (built)" map
                # so the Compare View can show JUST the expansion separately
                # from the total operational fleet. Brownfield (`ini` above)
                # is NOT added to this map — only vintage builds.
                if target_new_cap is not None:
                    _bucket_add(target_new_cap, carrier, opt, by_int)
                if cc_per_mw > 0:
                    _bucket_add(target_capex, carrier, cc_per_mw * opt / 1e6, by_int)
                if also_mwh is not None:
                    try:
                        mh = float(row["max_hours"]) if "max_hours" in df.columns else 0.0
                    except (TypeError, ValueError):
                        mh = 0.0
                    if _math.isfinite(mh) and mh > 0:
                        _bucket_add(also_mwh, carrier, opt * mh, by_int)
                        # New (built) energy increment — vintage builds only,
                        # never brownfield, so the storage table's "built MWh"
                        # column shows just the expansion.
                        if target_new_mwh is not None:
                            _bucket_add(target_new_mwh, carrier, opt * mh, by_int)

    def _walk_plain(df, cls_name: str, nom: str, target_cap: dict, target_capex: dict,
                   also_mwh: dict | None = None,
                   target_new_cap: dict | None = None,
                   target_new_mwh: dict | None = None) -> None:
        """
        For assets WITHOUT a vintage_results entry — read p_nom / p_nom_opt
        straight from the dataframe row. Treats the row's build_year as the
        attribution period if it sits within the planning horizon, otherwise
        the contribution goes only into the aggregated total.

        Brownfield split: the row's `p_nom` is treated as PRE-EXISTING (active
        in every period) and replicated across periods. The delta `p_nom_opt −
        p_nom` is treated as expansion, attributed to the row's build_year if
        it falls within the planning horizon. Without this split, fixed assets
        with build_year outside the horizon (e.g. dc_lowT_dump with build_year=0)
        showed total=500 MW but by_period={} → the Compare View per-period
        bar vanished when the user selected an individual investment period.
        """
        if df is None or df.empty:
            return
        opt_col = f"{nom}_opt"
        if opt_col not in df.columns:
            return
        for asset in df.index:
            if _vintage_handled(cls_name, asset):
                continue  # vintage path already counted this one
            row = df.loc[asset]
            try:
                opt = float(row[opt_col])
            except (TypeError, ValueError):
                continue
            if not _math.isfinite(opt) or opt <= 1e-9:
                continue
            carrier = str(row.get("carrier", "unknown") or "unknown").lower()
            by_int = _classify_build_year(row.get("build_year"))
            try:
                ini = float(row[nom])
            except (TypeError, ValueError):
                ini = 0.0
            if not _math.isfinite(ini) or ini < 0:
                ini = 0.0
            delta = max(0.0, opt - ini)
            # Brownfield: contributes once to total, replicated to every period.
            if ini > 1e-9:
                _bucket_add(target_cap, carrier, ini, None)
                _bucket_replicate_per_period(target_cap, carrier, ini, periods)
            # Expansion: attribute to the build year (single-period bucket).
            if delta > 1e-9:
                _bucket_add(target_cap, carrier, delta, by_int)
                # Mirror the expansion delta into the "new (built)" map; the
                # brownfield branch above is excluded here so the Compare View
                # can show only NEW builds separately from total operational
                # fleet (fixes the "heat-dump 500/500/Δ=0 reads as built" UX bug).
                if target_new_cap is not None:
                    _bucket_add(target_new_cap, carrier, delta, by_int)
                cc = _safe_capital_cost(row, pcc, _CLS_TO_ATTR[cls_name])
                if cc > 0:
                    _bucket_add(target_capex, carrier, cc * delta / 1e6, by_int)
            if also_mwh is not None:
                try:
                    mh = float(row["max_hours"]) if "max_hours" in df.columns else 0.0
                except (TypeError, ValueError):
                    mh = 0.0
                if _math.isfinite(mh) and mh > 0:
                    # Same split for storage MWh capacity.
                    if ini > 1e-9:
                        _bucket_add(also_mwh, carrier, ini * mh, None)
                        _bucket_replicate_per_period(also_mwh, carrier, ini * mh, periods)
                    if delta > 1e-9:
                        _bucket_add(also_mwh, carrier, delta * mh, by_int)
                        # New (built) energy increment — expansion only.
                        if target_new_mwh is not None:
                            _bucket_add(target_new_mwh, carrier, delta * mh, by_int)

    # Vintage path first, then plain. Vintage covers extendable assets with
    # per-period bounds; plain covers everything else (pre-existing fixed
    # capacity, lines/transformers without vintages, simple extendables).
    # Generators → generator capacity + generator-only "built" map.
    _walk_vintages("Generator",   n.generators,    cap_by_carrier,         capex_by_carrier,
                  target_new_cap=new_cap_by_carrier)
    # Storage units → storage MW + MWh, with storage-specific "built" maps.
    _walk_vintages("StorageUnit", n.storage_units, storage_mw_by_carrier,  capex_by_carrier,
                  also_mwh=storage_mwh_by_carrier,
                  target_new_cap=new_storage_mw_by_carrier,
                  target_new_mwh=new_storage_mwh_by_carrier)
    # Stores → energy-only (e_nom is MWh); built MWh into the storage new-mwh map.
    _walk_vintages("Store",       n.stores,        storage_mwh_by_carrier, capex_by_carrier,
                  target_new_cap=new_storage_mwh_by_carrier)
    # Links → dedicated link maps (heat-pumps, electrolyzers, datacenters,
    # P2X). Kept OUT of the generator capacity map so the generator table's
    # total/built columns cover only generators. The Compare View renders a
    # separate Links table from these.
    _walk_vintages("Link",        n.links,         link_cap_by_carrier,    capex_by_carrier,
                  target_new_cap=new_link_cap_by_carrier)
    _walk_plain(n.generators,    "Generator",   "p_nom", cap_by_carrier,         capex_by_carrier,
                target_new_cap=new_cap_by_carrier)
    _walk_plain(n.storage_units, "StorageUnit", "p_nom", storage_mw_by_carrier,  capex_by_carrier,
                also_mwh=storage_mwh_by_carrier,
                target_new_cap=new_storage_mw_by_carrier,
                target_new_mwh=new_storage_mwh_by_carrier)
    _walk_plain(n.stores,        "Store",       "e_nom", storage_mwh_by_carrier, capex_by_carrier,
                target_new_cap=new_storage_mwh_by_carrier)
    _walk_plain(n.links,         "Link",        "p_nom", link_cap_by_carrier,    capex_by_carrier,
                target_new_cap=new_link_cap_by_carrier)

    # ── Total annuitised CAPEX per carrier (existing + new) ──────────────────
    # Headline metric — matches `/results/cost_breakdown` so the compare view
    # reconciles with the live Results panel. For each cost-bearing asset:
    #   annuitised_per_yr = p_nom_opt × cc_per_MW_per_yr
    # cc_per_MW_per_yr falls back from capital_cost → overnight_cost ×
    # annuity(dr, lt). Per-period total = annuitised_per_yr × ipw.years[P].
    # The single-period case collapses to one total entry with no by_period
    # split.
    total_capex_by_carrier: dict = _compute_total_annuitised_capex(
        n, periods, is_multi, years_map, pcc,
    )

    return CapacityComparison(
        capacity_mw_by_carrier=_to_pv_dict(cap_by_carrier),
        capex_meur_by_carrier=_to_pv_dict(total_capex_by_carrier),
        new_capex_meur_by_carrier=_to_pv_dict(capex_by_carrier),
        new_capacity_mw_by_carrier=_to_pv_dict(new_cap_by_carrier),
        storage_mw_by_carrier=_to_pv_dict(storage_mw_by_carrier),
        storage_mwh_by_carrier=_to_pv_dict(storage_mwh_by_carrier),
        new_storage_mw_by_carrier=_to_pv_dict(new_storage_mw_by_carrier),
        new_storage_mwh_by_carrier=_to_pv_dict(new_storage_mwh_by_carrier),
        link_capacity_mw_by_carrier=_to_pv_dict(link_cap_by_carrier),
        new_link_capacity_mw_by_carrier=_to_pv_dict(new_link_cap_by_carrier),
    )


def _compute_total_annuitised_capex(
    n, periods, is_multi, years_map, pcc,
) -> dict:
    """
    Walk every cost-bearing component, accumulate ``p_nom_opt × cc_per_MW``
    per carrier as M€/yr, then expand into per-period values via
    ``ipw.years[P]``. Mirrors the per-period aggregation in
    ``cost_breakdown`` so the two views show the same gas / solar / battery
    CAPEX numbers. ``pcc`` is ``_periodized_lookup(n)``'s output, built once
    by the caller (``_compute_capacity_summary``) and passed through here
    rather than recomputed.
    """
    import math as _math

    out: dict = {}

    def _walk(df, nom_col: str, comp_attr: str, *, extendable_only: bool = False) -> None:
        if df is None or df.empty:
            return
        if extendable_only:
            ext_col = f"{nom_col}_extendable"
            if ext_col not in df.columns:
                return
            df = df[df[ext_col].astype(bool)]
            if df.empty:
                return
        opt_col = f"{nom_col}_opt"
        capacity_col = opt_col if opt_col in df.columns else (nom_col if nom_col in df.columns else None)
        if capacity_col is None:
            return
        for asset in df.index:
            row = df.loc[asset]
            try:
                opt = float(row[capacity_col])
            except (TypeError, ValueError, KeyError):
                continue
            if not _math.isfinite(opt) or opt <= 1e-9:
                continue
            cc_per_mw = _safe_capital_cost(row, pcc, comp_attr)
            if cc_per_mw <= 0:
                continue
            per_year_meur = (opt * cc_per_mw) / 1e6  # M€/yr annuitised
            carrier = str(row.get("carrier", "unknown") or "unknown").lower()
            b = out.setdefault(carrier, {"total": 0.0, "by_period": {}})
            if is_multi and periods:
                # Each period contributes annuitised_per_yr × ipw.years[P].
                # We assume the asset is active in every period — which is
                # what PyPSA's `n.statistics()` does for capex by default
                # (period-aware costing happens at LP time, not at stats
                # time). The horizon total is the sum across periods.
                for p in periods:
                    years = period_utils.years_for_period(years_map, p)
                    contrib = per_year_meur * years
                    b["by_period"][str(p)] = b["by_period"].get(str(p), 0.0) + contrib
                b["total"] = sum(b["by_period"].values())
            else:
                b["total"] += per_year_meur

    # Generation / storage / stores unconditionally, plus links.
    #
    # Lines and transformers stay omitted: a passive branch that the LP
    # cannot resize contributes nothing to the objective, and reporting its
    # notional CAPEX here produced a "line CAPEX" carrier value nobody could
    # reconcile with the Results panel. Branch expansion is visible in the
    # Line loading tab; that is where it belongs.
    #
    # An EXTENDABLE link is a different animal, and excluding it was a defect:
    # the LP does size it, its capital_cost does enter the objective, and
    # `_compute_economics_summary` has always counted it. So the two tabs of
    # one comparison reported different CAPEX for one network — MEASURED at
    # 25.154535 vs 25.320785 M€ on the golden fixture (exactly the
    # electrolyzer's CAPEX) and at a 56.192453 M€ gap on a real three-period
    # project. See docs/superpowers/findings/
    # 2026-08-03-compare-tab-correctness.md §S1.
    #
    # Every link that carries a capital_cost, not only the extendable ones.
    # `cost_breakdown` is built from `n.statistics()`, which charges
    # capital_cost x p_nom_opt for EVERY asset — so restricting this walk to
    # extendables made a fixed link with a capital_cost show up in Economics
    # and vanish from the Capacity tab, contradicting this function's
    # documented contract of mirroring cost_breakdown.
    _walk(n.generators,    "p_nom", "generators")
    _walk(n.storage_units, "p_nom", "storage_units")
    _walk(n.stores,        "e_nom", "stores")
    _walk(n.links,         "p_nom", "links")
    return out


def _compute_dispatch_summary(n, periods, is_multi, has_solve) -> DispatchComparison:
    """
    Dispatch side: GWh per carrier + OPEX (M€) + total load (GWh).
    All time-aggregated quantities are weighted by
    ``snapshot_weightings.objective × investment_period_weightings.years``
    so multi-period networks (where one operational year is replicated under
    each period) accumulate correctly.
    """
    import math as _math

    import pandas as pd

    if not has_solve:
        # Without a solve we still return a populated container — the
        # frontend distinguishes "no solve" via the top-level has_solve
        # flag rather than missing fields.
        return DispatchComparison()

    # ENERGY quantities (dispatch GWh, served load) use the `generators`
    # weighting column — the basis PyPSA's n.statistics() and the Results-tab
    # energy KPIs use. COST quantities (OPEX) use `objective`. Previously both
    # used `objective`, so Compare dispatch GWh diverged from the Results tab
    # whenever the two columns differed (representative-week runs). Identical
    # when the columns are equal (the common single-weight case).
    weights = _build_snapshot_weights(n, "generators")
    cost_weights = _build_snapshot_weights(n, "objective")
    sns = n.snapshots

    dispatch_by_carrier: dict = {}
    opex_bucket = {"total": 0.0, "by_period": {}}
    load_bucket = {"total": 0.0, "by_period": {}}

    def _accum_carrier(carrier: str, weighted_mwh: float, weighted_pp: dict[str, float]) -> None:
        b = dispatch_by_carrier.setdefault(carrier, {"total": 0.0, "by_period": {}})
        b["total"] += weighted_mwh / 1000.0  # → GWh
        for p, v in weighted_pp.items():
            b["by_period"][p] = b["by_period"].get(p, 0.0) + v / 1000.0

    gens = n.generators
    p_t = getattr(n.generators_t, "p", None) if hasattr(n, "generators_t") else None
    if p_t is not None and not p_t.empty:
        for g in p_t.columns:
            if g not in gens.index:
                continue
            try:
                series = p_t[g].reindex(sns).fillna(0.0).astype(float)
            except Exception:
                continue
            weighted = series * weights
            total_mwh = float(weighted.sum())
            if not _math.isfinite(total_mwh):
                continue
            pp = _per_period_groupby(weighted, sns, is_multi)
            carrier = str(gens.at[g, "carrier"]) if "carrier" in gens.columns else "unknown"
            _accum_carrier(carrier.lower(), total_mwh, pp)
            # OPEX from marginal_cost × dispatch. Carrier-level scalar; PyPSA
            # also supports generators_t.marginal_cost for time-varying costs
            # but solver_service restores the user-typed value after solve,
            # so the static column is what the LP saw on average.
            try:
                mc = float(gens.at[g, "marginal_cost"])
            except (TypeError, ValueError):
                mc = 0.0
            if _math.isfinite(mc) and mc > 0:
                opex_t = series * mc * cost_weights
                opex_bucket["total"] += float(opex_t.sum()) / 1e6
                for p, v in _per_period_groupby(opex_t, sns, is_multi).items():
                    opex_bucket["by_period"][p] = opex_bucket["by_period"].get(p, 0.0) + v / 1e6

    # Storage-unit dispatch — only the discharge half goes into the
    # energy-mix; charging is internal cycling, double-counting it would
    # inflate the energy-mix chart. We use `storage_units_t.p` (signed,
    # grid-side, post-efficiency) clipped to non-negative — same convention
    # as the Results panel's `/results/storage_dispatch` endpoint, so the
    # Compare-tab number matches what the user already sees there. (PyPSA's
    # `p_dispatch` is gross internal discharge before the efficiency factor
    # — summing it overstates dispatch by 1/eta_d, ~25 % on a typical
    # 0.78-eta round-trip battery.)
    sus = n.storage_units
    p_storage = getattr(n.storage_units_t, "p", None) if hasattr(n, "storage_units_t") else None
    if p_storage is not None and not p_storage.empty:
        for s in p_storage.columns:
            if s not in sus.index:
                continue
            try:
                series = p_storage[s].reindex(sns).fillna(0.0).clip(lower=0).astype(float)
            except Exception:
                continue
            weighted = series * weights
            total_mwh = float(weighted.sum())
            if not _math.isfinite(total_mwh) or total_mwh < 1e-9:
                continue
            pp = _per_period_groupby(weighted, sns, is_multi)
            carrier = str(sus.at[s, "carrier"]) if "carrier" in sus.columns else "storage"
            _accum_carrier(carrier.lower(), total_mwh, pp)

    # Storage equivalent-cycles per carrier — fleet-level.
    # Cycle = (gross throughput) / (2 × total energy capacity).
    # Throughput sums |p| over all snapshots × snapshot_weight (NOT ×
    # ipw.years — we want cycles-per-year, not cycles-over-horizon).
    storage_cycles: dict = {}
    if not sus.empty and p_storage is not None and not p_storage.empty:
        # Per-snapshot weight WITHOUT the investment_period_weightings.years
        # multiplier — `_build_snapshot_weights` includes years for
        # energy/cost totals, but cycles is intrinsically a per-year metric
        # and we don't want it scaled by horizon length.
        try:
            sw_only = n.snapshot_weightings.loc[sns, "objective"].astype(float)
        except Exception:
            sw_only = pd.Series(1.0, index=sns, dtype=float)
        # Carrier-level accumulators: throughput (MWh) and energy_cap (MWh).
        # Both numerator and denominator get period-split so per-period
        # cycles = period_throughput / (2 × period_energy_cap).
        throughput_by_carrier: dict = {}
        energy_cap_by_carrier: dict = {}
        for s in sus.index:
            try:
                p_nom_opt = float(sus.at[s, "p_nom_opt"]) if "p_nom_opt" in sus.columns else float(sus.at[s, "p_nom"])
                max_hours = float(sus.at[s, "max_hours"]) if "max_hours" in sus.columns else 0.0
            except (TypeError, ValueError):
                continue
            if not _math.isfinite(p_nom_opt) or p_nom_opt <= 1e-9:
                continue
            if not _math.isfinite(max_hours) or max_hours <= 0:
                continue
            energy_cap = p_nom_opt * max_hours  # MWh
            carrier = str(sus.at[s, "carrier"]) if "carrier" in sus.columns else "storage"
            ec_b = energy_cap_by_carrier.setdefault(carrier.lower(), {"total": 0.0, "by_period": {}})
            ec_b["total"] += energy_cap
            if is_multi:
                # Energy cap is constant per snapshot; the per-period split
                # uses the same value (the unit is active in every period
                # it appears in). Multiplying-by-time isn't appropriate —
                # the denominator in cycles is MWh of capacity, not MWh-yr.
                for p in periods:
                    ec_b["by_period"][str(p)] = ec_b["by_period"].get(str(p), 0.0) + energy_cap
            # Throughput: Σ |p| × snapshot_weight.
            if s not in p_storage.columns:
                continue
            try:
                series = p_storage[s].reindex(sns).fillna(0.0).abs().astype(float)
            except Exception:
                continue
            throughput_t = series * sw_only  # MWh per snapshot
            tp_b = throughput_by_carrier.setdefault(carrier.lower(), {"total": 0.0, "by_period": {}})
            tp_b["total"] += float(throughput_t.sum())
            for p, v in _per_period_groupby(throughput_t, sns, is_multi).items():
                tp_b["by_period"][p] = tp_b["by_period"].get(p, 0.0) + v
        # Cycles = throughput / (2 × energy_cap). Guard zero-cap.
        # Per-period: t_period / (2 × ec_period) for each period.
        # Horizon "total": MEAN across periods (not SUM) so a 3-year horizon
        # reports the per-year cycling rate, not 3× it. Previously this
        # computed `tp["total"] / (2 × ec["total"])` where tp["total"] was
        # summed across the horizon but ec["total"] was a single period's
        # energy cap → result was 3× the per-year value and DISAGREED with
        # the parallel `storage_cycling.cycles_by_carrier` view (which uses
        # mean) by exactly factor=n_periods. Compare View surfaces both →
        # contradictory numbers. Align here with mean-per-year so the two
        # views are consistent.
        for carrier, tp in throughput_by_carrier.items():
            ec = energy_cap_by_carrier.get(carrier) or {"total": 0.0, "by_period": {}}
            pp: dict[str, float] = {}
            for period_str, t_period in tp["by_period"].items():
                ec_period = ec["by_period"].get(period_str, 0.0)
                pp[period_str] = t_period / (2 * ec_period) if ec_period > 1e-9 else 0.0
            if pp:
                # Multi-period: mean cycles per year across the horizon.
                total_cycles = sum(pp.values()) / len(pp)
            elif ec["total"] > 1e-9:
                # Flat (single-period) fallback: tp/2*ec gives cycles directly.
                total_cycles = tp["total"] / (2 * ec["total"])
            else:
                total_cycles = 0.0
            storage_cycles[carrier] = {"total": total_cycles, "by_period": pp}

    # Load aggregation — magnitude only (load convention is positive
    # consumption on `p_set`; serving means dispatch matches load).
    loads = n.loads
    # Scaled demand (loads_t.p when present, else load_scalers×p_set) via the
    # shared helper, so this matches /results/loads and the compare-state
    # total_energy. `from_state=False` reads n's OWN frame (n is the loaded
    # bundle here), never the live network's cached snapshot.
    p_set_t = None
    try:
        from routers.simulation import _state as _sim_state3
        p_set_t = lp_scaled_load_frame(n, _sim_state3.get("solver_config"), from_state=False)
    except Exception:
        p_set_t = None
    if p_set_t is None:
        p_set_t = getattr(n.loads_t, "p_set", None) if hasattr(n, "loads_t") else None
    if p_set_t is not None and not p_set_t.empty:
        try:
            row_total = p_set_t.abs().sum(axis=1).reindex(sns).fillna(0.0).astype(float)
        except Exception:
            row_total = None
        if row_total is not None:
            weighted = row_total * weights
            load_bucket["total"] = float(weighted.sum()) / 1000.0
            for p, v in _per_period_groupby(weighted, sns, is_multi).items():
                load_bucket["by_period"][p] = v / 1000.0
    elif not loads.empty and "p_set" in loads.columns:
        # Static-only loads — apply a flat profile to keep the KPI honest.
        try:
            static_total = float(loads["p_set"].abs().sum())
        except Exception:
            static_total = 0.0
        if static_total > 0:
            # Treat as constant over the whole horizon; weights collapse
            # to nsnapshots × period_years.
            load_bucket["total"] = static_total * float(weights.sum()) / 1000.0

    return DispatchComparison(
        dispatch_gwh_by_carrier=_to_pv_dict(dispatch_by_carrier),
        opex_meur=_to_pv(opex_bucket),
        total_load_gwh=_to_pv(load_bucket),
        storage_cycles_by_carrier=_to_pv_dict(storage_cycles),
    )


def _compute_loading_summary(n, periods, is_multi, has_solve) -> LoadingComparison:
    """
    Per-line / per-transformer loading. Loading = ``|p0| / s_nom_opt``,
    averaged or peak-aggregated by period. Returns the entries sorted by
    horizon-wide peak loading (descending) so the frontend can render top-N
    of the most congested branches first.

    Operates on both lines AND transformers — bundled into a single list
    with ``is_transformer`` distinguishing them. Each entry carries three
    metrics: peak loading, snapshot-weighted mean loading, and weighted
    "binding hours" at ≥99 % of capacity (the canonical N-0 thermal
    proxy).
    """
    import math as _math


    out: list[LineLoadingEntry] = []
    if not has_solve:
        return LoadingComparison()

    # ENERGY/HOURS basis, not COST — binding_hours is a raw hours COUNT
    # (Task 15 / suspect S2: measured wrong on the "objective" basis, see
    # docs/superpowers/findings/2026-08-03-compare-tab-correctness.md §S2).
    # mean_loading is a weighted MEAN and is unaffected by this choice (the
    # weight series cancels under a uniform rescaling) — measured, not
    # merely assumed.
    weights = _build_snapshot_weights(n, "generators")
    sns = n.snapshots

    def _walk_branches(df, t_df, is_transformer: bool, is_link: bool = False, nom_field: str = "s_nom") -> None:
        if df is None or df.empty or t_df is None or t_df.empty:
            return
        # *_nom_opt is the LP-optimised rating; for non-extendable branches
        # it equals *_nom. For passive branches `s_nom` is the field;
        # Links use `p_nom`. Guard zero / NaN to avoid div-by-zero.
        nom_opt_col = f"{nom_field}_opt"
        nom_col = nom_opt_col if nom_opt_col in df.columns else nom_field
        carrier_col_present = is_link and "carrier" in df.columns
        for name in df.index:
            try:
                s_nom = float(df.at[name, nom_col])
            except (TypeError, ValueError):
                continue
            if not _math.isfinite(s_nom) or s_nom <= 1e-9:
                continue
            if name not in t_df.columns:
                continue
            link_carrier: str | None = None
            if carrier_col_present:
                try:
                    raw = df.at[name, "carrier"]
                    link_carrier = str(raw) if raw not in (None, "", float("nan")) else None
                except Exception:
                    link_carrier = None
            # IMPORTANT: do NOT fillna(0) here. A partial AC-PF run (rolling
            # horizon, or representative-week dispatch fix) populates p0 for
            # only a subset of snapshots; the rest are NaN. Treating NaN as
            # zero would *correctly* compute peak (max ignores zero baseline)
            # but *dramatically* understate the snapshot-weighted MEAN —
            # dividing the sum-of-real-values by the sum-of-all-weights
            # (26 280 h) instead of by the populated-only weights (e.g.
            # 168 h) → a 150× error. Use pandas' default skipna=True for
            # max / sum, and mask the weight series to the populated set
            # for the mean denominator.
            try:
                p0 = t_df[name].reindex(sns).astype(float)
            except Exception:
                continue
            loading = p0.abs() / s_nom  # NaN propagates through arithmetic
            present_mask = loading.notna()
            if not present_mask.any():
                # Whole branch was never written to. Skip — surfaces as a
                # missing entry in the comparison view instead of a row of
                # spurious zeros.
                continue

            # Horizon-wide aggregates over the populated subset.
            peak_total = float(loading.max(skipna=True))
            weights_present = weights.where(present_mask, other=0.0)
            wsum_total = float(weights_present.sum())
            if wsum_total > 0:
                mean_total = float((loading.fillna(0.0) * weights_present).sum() / wsum_total)
            else:
                mean_total = 0.0
            binding_total = float(
                (loading.fillna(0.0) >= 0.99).astype(float).mul(weights_present).sum()
            )

            peak_pp: dict[str, float] = {}
            mean_pp: dict[str, float] = {}
            binding_pp: dict[str, float] = {}
            if is_multi:
                try:
                    period_lvl = sns.get_level_values(0)
                    by_period_loading = loading.groupby(period_lvl)
                    by_period_w_present = weights_present.groupby(period_lvl)
                    by_period_binding = (
                        (loading.fillna(0.0) >= 0.99).astype(float)
                        .mul(weights_present)
                        .groupby(period_lvl).sum()
                    )
                    for p in periods:
                        try:
                            group_load = by_period_loading.get_group(p)
                            group_w = by_period_w_present.get_group(p)
                        except KeyError:
                            continue
                        # Period-local peak — skipna ignores NaN. If the
                        # period is entirely NaN, peak is NaN (skip).
                        peak_val = group_load.max(skipna=True)
                        if peak_val is None or (isinstance(peak_val, float) and not _math.isfinite(peak_val)):
                            continue
                        peak_pp[str(p)] = float(peak_val)
                        wsum = float(group_w.sum())
                        mean_pp[str(p)] = (
                            float((group_load.fillna(0.0) * group_w).sum() / wsum)
                            if wsum > 0 else 0.0
                        )
                        try:
                            binding_pp[str(p)] = float(by_period_binding.loc[p])
                        except KeyError:
                            binding_pp[str(p)] = 0.0
                except Exception:
                    pass
            out.append(LineLoadingEntry(
                name=str(name),
                s_nom_opt=s_nom,
                is_transformer=is_transformer,
                is_link=is_link,
                carrier=link_carrier,
                peak_loading=CarrierPeriodValue(total=peak_total, by_period=peak_pp),
                mean_loading=CarrierPeriodValue(total=mean_total, by_period=mean_pp),
                binding_hours=CarrierPeriodValue(total=binding_total, by_period=binding_pp),
            ))

    _walk_branches(n.lines,        getattr(n.lines_t, "p0", None) if hasattr(n, "lines_t") else None,        False)
    _walk_branches(n.transformers, getattr(n.transformers_t, "p0", None) if hasattr(n, "transformers_t") else None, True)
    # Sector-coupling Links (heat pumps, electrolysers, H2 pipelines, P2X)
    # — same loading metric on |p0|/p_nom_opt. Carrier exposes the energy
    # type so the frontend can group / filter by carrier in the loading tab
    # just like in dispatch / capacity tabs. Multi-port Links (bus2 set)
    # still report a single p_nom; the bus2 reverse flow is implicit via
    # efficiency2 and not separately rate-limited at the LP layer.
    _walk_branches(n.links,        getattr(n.links_t, "p0", None) if hasattr(n, "links_t") else None,        False, is_link=True, nom_field="p_nom")
    # Worst-first ordering across all branch types.
    out.sort(key=lambda e: e.peak_loading.total, reverse=True)
    return LoadingComparison(lines=out)


def _compute_prices_summary(n, periods, is_multi, has_solve) -> PricesComparison:
    """
    Marginal price view. Builds:

      * A sampled duration curve (101 points) over the flattened
        (bus × snapshot) marginal-price matrix. Sorted descending so
        index 0 is the peak-price point and index 100 the trough.
      * Per-period mean / median / p90 (top-decile, peak-hour proxy).

    Two design choices worth recording:

      1. Snapshot weighting is INCLUDED for the per-period mean/median/p90
         via ``np.repeat`` weights, but NOT for the duration-curve sampling
         (sampling 101 quantiles after a weighted sort gets fiddly fast,
         and the chart's purpose is shape comparison — small per-hour
         weight differences don't change the visual story).

      2. Bus-axis is also flattened — we treat every (bus, snapshot) pair
         as an independent observation. For locational-pricing networks
         that's exactly the spread the user wants to see; for single-bus
         networks each "row" of the matrix is identical and the curve
         degenerates to the snapshot price profile.
    """

    if not has_solve:
        return PricesComparison()
    # Curtailment-cost-corrected duals (same merit-order correction the Results
    # Prices tab applies), so the duration curve / mean / max / min match.
    # from_state=False → read this bundle's own buses_t, not the live snapshot.
    try:
        p_t = corrected_marginal_prices(n, from_state=False)
    except Exception:
        p_t = getattr(n.buses_t, "marginal_price", None) if hasattr(n, "buses_t") else None
    if p_t is None or p_t.empty:
        return PricesComparison()

    weights = _build_snapshot_weights(n)
    sns = n.snapshots

    # Flatten to 1-D array, dropping NaN/inf. For a 4×26k network this is
    # ~100k floats — fine for in-memory percentile.
    vals_flat = np.asarray(p_t.values).reshape(-1)
    finite_mask = np.isfinite(vals_flat)
    vals_clean = vals_flat[finite_mask]
    if vals_clean.size == 0:
        return PricesComparison(bus_count=len(p_t.columns))

    # Per-period stats setup. Replicate snapshot weight across the bus
    # dimension so the mean treats each bus-snapshot cell as one weighted
    # observation.
    n_buses = p_t.shape[1]
    w_arr = np.asarray(weights.values).reshape(-1, 1)  # snapshots × 1
    w_full = np.broadcast_to(w_arr, p_t.shape).reshape(-1)[finite_mask]

    # Weighted duration curve: sort descending, accumulate weights, sample
    # at 101 cumulative-weight points. For multi-period horizons the
    # weights already include `investment_period_weightings.years` via
    # `_build_snapshot_weights`, so hours from longer periods correctly
    # contribute more density to the curve. The previous implementation
    # used an unweighted np.sort + linear index sampling which under-
    # represented hours from periods with high `ipw.years` (e.g., a 5-year
    # period was treated equal to a 1-year period in the curve shape).
    order_desc = np.argsort(vals_clean)[::-1]
    sorted_desc = vals_clean[order_desc]
    sorted_weights_desc = w_full[order_desc]
    cum_w = np.cumsum(sorted_weights_desc)
    total_w = float(cum_w[-1]) if cum_w.size > 0 else 0.0
    if total_w > 0 and sorted_desc.size > 0:
        targets = np.linspace(0, total_w, 101)
        sample_idx = np.clip(np.searchsorted(cum_w, targets), 0, sorted_desc.size - 1)
        duration_curve = [float(x) for x in sorted_desc[sample_idx]]
    else:
        duration_curve = []

    def _stats(values, weights_arr):
        if values.size == 0 or weights_arr.sum() <= 0:
            return 0.0, 0.0, 0.0
        # Weighted mean (analytic), median + p90 from sorted + cumulative weight.
        mean = float((values * weights_arr).sum() / weights_arr.sum())
        order = np.argsort(values)
        sv = values[order]
        sw = weights_arr[order]
        cum = np.cumsum(sw)
        total = cum[-1]
        def _quantile(q):
            target = q * total
            idx = int(np.searchsorted(cum, target))
            idx = min(max(idx, 0), sv.size - 1)
            return float(sv[idx])
        return mean, _quantile(0.5), _quantile(0.9)

    mean_total, median_total, p90_total = _stats(vals_clean, w_full)
    mean_pp: dict[str, float] = {}
    median_pp: dict[str, float] = {}
    p90_pp: dict[str, float] = {}

    if is_multi:
        # Mask per period — repeat snapshot's period across the bus axis.
        period_lvl = np.asarray(sns.get_level_values(0))
        period_full = np.broadcast_to(period_lvl.reshape(-1, 1), p_t.shape).reshape(-1)[finite_mask]
        for p in periods:
            mask = period_full == p
            if not mask.any():
                continue
            mp, mdp, p9p = _stats(vals_clean[mask], w_full[mask])
            ps = str(p)
            mean_pp[ps] = mp
            median_pp[ps] = mdp
            p90_pp[ps] = p9p

    # Per-bus-carrier price stats. Group bus columns by their `carrier`
    # attribute, then compute the same weighted mean/median/p90 within
    # each group. Sector-coupled networks where electrical / H2 / heat
    # buses carry very different price regimes get a per-carrier table
    # row in the Compare View. Falls back gracefully to empty dict for
    # networks without a `buses.carrier` column.
    by_carrier_stats: dict[str, CarrierPriceStats] = {}
    buses_df = getattr(n, "buses", None)
    if (buses_df is not None and not buses_df.empty
            and "carrier" in buses_df.columns):
        carrier_for_bus: dict[str, str] = {}
        for bus_name in p_t.columns:
            if bus_name in buses_df.index:
                try:
                    raw_c = buses_df.at[bus_name, "carrier"]
                    c = str(raw_c).lower() if raw_c not in (None, "") else "unspecified"
                except Exception:
                    c = "unspecified"
                carrier_for_bus[bus_name] = c
        carrier_buses: dict[str, list[str]] = {}
        for bus_name, c in carrier_for_bus.items():
            carrier_buses.setdefault(c, []).append(bus_name)
        period_lvl_full = None
        if is_multi:
            try:
                period_lvl_full = np.asarray(sns.get_level_values(0))
            except Exception:
                period_lvl_full = None
        for c, bus_list in carrier_buses.items():
            if not bus_list:
                continue
            try:
                sub = p_t[bus_list].values
                sub_flat = sub.reshape(-1)
                sub_finite = np.isfinite(sub_flat)
                sub_clean = sub_flat[sub_finite]
                if sub_clean.size == 0:
                    continue
                sub_w_arr = np.broadcast_to(w_arr, sub.shape).reshape(-1)[sub_finite]
                cm, cmd, cp90 = _stats(sub_clean, sub_w_arr)
                cmean_pp: dict[str, float] = {}
                cmedian_pp: dict[str, float] = {}
                cp90_pp: dict[str, float] = {}
                if is_multi and period_lvl_full is not None:
                    sub_period = np.broadcast_to(
                        period_lvl_full.reshape(-1, 1), sub.shape
                    ).reshape(-1)[sub_finite]
                    for p in periods:
                        pmask = sub_period == p
                        if not pmask.any():
                            continue
                        mp, mdp, p9p = _stats(sub_clean[pmask], sub_w_arr[pmask])
                        ps = str(p)
                        cmean_pp[ps] = mp
                        cmedian_pp[ps] = mdp
                        cp90_pp[ps] = p9p
                by_carrier_stats[c] = CarrierPriceStats(
                    bus_count=len(bus_list),
                    mean_price=CarrierPeriodValue(total=cm, by_period=cmean_pp),
                    median_price=CarrierPeriodValue(total=cmd, by_period=cmedian_pp),
                    p90_price=CarrierPeriodValue(total=cp90, by_period=cp90_pp),
                )
            except Exception:
                pass

    # Extreme tail markers — the duration curve's first/last points can be
    # extreme single-bus-snapshot LP duals (constraint scarcity moments) that
    # dominate the y-axis and visually flatten the rest. Expose max/min
    # separately so the Compare View can show them as tooltips and optionally
    # clip the chart y-axis to a sensible band (e.g. p99.5).
    if sorted_desc.size > 0:
        try:
            max_price = float(sorted_desc[0])
            min_price = float(sorted_desc[-1])
        except (IndexError, TypeError, ValueError):
            max_price, min_price = 0.0, 0.0
    else:
        max_price, min_price = 0.0, 0.0
    return PricesComparison(
        duration_curve=duration_curve,
        mean_price=CarrierPeriodValue(total=mean_total, by_period=mean_pp),
        median_price=CarrierPeriodValue(total=median_total, by_period=median_pp),
        p90_price=CarrierPeriodValue(total=p90_total, by_period=p90_pp),
        max_price=max_price,
        min_price=min_price,
        bus_count=n_buses,
        by_carrier_stats=by_carrier_stats,
    )


def _co2_intensity_map(n) -> dict[str, float]:
    """
    Return ``carrier_name_lower → co2_emissions (tCO2/MWh_th)`` for every
    carrier whose ``co2_emissions`` is finite.

    Thin wrapper kept for this module's call sites; the implementation moved
    to `services.economics.co2_intensity_map` so the Results tab and the
    Compare rail read the same carrier intensities.
    """
    return economics.co2_intensity_map(n)


def _compute_emissions_summary(n, periods, is_multi, has_solve) -> EmissionsComparison:
    """
    Sum CO2 emissions per (carrier, period) using the same kg/MWh model
    the LP optimised against: ``dispatch × carrier.co2_emissions /
    efficiency × snapshot_weight × period_year``. Output in kilotons.

    Intensity = total kt × 1000 / total dispatch GWh × 1000 = kg/MWh.
    """
    import math as _math
    if not has_solve:
        return EmissionsComparison()
    # ENERGY basis (generators): emissions = dispatch × factor, and the dispatch
    # denominator must match _compute_dispatch_summary (also generators) so the
    # two Compare views — and the Results /emissions endpoint — reconcile. The
    # whole function is energy; there is no cost term here.
    weights = _build_snapshot_weights(n, "generators")
    sns = n.snapshots
    co2_map = _co2_intensity_map(n)
    if not co2_map:
        return EmissionsComparison()

    total_bucket = {"total": 0.0, "by_period": {}}
    by_carrier_kt: dict = {}
    # Total dispatch is needed for the intensity denominator — match what
    # _compute_dispatch_summary reports so the two views stay reconciled.
    total_dispatch_mwh = {"total": 0.0, "by_period": {}}

    gens = n.generators
    p_t = getattr(n.generators_t, "p", None) if hasattr(n, "generators_t") else None
    if p_t is None or p_t.empty:
        return EmissionsComparison()

    def _accum(bucket, mwh_total, mwh_pp):
        bucket["total"] += mwh_total
        for p, v in mwh_pp.items():
            bucket["by_period"][p] = bucket["by_period"].get(p, 0.0) + v

    for g in p_t.columns:
        if g not in gens.index:
            continue
        try:
            series = p_t[g].reindex(sns).fillna(0.0).astype(float)
        except Exception:
            continue
        weighted = series * weights
        mwh_total = float(weighted.sum())
        if not _math.isfinite(mwh_total):
            continue
        mwh_pp = _per_period_groupby(weighted, sns, is_multi)
        _accum(total_dispatch_mwh, mwh_total, mwh_pp)

        carrier = str(gens.at[g, "carrier"]).lower() if "carrier" in gens.columns else ""
        intensity = co2_map.get(carrier, 0.0)
        if intensity <= 0:
            continue
        eff = float(gens.at[g, "efficiency"]) if "efficiency" in gens.columns else 1.0
        if not _math.isfinite(eff) or eff <= 0:
            eff = 1.0
        out_intensity = intensity / eff  # tCO2 per MWh_elec
        # CO2 mass in tons → /1000 for kt.
        co2_total_kt = mwh_total * out_intensity / 1000.0
        if co2_total_kt < 1e-9:
            continue
        co2_pp_kt = {p: v * out_intensity / 1000.0 for p, v in mwh_pp.items()}

        total_bucket["total"] += co2_total_kt
        for p, v in co2_pp_kt.items():
            total_bucket["by_period"][p] = total_bucket["by_period"].get(p, 0.0) + v
        b = by_carrier_kt.setdefault(carrier, {"total": 0.0, "by_period": {}})
        b["total"] += co2_total_kt
        for p, v in co2_pp_kt.items():
            b["by_period"][p] = b["by_period"].get(p, 0.0) + v

    # Intensity = total kt × 1e6 (kt→kg) ÷ total MWh
    def _intensity(mwh, kt):
        if mwh <= 1e-9 or kt < 0:
            return 0.0
        return kt * 1e6 / mwh

    intensity_total = _intensity(total_dispatch_mwh["total"], total_bucket["total"])
    intensity_pp: dict[str, float] = {}
    for p, mwh in total_dispatch_mwh["by_period"].items():
        intensity_pp[p] = _intensity(mwh, total_bucket["by_period"].get(p, 0.0))

    return EmissionsComparison(
        total_kt=_to_pv(total_bucket),
        by_carrier_kt=_to_pv_dict(by_carrier_kt),
        intensity_kg_per_mwh=CarrierPeriodValue(total=intensity_total, by_period=intensity_pp),
    )


def _compute_economics_summary(n, periods, is_multi, has_solve, prices_from_state: bool = True) -> EconomicsComparison:
    """
    Per-carrier economic summary for the Economics comparison tab.

    For each generator/storage_unit in the network, accumulate four
    quantities into a per-carrier bucket:

      * dispatch (MWh)    — Σ p × generators_weight × period_year (ENERGY basis)
      * revenue (€)       — Σ p × bus_marginal_price × objective_weight
      * opex   (€)        — Σ p × marginal_cost × objective_weight
      * capex  (€/yr)     — p_nom_opt × annuitised_capital_cost

    Energy (dispatch_mwh) is weighted by ``snapshot_weightings.generators``;
    cost quantities by ``snapshot_weightings.objective``. See the weight setup
    below — this keeps the per-carrier GWh equal to the Compare Dispatch tab and
    n.statistics(), while LCOE's numerator stays on the cost basis.

    LCOE per carrier is then computed POST-roll-up:
      ``LCOE = (Σ capex + Σ opex) / Σ dispatch_MWh``

    A few subtle choices:

      * Storage discharge counts as dispatch for the revenue line so the
        battery's arbitrage profit shows up (and matches what dispatch's
        energy-mix chart already reports). Charging is excluded — it would
        artificially deflate net revenue.
      * Capex is attributed to its build_year period (matching CAPACITY's
        rule). Pre-existing assets (build_year outside the horizon) still
        contribute to the aggregate but not to a specific period.
      * Per-period LCOE uses the period's capex + opex over the period's
        dispatch — i.e., each year stands on its own, no cross-period
        amortization.
    """
    import math as _math

    import pandas as pd
    if not has_solve:
        return EconomicsComparison()

    # COST quantities (opex, revenue, curtailment, lost-load, storage charge
    # cost) use the `objective` weighting column; ENERGY quantities
    # (dispatch_mwh — the LCOE denominator) use `generators`, matching PyPSA's
    # n.statistics() and the Compare Dispatch tab (_compute_dispatch_summary).
    # When the two columns are equal (the common single-weight case) these are
    # identical; they diverge only under representative-week weighting, where
    # energy must follow `generators` or the Economics-tab GWh disagree with the
    # Dispatch tab for the same carrier.
    weights = _build_snapshot_weights(n)                       # objective → COST
    energy_weights = _build_snapshot_weights(n, "generators")  # generators → ENERGY
    sns = n.snapshots
    # Per-snapshot bus marginal price — needed for revenue. Average across
    # buses is wrong for locational pricing; use each generator's home bus.
    # Curtailment-cost-corrected bus marginal prices — the SAME merit-order
    # correction the per-asset Results tab (asset_economics) applies, via a
    # shared helper. Without it, storage charge-cost / revenue here priced
    # against the raw (subsidy-distorted) dual, so the per-carrier LCOE diverged
    # from the per-asset LCOS by the curtailment-subsidy term (battery 148 vs
    # 153 EUR/MWh). Lazy import avoids the simulation<->projects import cycle.
    # `prices_from_state`: True for the LIVE Results endpoint (read the LP-stage
    # `_state` snapshot, matching asset_economics); False for a loaded Compare
    # bundle (read temp_n's own duals, never the live network's cached snapshot).
    try:
        bus_prices = corrected_marginal_prices(n, from_state=prices_from_state)
    except Exception:
        bus_prices = getattr(n.buses_t, "marginal_price", None) if hasattr(n, "buses_t") else None

    # Per-asset annuitised capital_cost, resolved once via the SAME
    # `periodized_capital_costs` path `asset_economics` / `cost_breakdown` /
    # `asset_costs` use — see `_periodized_lookup`'s docstring.
    pcc = _periodized_lookup(n)

    # Investment-period weightings — years × period. Default 1.0 each. Used
    # to scale annuitised CAPEX commitment from a single-year cost to the
    # period's total commitment (annual_cost × years_in_period).
    ipw_years: dict = period_utils.period_years_map(n)

    # Same vintage_results dict that the capacity summary consumes — gives
    # us per-period p_nom_opt attribution that the collapsed dataframe row
    # loses after the solver restores vintages to their parent. Without
    # this, CAPEX bookkeeping would lump every asset's annual cost under
    # the parent's (often pre-horizon) build_year — making per-period
    # CAPEX columns zero and per-period LCOE degenerate to marginal_cost.
    vintage_root = (n.meta or {}).get("vintage_results", {})
    if not isinstance(vintage_root, dict):
        vintage_root = {}

    def _vintage_handled(cls_name: str, parent_name: str) -> bool:
        block = vintage_root.get(cls_name)
        return isinstance(block, dict) and parent_name in block

    by_carrier: dict[str, dict] = {}

    def _bucket(carrier: str) -> dict:
        if carrier not in by_carrier:
            by_carrier[carrier] = {
                "dispatch_mwh":  {"total": 0.0, "by_period": {}},
                "revenue_eur":   {"total": 0.0, "by_period": {}},
                # `opex_eur` is the headline TOTAL (gen + charge + curtailment
                # + lost_load). The split buckets below let the user see WHAT
                # the OPEX consists of per carrier.
                "opex_eur":      {"total": 0.0, "by_period": {}},
                "gen_cost_eur":            {"total": 0.0, "by_period": {}},
                "storage_charge_cost_eur": {"total": 0.0, "by_period": {}},
                "curtailment_cost_eur":    {"total": 0.0, "by_period": {}},
                "lost_load_cost_eur":      {"total": 0.0, "by_period": {}},
                "capex_eur":     {"total": 0.0, "by_period": {}},
            }
        return by_carrier[carrier]

    def _accum(bucket: dict, total_val: float, pp_vals: dict[str, float]) -> None:
        bucket["total"] += total_val
        for p, v in pp_vals.items():
            bucket["by_period"][p] = bucket["by_period"].get(p, 0.0) + v

    def _capex_commitment(cc_per_mw_yr: float, p_nom: float,
                          build_year, lifetime) -> tuple[float, dict[str, float]]:
        """
        Annuitised CAPEX commitment across the horizon — FULL-HORIZON basis.

        Returns ``(total_horizon_eur, by_period_eur)`` where:

          * ``total``       = annual_cost × Σ ipw.years over ALL horizon periods
          * ``by_period[P]`` = annual_cost × ipw.years[P]  (every period)

        This matches PyPSA's ``n.statistics()`` / ``/results/cost_breakdown``,
        the per-asset ``/results/asset_economics`` tab, and the Compare
        Capacity tab (``_compute_total_annuitised_capex``) — so the Economics
        comparison reconciles with all of them.

        Previously this gated each period on build-year "active" years
        (``build_year ≤ P < build_year + lifetime``), which UNDER-counted capex
        for capacity built in later vintages: a battery whose 477 MW is mostly
        built in 2027-28 accrued only ~1.2 years of annuity (€49.5 M) instead
        of the authoritative full-horizon €122.9 M reported everywhere else.
        ``build_year`` / ``lifetime`` are kept as parameters (the vintage walk
        passes them) but no longer reduce the commitment.

        Flat (single-period) networks short-circuit to one annual cost on
        the total bucket; ``by_period`` is empty (matches every other
        per-period field's flat-network behaviour).
        """
        if cc_per_mw_yr <= 0 or p_nom <= 1e-9 or not _math.isfinite(p_nom):
            return 0.0, {}
        annual = cc_per_mw_yr * p_nom  # €/yr
        if not is_multi or not periods:
            return annual, {}
        total = 0.0
        pp: dict[str, float] = {}
        for P in periods:
            years_in_P = period_utils.years_for_period(ipw_years, P)
            commitment = annual * years_in_P
            pp[str(P)] = commitment
            total += commitment
        return total, pp

    def _walk_capex_vintage(cls_name: str, df) -> None:
        """
        Per-vintage CAPEX attribution. Pulls each vintage row's
        ``p_nom_opt`` and ``build_year`` from ``n.meta["vintage_results"]``
        — the only place the per-period breakdown survives the post-solve
        collapse onto the parent.
        """
        block = vintage_root.get(cls_name)
        if not isinstance(block, dict) or df is None or df.empty:
            return
        for parent_name, payload in block.items():
            if parent_name not in df.index:
                continue
            row = df.loc[parent_name]
            carrier = str(row.get("carrier", "unknown") or "unknown").lower()
            cc = _safe_capital_cost(row, pcc, _CLS_TO_ATTR[cls_name])
            if cc <= 0:
                continue
            lt = row.get("lifetime") if "lifetime" in df.columns else None
            # Pre-existing capacity that came pre-build — annuitise under
            # the parent's build_year, treated as always-active when that
            # year is outside the horizon.
            try:
                ini = float(payload.get("initial_capacity") or 0)
            except (TypeError, ValueError):
                ini = 0.0
            if _math.isfinite(ini) and ini > 0:
                total, pp = _capex_commitment(cc, ini, row.get("build_year"), lt)
                _accum(_bucket(carrier)["capex_eur"], total, pp)
            for entry in payload.get("periods") or []:
                try:
                    opt = float(entry.get("p_nom_opt") or 0)
                except (TypeError, ValueError):
                    continue
                if not _math.isfinite(opt) or opt <= 1e-9:
                    continue
                total, pp = _capex_commitment(cc, opt, entry.get("build_year"), lt)
                _accum(_bucket(carrier)["capex_eur"], total, pp)

    def _walk_capex_plain(cls_name: str, df, nom: str) -> None:
        """
        Per-asset CAPEX for assets WITHOUT a vintage_results entry —
        uses the dataframe row's full ``p_nom_opt`` (which equals
        ``p_nom`` for non-extendable assets, so commitment = annual cost
        of the existing fleet).
        """
        if df is None or df.empty:
            return
        opt_col = f"{nom}_opt"
        if opt_col not in df.columns:
            return
        for asset_name in df.index:
            if _vintage_handled(cls_name, asset_name):
                continue
            row = df.loc[asset_name]
            carrier = str(row.get("carrier", "unknown") or "unknown").lower()
            cc = _safe_capital_cost(row, pcc, _CLS_TO_ATTR[cls_name])
            if cc <= 0:
                continue
            try:
                opt = float(row[opt_col])
            except (TypeError, ValueError):
                continue
            if not _math.isfinite(opt) or opt <= 1e-9:
                continue
            lt = row.get("lifetime") if "lifetime" in df.columns else None
            total, pp = _capex_commitment(cc, opt, row.get("build_year"), lt)
            _accum(_bucket(carrier)["capex_eur"], total, pp)

    def _walk_dispatch_side(df, t_p_df) -> None:
        """
        Per-asset DISPATCH + OPEX + REVENUE. CAPEX is handled separately
        above so the active-period attribution can be vintage-aware.
        """
        if df is None or df.empty or t_p_df is None or t_p_df.empty:
            return
        for asset_name in df.index:
            if asset_name not in t_p_df.columns:
                continue
            row = df.loc[asset_name]
            carrier = str(row.get("carrier", "unknown") or "unknown").lower()
            b = _bucket(carrier)
            try:
                series = t_p_df[asset_name].reindex(sns).fillna(0.0).astype(float)
            except Exception:
                continue
            # Storage: split the signed series into discharge (positive) and
            # charge (negative magnitude). The dispatch + revenue series use
            # discharge only — matching the dispatch summary's choice and
            # `/results/storage_dispatch`. The CHARGE COST (= bus_price ×
            # |charge| × weight) is added to opex_eur so the per-carrier LCOE
            # reflects what the storage operator actually pays for energy.
            # Without this, LCOE for storage under-counts the round-trip cost
            # → on a network with negative bus prices during charging hours
            # (solar curtailment) the gap with per-asset LCOS reaches several
            # €/MWh (user-flagged: Battery 1 showed 19.6 €/MWh here vs 16.4
            # in asset_economics; the 3.2 difference was exactly the missing
            # charge_cost term).
            is_storage_carrier = df is n.storage_units
            if is_storage_carrier:
                discharge_series = series.clip(lower=0)
                charge_series = (-series).clip(lower=0)
                weighted = discharge_series * energy_weights
            else:
                discharge_series = series
                charge_series = None
                weighted = series * energy_weights
            mwh_total = float(weighted.sum())
            if not _math.isfinite(mwh_total):
                continue
            mwh_pp = _per_period_groupby(weighted, sns, is_multi)
            _accum(b["dispatch_mwh"], mwh_total, mwh_pp)

            try:
                mc = float(row["marginal_cost"]) if "marginal_cost" in df.columns else 0.0
            except (TypeError, ValueError):
                mc = 0.0
            if _math.isfinite(mc) and mc > 0:
                opex_t = discharge_series * mc * weights
                opex_total = float(opex_t.sum())
                opex_pp = _per_period_groupby(opex_t, sns, is_multi)
                # Both the headline opex AND the gen_cost split bucket get
                # the VOM contribution. For storage_units this is the
                # discharge VOM only; the charge cost has its own bucket below.
                _accum(b["opex_eur"], opex_total, opex_pp)
                _accum(b["gen_cost_eur"], opex_total, opex_pp)

            bus = row.get("bus") if "bus" in df.columns else None
            if bus_prices is not None and not bus_prices.empty and bus in bus_prices.columns:
                try:
                    bp_series = bus_prices[bus].reindex(sns).fillna(0.0).astype(float)
                    rev_t = discharge_series * bp_series * weights
                    rev_total = float(rev_t.sum())
                    rev_pp = _per_period_groupby(rev_t, sns, is_multi)
                    _accum(b["revenue_eur"], rev_total, rev_pp)
                    # Storage charge cost — split into its own bucket AND
                    # added to the total opex_eur so the LCOE accounting
                    # matches /api/results/asset_economics's per-asset LCOS.
                    if is_storage_carrier and charge_series is not None:
                        cc_t = charge_series * bp_series * weights
                        cc_total = float(cc_t.sum())
                        cc_pp = _per_period_groupby(cc_t, sns, is_multi)
                        _accum(b["opex_eur"], cc_total, cc_pp)
                        _accum(b["storage_charge_cost_eur"], cc_total, cc_pp)
                except Exception:
                    pass

    # CAPEX: vintage path first, then plain path for assets without vintages.
    _walk_capex_vintage("Generator",   n.generators)
    _walk_capex_vintage("StorageUnit", n.storage_units)
    _walk_capex_vintage("Store",       n.stores)
    _walk_capex_vintage("Link",        n.links)
    _walk_capex_plain("Generator",   n.generators,    "p_nom")
    _walk_capex_plain("StorageUnit", n.storage_units, "p_nom")
    _walk_capex_plain("Store",       n.stores,        "e_nom")
    _walk_capex_plain("Link",        n.links,         "p_nom")

    # DISPATCH/OPEX/REVENUE: storage uses `_t.p` (signed, grid-side) so the
    # GWh match the dispatch summary + `/results/storage_dispatch`. Stores
    # have no dispatch — capex_only above. Links use `_t.p0` (signed flow
    # bus0→bus1); their revenue calculation safely no-ops because Links
    # don't have a single "bus" column (the revenue lookup falls through).
    # Sector-coupling Links contribute CAPEX + OPEX + dispatch to their
    # carrier bucket (heat-pump-waste, H2, P2X, etc.) so the economics tab
    # surfaces them in the per-carrier rollup.
    _walk_dispatch_side(n.generators,    getattr(n.generators_t, "p", None) if hasattr(n, "generators_t") else None)
    _walk_dispatch_side(n.storage_units, getattr(n.storage_units_t, "p", None) if hasattr(n, "storage_units_t") else None)
    _walk_dispatch_side(n.links,         getattr(n.links_t, "p0", None) if hasattr(n, "links_t") else None)

    # ── Curtailment cost per carrier ────────────────────────────────────────
    # For each renewable generator with `curtailment_cost > 0`, the LP pays
    # `cost × Σ_t (p_max_pu × p_nom_opt − p) × weights` for spilled energy.
    # Accumulated into the GENERATOR'S carrier bucket (so a solar generator's
    # curtailment penalty appears under "solar" carrier OPEX). Mirrors the
    # curtailment wrapper logic in solver_service so the displayed cost
    # matches what the LP actually paid.
    if "curtailment_cost" in n.generators.columns and hasattr(n, "generators_t"):
        p_t_curt = getattr(n.generators_t, "p", None)
        p_max_pu_t = getattr(n.generators_t, "p_max_pu", None)
        if p_t_curt is not None and not p_t_curt.empty:
            for g_name in n.generators.index:
                try:
                    cc = float(n.generators.at[g_name, "curtailment_cost"] or 0.0)
                except (TypeError, ValueError):
                    cc = 0.0
                if not _math.isfinite(cc) or cc <= 0:
                    continue
                if g_name not in p_t_curt.columns:
                    continue
                try:
                    p_series = p_t_curt[g_name].reindex(sns).fillna(0.0).astype(float)
                    p_nom_opt = float(n.generators.at[g_name, "p_nom_opt"]) if "p_nom_opt" in n.generators.columns else 0.0
                    if not _math.isfinite(p_nom_opt) or p_nom_opt <= 1e-9:
                        continue
                    # Effective max per snapshot — p_max_pu × p_nom_opt.
                    if p_max_pu_t is not None and g_name in p_max_pu_t.columns:
                        pmp_series = p_max_pu_t[g_name].reindex(sns).fillna(1.0).astype(float) * p_nom_opt
                    else:
                        static_pmp = float(n.generators.at[g_name, "p_max_pu"]) if "p_max_pu" in n.generators.columns else 1.0
                        pmp_series = pd.Series(static_pmp * p_nom_opt, index=sns, dtype=float)
                    curt_amt = (pmp_series - p_series).clip(lower=0)
                    curt_cost_t = curt_amt * cc * weights
                    curt_cost_total = float(curt_cost_t.sum())
                    if not _math.isfinite(curt_cost_total) or abs(curt_cost_total) < 1e-6:
                        continue
                    curt_cost_pp = _per_period_groupby(curt_cost_t, sns, is_multi)
                    carrier_g = str(n.generators.at[g_name, "carrier"] or "unknown").lower()
                    b = _bucket(carrier_g)
                    _accum(b["opex_eur"], curt_cost_total, curt_cost_pp)
                    _accum(b["curtailment_cost_eur"], curt_cost_total, curt_cost_pp)
                except Exception:
                    continue

    # ── Lost-load cost per carrier (load-bearing bus → its carrier) ─────────
    # VOLL slack dispatch lives in n.meta.last_lost_load (captured by the
    # solver service). For each bus with non-zero slack, look up its carrier
    # and accumulate (slack × VOLL) into the carrier's opex bucket + the
    # dedicated lost_load_cost_eur split. Skipped silently when the
    # capture is absent (network solved without VOLL).
    try:
        cap = (n.meta or {}).get("last_lost_load") if hasattr(n, "meta") else None
    except Exception:
        cap = None
    if isinstance(cap, dict):
        ll_df = cap.get("lost_load_t")
        ll_total_mwh = float(cap.get("lost_load_total_mwh", 0.0) or 0.0)
        ll_total_cost = float(cap.get("lost_load_cost_eur", 0.0) or 0.0)
        voll = (ll_total_cost / ll_total_mwh) if ll_total_mwh > 1e-9 else 0.0
        if voll > 0 and ll_df is not None and not getattr(ll_df, "empty", True):
            try:
                ll_aligned = ll_df.reindex(sns).fillna(0.0).astype(float)
                ll_weighted = ll_aligned.mul(weights, axis=0)
                # Bus → carrier lookup.
                bus_carrier_map: dict = {}
                if "carrier" in n.buses.columns:
                    for b_name in n.buses.index:
                        bus_carrier_map[b_name] = str(n.buses.at[b_name, "carrier"] or "unknown").lower()
                for bus_name in ll_weighted.columns:
                    bus_mwh = float(ll_weighted[bus_name].sum())
                    if not _math.isfinite(bus_mwh) or abs(bus_mwh) < 1e-6:
                        continue
                    bus_cost = bus_mwh * voll
                    bus_cost_pp = _per_period_groupby(ll_weighted[bus_name] * voll, sns, is_multi)
                    carrier_b = bus_carrier_map.get(bus_name, "unknown")
                    b = _bucket(carrier_b)
                    _accum(b["opex_eur"], bus_cost, bus_cost_pp)
                    _accum(b["lost_load_cost_eur"], bus_cost, bus_cost_pp)
            except Exception:
                pass

    # Convert €→M€ and MWh→GWh, then derive LCOE.
    def _to_meur(d):  return {"total": d["total"] / 1e6, "by_period": {k: v / 1e6 for k, v in d["by_period"].items()}}
    def _to_gwh(d):   return {"total": d["total"] / 1000.0, "by_period": {k: v / 1000.0 for k, v in d["by_period"].items()}}

    out: dict[str, CarrierEconomics] = {}
    for c, agg in by_carrier.items():
        rev_m = _to_meur(agg["revenue_eur"])
        opex_m = _to_meur(agg["opex_eur"])
        capex_m = _to_meur(agg["capex_eur"])
        disp_g = _to_gwh(agg["dispatch_mwh"])
        # New per-cost-type buckets — split of opex_eur so the frontend can
        # show users WHAT they paid for. Missing buckets fall through to a
        # zero CarrierPeriodValue.
        gen_cost_m = _to_meur(agg.get("gen_cost_eur") or {"total": 0.0, "by_period": {}})
        storage_charge_m = _to_meur(agg.get("storage_charge_cost_eur") or {"total": 0.0, "by_period": {}})
        curtailment_m = _to_meur(agg.get("curtailment_cost_eur") or {"total": 0.0, "by_period": {}})
        lost_load_m = _to_meur(agg.get("lost_load_cost_eur") or {"total": 0.0, "by_period": {}})
        # LCOE €/MWh = (capex_eur + CASH opex_eur) / dispatch_MWh, where CASH
        # opex EXCLUDES the curtailment + lost-load PENALTIES. Those are
        # non-cash modelling soft-constraints (curtailment_cost discourages
        # spilling renewables; VOLL prices unserved demand) — not real operating
        # expenses — so they don't belong in a levelised generation cost. This
        # matches the per-asset Results LCOE/LCOS (asset_economics uses
        # fixed + vom [+ charge] only), so e.g. "solar LCOE" reads identically
        # on the Results and Compare tabs. The penalties stay visible in
        # opex_meur (total) and their own split buckets — just not in the ratio.
        def _lcoe(capex_eur: float, opex_eur: float, dispatch_mwh: float) -> float:
            return (capex_eur + opex_eur) / dispatch_mwh if dispatch_mwh > 1e-9 else 0.0
        _curt_b = agg.get("curtailment_cost_eur") or {"total": 0.0, "by_period": {}}
        _ll_b = agg.get("lost_load_cost_eur") or {"total": 0.0, "by_period": {}}
        cash_opex_total = agg["opex_eur"]["total"] - _curt_b["total"] - _ll_b["total"]
        lcoe_total = _lcoe(agg["capex_eur"]["total"], cash_opex_total, agg["dispatch_mwh"]["total"])
        lcoe_pp: dict[str, float] = {}
        for p, mwh in agg["dispatch_mwh"]["by_period"].items():
            capex_pp = agg["capex_eur"]["by_period"].get(p, 0.0)
            cash_opex_pp = (agg["opex_eur"]["by_period"].get(p, 0.0)
                            - _curt_b["by_period"].get(p, 0.0)
                            - _ll_b["by_period"].get(p, 0.0))
            lcoe_pp[p] = _lcoe(capex_pp, cash_opex_pp, mwh)

        out[c] = CarrierEconomics(
            revenue_meur=CarrierPeriodValue(**rev_m),
            opex_meur=CarrierPeriodValue(**opex_m),
            gen_cost_meur=CarrierPeriodValue(**gen_cost_m),
            storage_charge_cost_meur=CarrierPeriodValue(**storage_charge_m),
            curtailment_cost_meur=CarrierPeriodValue(**curtailment_m),
            lost_load_cost_meur=CarrierPeriodValue(**lost_load_m),
            capex_meur=CarrierPeriodValue(**capex_m),
            dispatch_gwh=CarrierPeriodValue(**disp_g),
            lcoe_eur_per_mwh=CarrierPeriodValue(total=lcoe_total, by_period=lcoe_pp),
        )

    # Per-Link levelised cost. Closes BLOCKER from Compare View audit
    # 2026-05-25: users couldn't compare H2 plant / heat pump economics
    # across scenarios because only fleet rollup was exposed. For each
    # extendable Link with non-trivial dispatch, compute LCOH-style metric
    # = (CAPEX horizon-total + OPEX + input-electricity cost at bus0) /
    # (|p0| × efficiency, in MWh of bus1's carrier). Multi-port Links (bus2
    # set) only counts bus1 output; bus2's reverse flow contributes via
    # efficiency2 to the LP balance but isn't separately priced.
    per_asset_lcoh: list[AssetLCOHEntry] = []
    if n.links is not None and not n.links.empty:
        links_p0 = getattr(n.links_t, "p0", None) if hasattr(n, "links_t") else None
        if links_p0 is not None and not links_p0.empty:
            for link_name in n.links.index:
                if link_name not in links_p0.columns:
                    continue
                row = n.links.loc[link_name]
                try:
                    p_nom_opt = float(row.get("p_nom_opt", 0) or 0)
                except (TypeError, ValueError):
                    continue
                if not _math.isfinite(p_nom_opt) or p_nom_opt <= 1e-9:
                    continue
                try:
                    p0_series = links_p0[link_name].reindex(sns).fillna(0.0).astype(float)
                except Exception:
                    continue
                try:
                    eff = float(row.get("efficiency", 1.0) or 1.0)
                except (TypeError, ValueError):
                    eff = 1.0
                if not _math.isfinite(eff):
                    eff = 1.0
                output_t = p0_series.abs() * abs(eff) * weights
                output_mwh_total = float(output_t.sum())
                if output_mwh_total <= 1e-6:
                    continue
                output_mwh_pp = _per_period_groupby(output_t, sns, is_multi)

                try:
                    mc = float(row.get("marginal_cost", 0) or 0)
                except (TypeError, ValueError):
                    mc = 0.0
                if not _math.isfinite(mc) or mc <= 0:
                    opex_eur_total = 0.0
                    opex_eur_pp: dict[str, float] = {}
                else:
                    opex_t = p0_series.abs() * mc * weights
                    opex_eur_total = float(opex_t.sum())
                    opex_eur_pp = _per_period_groupby(opex_t, sns, is_multi)

                # Input-electricity cost at bus0 (only positive-direction flow
                # counts as input). For energy-flow Links bus0 is the source.
                input_eur_total = 0.0
                input_eur_pp: dict[str, float] = {}
                try:
                    bus0 = row.get("bus0")
                except Exception:
                    bus0 = None
                if (bus_prices is not None and not bus_prices.empty
                        and bus0 is not None and bus0 in bus_prices.columns):
                    try:
                        bp = bus_prices[bus0].reindex(sns).fillna(0.0).astype(float)
                        input_t = p0_series.clip(lower=0) * bp * weights
                        input_eur_total = float(input_t.sum())
                        input_eur_pp = _per_period_groupby(input_t, sns, is_multi)
                    except Exception:
                        pass

                # CAPEX: prefer vintage breakdown when present, else plain.
                cc = _safe_capital_cost(row, pcc, "links")
                lt_val = row.get("lifetime") if "lifetime" in n.links.columns else None
                capex_eur_total = 0.0
                capex_eur_pp: dict[str, float] = {}
                link_vintages = vintage_root.get("Link", {}).get(link_name) if isinstance(vintage_root.get("Link"), dict) else None
                if link_vintages and cc > 0:
                    try:
                        ini = float(link_vintages.get("initial_capacity") or 0)
                    except (TypeError, ValueError):
                        ini = 0.0
                    if _math.isfinite(ini) and ini > 0:
                        t, pp = _capex_commitment(cc, ini, row.get("build_year"), lt_val)
                        capex_eur_total += t
                        for k, v in pp.items():
                            capex_eur_pp[k] = capex_eur_pp.get(k, 0.0) + v
                    for entry in link_vintages.get("periods") or []:
                        try:
                            opt = float(entry.get("p_nom_opt") or 0)
                        except (TypeError, ValueError):
                            continue
                        if not _math.isfinite(opt) or opt <= 1e-9:
                            continue
                        t, pp = _capex_commitment(cc, opt, entry.get("build_year"), lt_val)
                        capex_eur_total += t
                        for k, v in pp.items():
                            capex_eur_pp[k] = capex_eur_pp.get(k, 0.0) + v
                elif cc > 0:
                    t, pp = _capex_commitment(cc, p_nom_opt, row.get("build_year"), lt_val)
                    capex_eur_total = t
                    capex_eur_pp = pp

                # LCOH = (CAPEX + OPEX + input_cost) / output_MWh
                total_cost_eur = capex_eur_total + opex_eur_total + input_eur_total
                lcoh_total = total_cost_eur / output_mwh_total if output_mwh_total > 1e-9 else 0.0
                lcoh_pp: dict[str, float] = {}
                for p_key, out_mwh in output_mwh_pp.items():
                    cx = capex_eur_pp.get(p_key, 0.0)
                    ox = opex_eur_pp.get(p_key, 0.0)
                    ix = input_eur_pp.get(p_key, 0.0)
                    lcoh_pp[p_key] = (cx + ox + ix) / out_mwh if out_mwh > 1e-9 else 0.0

                carrier_str = None
                try:
                    raw_c = row.get("carrier")
                    if raw_c not in (None, ""):
                        carrier_str = str(raw_c)
                except Exception:
                    pass

                per_asset_lcoh.append(AssetLCOHEntry(
                    name=str(link_name),
                    carrier=carrier_str,
                    p_nom_opt=p_nom_opt,
                    capex_meur=CarrierPeriodValue(
                        total=capex_eur_total / 1e6,
                        by_period={k: v / 1e6 for k, v in capex_eur_pp.items()},
                    ),
                    opex_meur=CarrierPeriodValue(
                        total=opex_eur_total / 1e6,
                        by_period={k: v / 1e6 for k, v in opex_eur_pp.items()},
                    ),
                    input_cost_meur=CarrierPeriodValue(
                        total=input_eur_total / 1e6,
                        by_period={k: v / 1e6 for k, v in input_eur_pp.items()},
                    ),
                    output_gwh=CarrierPeriodValue(
                        total=output_mwh_total / 1000.0,
                        by_period={k: v / 1000.0 for k, v in output_mwh_pp.items()},
                    ),
                    lcoh_eur_per_mwh=CarrierPeriodValue(total=lcoh_total, by_period=lcoh_pp),
                ))

        # Cheapest LCOH first — surface the most competitive Link on top.
        per_asset_lcoh.sort(key=lambda e: e.lcoh_eur_per_mwh.total)

    return EconomicsComparison(by_carrier=out, per_asset_lcoh=per_asset_lcoh)


def _compute_curtailment_summary(n, periods, is_multi, has_solve) -> CurtailmentComparison:
    """
    Per-carrier renewable curtailment in GWh + percent rate.

    Mirrors the logic in ``/results/curtailment``: per-snapshot effective
    capacity = ``initial + Σ vintages with build_year ≤ snapshot's period``,
    NOT the parent's final ``p_nom_opt``. Without this vintage mask, a
    2028-vintage build that's rolled into the parent's post-solve
    ``p_nom_opt`` inflates the 2026 max-available envelope and yields
    phantom curtailment — typically 5–10× too high. PyPSA's own
    ``n.statistics.curtailment()`` has the same overcounting issue on
    multi-period vintage-expanded networks; that's why the live Dispatch
    tab uses ``/results/curtailment`` (vintage-aware) instead.

    Only generators with a time-varying ``p_max_pu`` (renewables with a
    profile) contribute. Aggregation is weighted by
    ``_build_snapshot_weights`` so multi-period representative-week
    networks accumulate to the right horizon energy figures.
    """
    import math as _math

    import pandas as pd
    from models.schemas import CurtailmentComparison

    if not has_solve:
        return CurtailmentComparison()

    # ENERGY basis (generators): curtailment and available energy are MWh, so
    # they must use the same basis as the Compare Dispatch tab
    # (_compute_dispatch_summary, also generators) — otherwise curtailment GWh
    # disagrees with dispatch GWh under representative-week weighting. Whole
    # function is energy; no cost term.
    weights = _build_snapshot_weights(n, "generators")
    sns = n.snapshots

    curt_by_carrier: dict = {}
    avail_by_carrier: dict = {}

    gens = n.generators
    if gens.empty:
        return CurtailmentComparison()
    p_t = getattr(n.generators_t, "p", None) if hasattr(n, "generators_t") else None
    p_max_pu_t = getattr(n.generators_t, "p_max_pu", None) if hasattr(n, "generators_t") else None
    if p_t is None or p_t.empty or p_max_pu_t is None or p_max_pu_t.empty:
        return CurtailmentComparison()

    # ── Vintage-aware effective capacity per (snapshot, generator) ───────
    # For multi-period networks with vintage_results in n.meta, override the
    # parent's flat p_nom_opt with a time-varying effective capacity that
    # respects build_year. For non-vintage assets (no entry in meta) we fall
    # back to parent's static p_nom_opt — same as /results/curtailment.
    vintage_root = (n.meta or {}).get("vintage_results", {}) if hasattr(n, "meta") else {}
    gen_vr = vintage_root.get("Generator", {}) if isinstance(vintage_root, dict) else {}

    period_lvl = None
    if is_multi:
        try:
            period_lvl = sns.get_level_values(0).astype(int)
        except Exception:
            period_lvl = None

    def _effective_capacity_series(g_name: str) -> pd.Series:
        """
        Per-snapshot effective capacity for generator ``g_name``. Walks
        vintage_results entries and accumulates ``initial + Σ vintages
        with build_year ≤ snapshot's period``. Falls back to a flat series
        at parent's p_nom_opt when no vintage data exists (single-period
        networks or non-vintage-expanded assets).
        """
        try:
            parent_opt = float(gens.at[g_name, "p_nom_opt"]) if "p_nom_opt" in gens.columns else float(gens.at[g_name, "p_nom"])
        except (TypeError, ValueError, KeyError):
            parent_opt = 0.0
        if not _math.isfinite(parent_opt):
            parent_opt = 0.0
        meta_entry = gen_vr.get(g_name) if isinstance(gen_vr, dict) else None
        if not meta_entry or period_lvl is None:
            return pd.Series(parent_opt, index=sns, dtype=float)
        try:
            initial = float(meta_entry.get("initial_capacity", 0.0) or 0.0)
        except (TypeError, ValueError):
            initial = 0.0
        eff = pd.Series(initial, index=sns, dtype=float)
        for entry in (meta_entry.get("periods") or []):
            if not isinstance(entry, dict):
                continue
            try:
                by = int(entry.get("build_year"))
                pn = float(entry.get("p_nom_opt", 0.0) or 0.0)
            except (TypeError, ValueError):
                continue
            if pn <= 1e-9:
                continue
            mask = period_lvl >= by
            eff.values[mask] += pn
        return eff

    for g in p_t.columns:
        if g not in gens.index:
            continue
        # Only consider generators with a per-snapshot p_max_pu profile.
        # Thermal/conventional plants leave this column empty (static 1.0
        # applies); summing their (p_nom − p) would inflate the result with
        # economic dispatch headroom, not curtailment.
        if g not in p_max_pu_t.columns:
            continue
        eff_cap = _effective_capacity_series(g)
        if not _math.isfinite(float(eff_cap.max())) or float(eff_cap.max()) <= 1e-9:
            continue
        try:
            disp = p_t[g].reindex(sns).fillna(0.0).astype(float)
            pmu = p_max_pu_t[g].reindex(sns).fillna(0.0).astype(float)
        except Exception:
            continue
        available = pmu * eff_cap  # Hadamard product per snapshot
        # Clip negatives — solver tolerance can leave |p| > p_max_pu ×
        # effective_cap by ε; we don't want to count that as negative
        # curtailment.
        curtail = (available - disp).clip(lower=0)
        weighted_curt = curtail * weights
        weighted_avail = available * weights
        total_c = float(weighted_curt.sum())
        total_a = float(weighted_avail.sum())
        if not _math.isfinite(total_c) or not _math.isfinite(total_a):
            continue
        if total_a <= 1e-9:
            continue
        carrier = (str(gens.at[g, "carrier"]) if "carrier" in gens.columns else "unknown").lower()
        cb = curt_by_carrier.setdefault(carrier, {"total": 0.0, "by_period": {}})
        cb["total"] += total_c / 1000.0  # GWh
        for p, v in _per_period_groupby(weighted_curt, sns, is_multi).items():
            cb["by_period"][p] = cb["by_period"].get(p, 0.0) + v / 1000.0
        ab = avail_by_carrier.setdefault(carrier, {"total": 0.0, "by_period": {}})
        ab["total"] += total_a / 1000.0
        for p, v in _per_period_groupby(weighted_avail, sns, is_multi).items():
            ab["by_period"][p] = ab["by_period"].get(p, 0.0) + v / 1000.0

    if not curt_by_carrier:
        return CurtailmentComparison()

    # System totals.
    total_bucket = {"total": 0.0, "by_period": {}}
    total_avail = {"total": 0.0, "by_period": {}}
    for c, b in curt_by_carrier.items():
        total_bucket["total"] += b["total"]
        for p, v in b["by_period"].items():
            total_bucket["by_period"][p] = total_bucket["by_period"].get(p, 0.0) + v
    for c, b in avail_by_carrier.items():
        total_avail["total"] += b["total"]
        for p, v in b["by_period"].items():
            total_avail["by_period"][p] = total_avail["by_period"].get(p, 0.0) + v

    # Per-carrier rate.
    rate_by_carrier: dict = {}
    for c, b in curt_by_carrier.items():
        ab = avail_by_carrier.get(c) or {"total": 0.0, "by_period": {}}
        rate_t = 100.0 * b["total"] / ab["total"] if ab["total"] > 1e-9 else 0.0
        rate_pp: dict = {}
        for p, v in b["by_period"].items():
            ap = ab["by_period"].get(p, 0.0)
            rate_pp[p] = 100.0 * v / ap if ap > 1e-9 else 0.0
        rate_by_carrier[c] = {"total": rate_t, "by_period": rate_pp}

    # System rate.
    sys_rate_t = 100.0 * total_bucket["total"] / total_avail["total"] if total_avail["total"] > 1e-9 else 0.0
    sys_rate_pp: dict = {}
    for p, v in total_bucket["by_period"].items():
        ap = total_avail["by_period"].get(p, 0.0)
        sys_rate_pp[p] = 100.0 * v / ap if ap > 1e-9 else 0.0

    return CurtailmentComparison(
        total_gwh=_to_pv(total_bucket),
        by_carrier_gwh=_to_pv_dict(curt_by_carrier),
        rate_pct_by_carrier=_to_pv_dict(rate_by_carrier),
        system_rate_pct=_to_pv({"total": sys_rate_t, "by_period": sys_rate_pp}),
    )


def _compute_lost_load_summary(
    project_dir: pathlib.Path, n, periods, is_multi, has_solve,
) -> LostLoadComparison:
    """
    Lost-load (VOLL slack dispatch) view.

    Reads from ``results_state.pkl`` because the VOLL slack DataFrame can't
    survive a netcdf round-trip — the slack generators are stripped right
    after capture inside solver_service. The capture format is:
      ``{lost_load_t: DataFrame(snapshot × bus), lost_load_total_mwh: float,
         lost_load_cost_eur: float}``
    Returns ``available=False`` when the pickle is absent, the capture key
    is missing, or the DataFrame is empty — all three are "no shedding"
    states from the user's perspective. Multi-period split uses the snapshot
    weight matrix the rest of the comparison view shares.
    """
    from models.schemas import LostLoadBus, LostLoadByCarrier, LostLoadComparison

    if not has_solve:
        return LostLoadComparison()
    results_path = project_dir / "results_state.pkl"
    if not results_path.exists():
        return LostLoadComparison()
    try:
        raw = _safe_unpickle_results(results_path.read_bytes())
    except Exception:
        return LostLoadComparison()
    # Accept both the versioned envelope and the legacy bare dict.
    data = _unwrap_results_state(raw)
    cap = data.get("last_lost_load") if isinstance(data, dict) else None
    if not cap or cap.get("lost_load_t") is None:
        return LostLoadComparison()
    df = cap.get("lost_load_t")
    if df is None or getattr(df, "empty", True):
        return LostLoadComparison()

    total_mwh_scalar = float(cap.get("lost_load_total_mwh", 0.0) or 0.0)
    total_cost_scalar = float(cap.get("lost_load_cost_eur", 0.0) or 0.0)
    voll = total_cost_scalar / total_mwh_scalar if total_mwh_scalar > 0 else 0.0

    # Align to current snapshots. The capture is keyed on the snapshot index
    # used at solve time; if the project was re-saved with a different snapshot
    # set, reindex drops orphans and inserts zeros for missing — defensively
    # consistent rather than crashing.
    import math as _math
    # ENERGY basis (generators): lost-load is shed energy (MWh), so weight it on
    # the same basis as dispatch/served-load rather than the cost column. Whole
    # function is energy (× VOLL only for the derived € cost, applied after).
    weights = _build_snapshot_weights(n, "generators")
    sns = n.snapshots
    try:
        df_aligned = df.reindex(sns).fillna(0.0).astype(float)
        weighted = df_aligned.mul(weights, axis=0)
    except Exception:
        return LostLoadComparison()

    total_e = float(weighted.values.sum())
    if not _math.isfinite(total_e) or total_e <= 1e-9:
        # The capture exists but produced zero after reindex — treat as
        # "no shedding" rather than "available but zero."
        return LostLoadComparison(available=False, voll_eur_per_mwh=voll)

    total_e_bucket = {"total": total_e, "by_period": {}}
    total_c_bucket = {"total": total_e * voll / 1e6, "by_period": {}}
    if is_multi:
        try:
            by_period_e = weighted.groupby(sns.get_level_values(0)).sum().sum(axis=1)
            for p, v in by_period_e.items():
                v_f = float(v)
                if not _math.isfinite(v_f):
                    continue
                total_e_bucket["by_period"][str(int(p))] = v_f
                total_c_bucket["by_period"][str(int(p))] = v_f * voll / 1e6
        except Exception:
            pass

    # Per-bus rows sorted by horizon-wide energy desc. Cap at 24 entries to
    # keep the payload bounded on large networks; the frontend table renders
    # them ranked.
    by_bus_list: list[LostLoadBus] = []
    try:
        bus_totals = weighted.sum(axis=0).sort_values(ascending=False)
    except Exception:
        bus_totals = None
    if bus_totals is not None:
        for bus_name, energy in bus_totals.items():
            try:
                e_v = float(energy)
            except (TypeError, ValueError):
                continue
            if not _math.isfinite(e_v) or e_v <= 1e-6:
                continue
            e_bucket = {"total": e_v, "by_period": {}}
            c_bucket = {"total": e_v * voll / 1e6, "by_period": {}}
            if is_multi:
                try:
                    bus_series = weighted[bus_name]
                    grouped = bus_series.groupby(sns.get_level_values(0)).sum()
                    for p, vv in grouped.items():
                        vv_f = float(vv)
                        if not _math.isfinite(vv_f):
                            continue
                        e_bucket["by_period"][str(int(p))] = vv_f
                        c_bucket["by_period"][str(int(p))] = vv_f * voll / 1e6
                except Exception:
                    pass
            by_bus_list.append(LostLoadBus(
                bus=str(bus_name),
                energy_mwh=_to_pv(e_bucket),
                cost_meur=_to_pv(c_bucket),
            ))
            if len(by_bus_list) >= 24:
                break

    # Per-bus-carrier roll-up. Group buses by their `carrier` attribute on
    # the network and accumulate energy + cost. Each carrier's energy is
    # the sum of its bus shedding (horizon total + per-period). Empty when
    # the network lacks a `buses.carrier` column (rare).
    by_carrier_list: list[LostLoadByCarrier] = []
    buses_df = getattr(n, "buses", None)
    if (buses_df is not None and not buses_df.empty
            and "carrier" in buses_df.columns):
        carrier_acc: dict[str, dict] = {}
        for entry in by_bus_list:
            bus_name = entry.bus
            try:
                raw_c = buses_df.at[bus_name, "carrier"]
                carrier = str(raw_c).lower() if raw_c not in (None, "") else "unspecified"
            except Exception:
                carrier = "unspecified"
            acc = carrier_acc.setdefault(carrier, {
                "bus_count": 0,
                "e_total": 0.0,
                "e_pp": {},
                "c_total": 0.0,
                "c_pp": {},
            })
            acc["bus_count"] += 1
            acc["e_total"] += entry.energy_mwh.total
            for p_key, v in entry.energy_mwh.by_period.items():
                acc["e_pp"][p_key] = acc["e_pp"].get(p_key, 0.0) + v
            acc["c_total"] += entry.cost_meur.total
            for p_key, v in entry.cost_meur.by_period.items():
                acc["c_pp"][p_key] = acc["c_pp"].get(p_key, 0.0) + v
        for carrier, acc in carrier_acc.items():
            by_carrier_list.append(LostLoadByCarrier(
                carrier=carrier,
                bus_count=acc["bus_count"],
                energy_mwh=CarrierPeriodValue(total=acc["e_total"], by_period=acc["e_pp"]),
                cost_meur=CarrierPeriodValue(total=acc["c_total"], by_period=acc["c_pp"]),
            ))
        by_carrier_list.sort(key=lambda e: e.energy_mwh.total, reverse=True)

    return LostLoadComparison(
        available=True,
        voll_eur_per_mwh=voll,
        total_mwh=_to_pv(total_e_bucket),
        total_cost_meur=_to_pv(total_c_bucket),
        by_bus=by_bus_list,
        by_carrier=by_carrier_list,
    )


def _compute_storage_cycling_summary(n, periods, is_multi, has_solve) -> StorageCyclingComparison:
    """
    Per-storage-unit cycling with carrier rollup, vintage-aware per period.

    Cycle definition: ``throughput / (2 × energy_capacity)`` where throughput
    is ``Σ |p| × snapshot_weight`` (sign-agnostic — charge and discharge
    count equally). For multi-period vintage-expanded networks the
    ``energy_capacity`` denominator MUST be the cumulative-by-period
    capacity (``initial + Σ vintages with build_year ≤ P``), NOT the parent's
    final ``p_nom_opt × max_hours``. Using the final cumulative undercounts
    early-period cycles (denominator too big when the unit hadn't yet
    expanded) — e.g. a 50→500 MWh battery doing 200 MWh of throughput in
    its first year shows 0.2 cycles instead of the true 2.

    "All" (horizon-wide) is the AVERAGE of per-period cycles for multi-
    period networks — not throughput/total-cap — so a 3-year run with 100
    cycles/year reads as 100, not 300. For flat networks the per-period
    dict is empty and the total is computed straight from the horizon
    throughput and energy capacity.

    Snapshot weight is the OBJECTIVE weight only — NOT multiplied by
    ``investment_period_weightings.years`` — because cycles is intrinsically
    a per-year metric.
    """
    import math as _math

    import pandas as pd
    from models.schemas import StorageCyclingComparison, StorageUnitCycles

    if not has_solve:
        return StorageCyclingComparison()
    sus = n.storage_units
    if sus.empty:
        return StorageCyclingComparison()
    p_storage = getattr(n.storage_units_t, "p", None) if hasattr(n, "storage_units_t") else None
    if p_storage is None or p_storage.empty:
        return StorageCyclingComparison()

    sns = n.snapshots
    try:
        sw_only = n.snapshot_weightings.loc[sns, "objective"].astype(float)
    except Exception:
        sw_only = pd.Series(1.0, index=sns, dtype=float)

    vintage_root = (n.meta or {}).get("vintage_results", {}) if hasattr(n, "meta") else {}
    su_vr = vintage_root.get("StorageUnit", {}) if isinstance(vintage_root, dict) else {}

    def _per_period_pnom(s_name: str, parent_opt: float) -> dict[int, float]:
        """
        Cumulative ``p_nom`` active in each period for storage unit
        ``s_name``: ``initial + Σ vintages with build_year ≤ P``. Non-vintage
        / single-period assets return a flat ``{P: parent_opt}`` for each
        period.
        """
        if not is_multi or not periods:
            return {}
        meta_entry = su_vr.get(s_name) if isinstance(su_vr, dict) else None
        if not isinstance(meta_entry, dict):
            return {int(p): parent_opt for p in periods}
        try:
            initial = float(meta_entry.get("initial_capacity", 0.0) or 0.0)
        except (TypeError, ValueError):
            initial = 0.0
        out: dict[int, float] = {}
        for p in periods:
            cum = initial
            for entry in (meta_entry.get("periods") or []):
                if not isinstance(entry, dict):
                    continue
                try:
                    by = int(entry.get("build_year"))
                    pn = float(entry.get("p_nom_opt", 0.0) or 0.0)
                except (TypeError, ValueError):
                    continue
                if pn <= 1e-9:
                    continue
                if by <= int(p):
                    cum += pn
            out[int(p)] = cum
        return out

    by_unit: list[StorageUnitCycles] = []
    # Carrier rollup buckets: per-period throughput and per-period energy_cap
    # accumulated across units in the same carrier. cycles_carrier[P] is
    # derived from these as Σ_units throughput[P] / (2 × Σ_units energy_cap[P]).
    tp_by_carrier_pp: dict[str, dict[str, float]] = {}
    ec_by_carrier_pp: dict[str, dict[str, float]] = {}
    tp_by_carrier_total: dict[str, float] = {}     # horizon throughput
    ec_by_carrier_final: dict[str, float] = {}     # final energy_cap (for flat-net fallback)

    for s in sus.index:
        try:
            parent_pnom_opt = float(sus.at[s, "p_nom_opt"]) if "p_nom_opt" in sus.columns else float(sus.at[s, "p_nom"])
            max_hours = float(sus.at[s, "max_hours"]) if "max_hours" in sus.columns else 0.0
            carrier = (str(sus.at[s, "carrier"]) if "carrier" in sus.columns else "storage").lower()
        except (TypeError, ValueError):
            continue
        if not _math.isfinite(parent_pnom_opt) or parent_pnom_opt <= 1e-9:
            continue
        if not _math.isfinite(max_hours) or max_hours <= 0:
            continue
        if s not in p_storage.columns:
            continue
        try:
            series = p_storage[s].reindex(sns).fillna(0.0).abs().astype(float)
        except Exception:
            continue

        throughput_t = series * sw_only
        tp_total = float(throughput_t.sum())
        if not _math.isfinite(tp_total):
            continue
        tp_pp = _per_period_groupby(throughput_t, sns, is_multi)

        # Per-period effective energy_cap for THIS unit.
        per_period_pnom = _per_period_pnom(s, parent_pnom_opt)
        per_period_ec: dict[int, float] = {p: pn * max_hours for p, pn in per_period_pnom.items()}
        final_ec = parent_pnom_opt * max_hours  # for flat-net or all-horizon fallback

        # Per-period cycles using period-specific energy capacity.
        cycles_pp: dict[str, float] = {}
        for p_str, tp in tp_pp.items():
            try:
                ec_p = per_period_ec.get(int(p_str), final_ec)
            except (TypeError, ValueError):
                ec_p = final_ec
            cycles_pp[p_str] = (tp / (2 * ec_p)) if ec_p > 1e-9 else 0.0

        # Horizon-wide "total" = AVERAGE of per-period cycles for multi-period
        # (so a 3-year horizon doesn't read 3× the per-period count); for flat
        # networks fall back to throughput / (2 × final_ec).
        if is_multi and cycles_pp:
            cycles_total = sum(cycles_pp.values()) / len(cycles_pp)
        else:
            cycles_total = (tp_total / (2 * final_ec)) if final_ec > 1e-9 else 0.0

        by_unit.append(StorageUnitCycles(
            name=str(s),
            carrier=carrier,
            p_nom_mw=parent_pnom_opt,
            energy_mwh=final_ec,
            throughput_mwh=_to_pv({"total": tp_total, "by_period": tp_pp}),
            cycles=_to_pv({"total": cycles_total, "by_period": cycles_pp}),
        ))

        # Carrier rollup accumulators (per-period values, not unit-final).
        tp_b = tp_by_carrier_pp.setdefault(carrier, {})
        for p_str, tp in tp_pp.items():
            tp_b[p_str] = tp_b.get(p_str, 0.0) + tp
        ec_b = ec_by_carrier_pp.setdefault(carrier, {})
        for p_int, ec in per_period_ec.items():
            key = str(p_int)
            ec_b[key] = ec_b.get(key, 0.0) + ec
        tp_by_carrier_total[carrier] = tp_by_carrier_total.get(carrier, 0.0) + tp_total
        ec_by_carrier_final[carrier] = ec_by_carrier_final.get(carrier, 0.0) + final_ec

    # Carrier-level cycles: per-period using per-period denominator; total
    # is the AVERAGE of per-period values (multi-period) or throughput /
    # (2 × final_ec) for flat networks.
    cycles_by_carrier: dict = {}
    for c, tp_pp in tp_by_carrier_pp.items():
        ec_pp = ec_by_carrier_pp.get(c, {})
        pp: dict[str, float] = {}
        for p_str, tp in tp_pp.items():
            ec_p = ec_pp.get(p_str, 0.0)
            pp[p_str] = (tp / (2 * ec_p)) if ec_p > 1e-9 else 0.0
        if is_multi and pp:
            total = sum(pp.values()) / len(pp)
        else:
            final_ec_c = ec_by_carrier_final.get(c, 0.0)
            tp_total_c = tp_by_carrier_total.get(c, 0.0)
            total = (tp_total_c / (2 * final_ec_c)) if final_ec_c > 1e-9 else 0.0
        cycles_by_carrier[c] = {"total": total, "by_period": pp}

    # Sort detail rows by horizon-wide cycles desc so the most-active units
    # surface first.
    by_unit.sort(key=lambda u: -(u.cycles.total or 0))

    return StorageCyclingComparison(
        cycles_by_carrier=_to_pv_dict(cycles_by_carrier),
        by_unit=by_unit,
    )


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
    economics = _compute_economics_summary(temp_n, periods, is_multi, has_solve, prices_from_state=False)
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


