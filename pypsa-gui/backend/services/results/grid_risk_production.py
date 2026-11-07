"""
Grid-to-process ticket 13 (design §5, D5): "Grid risk to production", each grid result turned into tonnes per
year, then EUR, with no new loop and no re-solve.

``grid_risk_production(n, lost_load, campus=...)`` returns one line per grid risk, or None when the network has no
process unit (the section is omitted):

* ``ride_through``: the campus run's ``campus_dip_trips.csv`` ``total`` row as written (12b: trips × restart time ×
  rate × mass pull), never recomputed. ``unknown`` units make it partial and a lower bound, as the screen's own
  total says.
* ``island``: the grid connection's outage is the island event. Events per year = the period's year hours × q /
  MTTR, (q, MTTR) of the PoC import Link from ``occurrence.resolve_outage_params``, the formula of
  ``process_loss.expected_outage_loss``. Two rows from ``campus_restart.csv`` (``transition == "island"``,
  ``level == "unit"``):
  - ``outside_blocks``: the units no pickup block fits (``restart_sequence.NO_BLOCK``, no block assigned), lost
    for the whole outage: Σ rate_tph × mass_pull × MTTR × events per year. No inventory credit (conservative);
  - ``in_block_restart``: the restarted units' lost tonnes per event (pickup wait + restart, ``lost_t``) × events
    per year.
* ``n_1``: each site-transformer Link (an electricity-to-electricity Link that is neither the PoC import nor a
  process unit, with outage data) through ``process_loss.expected_outage_loss``: the units it feeds × q × MTTR,
  on the solved dispatch, inventory-aware.
* ``connection``: the LP's own lost production (the shortfall accessor, ``process_products.shortfalls``), shown,
  not recomputed, annualised per period by the period's weighted hours as ``expected_outage_loss`` does.

Every figure is t/yr per period (``"_"`` on a single-period network, the period's year otherwise); a horizon total
is never added to a per-year line. EUR is the tonnes at the shortfall product's lost-production value (the one
accessor); with no value it is None ("Unavailable", ``lost_production_value_missing``). A missing input gives
tonnes None with ``missing`` naming it, never 0.

Overlap (GK A, 2026-11-06): the lines are expected values over random events with no hour location, so the same
event can sit in two lines (an LP shortfall hour that is also an island hour). The total is Σ of the known lines,
labelled ``OVERLAP_LABEL``, and ``incomplete`` when any line is missing. Imports no router.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from services import process_products as PP
from services.adequacy import process_loss as PL
from services.solver.process_uc import process_mask
from datetime import UTC

OVERLAP_RULE = "upper_bound"
OVERLAP_LABEL = "upper bound (lines may overlap)"
LINE_KEYS = ("ride_through", "island", "n_1", "connection")
LABELS = {
    "ride_through": "Ride-through (voltage dips)",
    "island": "Island (grid outage)",
    "n_1": "N-1 (site transformer)",
    "connection": "Connection curtailment",
    "outside_blocks": "Units outside the pickup blocks, lost for the outage",
    "in_block_restart": "In-block units' restart loss",
}
DIP_TRIPS_CSV = "campus_dip_trips.csv"
RESTART_CSV = "campus_restart.csv"
NETWORK_FILE = "network.nc"
ELECTRIC_CARRIERS = ("AC", "DC")

LPV_MISSING = "lost_production_value_missing"
SEVERAL_PRODUCTS = "several_shortfall_products"
CONSERVATIVE = "no_inventory_credit"
LOWER_BOUND = "lower_bound"
STALE = "stale"


@dataclass(frozen=True)
class CampusRun:
    """The last campus run as the route found it: its run directory (None: no run), whether the campus state calls
    it stale, the units 12b-3 calls out of date, and the run's stamp."""

    run_dir: Path | None = None
    stale: bool = False
    out_of_date: tuple[str, ...] = ()
    stamp: dict = field(default_factory=dict)


