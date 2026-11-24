# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""The CO2 limit design goal (DC template review 02): "stay under X t CO2 a year".

The goal is written, for one solve only, as PyPSA's own CO2 cap: one
``GlobalConstraint`` per investment period, type ``primary_energy`` on the
carrier attribute ``co2_emissions``, sense ``<=``, named ``co2_cap`` (a
single-period solve) or ``co2_cap_<period>``. ``fuel_curve.co2_caps`` finds
these rows by type, so its refusal of a fuel-curve generator still applies.

PyPSA's row counts each generator at ``weight(t) × years(p) × co2 / efficiency``
(``pypsa/optimization/global_constraints.py``), so:

* the constant is ``limit × years(p) × Σ_{t∈p} weight(t) / 8760``: the limit is
  per year, and the snapshots may model less (or more) than a year;
* a generator with a per-snapshot intensity (``services.co2_override``, the
  staged grid's 0.35 / 0.30 / 0.22) would be counted at its carrier's flat
  figure. ``wrap_lp`` rebuilds the named row with that generator's terms at
  ``weight(t) × years(p) × intensity(t)``, read from the one shared helper, so
  the cap, the Emissions views and the CO2 price count the same tonnes. Every
  other term is left as PyPSA wrote it.

The rows exist only during the solve: ``apply`` adds them and returns the undo
that, before removing them, stores each row's dual in ``captured["co2_cap"]``
(the network's own frames are put back as they were). A cap the user wrote by
hand is left alone and the goal is refused next to it (``co2_cap_duplicate``).

A confirmed-infeasible solve under the goal is explained by one extra LP:
``unreachable`` drops the cap rows from the solved model, minimises the same
weighted emissions and names the least emissions reachable per period.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from services.co2_override import has_override, override_series

ROW = "co2_cap"
CARRIER_ATTRIBUTE = "co2_emissions"
HOURS_PER_YEAR = 8760.0
LABEL = "CO₂ limit"
UNREACHABLE = "co2_cap_unreachable"
DUPLICATE = "co2_cap_duplicate"
# The facts the rows were built from, kept on the network while they exist.
_BUILT = "_co2_cap_rows"
# The rows' LHS the last LP built, for `unreachable`'s extra LP.
_LHS = "_co2_cap_lhs"


class Co2CapRefusal(ValueError):
    """The goal cannot be written on this network; ``code`` names why."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def row_name(period: Any) -> str:
    """The cap row of one investment period (None: the single-period row)."""
    return ROW if period is None else f"{ROW}_{period}"


def target_of(cfg: Any) -> float | None:
    """The configured limit in t CO2 per year, or None when off. 0 is a limit
    (no emissions at all); a negative, NaN or infinite value is off (the
    schema refuses them, so only a hand-made config can carry one)."""
    try:
        v = getattr(cfg, "co2_limit_t_per_yr", None)
        v = None if v is None else float(v)
    except (TypeError, ValueError):
        return None
    if v is None or not math.isfinite(v) or v < 0.0:
        return None
    return v


def _multi(n, cfg) -> bool:
    return bool(getattr(cfg, "multi_investment_periods", False)) and isinstance(
        n.snapshots, pd.MultiIndex
    )


def _years(n, period) -> float:
    try:
        return float(n.investment_period_weightings.years.loc[period])
    except (KeyError, AttributeError):
        return 1.0


def year_fraction(n, period: Any = None) -> float:
    """The share of a year the snapshots of ``period`` (all, when None) stand
    for: Σ generator weight / 8760."""
    w = n.snapshot_weightings.generators
    if period is not None:
        w = w.loc[w.index.get_level_values(0) == period]
    return float(w.sum()) / HOURS_PER_YEAR


def row_scale(n, period: Any) -> float:
    """T in the cap row per t/yr of the limit: years(p) × year_fraction(p) for a
    period's row, year_fraction for the single-period row."""
    if period is None:
        return year_fraction(n)
    return _years(n, period) * year_fraction(n, period)


def periods(n, cfg) -> list:
    """The periods the goal writes a row for: [None] for a single-period solve."""
    if not _multi(n, cfg):
        return [None]
    return list(pd.Index(n.snapshots.get_level_values(0)).unique())


def apply(n, cfg, phase, captured: dict):
    """Add the goal's rows; return the undo (None when the goal is off).

    Raises ``Co2CapRefusal(DUPLICATE)`` when the network already carries a CO2
    cap: two caps would make the binding one depend on which the user wrote."""
    limit = target_of(cfg)
    if limit is None:
        return None
    from services import fuel_curve

    existing = fuel_curve.co2_caps(n)
    if existing:
        raise Co2CapRefusal(
            DUPLICATE,
            f"{LABEL}: the network already has a CO₂ cap ({', '.join(map(str, existing))}). "
            f"Remove that constraint or clear the limit in the Goal step.",
        )
    # The fuel curve guard (homer-parity 02a) reads the caps as the LP chain is
    # built, before these rows exist: run it here against them.
    names = [row_name(p) for p in periods(n, cfg)]
    refusals = [
        f"{g}: {msg}"
        for g in n.generators.index
        if (msg := fuel_curve.co2_cap_refusal(n, g, names))
    ]
    if refusals:
        raise fuel_curve.FuelCurveError("; ".join(refusals))
    gc = n.c.global_constraints
    saved = gc.static.copy()
    built: dict[str, dict] = {}
    for p in periods(n, cfg):
        scale = row_scale(n, p)
        name = row_name(p)
        n.add(
            "GlobalConstraint",
            name,
            type="primary_energy",
            carrier_attribute=CARRIER_ATTRIBUTE,
            sense="<=",
            constant=limit * scale,
            investment_period=np.nan if p is None else p,
        )
        built[name] = {
            "period": p,
            "scale": scale,
            "years": 1.0 if p is None else _years(n, p),
            "constant": limit * scale,
        }
    setattr(n, _BUILT, {"limit": limit, "rows": built})
    phase(f"{LABEL}: {_fmt(limit)} as {len(built)} cap row(s) ({', '.join(built)}).")

    def undo() -> None:
        captured["co2_cap"] = capture(n, limit, built)
        gc.static = saved
        try:
            delattr(n, _BUILT)
        except AttributeError:
            pass

    return undo


def _objective_weight(n, period) -> float:
    try:
        return float(n.investment_period_weightings.objective.loc[period])
    except (KeyError, AttributeError):
        return 1.0


def shadow_price(n, period, mu) -> float | None:
    """EUR per t CO2, undiscounted, positive when tightening the cap costs money:
    the row's dual is in objective EUR per t of the row, and the objective
    discounts a period's operating cost by objective(p) / years(p)."""
    if mu is None or not math.isfinite(mu):
        return None
    if period is None:
        return -mu
    obj = _objective_weight(n, period)
    if obj <= 0:
        return None
    return -mu * _years(n, period) / obj


def capture(n, limit: float, built: dict) -> dict:
    """The goal's result while its rows still exist: the limit, and per row its
    constant, dual (already rescaled for the objective scale) and EUR/t.
    Whether a row bound is judged with the tonnes used (`cap_entries`)."""
    gc = n.c.global_constraints.static
    rows = []
    for name, b in built.items():
        mu = None
        if name in gc.index and "mu" in gc.columns:
            v = gc.at[name, "mu"]
            mu = float(v) if v is not None and math.isfinite(float(v)) else None
        price = shadow_price(n, b["period"], mu)
        rows.append(
            {
                "name": name,
                "period": None if b["period"] is None else int(b["period"]),
                "cap_t": b["constant"],
                "row_scale": b["scale"],
                "mu": mu,
                "shadow_price_eur_per_t": price,
            }
        )
    return {"limit_t_per_yr": limit, "rows": rows}


# ── the LP row ────────────────────────────────────────────────────────────


def _period_snapshots(sns, period):
    if period is None:
        return sns
    if not isinstance(sns, pd.MultiIndex) or period not in sns.unique("period"):
        return None
    return sns[sns.get_level_values(0) == period]


def _flat_t_per_mwh(n, g, sns_p) -> pd.Series:
    """What PyPSA counts for ``g`` per MWh el without an override: its
    carrier's co2_emissions / efficiency(t)."""
    co2 = float(
        n.c.carriers.static[CARRIER_ATTRIBUTE].get(n.generators.at[g, "carrier"], 0.0)
        or 0.0
    )
    eff = n.get_switchable_as_dense("Generator", "efficiency", inds=[g]).loc[sns_p, g]
    return (co2 / eff).astype(float)


def target_coefficients(n, g, period, sns_p) -> pd.Series:
    """The cap row's coefficient on ``p[g, t]`` for every t of the period:
    weight(t) × years(p) × intensity(t), the intensity from the shared helper,
    a snapshot it has no figure for at the carrier's own t/MWh el."""
    w = n.snapshot_weightings.generators.loc[sns_p].astype(float)
    if period is not None:
        w = w * _years(n, period)
    ovr = override_series(n, g, np.nan, index=sns_p)
    ovr = ovr.fillna(_flat_t_per_mwh(n, g, sns_p))
    return w * ovr


def _row_coefficients(lhs, labels: np.ndarray) -> np.ndarray:
    """The coefficient the linear expression carries on each variable label
    (0 where it has none); repeated terms are summed."""
    flat = lhs.flat
    cur = flat.groupby("vars")["coeffs"].sum()
    return pd.Series(labels).map(cur).fillna(0.0).to_numpy(dtype=float)


def rebuild_rows(n, sns) -> dict:
    """Rebuild each of the goal's rows in ``n.model`` so a generator with a
    per-snapshot intensity counts at it; return {row name: its LHS}."""
    built = getattr(n, _BUILT, None)
    if not built:
        return {}
    m = n.model
    gens = [g for g in n.generators.index if has_override(n, g)]
    out = {}
    for name, b in built["rows"].items():
        cname = f"GlobalConstraint-{name}"
        if cname not in m.constraints:
            continue
        con = m.constraints[cname]
        lhs = con.lhs
        sns_p = _period_snapshots(sns, b["period"])
        if gens and sns_p is not None and len(sns_p):
            p = m["Generator-p"]
            for g in gens:
                pg = p.sel(name=g, snapshot=sns_p)
                labels = np.asarray(pg.labels.values).ravel()
                want = target_coefficients(n, g, b["period"], sns_p).to_numpy()
                delta = want - _row_coefficients(lhs, labels)
                if np.any(delta != 0.0):
                    lhs = lhs + (pg * delta).sum()
            rhs = float(np.asarray(con.rhs.values).ravel()[0])
            m.remove_constraints(cname)
            m.add_constraints(lhs, "<=", rhs, name=cname)
        out[name] = lhs
    return out


def wrap_lp(n, extra_fn, cfg, log_queue=None):
    """``extra_fn`` followed by the rebuild of the goal's rows; ``extra_fn``
    unchanged when the goal is off."""
    if target_of(cfg) is None:
        return extra_fn

    def _fn(net, sns):
        if extra_fn is not None:
            extra_fn(net, sns)
        lhs = rebuild_rows(net, sns)
        setattr(net, _LHS, lhs)

    return _fn


def take_lhs(n) -> dict:
    """The rows' LHS the last LP built (and forget them)."""
    lhs = getattr(n, _LHS, None) or {}
    try:
        delattr(n, _LHS)
    except AttributeError:
        pass
    return lhs


# ── unreachable ───────────────────────────────────────────────────────────


def _fmt(t: float) -> str:
    """Tonnes a year as the app writes them (en-US grouping, `fmtCo2PerYear`)."""
    return f"{t:,.0f} t CO₂/yr"


def _finite(v) -> float | None:
    return float(v) if v is not None and math.isfinite(float(v)) else None


def unreachable(
    n, result: dict, lhs: dict, solver_name: str, solver_options=None
) -> dict:
    """The typed refusal of a confirmed-infeasible solve under the goal: one
    extra LP on the solved model without the cap rows, minimising the same
    weighted emissions; the least reachable per period is its row value over
    the row scale. ``least_t_per_yr`` is None when it could not be found, and
    ``without_limit`` says why: "infeasible" (no design works without the
    limit either), else "not_found" (the extra LP could not run or stopped)."""
    limit = result["limit_t_per_yr"]
    rows = result["rows"]
    least: dict | None = None
    without = "not_found"
    m = getattr(n, "model", None)
    try:
        if m is not None and lhs and all(r["name"] in lhs for r in rows):
            obj = None
            for r in rows:
                obj = lhs[r["name"]] if obj is None else obj + lhs[r["name"]]
            for r in rows:
                cname = f"GlobalConstraint-{r['name']}"
                if cname in m.constraints:
                    m.remove_constraints(cname)
            m.add_objective(obj, overwrite=True)
            status, condition = m.solve(
                solver_name=solver_name, **(solver_options or {})
            )
            if str(condition) == "infeasible":
                without = "infeasible"
            elif status == "ok" and str(condition) == "optimal":
                least = {}
                for r in rows:
                    val = float(np.asarray(lhs[r["name"]].solution.sum()).item())
                    key = str(r["period"]) if r["period"] is not None else "all"
                    least[key] = (
                        _finite(val / r["row_scale"]) if r["row_scale"] > 0 else None
                    )
    except Exception:  # noqa: BLE001 - the refusal still names the limit
        least = None
    if least is not None and any(v is not None for v in least.values()):
        parts = [
            f"{_fmt(v)}" + ("" if k == "all" else f" in {k}")
            for k, v in least.items()
            if v is not None
        ]
        message = (
            f"{LABEL}: no design meets {_fmt(limit)}. The least reachable is "
            f"{' and '.join(parts)}."
        )
        without = "feasible"
    elif without == "infeasible":
        least = None
        message = (
            f"{LABEL}: no design meets {_fmt(limit)}. No design works without "
            f"the limit either, so the limit is not the cause."
        )
    else:
        least = None
        message = (
            f"{LABEL}: no design meets {_fmt(limit)}. The least emissions "
            f"reachable could not be found."
        )
    return {
        "code": UNREACHABLE,
        "message": message,
        "limit_t_per_yr": limit,
        "least_t_per_yr": least,
        "without_limit": without,
        "extra_solves": 1,
    }


# ── results ───────────────────────────────────────────────────────────────


def _used(r: dict, total_t, period_totals: dict) -> float | None:
    """The tonnes the row's period emitted, None when the run counted none for
    it (a withheld part, never 0 t)."""
    p = r.get("period")
    v = total_t if p is None else period_totals.get(p)
    return _finite(v) if v is not None else None


def _binding(r: dict, used: float | None) -> bool | None:
    """The Emissions view's verdict (`results.emissions._cap_binding`)."""
    from services.results.emissions import _cap_binding

    slack = None if used is None else r["cap_t"] - used
    return _cap_binding(r.get("mu"), slack, r["cap_t"])


def cap_entries(goal: dict, total_t, period_totals: dict) -> list[dict]:
    """The goal's rows as Emissions ``caps`` entries (`results/emissions`): the
    tonnes each row allowed and used, its raw dual (the sign and basis every
    stored cap shows), whether it bound, and ``cost_eur_per_t`` (undiscounted,
    positive: the headline's figure)."""
    out = []
    for r in goal.get("rows") or []:
        p = r.get("period")
        used = _used(r, total_t, period_totals)
        binding = _binding(r, used)
        out.append(
            {
                "active": True,
                "goal": True,
                "name": r["name"],
                "investment_period": p,
                "scope": "period" if p is not None else "horizon",
                "cap_tCO2": r["cap_t"],
                "used_tCO2": used,
                "shadow_price_eur_per_tCO2": r.get("mu"),
                "cost_eur_per_t": r.get("shadow_price_eur_per_t") if binding else None,
                "slack_tCO2": None if used is None else r["cap_t"] - used,
                "binding": binding,
            }
        )
    return out


def annual(goal: dict, total_t, period_totals: dict) -> dict:
    """The headline figures: t CO2 per year (the tonnes over the year-equivalent
    the row stands for); a multi-period run's is its highest period's, named in
    ``period``. None when any period's tonnes are missing (never 0 t). Plus the
    highest EUR/t of a binding row (None when none binds)."""
    rows = goal.get("rows") or []
    by_period: dict = {}
    missing = False
    binding_any = False
    prices = []
    for r in rows:
        used = _used(r, total_t, period_totals)
        b = _binding(r, used)
        binding_any = binding_any or bool(b)
        if b and r.get("shadow_price_eur_per_t") is not None:
            prices.append(r["shadow_price_eur_per_t"])
        if used is None or not r["row_scale"] > 0:
            missing = True
            continue
        by_period[r.get("period")] = _finite(used / r["row_scale"])
    missing = missing or any(v is None for v in by_period.values())
    period = None
    value = None
    if not missing and by_period:
        if None in by_period:
            value = by_period[None]
        else:
            period = max(by_period, key=by_period.get)
            value = by_period[period]
    return {
        "t_per_yr": value,
        "period": period,
        "by_period": {}
        if missing
        else {p: v for p, v in by_period.items() if p is not None},
        "limit_t_per_yr": goal.get("limit_t_per_yr"),
        "binding": binding_any,
        "shadow_price_eur_per_t": max(prices) if prices else None,
    }
