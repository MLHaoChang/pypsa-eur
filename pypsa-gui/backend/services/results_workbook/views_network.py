"""
The network result views' sheets (package 02, slice R1): Capacity Expansion,
Dispatch, Energy flow, Load shape, Storage cycling, Curtailment, Lost load,
Prices, Economics and Emissions.

Each builder reads a run's data from the same functions its tab's route
calls (`services/results/*`, `services.serialization.ts_payload` over the
run's own `result_df`), once per run (`ctx.runs`, the base first; a run that
is not comparable with the base has no network and gets no data), and hands
the payloads to `views.write_view`. Energy derived from a MW series is
Σ p·w over the snapshots with the generators weighting × the period's years
(`period_utils.snapshot_weights`, the basis the Dispatch and Curtailment tabs
use), so a period's figure covers its years and the horizon is their sum.
"""
from __future__ import annotations

from collections.abc import Callable

import pandas as pd

from services.results_workbook.registry import register
from services.results_workbook.views import HORIZON, Key, Table, ViewData, write_view

__all__: list[str] = []

ENERGY_RULE = ("MWh = Σ MW × the generators snapshot weighting × the period's years: a "
               "period's energy covers its years, the horizon is their sum.")


# ── helpers ──────────────────────────────────────────────────────────────

def _multi(n) -> bool:
    return isinstance(n.snapshots, pd.MultiIndex)


def _periods(n) -> list:
    return list(n.snapshots.get_level_values(0).unique()) if _multi(n) else []


def _by(n, per_period: dict, horizon) -> dict:
    """A key's values: each period (multi-period runs) and the horizon."""
    out = {str(p): per_period.get(p, per_period.get(str(p))) for p in _periods(n)}
    out[HORIZON] = horizon
    return out


def _ts_frame(payload: dict) -> pd.DataFrame:
    """A `ts_payload` dict as a long table: Period (multi-period), Snapshot, series."""
    frame = pd.DataFrame(payload["data"], columns=[str(c) for c in payload["columns"]])
    labels = {"Snapshot": payload["index"]}
    if "periods" in payload:
        labels = {"Period": payload["periods"], **labels}
    return pd.concat([pd.DataFrame(labels), frame], axis=1)


def _payload_frame(n, payload: dict) -> pd.DataFrame:
    """A whole-horizon `ts_payload` back on the network's snapshots (for Σ p·w)."""
    if len(payload["index"]) != len(n.snapshots):
        raise ValueError("the payload does not cover every snapshot")
    return pd.DataFrame(payload["data"], columns=[str(c) for c in payload["columns"]],
                        index=n.snapshots, dtype=float)


def _labels(payload: dict) -> int:
    return 2 if "periods" in payload else 1


def _ts_table(run, name: str, unit: str, payload: dict, time: str = "mean") -> Table:
    """A snapshot table, with each row's hours (`snapshot_weights(n, "generators")`)
    so `budget.coarsen` can aggregate it; no hours when the payload does not
    cover the network's snapshots (then it is never coarsened)."""
    from services.period_utils import snapshot_weights
    n = run.network
    w = (snapshot_weights(n, "generators").to_numpy(dtype=float)
         if len(payload["index"]) == len(n.snapshots) else None)
    return Table(name, unit, _ts_frame(payload), _labels(payload), time=time, weights=w)


def _energy(n, df: pd.DataFrame) -> pd.DataFrame:
    """Σ p·w per period (rows) per column; one row labelled HORIZON-less
    period `None` for a single-period run."""
    from services.period_utils import snapshot_weights
    w = snapshot_weights(n, "generators", sns=df.index)
    e = df.astype(float).mul(w, axis=0)
    if _multi(n):
        return e.groupby(level=0).sum()
    return e.sum().to_frame().T.set_axis([None])


def _per_period_table(name: str, unit: str, by_period: pd.DataFrame) -> Table:
    frame = by_period.copy()
    frame.columns = [str(c) for c in frame.columns]
    frame.insert(0, "Period", [HORIZON if p is None else p for p in frame.index])
    return Table(name, unit, frame.reset_index(drop=True))


