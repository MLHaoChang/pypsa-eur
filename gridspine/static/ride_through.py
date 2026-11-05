"""Voltage dips to process trips to lost tonnes (grid-to-process issue 12b, design §4.3 item 2).

A voltage dip at the point of connection (PoC) trips a process unit whose contactor or drive cannot ride it
through. The operator's dip statistics count dips per year by class; each unit's ride-through curve decides
which classes trip it; each trip costs the unit's restart:

    trips / yr    = sum of the counts of the classes the unit does not ride through
    lost t / trip = restart_time_h * rate_tph * mass_pull
    lost t / yr   = trips / yr * lost t / trip

**Classes.** The statistics follow the EN 50160 / IEC 61000-2-8 table: rows of *residual* voltage u (% of the
declared voltage) against columns of duration. A ride-through curve is given in *depth* (100 - u), so each class
is converted explicitly: its worst corner, the lowest residual (deepest) and the longest duration, is the dip the
unit must ride through. The u < 5 % row is depth 100.

**Curve.** ``"depth%:duration_s"`` pairs, e.g. ``"20:0.5, 50:0.15, 100:0.02"``: the unit rides through a dip of
depth d lasting t when some point has depth >= d and duration >= t. A dip deeper than every point trips the unit.
The points need not be monotone; a point dominated by another simply adds nothing.

A unit with no curve, restart time, rate or mass pull is "no input": verdict ``unknown``, never "rides", and the
total over units is ``incomplete`` with the unknown count. Every row carries ``dip_stats_source``: ``demo`` for
``DEMO_DIP_STATISTICS`` (so a total from it never reads as the site's figure) or ``user`` for the caller's table.

Pure pandas; a screening estimate, not dynamics.
"""
from __future__ import annotations

import math

import pandas as pd

from gridspine.schema.contracts import ContractError
from gridspine.static.flicker import _num

TOLERANCE = 1e-9

#: The EN 50160 residual-voltage rows, (lowest u, highest u) in % of the declared voltage, shallow to deep.
RESIDUAL_ROWS = ((80.0, 90.0), (70.0, 80.0), (40.0, 70.0), (5.0, 40.0), (0.0, 5.0))
#: The EN 50160 duration columns, (shortest, longest) in s.
DURATION_COLUMNS = ((0.01, 0.2), (0.2, 0.5), (0.5, 1.0), (1.0, 5.0), (5.0, 60.0))

#: Demo dips per year per class, rows as ``RESIDUAL_ROWS``, columns as ``DURATION_COLUMNS``: a rural MV feeder
#: of the order the CIGRE/CIRED C4.110 surveys report. Not this site's statistics: the operator's govern.
_DEMO_COUNTS = (
    (10.0, 3.0, 1.0, 0.5, 0.2),
    (5.0, 2.0, 0.5, 0.3, 0.1),
    (4.0, 1.5, 0.5, 0.2, 0.1),
    (1.5, 0.5, 0.2, 0.1, 0.05),
    (1.0, 0.5, 0.2, 0.2, 0.3),
)

STATS_COLUMNS = ("u_min_pct", "u_max_pct", "t_min_s", "t_max_s", "count_per_year")
DEMO, USER = "demo", "user"

LABEL = "screening estimate; the binding answer is the operator's dip statistics and the units' tested curves"
DEMO_NOTE = "demo dip statistics, not this site's: the operator's statistics govern"
LEDGER = (
    "dip statistics per PoC: EN 50160 / IEC 61000-2-8 classes, demo counts per year (demo, not the site's: the "
    "operator's statistics govern)",
    "a class is judged at its worst corner: its lowest residual voltage (depth = 100 - u) and its longest "
    "duration (conservative)",
    "a unit rides through a dip when a point of its ride-through curve has depth >= the dip's and duration >= "
    "the dip's (assumed: the curve is the unit's tested envelope)",
    "lost tonnes per trip = restart time * rate * mass pull: production is lost from the trip until full rate "
    "(assumed; the ramp back is counted as fully lost, conservative)",
)

CURVE_LABEL, RESTART_LABEL = "Ride-through curve", "Restart time"
TRIP_COLUMNS = ("unit", "u_min_pct", "u_max_pct", "t_min_s", "t_max_s", "depth_pct", "duration_s",
                "count_per_year", "verdict", "dip_stats_source", "note")
LOST_COLUMNS = ("unit", "trips_per_year", "restart_time_h", "rate_tph", "mass_pull", "lost_t_per_trip",
                "lost_t_per_year", "verdict", "dip_stats_source", "note", "label")


