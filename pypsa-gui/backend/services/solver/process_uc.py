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
* **The chronological year** (``stage_network(time_base="year")``, grid-to-process 09/10): linearised
  UC over 8,760 hours per period does not finish within the solve budget, so ``auto`` plans it as a
  plain LP (``plain``): ``relax_for_plan`` takes the process units' commitment and minimum load off
  for the plan solve and puts them back after it. The binary check then solves one week per
  season of each period (``YEAR_WINDOW_DAYS``), each starting from the plan's store levels and
  unit states at its first hour, so a week mid-year is not refilled from nothing.

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
#: On the chronological year: the day of the period each checked week starts (mid-January, -April, -July and
#: -October, one week per season).
YEAR_WINDOW_DAYS = (14, 104, 195, 287)
YEAR_HOURS = 8760
#: Binary minus linearised operating cost, relative to linearised, above which a period is flagged.
FLAG_PCT = 0.02
#: Extra shortfall treated as "any": above numerical noise, relative to the product delivered in
#: the window (weighted), with an absolute floor in product units.
SHORTFALL_TOL_REL = 1e-6
SHORTFALL_TOL_ABS = 1e-6
FLAG_MESSAGE = "linearised UC understates restarts"
_LIN = {"linearized_unit_commitment": True}


def flag_column(df: pd.DataFrame, col: str) -> pd.Series:
    """A bool column as a clean bool Series (False where absent or NaN)."""
    if df.empty or col not in df.columns:
        return pd.Series(False, index=df.index)
    return df[col].fillna(False).astype(bool)


def committable_mask(df: pd.DataFrame) -> pd.Series:
    return flag_column(df, "committable")


def process_mask(links: pd.DataFrame) -> pd.Series:
    """The process units (``eh_process``) among ``links``."""
    return flag_column(links, "eh_process")


def committable_counts(n) -> tuple[int, int]:
    """(committable Generators, committable Links)."""
    return int(committable_mask(n.generators).sum()), int(committable_mask(n.links).sum())


def process_committable(n) -> pd.Index:
    """The committable process units."""
    return n.links.index[committable_mask(n.links) & process_mask(n.links)]


def other_committable(n) -> tuple[int, int]:
    """(committable Generators, committable non-process Links)."""
    links = committable_mask(n.links) & ~process_mask(n.links)
    return int(committable_mask(n.generators).sum()), int(links.sum())


def _setting(cfg) -> str:
    return getattr(cfg, "process_uc", "auto") or "auto"


def is_year_base(n) -> bool:
    """Every period is one chronological year of hours (8,760 snapshots, every objective weighting 1): the
    ``year`` time base of ``eh_site.stage_network``."""
    sns = n.snapshots
    if not len(sns):
        return False
    periods = sns.get_level_values(0) if isinstance(sns, pd.MultiIndex) else np.zeros(len(sns))
    counts = pd.Series(1, index=periods).groupby(level=0).size()
    return bool((counts == YEAR_HOURS).all()
                and np.isclose(n.snapshot_weightings["objective"].to_numpy(float), 1.0).all())


def planning_uc_mode(n, cfg) -> str | None:
    """How the plan commits the process units: "linearised", "binary", "plain" (the year base under
    ``auto``: no commitment in the plan, module docstring), or None without any."""
    if not len(process_committable(n)):
        return None
    mode = _setting(cfg)
    if mode == "auto":
        if sum(other_committable(n)):
            return "binary"
        return "plain" if is_year_base(n) else "linearised"
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
    if mode == "plain":
        return (f"Process units: plain LP on {k} process link(s) on the chronological year (minimum load "
                "and min up / down are not in the plan; the binary check re-solves one week per season)")
    if mode == "binary":
        why = (" (process_uc auto: other units are committable, so the plan stays binary)"
               if _setting(cfg) == "auto" else "")
        return f"Process units: binary UC on {k} process link(s){why}"
    line = f"Process units: linearised UC on {k} process link(s)"
    if gens or links:
        line += (f"; WARN: also relaxes {gens} committable generator(s) and {links} other "
                 "committable link(s) (process_uc: linearised is network-wide)")
    return line


