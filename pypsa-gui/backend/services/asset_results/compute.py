"""
Per-metric computation. One function per metric.

Every function takes a single `Ctx` and returns either a pandas Series aligned
to `ctx.sns` (series metrics) or a JSON-ready scalar/dict (scalar metrics). No
HTTP, no serialisation, no NaN scrubbing — the router owns all three.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from services.dispatch_status import dispatch_status as _dispatch_status
from services.period_utils import is_multi_period, snapshot_weights

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
        REQ_CO2,
        REQ_COMMITTABLE,
        REQ_DISPATCH,
        REQ_DUALS,
        REQ_NOT_YET,
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

    # Phase 2/3 placeholder — never actionable.
    out[REQ_NOT_YET] = Status(
        "na", "not yet available — arrives in a later phase of this feature")
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


# ── capacity ────────────────────────────────────────────────────────────────

def gen_p_nom(ctx: Ctx):
    return _static(ctx, "p_nom")


def gen_p_nom_opt(ctx: Ctx):
    return _static(ctx, "p_nom_opt")


def gen_p_nom_delta(ctx: Ctx):
    opt, nom = gen_p_nom_opt(ctx), gen_p_nom(ctx)
    return None if opt is None or nom is None else opt - nom


def gen_capex_annual(ctx: Ctx):
    cc, opt = _static(ctx, "capital_cost"), gen_p_nom_opt(ctx)
    return None if cc is None or opt is None else cc * opt


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
    if p is None:
        return None
    return float(((p.abs() < 1e-9).astype(float) * ctx.weights).sum())


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


def not_yet(*_a: Any, **_k: Any) -> None:
    """Phase 2/3 placeholder metric — never invoked (always resolves `na`)."""
    return None


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

def bus_price_series(ctx: Ctx):
    """
    Nodal price at the asset's own bus. `bus` for injecting components,
    `bus0` for branches — Phase 1 only needs the former.
    """
    bus = ctx.params.get("bus") or ctx.params.get("bus0")
    if not bus:
        return None
    from routers.results import _result_df

    df = _result_df(ctx.n, "buses_t", "marginal_price", ctx.source)
    if df is None or getattr(df, "empty", True) or bus not in df.columns:
        return None
    return df[bus].reindex(ctx.sns)


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


def gen_binding_hours(ctx: Ctx):
    # No early return on "both series absent": a non-extendable, non-
    # committable generator legitimately has no mu_upper/mu_lower columns at
    # all (PyPSA enforces its dispatch bounds as variable bounds, not linear
    # constraints, so no dual is ever assigned) — that means the bound never
    # bound, i.e. zero binding hours, not "unknown". See
    # test_binding_hours_counts_snapshots_with_a_nonzero_dual.
    up, lo = gen_mu_upper(ctx), gen_mu_lower(ctx)
    binding = None
    for s in (up, lo):
        if s is None:
            continue
        b = s.abs() > 1e-9
        binding = b if binding is None else (binding | b)
    return float(binding.astype(float).sum()) if binding is not None else 0.0


# ── economics ───────────────────────────────────────────────────────────────

def gen_revenue(ctx: Ctx):
    p, lam = gen_p(ctx), bus_price_series(ctx)
    return None if p is None or lam is None else _cost_wsum(ctx, p * lam)


def gen_vom(ctx: Ctx):
    p, mc = gen_p(ctx), _static(ctx, "marginal_cost")
    return None if p is None or mc is None else _cost_wsum(ctx, p.abs() * mc)


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
    eff = _static(ctx, "efficiency") or 1.0
    if p is None or factor is None or not eff:
        return None
    # PyPSA's convention: co2_emissions is per unit of PRIMARY energy, so the
    # electrical output is divided by efficiency to recover fuel input first.
    return p / eff * factor


def gen_co2_total(ctx: Ctx):
    return _wsum(ctx, gen_co2_rate(ctx))


def gen_co2_intensity(ctx: Ctx):
    total, e = gen_co2_total(ctx), gen_energy(ctx)
    return None if total is None or not e else total / e
