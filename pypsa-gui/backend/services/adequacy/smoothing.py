"""
The load-smoothing screen (DC 03, owner decision RLR-527): can the site's
battery/UPS hold the AI-training load's steps and oscillation inside the
connection's ramp and oscillation limits, from the reserve its backup duty
leaves?

One battery/UPS, two duties, no double credit: the backup duty is reserved
first and smoothing gets what is left.

* **Backup duty, energy only.** The ride-through batteries keep the island
  study's outage reserve R (``site_peak.outage_reserve_mwh``, the one the
  peak check keeps): (critical load − firm gensets)⁺ × ``sustained_h``, plus
  the seamless bridge. A UPS keeps the energy to bridge its IT load for
  ``bridge_s``. Their power is free for smoothing while the grid is there:
  they carry the load only once it is lost, and then smoothing stops.
* **Smoothing need**, closed form per envelope phase (IT side at the PoC, as
  the scorecard's ramp and oscillation rows read it), R the ramp limit:

  - a step S (training starts or stops) through a ramp limit R needs ≈ S MW
    and S²/(2R) MWh, discharging on the way up and charging on the way down,
    so the battery holds a band of S²/R MWh above its backup floor;
  - a checkpoint dip of depth S for d s needs min(S·d, S²/(2R)) MWh (hold the
    grid flat, or ramp it), inside the step's band, every checkpoint interval;
  - an oscillation of amplitude A and period T inside the band of concern
    needs the part above the limit, A_x = max(0, A − A_lim), in MW and
    A_x·T/π MWh of swing; outside the band it needs nothing.

  Need = S + A_x MW and S²/R + A_x·T/π MWh. Up and down share one
  state-of-charge band above the backup floor: the battery sits where it can
  both give E_up and take E_down, so E_up + E_down ≤ e − R (UPS: e − bridge).

Only committed (non-extendable) StorageUnits count, as in the peak check: an
extendable unit is sized by the run, so the screen cannot know its rating
(one sizing basis with the peak check). Nothing is time-stepped, solved or
stored; no input is new. The screen reads the envelope's phases only: it is
blind to in-hour ramps beyond them (the hourly-mean profile carries none).
Energies are deliverable MWh; the charging side takes 1/η_store more from the
grid than it stores, which the screen does not add (a few per cent).
"""
from __future__ import annotations

import math

import numpy as np

from services.adequacy.site_peak import _energized, _periods, _value, import_links, outage_reserve_mwh

#: The screen's verdicts: the reserve covers the need, falls short, or the
#: screen lacks an input.
VERDICTS = ("covered", "short", "unknown")
NO_BATTERY = "unknown, no battery/UPS"


def step_mwh(step_mw: float, ramp_mw_per_min: float) -> float:
    """The energy a battery moves while the grid ramps through a step S at R:
    the triangle S·(S/R)/2, in MWh."""
    return step_mw * step_mw / (2.0 * ramp_mw_per_min * 60.0)


def checkpoint_mwh(depth_mw: float, duration_s: float, ramp_mw_per_min: float) -> float:
    """A checkpoint dip's energy: hold the grid flat for the dip (S·d) or ramp
    it down and back (S²/(2R)), whichever is less."""
    return min(depth_mw * duration_s / 3600.0, step_mwh(depth_mw, ramp_mw_per_min))


def oscillation_mwh(amplitude_mw: float, period_s: float) -> float:
    """The swing of a sinusoid of amplitude A and period T: A·T/π, in MWh."""
    return amplitude_mw * period_s / (math.pi * 3600.0)


def _year(cod: str) -> int | None:
    try:
        return int(str(cod)[:4])
    except ValueError:
        return None


def _active(su, name: str, year: int | None) -> bool:
    """A StorageUnit in service in ``year`` (its build year and lifetime)."""
    if year is None:
        return True
    built = float(su.at[name, "build_year"]) if "build_year" in su.columns else 0.0
    life = float(su.at[name, "lifetime"]) if "lifetime" in su.columns else math.inf
    return built <= year < built + life if built > 0 else True


