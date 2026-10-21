"""
`/results/load_shape`: how the site's load and its grid exchange are shaped in
time (Site commercial UX, package A2).

Plan: .scratch/site-commercial-ux/issues/02-load-shape-analysis.md.

Everything is computed from the solved dispatch of one modelled period (no new
solve), read through the same arrays as the energy flows
(`energy_flows.read_dispatch`), so the two views refuse the same networks
(non-energy buses, unreadable dispatch) in the same words.

Per snapshot, in MW:

  * grid import — what the "grid"-carrier generators supply;
  * export — what generators absorb (negative output) plus what branches
    deliver into the grid bus;
  * solar, wind — those carriers' generation; genset — every other
    non-grid, non-renewable generator;
  * EV, flexible load — loads and storages whose carrier names them
    (`ev…`, `flex…`/`dsr`/`demand response`);
  * battery state of charge — storage units whose carrier is a battery;
  * unserved load — the lost-load capture of the solve;
  * excess — the curtailed renewable energy of `/results/curtailment`.

A group the network does not model is `not_modelled`; one the server cannot
establish (no lost-load capture, no timestamps, representative snapshots for a
calendar view) is `unavailable` with the reason. Neither is ever a zero
(ADR-0001); a computed zero is a zero.

Views: weekday × hour heatmaps of import and export (mean MW over a window), a
one-week profile with flagged intervals, monthly site energy (a full calendar
year of snapshots only) and annual site energy per modelled period. Energy is
MW × `snapshot_weightings.generators`, per modelled year.

Imports no router.
"""
from __future__ import annotations

import math
from typing import Any, Callable

import numpy as np
import pandas as pd

from services.period_utils import is_multi_period
from services.results.energy_flows import (
    GRID_IMPORT, period_mask, periods_of, read_dispatch,
)

SERIES_ORDER = ("grid", "export", "solar", "wind", "genset", "ev", "flexible", "battery_soc", "unserved")
RENEWABLE = {"solar", "wind", "hydro", "ror", "onwind", "offwind", "geothermal", "wave", "tidal"}
BATTERY = {"battery", "bess", "li-ion", "lithium", "lithium-ion"}
AT_LIMIT = 0.999          # import at this share of the connection counts as "at the limit"
WEEK_HOURS_MAX = 24 * 8   # snapshots shown for one week


def _unavailable(reason: str) -> dict[str, Any]:
    return {"status": "unavailable", "reason": reason}


def _not_modelled(reason: str) -> dict[str, Any]:
    return {"status": "not_modelled", "reason": reason}


def _ok(**kw) -> dict[str, Any]:
    return {"status": "ok", **kw}


def _carrier_of(n, comp: str, names) -> list[str]:
    df = getattr(n, comp)
    col = df["carrier"] if "carrier" in df.columns else pd.Series("", index=df.index)
    return [str(col.get(x, "")).strip().lower() for x in names]


def _ev_flex_mask(carriers: list[str], kind: str) -> np.ndarray:
    if kind == "ev":
        return np.array([c == "ev" or c.startswith("ev ") or c.startswith("ev_") or c.startswith("ev-")
                         or "electric vehicle" in c for c in carriers], dtype=bool)
    return np.array([c.startswith("flex") or c == "dsr" or "demand response" in c or "demand_response" in c
                     for c in carriers], dtype=bool)


def _stamps(n, mask) -> pd.DatetimeIndex | None:
    sns = (n.snapshots.get_level_values(-1) if is_multi_period(n) else n.snapshots)[mask]
    return sns if isinstance(sns, pd.DatetimeIndex) else None


def _excess(n, mask) -> np.ndarray | None:
    """Curtailed renewable MW per snapshot of the period, None if not established."""
    from services.results.curtailment import compute_curtailment

    payload = compute_curtailment(n, None, None)
    if not payload or not payload.get("columns") or len(payload.get("data") or []) != len(n.snapshots):
        return None
    arr = np.array(payload["data"], dtype=float)
    return np.nan_to_num(arr).sum(axis=1)[mask]


