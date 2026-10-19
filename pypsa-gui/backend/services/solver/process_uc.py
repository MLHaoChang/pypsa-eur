"""
Unit commitment for process units on the solve path (grid-to-process ticket 03, design §2.3).

A process unit is a Link with ``eh_process=True``. When one is committable:

* **Planning** runs PyPSA's linearised UC (``linearized_unit_commitment=True``: u ∈ [0, 1],
  p ≥ p_min_pu·p_nom·u, min up/down on u) under ``process_uc`` ``auto`` or ``linearised``. The
  keyword is network-wide in PyPSA, so ``auto`` keeps a network binary when any other unit is
  committable (a user's binary gensets must not be relaxed silently); an explicit ``linearised``
  relaxes them and the phase line says so. This module writes nothing to the network: the units
  are committable in the data. The island path (``solver/island.py:linearised_uc``) stays the
  only writer and restorer of ``committable``; ``merge_uc_kwargs`` passes the keyword once.
* **The binary operational check** (``binary_check``) is mandatory after a linearised plan: the
  relaxation lets a unit sit "half on" below its minimum load, so it understates restarts and
  the shortfall. Per period it solves the first ``CHECK_WINDOW`` snapshots on a copy with every
  capacity fixed at the plan, once linearised and once binary, and flags the period above
  ``FLAG_PCT`` cost or any extra shortfall. It never fails the run or touches the plan.

**Upstream PyPSA gap (for an upstream report).** PyPSA 1.1.2 enters ``start_up_cost`` and
``shut_down_cost`` in the objective as ``var × cost`` with no snapshot or investment-period
weighting (``pypsa/optimization/optimize.py:301-316``), while marginal and stand-by costs are
weighted (``:211-216``, ``:257-275``). On a representative week standing for 52 weeks × years the
start-up cost is undercounted by the weight. The app does not pre-scale (weights may differ per
snapshot and the user would see a number that is not the real cost): the template rules refuse
the cost on a weighted network and the solver warns on a user network (``weighted_start_cost_units``).
"""
from __future__ import annotations

import math
import time

import numpy as np
import pandas as pd

from models.process_unit import product_role_generators

#: Snapshots per period the binary check solves: one representative week at 1 h.
CHECK_WINDOW = 168
#: Binary minus linearised operating cost, relative to linearised, above which a period is flagged.
FLAG_PCT = 0.02
#: Extra shortfall (product units, weighted) treated as "any".
SHORTFALL_TOL = 1e-6
FLAG_MESSAGE = "linearised UC understates restarts"
_LIN = {"linearized_unit_commitment": True}


def _committable(df: pd.DataFrame) -> pd.Series:
    if df.empty or "committable" not in df.columns:
        return pd.Series(False, index=df.index)
    return df["committable"].fillna(False).astype(bool)


def _is_process(links: pd.DataFrame) -> pd.Series:
    if links.empty or "eh_process" not in links.columns:
        return pd.Series(False, index=links.index)
    return links["eh_process"].fillna(False).astype(bool)


def committable_counts(n) -> tuple[int, int]:
    """(committable Generators, committable Links)."""
    return int(_committable(n.generators).sum()), int(_committable(n.links).sum())


def process_committable(n) -> pd.Index:
    """The committable process units."""
    return n.links.index[_committable(n.links) & _is_process(n.links)]


def other_committable(n) -> tuple[int, int]:
    """(committable Generators, committable non-process Links)."""
    links = _committable(n.links) & ~_is_process(n.links)
    return int(_committable(n.generators).sum()), int(links.sum())


def planning_uc_mode(n, cfg) -> str | None:
    """How the plan commits the process units: "linearised", "binary", or None without any."""
    if not len(process_committable(n)):
        return None
    mode = getattr(cfg, "process_uc", "auto") or "auto"
    if mode == "auto":
        return "binary" if sum(other_committable(n)) else "linearised"
    return mode


def planning_uc_kwargs(n, cfg) -> dict:
    """The ``optimize`` keywords the process units add to a planning solve."""
    return dict(_LIN) if planning_uc_mode(n, cfg) == "linearised" else {}