def _it_peak_mw(n, loads: list[str]) -> float | None:
    """The highest hourly total of the named loads, or None when none is in the network."""
    names = [x for x in loads if x in n.loads.index]
    if not names:
        return None
    ts = n.loads_t.p_set.reindex(columns=names)
    static = n.loads.loc[names, "p_set"] if "p_set" in n.loads.columns else 0.0
    return float(ts.fillna(static).reindex(n.snapshots).fillna(0.0).sum(axis=1).max())


def _delivered(n) -> np.ndarray | None:
    """The energized import after its efficiency per snapshot, as the peak check reads it."""
    links = import_links(n)
    if not links:
        return None
    return sum(_energized(n, l) * float(n.links.at[l, "efficiency"]) for l in links)


def _mask(n, year: int | None):
    """The snapshots of the investment period named for ``year``; the whole
    horizon when no period is (the reserve's maximum over it, conservative)."""
    for label, mask in _periods(n):
        if year is not None and label == str(year):
            return mask
    return None


def _reserve(n, island: dict, year: int | None) -> dict:
    """What the committed ride-through batteries and UPSs hold for smoothing
    in ``year``, after their backup duty (energy only): ``{mw, mwh,
    backup_mwh, units, unsized, notes}``."""
    su = n.storage_units
    units = island.get("units") or {}
    req = island.get("requirements") or {}
    rts = req.get("ride_through_storage")
    if rts is None:
        rts = [u for u, d in units.items() if d.get("kind") == "bess"]
    upss = [u for u, d in units.items() if d.get("kind") == "ups"]
    out = {"mw": 0.0, "mwh": 0.0, "backup_mwh": 0.0, "units": [], "unsized": [], "notes": []}

    def committed(names):
        live = [u for u in names if u in su.index and _active(su, u, year)]
        out["unsized"] += [u for u in live if bool(su.at[u, "p_nom_extendable"])]
        return [u for u in live if not bool(su.at[u, "p_nom_extendable"])
                and float(su.at[u, "p_nom"]) > 0]

    def deliverable(u):
        return float(su.at[u, "efficiency_dispatch"]) * float(su.at[u, "p_nom"]) * float(su.at[u, "max_hours"])

    rt = committed(rts)
    if rt:
        keep, words = outage_reserve_mwh(n, island, _mask(n, year), _delivered(n))
        out["mw"] += sum(float(su.at[u, "p_nom"]) for u in rt)
        out["mwh"] += sum(deliverable(u) for u in rt) - keep
        out["backup_mwh"] += keep
        out["units"] += rt
        out["notes"].append(f"ride-through {', '.join(rt)} keeps the {words}")
    bridge_s = float(_value(req.get("bridge_s")) or 0.0)
    for u in committed(upss):
        p, e = float(su.at[u, "p_nom"]), deliverable(u)
        load = _it_peak_mw(n, list(units[u].get("it_load") or []))
        carried = min(p, load) if load is not None else p
        keep = carried * bridge_s / 3600.0
        out["mw"] += p
        out["mwh"] += e - keep
        out["backup_mwh"] += keep
        out["units"].append(u)
        out["notes"].append(f"UPS {u}: {keep:.3g} MWh kept to bridge {carried:.3g} MW for {bridge_s:g} s"
                            + ("" if load is not None else " (its IT load is not in the network: its rating)"))
    out["mwh"] = max(out["mwh"], 0.0)
    return out


