"""Generate defaults for all (TS UI; gatekeeper plan D, 2026-10-20).

One review list for every family: each family that has a default brings a
``defaults(ctx, replace_generated)`` hook (`families.Family.defaults`) that
names, per component, a plan (what to generate and why) or a skip reason.
This module generates each plan into the pending store and sizes it; the
apply-all route writes the chosen ones under one undo step.

* Loads (today's only plugin): an archetype guessed from the load's name and
  its bus carrier, marked ``assumed`` with the reason it was picked. It is
  sized mean-preserving: the weighted mean over the model's snapshots equals
  the load's static p_set, the value the model uses in every hour today, so
  the energy stays as the model has it. Peak sizing (the peak equals the
  static p_set) is a per-row option. A peak above the point of connection is
  flagged.
* A measured series is never replaced; a generated one only when asked.
* Availability and prices have no default (a default must not change what
  the model can do; a price needs a money basis). Renewables plug in their
  own hook (RP-04/RP-09).
"""
from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

DEFAULT_PEAK_ABOVE_CONNECTION = "default_peak_above_connection"

# (pattern on the load's name, archetype, what matched) — first match wins.
_NAME_RULES: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"data.?(hall|cent)|(^|_)it(_|$)|server|rack"), "data_centre", "a data-hall name"),
    (re.compile(r"(^|_)ev(_|$)|charg"), "ev_charging", "an EV charging name"),
    (re.compile(r"heat|thermal"), "heat", "a heat name"),
    (re.compile(r"resid|house|home|dwelling"), "residential", "a residential name"),
    (re.compile(r"office|commerc|retail|shop|school|hospital"), "commercial", "a commercial name"),
    (re.compile(r"industr|plant|factory|process|mill|works"), "industrial", "an industrial name"),
)
# A bus carrier's default when the name says nothing.
_CARRIER_DEFAULT = {"ac": "commercial", "dc": "commercial", "electricity": "commercial",
                    "low voltage": "commercial", "heat": "heat"}


def guess_archetype(name: str, bus_carrier: str) -> tuple[str | None, str]:
    """(archetype, reason) for a load; (None, reason) when nothing fits."""
    low = name.lower()
    for pattern, archetype, what in _NAME_RULES:
        m = pattern.search(low)
        if m:
            return archetype, f"matched {m.group(0).strip('_')!r} in the load name ({what}) → {archetype}"
    carrier = (bus_carrier or "").strip().lower()
    if carrier in _CARRIER_DEFAULT:
        archetype = _CARRIER_DEFAULT[carrier]
        return archetype, (f"no match in the name {name!r}; the {carrier or 'electricity'} "
                           f"bus's default → {archetype}")
    return None, f"no archetype for a load on a {carrier or 'carrier-less'} bus; set it in the editor"


def load_defaults(ctx, replace_generated: bool) -> list[dict[str, Any]]:
    """The load family's plans and skips (`families.Family.defaults`)."""
    from services.dc_load_profile import read_envelope
    from services.profile_gen.families import DC_SPLIT_LOADS

    n = ctx.network
    storage = getattr(ctx, "storage_dir", None)
    dc_owned = bool(storage) and read_envelope(storage) is not None
    rows: list[dict[str, Any]] = []
    for load in n.loads.index:
        row: dict[str, Any] = {"family": "load", "component_type": "loads",
                               "attribute": "p_set", "component": str(load)}
        key = ("loads", "p_set", load)
        meta = ctx.user_ts_meta.get(key)
        if key in ctx.user_ts and not meta:
            rows.append({**row, "skipped": "carries a measured upload; never replaced"})
            continue
        if key in ctx.user_ts and not replace_generated:
            what = meta.get("archetype") or meta.get("kind") or meta.get("family")
            rows.append({**row, "skipped": f"already has a generated series ({what}); "
                                           "open its editor to change it"})
            continue
        if dc_owned and load in DC_SPLIT_LOADS:
            rows.append({**row, "skipped": "owned by this project's dc_load spec"})
            continue
        p_set = float(n.loads.at[load, "p_set"])
        if not p_set > 0:
            rows.append({**row, "skipped": "no size: its static p_set is 0 — state a "
                                           "peak or annual energy in the editor"})
            continue
        bus = n.loads.at[load, "bus"]
        carrier = str(n.buses.at[bus, "carrier"]) if bus in n.buses.index else ""
        archetype, reason = guess_archetype(str(load), carrier)
        if archetype is None or (dc_owned and archetype == "data_centre"):
            rows.append({**row, "skipped": reason if archetype is None else
                         "a data-centre load here belongs to the dc_load spec"})
            continue
        rows.append({**row, "plan": {"kind": archetype, "reason": reason,
                                     "static_p_set_mw": p_set}})
    return rows