def planning_uc_note(n, cfg) -> str | None:
    """The phase line for the process units' commitment, or None without any."""
    mode = planning_uc_mode(n, cfg)
    if mode is None:
        return None
    k = len(process_committable(n))
    gens, links = other_committable(n)
    if mode == "binary":
        why = (" (process_uc auto: other units are committable, so the plan stays binary)"
               if (getattr(cfg, "process_uc", "auto") or "auto") == "auto" else "")
        return f"Process units: binary UC on {k} process link(s){why}"
    line = f"Process units: linearised UC on {k} process link(s)"
    if gens or links:
        line += (f"; WARN: also relaxes {gens} committable generator(s) and {links} other "
                 "committable link(s) (process_uc: linearised is network-wide)")
    return line


def merge_uc_kwargs(island_kw: dict, process_kw: dict) -> dict:
    """One set of UC keywords from the island path and the process units. Both only ever set
    ``linearized_unit_commitment=True``, so the union passes it once."""
    return {**(island_kw or {}), **(process_kw or {})}


def sclopf_model_kwargs(uc_kwargs: dict) -> dict:
    """``optimize_security_constrained`` builds its model with ``model_kwargs`` (PyPSA 1.1.2
    ``abstract.py``), so the UC keyword goes there; ``{}`` (no keyword at all) without it."""
    if not uc_kwargs:
        return {}
    from pypsa import options

    return {"model_kwargs": {**options.params.optimize.model_kwargs.copy(), **uc_kwargs}}


def _objective_weights(n) -> tuple[pd.Series, pd.Series]:
    sw = n.snapshot_weightings["objective"]
    pw = (n.investment_period_weightings["objective"]
          if len(getattr(n, "investment_periods", [])) else pd.Series(dtype=float))
    return sw, pw


def is_weighted(n) -> bool:
    """Any snapshot objective weight or investment-period objective weight ≠ 1 (design §2.3)."""
    sw, pw = _objective_weights(n)
    return bool((~np.isclose(sw.to_numpy(float), 1.0)).any()
                or (len(pw) and (~np.isclose(pw.to_numpy(float), 1.0)).any()))


def weighted_start_cost_units(n) -> list[tuple[str, str, str, float]]:
    """(component, name, attr, value) for every committable unit with a start-up or shut-down cost
    above 0 on a weighted network: PyPSA leaves those costs unweighted (module docstring)."""
    if not is_weighted(n):
        return []
    out = []
    for comp, df in (("Generator", n.generators), ("Link", n.links)):
        cmt = _committable(df)
        for attr in ("start_up_cost", "shut_down_cost"):
            if attr not in df.columns:
                continue
            vals = pd.to_numeric(df[attr], errors="coerce").fillna(0.0)
            for name in df.index[cmt & (vals > 0)]:
                out.append((comp, str(name), attr, float(vals[name])))
    return out


# ── the binary operational check ───────────────────────────────────────────


def _fix_capacities(m) -> None:
    """Every extendable capacity fixed at the plan's optimum."""
    for attr, nom in (("generators", "p_nom"), ("links", "p_nom"), ("storage_units", "p_nom"),
                      ("stores", "e_nom"), ("lines", "s_nom"), ("transformers", "s_nom")):
        df = getattr(m, attr)
        ext = f"{nom}_extendable"
        if df.empty or ext not in df.columns:
            continue
        rows = df.index[df[ext].fillna(False).astype(bool)]
        if len(rows):
            df.loc[rows, nom] = pd.to_numeric(df.loc[rows, f"{nom}_opt"], errors="coerce").fillna(0.0)
            df.loc[rows, ext] = False


def _shortfall(m, sns) -> dict[str, dict]:
    """Lost production per product bus over ``sns``, weighted like the dispatch (generators
    weighting): {bus: {value, unit, t}}; ``t`` is tonnes when the bus is in MWh with mwh_per_t."""
    lost = product_role_generators(m, "shortfall")
    out: dict[str, dict] = {}
    if not len(lost):
        return out
    w = m.snapshot_weightings["generators"].reindex(sns).fillna(0.0)
    p = m.generators_t.p.reindex(index=sns, columns=lost).fillna(0.0)
    for g in lost:
        bus = str(m.generators.at[g, "bus"])
        value = float((p[g] * w).sum())
        row = out.setdefault(bus, {"value": 0.0, "unit": _bus_unit(m, bus), "t": None})
        row["value"] += value
    for bus, row in out.items():
        row["t"] = _tonnes(m, bus, row["value"])
    return out


