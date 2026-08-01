"""
Per-metric computation. One function per metric.

Every function takes a single `Ctx` and returns either a pandas Series aligned
to `ctx.sns` (series metrics) or a JSON-ready scalar/dict (scalar metrics). No
HTTP, no serialisation, no NaN scrubbing — the router owns all three.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from services.dispatch_status import dispatch_status as _dispatch_status
from services.period_utils import is_multi_period, snapshot_weights

logger = logging.getLogger(__name__)

# `.applicability` and `.registry` are imported LOCALLY inside the functions
# below, never at module level. `registry.py` does `from . import compute as
# C` at its own module level to bind each Metric's `compute=` field — so a
# module-level `from .applicability import ...` here (applicability itself
# imports `.registry`) closes a three-module cycle:
# registry -> compute -> applicability -> registry, and whichever of the
# three is imported first observes the second one only partially
# initialised. Same rationale as the `routers.simulation._state_snapshot`
# local import a few lines down. `TYPE_CHECKING` keeps `Status` resolvable
# for type checkers without executing at runtime.
if TYPE_CHECKING:
    from .applicability import Status

_ATTR: dict[str, str] = {
    "Bus": "buses", "Generator": "generators", "Load": "loads",
    "Line": "lines", "Transformer": "transformers", "Link": "links",
    "StorageUnit": "storage_units", "Store": "stores",
}


def attr_for(component_class: str) -> str:
    """PyPSA's lowercase-plural DataFrame attribute for a component class."""
    return _ATTR[component_class]


@dataclass(frozen=True)
class Ctx:
    n: Any
    component_class: str
    name: str
    source: str
    sns: Any             # pd.Index — the (already filtered) snapshot slice
    weights: Any         # pd.Series aligned to sns — snapshot × period weight
    is_multi: bool
    params: dict         # the asset's static row, NaN-scrubbed


def build_ctx(n, component_class: str, name: str, *, source: str, sns) -> Ctx:
    import math

    import pandas as pd

    attr = attr_for(component_class)
    df = getattr(n, attr)
    params: dict = {}
    if name in df.index:
        for k, v in df.loc[name].to_dict().items():
            if isinstance(v, float) and not math.isfinite(v):
                params[k] = None
            elif isinstance(v, pd.Timestamp):
                params[k] = v.isoformat()
            else:
                params[k] = v

    # Energy basis: the `generators` weighting column, matching n.statistics()
    # and every existing results endpoint.
    #
    # `snapshot_weights` ALREADY multiplies by investment_period_weightings.years
    # when `sns` is a MultiIndex — that is the entire purpose of the helper
    # (period_utils.py:123-135). Do NOT apply the years map again here. Doing so
    # yields weight x years^2 and inflates every energy and cost total by a factor
    # of `years` on any multi-period network.
    try:
        w = snapshot_weights(n, "generators", sns)
    except Exception:
        w = pd.Series(1.0, index=sns)
    multi = is_multi_period(n)
    return Ctx(n=n, component_class=component_class, name=name, source=source,
               sns=sns, weights=w, is_multi=multi, params=params)


# ── Preconditions ───────────────────────────────────────────────────────────

def preconditions(n, component_class: str, name: str) -> dict[str, Status]:
    """
    Evaluate every precondition once per request.

    Returned map is consumed by `applicability.resolve_metric`; anything a
    metric does not list is irrelevant to it.
    """
    from routers.simulation import _state_snapshot

    from .applicability import OK, Remedy, Status
    from .registry import (
        REQ_AC_PF,
        REQ_ANNUITY,
        REQ_CO2,
        REQ_COMMITTABLE,
        REQ_DISPATCH,
        REQ_DUALS,
    )

    out: dict[str, Status] = {}

    # 1. A FRESH solve. `is_solved` alone is not trustworthy — it survives a
    #    netcdf round-trip even when the _t frames are empty or describe a
    #    different topology, so gate on dispatch_status like every other
    #    results endpoint does.
    ds = _dispatch_status(n) if getattr(n, "is_solved", False) else "none"
    if ds == "fresh":
        out[REQ_DISPATCH] = OK
    elif ds == "stale":
        out[REQ_DISPATCH] = Status(
            "blocked",
            "results are stale — the network changed after the last solve, so "
            "dispatch no longer matches the current topology",
            Remedy("run_simulation", "Re-run simulation"),
        )
    else:
        out[REQ_DISPATCH] = Status(
            "blocked", "the network has not been solved",
            Remedy("run_simulation", "Run simulation"),
        )

    # 2. AC power flow stage results.
    ac = _state_snapshot().get("ac_pf_results")
    out[REQ_AC_PF] = OK if isinstance(ac, dict) and ac else Status(
        "blocked", "AC power flow has not been run",
        Remedy("run_ac_pf", "Run AC power flow"),
    )

    # 3. LP duals for this component class + nodal prices.
    out[REQ_DUALS] = _duals_status(n, component_class)

    # 4. Unit commitment on THIS asset.
    out[REQ_COMMITTABLE] = _committable_status(n, component_class, name)

    # 5. A carbon-bearing carrier on THIS asset.
    out[REQ_CO2] = _co2_status(n, component_class, name)

    # 6. An overnight_cost-priced asset must resolve a discount_rate and a
    #    finite lifetime to annualise — see `capex_unresolved_reason`.
    out[REQ_ANNUITY] = _annuity_status(n, component_class, name)
    return out


def _duals_status(n, component_class: str) -> Status:
    from .applicability import OK, Remedy, Status

    prices = getattr(n.buses_t, "marginal_price", None)
    if prices is not None and not prices.empty:
        return OK
    return Status(
        "blocked",
        "LP duals were not captured in this solve",
        Remedy("run_simulation", "Re-run simulation"),
    )


def _committable_status(n, component_class: str, name: str) -> Status:
    from .applicability import OK, Remedy, Status

    if component_class not in ("Generator", "Link"):
        return Status("na", f"{component_class} is not committable")
    df = getattr(n, attr_for(component_class))
    if name in df.index and bool(df.at[name, "committable"]):
        status = getattr(getattr(n, f"{attr_for(component_class)}_t"), "status", None)
        if status is not None and name in getattr(status, "columns", []):
            return OK
        return Status(
            "blocked",
            f"{name} is committable but this solve produced no commitment status",
            Remedy("run_simulation", "Re-run simulation"),
        )
    return Status(
        "blocked",
        f"unit commitment is not enabled on {name} (committable = false)",
        Remedy("open_properties", "Enable committable"),
    )


def _co2_status(n, component_class: str, name: str) -> Status:
    from .applicability import OK, Remedy, Status

    df = getattr(n, attr_for(component_class))
    if name not in df.index or "carrier" not in df.columns:
        return Status("na", f"{component_class} has no carrier")
    carrier = str(df.at[name, "carrier"])
    carriers = n.carriers
    if carrier in carriers.index and "co2_emissions" in carriers.columns:
        val = float(carriers.at[carrier, "co2_emissions"] or 0.0)
        if val > 0:
            return OK
    return Status(
        "blocked",
        f"carrier '{carrier}' declares no co2_emissions, so emissions are zero "
        f"by assumption rather than by result",
        Remedy("open_properties", f"Set co2_emissions on '{carrier}'"),
    )