def _series(n, R, mask, capture) -> dict[str, dict[str, Any]]:
    """The per-snapshot MW groups of one period (arrays kept under `_mw`)."""
    T = R["T"]
    gp, g_lab, g_bus, external = R["gp"], R["g_lab"], R["g_bus"], R["external"]
    out: dict[str, dict[str, Any]] = {}

    def gen(sel) -> np.ndarray:
        cols = [j for j, lab in enumerate(g_lab) if sel(lab)]
        return np.maximum(gp[:, cols], 0.0).sum(axis=1) if cols else np.zeros(T)

    has = lambda sel: any(sel(lab) for lab in g_lab)            # noqa: E731
    is_grid = lambda lab: lab == GRID_IMPORT                    # noqa: E731
    is_ren = lambda lab: lab.lower() in RENEWABLE               # noqa: E731
    out["grid"] = _ok(_mw=gen(is_grid)) if has(is_grid) else _not_modelled("no grid connection in this network")

    # export, as the energy flows count it: what branches deliver into a grid bus,
    # plus what a generator off the grid bus absorbs (a grid generator running
    # negative is the other end of the same branch flow: not counted twice)
    exp = np.zeros(T)
    for j, b in enumerate(g_bus):
        if not external[b]:
            exp += np.maximum(-gp[:, j], 0.0)
    for _, pl in R["elems"]:
        for b, p in pl:
            if external[b]:
                exp += np.maximum(-p, 0.0)
    out["export"] = _ok(_mw=exp) if (external.any() or (gp < 0).any() or has(is_grid)) \
        else _not_modelled("no grid connection in this network")

    for key in ("solar", "wind"):
        sel = (lambda lab, k=key: lab.lower() == k)
        out[key] = _ok(_mw=gen(sel)) if has(sel) else _not_modelled(f"no {key} generation in this network")
    gset = lambda lab: not is_grid(lab) and not is_ren(lab)     # noqa: E731
    out["genset"] = _ok(_mw=gen(gset)) if has(gset) else _not_modelled("no fuel-fired generation in this network")

    # EV and flexible load: loads and storages whose carrier names them
    lcar = _carrier_of(n, "loads", R["lnames"])
    for key in ("ev", "flexible"):
        k = "ev" if key == "ev" else "flex"
        lm = _ev_flex_mask(lcar, k)
        sm = _ev_flex_mask([_carrier_of(n, "storage_units", [st.name])[0] if st.name in n.storage_units.index
                            else _carrier_of(n, "stores", [st.name])[0] for st in R["storages"]], k)
        if not lm.any() and not sm.any():
            out[key] = _not_modelled("no " + ("EV" if key == "ev" else "flexible") + " load in this network")
            continue
        mw = R["lp"][:, lm].sum(axis=1) if lm.any() else np.zeros(T)
        for st, m in zip(R["storages"], sm):
            if m:
                mw = mw + st.x
        out[key] = _ok(_mw=mw)

    soc = [st for st in R["storages"]
           if st.name in n.storage_units.index and _carrier_of(n, "storage_units", [st.name])[0] in BATTERY]
    out["battery_soc"] = _ok(_mw=np.sum([st.soc for st in soc], axis=0)) if soc \
        else _not_modelled("no battery in this network")

    shed = R["shed"]
    out["unserved"] = _ok(_mw=shed.sum(axis=1)) if shed is not None \
        else _unavailable("this solve recorded no lost-load capture")
    return out


def _import_capacity(n) -> float | None:
    """The grid connection's capacity in MW, or None when it is not known."""
    links = n.links
    if "eh_role" not in links.columns:
        return None
    rows = links.index[links["eh_role"].astype(str) == "grid_import"]
    if not len(rows):
        return None
    col = "p_nom_opt" if "p_nom_opt" in links.columns else "p_nom"
    cap = float(links.loc[rows, col].fillna(0.0).sum())
    return cap if cap > 0 else None