def _bus_unit(m, bus) -> str:
    unit = m.buses.at[bus, "unit"] if "unit" in m.buses.columns else ""
    return "" if unit is None or (isinstance(unit, float) and math.isnan(unit)) else str(unit)


def _tonnes(m, bus, value):
    unit = _bus_unit(m, bus)
    if unit == "t":
        return value
    f = m.buses.at[bus, "mwh_per_t"] if "mwh_per_t" in m.buses.columns else float("nan")
    try:
        f = float(f)
    except (TypeError, ValueError):
        return None
    return value / f if unit == "MWh" and math.isfinite(f) and f > 0 else None


def _solve_window(m, sns, multi, cfg, solver_options, kw) -> tuple[str, str, float]:
    # `include_objective_constant=False`: with every capacity fixed the objective is the
    # operating cost of the window (capex is a constant, not a decision).
    common = dict(snapshots=sns, solver_name=cfg.solver_name, solver_options=solver_options,
                  include_objective_constant=False, **kw)
    if multi:
        try:
            status, cond = m.optimize(multi_investment_periods=True, **common)
        except Exception:                                        # noqa: BLE001
            # PyPSA's period-cost check can trip with no extendable left (as the myopic
            # driver's operational retry): solve the window as a plain single-period LP.
            status, cond = m.optimize(**common)
    else:
        status, cond = m.optimize(**common)
    return status, cond, float(m.objective)


def _copy(n):
    """A copy of the solved network without its linopy model. PyPSA refuses to copy a network
    with its solver model attached (and would otherwise deep-copy the whole model), so the model
    is detached for the copy and put back, leaving ``n`` as it was."""
    model = n._model
    n._model = None
    try:
        return n.copy()
    finally:
        n._model = model


def _window(n, period) -> pd.Index:
    sns = n.snapshots
    if period is not None:
        sns = sns[sns.get_level_values(0) == period]
    return sns[:CHECK_WINDOW]


def _empty_row(period, window, snapshots) -> dict:
    return {"period": period, "window": window, "snapshots": snapshots, "status": "ok",
            "reason": None, "cost_lin": None, "cost_bin": None, "delta": None, "pct": None,
            "shortfall_lin": {}, "shortfall_bin": {}, "extra_shortfall": {},
            "flagged": False, "non_finite": []}


def _period_row(n, period, cfg, solver_options, solve) -> dict:
    sns = _window(n, period)
    multi = isinstance(n.snapshots, pd.MultiIndex)
    window = (f"first {len(sns)} snapshots of period {period}" if period is not None
              else f"first {len(sns)} snapshots")
    row = _empty_row(period, window, len(sns))
    results = {}
    for label, kw in (("lin", _LIN), ("bin", {})):
        m = _copy(n)
        if multi:
            m.snapshots.name = "snapshot"   # PyPSA's copy() drops the MultiIndex name
        _fix_capacities(m)
        status, cond, cost = solve(m, sns, multi, cfg, solver_options, kw)
        if status != "ok":
            row.update(status="unverified", reason=f"{label} solve {status}/{cond}")
            return row
        results[label] = (cost, _shortfall(m, sns))
    (c_lin, s_lin), (c_bin, s_bin) = results["lin"], results["bin"]
    delta = c_bin - c_lin
    pct = delta / abs(c_lin) if abs(c_lin) > 1e-9 else (0.0 if abs(delta) <= 1e-9 else math.inf)
    extra = {}
    for bus in sorted({*s_lin, *s_bin}):
        lin = s_lin.get(bus, {"value": 0.0})["value"]
        b = s_bin.get(bus, {"value": 0.0, "unit": _bus_unit(n, bus)})
        v = b["value"] - lin
        extra[bus] = {"value": v, "unit": b.get("unit") or _bus_unit(n, bus), "t": _tonnes(n, bus, v)}
    row.update(cost_lin=c_lin, cost_bin=c_bin, delta=delta, pct=pct, shortfall_lin=s_lin,
               shortfall_bin=s_bin, extra_shortfall=extra)
    row["flagged"] = bool(pct > FLAG_PCT or any(e["value"] > SHORTFALL_TOL for e in extra.values()))
    for key in ("cost_lin", "cost_bin", "delta", "pct"):
        if not math.isfinite(row[key]):
            row["non_finite"].append(key)
            row[key] = None
    return row