def _annuity_status(n, component_class: str, name: str) -> Status:
    """
    `capex_unresolved_reason` promoted to a `Status` for the precondition map.

    Silent (OK) for every asset priced via `capital_cost` directly, or
    genuinely free — the reason function itself stays silent there, see its
    docstring. Only trips for an asset priced via `overnight_cost` whose
    `discount_rate` and/or `lifetime` did not resolve.
    """
    from .applicability import OK, Remedy, Status

    reason = capex_unresolved_reason(n, component_class, name)
    if reason is None:
        return OK
    label = "Set discount rate" if "discount_rate" in reason else "Set lifetime"
    return Status("blocked", reason, Remedy("open_properties", label))


# ── summary (every class) ───────────────────────────────────────────────────

def summary_identity(ctx: Ctx) -> dict:
    p = ctx.params
    out = {"name": ctx.name, "class": ctx.component_class,
           "carrier": p.get("carrier", "")}
    for key in ("bus", "bus0", "bus1", "bus2", "type"):
        if key in p and p[key] not in (None, ""):
            out[key] = p[key]
    return out


# Static columns worth showing per class. Curated, not exhaustive — the full
# parameter sheet stays in PropertiesPanel, which can also edit it.
_SUMMARY_PARAMS: dict[str, tuple[str, ...]] = {
    "Bus": ("v_nom", "control", "x", "y", "carrier"),
    "Generator": ("p_nom", "p_nom_extendable", "p_nom_min", "p_nom_max",
                  "marginal_cost", "capital_cost", "efficiency", "committable",
                  "build_year", "lifetime"),
    "Load": ("p_set", "q_set"),
    "Line": ("s_nom", "s_nom_extendable", "length", "r", "x", "b",
             "capital_cost", "v_nom"),
    "Transformer": ("s_nom", "s_nom_extendable", "r", "x", "tap_ratio",
                    "capital_cost"),
    "Link": ("p_nom", "p_nom_extendable", "efficiency", "marginal_cost",
             "capital_cost", "committable"),
    "StorageUnit": ("p_nom", "p_nom_extendable", "max_hours",
                    "efficiency_store", "efficiency_dispatch", "marginal_cost",
                    "capital_cost", "cyclic_state_of_charge"),
    "Store": ("e_nom", "e_nom_extendable", "e_cyclic", "marginal_cost",
              "capital_cost", "standing_loss"),
}


def summary_params(ctx: Ctx) -> dict:
    keys = _SUMMARY_PARAMS.get(ctx.component_class, ())
    return {k: ctx.params.get(k) for k in keys if k in ctx.params}


# ── Series access ───────────────────────────────────────────────────────────

def series_for(ctx: Ctx, attr: str):
    """
    This asset's column from `<component>_t.<attr>`, reindexed to ctx.sns.

    Honours the lopf/ac_pf snapshot the same way every /results/* endpoint
    does, and returns None (rather than raising) whenever the attribute, the
    frame or the column is absent — the caller turns that into `null`.
    """
    from routers.results import _result_df

    df = _result_df(ctx.n, f"{attr_for(ctx.component_class)}_t", attr, ctx.source)
    if df is None or getattr(df, "empty", True):
        return None
    if ctx.name not in df.columns:
        return None
    return df[ctx.name].reindex(ctx.sns)


def _static(ctx: Ctx, col: str) -> float | None:
    v = ctx.params.get(col)
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _wsum(ctx: Ctx, s) -> float | None:
    """Weighted sum over the context's snapshot slice."""
    if s is None:
        return None
    return float((s.fillna(0.0) * ctx.weights).sum())


def _whours(ctx: Ctx, mask) -> float | None:
    """
    Weighted hour-count of the snapshots where `mask` is true.

    Every "… hours" metric in this module goes through here so none of them
    can regress to a raw snapshot COUNT, which reads as real hours on any
    representative-period network (snapshot_weightings.generators = 52.14
    turns 84 snapshots into 4380 h, and the bare count would show "84 h").
    """
    if mask is None:
        return None
    return float((mask.astype(float) * ctx.weights).sum())


def _smax(s) -> float | None:
    return None if s is None or s.empty else float(s.max())


def _smin(s) -> float | None:
    return None if s is None or s.empty else float(s.min())


def _wmean(ctx: Ctx, s) -> float | None:
    """Snapshot-weighted mean — the only correct mean over a weighted horizon."""
    if s is None:
        return None
    hours = float(ctx.weights.sum())
    return None if not hours else float((s.fillna(0.0) * ctx.weights).sum()) / hours


def raw(attr: str):
    """
    Metric compute returning this asset's own `<class>_t.<attr>` column.

    Most series metrics are a straight passthrough of a PyPSA output frame and
    differ only in the attribute name. A factory keeps them one line each in
    the registry instead of ~40 identical two-line functions.
    """
    def _f(ctx: Ctx):
        return series_for(ctx, attr)
    _f.__name__ = f"raw_{attr}"
    _f.__qualname__ = _f.__name__
    return _f


def _dense(ctx: Ctx, col: str):
    """
    This asset's column of `get_switchable_as_dense(class, col)`.

    The static-or-time-varying dance PyPSA makes callers do for every input
    attribute: a scalar column broadcast across the horizon when the user
    never uploaded a profile, the profile itself when they did.
    """
    try:
        df = ctx.n.get_switchable_as_dense(ctx.component_class, col)
    except Exception:
        return None
    if df is None or ctx.name not in getattr(df, "columns", []):
        return None
    return df[ctx.name].reindex(ctx.sns)


# ── capacity (class-generic) ────────────────────────────────────────────────
# Every sizeable class has a nominal-capacity column that differs only in its
# name. One set of functions keyed off the class keeps `p_nom`/`s_nom`/`e_nom`
# from becoming four near-identical copies of the same four metrics.

_NOM_COL: dict[str, str] = {
    "Generator": "p_nom", "Link": "p_nom", "StorageUnit": "p_nom",
    "Line": "s_nom", "Transformer": "s_nom", "Store": "e_nom",
}


def _nom_col(ctx: Ctx) -> str:
    return _NOM_COL.get(ctx.component_class, "p_nom")


def nom_capacity(ctx: Ctx):
    return _static(ctx, _nom_col(ctx))


def nom_capacity_opt(ctx: Ctx):
    return _static(ctx, f"{_nom_col(ctx)}_opt")


def nom_capacity_delta(ctx: Ctx):
    opt, nom = nom_capacity_opt(ctx), nom_capacity(ctx)
    return None if opt is None or nom is None else opt - nom