def _year(ctx) -> int:
    snaps = ctx.network.snapshots
    first = (snaps.get_level_values(-1)[0] if isinstance(snaps, pd.MultiIndex)
             else pd.DatetimeIndex(snaps)[0])
    return int(first.year)


def _poc_mw(ctx) -> float | None:
    from services.profile_gen.apply import commercial_config

    poc = commercial_config(ctx).get("poc_link")
    links = ctx.network.links
    if poc and poc in links.index:
        return float(links.at[poc, "p_nom"]) * float(links.at[poc, "p_max_pu"])
    return None


def _generate_load(ctx, row: Mapping[str, Any], peak_sized: bool) -> tuple[pd.DataFrame, dict]:
    """The plan's archetype, sized on the model's own snapshots."""
    from services.profile_gen import engine
    from services.profile_gen.apply import TARGETS, commercial_timezone, prepare_profile

    plan, comp = row["plan"], row["component"]
    p_set = float(plan["static_p_set_mw"])
    params: dict[str, Any] = {"peak_mw": 1.0}
    tz = commercial_timezone(ctx)
    if tz:
        params["timezone"] = tz
    result = engine.generate_profile([comp], plan["kind"], params=params,
                                     year=_year(ctx), family="load")
    series, meta = result.series[comp], dict(result.meta)
    # Size on what apply would write: the aligned shape, weighted as the
    # model weights it (the load target's own weighting column).
    aligned = prepare_profile(ctx, comp, series, meta, component_type="loads",
                              attribute="p_set").aligned.to_numpy(dtype=float)
    w = ctx.network.snapshot_weightings[TARGETS[("loads", "p_set")].weighting]
    w = w.reindex(ctx.network.snapshots).to_numpy(dtype=float)
    if peak_sized:
        factor = p_set / float(np.max(aligned))
        basis = "peak"
    else:
        factor = p_set / float(np.sum(aligned * w) / np.sum(w))
        basis = "mean"
    meta["scale_mw"] = float(meta["scale_mw"]) * factor
    meta["provenance"] = "synthetic/assumed"
    meta["default"] = {"reason": plan["reason"], "sizing": basis,
                       "static_p_set_mw": p_set, "assumed": True}
    return result.series[[comp]], meta


def preview_defaults(ctx, project_dir: str, project: str, *,
                     replace_generated: bool = False,
                     peak_sized: frozenset[str] = frozenset()) -> list[dict[str, Any]]:
    """Every family's default rows, each plan generated into the pending
    store with its stats and warnings."""
    from services.profile_gen.engine import ProfileGenError
    from services.profile_gen.apply import ProfileApplyError
    from services.profile_gen.families import all_families
    from services.profile_gen.job import write_pending

    poc = _poc_mw(ctx)
    out: list[dict[str, Any]] = []
    for family in all_families():
        if family.defaults is None:
            continue
        for row in family.defaults(ctx, replace_generated):
            if "plan" not in row:
                out.append(row)
                continue
            try:
                frame, meta = _generate_load(ctx, row, row["component"] in peak_sized)
            except (ProfileGenError, ProfileApplyError) as exc:
                out.append({**{k: v for k, v in row.items() if k != "plan"},
                            "skipped": f"cannot generate: {exc}"})
                continue
            job_id = str(uuid.uuid4())
            record = {**meta, "job_id": job_id, "project_id": project,
                      "points": list(frame.columns)}
            write_pending(project_dir, job_id, frame, record)
            comp = row["component"]
            mw = frame[comp] * float(meta["scale_mw"])
            warnings: list[str] = []
            if poc is not None and float(mw.max()) > poc:
                warnings.append(
                    f"{DEFAULT_PEAK_ABOVE_CONNECTION}: the default's peak "
                    f"{float(mw.max()):,.2f} MW is above the point of connection's "
                    f"{poc:,.2f} MW")
            out.append({k: v for k, v in row.items() if k != "plan"} | {
                "job_id": job_id, "kind": row["plan"]["kind"],
                "reason": row["plan"]["reason"], "assumed": True,
                "sizing": meta["default"]["sizing"],
                "static_p_set_mw": row["plan"]["static_p_set_mw"],
                "stats": {"peak_mw": float(mw.max()), "mean_mw": float(mw.mean())},
                "warnings": warnings, "record": record,
            })
    return out
