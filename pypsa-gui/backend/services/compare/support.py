"""
Shared helpers of the compare engine: bucketing, per-period value objects, the periodized-cost lookup, capital-cost resolution, build-year classification, snapshot weights.

Moved from `routers/compare.py`. `routers.compare` re-exports every name here
under the same name — or wraps it, where the function now takes the solver
config / result lookup as keyword-only arguments instead of reading router
state — so no call site changed. See the decomposition spec, Phase 3 addendum.

math / pandas are imported locally inside functions where the router did the
same; module-level imports below are only what the bodies reference at module
scope.
"""
from __future__ import annotations

import pandas as pd
from models.schemas import (
    CarrierPeriodValue,
)
from services import economics
from services import period_utils
from services.serialization import safe_float as _safe_float


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


def _periodized_lookup(n, *, cfg=None) -> dict:
    """
    Build ``services.solver_service.periodized_capital_costs``'s per-asset
    dict ONCE for this network under ``cfg`` — ``None`` means a default
    ``SolverConfig()``. Callers that walk many asset rows (the ``_walk_*``
    closures in the summaries) must call this ONCE per network and pass the
    result to ``_safe_capital_cost`` — ``periodized_capital_costs`` walks
    every cost-bearing component class in one pass, so calling it per-row
    would be O(rows) times more expensive for the exact same answer.

    Returns ``{}`` if ``periodized_capital_costs`` itself raises. Every
    ``_safe_capital_cost`` lookup against an empty dict already resolves to
    0.0 (see that function's docstring), so this degrades to "no CAPEX
    contribution from any asset" rather than a 500 — matching
    ``get_economics_by_carrier`` (routers/results.py), which wraps its whole
    ``_compute_economics_summary`` call in a try/except for the same reason.

    The router used to resolve ``cfg`` in here, from the LIVE solver state
    (``routers.simulation._state["solver_config"]``) through a lazy import.
    ``routers.compare._periodized_lookup`` still does exactly that and passes
    the result in, so the service never reaches for router state.
    """
    from services.solver_service import SolverConfig, periodized_capital_costs

    if cfg is None:
        cfg = SolverConfig()
    try:
        return periodized_capital_costs(n, cfg)
    except Exception:
        return {}


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


def _co2_intensity_map(n) -> dict[str, float]:
    """
    Return ``carrier_name_lower → co2_emissions (tCO2/MWh_th)`` for every
    carrier whose ``co2_emissions`` is finite.

    Thin wrapper kept for this module's call sites; the implementation moved
    to `services.economics.co2_intensity_map` so the Results tab and the
    Compare rail read the same carrier intensities.
    """
    return economics.co2_intensity_map(n)