def _strip(s: dict[str, Any], sl=None) -> dict[str, Any]:
    """The JSON form of a series: `mw` list (rounded), not the private array."""
    if s["status"] != "ok":
        return s
    mw = s["_mw"] if sl is None else s["_mw"][sl]
    return {"status": "ok", "mw": [round(float(v), 4) for v in mw]}


def _heatmap(series, stamps, win: slice) -> dict[str, Any]:
    if stamps is None:
        return {k: _unavailable("the snapshots carry no timestamps") for k in ("import", "export")}
    sel = stamps[win]
    out = {}
    for key, name in (("import", "grid"), ("export", "export")):
        s = series[name]
        if s["status"] != "ok":
            out[key] = s
            continue
        mw = s["_mw"][win]
        df = pd.DataFrame({"d": sel.dayofweek, "h": sel.hour, "v": mw})
        mean = df.groupby(["d", "h"])["v"].mean()
        cnt = df.groupby(["d", "h"])["v"].size()
        grid = [[None] * 24 for _ in range(7)]
        n_ = [[0] * 24 for _ in range(7)]
        for (d, h), v in mean.items():
            grid[int(d)][int(h)] = round(float(v), 4)      # a cell with no snapshot stays None, not 0
            n_[int(d)][int(h)] = int(cnt[(d, h)])
        out[key] = _ok(mean_mw=grid, snapshots=n_)
    return out


def _weeks(stamps) -> list[dict[str, Any]]:
    iso = stamps.isocalendar()
    key = iso["year"].astype(str) + "-W" + iso["week"].astype(str).str.zfill(2)
    out, start = [], 0
    vals = key.to_numpy()
    for i in range(1, len(vals) + 1):
        if i == len(vals) or vals[i] != vals[start]:
            out.append({"key": str(vals[start]), "from": start, "to": i - 1,
                        "start": str(stamps[start]), "end": str(stamps[i - 1])})
            start = i
    return out


def _week_view(series, stamps, week, import_cap) -> dict[str, Any]:
    if stamps is None:
        return _unavailable("the snapshots carry no timestamps")
    weeks = _weeks(stamps)
    k = min(max(int(week or 0), 0), len(weeks) - 1)
    w = weeks[k]
    sl = slice(w["from"], w["to"] + 1)
    if w["to"] - w["from"] + 1 > WEEK_HOURS_MAX * 4:
        return _unavailable("a week holds too many snapshots to draw")
    grid_mw = series["grid"].get("_mw")
    flagged, basis = [], []
    uns = series["unserved"]
    if uns["status"] == "ok":
        basis.append("unserved load")
        flagged += [i for i in range(w["to"] - w["from"] + 1) if uns["_mw"][sl][i] > 1e-6]
    if grid_mw is not None and import_cap:
        basis.append(f"grid import at {import_cap:g} MW connection")
        flagged += [i for i in range(w["to"] - w["from"] + 1) if grid_mw[sl][i] >= AT_LIMIT * import_cap]
    return _ok(weeks=weeks, selected=k, index=[str(t) for t in stamps[sl]],
               series={name: _strip(series[name], sl) for name in SERIES_ORDER},
               flagged=sorted(set(flagged)), flag_basis=basis,
               import_capacity_mw=import_cap)


def _is_calendar_year(stamps, wg) -> str | None:
    """None when the snapshots are a calendar year at their own step, else why not."""
    if stamps is None:
        return "the snapshots carry no timestamps"
    if len(stamps) < 2:
        return "the period has a single snapshot"
    steps = np.diff(stamps.values).astype("timedelta64[s]").astype(float) / 3600.0
    if not np.allclose(steps, steps[0]) or steps[0] <= 0:
        return "the snapshots are not evenly spaced"
    if not np.allclose(wg, steps[0], rtol=1e-6):
        return (f"the run uses representative snapshots (each weighs {float(np.median(wg)):g} h of "
                f"a {steps[0]:g} h step), so calendar months are not modelled")
    if len(set(zip(stamps.year, stamps.month))) < 12:
        return "the snapshots do not span twelve calendar months"
    return None