def capex_annual(ctx: Ctx):
    """
    Annualised CAPEX, EUR/a.

    Resolves through `periodized_capital_costs` rather than reading the raw
    `capital_cost` column. MEASURED 2026-07-31: the raw column is 0.0 whenever
    the user parameterised the asset via `overnight_cost` — PyPSA derives the
    real figure on the components accessor, not on the DataFrame — so a raw
    read reported 22.3% low for a gas plant, 41.7% low for solar and EUR 0 for
    an electrolyser, against the Economics tab's figure for the same asset.
    """
    opt = nom_capacity_opt(ctx)
    if opt is None:
        return None

    from routers.simulation import _state
    from services.solver_service import periodized_capital_costs

    cfg = _state.get("solver_config")
    try:
        costs = periodized_capital_costs(ctx.n, cfg)
        cc = float(
            costs.get(attr_for(ctx.component_class), {})
                 .get(ctx.name, {})
                 .get("capital_cost", 0.0)
        )
    except Exception as exc:  # noqa: BLE001 — fall back to the raw column, never crash
        logger.warning(
            "capex_annual: periodized_capital_costs failed for %s %r, falling "
            "back to raw capital_cost column (reports 0.0 for an "
            "overnight_cost-priced asset): %s",
            ctx.component_class, ctx.name, exc,
        )
        raw = _static(ctx, "capital_cost")
        cc = float(raw) if raw is not None else 0.0

    return cc * opt


def capex_unresolved_reason(n, component_class: str, name: str) -> str | None:
    """
    Why an asset's CAPEX could not be annualised, or None if it could.

    Reads directly off the network's static row rather than requiring a full
    `Ctx`. `preconditions()` below calls this once per (class, name) on
    EVERY request — building a `Ctx` (weights, is_multi, a NaN-scrubbed copy
    of the whole row) just to answer this one question would be wasted work
    on every asset, including ones with no `overnight_cost` at all.

    Only the NaN-defaulted inputs are detectable. `capital_cost`,
    `marginal_cost` and `fom_cost` default to 0.0 and PyPSA materialises that
    on load — MEASURED: `links_marginal_cost` was absent from a real netCDF
    entirely and still read 0.0 in memory — so for those fields "unset" and
    "deliberately zero" are indistinguishable and this function stays silent.
    """
    import math

    import routers.simulation as sim_router

    df = getattr(n, attr_for(component_class), None)
    if df is None or name not in df.index:
        return None

    def _col(col: str) -> float | None:
        if col not in df.columns:
            return None
        try:
            return float(df.at[name, col])
        except (TypeError, ValueError):
            return None

    overnight = _col("overnight_cost")
    if overnight is None or math.isnan(overnight):
        # Priced directly via capital_cost (or genuinely free). Nothing to annualise.
        return None

    rate = _col("discount_rate")
    if rate is None or math.isnan(rate):
        cfg = sim_router._state.get("solver_config")
        cfg_rate = getattr(cfg, "discount_rate", None)
        if cfg_rate is None or (isinstance(cfg_rate, float) and math.isnan(cfg_rate)):
            return (
                "CAPEX cannot be annualised: this asset is priced via "
                "overnight_cost, but discount_rate is unset on the asset and "
                "no solver-config default applies."
            )

    lifetime = _col("lifetime")
    if lifetime is None or not math.isfinite(lifetime):
        return (
            "CAPEX cannot be annualised: this asset is priced via "
            "overnight_cost, but lifetime is unset (infinite), so there is no "
            "period to spread the investment over."
        )

    return None


def gen_capex_unresolved_reason(ctx: Ctx) -> str | None:
    """`Ctx`-flavoured wrapper around `capex_unresolved_reason` — kept for
    direct compute-layer tests; `preconditions()` calls the network-level
    form directly instead of building a `Ctx` for it."""
    return capex_unresolved_reason(ctx.n, ctx.component_class, ctx.name)


# Generator-flavoured names kept as aliases: for `Generator` the generic
# functions resolve `p_nom`/`p_nom_opt`, so these are the same callable under
# the name the Generator registry entries and their tests already use.
gen_p_nom = nom_capacity
gen_p_nom_opt = nom_capacity_opt
gen_p_nom_delta = nom_capacity_delta
gen_capex_annual = capex_annual


def gen_p_nom_by_vintage(ctx: Ctx):
    """
    Per-period capacity recovered from the solver's `<name>@<year>` clone rows.

    Those rows are transient and hidden from every asset list, but they carry
    the per-vintage p_nom_opt the user actually wants to see under the parent.
    Returns None on a flat network (no vintages) so the metric renders as
    "not applicable here" rather than an empty object.
    """
    df = getattr(ctx.n, attr_for(ctx.component_class))
    prefix = f"{ctx.name}@"
    out: dict[str, float] = {}
    for row in df.index:
        rs = str(row)
        if not rs.startswith(prefix):
            continue
        year = rs[len(prefix):]
        if not year.isdigit():
            continue
        try:
            out[year] = float(df.at[row, "p_nom_opt"])
        except (KeyError, TypeError, ValueError):
            continue
    return out or None


# ── dispatch: series ────────────────────────────────────────────────────────

def gen_p(ctx: Ctx):
    return series_for(ctx, "p")


def gen_p_max_pu(ctx: Ctx):
    """
    Availability. Time-varying when the user uploaded a profile, otherwise the
    static column broadcast across the horizon — a renewable with a flat
    p_max_pu still has a meaningful availability curve.
    """
    import pandas as pd

    s = series_for(ctx, "p_max_pu")
    if s is not None:
        return s
    v = _static(ctx, "p_max_pu")
    return None if v is None else pd.Series(v, index=ctx.sns)


def gen_available(ctx: Ctx):
    pu, opt = gen_p_max_pu(ctx), gen_p_nom_opt(ctx)
    return None if pu is None or opt is None else pu * opt


def gen_curtailment(ctx: Ctx):
    avail, p = gen_available(ctx), gen_p(ctx)
    if avail is None or p is None:
        return None
    # Clip at zero: tiny LP tolerances put p a hair above availability, and a
    # negative curtailment is not a thing anyone wants to explain.
    return (avail - p).clip(lower=0.0)


def gen_capacity_factor(ctx: Ctx):
    p, opt = gen_p(ctx), gen_p_nom_opt(ctx)
    if p is None or not opt:
        return None
    return p / opt


def gen_status(ctx: Ctx):
    return series_for(ctx, "status")


def gen_start_up(ctx: Ctx):
    return series_for(ctx, "start_up")


def gen_shut_down(ctx: Ctx):
    return series_for(ctx, "shut_down")


def gen_q(ctx: Ctx):
    return series_for(ctx, "q")


# ── dispatch: scalars ───────────────────────────────────────────────────────

def gen_energy(ctx: Ctx):
    return _wsum(ctx, gen_p(ctx))


def gen_full_load_hours(ctx: Ctx):
    e, opt = gen_energy(ctx), gen_p_nom_opt(ctx)
    return None if e is None or not opt else e / opt


def gen_mean_cf(ctx: Ctx):
    e, opt = gen_energy(ctx), gen_p_nom_opt(ctx)
    hours = float(ctx.weights.sum())
    return None if e is None or not opt or not hours else e / (opt * hours)


def gen_curtailed_energy(ctx: Ctx):
    return _wsum(ctx, gen_curtailment(ctx))


def gen_peak(ctx: Ctx):
    p = gen_p(ctx)
    return None if p is None or p.empty else float(p.max())