def _ledger(rows, cfg) -> list[dict]:
    gap = getattr(cfg, "mip_gap", 0.01)
    limit = getattr(cfg, "mip_time_limit_s", 0)
    out = [{"item": "method", "value": "binary operational check: each period re-solved on its own with "
            "every capacity fixed at the plan, once with linearised UC and once with binary UC; "
            "the plan's linearised result is not re-used"},
           {"item": "cost basis", "value": "operating cost of the window (capacities fixed, so capex "
            "is excluded), objective-weighted; pct = (binary − linearised) / linearised"},
           {"item": "MIP settings", "value": f"gap {gap:.3g}" + (f", time limit {int(limit)} s"
                                                               if limit and limit > 0 else ", no time limit")},
           {"item": "flag", "value": f"> {FLAG_PCT:.0%} cost, or any extra shortfall: \"{FLAG_MESSAGE}\""},
           {"item": "caveat", "value": "extra_functionality not applied (island rows, commercial terms, "
            "reserve and adequacy rows); island gensets keep their own committable flag"}]
    out += [{"item": f"window {r['period']}" if r["period"] is not None else "window",
             "value": r["window"]} for r in rows]
    return out


def binary_check(n, cfg, *, solver_options: dict | None = None, solve=None) -> dict:
    """The binary operational check on a solved network (module docstring). Never raises
    (except an abort's KeyboardInterrupt) and never changes ``n``: every solve is on a copy. A
    period that fails, times out or raises is ``unverified`` with its reason."""
    t0 = time.monotonic()
    solve = solve or _solve_window
    periods = (list(n.investment_periods) if isinstance(n.snapshots, pd.MultiIndex) else [None])
    rows = []
    for period in periods:
        try:
            rows.append(_period_row(n, period, cfg, solver_options or {}, solve))
        except Exception as exc:                                 # noqa: BLE001
            rows.append({**_empty_row(period, None, 0), "status": "unverified",
                         "reason": f"{type(exc).__name__}: {exc}"})
    verified = [r for r in rows if r["status"] == "ok"]
    flagged = any(r["flagged"] for r in verified)
    status = "ok" if len(verified) == len(rows) else "unverified"
    if flagged:
        message = (f"{FLAG_MESSAGE}: binary UC costs more than {FLAG_PCT:.0%} more or sheds more in "
                   + ", ".join(str(r["period"]) if r["period"] is not None else "the window"
                               for r in verified if r["flagged"]))
    elif status != "ok":
        message = "binary check unverified: " + "; ".join(
            f"{r['period']}: {r.get('reason')}" for r in rows if r["status"] != "ok")
    else:
        message = "binary check: linearised UC within the flag threshold in every period"
    return {"status": status, "flagged": flagged, "message": message,
            "process_units": [str(u) for u in process_committable(n)],
            "threshold": {"pct": FLAG_PCT, "extra_shortfall": SHORTFALL_TOL},
            "periods": rows, "ledger": _ledger(rows, cfg),
            "wall_time_s": time.monotonic() - t0}


def sanitize_check_payload(payload: dict) -> dict:
    """``payload`` as strict JSON: every non-finite number becomes None and its path is listed
    in ``non_finite_paths`` (the response is dumped with ``allow_nan=False``, #329)."""
    paths: list[str] = []

    def walk(v, path):
        if isinstance(v, dict):
            return {k: walk(x, f"{path}.{k}" if path else str(k)) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [walk(x, f"{path}[{i}]") for i, x in enumerate(v)]
        if isinstance(v, (float, np.floating)):
            if not math.isfinite(float(v)):
                paths.append(path)
                return None
            return float(v)
        if isinstance(v, np.integer):
            return int(v)
        if isinstance(v, np.bool_):
            return bool(v)
        return v

    out = walk(payload, "")
    out["non_finite_paths"] = paths
    return out