def _rows(name: str, unit: str, rows: list[dict], labels: list[str],
          values: list[str] | None = None) -> Table | None:
    if not rows:
        return None
    frame = pd.DataFrame(rows)
    values = values or [c for c in frame.columns if c not in labels
                        and pd.api.types.is_numeric_dtype(frame[c])]
    cols = [c for c in labels if c in frame.columns]
    return Table(name, unit, frame[cols + [c for c in values if c in frame.columns]],
                 labels=len(cols))


def _keys_per_column(prefix: str, unit: str, energy: pd.DataFrame, n) -> list[Key]:
    keys = []
    for col in energy.columns:
        per = {p: float(v) for p, v in energy[col].items()}
        keys.append(Key(f"{prefix} — {col}", unit,
                        _by(n, per, float(energy[col].sum()))))
    return keys


def _view(view_id: str, title: str, read: Callable[..., ViewData | None]):
    """Registers the builder: `read(run)` once per run that has a network."""

    @register(view_id)
    def build(wb, ctx) -> bool:
        return write_view(wb, ctx, view_id, title,
                          [read(run) if run.network is not None else None for run in ctx.runs])
    return build


def _ts(run, accessor: str, attr: str) -> dict | None:
    """The `_serve_ts` payload of the run's frame, or None where it answers 204."""
    from services.serialization import ts_payload
    df = run.result_df(run.network, accessor, attr, "lopf")
    if df is None or df.empty:
        return None
    return ts_payload(df)


def _frame(run, accessor: str, attr: str) -> pd.DataFrame | None:
    df = run.result_df(run.network, accessor, attr, "lopf")
    return None if df is None or df.empty else df


# ── Capacity Expansion ───────────────────────────────────────────────────

def _sum_or_none(values) -> float | None:
    """A total only when every part is known (ADR-0001: no partial sums)."""
    values = list(values)
    if any(v is None or (isinstance(v, float) and v != v) for v in values):
        return None
    return float(sum(values))


def _capacity_by_period(n) -> pd.DataFrame | None:
    """Optimal capacity (MW) of the assets active in each period, by carrier."""
    if not _multi(n):
        return None
    rows = {}
    for comp, attr in (("Generator", "p_nom_opt"), ("StorageUnit", "p_nom_opt")):
        static = n.components[comp].static
        if static.empty or attr not in static:
            continue
        for p in _periods(n):
            active = n.components[comp].get_active_assets(p).reindex(static.index).fillna(False)
            mw = static.loc[active.astype(bool), attr].groupby(
                static.loc[active.astype(bool), "carrier"].astype(str)).sum()
            for carrier, v in mw.items():
                rows.setdefault(p, {})[carrier] = rows.get(p, {}).get(carrier, 0.0) + float(v)
    return pd.DataFrame.from_dict(rows, orient="index").fillna(0.0) if rows else None


def _capex(run) -> ViewData | None:
    from services.results.cost_breakdown import compute_cost_breakdown
    from services.results.economics_by_carrier import compute_economics_by_carrier
    n = run.network
    cb = compute_cost_breakdown(n, run.cfg)
    ebc = compute_economics_by_carrier(n, run.cfg, run.lost_load, result_df=run.result_df)
    if cb is None and not ebc.get("available"):
        return None
    d = ViewData(notes=["Costs as the Capacity expansion tab reads them (cost breakdown and "
                        "economics by carrier); capacity is each asset's optimal size, counted in "
                        "the periods it is active."])
    if cb is not None:
        by_period = cb.get("by_period") or []
        for key, label in (("capex", "Capex (annualised)"), ("opex", "Opex"),
                           ("fom", "Fixed O&M"), ("total", "Total system cost")):
            d.keys.append(Key(label, "EUR", _by(n, {b["period"]: b.get(key) for b in by_period},
                                                cb.get(key))))
        d.keys.append(Key("Capex of new build", "EUR", {HORIZON: cb.get("capex_expansion")}))
        if by_period:
            d.tables.append(_rows("Cost by period", "EUR", by_period, ["period"],
                                  ["capex", "opex", "fom", "total"]))
            for metric, label in (("capex", "Capex"), ("opex", "Opex")):
                frame = pd.DataFrame({b["period"]: {c["carrier"]: c.get(metric)
                                                    for c in b.get("by_carrier") or []}
                                      for b in by_period}).T
                if not frame.empty:
                    d.tables.append(_per_period_table(f"{label} by carrier and period", "EUR",
                                                      frame))
        d.tables += [t for t in (
            _rows("Cost by component", "EUR", cb.get("by_component") or [], ["component"]),
            _rows("Cost by carrier", "EUR", cb.get("by_carrier") or [], ["component", "carrier"]),
        ) if t]
    by_carrier = ebc.get("by_carrier") or {}
    if by_carrier:
        rows = [{"carrier": c, **{m: (v.get(m) or {}).get("total") for m in v}}
                for c, v in by_carrier.items()]
        d.tables.append(_rows("Economics by carrier", "MEUR, GWh, EUR/MWh", rows, ["carrier"]))
    cap = _capacity_by_period(n)
    if cap is not None:
        d.tables.append(_per_period_table("Optimal capacity by carrier and period", "MW", cap))
        for carrier in cap.columns:
            d.keys.append(Key(f"Optimal capacity — {carrier}", "MW",
                              {str(p): float(v) for p, v in cap[carrier].items()}))
    return d