def gen_zero_hours(ctx: Ctx):
    p = gen_p(ctx)
    return None if p is None else _whours(ctx, p.abs() < 1e-9)


def gen_max_ramp_up(ctx: Ctx):
    p = gen_p(ctx)
    if p is None or len(p) < 2:
        return None
    return float(p.diff().dropna().max())


def gen_max_ramp_down(ctx: Ctx):
    p = gen_p(ctx)
    if p is None or len(p) < 2:
        return None
    return float(p.diff().dropna().min())


def gen_n_starts(ctx: Ctx):
    s = gen_start_up(ctx)
    return None if s is None else int(round(float(s.fillna(0.0).sum())))


# ── Cost weighting ──────────────────────────────────────────────────────────
# Energy figures use the `generators` weighting column (ctx.weights); COST
# figures use `objective`. `/results/asset_economics` draws the same
# distinction, and the reconciliation test in this task depends on matching it.

def cost_weights(ctx: Ctx):
    import pandas as pd

    from services.period_utils import snapshot_weights

    # Same rule as build_ctx: snapshot_weights already folds in
    # investment_period_weightings.years for a MultiIndex `sns`. Multiplying by
    # the years map again here would inflate every cost total by a factor of
    # `years` on a multi-period network.
    try:
        return snapshot_weights(ctx.n, "objective", ctx.sns)
    except Exception:
        return pd.Series(1.0, index=ctx.sns)


def _cost_wsum(ctx: Ctx, s) -> float | None:
    if s is None:
        return None
    return float((s.fillna(0.0) * cost_weights(ctx)).sum())


# ── prices ──────────────────────────────────────────────────────────────────

def price_at_bus(ctx: Ctx, bus: str | None):
    """
    Corrected nodal price series at a NAMED bus, reindexed to ctx.sns.

    MUST go through `corrected_marginal_prices`, which routers/results.py
    documents as the single source of truth for the merit-order correction and
    which `/results/asset_economics` and the Compare tab both price against.
    Reading raw `buses_t.marginal_price` here would make every revenue, cost,
    capture price and congestion rent in this module diverge from the Results
    tab for any network carrying `curtailment_cost > 0`.
    """
    if not bus:
        return None
    from routers.results import corrected_marginal_prices

    try:
        df = corrected_marginal_prices(ctx.n)
    except Exception:
        return None
    if df is None or getattr(df, "empty", True) or bus not in df.columns:
        return None
    return df[bus].reindex(ctx.sns)


def bus_price_at(ctx: Ctx, bus_key: str):
    """Price at whichever bus this asset's `bus_key` column points to."""
    return price_at_bus(ctx, ctx.params.get(bus_key))


def bus_price_series(ctx: Ctx):
    """
    Nodal price at the asset's own bus. `bus` for injecting components,
    `bus0` for branches.
    """
    return price_at_bus(ctx, ctx.params.get("bus") or ctx.params.get("bus0"))


def gen_bus_price(ctx: Ctx):
    return bus_price_series(ctx)


def gen_mu_upper(ctx: Ctx):
    return series_for(ctx, "mu_upper")


def gen_mu_lower(ctx: Ctx):
    return series_for(ctx, "mu_lower")


def gen_capture_price(ctx: Ctx):
    p, lam = gen_p(ctx), bus_price_series(ctx)
    if p is None or lam is None:
        return None
    w = cost_weights(ctx)
    denom = float((p.fillna(0.0) * w).sum())
    if abs(denom) < 1e-9:
        return None
    return float((p.fillna(0.0) * lam.fillna(0.0) * w).sum()) / denom


def gen_capture_rate(ctx: Ctx):
    cap, lam = gen_capture_price(ctx), bus_price_series(ctx)
    if cap is None or lam is None:
        return None
    w = cost_weights(ctx)
    hours = float(w.sum())
    if not hours:
        return None
    mean_price = float((lam.fillna(0.0) * w).sum()) / hours
    return None if abs(mean_price) < 1e-9 else cap / mean_price


def binding_hours(ctx: Ctx):
    """
    Weighted hours where either capacity dual is non-zero, for ANY class that
    has mu_upper / mu_lower (Generator, Link, Line, Transformer, StorageUnit,
    Store — the columns are named identically on all six).
    """
    # No early return on "both series absent": a non-extendable, non-
    # committable asset legitimately has no mu_upper/mu_lower columns at
    # all (PyPSA enforces its dispatch bounds as variable bounds, not linear
    # constraints, so no dual is ever assigned) — that means the bound never
    # bound, i.e. zero binding hours, not "unknown". See
    # test_binding_hours_counts_weighted_snapshots_with_a_nonzero_dual.
    #
    # Weighted via _whours, matching gen_zero_hours / gen_full_load_hours /
    # gen_mean_cf — all four carry unit="h" and sit side by side in the scalar
    # table. A bare `.sum()` of the boolean mask reports SNAPSHOT count, which
    # reads as real hours on any representative-week network (e.g.
    # snapshot_weightings.generators = 52.14): ~84 snapshots would show as
    # "84 h" next to a zero-output-hours KPI correctly reading ~4400 h.
    binding = None
    for s in (series_for(ctx, "mu_upper"), series_for(ctx, "mu_lower")):
        if s is None:
            continue
        b = s.abs() > 1e-9
        binding = b if binding is None else (binding | b)
    if binding is None:
        return 0.0
    return _whours(ctx, binding)


gen_binding_hours = binding_hours


# ── economics ───────────────────────────────────────────────────────────────

def gen_revenue(ctx: Ctx):
    p, lam = gen_p(ctx), bus_price_series(ctx)
    return None if p is None or lam is None else _cost_wsum(ctx, p * lam)


def marginal_cost_of(ctx: Ctx):
    """
    Marginal cost as a Series when a profile was uploaded, a float when only
    the static column exists, None when neither does.

    Time-varying first, static as fallback — the same idiom gen_p_max_pu uses,
    and what /results/asset_economics does via get_switchable_as_dense. A
    static-only read silently diverges from the Results tab for any asset
    carrying a fuel-price profile.
    """
    s = _dense(ctx, "marginal_cost")
    return s if s is not None else _static(ctx, "marginal_cost")


def vom_of(ctx: Ctx, flow):
    """Variable O&M over any throughput series: Σ |flow| × marginal_cost × w."""
    if flow is None:
        return None
    mc = marginal_cost_of(ctx)
    return None if mc is None else _cost_wsum(ctx, flow.abs() * mc)


def gen_vom(ctx: Ctx):
    return vom_of(ctx, gen_p(ctx))


def gen_fixed_cost(ctx: Ctx):
    return gen_capex_annual(ctx)


def gen_net_profit(ctx: Ctx):
    rev, fixed, vom = gen_revenue(ctx), gen_fixed_cost(ctx), gen_vom(ctx)
    if rev is None:
        return None
    return rev - ((fixed or 0.0) + (vom or 0.0))


def gen_lcoe(ctx: Ctx):
    e = gen_energy(ctx)
    if not e:
        return None
    return ((gen_fixed_cost(ctx) or 0.0) + (gen_vom(ctx) or 0.0)) / e