def relax_for_plan(n, cfg):
    """Under ``plain``: the committable process units solve as plain Links (``committable`` False,
    ``p_min_pu`` 0). Returns the undo, which puts them back (idempotent); a no-op otherwise."""
    if planning_uc_mode(n, cfg) != "plain":
        return lambda: None
    units = process_committable(n)
    saved = n.links.loc[units, ["committable", "p_min_pu"]].copy()
    series = n.links_t.p_min_pu[[u for u in units if u in n.links_t.p_min_pu.columns]].copy()
    n.links.loc[units, "committable"] = False
    n.links.loc[units, "p_min_pu"] = 0.0
    if len(series.columns):
        n.links_t.p_min_pu = n.links_t.p_min_pu.drop(columns=series.columns)
    done = {"v": False}

    def undo() -> None:
        if done["v"]:
            return
        n.links.loc[saved.index, ["committable", "p_min_pu"]] = saved
        n.links["committable"] = n.links["committable"].astype(bool)
        for u in series.columns:
            n.links_t.p_min_pu[u] = series[u]
        done["v"] = True

    return undo


def merge_uc_kwargs(island_kw: dict, process_kw: dict) -> dict:
    """One set of UC keywords from the island path and the process units. Both only ever set
    ``linearized_unit_commitment=True``, so the union passes it once."""
    return {**(island_kw or {}), **(process_kw or {})}


def island_relaxes_binary_note(n, cfg, island_kw: dict) -> str | None:
    """The WARN line when the island's linearised UC relaxes process units the plan meant to
    keep binary (the keyword is network-wide), or None."""
    if planning_uc_mode(n, cfg) == "binary" and (island_kw or {}).get("linearized_unit_commitment"):
        return (f"WARN: the island's linearised_uc relaxes the {len(process_committable(n))} "
                "process link(s) too (linearized_unit_commitment is network-wide); the binary "
                "operational check runs")
    return None


def sclopf_model_kwargs(n, uc_kwargs: dict) -> dict:
    """The UC keyword for ``optimize_security_constrained`` (PyPSA 1.1.2 ``abstract.py``): it
    builds its model with ``model_kwargs``, except on a network with no passive branch, where it
    calls ``optimize`` directly and the keyword must go as itself. ``{}`` without it."""
    if not uc_kwargs:
        return {}
    if not len(n.passive_branches().index):
        return dict(uc_kwargs)
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
        cmt = committable_mask(df)
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
        rows = df.index[flag_column(df, ext)]
        if len(rows):
            df.loc[rows, nom] = pd.to_numeric(df.loc[rows, f"{nom}_opt"], errors="coerce").fillna(0.0)
            df.loc[rows, ext] = False


def _shortfall(m, sns) -> dict[str, dict]:
    """Shortfall per product bus over ``sns``: {bus: {value, value_window, delivered, unit, t}}.
    ``value`` is weighted like the dispatch (generators weighting, so a representative week
    counts for the year it stands for); ``value_window`` is the window's own; ``delivered`` is
    the weighted demand on the bus; ``t`` is ``value`` in tonnes when the bus says how (unit t,
    or MWh with mwh_per_t), else None."""
    lost = product_role_generators(m, "shortfall")
    out: dict[str, dict] = {}
    if not len(lost):
        return out
    w = m.snapshot_weightings["generators"].reindex(sns).fillna(0.0)
    p = m.generators_t.p.reindex(index=sns, columns=lost).fillna(0.0)
    demand = m.loads_t.p.reindex(index=sns).fillna(0.0)
    for g in lost:
        bus = str(m.generators.at[g, "bus"])
        row = out.setdefault(bus, {"value": 0.0, "value_window": 0.0, "delivered": 0.0,
                                   "unit": _bus_unit(m, bus), "t": None})
        row["value"] += float((p[g] * w).sum())
        row["value_window"] += float(p[g].sum())
    for bus, row in out.items():
        loads = [c for c in m.loads.index[m.loads.bus == bus] if c in demand.columns]
        row["delivered"] = float(demand[loads].sum(axis=1).mul(w).sum()) if loads else 0.0
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
    # Multi-period: one period's snapshots with multi_investment_periods, so PyPSA applies
    # build_year and lifetime (an asset not active in the period has no capacity). No
    # single-period retry: it would make every asset active; a failure is "unverified".
    status, cond = m.optimize(multi_investment_periods=multi, **common)
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