# ── Dispatch ─────────────────────────────────────────────────────────────

def _dispatch(run) -> ViewData | None:
    n = run.network
    gen = _ts(run, "generators_t", "p")
    if gen is None:
        return None
    d = ViewData(notes=[ENERGY_RULE])
    df = _frame(run, "generators_t", "p")
    carrier = n.generators.carrier.reindex(df.columns).fillna("").astype(str)
    energy = _energy(n, df.T.groupby(carrier.values).sum().T)
    d.keys += _keys_per_column("Generation", "MWh", energy, n)
    d.tables.append(_per_period_table("Energy by carrier and period", "MWh", energy))
    d.tables.append(_ts_table(run, "Generator dispatch", "MW", gen))
    for accessor, attr, name, unit in (
            ("storage_units_t", "p", "Storage dispatch", "MW (+ discharge, − charge)"),
            ("stores_t", "p", "Store dispatch", "MW (+ discharge, − charge)"),
            ("links_t", "p0", "Link flow (bus0)", "MW"),
            ("loads_t", "p", "Load", "MW")):
        ts = _ts(run, accessor, attr)
        if ts is not None:
            d.tables.append(_ts_table(run, name, unit, ts))
    return d


# ── Energy flow ──────────────────────────────────────────────────────────

def _flows(run) -> ViewData | None:
    from services.results.energy_flows import energy_flows
    n = run.network
    rows, gen, dest, resid, notes = [], {}, {}, {}, []
    for period in _periods(n) or [None]:
        p = energy_flows(n, period, None, result_df=run.result_df, lost_load=run.lost_load)
        if p.get("status") != "ok":
            notes.append(f"Period {period}: {p.get('status')} ({p.get('reason', '')}).")
            continue
        for r in p.get("rows") or []:
            rows.append({"Period": HORIZON if period is None else period, **r})
        c = p.get("conservation") or {}
        key = None if period is None else period
        gen[key], dest[key], resid[key] = (c.get("generation_mwh"), c.get("destinations_mwh"),
                                           c.get("residual_mwh"))
    if not rows:
        return ViewData(notes=notes) if notes else None
    d = ViewData(notes=["Source → via → destination MWh per period, as the Energy flow tab "
                        "reads them; each period's energy per modelled "
                        "year of that period.", *notes])

    def tot(x):
        return sum(v for v in x.values() if v is not None) if all(
            v is not None for v in x.values()) else None
    for label, x in (("Generation", gen), ("Delivered to destinations", dest),
                     ("Conservation residual", resid)):
        d.keys.append(Key(label, "MWh", {**{str(p): v for p, v in x.items() if p is not None},
                                         HORIZON: tot(x)}))
    flows = pd.DataFrame(rows)
    d.tables.append(Table("Energy flows", "MWh",
                          flows[["Period", "source", "via", "destination", "mwh"]], labels=4))
    pivot = flows.pivot_table(index="Period", columns="source", values="mwh", aggfunc="sum",
                              sort=False).fillna(0.0)
    pivot.columns = [str(c) for c in pivot.columns]
    d.tables.append(Table("Energy by source and period", "MWh", pivot.reset_index()))
    return d


# ── Load shape ───────────────────────────────────────────────────────────

_SHAPE = ("load_served", "solar", "wind", "genset", "grid", "excess")