def _phase(ph: dict, reserve: dict, ramp: float | None, osc_limit_pct: float | None,
           connection_mw: float | None, band_hz: tuple[float, float]) -> dict:
    s = float(ph["swing_mw"])
    ck, osc = ph.get("checkpoint") or {}, ph.get("oscillation") or {}
    row = {"cod": ph.get("cod"), "it_mw": ph.get("it_mw"), "step_mw": s}
    if ramp is None or ramp <= 0:
        return {**row, "verdict": "unknown", "note": "no connection ramp limit to smooth to"}
    e_step = step_mwh(s, ramp)
    e_ck = checkpoint_mwh(float(ck.get("depth_mw", 0.0)), float(ck.get("duration_s", 0.0)), ramp)
    interval = float(ck.get("interval_s") or 0.0)
    amp, period = float(osc.get("amplitude_mw", 0.0)), float(osc.get("period_s", 0.0))
    hz = 1.0 / period if period > 0 else math.inf
    in_band = band_hz[0] <= hz <= band_hz[1]
    a_lim = (osc_limit_pct / 100.0 * connection_mw / 2.0
             if osc_limit_pct is not None and connection_mw else None)
    a_x = (max(0.0, amp - a_lim) if a_lim is not None else amp) if in_band else 0.0
    e_osc = oscillation_mwh(a_x, period) if a_x > 0 else 0.0
    need_mw, need_mwh = s + a_x, 2.0 * e_step + e_osc
    row.update({
        "ramp_limit_mw_per_min": ramp, "ramp_minutes": s / ramp,
        "step_mwh": e_step, "checkpoint_mwh": e_ck,
        "checkpoints_per_day": 86400.0 / interval if interval > 0 and e_ck > 0 else 0.0,
        "oscillation_excess_mw": a_x, "oscillation_mwh": e_osc, "oscillation_in_band": in_band,
        "need_mw": need_mw, "need_mwh": need_mwh,
        "reserve_mw": reserve["mw"], "reserve_mwh": reserve["mwh"],
        "backup_mwh": reserve["backup_mwh"],
        "units": list(reserve["units"]),
    })
    if not reserve["units"]:
        why = (f"; {', '.join(reserve['unsized'])} sized by the run, not counted" if reserve["unsized"] else "")
        return {**row, "verdict": "unknown", "note": NO_BATTERY + why}
    short = [w for w, ok in (("MW", reserve["mw"] >= need_mw - 1e-9),
                             ("MWh", reserve["mwh"] >= need_mwh - 1e-9)) if not ok]
    words = [f"a {s:.3g} MW step through the {ramp:g} MW/min limit takes {s / ramp:.3g} min and "
             f"{e_step:.3g} MWh each way",
             (f"oscillation {a_x:.3g} MW above the limit, {e_osc:.2g} MWh of swing" if a_x > 0
              else "the oscillation needs no smoothing" + ("" if in_band else " (outside the band of concern)")),
             f"need {need_mw:.3g} MW / {need_mwh:.3g} MWh against {reserve['mw']:.3g} MW / "
             f"{reserve['mwh']:.3g} MWh left after backup", *reserve["notes"]]
    if reserve["unsized"]:
        words.append(f"{', '.join(reserve['unsized'])} sized by the run, not counted")
    return {**row, "verdict": "short" if short else "covered", "short": short, "note": "; ".join(words)}


def smoothing_screen(n, island: dict | None, envelope: dict | None, *, ramp_limit_mw_per_min: float | None,
                     oscillation_limit_pct: float | None, connection_mw: float | None,
                     band_hz: tuple[float, float] = (0.1, 2.0)) -> dict:
    """The screen per envelope phase: ``{"verdict", "note", "phases": [{cod,
    step_mw, step_mwh, checkpoint_mwh, oscillation_mwh, need_mw, need_mwh,
    reserve_mw, reserve_mwh, backup_mwh, units, verdict, note}]}``.
    The overall verdict is the worst phase's (short over unknown over covered).
    ``None`` without an AI-training envelope: nothing to screen."""
    if not envelope or not envelope.get("phases"):
        return None
    if n is None or not island:
        return {"verdict": "unknown", "note": NO_BATTERY, "phases": []}
    phases = [_phase(ph, _reserve(n, island, _year(ph.get("cod"))), ramp_limit_mw_per_min,
                     oscillation_limit_pct, connection_mw, band_hz) for ph in envelope["phases"]]
    order = {"short": 0, "unknown": 1, "covered": 2}
    worst = min(phases, key=lambda p: order[p["verdict"]])
    return {"verdict": worst["verdict"], "note": worst["note"], "phases": phases}