def _windows(n, period) -> list[tuple[int, pd.Index]]:
    """(first hour in the period, snapshots) of each window the check solves in ``period``: the first
    ``CHECK_WINDOW`` snapshots, or on the chronological year one week from each of ``YEAR_WINDOW_DAYS``."""
    sns = n.snapshots
    if period is not None:
        sns = sns[sns.get_level_values(0) == period]
    starts = [d * 24 for d in YEAR_WINDOW_DAYS] if is_year_base(n) else [0]
    return [(h, sns[h:h + CHECK_WINDOW]) for h in starts]


def _runs_before(on: np.ndarray) -> int:
    """How many hours at the end of ``on`` hold its last value."""
    if not len(on):
        return 0
    change = np.flatnonzero(on != on[-1])
    return int(len(on) - (change[-1] + 1 if len(change) else 0))


def _start_from_plan(m, n, period, first: int) -> None:
    """A window starting ``first`` hours into ``period`` starts where the plan is then: each store and
    storage unit at the plan's level after the hour before (no longer cyclic within the window), and each
    committable process unit up or down for as long as the plan has had it so (a unit runs when its plan
    dispatch is above 1e-6 of its p_nom). A window at the start of a period keeps the network's own start
    (cyclic, as the representative week)."""
    if first <= 0:
        return
    sns = n.snapshots
    if period is not None:
        sns = sns[sns.get_level_values(0) == period]
    before = sns[:first]
    last = before[-1]
    if len(m.stores):
        m.stores["e_initial"] = n.stores_t.e.loc[last].reindex(m.stores.index).fillna(0.0).to_numpy()
        for c in ("e_cyclic", "e_cyclic_per_period", "e_initial_per_period"):
            m.stores[c] = False
    if len(m.storage_units):
        soc = n.storage_units_t.state_of_charge.loc[last].reindex(m.storage_units.index).fillna(0.0)
        m.storage_units["state_of_charge_initial"] = soc.to_numpy()
        for c in ("cyclic_state_of_charge", "cyclic_state_of_charge_per_period",
                  "state_of_charge_initial_per_period"):
            m.storage_units[c] = False
    # ... and ends no emptier than the plan: each store at least at the plan's level after the window's last
    # hour, so the window cannot run its inventories down for free. A product short of it is lost
    # production: the shortfall slack may cover it in any hour of the window, at its value.
    after = sns[first:first + CHECK_WINDOW][-1]
    if len(m.stores):
        e_nom = pd.to_numeric(m.stores["e_nom"], errors="coerce").fillna(0.0)
        end = n.stores_t.e.loc[after].reindex(m.stores.index).fillna(0.0)
        for st in m.stores.index[e_nom > 0]:
            col = m.stores_t.e_min_pu[st] if st in m.stores_t.e_min_pu.columns else \
                pd.Series(float(m.stores.at[st, "e_min_pu"]), index=m.snapshots)
            col.loc[after] = min(1.0, max(float(col.loc[after]), float(end[st]) / float(e_nom[st])))
            m.stores_t.e_min_pu[st] = col
    for g in product_role_generators(m, "shortfall"):
        m.generators_t.p_max_pu.loc[sns[first:first + CHECK_WINDOW], g] = 1.0
    for u in process_committable(m):
        p_nom = float(m.links.at[u, "p_nom"]) or 1.0
        on = n.links_t.p0[u].loc[before].to_numpy() > 1e-6 * p_nom
        hours = _runs_before(on)
        m.links.at[u, "up_time_before"] = hours if on[-1] else 0
        m.links.at[u, "down_time_before"] = 0 if on[-1] else hours