# ── emissions ───────────────────────────────────────────────────────────────

def _co2_intensity_of_carrier(ctx: Ctx) -> float | None:
    carrier = ctx.params.get("carrier")
    carriers = ctx.n.carriers
    if not carrier or carrier not in carriers.index:
        return None
    if "co2_emissions" not in carriers.columns:
        return None
    try:
        return float(carriers.at[carrier, "co2_emissions"] or 0.0)
    except (TypeError, ValueError):
        return None


def gen_co2_rate(ctx: Ctx):
    p = gen_p(ctx)
    factor = _co2_intensity_of_carrier(ctx)
    # `or 1.0` already guarantees `eff` is truthy (falls back to 1.0 whenever
    # the static column is None/0/NaN) — no separate zero-efficiency guard
    # is reachable, so none is written.
    eff = _static(ctx, "efficiency") or 1.0
    if p is None or factor is None:
        return None
    # PyPSA's convention: co2_emissions is per unit of PRIMARY energy, so the
    # electrical output is divided by efficiency to recover fuel input first.
    return p / eff * factor


def gen_co2_total(ctx: Ctx):
    return _wsum(ctx, gen_co2_rate(ctx))


def gen_co2_intensity(ctx: Ctx):
    total, e = gen_co2_total(ctx), gen_energy(ctx)
    return None if total is None or not e else total / e


# ════════════════════════════════════════════════════════════════════════════
# Bus
# ════════════════════════════════════════════════════════════════════════════
# A bus has almost no results of its own — `p`, the three AC PF series and its
# marginal price. Everything else a user wants to know about a node ("how much
# generation sits here, how much load does it serve") is an aggregate over the
# components ATTACHED to it, so these helpers walk the other frames.

def _attached(ctx: Ctx, component_class: str, col: str = "bus") -> list[str]:
    """Names of `component_class` rows whose `col` points at this bus."""
    df = getattr(ctx.n, attr_for(component_class), None)
    if df is None or getattr(df, "empty", True):
        return []
    if col not in getattr(df, "columns", []):
        return []
    return [str(x) for x in df.index[df[col].astype(str) == str(ctx.name)]]


# An aggregate over an EMPTY attachment set is zero, not unknown. A bus with
# no storage attached has 0 MW of storage capacity — that is an answer, and
# returning None instead would downgrade the row to "not produced by this
# solve", which reads as a solver problem rather than as a fact about the
# topology. `None` stays reserved for "the frame or column does not exist",
# which genuinely is unknown.

def _zeros(ctx: Ctx):
    import pandas as pd

    return pd.Series(0.0, index=ctx.sns)


def _sum_series(ctx: Ctx, component_class: str, attr: str, names: list[str]):
    """Σ over `names` of `<class>_t.<attr>`, reindexed to ctx.sns."""
    from routers.results import _result_df

    df = _result_df(ctx.n, f"{attr_for(component_class)}_t", attr, ctx.source)
    if df is None or getattr(df, "empty", True):
        return None
    cols = [c for c in names if c in getattr(df, "columns", [])]
    if not cols:
        return _zeros(ctx)
    return df[cols].sum(axis=1).reindex(ctx.sns).fillna(0.0)


def _sum_dense(ctx: Ctx, component_class: str, col: str, names: list[str]):
    """Σ over `names` of the dense (static-or-profile) input column `col`."""
    if not names:
        return _zeros(ctx)
    try:
        df = ctx.n.get_switchable_as_dense(component_class, col)
    except Exception:
        return None
    if df is None:
        return None
    cols = [c for c in names if c in getattr(df, "columns", [])]
    if not cols:
        return _zeros(ctx)
    return df[cols].sum(axis=1).reindex(ctx.sns).fillna(0.0)


def _sum_static(ctx: Ctx, component_class: str, col: str,
                names: list[str]) -> float | None:
    df = getattr(ctx.n, attr_for(component_class), None)
    if df is None or col not in getattr(df, "columns", []):
        return None
    rows = [x for x in names if x in df.index]
    if not rows:
        return 0.0
    try:
        return float(df.loc[rows, col].astype(float).sum())
    except (TypeError, ValueError):
        return None


# ── Bus: capacity ───────────────────────────────────────────────────────────

def bus_gen_p_nom(ctx: Ctx):
    return _sum_static(ctx, "Generator", "p_nom", _attached(ctx, "Generator"))


def bus_gen_p_nom_opt(ctx: Ctx):
    return _sum_static(ctx, "Generator", "p_nom_opt", _attached(ctx, "Generator"))


def bus_capacity_by_carrier(ctx: Ctx):
    """Optimised generation capacity at this bus, split by carrier."""
    names = _attached(ctx, "Generator")
    df = getattr(ctx.n, "generators", None)
    if not names or df is None or "p_nom_opt" not in getattr(df, "columns", []):
        return None
    rows = [x for x in names if x in df.index]
    if not rows:
        return None
    sub = df.loc[rows]
    carriers = sub["carrier"] if "carrier" in sub.columns else None
    if carriers is None:
        return None
    try:
        grouped = sub["p_nom_opt"].astype(float).groupby(carriers).sum()
    except (TypeError, ValueError):
        return None
    out = {str(k): float(v) for k, v in grouped.items() if float(v) != 0.0}
    return out or None


def bus_storage_p_nom_opt(ctx: Ctx):
    return _sum_static(ctx, "StorageUnit", "p_nom_opt",
                       _attached(ctx, "StorageUnit"))


def bus_store_e_nom_opt(ctx: Ctx):
    return _sum_static(ctx, "Store", "e_nom_opt", _attached(ctx, "Store"))


def bus_load_p_set(ctx: Ctx):
    """Nameplate load at this bus — the mean of the dense p_set profile."""
    return _wmean(ctx, _sum_dense(ctx, "Load", "p_set", _attached(ctx, "Load")))


# ── Bus: dispatch ───────────────────────────────────────────────────────────

def bus_net_injection(ctx: Ctx):
    """`buses_t.p` — positive when the bus is a net exporter."""
    return series_for(ctx, "p")


def bus_generation(ctx: Ctx):
    return _sum_series(ctx, "Generator", "p", _attached(ctx, "Generator"))


def bus_load_series(ctx: Ctx):
    """
    Load at this bus. `loads_t.p` after a solve; the dense `p_set` input
    profile otherwise, so the number is not blank on an unsolved network —
    load is a model INPUT, and hiding it until a solve lands would be wrong.
    """
    names = _attached(ctx, "Load")
    s = _sum_series(ctx, "Load", "p", names)
    return s if s is not None else _sum_dense(ctx, "Load", "p_set", names)


def bus_storage_net(ctx: Ctx):
    """Net storage power at this bus (positive = discharging into the bus)."""
    parts = [
        _sum_series(ctx, "StorageUnit", "p", _attached(ctx, "StorageUnit")),
        _sum_series(ctx, "Store", "p", _attached(ctx, "Store")),
    ]
    present = [s for s in parts if s is not None]
    if not present:
        return None
    total = present[0]
    for s in present[1:]:
        total = total + s
    return total