def _blank(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v)) or (isinstance(v, str) and not v.strip())


def parse_curve(text) -> tuple[tuple[float, float], ...]:
    """The ride-through curve ``((depth %, duration s), ...)`` of ``"20:0.5, 50:0.15"``; ``()`` when not given.
    Depth must be in (0, 100], duration above 0, each depth once."""
    if _blank(text):
        return ()
    out: dict[float, float] = {}
    for part in str(text).split(","):
        part = part.strip()
        if part.count(":") != 1:
            raise ContractError(f"{CURVE_LABEL}: expected depth%:duration_s pairs such as 50:0.15, got {part!r}.")
        d, t = (s.strip() for s in part.split(":"))
        try:
            depth, dur = float(d), float(t)
        except ValueError:
            raise ContractError(f"{CURVE_LABEL}: {part!r} is not a pair of numbers.") from None
        if not 0.0 < depth <= 100.0:   # also refuses nan and inf
            raise ContractError(f"{CURVE_LABEL}: depth {d!r} must be above 0 and at most 100 %.")
        if not math.isfinite(dur) or dur <= 0.0:
            raise ContractError(f"{CURVE_LABEL}: duration {t!r} at depth {depth:g} % must be above 0 s.")
        if depth in out:
            raise ContractError(f"{CURVE_LABEL}: depth {depth:g} % appears twice.")
        out[depth] = dur
    return tuple(sorted(out.items()))


def rides(curve, depth_pct: float, duration_s: float) -> bool:
    """Whether a unit with ``curve`` rides through a dip of ``depth_pct`` lasting ``duration_s``."""
    return any(d >= depth_pct - TOLERANCE and t >= duration_s - TOLERANCE for d, t in curve)


def demo_dip_statistics() -> pd.DataFrame:
    """``DEMO`` statistics, one row per class (``STATS_COLUMNS``)."""
    return pd.DataFrame([(u0, u1, t0, t1, _DEMO_COUNTS[i][j]) for i, (u0, u1) in enumerate(RESIDUAL_ROWS)
                         for j, (t0, t1) in enumerate(DURATION_COLUMNS)], columns=list(STATS_COLUMNS))


def dip_classes(stats: pd.DataFrame | None = None) -> tuple[pd.DataFrame, str]:
    """``(classes, source)``: the caller's statistics (``USER``) or the demo ones (``DEMO``), each class with its
    worst corner ``depth_pct`` = 100 - ``u_min_pct`` and ``duration_s`` = ``t_max_s``."""
    source = DEMO if stats is None else USER
    stats = demo_dip_statistics() if stats is None else stats
    missing = [c for c in STATS_COLUMNS if c not in stats.columns]
    if missing:
        raise ContractError(f"dip statistics need column(s) {missing}")
    out = stats[list(STATS_COLUMNS)].astype(float).reset_index(drop=True)
    for i, r in out.iterrows():
        if not (0.0 <= r.u_min_pct < r.u_max_pct <= 100.0):
            raise ContractError(f"dip statistics row {i}: residual voltage {r.u_min_pct:g}..{r.u_max_pct:g} % "
                                "must lie in 0..100 % with the lower bound first")
        if not (0.0 < r.t_min_s < r.t_max_s and math.isfinite(r.t_max_s)):
            raise ContractError(f"dip statistics row {i}: duration {r.t_min_s:g}..{r.t_max_s:g} s must be above "
                                "0 with the shorter bound first")
        if not (math.isfinite(r.count_per_year) and r.count_per_year >= 0.0):
            raise ContractError(f"dip statistics row {i}: count {r.count_per_year:g} per year must be 0 or more")
    out["depth_pct"] = 100.0 - out["u_min_pct"]
    out["duration_s"] = out["t_max_s"]
    return out, source


def _curve_of(name, text):
    try:
        return parse_curve(text)
    except ContractError as exc:
        raise ContractError(f"{name}: {exc}") from None