def _loadshape(run) -> ViewData | None:
    from services.results.load_shape import load_shape
    n = run.network
    first = load_shape(n, None, result_df=run.result_df, lost_load=run.lost_load)
    annual = [a for a in first.get("annual") or [] if a.get("status") == "ok"]
    if first.get("status") != "ok" or not annual:
        return None
    d = ViewData(notes=["Annual and monthly totals as the Load shape tab "
                        "reads them, per modelled year of each period; the per-snapshot series "
                        "are on the Dispatch sheet."])
    for k in _SHAPE:
        per = {a["period"]: a.get(k) for a in annual}
        if any(v is not None for v in per.values()):
            d.keys.append(Key(k.replace("_", " ").capitalize(), "MWh/a",
                              {str(p): v for p, v in per.items()}))
    frame = pd.DataFrame([{"Period": a["period"], **{k: a.get(k) for k in _SHAPE}}
                          for a in annual]).dropna(axis=1, how="all")
    d.tables.append(Table("Annual totals", "MWh/a", frame))
    months = []
    for period in _periods(n) or [None]:
        p = first if period is None or period == first.get("period") else load_shape(
            n, period, result_df=run.result_df, lost_load=run.lost_load)
        m = p.get("monthly") or {}
        if m.get("status") == "ok":
            months += [{"Period": HORIZON if period is None else period, **r}
                       for r in m.get("months") or []]
        elif m.get("reason"):
            d.notes.append(f"Monthly totals: {m['reason']}.")
    if months:
        frame = pd.DataFrame(months).dropna(axis=1, how="all")
        d.tables.append(Table("Monthly totals", "MWh", frame, labels=2))
    d.notes = list(dict.fromkeys(d.notes))
    return d


# ── Storage cycling ──────────────────────────────────────────────────────

def _storage(run) -> ViewData | None:
    from services.compare.storage_cycling import _compute_storage_cycling_summary
    n = run.network
    dispatch = _ts(run, "storage_units_t", "p")
    if dispatch is None:
        return None
    cyc = _compute_storage_cycling_summary(n, _periods(n), _multi(n), True)
    if not cyc.available:
        return None
    d = ViewData(notes=["Equivalent full cycles = throughput ÷ (2 × energy capacity), the "
                        "Storage cycling tab's convention: per solved year of each period, the "
                        "horizon figure is the mean over the periods."])
    for u in cyc.by_unit:
        d.keys.append(Key(f"Cycles — {u.name}", "cycles",
                          {**u.cycles.by_period, HORIZON: u.cycles.total}))
        d.keys.append(Key(f"Throughput — {u.name}", "MWh",
                          {**u.throughput_mwh.by_period, HORIZON: u.throughput_mwh.total}))
    rows = [{"name": u.name, "carrier": u.carrier, "p_nom_mw": u.p_nom_mw,
             "energy_mwh": u.energy_mwh, "cycles": u.cycles.total,
             "throughput_mwh": u.throughput_mwh.total} for u in cyc.by_unit]
    if rows:
        d.tables.append(_rows("Storage units", "MW, MWh, cycles", rows, ["name", "carrier"]))
    if _periods(n) and cyc.by_unit:
        frame = pd.DataFrame({u.name: {p: u.cycles.by_period.get(str(p)) for p in _periods(n)}
                              for u in cyc.by_unit})
        d.tables.append(_per_period_table("Cycles by unit and period", "cycles", frame))
    d.tables.append(_ts_table(run, "Storage dispatch", "MW (+ discharge, − charge)",
                                    dispatch))
    soc = _ts(run, "storage_units_t", "state_of_charge")
    if soc is not None:
        d.tables.append(_ts_table(run, "State of charge", "MWh", soc, "state"))
    return d


# ── Curtailment ──────────────────────────────────────────────────────────