def bus_net_import(ctx: Ctx):
    """Net power flowing INTO this bus from the rest of the network."""
    p = bus_net_injection(ctx)
    return None if p is None else -p


def bus_generation_mwh(ctx: Ctx):
    return _wsum(ctx, bus_generation(ctx))


def bus_load_mwh(ctx: Ctx):
    return _wsum(ctx, bus_load_series(ctx))


def bus_net_import_mwh(ctx: Ctx):
    return _wsum(ctx, bus_net_import(ctx))


def bus_peak_generation(ctx: Ctx):
    return _smax(bus_generation(ctx))


def bus_peak_load(ctx: Ctx):
    return _smax(bus_load_series(ctx))


def bus_load_factor(ctx: Ctx):
    e, peak = bus_load_mwh(ctx), bus_peak_load(ctx)
    hours = float(ctx.weights.sum())
    return None if not e or not peak or not hours else e / (peak * hours)


def bus_self_sufficiency(ctx: Ctx):
    """Generation ÷ load at this bus — >1 means the node is a net exporter."""
    g, l_ = bus_generation_mwh(ctx), bus_load_mwh(ctx)
    return None if g is None or not l_ else g / l_


# ── Bus: load flow ──────────────────────────────────────────────────────────

def bus_v_min(ctx: Ctx):
    return _smin(series_for(ctx, "v_mag_pu"))


def bus_v_max(ctx: Ctx):
    return _smax(series_for(ctx, "v_mag_pu"))


def bus_v_mean(ctx: Ctx):
    return _wmean(ctx, series_for(ctx, "v_mag_pu"))


# ── Bus: prices ─────────────────────────────────────────────────────────────

def bus_price(ctx: Ctx):
    """This bus IS the pricing node, so look its own name up directly."""
    return price_at_bus(ctx, ctx.name)


def bus_price_mean(ctx: Ctx):
    s = bus_price(ctx)
    if s is None:
        return None
    w = cost_weights(ctx)
    hours = float(w.sum())
    return None if not hours else float((s.fillna(0.0) * w).sum()) / hours


def bus_price_min(ctx: Ctx):
    return _smin(bus_price(ctx))


def bus_price_max(ctx: Ctx):
    return _smax(bus_price(ctx))


def bus_load_weighted_price(ctx: Ctx):
    """What a consumer at this bus actually paid, on average."""
    lam, load = bus_price(ctx), bus_load_series(ctx)
    if lam is None or load is None:
        return None
    w = cost_weights(ctx)
    denom = float((load.fillna(0.0) * w).sum())
    if abs(denom) < 1e-9:
        return None
    return float((load.fillna(0.0) * lam.fillna(0.0) * w).sum()) / denom


# ── Bus: economics ──────────────────────────────────────────────────────────

def bus_load_cost(ctx: Ctx):
    lam, load = bus_price(ctx), bus_load_series(ctx)
    return None if lam is None or load is None else _cost_wsum(ctx, load * lam)


def bus_generation_revenue(ctx: Ctx):
    lam, gen = bus_price(ctx), bus_generation(ctx)
    return None if lam is None or gen is None else _cost_wsum(ctx, gen * lam)


# ════════════════════════════════════════════════════════════════════════════
# Branches — Line, Transformer and Link
# ════════════════════════════════════════════════════════════════════════════
# All three carry p0/p1 and a nominal rating, so flow, loading, losses and
# congestion are the same arithmetic on all three; only the rating column
# differs and `nom_capacity_opt` already handles that. A Link's rating is in
# MW and a Line's in MVA — the registry labels each metric with its own unit,
# so nothing here needs to know which.

_LOADED = 0.99   # loading fraction at which a branch counts as congested


def br_loading(ctx: Ctx):
    """|p0| as a percentage of the optimised rating."""
    p0, s_nom = series_for(ctx, "p0"), nom_capacity_opt(ctx)
    if p0 is None or not s_nom:
        return None
    return p0.abs() / s_nom * 100.0


def br_losses(ctx: Ctx):
    """
    p0 + p1. PyPSA signs both ends as "power INTO the branch", so on a lossy
    branch the pair does not cancel and the residual is the loss.
    """
    p0, p1 = series_for(ctx, "p0"), series_for(ctx, "p1")
    return None if p0 is None or p1 is None else p0 + p1


def br_max_loading(ctx: Ctx):
    return _smax(br_loading(ctx))


def br_mean_loading(ctx: Ctx):
    return _wmean(ctx, br_loading(ctx))


def br_congested_hours(ctx: Ctx):
    load = br_loading(ctx)
    return None if load is None else _whours(ctx, load >= _LOADED * 100.0)


def br_losses_mwh(ctx: Ctx):
    return _wsum(ctx, br_losses(ctx))


def br_gross_transfer_mwh(ctx: Ctx):
    p0 = series_for(ctx, "p0")
    return None if p0 is None else _wsum(ctx, p0.abs())


def br_net_transfer_mwh(ctx: Ctx):
    """Signed: positive means net flow ran bus0 → bus1 over the horizon."""
    return _wsum(ctx, series_for(ctx, "p0"))


def br_peak_flow(ctx: Ctx):
    p0 = series_for(ctx, "p0")
    return None if p0 is None else _smax(p0.abs())


def br_reverse_hours(ctx: Ctx):
    p0 = series_for(ctx, "p0")
    return None if p0 is None else _whours(ctx, p0 < -1e-9)


def br_idle_hours(ctx: Ctx):
    p0 = series_for(ctx, "p0")
    return None if p0 is None else _whours(ctx, p0.abs() < 1e-9)


def br_loss_rate(ctx: Ctx):
    losses, gross = br_losses_mwh(ctx), br_gross_transfer_mwh(ctx)
    return None if losses is None or not gross else losses / gross


def br_utilisation(ctx: Ctx):
    """Gross transfer ÷ what the branch could have carried flat out."""
    gross, s_nom = br_gross_transfer_mwh(ctx), nom_capacity_opt(ctx)
    hours = float(ctx.weights.sum())
    return None if gross is None or not s_nom or not hours else \
        gross / (s_nom * hours)


# ── Branch: prices ──────────────────────────────────────────────────────────

def br_price0(ctx: Ctx):
    return bus_price_at(ctx, "bus0")


def br_price1(ctx: Ctx):
    return bus_price_at(ctx, "bus1")


def br_price_spread(ctx: Ctx):
    """λ(bus1) − λ(bus0). Non-zero exactly when the branch is binding."""
    a, b = br_price0(ctx), br_price1(ctx)
    return None if a is None or b is None else b - a


def br_mean_spread(ctx: Ctx):
    return _wmean(ctx, br_price_spread(ctx))


def br_congestion_rent(ctx: Ctx):
    """
    Σ p0 × (λ1 − λ0) × w — the rent the branch earns by moving power from the
    cheap end to the expensive one. Positive whenever flow runs downhill in
    price, which is what an optimal dispatch produces.
    """
    p0, spread = series_for(ctx, "p0"), br_price_spread(ctx)
    return None if p0 is None or spread is None else _cost_wsum(ctx, p0 * spread)


def br_fixed_cost(ctx: Ctx):
    return capex_annual(ctx)