def trip_table(units: pd.DataFrame, stats: pd.DataFrame | None = None) -> pd.DataFrame:
    """One row per unit and dip class (``TRIP_COLUMNS``): ``trips``, ``rides`` or ``unknown`` (no curve).
    ``units``: ``unit`` and ``ride_through_curve`` (text, NaN/None/"" for not given)."""
    for col in ("unit", "ride_through_curve"):
        if col not in units.columns:
            raise ContractError(f"units needs a {col!r} column")
    classes, source = dip_classes(stats)
    rows = []
    for rec in units.to_dict("records"):
        curve = _curve_of(rec["unit"], rec["ride_through_curve"])
        for c in classes.to_dict("records"):
            base = {"unit": rec["unit"], **{k: c[k] for k in (*STATS_COLUMNS, "depth_pct", "duration_s")},
                    "dip_stats_source": source}
            if not curve:
                rows.append({**base, "verdict": "unknown", "note": f"no input: {CURVE_LABEL} not set"})
                continue
            ok = rides(curve, c["depth_pct"], c["duration_s"])
            rows.append({**base, "verdict": "rides" if ok else "trips",
                         "note": f"depth {c['depth_pct']:g} % for {c['duration_s']:g} s "
                                 f"{'within' if ok else 'outside'} the curve"})
    return pd.DataFrame(rows, columns=list(TRIP_COLUMNS))


def lost_tonnes_table(units: pd.DataFrame, stats: pd.DataFrame | None = None) -> pd.DataFrame:
    """One row per unit (``LOST_COLUMNS``): its expected trips and lost tonnes per year. ``units``: ``unit``,
    ``ride_through_curve``, ``restart_time_h`` (h), ``rate_tph`` (t/h) and ``mass_pull`` (t product per t),
    NaN/None for not given. A unit missing any is ``unknown``."""
    for col in ("unit", "ride_through_curve", "restart_time_h", "rate_tph", "mass_pull"):
        if col not in units.columns:
            raise ContractError(f"units needs a {col!r} column")
    if units["unit"].duplicated().any():
        raise ContractError(f"duplicate unit(s): {sorted(units.loc[units['unit'].duplicated(), 'unit'])}")
    trips = trip_table(units, stats)
    source = DEMO if stats is None else USER
    rows = []
    for rec in units.to_dict("records"):
        name = rec["unit"]
        restart, rate, mp = (_num(rec[c]) for c in ("restart_time_h", "rate_tph", "mass_pull"))
        for label, v in ((RESTART_LABEL, restart), ("rate", rate), ("mass pull", mp)):
            if v is not None and v < 0:
                raise ContractError(f"{name}: {label} must be 0 or more, got {v:g}.")
        mine = trips[trips["unit"] == name]
        n_trips = float(mine.loc[mine["verdict"] == "trips", "count_per_year"].sum())
        has_curve = bool(_curve_of(name, rec["ride_through_curve"]))
        missing = [lab for lab, v in ((CURVE_LABEL, 1 if has_curve else None), (RESTART_LABEL, restart),
                                      ("rate (t/h)", rate), ("mass pull", mp)) if v is None]
        row = {"unit": name, "trips_per_year": None if CURVE_LABEL in missing else n_trips,
               "restart_time_h": restart, "rate_tph": rate, "mass_pull": mp, "dip_stats_source": source,
               "label": LABEL}
        if missing:
            rows.append({**row, "lost_t_per_trip": None, "lost_t_per_year": None, "verdict": "unknown",
                         "note": f"no input: {' and '.join(missing)} not set"})
            continue
        per_trip = restart * rate * mp
        note = (f"{n_trips:g} trips/yr × ({restart:g} h × {rate:g} t/h × {mp:g}) = {n_trips * per_trip:.4g} t/yr")
        if source == DEMO:
            note += f"; {DEMO_NOTE}"
        rows.append({**row, "lost_t_per_trip": per_trip, "lost_t_per_year": n_trips * per_trip, "verdict": "known",
                     "note": note})
    return pd.DataFrame(rows, columns=list(LOST_COLUMNS))


def lost_tonnes_total(table: pd.DataFrame) -> dict:
    """The expected lost tonnes per year over a ``lost_tonnes_table``: the known units' sum, ``incomplete`` with
    ``unknown_count`` when any unit is unknown (the sum is then a lower bound, not the site's figure)."""
    unknown = int((table["verdict"] == "unknown").sum())
    known = table.loc[table["verdict"] == "known", "lost_t_per_year"]
    sources = sorted(set(table["dip_stats_source"])) or [DEMO]
    source = sources[0] if len(sources) == 1 else "mixed"
    notes = []
    if unknown:
        notes.append(f"incomplete: {unknown} unit(s) unknown, so the total is a lower bound")
    if DEMO in sources:
        notes.append(DEMO_NOTE)
    return {"lost_t_per_year": float(known.astype(float).sum()), "incomplete": bool(unknown),
            "unknown_count": unknown, "known_count": int(len(known)), "dip_stats_source": source,
            "note": "; ".join(notes)}