def _curtailment(run) -> ViewData | None:
    from services.results.curtailment import compute_curtailment, curtailment_by_cause
    n = run.network
    payload = compute_curtailment(n, None, None)
    if payload is None:
        return None
    d = ViewData(notes=[ENERGY_RULE])
    energy = _energy(n, _payload_frame(n, payload))
    d.keys += _keys_per_column("Curtailed energy", "MWh", energy, n)
    d.tables.append(_per_period_table("Curtailed energy by plant and period", "MWh", energy))
    d.tables.append(_ts_table(run, "Curtailment", "MW", payload))
    cause = curtailment_by_cause(n, getattr(run.cfg, "commercial", None))
    if cause and cause.get("plants"):
        rows = [{"plant": g, "total_mwh": v.get("total_mwh"), "available_mwh": v.get("available_mwh"),
                 **{f"{c} MWh": m for c, m in (v.get("by_cause_mwh") or {}).items()}}
                for g, v in cause["plants"].items()]
        d.tables.append(_rows("Curtailment by cause", "MWh", rows, ["plant"]))
        if cause.get("status") != "ok":
            d.notes.append(f"Causes not established: {cause.get('reason')}.")
    return d


# ── Lost load ────────────────────────────────────────────────────────────

def _lostload(run) -> ViewData | None:
    from services.results.lost_load import lost_load_payload
    n = run.network
    p = lost_load_payload(n, run.lost_load, run.solved_settings or (lambda: None))
    if p is None:
        return None
    d = ViewData(notes=["Lost load as the Lost load tab reads it. "
                        + ("VoLL was applied in the solve." if p.get("voll_applied") else
                           "VoLL was not applied in the solve: lost load was not modelled.")])
    energy = _energy(n, _payload_frame(n, p)) if p.get("columns") else None
    per = ({k: float(v) for k, v in energy.sum(axis=1).items()} if energy is not None and _multi(n)
           else {})
    d.keys += [Key("Lost load", "MWh", _by(n, per, p.get("total_mwh"))),
               Key("Lost-load cost", "EUR", {HORIZON: p.get("total_cost_eur")}),
               Key("VoLL", "EUR/MWh", {HORIZON: p.get("voll_eur_per_mwh")})]
    sh = p.get("shed_hours")
    d.keys.append(Key("Shed hours (electrical buses)", "h",
                      _by(n, (sh or {}).get("by_period") or {}, (sh or {}).get("total"))))
    if p.get("columns"):
        d.tables.append(_per_period_table("Lost load by bus and period", "MWh", energy))
        d.tables.append(_ts_table(run, "Lost load", "MW", p))
    return d


# ── Prices ───────────────────────────────────────────────────────────────

def _prices(run) -> ViewData | None:
    from services.results.prices import compute_price_drivers, compute_prices
    n = run.network
    p = compute_prices(n, "lopf", None, None, result_df=run.result_df)
    if p is None or not p.get("columns"):
        return None
    d = ViewData(notes=["Marginal prices per bus and snapshot as the Prices tab "
                        "reads them; the mean is the plain mean over the "
                        "snapshots."])
    periods = p.get("periods") or [None] * len(p["index"])
    df = pd.DataFrame(p["data"], columns=[str(c) for c in p["columns"]], dtype=float)
    df.insert(0, "Period", periods)
    stats = {}
    for bus in df.columns[1:]:
        g = df.groupby("Period", dropna=False)[bus]
        for label, agg in (("Mean price", "mean"), ("Max price", "max"), ("Min price", "min")):
            per = {k: float(v) for k, v in g.agg(agg).items()} if _multi(n) else {}
            whole = float(getattr(df[bus], agg)())
            stats.setdefault((label, bus), (per, whole))
    for (label, bus), (per, whole) in stats.items():
        d.keys.append(Key(f"{label} — {bus}", "EUR/MWh", _by(n, per, whole)))
    d.keys.append(Key("Snapshots with a negative price", "count", {HORIZON: p.get("negative_hours")}))
    mean = df.groupby("Period", dropna=False).mean() if _multi(n) else \
        df.drop(columns="Period").mean().to_frame().T.set_axis([None])
    d.tables.append(_per_period_table("Mean price by bus and period", "EUR/MWh", mean))
    curve = {}
    for period in _periods(n) or [None]:
        sel = df if period is None else df[df["Period"] == period]
        for bus in df.columns[1:]:
            name = bus if period is None else f"{bus} {period}"
            curve[name] = sel[bus].sort_values(ascending=False).reset_index(drop=True)
    dur = pd.DataFrame(curve)
    dur.insert(0, "Rank", range(1, len(dur) + 1))
    d.tables.append(Table("Price duration", "EUR/MWh", dur))
    d.tables.append(_ts_table(run, "Prices", "EUR/MWh", p, "price"))
    drivers = compute_price_drivers(n, 2000.0, 200)  # the route's defaults
    if drivers and drivers.get("rows"):
        d.tables.append(_rows(f"Price drivers (price ≥ {drivers['threshold']:g})", "EUR/MWh",
                              drivers["rows"], ["snapshot", "bus", "marginal_gen", "carrier"]))
    return d