# ════════════════════════════════════════════════════════════════════════════
# Link
# ════════════════════════════════════════════════════════════════════════════
# A link both dispatches (it is controllable) and carries flow (it is a
# branch), so it declares metrics in BOTH categories: throughput, efficiency
# and commitment under Dispatch; loading and congestion under Load flow.

def link_output(ctx: Ctx):
    """
    −p1: power actually delivered at bus1. PyPSA signs p1 negative when the
    link injects, which reads backwards on a chart next to p0.
    """
    p1 = series_for(ctx, "p1")
    return None if p1 is None else -p1


def link_energy_in(ctx: Ctx):
    return _wsum(ctx, series_for(ctx, "p0"))


def link_energy_out(ctx: Ctx):
    return _wsum(ctx, link_output(ctx))


def link_losses_mwh(ctx: Ctx):
    return _wsum(ctx, br_losses(ctx))


def link_mean_efficiency(ctx: Ctx):
    """
    Realised efficiency over the horizon: energy out ÷ energy in. Differs from
    the static `efficiency` parameter whenever that parameter is time-varying.
    """
    e_in, e_out = link_energy_in(ctx), link_energy_out(ctx)
    return None if e_out is None or not e_in else e_out / e_in


def link_full_load_hours(ctx: Ctx):
    e_in, opt = link_energy_in(ctx), nom_capacity_opt(ctx)
    return None if e_in is None or not opt else e_in / opt


def link_mean_capacity_factor(ctx: Ctx):
    gross, opt = br_gross_transfer_mwh(ctx), nom_capacity_opt(ctx)
    hours = float(ctx.weights.sum())
    return None if gross is None or not opt or not hours else \
        gross / (opt * hours)


def link_max_ramp_up(ctx: Ctx):
    p0 = series_for(ctx, "p0")
    return None if p0 is None or len(p0) < 2 else float(p0.diff().dropna().max())


def link_max_ramp_down(ctx: Ctx):
    p0 = series_for(ctx, "p0")
    return None if p0 is None or len(p0) < 2 else float(p0.diff().dropna().min())


def link_n_starts(ctx: Ctx):
    s = series_for(ctx, "start_up")
    return None if s is None else int(round(float(s.fillna(0.0).sum())))


def link_input_cost(ctx: Ctx):
    p0, lam = series_for(ctx, "p0"), br_price0(ctx)
    return None if p0 is None or lam is None else _cost_wsum(ctx, p0 * lam)


def link_output_revenue(ctx: Ctx):
    out, lam = link_output(ctx), br_price1(ctx)
    return None if out is None or lam is None else _cost_wsum(ctx, out * lam)


def link_vom(ctx: Ctx):
    return vom_of(ctx, series_for(ctx, "p0"))


def link_net_profit(ctx: Ctx):
    rev = link_output_revenue(ctx)
    if rev is None:
        return None
    return rev - ((link_input_cost(ctx) or 0.0) + (link_vom(ctx) or 0.0)
                  + (capex_annual(ctx) or 0.0))


def link_co2_rate(ctx: Ctx):
    """
    p0 × carrier.co2_emissions. p0 is the PRIMARY energy the link withdraws,
    which is exactly the basis PyPSA defines co2_emissions on — so unlike the
    Generator case there is no efficiency division to undo here.
    """
    p0, factor = series_for(ctx, "p0"), _co2_intensity_of_carrier(ctx)
    return None if p0 is None or factor is None else p0 * factor


def link_co2_total(ctx: Ctx):
    return _wsum(ctx, link_co2_rate(ctx))


def link_co2_intensity(ctx: Ctx):
    """Per unit of DELIVERED energy — the number a user compares to a plant."""
    total, e_out = link_co2_total(ctx), link_energy_out(ctx)
    return None if total is None or not e_out else total / e_out


# ════════════════════════════════════════════════════════════════════════════
# StorageUnit
# ════════════════════════════════════════════════════════════════════════════
# PyPSA splits a storage unit's power into p_dispatch (out) and p_store (in),
# both non-negative, with `p = p_dispatch − p_store`. Charge and discharge are
# therefore separate metrics, not one signed series read two ways.

def su_energy_capacity(ctx: Ctx):
    """p_nom_opt × max_hours — the usable reservoir, in MWh."""
    opt, max_hours = nom_capacity_opt(ctx), _static(ctx, "max_hours")
    return None if opt is None or max_hours is None else opt * max_hours


def su_soc_pu(ctx: Ctx):
    soc, cap = series_for(ctx, "state_of_charge"), su_energy_capacity(ctx)
    return None if soc is None or not cap else soc / cap


def su_energy_discharged(ctx: Ctx):
    return _wsum(ctx, series_for(ctx, "p_dispatch"))


def su_energy_charged(ctx: Ctx):
    return _wsum(ctx, series_for(ctx, "p_store"))


def su_spilled(ctx: Ctx):
    return _wsum(ctx, series_for(ctx, "spill"))


def su_round_trip_efficiency(ctx: Ctx):
    """
    Realised, not nameplate: energy out ÷ energy in over the horizon. Includes
    standing losses and any spillage, so it sits at or below
    efficiency_store × efficiency_dispatch.
    """
    out, in_ = su_energy_discharged(ctx), su_energy_charged(ctx)
    return None if out is None or not in_ else out / in_


def su_full_cycles(ctx: Ctx):
    """Equivalent full cycles: energy discharged ÷ reservoir size."""
    out, cap = su_energy_discharged(ctx), su_energy_capacity(ctx)
    return None if out is None or not cap else out / cap


def su_soc_min(ctx: Ctx):
    return _smin(series_for(ctx, "state_of_charge"))


def su_soc_max(ctx: Ctx):
    return _smax(series_for(ctx, "state_of_charge"))


def su_soc_mean(ctx: Ctx):
    return _wmean(ctx, series_for(ctx, "state_of_charge"))


def su_depth_of_discharge(ctx: Ctx):
    """How much of the reservoir the schedule actually swings through."""
    hi, lo, cap = su_soc_max(ctx), su_soc_min(ctx), su_energy_capacity(ctx)
    return None if hi is None or lo is None or not cap else (hi - lo) / cap


def su_peak_discharge(ctx: Ctx):
    return _smax(series_for(ctx, "p_dispatch"))


def su_peak_charge(ctx: Ctx):
    return _smax(series_for(ctx, "p_store"))


def su_discharge_hours(ctx: Ctx):
    s = series_for(ctx, "p_dispatch")
    return None if s is None else _whours(ctx, s > 1e-9)


def su_charge_hours(ctx: Ctx):
    s = series_for(ctx, "p_store")
    return None if s is None else _whours(ctx, s > 1e-9)


def su_idle_hours(ctx: Ctx):
    p = series_for(ctx, "p")
    return None if p is None else _whours(ctx, p.abs() < 1e-9)


def su_max_ramp_up(ctx: Ctx):
    p = series_for(ctx, "p")
    return None if p is None or len(p) < 2 else float(p.diff().dropna().max())


def su_max_ramp_down(ctx: Ctx):
    p = series_for(ctx, "p")
    return None if p is None or len(p) < 2 else float(p.diff().dropna().min())