def _empty_row(period, window=None, snapshots=0) -> dict:
    """The one shape of a period row, verified or not."""
    return {"period": period, "window": window, "snapshots": snapshots, "status": "ok",
            "reason": None, "cost_lin": None, "cost_bin": None, "delta": None, "pct": None,
            "shortfall_lin": {}, "shortfall_bin": {}, "extra_shortfall": {},
            "flagged": False, "non_finite": []}


def window_copy(n, period, first: int):
    """The copy a window solves on: capacities fixed at the plan, started from the plan (``_start_from_plan``)."""
    m = _copy(n)
    if isinstance(n.snapshots, pd.MultiIndex):
        m.snapshots.name = "snapshot"   # PyPSA's copy() drops the MultiIndex name
    _fix_capacities(m)
    _start_from_plan(m, n, period, first)
    return m


def _window_text(period, first: int, sns) -> str:
    if first > 0:
        start = sns[0][1] if isinstance(sns, pd.MultiIndex) else sns[0]
        at = f"{len(sns)} snapshots from hour {first}"
        at += f" ({pd.Timestamp(start):%d %b})" if isinstance(start, (pd.Timestamp, np.datetime64)) else ""
        return at + (f" of period {period}" if period is not None else "")
    return (f"first {len(sns)} snapshots of period {period}" if period is not None
            else f"first {len(sns)} snapshots")


def _period_row(n, period, cfg, solver_options, solve, first: int = 0, sns=None) -> dict:
    if sns is None:
        first, sns = _windows(n, period)[0]
    multi = isinstance(n.snapshots, pd.MultiIndex)
    row = _empty_row(period, _window_text(period, first, sns), len(sns))
    results = {}
    for label, kw in (("lin", _LIN), ("bin", {})):
        m = window_copy(n, period, first)
        status, cond, cost = solve(m, sns, multi, cfg, solver_options, kw)
        if status != "ok":
            row.update(status="unverified", reason=f"{label} solve {status}/{cond}")
            return row
        results[label] = (cost, _shortfall(m, sns))
    (c_lin, s_lin), (c_bin, s_bin) = results["lin"], results["bin"]
    delta = c_bin - c_lin
    pct = delta / abs(c_lin) if abs(c_lin) > 1e-9 else (0.0 if abs(delta) <= 1e-9 else math.inf)
    # Both solves read the same shortfall Generators, so both carry the same buses.
    extra = {}
    for bus in sorted(s_bin):
        lin, b = s_lin[bus], s_bin[bus]
        v = b["value"] - lin["value"]
        tol = max(SHORTFALL_TOL_ABS, SHORTFALL_TOL_REL * b["delivered"])
        extra[bus] = {"value": v, "value_window": b["value_window"] - lin["value_window"],
                      "unit": b["unit"], "t": _tonnes(n, bus, v), "above_tolerance": v > tol}
    row.update(cost_lin=c_lin, cost_bin=c_bin, delta=delta, pct=pct, shortfall_lin=s_lin,
               shortfall_bin=s_bin, extra_shortfall=extra)
    row["flagged"] = bool(pct > FLAG_PCT or any(e["above_tolerance"] for e in extra.values()))
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
           {"item": "shortfall basis", "value": "shortfall Generator dispatch (process_role "
            "'shortfall') weighted by snapshot_weightings.generators, in the product bus unit (t "
            "where the bus gives a factor); 'any' means above "
            f"{SHORTFALL_TOL_REL:g} × the weighted delivery (floor {SHORTFALL_TOL_ABS:g})"},
           {"item": "caveat", "value": "extra_functionality not applied (island rows, commercial terms, "
            "reserve and adequacy rows); island gensets keep their own committable flag"}]
    per_period = pd.Series([r["period"] for r in rows], dtype=object).value_counts(dropna=False)
    for r in rows:
        label = f"period {r['period']}" if r["period"] is not None else "window"
        if per_period.get(r["period"], 1) > 1:          # several weeks a period (the year base)
            label += f", week {sum(1 for q in rows[:rows.index(r) + 1] if q['period'] == r['period'])}"
        out.append({"item": f"{label}: window", "value": r["window"]})
        out.append({"item": f"{label}: result", "value": result_line(r)})
    return out