# ── Economics ────────────────────────────────────────────────────────────

_ASSET_VALUES = ("p_nom_opt_mw", "energy_mwh", "capacity_factor", "revenue_eur",
                 "vom_cost_eur", "fixed_cost_eur", "fom_cost_eur", "net_profit_eur",
                 "lcoe_eur_per_mwh", "avg_price_eur_per_mwh")


def _economics(run) -> ViewData | None:
    from services.results.asset_economics import compute_asset_economics
    n = run.network
    ae = compute_asset_economics(n, run.cfg, result_df=run.result_df)
    assets = [dict(r, component=comp) for comp in ("generators", "storage_units", "stores", "links")
              for r in ae.get(comp) or []]
    if not assets:
        return None
    d = ViewData(notes=["Per-asset economics as the Economics tab "
                        "reads them.", *(ae.get("reading_notes") or [])])
    by_period = [{"period": b.get("period"), "name": a["name"], **b}
                 for a in assets for b in a.get("by_period") or []]
    for metric, label in (("revenue_eur", "Revenue"), ("net_profit_eur", "Net profit")):
        parts: dict = {}
        for b in by_period:
            parts.setdefault(b["period"], []).append(b.get(metric))
        per = {k: _sum_or_none(v) for k, v in parts.items()}
        d.keys.append(Key(f"{label} (all assets)", "EUR",
                          _by(n, per, _sum_or_none(a.get(metric) for a in assets))))
    if by_period:
        frame = pd.DataFrame(by_period).pivot_table(index="period", columns="name",
                                                    values="net_profit_eur", sort=False)
        d.tables.append(_per_period_table("Net profit by asset and period", "EUR", frame))
    d.tables.append(_rows("Asset economics", "EUR, MWh, MW", assets,
                          ["name", "component", "carrier", "bus"], list(_ASSET_VALUES)))
    if by_period:
        d.tables.append(_rows("Asset economics by period", "EUR, MWh", by_period,
                              ["period", "name"]))
    return d


# ── Emissions ────────────────────────────────────────────────────────────

def _emissions(run) -> ViewData | None:
    from services.results.emissions import compute_emissions
    n = run.network
    e = compute_emissions(n, "lopf", result_df=run.result_df)
    if e is None:
        return None
    d = ViewData(notes=["Emissions as the Emissions tab reads them."])
    per = {b["period"]: b.get("total_tCO2") for b in e.get("by_period") or []}
    d.keys.append(Key("Total emissions", "tCO2", _by(n, per, e.get("total_tCO2"))))
    for c in e.get("by_carrier") or []:
        cp = {b["period"]: next((x.get("tCO2") for x in b.get("by_carrier") or []
                                 if x.get("carrier") == c["carrier"]), None)
              for b in e.get("by_period") or []}
        d.keys.append(Key(f"Emissions — {c['carrier']}", "tCO2", _by(n, cp, c.get("tCO2"))))
    rows = [{"Period": b["period"], **{x["carrier"]: x.get("tCO2") for x in b.get("by_carrier") or []}}
            for b in e.get("by_period") or []]
    if rows:
        d.tables.append(Table("Emissions by carrier and period", "tCO2", pd.DataFrame(rows)))
    d.tables += [t for t in (
        _rows("Emissions by carrier", "tCO2", e.get("by_carrier") or [], ["carrier"]),
        _rows("Emissions by generator", "tCO2, MWh", e.get("by_generator") or [],
              ["name", "carrier"]),
    ) if t]
    return d


_view("capex", "Capacity expansion", _capex)
_view("dispatch", "Dispatch", _dispatch)
_view("flows", "Energy flow", _flows)
_view("loadshape", "Load shape", _loadshape)
_view("storage", "Storage cycling", _storage)
_view("curtailment", "Curtailment", _curtailment)
_view("lostload", "Lost load", _lostload)
_view("prices", "Prices", _prices)
_view("economics", "Economics", _economics)
_view("emissions", "Emissions", _emissions)
