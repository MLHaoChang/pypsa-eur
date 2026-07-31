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


def _todo(*_a: Any, **_k: Any):  # replaced one-by-one as Tasks 3-4 land
    raise NotImplementedError


def not_yet(*_a: Any, **_k: Any) -> None:
    """Phase 2/3 placeholder metric — never invoked (always resolves `na`)."""
    return None


gen_p_nom = gen_p_nom_opt = gen_p_nom_delta = gen_capex_annual = _todo
gen_p_nom_by_vintage = _todo
gen_p = gen_p_max_pu = gen_available = gen_curtailment = _todo
gen_capacity_factor = gen_status = gen_start_up = gen_shut_down = _todo
gen_energy = gen_full_load_hours = gen_mean_cf = gen_curtailed_energy = _todo
gen_peak = gen_zero_hours = gen_max_ramp_up = gen_max_ramp_down = _todo
gen_n_starts = gen_q = _todo
gen_bus_price = gen_mu_upper = gen_mu_lower = _todo
gen_capture_price = gen_capture_rate = gen_binding_hours = _todo
gen_revenue = gen_vom = gen_fixed_cost = gen_net_profit = gen_lcoe = _todo
gen_co2_rate = gen_co2_total = gen_co2_intensity = _todo