def result_line(r: dict) -> str:
    """One period row as a sentence (the ledger and the live log)."""
    if r["status"] != "ok":
        return f"unverified ({r['reason']})"
    pct = "n/a" if r["pct"] is None else f"{r['pct']:+.2%}"
    delta = "n/a" if r["delta"] is None else f"{r['delta']:,.0f}"
    extra = ", ".join(f"{b} {e['value']:.4g} {e['unit']}".rstrip()
                      for b, e in r["extra_shortfall"].items()) or "no shortfall slack"
    return (f"binary − linearised {delta} ({pct}); extra shortfall {extra}"
            + (f"; FLAGGED: {FLAG_MESSAGE}" if r["flagged"] else ""))


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
            windows = _windows(n, period)
        except Exception as exc:                                 # noqa: BLE001
            rows.append({**_empty_row(period), "status": "unverified",
                         "reason": f"{type(exc).__name__}: {exc}"})
            continue
        for first, sns in windows:
            try:
                rows.append(_period_row(n, period, cfg, solver_options or {}, solve, first, sns))
            except Exception as exc:                             # noqa: BLE001
                rows.append({**_empty_row(period, _window_text(period, first, sns), len(sns)),
                             "status": "unverified", "reason": f"{type(exc).__name__}: {exc}"})
    verified = [r for r in rows if r["status"] == "ok"]
    flagged = any(r["flagged"] for r in verified)
    status = "ok" if len(verified) == len(rows) else "unverified"
    if flagged:
        message = (f"{FLAG_MESSAGE}: binary UC costs more than {FLAG_PCT:.0%} more or sheds more in "
                   + ", ".join(dict.fromkeys(str(r["period"]) if r["period"] is not None else "the window"
                                             for r in verified if r["flagged"])))
    elif status != "ok":
        several = is_year_base(n)       # several weeks a period: name the week, not only the period
        message = "binary check unverified: " + "; ".join(
            f"{r['window'] if several and r['window'] else r['period']}: {r.get('reason')}"
            for r in rows if r["status"] != "ok")
    else:
        message = "binary check: linearised UC within the flag threshold in every period"
    return _payload(n, status, flagged, message, rows, _ledger(rows, cfg), t0)


def _payload(n, status, flagged, message, rows, ledger, t0) -> dict:
    return {"status": status, "flagged": flagged, "message": message,
            "process_units": [str(u) for u in process_committable(n)],
            "threshold": {"pct": FLAG_PCT, "extra_shortfall_rel": SHORTFALL_TOL_REL,
                          "extra_shortfall_abs": SHORTFALL_TOL_ABS},
            "cost_basis": "operating cost of the window, capacities fixed (capex excluded)",
            "periods": rows, "ledger": ledger, "wall_time_s": time.monotonic() - t0}


def unverified_payload(n, reason: str, t0: float) -> dict:
    """The payload of a check that could not run at all, in ``binary_check``'s shape."""
    message = f"binary check unverified: {reason}"
    return _payload(n, "unverified", False, message, [], [{"item": "result", "value": message}], t0)


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