# ── helpers ──────────────────────────────────────────────────────────────


def _num(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _rows(path: Path) -> list[dict] | None:
    if not path.is_file():
        return None
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


def _periods(n) -> list:
    return (
        list(n.snapshots.get_level_values(0).unique())
        if isinstance(n.snapshots, pd.MultiIndex)
        else [None]
    )


def _key(p) -> str:
    return "_" if p is None else str(int(p))


def _cell(t, eur, eur_flag=None) -> dict:
    return {"t_per_year": t, "eur_per_year": eur, "eur_flag": eur_flag}


def _target(section, panel=None, component=None, entity=None, field=None) -> dict:
    """Where the input is set, as the app's flow target (`FlowTarget`)."""
    return {
        "section": section,
        "panel": panel,
        "component": component,
        "entity": entity,
        "field": field,
    }


#: The campus study's dips and restarts (section 10's campus results); the frontend's word for it.
CAMPUS_RUN = ("campus study run", "campus_run", _target("results", "campusResults"))


def _need(text, word, target) -> tuple:
    """A missing input: its text (chat, report), the frontend's word key (a form field, else a named place) and
    where it is set."""
    return text, word, target


def _line(
    key,
    *,
    source_files,
    stamp=None,
    stale=False,
    missing=(),
    basis="",
    flags=(),
    rows=(),
    by_period=None,
):
    needs = [m if isinstance(m, tuple) else _need(m, None, None) for m in missing]
    missing = [m[0] for m in needs]
    missing_inputs = [{"word": m[1], "target": m[2]} for m in needs]
    flags = list(dict.fromkeys(flags))
    if stale:
        flags.append(STALE)
    by_period = by_period or {}
    known = any(c["t_per_year"] is not None for c in by_period.values())
    status = (
        "missing"
        if missing or not known
        else ("partial" if LOWER_BOUND in flags else "ok")
    )
    return {
        "key": key,
        "label": LABELS[key],
        "status": status,
        "missing": missing,
        "missing_inputs": missing_inputs,
        "flags": flags,
        "basis": basis,
        "source_files": list(source_files),
        "stamp": stamp or {},
        "stale": bool(stale),
        "rows": list(rows),
        "by_period": by_period,
    }


@dataclass(frozen=True)
class _Prices:
    """EUR per t of the campus lines' tonnes. One shortfall product: its value prices any sum (``per_t``, or None
    with ``flag``). Several (``flag`` is ``SEVERAL_PRODUCTS``): each unit row is priced at the product the campus
    run wrote on it (the bus its mass pull walk ends at), from ``by_product`` (None when unpriced, never 0)."""

    per_t: float | None
    flag: str | None
    by_product: dict = field(default_factory=dict)

    def eur(self, t, parts=None) -> tuple[float | None, str | None]:
        """(EUR, flag) of ``t`` tonnes; ``parts``: the (tonnes, product) of the unit rows ``t`` is the sum of."""
        if t is None or self.flag != SEVERAL_PRODUCTS:
            return _eur(t, self.per_t, self.flag)
        if parts is None or not math.isclose(
            sum(pt for pt, _ in parts), t, rel_tol=1e-6, abs_tol=1e-9
        ):
            return None, SEVERAL_PRODUCTS  # never priced on a part of the tonnes
        eur = 0.0
        for pt, prod in parts:
            if not pt:
                continue  # a row with no tonnes costs nothing, whatever its product
            if prod not in self.by_product:  # "" too
                return (
                    None,
                    SEVERAL_PRODUCTS,
                )  # a run from before the product column, or a product not short
            if self.by_product[prod] is None:
                return None, LPV_MISSING
            eur += pt * self.by_product[prod]
        return eur, None


def _prices(n, lost_load) -> _Prices:
    """The prices of the campus lines' tonnes: each shortfall product's lost-production value over its tonne
    factor (None when it has no value). One product prices the lines as a whole; several price each unit row at
    the product the campus run names on it."""
    by_product = {}
    for s in PP.shortfalls(n, lost_load):
        if s.bus not in by_product:
            tpu = PP.t_per_unit(n, s.bus)
            by_product[s.bus] = None if s.value is None or not tpu else s.value / tpu
    if len(by_product) != 1:
        return _Prices(
            None, SEVERAL_PRODUCTS if by_product else LPV_MISSING, by_product
        )
    per_t = next(iter(by_product.values()))
    return _Prices(per_t, None if per_t is not None else LPV_MISSING, by_product)


def eur_inputs(n, lost_load) -> list[dict]:
    """Where each shortfall product's lost-production value is set: its shortfall Generator's running cost when it
    has one, else the bus's lost-production value (the accessor's order)."""
    out = []
    for s in PP.shortfalls(n, lost_load):
        if s.source == "generator":
            out.append(
                {
                    "product": s.bus,
                    "word": "marginal_cost",
                    "set": s.value is not None,
                    "target": _target(
                        "assets", None, "Generator", s.name, "marginal_cost"
                    ),
                }
            )
        else:
            out.append(
                {
                    "product": s.bus,
                    "word": "lost_production_value",
                    "set": s.value is not None,
                    "target": _target(
                        "assets", None, "Bus", s.bus, "lost_production_value"
                    ),
                }
            )
    return out


def _eur(t, per_t, flag):
    if t is None:
        return None, None
    return (None, flag) if per_t is None else (t * per_t, None)


def _import_link(n) -> tuple[str | None, str | None]:
    links = _import_link_all(n)
    if not links:
        return None, "PoC import Link (eh_role grid_import)"
    if len(links) > 1:
        return None, f"one PoC import Link (found {len(links)}: {', '.join(links)})"
    return links[0], None


def _outage(n, link: str) -> tuple[float | None, float | None]:
    from services.adequacy.occurrence import resolve_outage_params

    row = resolve_outage_params(n, "links").loc[link]
    if row["source"] == "missing":
        return None, None
    return _num(row["rate"]), _num(row["mttr_hours"])


# ── the lines ────────────────────────────────────────────────────────────


def _ride_through(n, campus: CampusRun, prices: _Prices, periods) -> dict:
    src = [DIP_TRIPS_CSV]
    rows = None if campus.run_dir is None else _rows(campus.run_dir / DIP_TRIPS_CSV)
    if rows is None:
        return _line("ride_through", source_files=src, missing=[CAMPUS_RUN])
    total = next((r for r in rows if r.get("level") == "total"), None)
    units = [r for r in rows if r.get("level") == "unit"]
    known = int(_num((total or {}).get("known_count")) or 0)
    t = _num((total or {}).get("lost_t_per_year"))
    flags = [f"dip_stats_source:{(total or {}).get('dip_stats_source') or 'unknown'}"]
    if str((total or {}).get("incomplete")).lower() == "true":
        flags.append(LOWER_BOUND)
    detail = [
        {
            "unit": r["unit"],
            "lost_t_per_year": _num(r.get("lost_t_per_year")),
            "verdict": r.get("verdict"),
            "note": r.get("note") or "",
        }
        for r in units
    ]
    stale = campus.stale and bool(
        set(campus.out_of_date) & {r["unit"] for r in units} or not campus.out_of_date
    )
    if total is None or t is None or not known:
        missing = [
            _need(
                f"ride-through inputs of {r['unit']}",
                "ride_through_curve",
                _target("assets", None, "Link", r["unit"], "ride_through_curve"),
            )
            for r in units
            if r.get("verdict") == "unknown"
        ]
        return _line(
            "ride_through",
            source_files=src,
            stamp=campus.stamp,
            stale=stale,
            missing=missing or [CAMPUS_RUN],
            flags=flags,
            rows=detail,
        )
    parts = [
        (d["lost_t_per_year"], _product(r))
        for d, r in zip(detail, units)
        if d["lost_t_per_year"] is not None
    ]
    eur, ef = prices.eur(t, parts)
    return _line(
        "ride_through",
        source_files=src,
        stamp=campus.stamp,
        stale=stale,
        flags=flags,
        rows=detail,
        basis="the campus study's ride-through total as it wrote it: trips per year × restart time × rate × "
        "mass pull; not recomputed",
        by_period={_key(p): _cell(t, eur, ef) for p in periods},
    )


def _island(n, campus: CampusRun, prices: _Prices, periods) -> dict:
    src = [RESTART_CSV, NETWORK_FILE]
    try:
        from gridspine.static.restart_sequence import NO_BLOCK
    except ImportError:  # a build without gridspine has no restart screen
        return _line(
            "island",
            source_files=src,
            missing=["the gridspine restart screen (not in this build)"],
        )
    missing = []
    rows = None if campus.run_dir is None else _rows(campus.run_dir / RESTART_CSV)
    if rows is None:
        missing.append(CAMPUS_RUN)
    link, why = _import_link(n)
    q = mttr = None
    if link is None:
        missing.append(_need(why, "grid_import", _target("connection")))
    else:
        q, mttr = _outage(n, link)
        if q is None or not mttr or mttr <= 0:
            field = "outage_rate_value" if q is None else "mttr_hours"
            missing.append(
                _need(
                    f"outage rate and MTTR of {link}",
                    field,
                    _target("assets", None, "Link", link, field),
                )
            )
    if missing:
        return _line("island", source_files=src, missing=missing)
    units = [
        r for r in rows if r.get("level") == "unit" and r.get("transition") == "island"
    ]
    outside, inside, flags = [], [], [CONSERVATIVE]
    for r in units:
        no_block = not (r.get("block") or "").strip()
        if no_block and NO_BLOCK in (r.get("note") or ""):
            outside.append(r)
        elif no_block or _num(r.get("lost_t")) is None:
            flags.append(
                LOWER_BOUND
            )  # no rated MW, or no restart input: not counted, never 0
            if not no_block:
                inside.append(r)
        else:
            inside.append(r)
    per_hour, unknown_out, out_parts = 0.0, [], []  # out_parts: (t/h, product) per unit
    for r in outside:
        rate, mp = _num(r.get("rate_tph")), _num(r.get("mass_pull"))
        if rate is None or mp is None:
            unknown_out.append(r["unit"])
        else:
            per_hour += rate * mp
            out_parts.append((rate * mp, _product(r)))
    if unknown_out:
        flags.append(LOWER_BOUND)
    in_parts = [  # (t per event, product) per unit
        (_num(r.get("lost_t")), _product(r))
        for r in inside
        if _num(r.get("lost_t")) is not None
    ]
    per_event_in = sum(pt for pt, _ in in_parts)
    totals, outside_bp, inside_bp = {}, {}, {}
    for p in periods:
        events = PL._year_hours(p) * q / mttr
        t_out, t_in = per_hour * mttr * events, per_event_in * events
        e_out = prices.eur(t_out, [(v * mttr * events, b) for v, b in out_parts])
        e_in = prices.eur(t_in, [(v * events, b) for v, b in in_parts])
        outside_bp[_key(p)] = _cell(t_out, *e_out)
        inside_bp[_key(p)] = _cell(t_in, *e_in)
        t = t_out + t_in
        totals[_key(p)] = {
            **_cell(t, *_island_eur(t, prices, e_out, e_in)),
            "events_per_year": events,
        }
    names = {r["unit"] for r in units}
    stale = campus.stale and bool(
        set(campus.out_of_date) & names or not campus.out_of_date
    )
    detail = [
        {
            "key": "outside_blocks",
            "label": LABELS["outside_blocks"],
            "units": [r["unit"] for r in outside],
            "flags": [CONSERVATIVE] + ([LOWER_BOUND] if unknown_out else []),
            "by_period": outside_bp,
        },
        {
            "key": "in_block_restart",
            "label": LABELS["in_block_restart"],
            "units": [r["unit"] for r in inside],
            "flags": [],
            "by_period": inside_bp,
        },
    ]
    return _line(
        "island",
        source_files=src,
        stamp=campus.stamp,
        stale=stale,
        flags=flags,
        rows=detail,
        basis=f"an outage of the grid import link is the island event: events per year = year hours × "
        f"outage rate / repair time (rate {q:g}, repair time {mttr:g} h); units outside every pickup "
        "block are lost for the repair time at rate × mass pull, with no inventory credit "
        "(conservative); units in a block lose their pickup wait and restart per event, as the "
        "campus study's restart screen gives it",
        by_period=totals,
    )


def _product(row: dict) -> str:
    """The shortfall product a campus unit row's tonnes are of; "" for a run from before the column."""
    return (row.get("product") or "").strip()


def _island_eur(t, prices: _Prices, e_out, e_in) -> tuple[float | None, str | None]:
    """The island line's EUR: one product prices its tonnes as a whole; several, the sum of its two rows."""
    if prices.flag != SEVERAL_PRODUCTS:
        return prices.eur(t)
    for eur, flag in (e_out, e_in):
        if eur is None:
            return None, flag
    return e_out[0] + e_in[0], None


def _site_transformers(n) -> list[str]:
    links = n.links
    if links.empty:
        return []
    imports = set(_import_link_all(n))
    proc = process_mask(links)
    carrier = (
        n.buses["carrier"].astype(str)
        if "carrier" in n.buses.columns
        else pd.Series(dtype=str)
    )
    out = []
    for name in links.index:
        if str(name) in imports or bool(proc.get(name, False)):
            continue
        b0, b1 = str(links.at[name, "bus0"]), str(links.at[name, "bus1"])
        if (
            carrier.get(b0) in ELECTRIC_CARRIERS
            and carrier.get(b1) in ELECTRIC_CARRIERS
        ):
            out.append(str(name))
    return out


def _import_link_all(n) -> list[str]:
    from models.energy_hub import ImportOverlaySpec
    from services.adequacy.archetypes import select_import_links

    return list(select_import_links(n, ImportOverlaySpec()))


def _fold(products) -> tuple:
    """(t, EUR, flag) of a sequence of the process-loss screen's product rows: t None when any is None; EUR None
    with the last unpriced product's flag (else ``LPV_MISSING``) when any is unpriced, and None when t is."""
    t = eur = 0.0
    ef = None
    for prod in products:
        if prod["lost_t_per_year"] is None:
            t = None
        elif t is not None:
            t += prod["lost_t_per_year"]
        if prod["lost_eur_per_year"] is None:
            eur, ef = None, prod["flag"] or LPV_MISSING
        elif eur is not None:
            eur += prod["lost_eur_per_year"]
    return t, eur if t is not None else None, ef


def _n_1(n, lost_load, periods) -> dict:
    src = [NETWORK_FILE]
    feeding, rows, flags, skipped = [], [], [], []
    for link in _site_transformers(n):
        q, mttr = _outage(n, link)
        if q is None or not mttr or mttr <= 0 or not PL.units_cut_off(n, link):
            skipped.append(link)
            continue
        res = PL.expected_outage_loss(n, link, mttr, q, lost_load=lost_load)
        feeding.append(link)
        rows.append(
            {
                "key": link,
                "label": f"{link} out of service",
                "units": res["stopped_units"],
                "flags": res["flags"],
                "by_period": {
                    k: {
                        "occurrence_per_year": v["occurrence_per_year"],
                        "outage_hours": v["outage_hours"],
                        "products": v["products"],
                        "flags": [  # a copy: the screen's list is never changed
                            *v["flags"],
                            *([LOWER_BOUND] if v.get("bound") == "lower" else []),
                        ],
                        **({"bound": v["bound"]} if "bound" in v else {}),
                        **_cell(*_fold(v["products"])),
                    }
                    for k, v in res["by_period"].items()
                },
            }
        )
        flags += res["flags"]
    if not feeding:
        return _line(
            "n_1",
            source_files=src,
            missing=[
                _need(
                    "a site transformer: an electricity link that feeds the process units, with "
                    "an outage rate and a repair time (not the grid import link)",
                    "site_transformer",
                    _target("assets", None, "Link"),
                )
            ],
        )
    by_period = {}
    for p in periods:
        k = _key(p)
        cells = [r["by_period"].get(k) or {} for r in rows]
        # One fold over every product of every row, in row order: never the sum of the row sums (float order).
        by_period[k] = _cell(*_fold(p for c in cells for p in c.get("products", [])))
        flags += [LOWER_BOUND for c in cells if c.get("bound") == "lower"]
    return _line(
        "n_1",
        source_files=src,
        stamp=_solve_stamp(n),
        flags=flags,
        rows=rows,
        basis="the process-loss screen's expected outage loss per site transformer: events per year = year "
        "hours × outage rate / repair time, each event the mean undelivered tonnes over every start "
        "hour of the solved dispatch, with inventory credited"
        + (
            f". It covers only the transformers listed. Not counted: {', '.join(skipped)} (no outage rate, "
            "no repair time or no process unit behind it)"
            if skipped
            else ""
        ),
        by_period=by_period,
    )


def _connection(n, lost_load, periods) -> dict:
    src = [NETWORK_FILE]
    shortfalls = PP.shortfalls(n, lost_load)
    if not shortfalls:
        return _line(
            "connection",
            source_files=src,
            missing=[
                _need(
                    "a lost-production value on the product bus",
                    "lost_production_value",
                    _target("assets", None, "Bus", None, "lost_production_value"),
                )
            ],
        )
    wg = n.snapshot_weightings.generators.reindex(n.snapshots)
    flags, rows, missing, stale = [], [], [], False
    by_period = {_key(p): {"t": 0.0, "eur": 0.0, "ef": None} for p in periods}
    for s in shortfalls:
        src.append(
            f"{s.name} ({'shortfall Generator' if s.source == 'generator' else 'solve capture'})"
        )
        if s.flag == "lost_production_capture_stale":
            stale = True
        tpu = PP.t_per_unit(n, s.bus)
        if s.p is None or tpu is None:
            if tpu is None:
                missing.append(
                    _need(
                        f"lost production of {s.bus} (tonne factor)",
                        "mwh_per_t",
                        _target("assets", None, "Bus", s.bus, "mwh_per_t"),
                    )
                )
            else:
                missing.append(
                    _need(
                        f"lost production of {s.bus} (no capture of this solve: run it again)",
                        "run",
                        _target("readiness"),
                    )
                )
            continue
        row_bp = {}
        for p in periods:
            sns = PL._period_index(n, p)
            w = wg.reindex(sns)
            span = float(w.sum())
            scale = PL._year_hours(p) / span if span > 0 else 0.0
            x = s.p.reindex(sns).clip(lower=0)
            t = float((x * w).sum()) * tpu * scale
            eur = (
                float((x * s.cost.reindex(sns) * w).sum()) * scale
                if s.value is not None
                else None
            )
            ef = None if s.value is not None else LPV_MISSING
            row_bp[_key(p)] = _cell(t, eur, ef)
            acc = by_period[_key(p)]
            acc["t"] += t
            if eur is None:
                acc["eur"], acc["ef"] = None, ef
            elif acc["eur"] is not None:
                acc["eur"] += eur
        rows.append(
            {
                "key": s.bus,
                "label": s.bus,
                "flags": [f for f in (s.flag,) if f],
                "by_period": row_bp,
            }
        )
    if missing:
        return _line(
            "connection",
            source_files=src,
            stale=stale,
            missing=missing,
            rows=rows,
            stamp=_solve_stamp(n),
        )
    return _line(
        "connection",
        source_files=src,
        stamp=_solve_stamp(n),
        stale=stale,
        flags=flags,
        rows=rows,
        basis="the solve's own lost production, shown, not recomputed; each period weighted by its year "
        "hours over its modelled hours",
        by_period={k: _cell(v["t"], v["eur"], v["ef"]) for k, v in by_period.items()},
    )


def _solve_stamp(n) -> dict:
    rec = (getattr(n, "meta", None) or {}).get("sizing_record") or {}
    return {k: rec[k] for k in ("solved_at", "revision") if rec.get(k) is not None}


def _total(lines, periods) -> dict:
    by_period = {}
    for p in periods:
        k = _key(p)
        t = eur = 0.0
        ef = None
        for ln in lines:
            c = ln["by_period"].get(k)
            if ln["status"] == "missing" or c is None or c["t_per_year"] is None:
                continue
            t += c["t_per_year"]
            if c["eur_per_year"] is None:
                eur, ef = None, c["eur_flag"] or LPV_MISSING
            elif eur is not None:
                eur += c["eur_per_year"]
        by_period[k] = _cell(t, eur, ef)
    return {
        "label": OVERLAP_LABEL,
        "overlap_rule": OVERLAP_RULE,
        "incomplete": any(ln["status"] == "missing" for ln in lines),
        "lower_bound_lines": [ln["key"] for ln in lines if ln["status"] == "partial"],
        "by_period": by_period,
    }


def grid_risk_production(
    n, lost_load=None, *, campus: CampusRun | None = None
) -> dict | None:
    """The section's payload (module docstring), or None when the network has no process unit."""
    links = getattr(n, "links", None)
    if links is None or links.empty or not process_mask(links).any():
        return None
    campus = campus or CampusRun()
    periods = _periods(n)
    prices = _prices(n, lost_load)
    lines = [
        _ride_through(n, campus, prices, periods),
        _island(n, campus, prices, periods),
        _n_1(n, lost_load, periods),
        _connection(n, lost_load, periods),
    ]
    return {
        "periods": [_key(p) for p in periods],
        "unit": "t/yr",
        "currency": "EUR",
        "eur_inputs": eur_inputs(n, lost_load),
        "overlap_rule": OVERLAP_RULE,
        "overlap_label": OVERLAP_LABEL,
        "lines": lines,
        "total": _total(lines, periods),
    }


def campus_run_of(project) -> CampusRun:
    """The project's last campus run as the campus state reads it (``campus_electrical_service``): its run
    directory, stale when the campus, network or library changed since (``_stale``), the units whose restart,
    ride-through or motor field changed (12b-3's ``out_of_date``) and its stamp (the run's network digest and the
    screens' file time). No run: an empty ``CampusRun`` (the campus lines read missing)."""
    import json
    from datetime import datetime

    from services import campus_electrical_service as ce, project_registry

    d = project_registry.project_dir(project) / ce.SUBDIR
    run = d / "run"
    files = [run / f for f in (DIP_TRIPS_CSV, RESTART_CSV) if (run / f).is_file()]
    if not files:
        return CampusRun()
    stale = ce._stale(project, d)
    out_of_date: tuple[str, ...] = ()
    meta = run / ce.DIPS_RESTART_META
    if stale and meta.is_file():
        inputs = json.loads(meta.read_text()).get("inputs")
        if inputs:
            out_of_date = tuple(ce._dips_out_of_date(project, d, inputs))
    stamp = {
        "run_at": datetime.fromtimestamp(
            max(f.stat().st_mtime for f in files), UTC
        ).isoformat(timespec="seconds")
    }
    manifest = run / ce.cs.CAMPUS_MANIFEST if ce.cs is not None else None
    if manifest is not None and manifest.is_file():
        sha = json.loads(manifest.read_text()).get("network_sha256")
        if sha:
            stamp["network_sha256"] = str(sha)[:12]
    return CampusRun(run_dir=run, stale=stale, out_of_date=out_of_date, stamp=stamp)