def _energy(series, key, wg, sel=None) -> float | None:
    s = series[key]
    if s["status"] != "ok":
        return None
    mw = s["_mw"] if sel is None else s["_mw"][sel]
    w = wg if sel is None else wg[sel]
    return float((mw * w).sum())


def _monthly(series, stamps, wg, excess) -> dict[str, Any]:
    why = _is_calendar_year(stamps, wg)
    if why:
        return _unavailable(why)
    months, daily = [], []
    keys = np.array([f"{y}-{m:02d}" for y, m in zip(stamps.year, stamps.month)])
    days_gen = None
    gen_total = np.zeros(len(stamps))
    for k in ("solar", "wind", "genset", "grid"):
        if series[k]["status"] == "ok":
            gen_total += series[k]["_mw"]
    for mk in sorted(set(keys)):
        sel = keys == mk
        row: dict[str, Any] = {"month": mk}
        for k in ("solar", "wind", "genset", "grid", "export"):
            row[k] = _energy(series, k, wg, sel)
        row["excess"] = float((excess[sel] * wg[sel]).sum()) if excess is not None else None
        ndays = len(set(stamps[sel].date))
        row["daily_generation_mwh"] = float((gen_total[sel] * wg[sel]).sum() / ndays) if ndays else None
        months.append(row)
        daily.append(row["daily_generation_mwh"])
    vals = [d for d in daily if d is not None]
    return _ok(months=months, average_daily_generation_mwh=(sum(vals) / len(vals) if vals else None))


def _annual_row(n, period, result_df, capture) -> dict[str, Any]:
    R, why = read_dispatch(n, period_mask(n, period), result_df, capture)
    if R is None:
        return {"period": period, **_unavailable(why or "not established")}
    mask = period_mask(n, period)
    series = _series(n, R, mask, capture)
    ex = _excess(n, mask)
    wg = R["wg"]
    load = float((R["served"].sum(axis=1) * wg).sum())
    return {"period": period, "status": "ok", "load_served": load,
            "excess": float((ex * wg).sum()) if ex is not None else None,
            **{k: _energy(series, k, wg) for k in ("solar", "wind", "genset", "grid", "export")}}


def load_shape(n, period=None, window: tuple[int, int] | None = None, week: int | None = None, *,
               result_df: Callable | None = None, lost_load=None) -> dict[str, Any]:
    """The load-shape views for one period; `window` is an inclusive positional
    (from, to) into that period's snapshots and narrows the heatmaps only."""
    periods = periods_of(n)
    if period is None:
        period = periods[0]
    base = {"status": "ok", "period": period, "periods": periods, "unit": "MW"}
    if period not in periods:
        return {**base, "status": "not_established", "reason": f"no period {period}"}
    mask = period_mask(n, period)
    R, why = read_dispatch(n, mask, result_df, lost_load)
    if R is None:
        return {**base, "status": "not_established", "reason": why}
    series = _series(n, R, mask, lost_load)
    exc = _excess(n, mask)
    series["excess"] = _ok(_mw=exc) if exc is not None else _not_modelled("no curtailable generation in this network")
    stamps = _stamps(n, mask)
    T = R["T"]
    lo, hi = 0, T - 1
    if window is not None:
        lo, hi = max(0, int(window[0])), min(T - 1, int(window[1]))
    win = slice(lo, hi + 1) if hi >= lo else slice(0, 0)
    cap = _import_capacity(n)
    return {
        **base,
        "window": {"from": lo, "to": hi, "snapshots": T,
                   "start": str(stamps[lo]) if stamps is not None and 0 <= lo < T else None,
                   "end": str(stamps[hi]) if stamps is not None and 0 <= hi < T else None},
        "series_status": {k: {kk: vv for kk, vv in series[k].items() if kk != "_mw"} for k in series},
        "heatmaps": _heatmap(series, stamps, win),
        "week": _week_view(series, stamps, week, cap),
        "monthly": _monthly(series, stamps, R["wg"], exc),
        "annual": [_annual_row(n, p, result_df, lost_load) for p in periods],
        "rule": "MW per snapshot; energy = MW × generators weighting, per modelled year.",
    }