def _flow_weighted_price(ctx: Ctx, flow):
    """Σ flow·λ·w ÷ Σ flow·w — the price a given flow actually transacted at."""
    lam = bus_price_series(ctx)
    if flow is None or lam is None:
        return None
    w = cost_weights(ctx)
    denom = float((flow.fillna(0.0) * w).sum())
    if abs(denom) < 1e-9:
        return None
    return float((flow.fillna(0.0) * lam.fillna(0.0) * w).sum()) / denom


def su_capture_price(ctx: Ctx):
    return _flow_weighted_price(ctx, series_for(ctx, "p_dispatch"))


def su_charge_price(ctx: Ctx):
    return _flow_weighted_price(ctx, series_for(ctx, "p_store"))


def su_captured_spread(ctx: Ctx):
    """What arbitrage actually earned per MWh cycled, before losses."""
    sell, buy = su_capture_price(ctx), su_charge_price(ctx)
    return None if sell is None or buy is None else sell - buy


def su_revenue(ctx: Ctx):
    lam, out = bus_price_series(ctx), series_for(ctx, "p_dispatch")
    return None if lam is None or out is None else _cost_wsum(ctx, out * lam)


def su_charging_cost(ctx: Ctx):
    lam, in_ = bus_price_series(ctx), series_for(ctx, "p_store")
    return None if lam is None or in_ is None else _cost_wsum(ctx, in_ * lam)


def su_vom(ctx: Ctx):
    return vom_of(ctx, series_for(ctx, "p"))


def su_net_profit(ctx: Ctx):
    rev = su_revenue(ctx)
    if rev is None:
        return None
    return rev - ((su_charging_cost(ctx) or 0.0) + (su_vom(ctx) or 0.0)
                  + (capex_annual(ctx) or 0.0))


def su_lcos(ctx: Ctx):
    """Levelised cost of storage: all costs ÷ energy actually delivered."""
    out = su_energy_discharged(ctx)
    if not out:
        return None
    return ((capex_annual(ctx) or 0.0) + (su_vom(ctx) or 0.0)
            + (su_charging_cost(ctx) or 0.0)) / out


# ════════════════════════════════════════════════════════════════════════════
# Store
# ════════════════════════════════════════════════════════════════════════════
# A Store has one signed power series (`p`, positive when discharging into the
# bus) and an energy level `e`. Charge and discharge are recovered by clipping.

def store_discharge(ctx: Ctx):
    p = series_for(ctx, "p")
    return None if p is None else p.clip(lower=0.0)


def store_charge(ctx: Ctx):
    p = series_for(ctx, "p")
    return None if p is None else (-p).clip(lower=0.0)


def store_e_pu(ctx: Ctx):
    e, cap = series_for(ctx, "e"), nom_capacity_opt(ctx)
    return None if e is None or not cap else e / cap


def store_energy_out(ctx: Ctx):
    return _wsum(ctx, store_discharge(ctx))


def store_energy_in(ctx: Ctx):
    return _wsum(ctx, store_charge(ctx))


def store_e_min(ctx: Ctx):
    return _smin(series_for(ctx, "e"))


def store_e_max(ctx: Ctx):
    return _smax(series_for(ctx, "e"))


def store_e_mean(ctx: Ctx):
    return _wmean(ctx, series_for(ctx, "e"))


def store_full_cycles(ctx: Ctx):
    out, cap = store_energy_out(ctx), nom_capacity_opt(ctx)
    return None if out is None or not cap else out / cap


def store_round_trip_efficiency(ctx: Ctx):
    out, in_ = store_energy_out(ctx), store_energy_in(ctx)
    return None if out is None or not in_ else out / in_


def store_net_absorbed_mwh(ctx: Ctx):
    """
    Energy charged minus energy discharged over the horizon.

    By the store's energy balance this equals the change in stored level plus
    any standing loss, so on a CYCLIC store over the full horizon — where the
    level change is zero by construction — it is exactly the standing loss.
    On a non-cyclic store, or a sliced horizon, it also carries whatever the
    reservoir gained or gave up across the window.

    Deliberately NOT reported as "standing loss" with the level change
    subtracted out. Doing that needs the level at the snapshot BEFORE the
    first one in the slice: `e[first]` already has one timestep of flow baked
    into it (and under PyPSA's cyclic wrap the pre-horizon level IS
    `e[last]`), so `e[last] − e[first]` undercounts by exactly one timestep
    and produced a NEGATIVE standing loss on a lossless cyclic store. A
    quantity that is always correct beats a more specific one that is not.
    """
    out, in_ = store_energy_out(ctx), store_energy_in(ctx)
    return None if out is None or in_ is None else in_ - out


def store_peak_discharge(ctx: Ctx):
    return _smax(store_discharge(ctx))


def store_peak_charge(ctx: Ctx):
    return _smax(store_charge(ctx))


def store_capture_price(ctx: Ctx):
    return _flow_weighted_price(ctx, store_discharge(ctx))


def store_charge_price(ctx: Ctx):
    return _flow_weighted_price(ctx, store_charge(ctx))


def store_revenue(ctx: Ctx):
    lam, out = bus_price_series(ctx), store_discharge(ctx)
    return None if lam is None or out is None else _cost_wsum(ctx, out * lam)


def store_charging_cost(ctx: Ctx):
    lam, in_ = bus_price_series(ctx), store_charge(ctx)
    return None if lam is None or in_ is None else _cost_wsum(ctx, in_ * lam)


def store_vom(ctx: Ctx):
    return vom_of(ctx, series_for(ctx, "p"))


def store_net_profit(ctx: Ctx):
    rev = store_revenue(ctx)
    if rev is None:
        return None
    return rev - ((store_charging_cost(ctx) or 0.0) + (store_vom(ctx) or 0.0)
                  + (capex_annual(ctx) or 0.0))


# ════════════════════════════════════════════════════════════════════════════
# Load
# ════════════════════════════════════════════════════════════════════════════

def load_profile(ctx: Ctx):
    """
    `loads_t.p` after a solve, the dense `p_set` input otherwise. Load is a
    model input — a user opening an unsolved network should still see it.
    """
    s = series_for(ctx, "p")
    return s if s is not None else _dense(ctx, "p_set")


def load_energy(ctx: Ctx):
    return _wsum(ctx, load_profile(ctx))


def load_peak(ctx: Ctx):
    return _smax(load_profile(ctx))


def load_min(ctx: Ctx):
    return _smin(load_profile(ctx))


def load_mean(ctx: Ctx):
    return _wmean(ctx, load_profile(ctx))


def load_factor(ctx: Ctx):
    e, peak = load_energy(ctx), load_peak(ctx)
    hours = float(ctx.weights.sum())
    return None if not e or not peak or not hours else e / (peak * hours)


def load_price_paid(ctx: Ctx):
    return _flow_weighted_price(ctx, load_profile(ctx))


def load_cost(ctx: Ctx):
    lam, p = bus_price_series(ctx), load_profile(ctx)
    return None if lam is None or p is None else _cost_wsum(ctx, p * lam)
